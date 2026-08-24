"""OpenCV document scanning adapter.

This follows the public OpenCV-Document-Scanner project's document scanning
stages: conditional corner detection, four-point unwarping, sharpening, and
adaptive thresholding to produce a clean, white scan. It has
no text-recognition model; this project returns the scanned image and keeps the
existing API response shapes for clients that still call the text endpoints.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .imaging import PreprocessOptions, encode_png, load_rgb_array, preprocess


class ScannerUnavailable(RuntimeError):
    """OpenCV is unavailable."""


class ScannerFailed(RuntimeError):
    """The document scanner failed."""


class ScannerBusy(RuntimeError):
    """The server is already scanning another document."""


_lock = threading.BoundedSemaphore(value=1)


def scan_array(image: Any) -> Any:
    """Conditionally unwrap/crop the page, then create the final scan."""
    if not _lock.acquire(blocking=False):
        raise ScannerBusy("The scanner is busy processing another document.")
    try:
        try:
            import cv2
        except Exception as exc:  # pragma: no cover
            raise ScannerUnavailable("opencv-python-headless is not installed.") from exc

        # Geometry runs before scanner cleanup. If no trustworthy page corners
        # are found, preprocess() returns the original frame unchanged.
        prepared = preprocess(
            image,
            PreprocessOptions(
                orientation=False,
                perspective=True,
                deskew=False,
                denoise=True,
                illumination=True,
            ),
        )
        page = prepared.image
        gray = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
        sharpened = cv2.addWeighted(
            gray, 1.5, cv2.GaussianBlur(gray, (0, 0), 3), -0.5, 0
        )
        scanned = cv2.adaptiveThreshold(
            sharpened,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            21,
            15,
        )
        return cv2.cvtColor(scanned, cv2.COLOR_GRAY2RGB)
    finally:
        _lock.release()


def prepare_scan(path: str | Path) -> Path:
    """Scan an image in place and return a temporary output path."""
    source = Path(path)
    try:
        image = load_rgb_array(source.read_bytes())
        scanned = scan_array(image)
        output = source.with_name(f"{source.stem}_scanned.png")
        output.write_bytes(encode_png(scanned))
        return output
    except (ScannerBusy, ScannerUnavailable):
        raise
    except Exception as exc:
        raise ScannerFailed(f"Document scanning failed: {exc}") from exc


def run_structure(path: str | Path) -> list[dict[str, Any]]:
    """Return the scanner result shape used by legacy callers.

    OpenCV-Document-Scanner produces images, not OCR/layout records, so this
    result is intentionally empty and the image remains the source of truth.
    """
    return []


def run_structure_with_vl(path: str | Path) -> tuple[list[Any], None]:
    return run_structure(path), None


def extract_plain_text(results: list[Any]) -> str:
    return ""


def pipeline_status() -> dict[str, Any]:
    try:
        import cv2

        version = cv2.__version__
        installed = True
    except Exception:
        version = None
        installed = False
    return {
        "engine": "OpenCV-Document-Scanner",
        "opencvInstalled": installed,
        "opencvVersion": version,
        "textRecognition": False,
        "documentScanning": installed,
    }