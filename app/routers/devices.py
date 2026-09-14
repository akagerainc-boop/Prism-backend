"""Device / FCM token registration.

Contract (from ``lib/services/push_notification_service.dart``):

  POST /devices/register
      -> {"fcmToken": "...", "email": "..."|null, "platform": "...", "appVersion": "..."}
      <- 200 {"message": "..."}

Called on every app start (token may have changed) and whenever FCM hands
the client a refreshed token. ``email`` is null for guests -- they still
register so a broadcast notification reaches them before they sign in.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..logging_config import get_logger
from ..models import Device, User
from ..schemas import DeviceRegisterBody, DeviceRegisterResponse
from ..security import normalize_email

log = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@router.post("/register", response_model=DeviceRegisterResponse)
def register_device(
    body: DeviceRegisterBody,
    db: Session = Depends(get_db),
) -> DeviceRegisterResponse:
    user_id = None
    if body.email:
        email = normalize_email(body.email)
        user = db.scalar(select(User).where(User.email == email))
        user_id = user.id if user else None

    device = db.scalar(select(Device).where(Device.fcm_token == body.fcmToken))
    if device is None:
        device = Device(fcm_token=body.fcmToken)

    device.user_id = user_id
    device.platform = body.platform
    device.app_version = body.appVersion
    device.last_seen_at = _utcnow()
    db.add(device)

    return DeviceRegisterResponse(message="Registered.")
