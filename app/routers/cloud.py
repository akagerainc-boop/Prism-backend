"""Prism Cloud: account, document listing, upload and download.

Contract (from ``lib/services/prism_cloud_service.dart`` +
``lib/models/prism_cloud_account.dart``):

  POST /cloud/account
      -> {"email": "...", "plan": "..."}
      <- {"email", "plan", "storageLimitBytes", "storageUsedBytes"}

  GET  /cloud/documents        (header X-User-Email)
      <- {"documents": [{"id", "name", "sizeBytes", "modifiedAt"}, ...]}

  POST /cloud/documents        (multipart: email, name, file)
      <- 2xx (client only checks the status code)

  GET  /cloud/documents/{id}/file   (header X-User-Email)  [new]
      <- raw PDF bytes, application/pdf, Content-Disposition: attachment
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..logging_config import get_logger
from ..models import Account, Document, StorageUsage, User
from ..plans import DEFAULT_PLAN, normalize_plan, storage_limit_for
from ..schemas import (
    CloudAccountBody,
    CloudAccountResponse,
    CloudDocumentListResponse,
    CloudDocumentSummary,
    CloudUploadResponse,
)
from ..security import is_valid_email, normalize_email

log = get_logger(__name__)

router = APIRouter(prefix="/cloud", tags=["cloud"])

_CHUNK = 1024 * 1024


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _get_or_create_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, created_at=_utcnow(), is_active=True)
        db.add(user)
        db.flush()
        log.info("Created user row for %s via Prism Cloud", email)
    if db.get(StorageUsage, user.id) is None:
        db.add(StorageUsage(user_id=user.id, used_bytes=0, document_count=0))
    return user


def _used_bytes(db: Session, user_id: int) -> int:
    """Authoritative usage: the live sum of this user's stored document sizes."""
    total = db.scalar(
        select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
            Document.user_id == user_id, Document.deleted_at.is_(None)
        )
    )
    return int(total or 0)


def _refresh_usage_cache(db: Session, user_id: int) -> int:
    used = _used_bytes(db, user_id)
    count = int(
        db.scalar(
            select(func.count(Document.id)).where(
                Document.user_id == user_id, Document.deleted_at.is_(None)
            )
        )
        or 0
    )
    # autoflush is disabled for request sessions. When a new cloud account is
    # created, _get_or_create_user() may already have queued this row in
    # db.new, so db.get() cannot see it yet and must not create a duplicate.
    usage = next(
        (
            pending
            for pending in db.new
            if isinstance(pending, StorageUsage) and pending.user_id == user_id
        ),
        None,
    ) or db.get(StorageUsage, user_id)
    if usage is None:
        usage = StorageUsage(user_id=user_id)
    usage.used_bytes = used
    usage.document_count = count
    db.add(usage)
    return used


def _require_email_header(x_user_email: str | None) -> str:
    email = normalize_email(x_user_email)
    if not email or not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid X-User-Email header is required.",
        )
    return email


# ---------------------------------------------------------------------------
# POST /cloud/account
# ---------------------------------------------------------------------------
@router.post("/account", response_model=CloudAccountResponse)
def create_or_fetch_account(
    body: CloudAccountBody,
    db: Session = Depends(get_db),
) -> CloudAccountResponse:
    email = normalize_email(body.email)
    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid email address.",
        )

    plan = normalize_plan(body.plan)
    limit = storage_limit_for(plan)

    user = _get_or_create_user(db, email)

    account = db.get(Account, user.id)
    if account is None:
        account = Account(user_id=user.id, plan=plan, storage_limit_bytes=limit)
    else:
        # Prism is fully free -- normalize_plan()/storage_limit_for() always
        # resolve to "free" now, which also self-heals any account row left
        # over from before paid plans were removed.
        account.plan = plan
        account.storage_limit_bytes = limit
    db.add(account)

    used = _refresh_usage_cache(db, user.id)
    db.flush()

    return CloudAccountResponse(
        email=email,
        plan=plan,
        storageLimitBytes=limit,
        storageUsedBytes=used,
    )


