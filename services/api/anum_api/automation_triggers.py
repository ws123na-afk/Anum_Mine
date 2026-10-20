"""Event and webhook triggers of automations: models, matching and signatures.

An *event trigger* starts its automation when a domain event of one type is committed in
the workspace and every condition matches the event's payload. A *webhook trigger* starts
it when an outside system POSTs a request signed with the trigger's secret to
``/api/v1/automation/hooks/{tenant}/{workspace}/{trigger}`` (docs/automation.md#triggers).

This module holds no storage; the engines in ``automation.py`` and
``db/automation_repository.py`` keep triggers next to workflows, schedules and runs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from .schemas import DomainEvent


class TriggerKind(StrEnum):
    EVENT = "event"
    WEBHOOK = "webhook"


class TriggerOutcome(StrEnum):
    """What the last delivery to a trigger did (``last_outcome``)."""

    FIRED = "fired"
    RATE_LIMITED = "rate_limited"
    WORKFLOW_PAUSED = "workflow_paused"
    SKIPPED_OVERLAP = "skipped_overlap"
    OWNER_INACTIVE = "owner_inactive"
    WORKSPACE_ARCHIVED = "workspace_archived"
    EMERGENCY_STOP = "emergency_stop"
    LOOP_SKIPPED = "loop_skipped"
    DISABLED = "disabled"


class TriggerEvent(BaseModel):
    """One event type automations can start on, and the payload fields conditions can test."""

    type: str
    label: str
    description: str
    fields: list[str]


# The events a trigger may name. Automation events are not listed, so an automation can
# never start on its own run's events.
TRIGGER_EVENTS: dict[str, TriggerEvent] = {
    event.type: event
    for event in (
        TriggerEvent(
            type="agent_run.completed",
            label="Task completed",
            description="An agent task finished.",
            fields=["task_id", "approval_id"],
        ),
        TriggerEvent(
            type="agent_run.failed",
            label="Task failed",
            description="An agent task stopped with an error.",
            fields=["task_id", "approval_id"],
        ),
        TriggerEvent(
            type="approval.approved",
            label="Approval approved",
            description="Someone approved an action.",
            fields=["task_id", "reason"],
        ),
        TriggerEvent(
            type="approval.rejected",
            label="Approval rejected",
            description="Someone rejected an action.",
            fields=["task_id", "reason"],
        ),
        TriggerEvent(
            type="approval.expired",
            label="Approval expired",
            description="An approval lapsed without a decision.",
            fields=["task_id"],
        ),
        TriggerEvent(
            type="email.received",
            label="Email received",
            description="A new message arrived in a connected mailbox (workspace mailboxes; private ones carry ids only).",
            fields=["account_id", "message_id", "from", "subject", "category", "priority", "private"],
        ),
        TriggerEvent(
            type="whatsapp.message_received",
            label="WhatsApp message received",
            description="A customer sent a WhatsApp message to the connected number.",
            fields=["conversation_id", "message_id", "from", "contact_name", "kind", "category"],
        ),
        TriggerEvent(
            type="file.uploaded",
            label="File uploaded",
            description="Someone uploaded a file to the workspace.",
            fields=["file_id", "name", "content_type", "size_bytes", "uploaded_by"],
        ),
    )
}

# Events that move a step waiting on a task (``task.create``, ``email.send``) forward.
TASK_PROGRESS_EVENTS = frozenset(
    {
        "agent_run.completed",
        "agent_run.failed",
        "agent_run.waiting_approval",
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "approval.expired",
        "task.cancelled",
    }
)

MAX_CONDITIONS = 10
# Stored with a trigger but never returned by the API.
PRIVATE_TRIGGER_FIELDS = ("secret_ciphertext",)
PAYLOAD_MAX_BYTES = 16_000
WEBHOOK_BODY_MAX_BYTES = 64_000
SIGNATURE_HEADER = "X-Anum-Signature"
DELIVERY_HEADER = "X-Anum-Delivery"
HOOK_KEY_PREFIX = "whsec_"
_FIELD = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+){0,5}$")
_SIGNATURE = re.compile(r"^t=(\d{1,12}),v1=([0-9a-f]{64})$")
_DELIVERY = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")


class TriggerCondition(BaseModel):
    """``field`` is a dotted path in the payload (``from``, ``body.order.status``)."""

    field: str = Field(min_length=1, max_length=120)
    operator: Literal["equals", "not_equals", "contains", "starts_with", "exists"] = "equals"
    value: str | None = Field(default=None, max_length=300)

    @field_validator("field")
    @classmethod
    def path(cls, value: str) -> str:
        value = value.strip()
        if not _FIELD.match(value):
            raise ValueError(
                f"Condition field {value!r} is not a payload path. Use letters, digits, _ and dots, for example from or body.status."
            )
        return value

    @model_validator(mode="after")
    def needs_value(self) -> TriggerCondition:
        if self.operator != "exists" and (self.value is None or not self.value.strip()):
            raise ValueError(f'Condition on "{self.field}" needs a value to compare with.')
        return self


def _check_event_type(kind: TriggerKind, event_type: str | None) -> str | None:
    if kind == TriggerKind.WEBHOOK:
        return None
    if not event_type or event_type not in TRIGGER_EVENTS:
        raise ValueError(f"Choose the event that starts the automation: one of {', '.join(TRIGGER_EVENTS)}.")
    return event_type


class TriggerCreate(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    kind: TriggerKind
    event_type: str | None = Field(default=None, max_length=160)
    conditions: list[TriggerCondition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    enabled: bool = True
    # Runs this trigger may start per minute; more are refused (429) or skipped.
    rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    # Webhooks: how old a signed timestamp may be (seconds), so a captured request cannot be replayed later.
    replay_window_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("name")
    @classmethod
    def named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the trigger a name.")
        return value.strip()

    @model_validator(mode="after")
    def event_for_kind(self) -> TriggerCreate:
        self.event_type = _check_event_type(self.kind, self.event_type)
        return self


class TriggerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    event_type: str | None = Field(default=None, max_length=160)
    conditions: list[TriggerCondition] | None = Field(default=None, max_length=MAX_CONDITIONS)
    enabled: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)
    replay_window_seconds: int | None = Field(default=None, ge=30, le=3600)


class AutomationTrigger(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    workflow_id: str
    name: str
    kind: TriggerKind
    event_type: str | None = None
    conditions: list[TriggerCondition] = Field(default_factory=list)
    enabled: bool = True
    rate_limit_per_minute: int = 30
    replay_window_seconds: int = 300
    # Webhooks: the secret's last four characters; the secret itself is shown once.
    secret_hint: str | None = None
    secret_rotated_at: datetime | None = None
    last_fired_at: datetime | None = None
    last_outcome: str | None = None
    fire_count: int = 0
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    # Event triggers: events at or after this time are still to be checked (catch-up).
    cursor_at: datetime | None = None
    # Stored only, never returned (``AutomationTrigger.public``).
    secret_ciphertext: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def webhook_path(self) -> str | None:
        if self.kind != TriggerKind.WEBHOOK:
            return None
        return f"/api/v1/automation/hooks/{self.tenant_id}/{self.workspace_id}/{self.id}"

    def public(self) -> AutomationTrigger:
        return self.model_copy(update=dict.fromkeys(PRIVATE_TRIGGER_FIELDS))


class TriggerWithSecret(BaseModel):
    """Create and rotate answers: the only time the webhook secret is shown."""

    trigger: AutomationTrigger
    secret: str | None = None


class WebhookAccepted(BaseModel):
    run_id: str
    status: str
    duplicate: bool = False


# -- matching ---------------------------------------------------------------------------------


def payload_value(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def conditions_match(conditions: list[TriggerCondition], payload: dict[str, Any]) -> bool:
    """Every condition holds (case-insensitive text comparison)."""
    for condition in conditions:
        value = payload_value(payload, condition.field)
        if condition.operator == "exists":
            if value is None or value == "":
                return False
            continue
        if value is None:
            if condition.operator == "not_equals":
                continue
            return False
        actual, expected = _as_text(value).casefold(), (condition.value or "").strip().casefold()
        if condition.operator == "equals" and actual != expected:
            return False
        if condition.operator == "not_equals" and actual == expected:
            return False
        if condition.operator == "contains" and expected not in actual:
            return False
        if condition.operator == "starts_with" and not actual.startswith(expected):
            return False
    return True


def bounded(payload: dict[str, Any], limit: int = PAYLOAD_MAX_BYTES) -> dict[str, Any]:
    """The payload if its JSON fits ``limit`` bytes, else a note that it was too large."""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text.encode()) <= limit:
        return json.loads(text)
    return {"truncated": True, "note": f"The payload was larger than {limit} bytes and was not kept."}


def event_payload(event: DomainEvent) -> dict[str, Any]:
    """What a run started by ``event`` sees as ``{{trigger.*}}``."""
    return bounded(
        {
            **event.payload,
            "event_id": event.id,
            "event_type": event.type,
            "event_subject": event.subject,
            "occurred_at": event.created_at.isoformat(),
        }
    )


def event_task_id(event: DomainEvent) -> str | None:
    """The agent task an event is about, if any."""
    task_id = event.payload.get("task_id")
    if isinstance(task_id, str) and task_id:
        return task_id
    if event.type.startswith("task.") and event.subject:
        return event.subject
    return None


def event_fire_key(trigger_id: str, event_id: str) -> str:
    return f"event:{trigger_id}:{event_id}"


def webhook_fire_key(trigger_id: str, delivery_id: str) -> str:
    return f"webhook:{trigger_id}:{delivery_id}"


# -- webhook signatures ----------------------------------------------------------------------


def new_secret() -> str:
    return HOOK_KEY_PREFIX + secrets.token_urlsafe(32)


def secret_hint(secret: str) -> str:
    return secret[-4:]


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The signature header value for ``body`` sent at ``timestamp`` (Unix seconds)."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class SignatureError(ValueError):
    """The request is not signed with the trigger's secret, or is outside the replay window."""


