"""OpenCV document scanning and format export.

Endpoints
---------
``POST /document/structure``
    One image (multipart ``file``). Scans it with OpenCV and returns the
    structured JSON document model.

``POST /document/book/structure``
    Same multipart convention as ``/document/ocr/book`` (a ``meta`` JSON field
    plus repeated ``pages`` files). Runs the structured pipeline per page AND
    reconstructs one merged, page-numbered PDF of the whole batch.

``GET /document/book/{job_id}/file``
    Downloads that merged PDF.

``POST /document/{format}/export``  (pdf | docx | markdown | xlsx)
    Takes a structured document model back and reconstructs a real file.

DESIGN DECISION -- merged book PDF retrieval
--------------------------------------------
The spec offered two options: inline base64 in the JSON, or a follow-up GET.
**This implementation uses the follow-up GET.** ``POST /document/book/structure``
returns ``jobId`` and ``bookPdfUrl``; the client then fetches
``GET /document/book/{job_id}/file`` for the PDF bytes.

Reason: continuous scan mode routinely produces 20-50+ pages. Base64 inflates
the payload ~33% and forces both server and client to hold the entire PDF in
memory as a string inside an already-large JSON body, on a mobile client. The
GET streams from disk instead, and lets the client retry the download without
re-running OCR. Wire the Flutter client to this shape.
"""

from __future__ import annotations

import datetime as dt
import base64
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..document_model import build_document, normalize_page, page_plain_text
from ..exporters import EXPORTERS
from ..exporters.pdf_export import ExportUnavailable, export_pdf
from ..logging_config import get_logger
from ..models import OcrJob
from ..ocr_support import make_workdir, prepare_page, read_upload
from ..opencv_document_scanner import ScannerFailed, ScannerBusy, ScannerUnavailable
from ..schemas import BookStructureResponse, StructuredDocument, StructuredPdfResponse
from ..storage import is_within, job_dir, safe_rmtree, storage_root

log = get_logger(__name__)

router = APIRouter(prefix="/document", tags=["document-structure"])

_ENGINE_NAME = "OpenCV-Document-Scanner"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _pipeline_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ScannerBusy):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "5"},
        )
    if isinstance(exc, ScannerUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
    )


# ---------------------------------------------------------------------------
# POST /document/structure
# ---------------------------------------------------------------------------
@router.post("/structure", response_model=StructuredDocument)
def structure_single(file: UploadFile = File(...)) -> StructuredDocument:
    """Full structured parse of a single image or PDF."""
    data = read_upload(file)
    workdir = make_workdir("structure")

    try:
        prepared = prepare_page(data, workdir, stem="page")
        if prepared.is_pdf:
            raise HTTPException(status_code=400, detail="OpenCV scanner accepts images, not PDF files.")

        pages = []
        pages.append(normalize_page(
            {}, page_number=1, page_width=prepared.width, page_height=prepared.height,
            rotation=prepared.rotation, image_ref=None, warnings=list(prepared.warnings),
        ))

        document = build_document(
            pages,
            source_filename=file.filename,
            engine=_ENGINE_NAME,
        )
    except (ScannerBusy, ScannerUnavailable, ScannerFailed) as exc:
        log.warning("Structure parse unavailable/failed: %s", exc)
        raise _pipeline_http_error(exc) from exc
    finally:
        safe_rmtree(workdir)

    return document


