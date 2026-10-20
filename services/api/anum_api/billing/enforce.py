"""Plan limits and read-only mode (docs/billing.md#plans-and-limits, #lifecycle).

Both refuse only new work: nothing is ever deleted, and every read keeps working. An
organisation without a subscription (the platform owner's own, or one not billed yet) has
no limits. Each check is a blocking database read: async routes call it through
``asyncio.to_thread``.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from ..errors import ApplicationError, ErrorCode
from ..schemas import TenantContext, utc_now
from .rows import open_billing
from .service import live_subscription
from .usage import GB, measure

READ_ONLY_TTL_SECONDS = 15.0
_cache: dict[str, tuple[float, bool]] = {}
_cache_lock = Lock()

READ_ONLY_MESSAGE = (
    "This organisation is read-only because an invoice is overdue and the grace period has ended. "
    "Everything stays readable. A billing admin can settle the invoice in Settings > Billing; "
    "writes come back as soon as the payment is recorded."
)
# Writes that stay open in read-only mode: billing itself, sign-in, approvals, privacy requests.
READ_ONLY_EXEMPT_PREFIXES = (
    "/api/v1/billing", "/api/v1/platform", "/api/v1/auth", "/api/v1/approvals", "/api/v1/me", "/api/v1/privacy",
    "/api/v1/signup", "/api/v1/workspaces/current/restore",
)
LABELS = {
    "seats": ("seats", "seat"),
    "workspaces": ("workspaces", "workspace"),
    "agent_runs": ("agent runs this month", "agent run"),
    "storage": ("GB of storage", "upload"),
    "messages": ("messages this month", "message"),
}
HINTS = {
    "seats": "Upgrade in Settings > Billing, or remove a pending invitation or a member you no longer need.",
    "workspaces": "Upgrade in Settings > Billing, or archive a workspace you no longer use.",
    "agent_runs": "Upgrade in Settings > Billing; the count starts again on the 1st of next month.",
    "storage": "Upgrade in Settings > Billing, or delete files you no longer need.",
    "messages": "Upgrade in Settings > Billing; the count starts again on the 1st of next month.",
}


def clear_cache(tenant_id: str | None = None) -> None:
    with _cache_lock:
        if tenant_id is None:
            _cache.clear()
        else:
            _cache.pop(tenant_id, None)


def is_read_only(tenant_id: str) -> bool:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(tenant_id)
    if cached and now - cached[0] < READ_ONLY_TTL_SECONDS:
        return cached[1]
    with open_billing(tenant_id) as rows:
        account = rows.get("billing_accounts", tenant_id=tenant_id)
    value = bool(account and account["state"] == "read_only")
    with _cache_lock:
        _cache[tenant_id] = (now, value)
    return value


def refuse_writes_when_read_only(method: str, path: str, context: TenantContext) -> None:
    if method in ("GET", "HEAD", "OPTIONS") or path.startswith(READ_ONLY_EXEMPT_PREFIXES):
        return
    if is_read_only(context.tenant_id):
        raise ApplicationError(ErrorCode.BILLING_READ_ONLY, READ_ONLY_MESSAGE, status_code=402)


def _limit(sub: dict[str, Any], key: str) -> int | None:
    if key == "seats" and sub["per_seat"]:
        return int(sub["seats"])
    raw = (sub.get("limits") or {}).get({"agent_runs": "agent_runs_per_month", "storage": "storage_gb", "messages": "messages_per_month"}.get(key, key))
    return None if raw is None else int(raw)


def require_capacity(context: TenantContext, key: str, adding: int = 1) -> None:
    """``402 plan_limit_reached`` when ``adding`` more of ``key`` would pass the plan's limit."""
    with open_billing(context.tenant_id) as rows:
        sub = live_subscription(rows)
    if sub is None:
        return
    limit = _limit(sub, key)
    if limit is None:
        return
    used = measure(context.tenant_id, utc_now()).get(key)
    if key == "storage":
        if used + adding <= limit * GB:
            return
        message = (
            f"Your plan includes {limit} GB of storage and {used / GB:.2f} GB is in use, so this upload does not fit. "
            f"{HINTS[key]}"
        )
    else:
        if used + adding <= limit:
            return
        plural, _ = LABELS[key]
        message = f"Your plan allows {limit} {plural} and {used} are in use. {HINTS[key]}"
    raise ApplicationError(ErrorCode.PLAN_LIMIT_REACHED, message, status_code=402)
