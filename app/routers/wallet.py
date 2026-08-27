"""Prism Cloud Wallet: sync bank/ID/passport/license cards across devices.

Contract (from ``lib/services/wallet_cloud_service.dart``):

  POST /cloud/cards        (multipart: email, id, type, cardData, front?, back?)
      <- {"id", "modifiedAt", "message"}   -- create or update by id (upsert)

  GET  /cloud/cards        (header X-User-Email)
      <- {"cards": [{"id","type","cardData","hasFrontImage","hasBackImage","modifiedAt"}, ...]}

  GET  /cloud/cards/{id}/front   (header X-User-Email)
  GET  /cloud/cards/{id}/back    (header X-User-Email)
      <- raw image bytes

  DELETE /cloud/cards/{id}   (header X-User-Email)
      <- 204

Cards are not counted against the Prism Cloud storage quota that governs
``/cloud/documents`` -- they're a different, typically much smaller, data
domain (a handful of small JSON records plus at most two photos each), and
conflating the two would make the document-library quota confusing to
reason about. This table also carries no server-side interpretation of
``card_data``'s contents: it is the Flutter client's own field set
(``lib/models/wallet_card.dart``), stored and returned verbatim.
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..logging_config import get_logger
from ..models import Card, User
from ..schemas import CardListResponse, CardSummary, CardUploadResponse
from ..security import is_valid_email, normalize_email

log = get_logger(__name__)

router = APIRouter(prefix="/cloud/cards", tags=["wallet"])

_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # a photographed ID/card photo, generously capped


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _require_email_header(x_user_email: str | None) -> str:
    email = normalize_email(x_user_email)
    if not email or not is_valid_email(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid X-User-Email header is required.",
        )
    return email


def _get_or_create_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, created_at=_utcnow(), is_active=True)
        db.add(user)
        db.flush()
        log.info("Created user row for %s via Wallet sync", email)
    return user


def _to_summary(card: Card) -> CardSummary:
    return CardSummary(
        id=card.id,
        type=card.type,
        cardData=card.card_data,
        hasFrontImage=card.front_image is not None,
        hasBackImage=card.back_image is not None,
        modifiedAt=card.modified_at,
    )


# ---------------------------------------------------------------------------
# GET /cloud/cards
# ---------------------------------------------------------------------------
@router.get("", response_model=CardListResponse)
def list_cards(
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> CardListResponse:
    email = _require_email_header(x_user_email)

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        return CardListResponse(cards=[])

    rows = db.scalars(
        select(Card)
        .where(Card.user_id == user.id, Card.deleted_at.is_(None))
        .order_by(Card.modified_at.desc())
    ).all()
    return CardListResponse(cards=[_to_summary(row) for row in rows])


# ---------------------------------------------------------------------------
# POST /cloud/cards  (multipart upsert)
# ---------------------------------------------------------------------------
@router.post("", response_model=CardUploadResponse, status_code=status.HTTP_201_CREATED)
def upsert_card(
    email: str = Form(...),
    id: str = Form(...),
    type: str = Form(...),
    cardData: str = Form(...),
    front: UploadFile | None = File(default=None),
    back: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> CardUploadResponse:
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address."
        )

    try:
        parsed_data = json.loads(cardData)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"'cardData' is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed_data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="'cardData' must be a JSON object."
        )

    user = _get_or_create_user(db, normalized)

    # This route isn't declared async, so FastAPI runs it in a threadpool --
    # a plain blocking .file.read() is fine and keeps this simple.
    front_bytes = front.file.read() if front is not None else None
    if front_bytes is not None and len(front_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Card image is larger than the {_MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.",
        )
    back_bytes = back.file.read() if back is not None else None
    if back_bytes is not None and len(back_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Card image is larger than the {_MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.",
        )

    now = _utcnow()
    card = db.get(Card, id)
    if card is not None and card.user_id != user.id:
        # Someone else's card id -- report exactly like "doesn't exist" so
        # ids can't be probed for existence across accounts.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.")

    if card is None:
        card = Card(
            id=id,
            user_id=user.id,
            type=type,
            card_data=parsed_data,
            front_image=front_bytes,
            back_image=back_bytes,
            created_at=now,
            modified_at=now,
        )
        db.add(card)
    else:
        card.type = type
        card.card_data = parsed_data
        card.deleted_at = None
        if front_bytes is not None:
            card.front_image = front_bytes
        if back_bytes is not None:
            card.back_image = back_bytes
        card.modified_at = now

    db.flush()
    log.info("Synced Wallet card %s (%s) for %s", id, type, normalized)

    return CardUploadResponse(id=card.id, modifiedAt=card.modified_at, message="Card synced.")


def _owned_card(db: Session, card_id: str, email: str) -> Card:
    user = db.scalar(select(User).where(User.email == email))
    card = db.get(Card, card_id) if user is not None else None
    if user is None or card is None or card.user_id != user.id or card.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.")
    return card


# ---------------------------------------------------------------------------
# GET /cloud/cards/{card_id}/front | /back
# ---------------------------------------------------------------------------
@router.get(
    "/{card_id}/front",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 404: {"description": "Not found"}},
)
def download_front_image(
    card_id: str,
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> Response:
    email = _require_email_header(x_user_email)
    card = _owned_card(db, card_id, email)
    if card.front_image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This card has no front image.")
    return Response(content=card.front_image, media_type="image/jpeg")


@router.get(
    "/{card_id}/back",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 404: {"description": "Not found"}},
)
def download_back_image(
    card_id: str,
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> Response:
    email = _require_email_header(x_user_email)
    card = _owned_card(db, card_id, email)
    if card.back_image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This card has no back image.")
    return Response(content=card.back_image, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# DELETE /cloud/cards/{card_id}
# ---------------------------------------------------------------------------
@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card(
    card_id: str,
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    db: Session = Depends(get_db),
) -> Response:
    email = _require_email_header(x_user_email)
    card = _owned_card(db, card_id, email)
    card.deleted_at = _utcnow()
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
