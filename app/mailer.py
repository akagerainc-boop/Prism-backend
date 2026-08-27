"""Gmail SMTP delivery for OTP emails.

Credentials come from:
    SMTP_USER
    SMTP_APP_PASSWORD

SMTP_APP_PASSWORD must be a Gmail App Password, not the normal
Gmail account password.

Supported Gmail configurations:

    Port 465:
        SMTP_USE_SSL=true
        Uses SMTP_SSL directly.

    Port 587:
        SMTP_USE_SSL=false
        Uses STARTTLS.
"""

from __future__ import annotations

import smtplib
import ssl
import json
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)


class MailError(Exception):
    """Raised when the OTP email could not be handed to Gmail."""


def _build_message(
    to_email: str,
    otp: str,
    ttl_minutes: int,
) -> EmailMessage:
    """Build the OTP email."""

    msg = EmailMessage()

    msg["Subject"] = f"{otp} is your Prism verification code"

    msg["From"] = formataddr(
        (
            settings.smtp_from_name,
            settings.smtp_user,
        )
    )

    msg["To"] = to_email

    msg["Message-ID"] = make_msgid(domain="prism.app")

    # Helps Gmail/Apple surface the code in notifications.
    msg["X-Entity-Ref-ID"] = otp

    spaced = " ".join(otp)

    # Plain-text version
    msg.set_content(
        f"Your Prism verification code is {otp}\n\n"
        f"It expires in {ttl_minutes} minutes and can only be used once.\n\n"
        "If you didn't request this code, you can safely ignore this email -- "
        "someone may have typed your address by mistake.\n\n"
        "-- Prism Scanner\n"
    )

    # HTML version
    msg.add_alternative(
        f"""<!doctype html>
<html>
  <body style="
      margin:0;
      padding:32px;
      background:#f5f6f8;
      font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  ">
    <div style="
        max-width:440px;
        margin:0 auto;
        background:#ffffff;
        border-radius:14px;
        padding:32px;
        border:1px solid #e5e7eb;
    ">

      <h1 style="
          margin:0 0 8px;
          font-size:19px;
          color:#111827;
      ">
        Verify your email
      </h1>

      <p style="
          margin:0 0 24px;
          font-size:14px;
          line-height:1.6;
          color:#4b5563;
      ">
        Enter this code in Prism Scanner to finish signing in.
      </p>

      <div style="
          font-size:30px;
          font-weight:700;
          letter-spacing:9px;
          color:#111827;
          background:#f3f4f6;
          border-radius:10px;
          padding:18px;
          text-align:center;
      ">
        {spaced}
      </div>

      <p style="
          margin:24px 0 0;
          font-size:13px;
          line-height:1.6;
          color:#6b7280;
      ">
        This code expires in {ttl_minutes} minutes and can only be used once.
        If you didn't request it, you can safely ignore this email.
      </p>

    </div>
  </body>
</html>""",
        subtype="html",
    )

    return msg


def _send_with_ssl(msg: EmailMessage) -> None:
    """Send using Gmail implicit SSL, normally port 465."""

    log.info(
        "SMTP DEBUG: using SMTP_SSL host=%r port=%r user=%r",
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_user,
    )

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        host=settings.smtp_host,
        port=settings.smtp_port,
        context=context,
        timeout=settings.smtp_timeout_seconds,
    ) as server:

        log.info("SMTP DEBUG: SSL connection established")

        server.login(
            settings.smtp_user,
            settings.smtp_app_password,
        )

        log.info("SMTP DEBUG: Gmail authentication successful")

        server.send_message(msg)
        log.info("SMTP DEBUG: SMTP message accepted")

        log.info("SMTP DEBUG: SMTP message accepted")


def _send_with_starttls(msg: EmailMessage) -> None:
    """Send using Gmail STARTTLS, normally port 587."""

    log.info(
        "SMTP DEBUG: using STARTTLS host=%r port=%r user=%r",
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_user,
    )

    context = ssl.create_default_context()

    with smtplib.SMTP(
        host=settings.smtp_host,
        port=settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as server:

        server.ehlo()

        log.info("SMTP DEBUG: plain SMTP connection established")

        server.starttls(context=context)

        server.ehlo()

        log.info("SMTP DEBUG: STARTTLS negotiation successful")

        server.login(
            settings.smtp_user,
            settings.smtp_app_password,
        )

        log.info("SMTP DEBUG: Gmail authentication successful")

        server.send_message(msg)


def _send_with_resend(msg: EmailMessage) -> None:
    """Send through Resend's HTTPS API, which works on Render."""
    body = msg.get_body(preferencelist=("plain",))
    html_body = msg.get_body(preferencelist=("html",))
    payload = {
        "from": settings.email_from,
        "to": [msg["To"]],
        "subject": msg["Subject"],
        "text": body.get_content() if body else "",
        "html": html_body.get_content() if html_body else "",
    }
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PrismScanner-Backend/1.0",
        },
        method="POST",
    )
    log.info("Email DEBUG: sending through Resend HTTPS API")
    try:
        with urllib.request.urlopen(
            request, timeout=settings.smtp_timeout_seconds
        ) as response:
            if response.status < 200 or response.status >= 300:
                raise MailError("Resend rejected the email.")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        log.error("Resend rejected the email: HTTP %s: %s", exc.code, detail)
        raise MailError("Resend rejected the email.") from exc
    except urllib.error.URLError as exc:
        log.error("Network error while calling Resend: %s", exc.reason)
        raise MailError("Could not connect to Resend.") from exc

