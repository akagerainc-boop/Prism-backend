"""Wallet's second MFA factor: an emailed one-time code.

Prism's Wallet ("Cards" section) requires two factors to open:
  1. device PIN or biometric (handled entirely on-device, see
     ``lib/services/security_service.dart`` -- this backend is never
     involved in that factor)
  2. a 5-digit code emailed to the signed-in address, verified here

This reuses the exact same OTP mechanism as login (``routers/auth.py`` /
``otp_flow.py`` -- same ``otp_codes`` table, same hashing, same per-email
rate limiting) because it's genuinely the same primitive: prove control of
an email address via a short-lived emailed code. What's different on
purpose:

  * a *separate* attempt cap (3, not ``settings.otp_max_attempts``) --
    Wallet's own requirement, stricter than login's,
  * success here does **not** log the user in, create an account, or issue
    a session token -- it just proves the second factor and returns a
    plain confirmation. The caller must already be signed in.

Contract (from ``lib/services/card_otp_service.dart``):

  POST /auth/email/card-otp/request
      -> {"email": "..."}
      <- 200 {"verificationToken": "...", "message": "..."}

  POST /auth/email/card-otp/verify
      -> {"email": "...", "otp": "...", "verificationToken": "..."}
      <- 200 {"message": "Verified."}
      <- 400 {"message": "..."}  -- wrong/expired code, or 3rd wrong attempt
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..logging_config import get_logger
from ..mailer import MailError, send_otp_email
from ..models import OtpCode, OtpRequestLog
from ..otp_flow import client_ip, enforce_rate_limit, purge_expired, utcnow
from ..schemas import MessageResponse, RequestOtpBody, RequestOtpResponse, VerifyOtpBody
from ..security import (
    generate_otp,
    generate_verification_token,
    hash_otp,
    is_valid_email,
    new_salt,
    normalize_email,
    verify_otp_hash,
)

log = get_logger(__name__)

router = APIRouter(prefix="/auth/email/card-otp", tags=["wallet-mfa"])

CARD_OTP_MAX_ATTEMPTS = 3

_GENERIC_FAILURE = "That code isn't valid or has expired. Request a new one."


@router.post(
    "/request",
    response_model=RequestOtpResponse,
    responses={400: {"description": "Invalid email"}, 429: {"description": "Rate limited"}},
)
def request_card_otp(
    body: RequestOtpBody,
    request: Request,
    db: Session = Depends(get_db),
) -> RequestOtpResponse:
    email = normalize_email(body.email)
    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address."
        )

    enforce_rate_limit(db, email)
    purge_expired(db, email)

    otp = generate_otp()
    salt = new_salt()
    token = generate_verification_token()
    expires_at = utcnow() + dt.timedelta(seconds=settings.otp_ttl_seconds)

    challenge = OtpCode(
        email=email,
        verification_token=token,
        otp_hash=hash_otp(otp, salt),
        otp_salt=salt,
        expires_at=expires_at,
        attempts=0,
    )
    db.add(challenge)

    log_row = OtpRequestLog(email=email, requested_at=utcnow(), client_ip=client_ip(request))
    db.add(log_row)
    db.flush()

    try:
        send_otp_email(email, otp)
    except MailError as exc:
        db.rollback()
        log.error("Card-unlock OTP delivery failed for %s: %s", email, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't send the verification email. Please try again.",
        ) from exc

    log_row.delivered = True
    db.add(log_row)

    log.info("Issued card-unlock OTP for %s (expires %s)", email, expires_at)
    return RequestOtpResponse(
        verificationToken=token,
        message=f"We sent a {settings.otp_length}-digit code to {email}.",
    )


@router.post(
    "/verify",
    response_model=MessageResponse,
    responses={400: {"description": "Invalid or expired code"}},
)
def verify_card_otp(body: VerifyOtpBody, db: Session = Depends(get_db)) -> MessageResponse:
    email = normalize_email(body.email)
    otp = (body.otp or "").strip()
    token = (body.verificationToken or "").strip()

    def fail() -> HTTPException:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GENERIC_FAILURE)

    if not email or not otp or not token:
        raise fail()

    challenge = db.scalar(select(OtpCode).where(OtpCode.verification_token == token))

    if challenge is None:
        verify_otp_hash(otp, new_salt(), "0" * 64)
        raise fail()
    if challenge.email != email:
        verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash)
        raise fail()
    if challenge.consumed_at is not None:
        verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash)
        raise fail()
    if challenge.expires_at < utcnow():
        verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash)
        db.delete(challenge)
        raise fail()
    if challenge.attempts >= CARD_OTP_MAX_ATTEMPTS:
        db.delete(challenge)
        raise fail()

    challenge.attempts += 1
    db.add(challenge)

    if not verify_otp_hash(otp, challenge.otp_salt, challenge.otp_hash):
        db.flush()
        remaining = max(0, CARD_OTP_MAX_ATTEMPTS - challenge.attempts)
        if remaining == 0:
            db.delete(challenge)
            raise fail()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} left.",
        )

    challenge.consumed_at = utcnow()
    db.add(challenge)
    db.flush()

    log.info("Card-unlock OTP verified for %s", email)
    return MessageResponse(message="Verified.")