@router.post("/structure/pdf", response_model=StructuredPdfResponse)
def structure_single_pdf(file: UploadFile = File(...)) -> StructuredPdfResponse:
    """Parse one scan and return its structured JSON plus searchable PDF."""
    data = read_upload(file)
    workdir = make_workdir("structure_pdf")

    try:
        prepared = prepare_page(data, workdir, stem="page")
        if prepared.is_pdf:
            raise HTTPException(status_code=400, detail="OpenCV scanner accepts images, not PDF files.")
        pages = []
        page = normalize_page(
            {}, page_number=1, page_width=prepared.width, page_height=prepared.height,
            image_ref=None, warnings=list(prepared.warnings),
        )
        if prepared.display_bytes:
            page.imageBase64 = base64.b64encode(prepared.display_bytes).decode("ascii")
        pages.append(page)

        document = build_document(
            pages,
            source_filename=file.filename,
            engine=_ENGINE_NAME,
        )
        pdf_bytes = export_pdf(document)
        text = "\n\n".join(page_plain_text(page) for page in pages).strip()
        return StructuredPdfResponse(
            document=document,
            text=text,
            pdfBase64=base64.b64encode(pdf_bytes).decode("ascii"),
            scannedImageBase64=(
                base64.b64encode(prepared.display_bytes).decode("ascii")
                if prepared.display_bytes
                else None
            ),
        )
    except ExportUnavailable as exc:
        log.warning("Structured PDF export unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (ScannerBusy, ScannerUnavailable, ScannerFailed) as exc:
        log.warning("Structured PDF parse unavailable/failed: %s", exc)
        raise _pipeline_http_error(exc) from exc
    finally:
        safe_rmtree(workdir)


# ---------------------------------------------------------------------------
# POST /document/book/structure
# ---------------------------------------------------------------------------
@router.post("/book/structure", response_model=BookStructureResponse)
def structure_book(
    meta: str | None = Form(default=None),
    pages: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> BookStructureResponse:
    """Structured parse of every page, plus one merged, numbered PDF."""
    if not pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No pages were uploaded."
        )

    user_email: str | None = None
    if meta:
        try:
            parsed = json.loads(meta)
            declared = parsed.get("pageCount")
            if isinstance(declared, int) and declared != len(pages):
                log.warning(
                    "Book meta declared %d pages but %d files arrived",
                    declared,
                    len(pages),
                )
            email = parsed.get("email")
            if isinstance(email, str):
                user_email = email.strip().lower() or None
        except (json.JSONDecodeError, AttributeError):
            log.warning("Ignoring unparsable book meta field")

    job_id = str(uuid.uuid4())
    destination = job_dir(job_id)
    workdir = make_workdir("bookstruct")

    doc_pages = []
    page_texts: list[str] = []

    try:
        for index, upload in enumerate(pages):
            data = read_upload(upload)
            prepared = prepare_page(data, workdir, stem=f"page_{index}")
            if prepared.is_pdf:
                raise HTTPException(status_code=400, detail="OpenCV scanner accepts images, not PDF files.")

            # Keep the page image inside the job directory so the structured
            # model can reference it later (searchable-PDF/DOCX export needs it).
            image_ref: str | None = None
            if prepared.display_bytes:
                stored = destination / f"page_{index:04d}.png"
                stored.write_bytes(prepared.display_bytes)
                image_ref = str(stored)

            page_number = len(doc_pages) + 1
            doc_page = normalize_page(
                {}, page_number=page_number, page_width=prepared.width,
                page_height=prepared.height, rotation=prepared.rotation,
                image_ref=image_ref, warnings=list(prepared.warnings),
            )
            doc_pages.append(doc_page)
            page_texts.append(page_plain_text(doc_page))

        document = build_document(
            doc_pages,
            source_filename=f"book_{job_id}",
            engine=_ENGINE_NAME,
            document_id=job_id,
        )

            # Keep each processed scan as the visible page in the exported PDF.
        if not any(page.imageRef for page in doc_pages):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "The merged book PDF needs image pages; PDF uploads are "
                    "parsed but do not currently include raster page images."
                ),
            )
        pdf_path = destination / "book.pdf"
        try:
            pdf_path.write_bytes(export_pdf(document))
        except ExportUnavailable as exc:
            log.error("Could not build the merged searchable PDF: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not build the merged PDF: {exc}",
            ) from exc

        json_path = destination / "document.json"
        json_path.write_text(
            document.model_dump_json(indent=2, exclude_none=False), encoding="utf-8"
        )

        db.add(
            OcrJob(
                id=job_id,
                user_email=user_email,
                kind="book",
                status="completed",
                page_count=len(doc_pages),
                pdf_path=str(pdf_path) if pdf_path else None,
                json_path=str(json_path),
                created_at=_utcnow(),
            )
        )
        db.flush()

    except (ScannerBusy, ScannerUnavailable, ScannerFailed) as exc:
        safe_rmtree(destination)
        log.warning("Book structure parse unavailable/failed: %s", exc)
        raise _pipeline_http_error(exc) from exc
    except HTTPException:
        safe_rmtree(destination)
        raise
    finally:
        safe_rmtree(workdir)

    log.info("Book job %s completed: %d pages", job_id, len(doc_pages))
    return BookStructureResponse(
        jobId=job_id,
        pageCount=len(doc_pages),
        bookPdfUrl=f"/document/book/{job_id}/file",
        document=document,
        pages=page_texts,
    )


# ---------------------------------------------------------------------------
# GET /document/book/{job_id}/file
# ---------------------------------------------------------------------------
@router.get(
    "/book/{job_id}/file",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Merged book PDF"},
        404: {"description": "Unknown job, or its PDF is gone"},
    },
)
def download_book_pdf(job_id: str, db: Session = Depends(get_db)) -> FileResponse:
    job = db.get(OcrJob, job_id)
    if job is None or not job.pdf_path:
        raise HTTPException(status_code=404, detail="That scan job wasn't found.")

    path = Path(job.pdf_path)
    if not is_within(path, storage_root()) or not path.is_file():
        log.error("Book job %s references a missing PDF at %s", job_id, path)
        raise HTTPException(status_code=404, detail="That scan job wasn't found.")

    return FileResponse(
        path=path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="prism_scan_{job_id[:8]}.pdf"'
        },
    )


# ---------------------------------------------------------------------------
# POST /document/{format}/export
# ---------------------------------------------------------------------------
@router.post(
    "/{export_format}/export",
    response_class=Response,
    responses={
        200: {"description": "The reconstructed document"},
        400: {"description": "Unsupported format or malformed document model"},
        503: {"description": "The writer library for that format isn't installed"},
    },
)
def export_document(
    export_format: str,
    body: dict[str, Any] = Body(...),
) -> Response:
    """Reconstruct a structured document model into pdf/docx/markdown/xlsx."""
    key = (export_format or "").strip().lower()
    if key == "md":
        key = "markdown"
    if key == "excel":
        key = "xlsx"
    if key == "word":
        key = "docx"

    if key not in EXPORTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported export format '{export_format}'. "
                f"Use one of: {', '.join(sorted(EXPORTERS))}."
            ),
        )

    # Accept both {"document": {...}} and the bare document model.
    payload = body.get("document") if isinstance(body.get("document"), dict) else body
    requested_name = body.get("filename") if isinstance(body.get("filename"), str) else None

    try:
        document = StructuredDocument.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"That isn't a valid structured document model: {exc.error_count()} problem(s).",
        ) from exc

    if not document.pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The document model contains no pages.",
        )

    writer, media_type, extension = EXPORTERS[key]

    try:
        content = writer(document)
    except Exception as exc:
        # The three writers raise their own ExportUnavailable when the library
        # is missing; anything else is a genuine failure to render.
        if type(exc).__name__ == "ExportUnavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        log.exception("Export to %s failed", key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not export the document as {key}: {exc}",
        ) from exc

    stem = requested_name or document.sourceFilename or "document"
    stem = Path(stem).stem.replace('"', "").replace("\r", "").replace("\n", "") or "document"
    filename = f"{stem}.{extension}"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
