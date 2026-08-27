"""API routers, one module per feature area."""

from __future__ import annotations

from . import (
    ai_history,
    auth,
    card_mfa,
    cloud,
    ocr,
    passport_photo,
    perfect,
    structure,
    wallet,
)

__all__ = [
    "ai_history",
    "auth",
    "card_mfa",
    "cloud",
    "ocr",
    "passport_photo",
    "perfect",
    "structure",
    "wallet",
]
