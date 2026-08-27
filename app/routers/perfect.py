"""Perfect OCR: reconstruct a real document from client-supplied structure.

Architecture decision
----------------------
No OCR/layout ML model runs on this backend. The Flutter client reads each
page with Gemini 2.5 Flash (multimodal, via the Firebase AI SDK already used
for document classification -- see ``lib/services/perfect_ocr_service.dart``)
and sends back a structured list of elements per page: headings, paragraphs,
lists, tables (with real cells), formulas (as LaTeX), figures/diagrams/
charts, each with its position. This router turns that into a real
:class:`~app.schemas.StructuredDocument` and reconstructs a clean,
position-preserving PDF using :func:`app.exporters.pdf_export.export_clean_pdf`
-- the same reconstruction layer ``/document/structure`` already uses.

This keeps the backend light and fast (matches the plain ``requirements.txt``
-- no torch/paddle/tesseract), at the cost of the recognition itself running
against Google's Gemini API rather than a self-hosted model.

Endpoints
---------
``POST /document/perfect/page``
    One image (multipart ``file``) plus its recognized ``elements`` (Form,
    JSON array). Returns the structured document, plain text, and the
    reconstructed PDF -- same response shape as ``/document/structure/pdf``.

``POST /document/perfect/book``
    Multiple pages (multipart ``pages``) plus one JSON array of per-page
    element arrays (Form ``elements``). Reconstructs one merged PDF and
    reuses the existing ``GET /document/book/{job_id}/file`` download route
    (jobs from this endpoint are tagged ``kind="perfect_book"`` but stored
    in the same ``ocr_jobs`` table, so that route needs no changes).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..document_model import (
    build_document,
    infer_hierarchy,
    infer_reading_order,
    normalize_label,
    page_plain_text,
)
from ..exporters.pdf_export import ExportUnavailable, export_clean_pdf
from ..logging_config import get_logger
from ..models import OcrJob
from ..ocr_support import make_workdir, prepare_page, read_upload
from ..schemas import (
    BookStructureResponse,
    DocElement,
    DocPage,
    StructuredPdfResponse,
    TableCell,
    TableData,
)
from ..storage import job_dir, safe_rmtree

log = get_logger(__name__)

router = APIRouter(prefix="/document/perfect", tags=["perfect-ocr"])

_ENGINE_NAME = "Gemini-2.5-Flash (client) + Prism reconstruction"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _parse_elements_field(raw: str | None, *, context: str) -> Any:
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing '{context}' field with the recognized page structure.",
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{context}' is not valid JSON: {exc}",
        ) from exc


def _parse_table(raw: dict) -> TableData:
    cells: list[TableCell] = []
    for cell in raw.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        try:
            cells.append(
                TableCell(
                    row=int(cell.get("row", 0)),
                    col=int(cell.get("col", 0)),
                    rowSpan=max(1, int(cell.get("rowSpan", 1) or 1)),
                    colSpan=max(1, int(cell.get("colSpan", 1) or 1)),
                    text=str(cell.get("text", "") or ""),
                    isHeader=bool(cell.get("isHeader", False)),
                )
            )
        except (TypeError, ValueError):
            continue

    row_count = raw.get("rowCount")
    col_count = raw.get("columnCount")
    return TableData(
        rowCount=int(row_count)
        if isinstance(row_count, (int, float))
        else max((c.row + c.rowSpan for c in cells), default=0),
        columnCount=int(col_count)
        if isinstance(col_count, (int, float))
        else max((c.col + c.colSpan for c in cells), default=0),
        cells=cells,
    )


def _parse_element(
    raw: Any, *, page_number: int, index: int, image_ref: str | None
) -> DocElement | None:
    if not isinstance(raw, dict):
        return None

    element_type = normalize_label(raw.get("type"))

    bbox: list[float] = []
    bbox_raw = raw.get("bbox")
    if isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox_raw)
            bbox = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        except (TypeError, ValueError):
            bbox = []

    text_raw = raw.get("text")
    text = str(text_raw) if isinstance(text_raw, (str, int, float)) else None

    table: TableData | None = None
    table_raw = raw.get("table")
    if element_type == "table" and isinstance(table_raw, dict):
        table = _parse_table(table_raw)
        text = None

    confidence_raw = raw.get("confidence")
    confidence = (
        max(0.0, min(1.0, float(confidence_raw)))
        if isinstance(confidence_raw, (int, float))
        else None
    )

    return DocElement(
        id=uuid.uuid4().hex[:16],
        page=page_number,
        type=element_type,
        text=text or None,
        bbox=bbox,
        width=round(bbox[2] - bbox[0], 2) if len(bbox) == 4 else 0.0,
        height=round(bbox[3] - bbox[1], 2) if len(bbox) == 4 else 0.0,
        confidence=confidence,
        readingOrder=index,
        table=table,
        sourceImage=image_ref,
    )


def _build_page(
    elements_raw: Any,
    *,
    page_number: int,
    page_width: float,
    page_height: float,
    image_ref: str | None,
) -> DocPage:
    page = DocPage(
        pageNumber=page_number,
        width=page_width,
        height=page_height,
        imageRef=image_ref,
    )
    if not isinstance(elements_raw, list) or not elements_raw:
        page.warnings.append("Gemini returned no parsable structure for this page.")
        return page

    elements: list[DocElement] = []
    for index, raw in enumerate(elements_raw):
        element = _parse_element(raw, page_number=page_number, index=index, image_ref=image_ref)
        if element is not None:
            elements.append(element)

    if not elements:
        page.warnings.append("No elements were recognized on this page.")

    infer_reading_order(elements, page_width)
    infer_hierarchy(elements)
    page.elements = elements
    return page


def _export_or_503(document) -> bytes:
    try:
        return export_clean_pdf(document)
    except ExportUnavailable as exc:
        log.error("Could not build the reconstructed PDF: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not build the reconstructed PDF: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# POST /document/perfect/page
# ---------------------------------------------------------------------------
@router.post("/page", response_model=StructuredPdfResponse)
def perfect_page(
    file: UploadFile = File(...),
    elements: str = Form(...),
) -> StructuredPdfResponse:
    """Reconstruct one page from Gemini's structured reading of it."""
    data = read_upload(file)
    workdir = make_workdir("perfect")
    try:
        # The client already cropped/adjusted this image in the scan editor,
        # so the OpenCV edge-detection scanner (built for a raw camera photo
        # of a page in a scene) is skipped here -- it would only distort an
        # already-clean image.
        prepared = prepare_page(data, workdir, stem="page", enable_preprocessing=False)
        if prepared.is_pdf:
            raise HTTPException(
                status_code=400, detail="Perfect OCR accepts images, not PDF files."
            )

        parsed_elements = _parse_elements_field(elements, context="elements")

        image_path = workdir / "source.png"
        image_path.write_bytes(prepared.display_bytes)

        page = _build_page(
            parsed_elements,
            page_number=1,
            page_width=prepared.width,
            page_height=prepared.height,
            image_ref=str(image_path),
        )
        document = build_document([page], source_filename=file.filename, engine=_ENGINE_NAME)
        pdf_bytes = _export_or_503(document)

        return StructuredPdfResponse(
            document=document,
            text=page_plain_text(page),
            pdfBase64=base64.b64encode(pdf_bytes).decode("ascii"),
            scannedImageBase64=base64.b64encode(prepared.display_bytes).decode("ascii"),
        )
    finally:
        safe_rmtree(workdir)


