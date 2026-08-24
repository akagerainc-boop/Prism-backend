"""On-disk storage helpers for Prism Cloud documents and OCR job artifacts.

Layout under ``PRISM_STORAGE_ROOT``::

    <root>/documents/<user_id>/<document_id>.pdf
    <root>/jobs/<job_id>/book.pdf
    <root>/jobs/<job_id>/document.json
    <root>/tmp/...            (scratch for document scanning)
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)


def storage_root() -> Path:
    root = settings.storage_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def documents_dir(user_id: int) -> Path:
    path = storage_root() / "documents" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def document_path(user_id: int, document_id: str) -> Path:
    return documents_dir(user_id) / f"{document_id}.pdf"


def job_dir(job_id: str) -> Path:
    path = storage_root() / "jobs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def tmp_dir() -> Path:
    path = storage_root() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ocr_output_dir() -> Path:
    """Return the persistent directory containing inspectable OCR results."""
    path = storage_root() / "OCR output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def student_proof_dir() -> Path:
    path = storage_root() / "student_applications"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_unlink(path: Path | str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover
        log.warning("Could not delete %s: %s", path, exc)


def safe_rmtree(path: Path | str | None) -> None:
    if not path:
        return
    shutil.rmtree(Path(path), ignore_errors=True)


def is_within(child: Path, parent: Path) -> bool:
    """Guard against path traversal via crafted ids/filenames."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False
