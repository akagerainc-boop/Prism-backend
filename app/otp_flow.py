"""Shared plumbing behind every email-OTP challenge in this backend.

Originally lived only in ``routers/auth.py`` (login); extracted so
``routers/card_mfa.py`` (Wallet's second MFA factor) can reuse the exact
same rate-limiting and cleanup behaviour instead of a parallel copy that
could drift out of sync.
"""

from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import OtpCode, OtpRequestLog


def utcnow() -> dt.datetime:
    """Naive UTC -- MySQL DATETIME columns are timezone-less."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def enforce_rate_limit(db: Session, email: str) -> None:
    """Max 1 request per ``otp_min_interval_seconds``, max ``otp_max_per_hour``.

    Shared across every OTP purpose (login, card-unlock) -- deliberately:
    it's a per-email-address limit against mail-bombing that address, not
    a per-feature one.
    """
    now = utcnow()

    last_at = db.scalar(
        select(func.max(OtpRequestLog.requested_at)).where(OtpRequestLog.email == email)
    )
    if last_at is not None:
        elapsed = (now - last_at).total_seconds()
        if elapsed < settings.otp_min_interval_seconds:
            wait = int(settings.otp_min_interval_seconds - elapsed) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {wait} seconds before requesting another code.",
            )

    hour_ago = now - dt.timedelta(hours=1)
    recent = db.scalar(
        select(func.count(OtpRequestLog.id)).where(
            OtpRequestLog.email == email,
            OtpRequestLog.requested_at >= hour_ago,
        )
    )
    if (recent or 0) >= settings.otp_max_per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many codes requested for this address. Try again in an hour.",
        )


def purge_expired(db: Session, email: str) -> None:
    """Drop this address's stale challenges so only the newest one is live."""
    db.execute(
        delete(OtpCode).where(
            OtpCode.email == email,
            (OtpCode.expires_at < utcnow()) | (OtpCode.consumed_at.is_not(None)),
        )
    )