# ---------------------------------------------------------------------------
# GET /cloud/documents
# ---------------------------------------------------------------------------
@router.get("/documents", response_model=CloudDocumentListResponse)
def list_documents(
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> CloudDocumentListResponse:
    email = _require_email_header(x_user_email)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # Not an error -- an unknown address simply has no cloud documents.
        return CloudDocumentListResponse(documents=[])

    rows = db.scalars(
        select(Document)
        .where(Document.user_id == user.id, Document.deleted_at.is_(None))
        .order_by(Document.modified_at.desc())
    ).all()

    return CloudDocumentListResponse(
        documents=[
            CloudDocumentSummary(
                id=row.id,
                name=row.name,
                sizeBytes=int(row.size_bytes),
                modifiedAt=row.modified_at,
            )
            for row in rows
        ]
    )


# ---------------------------------------------------------------------------
# POST /cloud/documents  (multipart upload)
# ---------------------------------------------------------------------------
@router.post(
    "/documents",
    response_model=CloudUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    email: str = Form(...),
    name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> CloudUploadResponse:
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid email address.",
        )

    doc_name = (name or "").strip() or (file.filename or "Untitled.pdf")
    if len(doc_name) > 512:
        doc_name = doc_name[:512]

    user = _get_or_create_user(db, normalized)

    account = db.get(Account, user.id)
    if account is None:
        account = Account(
            user_id=user.id,
            plan=DEFAULT_PLAN,
            storage_limit_bytes=storage_limit_for(DEFAULT_PLAN),
        )
        db.add(account)
        db.flush()

    limit = int(account.storage_limit_bytes)
    used = _used_bytes(db, user.id)

    doc_id = str(uuid.uuid4())

    # Buffer in memory, enforcing both the per-request cap and the plan quota
    # as we go, so an oversized upload is rejected before it fully lands.
    buffer = io.BytesIO()
    digest = hashlib.sha256()
    written = 0
    try:
        while True:
            chunk = file.file.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)

            if written > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        "That file is larger than the "
                        f"{settings.max_upload_bytes // (1024 * 1024)} MB "
                        "per-file limit."
                    ),
                )

            if used + written > limit:
                raise HTTPException(
                    status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
                    detail=(
                        f"This upload would exceed your {account.plan} plan's "
                        f"{_human_bytes(limit)} of Prism Cloud storage "
                        f"({_human_bytes(used)} already used). "
                        "Free up space or upgrade your plan."
                    ),
                )

            buffer.write(chunk)
            digest.update(chunk)
    finally:
        file.file.close()

    if written == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty."
        )

    now = _utcnow()
    document = Document(
        id=doc_id,
        user_id=user.id,
        name=doc_name,
        size_bytes=written,
        file_data=buffer.getvalue(),
        content_type=file.content_type or "application/pdf",
        checksum_sha256=digest.hexdigest(),
        created_at=now,
        modified_at=now,
    )
    db.add(document)
    db.flush()

    total_used = _refresh_usage_cache(db, user.id)
    log.info(
        "Stored document %s (%d bytes) for %s -- %d/%d bytes used",
        doc_id,
        written,
        normalized,
        total_used,
        limit,
    )

    return CloudUploadResponse(
        id=doc_id,
        name=doc_name,
        sizeBytes=written,
        modifiedAt=now,
        storageUsedBytes=total_used,
        storageLimitBytes=limit,
        message="Uploaded to Prism Cloud.",
    )


# ---------------------------------------------------------------------------
# GET /cloud/documents/{document_id}/file   (new -- Flutter side to follow)
# ---------------------------------------------------------------------------
@router.get(
    "/documents/{document_id}/file",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "The stored PDF"},
        404: {"description": "Not found or not owned by this address"},
    },
)
def download_document(
    document_id: str,
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> Response:
    email = _require_email_header(x_user_email)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    document = db.get(Document, document_id)
    # A document owned by someone else is reported exactly like a missing one,
    # so ids can't be probed for existence.
    if (
        document is None
        or document.user_id != user.id
        or document.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Document not found.")

    safe_name = document.name.replace('"', "").replace("\r", "").replace("\n", "")
    if not safe_name.lower().endswith(".pdf"):
        safe_name = f"{safe_name}.pdf"

    return Response(
        content=document.file_data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


def _human_bytes(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:g} GB"
    if value >= 1_000_000:
        return f"{value / 1_000_000:g} MB"
    if value >= 1_000:
        return f"{value / 1_000:g} KB"
    return f"{value} B"