def verify(secret: str, header: str | None, body: bytes, *, now: int, window_seconds: int) -> str:
    """Check the signature and its age; return the signature digest (the default delivery id)."""
    match = _SIGNATURE.match((header or "").strip())
    if match is None:
        raise SignatureError(
            f"Sign the request: send {SIGNATURE_HEADER}: t=<unix seconds>,v1=<hex HMAC-SHA256 of '<t>.<body>' with the trigger's secret>."
        )
    timestamp, digest = int(match.group(1)), match.group(2)
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise SignatureError("The signature does not match this trigger's secret. Check the secret (it may have been rotated).")
    if abs(now - timestamp) > window_seconds:
        raise SignatureError(
            f"The signed timestamp is more than {window_seconds} seconds from the server's clock, so the request was refused as a possible replay."
        )
    return digest


def delivery_id(header: str | None, digest: str) -> str:
    """The sender's delivery id (``X-Anum-Delivery``) or, without one, the signature."""
    value = (header or "").strip()
    if value:
        if not _DELIVERY.match(value):
            raise SignatureError(f"{DELIVERY_HEADER} may hold up to 120 letters, digits and . _ : - characters.")
        return value
    return digest[:64]


# -- inbound events -----------------------------------------------------------------------


def record_inbound_events(context: Any, items: list[tuple[str, str, dict[str, Any]]]) -> None:
    """Record ``(event type, subject, payload)`` events right after inbound data was stored.

    Used by the mailbox sync, the WhatsApp webhook and file uploads, whose stores commit in
    their own transactions. Best effort: a failure is logged and never fails the caller
    (the stored message or file is kept; only automations miss it).
    """
    if not items:
        return
    import logging

    from .durable_runs import tenant_unit_of_work
    from .events import CanonicalEventName, create_event

    try:
        with tenant_unit_of_work(context) as repository:
            for event_type, subject, payload in items:
                repository.record_event(
                    create_event(CanonicalEventName(event_type), context, subject, payload, correlation_id=subject).event
                )
    except Exception:
        logging.getLogger(__name__).exception("inbound_events_not_recorded count=%d", len(items))