def send_otp_email(
    to_email: str,
    otp: str,
) -> None:
    """Send an OTP email.

    Raises:
        MailError: If the email cannot be sent.
    """

    ttl_minutes = max(
        1,
        settings.otp_ttl_seconds // 60,
    )

    # Development mode.
    if settings.smtp_dev_mode:
        log.warning(
            "SMTP_DEV_MODE is ON -- not sending email. "
            "OTP for %s is %s",
            to_email,
            otp,
        )
        return

    if settings.email_provider.lower() == "resend":
        if not settings.resend_api_key or not settings.email_from:
            raise MailError(
                "EMAIL_FROM / RESEND_API_KEY are not configured."
            )
    # Validate SMTP credentials.
    elif (
        not settings.smtp_user
        or not settings.smtp_app_password
    ):
        raise MailError(
            "SMTP_USER / SMTP_APP_PASSWORD are not configured. "
            "Set them in backend/.env using a Gmail App Password, "
            "or set SMTP_DEV_MODE=true for local development."
        )

    # Diagnostic information.
    #
    # IMPORTANT:
    # We intentionally DO NOT log SMTP_APP_PASSWORD.
    if settings.email_provider.lower() == "resend":
        log.info("Email DEBUG: provider=resend from=%r", settings.email_from)
    else:
        log.info(
            "SMTP DEBUG: configuration host=%r port=%r ssl=%r user=%r",
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_use_ssl,
            settings.smtp_user,
        )

    # Do not log the actual password.
    if settings.email_provider.lower() != "resend":
        log.info(
            "SMTP DEBUG: App Password configured=%s length=%d",
            bool(settings.smtp_app_password),
            len(settings.smtp_app_password),
        )

    msg = _build_message(
        to_email=to_email,
        otp=otp,
        ttl_minutes=ttl_minutes,
    )

    try:

        if settings.email_provider.lower() == "resend":
            _send_with_resend(msg)
        # Gmail port 465: implicit SSL/TLS.
        elif (
            settings.smtp_use_ssl
            or settings.smtp_port == 465
        ):
            log.info(
                "SMTP DEBUG: selecting SMTP_SSL because "
                "SMTP_USE_SSL=%r and SMTP_PORT=%r",
                settings.smtp_use_ssl,
                settings.smtp_port,
            )

            _send_with_ssl(msg)

        # Gmail port 587:
        # SMTP + STARTTLS.
        else:
            log.info(
                "SMTP DEBUG: selecting STARTTLS because "
                "SMTP_USE_SSL=%r and SMTP_PORT=%r",
                settings.smtp_use_ssl,
                settings.smtp_port,
            )

            _send_with_starttls(msg)

    except smtplib.SMTPAuthenticationError as exc:

        log.error(
            "Gmail rejected the SMTP credentials: %s",
            exc,
        )

        raise MailError(
            "Gmail rejected the SMTP credentials. "
            "Confirm SMTP_APP_PASSWORD is a valid Gmail "
            "App Password and that 2-Step Verification is enabled."
        ) from exc

    except ssl.SSLError as exc:

        log.error(
            "SMTP SSL/TLS error while sending OTP to %s: %s",
            to_email,
            exc,
        )

        raise MailError(
            "Could not establish a secure connection to Gmail SMTP."
        ) from exc

    except smtplib.SMTPException as exc:

        log.error(
            "SMTP error while sending OTP to %s: %s",
            to_email,
            exc,
        )

        raise MailError(
            "Gmail SMTP rejected the email."
        ) from exc

    except OSError as exc:

        log.error(
            "Network error while sending OTP to %s: %s",
            to_email,
            exc,
        )

        raise MailError(
            "Could not connect to Gmail SMTP."
        ) from exc

    except Exception as exc:

        # Last-resort diagnostic.
        # We deliberately don't expose credentials.
        log.exception(
            "Unexpected error while sending OTP to %s: %s",
            to_email,
            exc,
        )

        raise MailError(
            "Could not send the verification email."
        ) from exc

    log.info(
        "OTP email dispatched successfully to %s",
        to_email,
    )

