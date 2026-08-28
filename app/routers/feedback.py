"""POST /feedback -- "How was this scan?" prompt responses.

Contract (from ``lib/services/scan_feedback_service.dart``):

  POST /feedback
      -> {"email": "...", "rating": "perfect"|"good"|"bad"|"veryBad",
          "suggestion": "..." (optional)}
      <- 201 {"message": "..."}
      <- 4xx {"message": "..."}

The app shows this prompt every 5th document scanned, capped at the first
2 prompts (see ``lib/services/scan_feedback_tracker.dart`` -- purely a
local counter, this endpoint has no opinion on when it's called). Every
submission is saved, one row per response.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..logging_config import get_logger
from ..models import ScanFeedback, User
from ..schemas import MessageResponse, ScanFeedbackBody
from ..security import is_valid_email, normalize_email

log = get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

_VALID_RATINGS = {"perfect", "good", "bad", "veryBad"}
_MAX_SUGGESTION_LENGTH = 2000


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@router.post("", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def submit_feedback(body: ScanFeedbackBody, db: Session = Depends(get_db)) -> MessageResponse:
    email = normalize_email(body.email)
    if not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address."
        )

    rating = (body.rating or "").strip()
    if rating not in _VALID_RATINGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'rating' must be one of {sorted(_VALID_RATINGS)}.",
        )

    suggestion = (body.suggestion or "").strip() or None
    if suggestion and len(suggestion) > _MAX_SUGGESTION_LENGTH:
        suggestion = suggestion[:_MAX_SUGGESTION_LENGTH]

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, created_at=_utcnow(), is_active=True)
        db.add(user)
        db.flush()
        log.info("Created user row for %s via scan feedback", email)

    db.add(
        ScanFeedback(
            id=str(uuid.uuid4()),
            user_id=user.id,
            rating=rating,
            suggestion=suggestion,
            created_at=_utcnow(),
        )
    )
    db.flush()

    log.info("Scan feedback recorded for %s: %s", email, rating)
    return MessageResponse(message="Thanks for the feedback!")
