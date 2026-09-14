"""Admin dashboard API -- users, devices, force-update config, notifications.

Every endpoint except ``POST /admin/auth/login`` requires
``Authorization: Bearer <admin token>`` (see ``get_current_admin``). Admin
tokens are issued by this router's login endpoint and are a distinct JWT
``typ`` from app-user session tokens (see ``security.create_admin_token``),
so one can never be used in place of the other even though both are signed
with the same ``JWT_SECRET``.

There is no public admin signup -- see backend/README.md for how to seed
the first admin_users row.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import fcm
from ..app_config import get_force_update_config, set_force_update_config
from ..db import get_db
from ..logging_config import get_logger
from ..models import Account, AdminUser, Device, Document, NotificationLog, StorageUsage, User
from ..schemas import (
    AdminAppConfigBody,
    AdminAppConfigResponse,
    AdminDeviceListResponse,
    AdminDeviceSummary,
    AdminDocumentSummary,
    AdminLoginBody,
    AdminLoginResponse,
    AdminNotificationSendBody,
    AdminNotificationSendResponse,
    AdminSetActiveBody,
    AdminUserDetailResponse,
    AdminUserListResponse,
    AdminUserSummary,
)
from ..security import (
    TokenError,
    create_admin_token,
    decode_admin_token,
    normalize_email,
    verify_admin_password,
)

log = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def get_current_admin(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AdminUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing admin token."
        )
    token = authorization[7:].strip()
    try:
        payload = decode_admin_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired admin token."
        ) from exc

    admin = db.get(AdminUser, int(payload["sub"]))
    if admin is None or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin account not found or disabled."
        )
    return admin


@router.post("/auth/login", response_model=AdminLoginResponse)
def admin_login(body: AdminLoginBody, db: Session = Depends(get_db)) -> AdminLoginResponse:
    email = normalize_email(body.email)
    admin = db.scalar(select(AdminUser).where(AdminUser.email == email))

    # Same generic failure regardless of which check failed, so this
    # endpoint can't be used to enumerate admin email addresses.
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
    )
    if admin is None or not admin.is_active:
        raise invalid
    if not verify_admin_password(body.password, admin.password_hash):
        raise invalid

    admin.last_login_at = _utcnow()
    db.add(admin)

    token = create_admin_token(admin_id=admin.id, email=admin.email)
    log.info("Admin login: %s", admin.email)
    return AdminLoginResponse(message="Signed in.", token=token, displayName=admin.display_name)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _user_summary(db: Session, user: User) -> AdminUserSummary:
    account = db.get(Account, user.id)
    usage = db.get(StorageUsage, user.id)
    used = db.scalar(
        select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
            Document.user_id == user.id, Document.deleted_at.is_(None)
        )
    )
    count = db.scalar(
        select(func.count(Document.id)).where(
            Document.user_id == user.id, Document.deleted_at.is_(None)
        )
    )
    return AdminUserSummary(
        id=user.id,
        email=user.email,
        createdAt=user.created_at,
        lastLoginAt=user.last_login_at,
        isActive=user.is_active,
        plan=account.plan if account else "free",
        storageUsedBytes=int(used or 0),
        storageLimitBytes=account.storage_limit_bytes if account else 0,
        documentCount=int(count or 0),
    )


@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    search: str | None = None,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin),
) -> AdminUserListResponse:
    stmt = select(User).order_by(User.created_at.desc())
    if search:
        stmt = stmt.where(User.email.ilike(f"%{search.strip()}%"))
    users = db.scalars(stmt.limit(500)).all()
    return AdminUserListResponse(users=[_user_summary(db, u) for u in users])


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
def get_user_detail(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin),
) -> AdminUserDetailResponse:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    docs = db.scalars(
        select(Document)
        .where(Document.user_id == user_id, Document.deleted_at.is_(None))
        .order_by(Document.modified_at.desc())
    ).all()

    return AdminUserDetailResponse(
        user=_user_summary(db, user),
        documents=[
            AdminDocumentSummary(
                id=d.id,
                name=d.name,
                sizeBytes=d.size_bytes,
                createdAt=d.created_at,
                modifiedAt=d.modified_at,
            )
            for d in docs
        ],
    )


@router.patch("/users/{user_id}/active", response_model=AdminUserSummary)
def set_user_active(
    user_id: int,
    body: AdminSetActiveBody,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> AdminUserSummary:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = body.isActive
    db.add(user)
    log.info(
        "Admin %s %s user %s", admin.email, "activated" if body.isActive else "deactivated", user.email
    )
    return _user_summary(db, user)


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
@router.get("/devices", response_model=AdminDeviceListResponse)
def list_devices(
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin),
) -> AdminDeviceListResponse:
    devices = db.scalars(select(Device).order_by(Device.last_seen_at.desc()).limit(1000)).all()
    total = db.scalar(select(func.count(Device.id))) or 0

    user_ids = {d.user_id for d in devices if d.user_id is not None}
    emails_by_id: dict[int, str] = {}
    if user_ids:
        for user in db.scalars(select(User).where(User.id.in_(user_ids))):
            emails_by_id[user.id] = user.email

    return AdminDeviceListResponse(
        devices=[
            AdminDeviceSummary(
                id=d.id,
                userEmail=emails_by_id.get(d.user_id) if d.user_id else None,
                platform=d.platform,
                appVersion=d.app_version,
                createdAt=d.created_at,
                lastSeenAt=d.last_seen_at,
            )
            for d in devices
        ],
        totalCount=int(total),
    )


# ---------------------------------------------------------------------------
# Force-update config
# ---------------------------------------------------------------------------
@router.get("/app-config", response_model=AdminAppConfigResponse)
def get_app_config(
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin),
) -> AdminAppConfigResponse:
    min_supported, latest, play_store_url = get_force_update_config(db)
    return AdminAppConfigResponse(
        minSupportedVersion=min_supported, latestVersion=latest, playStoreUrl=play_store_url
    )


@router.put("/app-config", response_model=AdminAppConfigResponse)
def update_app_config(
    body: AdminAppConfigBody,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> AdminAppConfigResponse:
    set_force_update_config(
        db,
        min_supported_version=body.minSupportedVersion,
        latest_version=body.latestVersion,
        play_store_url=body.playStoreUrl,
    )
    log.info(
        "Admin %s set min_supported_version=%s play_store_url=%s",
        admin.email, body.minSupportedVersion, body.playStoreUrl,
    )
    return AdminAppConfigResponse(
        minSupportedVersion=body.minSupportedVersion,
        latestVersion=body.latestVersion,
        playStoreUrl=body.playStoreUrl,
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@router.post("/notifications/send", response_model=AdminNotificationSendResponse)
def send_notification(
    body: AdminNotificationSendBody,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> AdminNotificationSendResponse:
    if body.target == "device":
        if body.deviceId is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="deviceId is required when target is 'device'.",
            )
        device = db.get(Device, body.deviceId)
        if device is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")
        tokens = [device.fcm_token]
        target_device_id = device.id
    else:
        tokens = [
            t for t in db.scalars(select(Device.fcm_token)).all() if t
        ]
        target_device_id = None

    try:
        success, failure = fcm.send_to_tokens(
            tokens=tokens, title=body.title, body=body.body, image_url=body.imageUrl
        )
    except fcm.FcmUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    log_row = NotificationLog(
        id=str(uuid.uuid4()),
        title=body.title,
        body=body.body,
        image_url=body.imageUrl,
        target=body.target,
        target_device_id=target_device_id,
        sent_by_admin_id=admin.id,
        success_count=success,
        failure_count=failure,
    )
    db.add(log_row)

    log.info(
        "Admin %s sent notification %r to target=%s (%d ok, %d failed)",
        admin.email, body.title, body.target, success, failure,
    )
    return AdminNotificationSendResponse(
        message="Sent.", successCount=success, failureCount=failure
    )
