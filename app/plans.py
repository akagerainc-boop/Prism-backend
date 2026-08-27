"""Prism is fully free -- one flat Prism Cloud storage limit for every
account. No paid tiers exist; ``normalize_plan`` always resolves to
``"free"`` regardless of what a (now-nonexistent) client billing screen
might send, which also self-heals any account row left over from before
paid plans were removed the next time ``/cloud/account`` runs.

Mirrors ``lib/constant/storage_limits.dart``'s ``kCloudStorageLimitMb`` --
change both together if this number ever changes.
"""

from __future__ import annotations

MB = 1_000_000
GB = 1_000_000_000

DEFAULT_PLAN = "free"

PLAN_STORAGE_LIMITS: dict[str, int] = {
    "free": 1 * GB,  # 1 GB
}


def normalize_plan(plan: str | None) -> str:
    """Always ``"free"`` -- kept as a function (not a constant) so callers
    that used to branch on a client-supplied plan don't need to change.
    """
    return DEFAULT_PLAN


def storage_limit_for(plan: str | None) -> int:
    return PLAN_STORAGE_LIMITS[normalize_plan(plan)]
