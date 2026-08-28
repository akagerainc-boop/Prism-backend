"""Passport-photo background replacement.

Product requirement: segment the person out (hair, ears, shoulders, neck,
clothing -- the *whole* silhouette, not just a face crop) and drop them onto
a clean, solid #FFFFFF backdrop, as if shot in front of a professional white
studio background. The result is **opaque white**, never transparency:
passport specifications require a plain light background, and a transparent
PNG would print black on most pipelines.

Pipeline: ``rembg`` (``u2net_human_seg`` by default -- the portrait-tuned
model) locates the subject, then **real closed-form alpha matting**
(``rembg``'s ``alpha_matting=True``, backed by ``pymatting``) refines a
continuous, sub-pixel alpha channel from that coarse mask -- this is what
actually captures individual hair strands and soft/semi-transparent edges,
and its foreground-colour estimation step is what prevents a visible
background-colour halo around hair. A plain segmentation mask (the previous
implementation here) cannot do either of those; it can only produce a hard
or near-hard edge.

**Important:** ``only_mask=True`` (the old approach) silently *disables*
``alpha_matting`` inside rembg's own ``remove()`` -- the two are mutually
exclusive in rembg's implementation, not merely independent options. That
combination is why the old code never actually benefited from matting.

``rembg``/``onnxruntime``/``pymatting`` are imported lazily so the rest of
the API still boots when they are not installed (see README: they may not
have wheels for the Python version in use).
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any

from PIL import Image, ImageOps

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

WHITE = (255, 255, 255)

# alpha_matting_erode_size is a FIXED PIXEL COUNT, not proportional to image
# size. A modern phone photo is routinely 3000-4000px+ on its long edge; at
# that scale, eroding by 10px barely shrinks the confident foreground/
# background regions at all, leaving almost no "unknown" band for the
# matting solver to actually work in -- so it degrades toward the original
# hard-ish mask rather than producing a real soft matte, which is exactly
# the kind of bad-background/odd-cut artifact this is meant to prevent.
# Segmenting at a fixed, moderate working resolution keeps the erode size
# (and thresholds) proportionally meaningful regardless of the source
# photo's resolution, and is dramatically cheaper to run to boot -- the
# final composite still happens at the original resolution (the cutout is
# upscaled back before compositing), so output quality isn't reduced.
_MATTING_WORKING_MAX_DIM = 1400

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
                "`pip install rembg onnxruntime pymatting` (see README -- it "
                "needs a Python version with onnxruntime wheels)."
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
    """Return (confident-foreground ratio, ambiguous ratio) for an 'L' mask.

    With a real alpha matte (as opposed to a coarse binary mask), the
    "ambiguous" band is exactly where it should be: soft hair/edge pixels
    with a genuine partial-transparency value, not model uncertainty.
    """
    histogram = mask.histogram()
    total = sum(histogram) or 1
    foreground = sum(histogram[200:])  # confidently subject
    ambiguous = sum(histogram[64:200])  # soft edges (hair, semi-transparent)
    return foreground / total, ambiguous / total


def _corner_background_ok(canvas: Image.Image, patch: int = 14, tolerance: int = 14) -> bool:
    """Quality-control check (spec: "ensure the background is exactly or
    visually close to pure white"): sample each of the composited image's
    four corners -- reliably background in any properly framed passport
    photo -- and confirm compositing actually produced white there, not
    leftover subject/background pixels that escaped segmentation.
    """
    width, height = canvas.size
    patch = max(1, min(patch, width // 4, height // 4))
    corners = [
        (0, 0, patch, patch),
        (width - patch, 0, width, patch),
        (0, height - patch, patch, height),
        (width - patch, height - patch, width, height),
    ]
    for box in corners:
        # Box-filter the corner patch down to one pixel -- a cheap average
        # that tolerates a few stray noisy/anti-aliased pixels.
        averaged = canvas.crop(box).resize((1, 1), Image.Resampling.BOX)
        r, g, b = averaged.getpixel((0, 0))[:3]
        if 255 - min(r, g, b) > tolerance:
            return False
    return True


def process_passport_photo(data: bytes) -> bytes:
    """Segment the subject with real alpha matting and composite onto
    solid white.

    Returns encoded JPEG bytes at the original image dimensions.

    Raises:
        ValueError: the upload is not a readable image.
        SegmentationUnavailable: rembg/onnxruntime/pymatting missing (-> 503).
        SegmentationFailed: the result is implausible (-> 422); the client
            falls back to the locally-edited photo rather than showing a
            garbled one.
    """
    request_started = time.monotonic()
    image = _load_image(data)
    log.info(
        "Passport photo: loaded image %dx%d (%d bytes uploaded)",
        image.width, image.height, len(data),
    )

    session_started = time.monotonic()
    session = _get_session()
    log.info(
        "Passport photo: rembg session ready in %.2fs (model=%s)",
        time.monotonic() - session_started, settings.rembg_model,
    )

    # Segment/matte at a fixed working resolution -- see
    # _MATTING_WORKING_MAX_DIM's comment for why -- then upscale the result
    # back to the original size below, before the guardrail checks and the
    # final composite.
    longest_edge = max(image.size)
    if longest_edge > _MATTING_WORKING_MAX_DIM:
        scale = _MATTING_WORKING_MAX_DIM / longest_edge
        working_image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        log.info(
            "Passport photo: downscaled to %dx%d for matting (from %dx%d)",
            working_image.width, working_image.height, image.width, image.height,
        )
    else:
        working_image = image

    matting_started = time.monotonic()
    try:
        from rembg import remove  # type: ignore[import-not-found]

        # alpha_matting=True: closed-form matting (pymatting) refines the
        # coarse u2net mask into a real, continuous alpha channel, with its
        # own foreground-colour estimation to stop background colour from
        # bleeding into semi-transparent hair pixels (the actual cause of a
        # visible halo). NOT combined with only_mask -- rembg's remove()
        # silently ignores alpha_matting when only_mask=True, so requesting
        # the raw mask and matting at once is not possible; the RGBA cutout
        # below carries both the matte (alpha channel) and the
        # colour-corrected foreground (RGB channels) together.
        cutout = remove(
            working_image,
            session=session,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
            post_process_mask=True,
        )
    except Exception as exc:
        # Log the REAL underlying exception -- type, message, and full
        # traceback -- before replacing it with the generic client-facing
        # SegmentationUnavailable. Without this, an OOM kill, a pymatting
        # numerical failure, or any other genuine crash inside remove() was
        # completely invisible in the logs; only the generic message
        # survived, giving no way to tell "briefly overloaded" apart from
        # "will never work" or "specific bug in this exact photo".
        log.exception(
            "Passport photo: segmentation raised %s after %.2fs: %s",
            type(exc).__name__, time.monotonic() - matting_started, exc,
        )
        raise SegmentationUnavailable(
            "Background segmentation failed to run."
        ) from exc

    log.info(
        "Passport photo: matting completed in %.2fs", time.monotonic() - matting_started,
    )

    if not isinstance(cutout, Image.Image):  # pragma: no cover - defensive
        try:
            cutout = Image.open(io.BytesIO(cutout))
        except Exception as exc:
            log.exception("Passport photo: rembg returned an unusable result: %s", exc)
            raise SegmentationFailed("Segmentation returned an unusable result.") from exc

    cutout = cutout.convert("RGBA")
    if cutout.size != image.size:
        cutout = cutout.resize(image.size, Image.Resampling.LANCZOS)

    mask = cutout.getchannel("A")

    foreground_ratio, ambiguous_ratio = _mask_quality(mask)
    log.info(
        "Passport photo: foreground_ratio=%.3f ambiguous_ratio=%.3f "
        "(min=%.3f max=%.3f)",
        foreground_ratio, ambiguous_ratio,
        settings.passport_min_subject_ratio, settings.passport_max_subject_ratio,
    )

    # Guard rails: an empty mask (nobody found) or a near-full mask (the model
    # decided the whole frame is subject) would both produce a useless photo.
    if foreground_ratio < settings.passport_min_subject_ratio:
        log.warning(
            "Passport photo: rejected -- foreground_ratio %.3f below minimum %.3f",
            foreground_ratio, settings.passport_min_subject_ratio,
        )
        raise SegmentationFailed(
            "No clear subject was found in the photo. Retake it with the face "
            "filling more of the frame."
        )
    if foreground_ratio > settings.passport_max_subject_ratio:
        log.warning(
            "Passport photo: rejected -- foreground_ratio %.3f above maximum %.3f",
            foreground_ratio, settings.passport_max_subject_ratio,
        )
        raise SegmentationFailed(
            "The background couldn't be separated from the subject. Retake the "
            "photo against a plainer, more contrasting background."
        )
    if ambiguous_ratio > 0.35:
        log.warning(
            "Passport photo: rejected -- ambiguous_ratio %.3f above 0.35",
            ambiguous_ratio,
        )
        raise SegmentationFailed(
            "The subject outline is too indistinct to cut out cleanly. Retake "
            "the photo with more even lighting."
        )

    canvas = Image.new("RGB", image.size, WHITE)
    # Composite the matte's own foreground-colour-corrected RGB, not the
    # original pixels -- pasting the original through a soft alpha would
    # let the original background colour bleed through translucent hair
    # pixels, producing exactly the halo the matting step is meant to avoid.
    canvas.paste(cutout.convert("RGB"), (0, 0), mask)

    if not _corner_background_ok(canvas):
        log.warning("Passport photo: rejected -- corners of the composite weren't white")
        raise SegmentationFailed(
            "The background wasn't fully removed at the photo's edges. Retake "
            "the photo with the subject centred and more margin around them."
        )

    buffer = io.BytesIO()
    canvas.save(
        buffer,
        format="JPEG",
        quality=settings.passport_jpeg_quality,
        subsampling=0,  # 4:4:4 -- avoids colour bleed at the hair/white edge
        optimize=True,
    )

    log.info(
        "Passport photo processed (%dx%d, subject=%.1f%%, %d bytes out, "
        "total %.2fs)",
        image.width,
        image.height,
        foreground_ratio * 100,
        buffer.tell(),
        time.monotonic() - request_started,
    )
    return buffer.getvalue()
