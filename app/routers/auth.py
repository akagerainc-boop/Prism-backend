"""Email + OTP authentication.

Contract (from ``lib/UI/auth/auth_service.dart``):

  POST /auth/email/request-otp
      -> {"email": "..."}
      <- 200 {"verificationToken": "...", "message": "..."}
      <- 4xx {"message": "..."}

  POST /auth/email/verify-otp
      -> {"email": "...", "otp": "...", "verificationToken": "..."}
      <- 200 {"message": "...", "sessionToken": "..."}
      <- 4xx {"message": "..."}

First successful verification for an address creates the user (signup and login
are the same flow).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..logging_config import get_logger
from ..mailer import MailError, send_otp_email
from ..models import Account, OtpCode, OtpRequestLog, StorageUsage, User
from ..plans import DEFAULT_PLAN, storage_limit_for
from ..schemas import (
    RequestOtpBody,
    RequestOtpResponse,
    VerifyOtpBody,
    VerifyOtpResponse,
)
from ..security import (
    create_session_token,
    generate_otp,
    generate_verification_token,
    hash_otp,
    is_valid_email,
    new_salt,
    normalize_email,
    verify_otp_hash,
)

log = get_logger(__name__)

router = APIRouter(prefix="/auth/email", tags=["auth"])

# One generic failure message for every verification failure. Wrong code,
# expired code, unknown token and unknown email are indistinguishable to the
# caller, so this endpoint cannot be used to enumerate registered addresses.
_GENERIC_VERIFY_FAILURE = "That code isn't valid or has expired. Request a new one."


def _utcnow() -> dt.datetime:
    """Naive UTC -- MySQL DATETIME columns are timezone-less."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def _enforce_rate_limit(db: Session, email: str) -> None:
    """Max 1 request per ``otp_min_interval_seconds``, max ``otp_max_per_hour``."""
    now = _utcnow()

    last_at = db.scalar(
        select(func.max(OtpRequestLog.requested_at)).where(
            OtpRequestLog.email == email
        )
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


def _purge_expired(db: Session, email: str) -> None:
    """Drop this address's stale challenges so only the newest one is live."""
    db.execute(
        delete(OtpCode).where(
            OtpCode.email == email,
            (OtpCode.expires_at < _utcnow()) | (OtpCode.consumed_at.is_not(None)),
        )
    )


@router.post(
    "/request-otp",
    response_model=RequestOtpResponse,
    responses={400: {"description": "Invalid email"}, 429: {"description": "Rate limited"}},
)
def request_otp(
    body: RequestOtpBody,
    request: Request,
    db: Session = Depends(get_db),
) -> RequestOtpResponse:
    email = normalize_email(body.email)

    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid email address.",
        )

    _enforce_rate_limit(db, email)
    _purge_expired(db, email)

    otp = generate_otp()
    salt = new_salt()
    token = generate_verification_token()
    expires_at = _utcnow() + dt.timedelta(seconds=settings.otp_ttl_seconds)

    challenge = OtpCode(
        email=email,
        verification_token=token,
        otp_hash=hash_otp(otp, salt),
        otp_salt=salt,
        expires_at=expires_at,
        attempts=0,
    )
    db.add(challenge)

    log_row = OtpRequestLog(
        email=email, requested_at=_utcnow(), client_ip=_client_ip(request)
    )
    db.add(log_row)
    # Persist the challenge + rate-limit row before the (slow, fallible) send,
    # so a burst of requests can't slip past the limiter while SMTP blocks.
    db.flush()

    try:
        send_otp_email(email, otp)
    except MailError as exc:
        db.rollback()
        log.error("OTP delivery failed for %s: %s", email, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send the verification email. Please try again.",
        ) from exc

    log_row.delivered = True
    db.add(log_row)

    log.info("Issued OTP challenge for %s (expires %s)", email, expires_at)
    return RequestOtpResponse(
        verificationToken=token,
        message=f"We sent a {settings.otp_length}-digit code to {email}.",
    )


@router.post(
    "/verify-otp",
    response_model=VerifyOtpResponse,
    responses={400: {"description": "Invalid or expired code"}},
)
def verify_otp(
    body: VerifyOtpBody,
    db: Session = Depends(get_db),
) -> VerifyOtpResponse:
    email = normalize_email(body.email)
    otp = (body.otp or "").strip()
    token = (body.verificationToken or "").strip()

    def fail() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_GENERIC_VERIFY_FAILURE
        )

    if not email or not otp or not token:
        raise fail()

    challenge = db.scalar(select(OtpCode).where(OtpCode.verification_token == token))

    # Every failure below returns the identical message and status, and does the
    # same amount of hashing work, so neither the response nor its timing tells
    # a caller whether the address exists.
    if challenge is None:
        # Burn equivalent work so a bogus token isn't measurably faster.
        verify_otp_hash(otp, new_salt(), "0" * 64)
        raise fail()

    if challenge.email != email:
        verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash)
        raise fail()

    if challenge.consumed_at is not None:
        verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash)
        raise fail()

    if challenge.expires_at < _utcnow():
        verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash)
        db.delete(challenge)
        raise fail()

    if challenge.attempts >= settings.otp_max_attempts:
        db.delete(challenge)
        raise fail()

    challenge.attempts += 1
    db.add(challenge)

    if not verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash):
        db.flush()
        raise fail()

    # ---- Success: consume the challenge, then signup-or-login. ----
    challenge.consumed_at = _utcnow()
    db.add(challenge)

    user = db.scalar(select(User).where(User.email == email))
    created = False
    if user is None:
        user = User(email=email, created_at=_utcnow(), is_active=True)
        db.add(user)
        db.flush()  # assign user.id
        created = True

    if db.get(Account, user.id) is None:
        db.add(
            Account(
                user_id=user.id,
                plan=DEFAULT_PLAN,
                storage_limit_bytes=storage_limit_for(DEFAULT_PLAN),
            )
        )
    if db.get(StorageUsage, user.id) is None:
        db.add(StorageUsage(user_id=user.id, used_bytes=0, document_count=0))

    user.last_login_at = _utcnow()
    db.add(user)
    db.flush()

    session_token = create_session_token(user_id=user.id, email=email)

    log.info("OTP verified for %s (%s)", email, "new account" if created else "returning")
    return VerifyOtpResponse(
        message="Signed in successfully." if not created else "Account created.",
        sessionToken=session_token,
    )
