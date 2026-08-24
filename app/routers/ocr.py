"""The two legacy text endpoint contracts.

**These response shapes are frozen.** ``lib/services/cloud_ocr_service.dart``
already depends on them exactly as written:

    POST /document/ocr        multipart file="file"
        <- {"text": "..."}

    POST /document/ocr/book   multipart meta=<json>, repeated files "pages"
        <- {"pages": ["page 0 text", "page 1 text", ...]}

Do not rename these keys or add required fields. OpenCV-Document-Scanner
produces page images, not text recognition, so these endpoints preserve the
client contract and return an empty text value after scanning.

On failure these return a non-2xx, which the client treats as "fall back to
on-device OCR" -- that is the intended behaviour, not a bug to paper over with a
fabricated empty success.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..logging_config import get_logger
from ..ocr_support import make_workdir, prepare_page, read_upload
from ..opencv_document_scanner import ScannerBusy, ScannerFailed, ScannerUnavailable
from ..schemas import OcrBookResponse, OcrTextResponse
from ..storage import safe_rmtree

log = get_logger(__name__)

router = APIRouter(prefix="/document", tags=["ocr"])


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


@router.post("/ocr", response_model=OcrTextResponse)
def ocr_single(file: UploadFile = File(...)) -> OcrTextResponse:
    """Recognise the text in one page and return it as plain text."""
    data = read_upload(file)
    workdir = make_workdir("ocr")

    try:
        page = prepare_page(data, workdir, stem="page")
        text = ""
    except (ScannerBusy, ScannerUnavailable, ScannerFailed) as exc:
        log.warning("OCR unavailable/failed: %s", exc)
        raise _pipeline_http_error(exc) from exc
    finally:
        safe_rmtree(workdir)

    return OcrTextResponse(text=text)


@router.post("/ocr/book", response_model=OcrBookResponse)
def ocr_book(
    meta: str | None = Form(default=None),
    pages: list[UploadFile] = File(...),
) -> OcrBookResponse:
    """Recognise a batch of pages submitted as one bound document.

    ``meta`` is the client's ``{"kind": "book", "pageCount": N}`` JSON. It is
    advisory -- the uploaded file count is authoritative -- but a mismatch is
    logged because it usually means a page failed to attach on the client.
    """
    if not pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No pages were uploaded."
        )

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
        except (json.JSONDecodeError, AttributeError):
            log.warning("Ignoring unparsable book meta field")

    workdir = make_workdir("book")
    texts: list[str] = []

    try:
        for index, upload in enumerate(pages):
            data = read_upload(upload)
            prepare_page(data, workdir, stem=f"page_{index}")
            texts.append("")
    except (ScannerBusy, ScannerUnavailable, ScannerFailed) as exc:
        log.warning("Book OCR unavailable/failed: %s", exc)
        raise _pipeline_http_error(exc) from exc
    finally:
        safe_rmtree(workdir)

    return OcrBookResponse(pages=texts)
