"""Passport-photo background replacement.

Product requirement: segment the person out (hair, ears, shoulders, neck,
clothing -- the *whole* silhouette, not just a face crop) and drop them onto
a clean, solid #FFFFFF backdrop, as if shot in front of a professional white
studio background. The result is **opaque white**, never transparency:
passport specifications require a plain light background, and a transparent
PNG would print black on most pipelines.

Pipeline: ``rembg`` (``u2net_human_seg`` -- the portrait-tuned model)
segments the subject into a mask, a light Gaussian blur softens the mask's
edge (cheap "feathering" -- avoids a harsh, jagged pixel-stair cutout without
the cost of real alpha matting), then that mask composites the original
photo onto solid white.

**Deliberately lightweight, not maximum-precision.** An earlier version of
this used real closed-form alpha matting (``rembg``'s ``alpha_matting=True``,
backed by ``pymatting``) for genuinely better hair-strand-level edges. It
also measured 6+ seconds for the matting step ALONE on a small, easy test
image on a fast local machine -- on Render's shared, resource-limited CPU,
that cost (on top of model load / cold start) was landing this feature in
request-timeout/hang territory more than it was landing clean photos. This
version trades some edge precision (a few pixels of soft blur, not a true
per-strand alpha matte -- and unlike real matting, a blurred mask alone
doesn't correct for background colour bleeding into that soft band, so a
faint colour fringe at the edge is possible, especially against a strongly
coloured background) for being dramatically cheaper to run and much less
likely to time out. If you want the higher-precision version back, the
matting approach is straightforward to reintroduce; it's the trade-off that
changed, not a mistake in either version.

``rembg``/``onnxruntime`` are imported lazily so the rest of the API still
boots when they are not installed (see README: they may not have wheels for
the Python version in use).
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any

from PIL import Image, ImageFilter, ImageOps

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

WHITE = (255, 255, 255)

# Segmenting at a fixed, moderate working resolution regardless of the
# source photo's actual size keeps this fast and its memory use bounded on
# Render's limited CPU/RAM -- a modern phone photo can be 3000-4000px+ on
# its long edge, and u2net's own accuracy doesn't meaningfully improve past
# a much lower resolution anyway (its internal inference size is far
# smaller than this). The final composite still happens at the original
# resolution (the mask is upscaled back before compositing), so output
# quality isn't reduced.
_SEGMENTATION_WORKING_MAX_DIM = 1400

# Gaussian blur radius (px, at the working resolution above) used to
# feather the mask edge -- cheap insurance against a hard/jagged cutout,
# nowhere near the cost of real alpha matting.
_FEATHER_RADIUS = 2.5

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
                "`pip install rembg onnxruntime` (see README -- it "
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


def warm_up_session() -> None:
    """Load (and, on a cold instance, download) the rembg model once, up
    front, instead of lazily on whoever's request happens to arrive first.

    Render's filesystem is ephemeral -- wiped on every deploy -- so the
    model weights (~176MB) can end up re-downloading from scratch on the
    first request after every single deploy. Left lazy, that download (plus
    the CPU-bound session setup) happens inside a real user's request and
    can easily outlast any reasonable timeout, which is exactly what
    produced silent, log-free hangs after "loaded image" and nothing else.
    Call this from a background thread at server startup (see main.py's
    lifespan) so a slow first load happens once, at deploy time, with
    nobody waiting on it -- not inside a user-facing request.
    """
    try:
        _get_session()
    except SegmentationUnavailable as exc:
        # Not fatal to the whole server -- every other feature still works.
        # The passport-photo endpoint itself will raise the same error (and
        # log it) on first real use, same as before this warm-up existed.
        log.error("Passport photo: warm-up could not load the rembg model: %s", exc)


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

    "Ambiguous" here is the feathered edge band plus any genuine model
    uncertainty -- with the lightweight blur-feather approach (not real
    alpha matting) the two aren't distinguishable from the mask alone, but
    the same ratios still work as a sanity check: a huge ambiguous band
    means segmentation itself was poor, not just that edges are soft.
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
    """Segment the subject (lightweight: mask + blur-feather, not full
    alpha matting -- see the module docstring for why) and composite onto
    solid white.

    Returns encoded JPEG bytes at the original image dimensions.

    Raises:
        ValueError: the upload is not a readable image.
        SegmentationUnavailable: rembg/onnxruntime missing (-> 503).
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

    # Segment at a fixed working resolution -- see
    # _SEGMENTATION_WORKING_MAX_DIM's comment for why -- then upscale the
    # mask back to the original size below, before the guardrail checks and
    # the final composite (which uses the ORIGINAL full-resolution photo).
    longest_edge = max(image.size)
    if longest_edge > _SEGMENTATION_WORKING_MAX_DIM:
        scale = _SEGMENTATION_WORKING_MAX_DIM / longest_edge
        working_image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
        log.info(
            "Passport photo: downscaled to %dx%d for segmentation (from %dx%d)",
            working_image.width, working_image.height, image.width, image.height,
        )
    else:
        working_image = image

    segmentation_started = time.monotonic()
    try:
        from rembg import remove  # type: ignore[import-not-found]

        # only_mask=True: a single-channel subject mask, not a full RGBA
        # cutout -- this is the fast path (no alpha-matting solve). We
        # composite the ORIGINAL image through this mask ourselves below,
        # after feathering its edge with a cheap Gaussian blur.
        mask = remove(
            working_image,
            session=session,
            only_mask=True,
            post_process_mask=True,
        )
    except Exception as exc:
        # Log the REAL underlying exception -- type, message, and full
        # traceback -- before replacing it with the generic client-facing
        # SegmentationUnavailable. Without this, an OOM kill or any other
        # genuine crash inside remove() was completely invisible in the
        # logs; only the generic message survived, giving no way to tell
        # "briefly overloaded" apart from "will never work" or "specific
        # bug in this exact photo".
        log.exception(
            "Passport photo: segmentation raised %s after %.2fs: %s",
            type(exc).__name__, time.monotonic() - segmentation_started, exc,
        )
        raise SegmentationUnavailable(
            "Background segmentation failed to run."
        ) from exc

    log.info(
        "Passport photo: segmentation completed in %.2fs",
        time.monotonic() - segmentation_started,
    )

    if not isinstance(mask, Image.Image):  # pragma: no cover - defensive
        try:
            mask = Image.open(io.BytesIO(mask))
        except Exception as exc:
            log.exception("Passport photo: rembg returned an unusable mask: %s", exc)
            raise SegmentationFailed("Segmentation returned an unusable result.") from exc

    mask = mask.convert("L")
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.LANCZOS)

    # Cheap feathering: softens the mask edge so the cutout isn't a harsh,
    # jagged pixel-stair line, without the cost of real alpha matting. See
    # the module docstring for the honest trade-off (a faint colour fringe
    # is possible in this soft band, since -- unlike real matting -- this
    # doesn't correct for background colour bleeding into it).
    mask = mask.filter(ImageFilter.GaussianBlur(radius=_FEATHER_RADIUS))

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
    # Composite the ORIGINAL full-resolution photo through the (feathered)
    # mask -- there's no separate colour-corrected cutout in this
    # lightweight approach, just a mask, so the original pixels are what
    # there is to paste. See the module docstring: this is the one place
    # the lighter approach genuinely gives something up versus real alpha
    # matting -- a faint background-colour fringe is possible in the
    # feathered band, since nothing here decontaminates it.
    canvas.paste(image, (0, 0), mask)

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
