"""Force-update check.

Contract (from ``lib/services/version_check_service.dart``):

  GET /app/version-check?current=1.0.1
      <- 200 {"minSupportedVersion", "latestVersion", "playStoreUrl", "updateRequired"}

Called once at startup (see SplashScreen). ``updateRequired`` is computed
server-side so the client never re-implements version-comparison logic.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..app_config import get_force_update_config, is_below
from ..db import get_db
from ..schemas import VersionCheckResponse

router = APIRouter(prefix="/app", tags=["app"])


@router.get("/version-check", response_model=VersionCheckResponse)
def version_check(
    current: str = Query(..., description="The installed app's version, e.g. 1.0.1"),
    db: Session = Depends(get_db),
) -> VersionCheckResponse:
    min_supported, latest, play_store_url = get_force_update_config(db)
    return VersionCheckResponse(
        minSupportedVersion=min_supported,
        latestVersion=latest,
        playStoreUrl=play_store_url,
        updateRequired=is_below(current, min_supported),
    )
