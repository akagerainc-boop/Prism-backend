"""Prism AI conversation history sync.

Contract (from ``lib/services/chat_history_service.dart`` /
``lib/services/ai_history_service.dart``):

    POST   /ai/history                 body: AiChatSessionBody
        <- {"id", "status": "saved"}

    GET    /ai/history                 header X-User-Email
        <- {"sessions": [{"id", "title", "createdAt", "messages": [...]}]}

    DELETE /ai/history/{session_id}    header X-User-Email
        <- 204

Local storage (``ChatHistoryService``) is always the source of truth on the
device that created a session -- this endpoint exists so the same
conversation is visible after installing Prism on another device. Media
attachments are NOT synced (see ``AiChatMessageBody`` in ``schemas.py``);
only the text conversation round-trips.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..logging_config import get_logger
from ..models import AiChatSession
from ..schemas import (
    AiChatHistoryResponse,
    AiChatMessageBody,
    AiChatSessionBody,
    AiChatSessionSummary,
)
from ..security import is_valid_email, normalize_email

log = get_logger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-history"])


def _require_email_header(x_user_email: str | None) -> str:
    email = normalize_email(x_user_email)
    if not email or not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid X-User-Email header is required.",
        )
    return email


@router.post("/history", status_code=status.HTTP_200_OK)
def save_session(body: AiChatSessionBody, db: Session = Depends(get_db)) -> dict:
    email = normalize_email(body.email)
    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address."
        )
    if not body.messages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="A session needs at least one message."
        )

    messages_json = json.dumps([m.model_dump() for m in body.messages])
    created_at = body.createdAt.replace(tzinfo=None)

    existing = db.get(AiChatSession, body.id)
    if existing is not None:
        if existing.user_email != email:
            # Never let one email overwrite another's session by guessing an id.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
        existing.title = body.title
        existing.messages_json = messages_json
        db.add(existing)
    else:
        db.add(
            AiChatSession(
                id=body.id,
                user_email=email,
                title=body.title,
                messages_json=messages_json,
                created_at=created_at,
            )
        )
    db.flush()

    return {"id": body.id, "status": "saved"}


@router.get("/history", response_model=AiChatHistoryResponse)
def list_sessions(
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> AiChatHistoryResponse:
    email = _require_email_header(x_user_email)

    rows = db.scalars(
        select(AiChatSession)
        .where(AiChatSession.user_email == email)
        .order_by(AiChatSession.created_at.desc())
    ).all()

    sessions: list[AiChatSessionSummary] = []
    for row in rows:
        try:
            raw_messages = json.loads(row.messages_json)
        except (json.JSONDecodeError, TypeError):
            log.warning("Could not parse messages_json for AI session %s", row.id)
            continue
        sessions.append(
            AiChatSessionSummary(
                id=row.id,
                title=row.title,
                createdAt=row.created_at,
                messages=[AiChatMessageBody.model_validate(m) for m in raw_messages],
            )
        )

    return AiChatHistoryResponse(sessions=sessions)


@router.delete("/history/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> None:
    email = _require_email_header(x_user_email)
    db.execute(
        delete(AiChatSession).where(
            AiChatSession.id == session_id, AiChatSession.user_email == email
        )
    )
    db.flush()
