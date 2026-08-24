"""Resolve the image bytes backing a page or a figure element.

Images can reach an exporter three ways:
  1. ``DocPage.imageBase64`` -- inline, which is how a client round-trips a
     document through ``POST /document/{format}/export`` without the server
     needing to still have the original file;
  2. ``DocPage.imageRef`` / ``DocElement.sourceImage`` -- a path that still
     exists on this server (same-process export straight after OCR);
  3. neither, in which case the exporter degrades to text-only.

Any path is resolved strictly inside ``PRISM_STORAGE_ROOT``: a client-supplied
document model is untrusted input, so ``imageRef`` must never be able to read
arbitrary files off the host.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from ..logging_config import get_logger
from ..schemas import DocElement, DocPage, StructuredDocument
from ..storage import is_within, storage_root

log = get_logger(__name__)


def _decode_base64(value: str | None) -> bytes | None:
    if not value:
        return None
    payload = value
    if payload.startswith("data:"):  # strip a data: URI prefix
        _, _, payload = payload.partition(",")
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        log.debug("Could not decode inline image: %s", exc)
        return None


def _read_path(reference: str | None) -> bytes | None:
    if not reference:
        return None
    try:
        path = Path(reference)
    except (TypeError, ValueError):
        return None

    root = storage_root()
    if not path.is_absolute():
        path = root / path

    # Untrusted input -- never read outside the storage root.
    if not is_within(path, root):
        log.warning("Refused to read image outside the storage root: %s", reference)
        return None
    if not path.is_file():
        return None

    try:
        return path.read_bytes()
    except OSError as exc:  # pragma: no cover
        log.debug("Could not read image %s: %s", path, exc)
        return None


def page_image_bytes(page: DocPage) -> bytes | None:
    return _decode_base64(page.imageBase64) or _read_path(page.imageRef)


def element_image_bytes(
    element: DocElement, document: StructuredDocument
) -> bytes | None:
    """Bytes for a figure/chart element, cropped from its page when possible."""
    inline = _decode_base64(getattr(element, "imageBase64", None))
    if inline:
        return inline

    page = next((p for p in document.pages if p.pageNumber == element.page), None)
    if page is None:
        return _read_path(element.sourceImage)

    source = page_image_bytes(page)
    if not source:
        return _read_path(element.sourceImage)

    if len(element.bbox) != 4:
        return None

    # Crop the element's region out of the full page image so figures are
    # preserved in the reconstruction rather than discarded.
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(source)) as image:
            image.load()
            x0, y0, x1, y1 = (int(round(v)) for v in element.bbox)
            x0 = max(0, min(x0, image.width))
            x1 = max(0, min(x1, image.width))
            y0 = max(0, min(y0, image.height))
            y1 = max(0, min(y1, image.height))
            if x1 - x0 < 4 or y1 - y0 < 4:
                return None
            cropped = image.convert("RGB").crop((x0, y0, x1, y1))
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception as exc:  # pragma: no cover
        log.debug("Could not crop element %s: %s", element.id, exc)
        return None
