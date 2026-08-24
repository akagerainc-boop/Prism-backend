"""Document image preprocessing that runs *before* the OCR pipeline.

Covers the preprocessing the product spec asks for:
  * orientation correction (EXIF + coarse 90-degree detection)
    * optional perspective correction (disabled for the scanner path)
  * deskewing (residual small-angle rotation)
  * noise reduction
  * contrast / illumination correction

Design notes
------------
* OpenCV is imported lazily. If it is missing, every step degrades to a no-op
    and the original image is passed through.
* Preprocessing is intentionally *conservative*. Over-aggressive
    binarisation/denoising can hurt page quality. Each step is individually switchable via :class:`PreprocessOptions`
  and bails out rather than guessing when its own confidence is low.
* Every step is a pure function on a numpy array so they can be reordered or
  tested individually.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .logging_config import get_logger

log = get_logger(__name__)

_cv2: Any = None
_cv2_checked = False


def _get_cv2() -> Any | None:
    """Import OpenCV once; return None (and warn once) when unavailable."""
    global _cv2, _cv2_checked
    if _cv2_checked:
        return _cv2
    _cv2_checked = True
    try:
        import cv2  # type: ignore[import-not-found]

        _cv2 = cv2
    except Exception as exc:  # pragma: no cover
        log.warning(
            "OpenCV unavailable (%s) -- image preprocessing will be skipped.", exc
        )
        _cv2 = None
    return _cv2


@dataclass
class PreprocessOptions:
    orientation: bool = True
    perspective: bool = True
    deskew: bool = True
    denoise: bool = True
    illumination: bool = True
    # Skew below this many degrees is left alone (rotating costs sharpness).
    min_deskew_degrees: float = 0.35
    max_deskew_degrees: float = 15.0
    # The page quad must cover at least this fraction of the frame to be
    # trusted as a document boundary. A page photographed with margins can be
    # smaller than a third of the camera frame.
    min_quad_area_ratio: float = 0.20


@dataclass
class PreprocessResult:
    image: np.ndarray
    applied: list[str] = field(default_factory=list)
    rotation_applied: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loading / encoding
# ---------------------------------------------------------------------------
def load_rgb_array(data: bytes) -> np.ndarray:
    """Decode bytes to an RGB numpy array, applying EXIF orientation."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError("That file isn't a readable image.") from exc

    try:
        image = ImageOps.exif_transpose(image)
    except Exception:  # pragma: no cover
        pass

    return np.asarray(image.convert("RGB"))


def to_pil(array: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(array), mode="RGB")


