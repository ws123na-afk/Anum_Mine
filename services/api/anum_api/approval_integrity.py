"""Approval integrity: what an approval shows, what it binds to, and when it lapses.

See ``docs/approvals-and-risk.md``. An approval carries the exact tool name and the
tool arguments (with secret-looking values redacted for display), and a SHA-256 hash
of the canonical JSON of the full, unredacted tool call together with the task, run
and proposal step it belongs to. The person deciding sends back the hash they were
shown; the runtime recomputes it from the run's checkpoint before executing and
refuses to run anything that no longer matches.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .agent_tools import ToolCall
from .schemas import Approval, ApprovalStatus

REDACTED = "[REDACTED]"
PAYLOAD_HASH_PATTERN = r"^[0-9a-f]{64}$"

_SECRET_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "cookie",
    "session",
)
# Values that look like credentials whatever their key is called.
_SECRET_VALUE = re.compile(
    r"^(?:bearer\s+\S+|basic\s+\S+|sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|xox[abposr]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*)$",
    re.IGNORECASE,
)


def canonical_tool_call(call: ToolCall, *, task_id: str, run_id: str, step_id: str | None) -> bytes:
    """The bytes an approval is bound to: sorted keys, no whitespace, UTF-8."""
    document = {
        "arguments": call.arguments,
        "run_id": run_id,
        "step_id": step_id,
        "task_id": task_id,
        "tool": call.name,
    }
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str
    ).encode("utf-8")


def payload_hash(call: ToolCall, *, task_id: str, run_id: str, step_id: str | None) -> str:
    """SHA-256 (hex) of :func:`canonical_tool_call`."""
    return hashlib.sha256(canonical_tool_call(call, task_id=task_id, run_id=run_id, step_id=step_id)).hexdigest()


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_secret_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and _SECRET_VALUE.match(value.strip()):
        return REDACTED
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def display_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments as shown to the approver: everything, secret-looking values redacted."""
    return _redact(dict(arguments))


def is_expired(approval: Approval, now: datetime) -> bool:
    """A pending approval whose ``expires_at`` has passed."""
    return (
        approval.status == ApprovalStatus.PENDING
        and approval.expires_at is not None
        and now >= approval.expires_at
    )


def as_viewed(approval: Approval, now: datetime) -> Approval:
    """What clients see: a lapsed pending approval reads as ``expired`` before it is stored."""
    if is_expired(approval, now):
        return approval.model_copy(update={"status": ApprovalStatus.EXPIRED})
    return approval
