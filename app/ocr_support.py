"""Shared helpers for the OCR endpoints: upload handling and preprocessing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastapi import HTTPException, UploadFile, status

from .config import settings
from .imaging import encode_png, load_rgb_array
from .opencv_document_scanner import scan_array
from .logging_config import get_logger
from .storage import tmp_dir

log = get_logger(__name__)

PDF_MAGIC = b"%PDF-"


@dataclass
class PreparedPage:
    """One page ready for the OpenCV document scanner."""

    path: Path  # what the pipeline reads
    original_path: Path  # the bytes exactly as uploaded
    width: float
    height: float
    rotation: float
    is_pdf: bool
    warnings: list[str]
    display_bytes: bytes  # image used for the book PDF / searchable-PDF layer


def read_upload(upload: UploadFile) -> bytes:
    """Read an upload, enforcing the size cap."""
    data = upload.file.read(settings.max_upload_bytes + 1)
    try:
        upload.file.close()
    except Exception:  # pragma: no cover
        pass

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file is empty.",
        )
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "That file is larger than the "
                f"{settings.max_upload_bytes // (1024 * 1024)} MB limit."
            ),
        )
    return data


def make_workdir(prefix: str = "job") -> Path:
    path = tmp_dir() / f"{prefix}_{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_page(
    data: bytes,
    workdir: Path,
    *,
    stem: str = "page",
    enable_preprocessing: bool = True,
) -> PreparedPage:
    """Persist the upload and (for images) run the preprocessing chain.

    PDFs are not accepted because the selected OpenCV scanner operates on
    raster images.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if data.startswith(PDF_MAGIC):
        original = workdir / f"{stem}.pdf"
        original.write_bytes(data)
        return PreparedPage(
            path=original,
            original_path=original,
            width=0.0,
            height=0.0,
            rotation=0.0,
            is_pdf=True,
            warnings=["PDF input: image preprocessing was skipped."],
            display_bytes=b"",
        )

    try:
        array = load_rgb_array(data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    max_dimension = settings.max_ocr_image_dimension
    height, width = array.shape[:2]
    longest_edge = max(height, width)
    if max_dimension > 0 and longest_edge > max_dimension:
        scale = max_dimension / longest_edge
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        from PIL import Image

        array = np.asarray(
            Image.fromarray(array).resize(
                (resized_width, resized_height), Image.Resampling.LANCZOS
            )
        )
        warnings.append(
            f"Image downscaled to {resized_width}x{resized_height} for OCR performance."
        )

    original = workdir / f"{stem}_original.png"
    original.write_bytes(encode_png(array))

    rotation = 0.0
    if enable_preprocessing:
        array = scan_array(array)
        warnings.append("OpenCV-Document-Scanner produced the processed page image.")

    prepared_bytes = encode_png(array)
    prepared = workdir / f"{stem}.png"
    prepared.write_bytes(prepared_bytes)

    height, width = array.shape[0], array.shape[1]
    return PreparedPage(
        path=prepared,
        original_path=original,
        width=float(width),
        height=float(height),
        rotation=rotation,
        is_pdf=False,
        warnings=warnings,
        display_bytes=prepared_bytes,
    )
