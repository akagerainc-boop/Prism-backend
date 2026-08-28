"""SQLAlchemy ORM models mirroring ``schema.sql``.

``schema.sql`` is the authoritative DDL (it is what the user imports through
phpMyAdmin). These classes must stay in sync with it.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account: Mapped["Account | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Account(Base):
    """Prism Cloud account: the plan and the storage limit it grants."""

    __tablename__ = "accounts"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    storage_limit_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="account")


class OtpCode(Base):
    """One issued OTP challenge, keyed by its opaque verification token."""

    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    verification_token: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    otp_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    otp_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_otp_codes_email", "email"),
        Index("ix_otp_codes_expires_at", "expires_at"),
    )


class OtpRequestLog(Base):
    """Timestamps of OTP requests, used for per-email rate limiting."""

    __tablename__ = "otp_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    requested_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    client_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_otp_requests_email_time", "email", "requested_at"),)


class Document(Base):
    """A PDF backed up to Prism Cloud."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID4 string
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_data: Mapped[bytes] = mapped_column(LONGBLOB, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/pdf"
    )
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    modified_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="documents")

    __table_args__ = (Index("ix_documents_user_modified", "user_id", "modified_at"),)


class Card(Base):
    """A Wallet card -- bank card, national ID, passport, or driving
    license -- synced to Prism Cloud so it's available on any device.

    ``card_data`` holds every non-image field (card number, holder name,
    expiry, CVV, ID number, etc.) as one JSON object; the Flutter client
    owns that shape (see ``lib/models/wallet_card.dart``) so this table
    doesn't need a migration every time a field is added there.
    """

    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    card_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    front_image: Mapped[Optional[bytes]] = mapped_column(LONGBLOB, nullable=True)
    back_image: Mapped[Optional[bytes]] = mapped_column(LONGBLOB, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    modified_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()

    __table_args__ = (Index("ix_cards_user_modified", "user_id", "modified_at"),)


class StorageUsage(Base):
    """Denormalised cache of a user's consumed bytes.

    The authoritative number is always ``SUM(documents.size_bytes)``; this row
    is refreshed alongside it so dashboards can read it cheaply.
    """

    __tablename__ = "storage_usage"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SyncState(Base):
    """Per-device sync cursor so clients can pull only what changed."""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_synced_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    cursor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uq_sync_user_device"),
    )


class ScanFeedback(Base):
    """One "how was this scan?" response -- the app prompts for this every
    5th document scanned, capped at the first 2 prompts (see
    ``lib/services/scan_feedback_tracker.dart``). ``suggestion`` is
    optional free text.
    """

    __tablename__ = "scan_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(16), nullable=False)  # perfect|good|bad|veryBad
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_scan_feedback_user", "user_id"),)


class StudentApplication(Base):
    """Historical record only -- the Student plan (and every paid plan) was
    removed; Prism is fully free now, so `routers/billing.py` (which wrote
    to this table) was deleted along with it. Left in place, unwritten, so
    any applications submitted before the removal aren't silently dropped.
    """

    __tablename__ = "student_applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID4 string
    user_email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    student_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    proof_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_student_applications_email", "user_email"),)


class AiChatSession(Base):
    """One saved Prism AI conversation, synced from the client.

    ``messages_json`` stores the exact message list the client already
    persists locally (see ``lib/services/chat_history_service.dart``) --
    text, sender, attachment name, error flag. Media attachments
    (``mediaPath``/``mediaMime``) are NOT uploaded here and stay
    device-local; only the text conversation round-trips through the
    server, so a session restored on a new device is complete except for
    inline images/files from a previous device.
    """

    __tablename__ = "ai_chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # client-generated
    user_email: Mapped[str] = mapped_column(String(320), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Conversation")
    messages_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_ai_chat_sessions_email_created", "user_email", "created_at"),
    )


class OcrJob(Base):
    """A structured-OCR / book-reconstruction job.

    Backs ``GET /document/book/{job_id}/file``: the merged, page-numbered PDF is
    written to disk and this row records where it went.
    """

    __tablename__ = "ocr_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID4 string
    user_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="book")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    json_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
