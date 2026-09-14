"""OTP hashing, verification tokens and JWT session tokens."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import secrets

import jwt  # PyJWT

from .config import settings

# PBKDF2 parameters for OTP hashing. A 5-digit OTP has only 100k possible
# values, so the work factor is what makes an offline attack on a leaked
# database row unattractive; the short TTL does the rest.
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_DIGEST = "sha256"

# Deliberately permissive but structurally strict. Full RFC 5322 validation is
# pointless here -- deliverability is proven by the OTP round-trip itself.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


class TokenError(Exception):
    """Raised when a session token is absent, malformed or expired."""


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    if not email or len(email) > 320:
        return False
    return bool(_EMAIL_RE.match(email))


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------
def generate_otp(length: int | None = None) -> str:
    """Cryptographically random numeric OTP, zero-padded to ``length``."""
    n = length or settings.otp_length
    upper = 10**n
    return str(secrets.randbelow(upper)).zfill(n)


def generate_verification_token() -> str:
    """Opaque token tying a verify-otp call to one specific request-otp call."""
    return secrets.token_urlsafe(32)


def hash_otp(otp: str, salt: str) -> str:
    derived = hashlib.pbkdf2_hmac(
        _PBKDF2_DIGEST,
        otp.encode("utf-8"),
        bytes.fromhex(salt),
        _PBKDF2_ITERATIONS,
    )
    return derived.hex()


def new_salt() -> str:
    return secrets.token_bytes(16).hex()


def verify_otp_hash(otp: str, salt: str, expected_hash: str) -> bool:
    """Constant-time comparison of a candidate OTP against the stored hash."""
    try:
        candidate = hash_otp(otp, salt)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected_hash)


# ---------------------------------------------------------------------------
# JWT session tokens
# ---------------------------------------------------------------------------
def _require_secret() -> str:
    secret = settings.jwt_secret
    if not secret or len(secret) < 16:
        raise RuntimeError(
            "JWT_SECRET is missing or too short. Set a long random value in "
            "backend/.env -- e.g. `python -c \"import secrets;"
            'print(secrets.token_urlsafe(48))"`.'
        )
    return secret


def create_session_token(*, user_id: int, email: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + dt.timedelta(minutes=settings.jwt_expires_minutes)).timestamp()
        ),
        "iss": "prism-backend",
        "typ": "session",
    }
    return jwt.encode(payload, _require_secret(), algorithm=settings.jwt_algorithm)


def decode_session_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            _require_secret(),
            algorithms=[settings.jwt_algorithm],
            issuer="prism-backend",
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("typ") != "session":
        raise TokenError("Not a session token.")
    return payload


# ---------------------------------------------------------------------------
# Admin passwords + session tokens
#
# Admins are not app users (see models.AdminUser) -- a separate password
# hash and a separate `typ` claim keep an app-user session token from ever
# being usable against an admin-only endpoint, and vice versa, even though
# both are signed with the same JWT_SECRET.
# ---------------------------------------------------------------------------
_ADMIN_PBKDF2_ITERATIONS = 310_000  # OWASP 2023 minimum for PBKDF2-SHA256


def hash_admin_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ADMIN_PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_ADMIN_PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt_hex, hash_hex = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_str),
        )
        return hmac.compare_digest(derived.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def create_admin_token(*, admin_id: int, email: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(admin_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(hours=12)).timestamp()),
        "iss": "prism-backend",
        "typ": "admin",
    }
    return jwt.encode(payload, _require_secret(), algorithm=settings.jwt_algorithm)


def decode_admin_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            _require_secret(),
            algorithms=[settings.jwt_algorithm],
            issuer="prism-backend",
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("typ") != "admin":
        raise TokenError("Not an admin token.")
    return payload
