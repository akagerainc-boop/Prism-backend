"""Firebase Cloud Messaging -- sending pushes via the Admin SDK.

Credentials come from ``FIREBASE_SERVICE_ACCOUNT_JSON`` (see config.py),
accepted as either a filesystem path (local dev: the actual .json file) or
the JSON itself pasted inline (Railway env vars can't hold a file). Lazily
initialised, same pattern as ``passport._get_session`` -- so the rest of the
API still boots when Firebase isn't configured yet; only sending a
notification fails until it is.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

_app: Any = None
_app_lock = threading.Lock()


class FcmUnavailable(RuntimeError):
    """FIREBASE_SERVICE_ACCOUNT_JSON is missing or invalid."""


def _get_app() -> Any:
    global _app
    if _app is not None:
        return _app

    with _app_lock:
        if _app is not None:
            return _app

        raw = (settings.firebase_service_account_json or "").strip()
        if not raw:
            raise FcmUnavailable(
                "FIREBASE_SERVICE_ACCOUNT_JSON is not set -- add it to "
                "backend/.env (a file path) or as a Railway env var (the "
                "JSON itself)."
            )

        import firebase_admin
        from firebase_admin import credentials

        try:
            if raw.lstrip().startswith("{"):
                cert = credentials.Certificate(json.loads(raw))
            else:
                cert = credentials.Certificate(raw)
            _app = firebase_admin.initialize_app(cert)
        except Exception as exc:
            raise FcmUnavailable(
                f"Could not initialize Firebase Admin SDK: {exc}"
            ) from exc

        log.info("Firebase Admin SDK initialized.")
        return _app


def send_to_token(
    *, token: str, title: str, body: str, image_url: str | None = None
) -> bool:
    """Send one push to a single device token. Returns True on success,
    False on any per-token failure (e.g. the token is stale/unregistered) --
    the caller decides whether that's worth surfacing.
    """
    _get_app()
    from firebase_admin import messaging

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body, image=image_url),
        # Mirrored into the data payload too, so PushNotificationService on
        # the client can render the Notification Detail screen from
        # whichever of onMessageOpenedApp/getInitialMessage fires, without
        # depending on platform-specific notification-payload parsing.
        data={
            "title": title,
            "body": body,
            "imageUrl": image_url or "",
        },
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(image=image_url),
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(aps=messaging.Aps(content_available=True)),
            fcm_options=messaging.APNSFCMOptions(image=image_url) if image_url else None,
        ),
    )
    try:
        messaging.send(message)
        return True
    except Exception as exc:
        log.warning("FCM send to token failed: %s", exc)
        return False


_MULTICAST_BATCH = 500  # FCM's own per-call limit


def send_to_tokens(
    *, tokens: list[str], title: str, body: str, image_url: str | None = None
) -> tuple[int, int]:
    """Send to every token in ``tokens`` (the admin router passes every
    registered device for a broadcast). Returns (success_count,
    failure_count) -- real per-device numbers, not an estimate, since
    that's what the admin dashboard's send-notification result shows.
    Batches at 500 tokens/call, FCM's own multicast limit.
    """
    if not tokens:
        return 0, 0

    _get_app()
    from firebase_admin import messaging

    success = 0
    failure = 0
    for start in range(0, len(tokens), _MULTICAST_BATCH):
        batch = tokens[start : start + _MULTICAST_BATCH]
        message = messaging.MulticastMessage(
            tokens=batch,
            notification=messaging.Notification(title=title, body=body, image=image_url),
            data={
                "title": title,
                "body": body,
                "imageUrl": image_url or "",
            },
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(image=image_url),
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(aps=messaging.Aps(content_available=True)),
                fcm_options=messaging.APNSFCMOptions(image=image_url) if image_url else None,
            ),
        )
        try:
            response = messaging.send_each_for_multicast(message)
            success += response.success_count
            failure += response.failure_count
        except Exception as exc:
            log.warning("FCM multicast batch failed: %s", exc)
            failure += len(batch)

    return success, failure
