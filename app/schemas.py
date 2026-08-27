"""Pydantic request/response models.

These mirror the Flutter client's contracts EXACTLY. The field names below are
what ``lib/UI/auth/auth_service.dart``, ``lib/services/prism_cloud_service.dart``
and ``lib/models/prism_cloud_account.dart`` read -- do not rename them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------
class MessageResponse(BaseModel):
    """Every error body the client sees has this shape (it reads ``message``)."""

    message: str


class HealthResponse(BaseModel):
    status: str = "ok"


# ---------------------------------------------------------------------------
# Auth -- POST /auth/email/request-otp, POST /auth/email/verify-otp
# ---------------------------------------------------------------------------
class RequestOtpBody(BaseModel):
    email: str


class RequestOtpResponse(BaseModel):
    verificationToken: str
    message: str


class VerifyOtpBody(BaseModel):
    email: str
    otp: str
    verificationToken: str


class VerifyOtpResponse(BaseModel):
    message: str
    sessionToken: str


# ---------------------------------------------------------------------------
# Prism Cloud -- POST /cloud/account, GET /cloud/documents
# ---------------------------------------------------------------------------
class CloudAccountBody(BaseModel):
    email: str
    plan: str


class CloudAccountResponse(BaseModel):
    """Read by ``PrismCloudAccount.fromJson``."""

    email: str
    plan: str
    storageLimitBytes: int
    storageUsedBytes: int


class CloudDocumentSummary(BaseModel):
    """Read by ``CloudDocumentSummary.fromJson``.

    ``modifiedAt`` is serialised as an ISO-8601 UTC string, which Dart's
    ``DateTime.tryParse`` accepts.
    """

    id: str
    name: str
    sizeBytes: int
    modifiedAt: dt.datetime


class CloudDocumentListResponse(BaseModel):
    documents: list[CloudDocumentSummary]


class CloudUploadResponse(BaseModel):
    id: str
    name: str
    sizeBytes: int
    modifiedAt: dt.datetime
    storageUsedBytes: int
    storageLimitBytes: int
    message: str


# ---------------------------------------------------------------------------
# Billing -- POST /billing/student-application
# ---------------------------------------------------------------------------
class StudentApplicationResponse(BaseModel):
    id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Prism AI history -- POST /ai/history, GET /ai/history, DELETE /ai/history/{id}
# Mirrors lib/services/chat_history_service.dart's ChatMessageRecord /
# ChatSessionRecord exactly -- do not rename these fields.
# ---------------------------------------------------------------------------
class AiChatMessageBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    user: bool
    attachmentName: str | None = None
    error: bool = False
    # Deliberately NOT synced -- device-local paths, meaningless on the
    # server or another device. Accepted-and-ignored so the client can send
    # its local record verbatim without stripping fields first.
    mediaPath: str | None = None
    mediaMime: str | None = None


class AiChatSessionBody(BaseModel):
    email: str
    id: str
    title: str
    createdAt: dt.datetime
    messages: list[AiChatMessageBody]


class AiChatSessionSummary(BaseModel):
    id: str
    title: str
    createdAt: dt.datetime
    messages: list[AiChatMessageBody]


class AiChatHistoryResponse(BaseModel):
    sessions: list[AiChatSessionSummary]


# ---------------------------------------------------------------------------
# Simple OCR -- POST /document/ocr, POST /document/ocr/book
# EXISTING CONTRACT. The client depends on these shapes verbatim:
#   /document/ocr      -> {"text": "..."}            (CloudOcrService.recognizeText)
#   /document/ocr/book -> {"pages": ["...", "..."]}  (CloudOcrService.recognizeBook)
# Do not add required fields or rename these keys.
# ---------------------------------------------------------------------------
class OcrTextResponse(BaseModel):
    text: str


class OcrBookResponse(BaseModel):
    pages: list[str]


# ---------------------------------------------------------------------------
# Structured document model (normalised scanner output)
# ---------------------------------------------------------------------------
ElementType = str  # open vocabulary; see document_model.ELEMENT_TYPES


class TableCell(BaseModel):
    model_config = ConfigDict(extra="allow")

    row: int = 0
    col: int = 0
    rowSpan: int = 1
    colSpan: int = 1
    text: str = ""
    isHeader: bool = False
    bbox: list[float] | None = None


class TableData(BaseModel):
    model_config = ConfigDict(extra="allow")

    rowCount: int = 0
    columnCount: int = 0
    cells: list[TableCell] = Field(default_factory=list)
    html: str | None = None


class DocElement(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    page: int = 1
    type: ElementType = "text"
    text: str | None = None
    html: str | None = None
    # [x0, y0, x1, y1] in source-image pixel coordinates, origin top-left.
    bbox: list[float] = Field(default_factory=list)
    polygon: list[list[float]] | None = None
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    # None when the underlying model does not expose a score for this element
    # type -- never faked, and never 1.0-by-default.
    confidence: float | None = None
    readingOrder: int = 0
    parentId: str | None = None
    childIds: list[str] = Field(default_factory=list)
    sourceImage: str | None = None
    table: TableData | None = None
    level: int | None = None  # heading depth, when known
    # Real text styling (Perfect OCR / any other producer that can report it).
    # None/False are honest defaults -- never inferred as "probably styled".
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    color: str | None = None  # "#RRGGBB"
    highlightColor: str | None = None  # "#RRGGBB"
    align: str | None = None  # "left" | "center" | "right"
    # Only meaningful when type == "checkbox".
    checked: bool | None = None
    # Optional secondary-engine cross-check fields.
    # crossValidated=True means VL's independent reading agreed closely with
    # A secondary engine's; confidence is nudged up (never to 1.0) when it does.
    # crossValidated=False means they disagreed -- vlText carries VL's
    # alternate reading for that region so a human can see the discrepancy;
    # the original text is never silently overwritten.
    crossValidated: bool | None = None
    vlAgreement: float | None = None
    vlText: str | None = None


class DocPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    pageNumber: int = 1
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    imageRef: str | None = None
    imageBase64: str | None = None
    elements: list[DocElement] = Field(default_factory=list)
    markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)
    # Whole-page text from an optional secondary engine, when available.
    vlAvailable: bool = False
    vlText: str | None = None


class StructuredDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    documentId: str
    sourceFilename: str | None = None
    pageCount: int = 0
    generator: str = "prism-backend"
    engine: str | None = None
    # Set when a secondary engine ran as a cross-check alongside `engine`; None
    # when VL was disabled, unavailable, or failed for this document.
    vlEngine: str | None = None
    createdAt: dt.datetime | None = None
    pages: list[DocPage] = Field(default_factory=list)
    # Aggregate confidence over all recognised text, 0..1. Advisory only --
    # low values mean "flag these regions for user correction", never
    # "this document is 100% correct".
    meanTextConfidence: float | None = None
    lowConfidenceElementIds: list[str] = Field(default_factory=list)


class BookStructureResponse(BaseModel):
    jobId: str
    pageCount: int
    # Relative URL for the merged, page-numbered PDF.
    bookPdfUrl: str
    document: StructuredDocument
    pages: list[str]  # plain text per page, mirroring /document/ocr/book


class StructuredPdfResponse(BaseModel):
    """Structured scan result plus the reconstructed searchable PDF."""

    document: StructuredDocument
    text: str
    pdfBase64: str
    scannedImageBase64: str | None = None


ExportFormat = Literal["pdf", "docx", "markdown", "xlsx"]


class ExportBody(BaseModel):
    """Body of ``POST /document/{format}/export``.

    Accepts either the bare structured document, or ``{"document": {...}}``.
    """

    model_config = ConfigDict(extra="allow")

    document: StructuredDocument | None = None
    filename: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