def encode_png(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    to_pil(array).save(buffer, format="PNG")
    return buffer.getvalue()


def encode_jpeg(array: np.ndarray, quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    to_pil(array).save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------
def correct_orientation(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Coarse 90-degree correction from the dominant text-line direction.

    Returns (image, degrees_rotated). Only acts when the evidence is strong;
    The scanner's geometric correction is the source of truth for orientation.
    """
    cv2 = _get_cv2()
    if cv2 is None:
        return image, 0.0

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    # Text lines produce far more horizontal than vertical gradient energy when
    # the page is upright.
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    energy_x = float(np.abs(sobel_x).mean())
    energy_y = float(np.abs(sobel_y).mean())

    if energy_y <= 0:
        return image, 0.0

    # Upright text: strong vertical gradients (crossing horizontal strokes).
    if energy_x > energy_y * 1.6:
        rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        return rotated, 90.0

    return image, 0.0


def _order_quad(points: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    ordered = np.zeros((4, 2), dtype=np.float32)
    summed = points.sum(axis=1)
    diff = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(summed)]  # top-left
    ordered[2] = points[np.argmax(summed)]  # bottom-right
    ordered[1] = points[np.argmin(diff)]  # top-right
    ordered[3] = points[np.argmax(diff)]  # bottom-left
    return ordered


def correct_perspective(
    image: np.ndarray, options: PreprocessOptions
) -> tuple[np.ndarray, bool]:
    """Find the page quadrilateral and warp it to a clean rectangle.

    Camera images often have both a strong page shadow and textured edges, so
    a single Canny contour is not reliable enough. Candidates are collected
    from several edge/threshold masks and the best convex quadrilateral is
    selected by area and rectangularity.
    """
    cv2 = _get_cv2()
    if cv2 is None:
        return image, False

    height, width = image.shape[:2]
    frame_area = float(height * width)

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    masks = []
    for low, high, kernel_size in ((25, 90, 3), (40, 140, 5), (70, 210, 7)):
        edges = cv2.Canny(blurred, low, high)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (kernel_size * 3, kernel_size * 3)
        )
        masks.append(cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel))
    # A light paper sheet against a darker surface is easier to recover from
    # a threshold mask when its boundary is softened by a shadow.
    for threshold in (140, 170, 200, 225):
        bright = cv2.threshold(blurred, threshold, 255, cv2.THRESH_BINARY)[1]
        masks.append(
            cv2.morphologyEx(
                bright,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)),
            )
        )

    # Paper is usually brighter and less saturated than wood, fabric, or a
    # desk. This mask recovers boundaries that Canny loses in soft shadows.
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    paper = cv2.inRange(hsv, np.array([0, 0, 125]), np.array([180, 115, 255]))
    paper = cv2.morphologyEx(
        paper,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)),
    )
    masks.append(paper)

    contours: list[np.ndarray] = []
    for mask in masks:
        found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    if not contours:
        return image, False

    best_quad: np.ndarray | None = None
    best_score = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:80]:
        area = cv2.contourArea(contour)
        if area < frame_area * options.min_quad_area_ratio:
            continue

        perimeter = cv2.arcLength(contour, True)
        for epsilon_ratio in (0.01, 0.02, 0.035, 0.05):
            approx = cv2.approxPolyDP(contour, epsilon_ratio * perimeter, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue

            points = approx.reshape(4, 2).astype(np.float32)
            quad = _order_quad(points)
            # A contour touching the frame is usually the camera border or a
            # second sheet entering the photo, not the page being scanned.
            edge_margin = max(2.0, min(width, height) * 0.005)
            if any(
                point[0] <= edge_margin
                or point[1] <= edge_margin
                or point[0] >= width - edge_margin
                or point[1] >= height - edge_margin
                for point in quad
            ):
                continue
            tl, tr, br, bl = quad
            target_w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
            target_h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
            if target_w < 32 or target_h < 32:
                continue

            angles = []
            for index, point in enumerate(quad):
                previous = quad[index - 1] - point
                following = quad[(index + 1) % 4] - point
                cosine = float(np.dot(previous, following)) / max(
                    np.linalg.norm(previous) * np.linalg.norm(following), 1.0
                )
                angles.append(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))
            if max(angles) > 145 or min(angles) < 35 or np.ptp(angles) > 75:
                continue

            quad_area = abs(float(cv2.contourArea(quad.reshape(-1, 1, 2))))
            rectangularity = quad_area / max(float(target_w * target_h), 1.0)
            score = (quad_area / frame_area) * rectangularity
            if score > best_score:
                best_score = score
                best_quad = quad
            break

    # Fallback for pages whose shadow breaks their outline into a blob. The
    # largest interior paper component still gives a useful four-corner crop;
    # unlike a frame contour it cannot include the whole camera background.
    if best_quad is None:
        paper_contours: list[np.ndarray] = []
        paper_mask = masks[-1]
        found, _ = cv2.findContours(
            paper_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for contour in sorted(found, key=cv2.contourArea, reverse=True)[:10]:
            area = cv2.contourArea(contour)
            if area < frame_area * options.min_quad_area_ratio:
                continue
            x, y, contour_width, contour_height = cv2.boundingRect(contour)
            if x <= 1 or y <= 1 or x + contour_width >= width - 1 or y + contour_height >= height - 1:
                continue
            rectangle = cv2.minAreaRect(contour)
            candidate = cv2.boxPoints(rectangle).astype(np.float32)
            candidate = _order_quad(candidate)
            candidate_area = abs(float(cv2.contourArea(candidate.reshape(-1, 1, 2))))
            if candidate_area / frame_area >= options.min_quad_area_ratio:
                paper_contours.append(candidate)
                break
        if paper_contours:
            best_quad = paper_contours[0]

    if best_quad is None:
        return image, False

    tl, tr, br, bl = best_quad
    target_w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    target_h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    destination = np.array(
        [[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(best_quad, destination)
    warped = cv2.warpPerspective(
        image, matrix, (target_w, target_h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return warped, True


def estimate_skew(image: np.ndarray, options: PreprocessOptions) -> float:
    """Estimate residual skew in degrees via the minimum-area box of text pixels."""
    cv2 = _get_cv2()
    if cv2 is None:
        return 0.0

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    # Bridge characters into text lines so the box follows the baseline.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    joined = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    coords = cv2.findNonZero(joined)
    if coords is None or len(coords) < 50:
        return 0.0

    angle = cv2.minAreaRect(coords)[-1]
    # OpenCV reports the angle in (0, 90]; map it to (-45, 45].
    if angle > 45:
        angle -= 90

    if abs(angle) > options.max_deskew_degrees:
        return 0.0
    return float(angle)


def rotate(image: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate about the centre, expanding the canvas so nothing is clipped."""
    cv2 = _get_cv2()
    if cv2 is None or abs(degrees) < 1e-3:
        return image

    height, width = image.shape[:2]
    centre = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, degrees, 1.0)

    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_w = int(height * sin + width * cos)
    new_h = int(height * cos + width * sin)
    matrix[0, 2] += new_w / 2.0 - centre[0]
    matrix[1, 2] += new_h / 2.0 - centre[1]

    return cv2.warpAffine(
        image,
        matrix,
        (new_w, new_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def reduce_noise(image: np.ndarray) -> np.ndarray:
    cv2 = _get_cv2()
    if cv2 is None:
        return image
    # Mild, edge-preserving. Strong denoising erodes thin glyph strokes.
    return cv2.fastNlMeansDenoisingColored(image, None, 3, 3, 7, 21)


def correct_illumination(image: np.ndarray) -> np.ndarray:
    """Flatten uneven lighting/shadow gradients and lift local contrast."""
    cv2 = _get_cv2()
    if cv2 is None:
        return image

    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
    lightness, a_channel, b_channel = cv2.split(lab)

    # Divide out the low-frequency illumination field (shadows, vignetting).
    background = cv2.GaussianBlur(lightness, (0, 0), sigmaX=31, sigmaY=31)
    background = np.where(background == 0, 1, background).astype(np.float32)
    flattened = np.clip(
        lightness.astype(np.float32) / background * float(np.mean(background)), 0, 255
    ).astype(np.uint8)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    equalised = clahe.apply(flattened)

    merged = cv2.merge((equalised, a_channel, b_channel))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def preprocess(
    image: np.ndarray, options: PreprocessOptions | None = None
) -> PreprocessResult:
    """Run the enabled preprocessing steps in the order that makes sense.

    Order matters: geometry first (orientation -> perspective -> deskew), then
    photometry (denoise -> illumination), so the photometric steps operate on
    the final, rectified pixel grid.
    """
    options = options or PreprocessOptions()
    result = PreprocessResult(image=image)

    if _get_cv2() is None:
        result.warnings.append(
            "OpenCV is not installed; preprocessing was skipped and the image "
            "was passed to the OCR pipeline as-is."
        )
        return result

    current = image
    total_rotation = 0.0

    if options.orientation:
        try:
            current, degrees = correct_orientation(current)
            if degrees:
                total_rotation += degrees
                result.applied.append("orientation")
        except Exception as exc:  # pragma: no cover
            result.warnings.append(f"orientation correction failed: {exc}")

    if options.perspective:
        try:
            current, changed = correct_perspective(current, options)
            if changed:
                result.applied.append("perspective")
        except Exception as exc:  # pragma: no cover
            result.warnings.append(f"perspective correction failed: {exc}")

    if options.deskew:
        try:
            angle = estimate_skew(current, options)
            if abs(angle) >= options.min_deskew_degrees:
                current = rotate(current, angle)
                total_rotation += angle
                result.applied.append(f"deskew({angle:.2f}deg)")
        except Exception as exc:  # pragma: no cover
            result.warnings.append(f"deskew failed: {exc}")

    if options.denoise:
        try:
            current = reduce_noise(current)
            result.applied.append("denoise")
        except Exception as exc:  # pragma: no cover
            result.warnings.append(f"denoise failed: {exc}")

    if options.illumination:
        try:
            current = correct_illumination(current)
            result.applied.append("illumination")
        except Exception as exc:  # pragma: no cover
            result.warnings.append(f"illumination correction failed: {exc}")

    result.image = current
    result.rotation_applied = total_rotation
    log.debug("Preprocessing applied: %s", ", ".join(result.applied) or "none")
    return result