# ---------------------------------------------------------------------------
# POST /document/perfect/book
# ---------------------------------------------------------------------------
@router.post("/book", response_model=BookStructureResponse)
def perfect_book(
    meta: str | None = Form(default=None),
    pages: list[UploadFile] = File(...),
    elements: str = Form(...),
    db: Session = Depends(get_db),
) -> BookStructureResponse:
    """Merge every page's Gemini-recognized structure into one book PDF."""
    if not pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No pages were uploaded."
        )

    user_email: str | None = None
    if meta:
        try:
            parsed_meta = json.loads(meta)
            email = parsed_meta.get("email")
            if isinstance(email, str):
                user_email = email.strip().lower() or None
        except (json.JSONDecodeError, AttributeError):
            log.warning("Ignoring unparsable perfect-book meta field")

    pages_elements = _parse_elements_field(elements, context="elements")
    if not isinstance(pages_elements, list) or len(pages_elements) != len(pages):
        got = len(pages_elements) if isinstance(pages_elements, list) else "not an array"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "'elements' must be a JSON array with one entry per page "
                f"({len(pages)} pages uploaded, {got} given)."
            ),
        )

    job_id = str(uuid.uuid4())
    destination = job_dir(job_id)
    workdir = make_workdir("perfectbook")

    doc_pages: list[DocPage] = []
    page_texts: list[str] = []
    document = None

    try:
        for index, upload in enumerate(pages):
            data = read_upload(upload)
            prepared = prepare_page(
                data, workdir, stem=f"page_{index}", enable_preprocessing=False
            )
            if prepared.is_pdf:
                raise HTTPException(
                    status_code=400, detail="Perfect OCR accepts images, not PDF files."
                )

            stored = destination / f"page_{index:04d}.png"
            stored.write_bytes(prepared.display_bytes)

            page_number = index + 1
            page = _build_page(
                pages_elements[index],
                page_number=page_number,
                page_width=prepared.width,
                page_height=prepared.height,
                image_ref=str(stored),
            )
            doc_pages.append(page)
            page_texts.append(page_plain_text(page))

        document = build_document(
            doc_pages,
            source_filename=f"perfect_book_{job_id}",
            engine=_ENGINE_NAME,
            document_id=job_id,
        )

        pdf_path = destination / "book.pdf"
        pdf_path.write_bytes(_export_or_503(document))

        json_path = destination / "document.json"
        json_path.write_text(
            document.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
        )

        db.add(
            OcrJob(
                id=job_id,
                user_email=user_email,
                kind="perfect_book",
                status="completed",
                page_count=len(doc_pages),
                pdf_path=str(pdf_path),
                json_path=str(json_path),
                created_at=_utcnow(),
            )
        )
        db.flush()

    except HTTPException:
        safe_rmtree(destination)
        raise
    finally:
        safe_rmtree(workdir)

    log.info("Perfect OCR book job %s completed: %d pages", job_id, len(doc_pages))
    return BookStructureResponse(
        jobId=job_id,
        pageCount=len(doc_pages),
        bookPdfUrl=f"/document/book/{job_id}/file",
        document=document,
        pages=page_texts,
    )
