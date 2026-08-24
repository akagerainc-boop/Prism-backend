"""POST /passport-photo/process.

Contract (from ``lib/services/passport_photo_service.dart``): multipart field
``file``; the response body is the processed image as **raw bytes** -- no JSON
wrapper. The client reads ``response.bodyBytes`` directly.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from ..config import settings
from ..logging_config import get_logger
from ..passport import (
    SegmentationFailed,
    SegmentationUnavailable,
    process_passport_photo,
)

log = get_logger(__name__)

router = APIRouter(prefix="/passport-photo", tags=["passport-photo"])


@router.post(
    "/process",
    response_class=Response,
    responses={
        200: {
            "content": {"image/jpeg": {}},
            "description": "Processed photo on a solid white background",
        },
        422: {"description": "Segmentation produced an implausible result"},
        503: {"description": "Segmentation backend unavailable"},
    },
)
def process(file: UploadFile = File(...)) -> Response:
    data = file.file.read(settings.max_upload_bytes + 1)
    file.file.close()

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No image was uploaded."
        )
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That image is too large.",
        )

    try:
        processed = process_passport_photo(data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except SegmentationFailed as exc:
        # Deliberately an error, not a best-effort image: the client falls back
        # to the locally-edited photo, which beats returning something garbled.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except SegmentationUnavailable as exc:
        log.error("Passport segmentation unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    return Response(
        content=processed,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": 'inline; filename="passport.jpg"',
            "Cache-Control": "no-store",
        },
    )
