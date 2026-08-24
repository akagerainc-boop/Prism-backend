"""Passport-photo background replacement.

Product requirement, verbatim: *"just make python codes just it will delete user
background and make it white clear white of a cloth but a white"* -- i.e. segment
the person out and drop them onto a clean, solid white backdrop (as if they were
shot against a white cloth). The result is **opaque white**, never transparency:
passport specifications require a plain light background, and a transparent PNG
would print black on most pipelines.

Implementation: ``rembg`` (``u2net_human_seg`` by default -- the portrait-tuned
model) produces the subject mask, then Pillow composites the original pixels
over a solid #FFFFFF canvas at the original dimensions.

``rembg``/``onnxruntime`` are imported lazily so the rest of the API still boots
when they are not installed (see README: they may not have wheels for the
Python version in use).
"""

from __future__ import annotations

import io
import threading
from typing import Any

from PIL import Image, ImageOps

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

WHITE = (255, 255, 255)

_session: Any = None
_session_lock = threading.Lock()


class SegmentationUnavailable(RuntimeError):
    """``rembg`` (or its onnxruntime backend) is not installed/usable."""


class SegmentationFailed(RuntimeError):
    """A mask was produced but it is not a plausible portrait segmentation."""


def _get_session() -> Any:
    """Create the rembg session once and reuse it (model load is expensive)."""
    global _session
    if _session is not None:
        return _session

    with _session_lock:
        if _session is not None:
            return _session
        try:
            from rembg import new_session  # type: ignore[import-not-found]
        except Exception as exc:  # ImportError, or onnxruntime load failure
            raise SegmentationUnavailable(
                "rembg is not available. Install it with "
                "`pip install rembg onnxruntime` (see README -- it needs a "
                "Python version with onnxruntime wheels)."
            ) from exc

        try:
            _session = new_session(settings.rembg_model)
        except Exception as exc:
            raise SegmentationUnavailable(
                f"Could not load the rembg model '{settings.rembg_model}'. The "
                "weights download on first use -- check network access."
            ) from exc

        log.info("rembg session ready (model=%s)", settings.rembg_model)
        return _session


def _load_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError("That file isn't a readable image.") from exc

    # Honour the camera's EXIF rotation before anything else, so the subject is
    # upright in the output.
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:  # pragma: no cover - malformed EXIF
        pass

    return image.convert("RGB")


def _mask_quality(mask: Image.Image) -> tuple[float, float]:
    """Return (confident-foreground ratio, ambiguous ratio) for an 'L' mask."""
    histogram = mask.histogram()
    total = sum(histogram) or 1
    foreground = sum(histogram[200:])  # confidently subject
    ambiguous = sum(histogram[64:200])  # neither clearly subject nor background
    return foreground / total, ambiguous / total


def process_passport_photo(data: bytes) -> bytes:
    """Segment the subject and composite it onto solid white.

    Returns encoded JPEG bytes at the original image dimensions.

    Raises:
        ValueError: the upload is not a readable image.
        SegmentationUnavailable: rembg/onnxruntime missing (-> 503).
        SegmentationFailed: the mask is implausible (-> 422); the client falls
            back to the locally-edited photo rather than showing a garbled one.
    """
    image = _load_image(data)
    session = _get_session()

    try:
        from rembg import remove  # type: ignore[import-not-found]

        # only_mask -> a single-channel subject mask we composite ourselves,
        # which keeps full control of the background colour.
        mask = remove(
            image,
            session=session,
            only_mask=True,
            post_process_mask=True,
        )
    except Exception as exc:
        raise SegmentationUnavailable(
            "Background segmentation failed to run."
        ) from exc

    if not isinstance(mask, Image.Image):  # pragma: no cover - defensive
        try:
            mask = Image.open(io.BytesIO(mask))
        except Exception as exc:
            raise SegmentationFailed("Segmentation returned an unusable mask.") from exc

    mask = mask.convert("L")
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.LANCZOS)

    foreground_ratio, ambiguous_ratio = _mask_quality(mask)

    # Guard rails: an empty mask (nobody found) or a near-full mask (the model
    # decided the whole frame is subject) would both produce a useless photo.
    if foreground_ratio < settings.passport_min_subject_ratio:
        raise SegmentationFailed(
            "No clear subject was found in the photo. Retake it with the face "
            "filling more of the frame."
        )
    if foreground_ratio > settings.passport_max_subject_ratio:
        raise SegmentationFailed(
            "The background couldn't be separated from the subject. Retake the "
            "photo against a plainer, more contrasting background."
        )
    if ambiguous_ratio > 0.35:
        raise SegmentationFailed(
            "The subject outline is too indistinct to cut out cleanly. Retake "
            "the photo with more even lighting."
        )

    canvas = Image.new("RGB", image.size, WHITE)
    canvas.paste(image, (0, 0), mask)

    buffer = io.BytesIO()
    canvas.save(
        buffer,
        format="JPEG",
        quality=settings.passport_jpeg_quality,
        subsampling=0,  # 4:4:4 -- avoids colour bleed at the hair/white edge
        optimize=True,
    )

    log.info(
        "Passport photo processed (%dx%d, subject=%.1f%%)",
        image.width,
        image.height,
        foreground_ratio * 100,
    )
    return buffer.getvalue()
