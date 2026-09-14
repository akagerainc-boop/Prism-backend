"""Admin-editable key/value app settings (the `app_config` table).

Currently just the force-update fields. Key/value rather than named columns
so a new setting never needs a migration -- see models.AppConfig / the seed
rows at the bottom of schema_postgres.sql.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import AppConfig

_MIN_SUPPORTED_VERSION = "min_supported_version"
_LATEST_VERSION = "latest_version"
_PLAY_STORE_URL = "play_store_url"


def _get(db: Session, key: str, default: str) -> str:
    row = db.get(AppConfig, key)
    return row.value if row is not None else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(AppConfig, key)
    if row is None:
        db.add(AppConfig(key=key, value=value))
    else:
        row.value = value
        db.add(row)


def get_force_update_config(db: Session) -> tuple[str, str, str]:
    """Returns (min_supported_version, latest_version, play_store_url)."""
    return (
        _get(db, _MIN_SUPPORTED_VERSION, settings.min_supported_version),
        _get(db, _LATEST_VERSION, settings.min_supported_version),
        _get(db, _PLAY_STORE_URL, settings.play_store_url),
    )


def set_force_update_config(
    db: Session, *, min_supported_version: str, latest_version: str, play_store_url: str
) -> None:
    _set(db, _MIN_SUPPORTED_VERSION, min_supported_version)
    _set(db, _LATEST_VERSION, latest_version)
    _set(db, _PLAY_STORE_URL, play_store_url)


def parse_version(version: str) -> tuple[int, ...]:
    """"1.0.10" -> (1, 0, 10). Unparseable segments become 0, never raises --
    a malformed version string should fail open (no forced update), not
    crash the version-check endpoint every client calls on launch.
    """
    parts: list[int] = []
    for segment in (version or "").split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_below(current: str, minimum: str) -> bool:
    return parse_version(current) < parse_version(minimum)
