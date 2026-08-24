"""Plan catalog -- mirrors ``lib/models/prism_plan.dart``.

The Flutter catalog defines three plans and states their storage as
"50 MB" / "500 MB" / "5 GB". Those strings are marketing-decimal units, so the
byte limits below use decimal (1 MB = 1_000_000 B), which is what a user
comparing "50 MB" against their file manager will expect.

If ``prism_plan.dart`` ever changes, change these two things together.
"""

from __future__ import annotations

MB = 1_000_000
GB = 1_000_000_000

DEFAULT_PLAN = "free"

# plan id (matches PlanId.name on the Dart side) -> storage limit in bytes
PLAN_STORAGE_LIMITS: dict[str, int] = {
    "free": 50 * MB,  # 50 MB
    "student": 500 * MB,  # 500 MB
    "personal": 5 * GB,  # 5 GB
}


def normalize_plan(plan: str | None) -> str:
    """Map an incoming plan id onto a known plan, defaulting to ``free``.

    Accepts the raw ``PlanId.name`` the client sends, and tolerates values like
    ``"PlanId.free"`` or ``"Free"`` defensively.
    """
    if not plan:
        return DEFAULT_PLAN
    candidate = str(plan).strip().lower()
    if candidate.startswith("planid."):
        candidate = candidate.split(".", 1)[1]
    return candidate if candidate in PLAN_STORAGE_LIMITS else DEFAULT_PLAN


def storage_limit_for(plan: str | None) -> int:
    return PLAN_STORAGE_LIMITS[normalize_plan(plan)]
