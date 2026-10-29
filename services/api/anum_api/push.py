"""Phone push notifications (docs/notifications.md).

Devices. ``POST /api/v1/me/devices`` registers the caller's phone (``android`` or
``ios`` and its Firebase Cloud Messaging token) in the current workspace;
``GET /api/v1/me/devices`` lists the caller's own phones and ``DELETE
/api/v1/me/devices/{id}`` removes one. There is never a user id in the path or the body:
rows are keyed by the caller's tenant, workspace and user and, with PostgreSQL, sit under
forced RLS on all three (migration ``0027_push_devices``). The token is encrypted with
``ANUM_SECRETS_KEY`` before it is stored; responses, logs and audit records carry only
its last four characters.

Status. ``GET /api/v1/push/status`` says whether the server can deliver at all:
``ANUM_FCM_SERVICE_ACCOUNT_JSON`` empty means push is off, and clients explain that
instead of asking for notification permission.

Delivery. Committed domain events reach ``PushDispatcher.submit`` through
``EventBusRuntime.after_commit`` (the same hand-off that feeds the outbox), in the API and
in the worker. Four kinds become pushes:

=============================================  ==============================  =====================
event                                          recipients                       preference
=============================================  ==============================  =====================
``approval.requested``                         active members who may decide    ``approval_required``
                                               it (``approval:decide``; not the
                                               creator or requester when the
                                               two-person rule applies)
``agent_run.completed``                        the task's creator               ``task_completed``
``agent_run.failed``                           the task's creator               ``run_failed``
``developer_request.ready_for_review``         the request's creator (owner)    ``approval_required``
``developer_request.failed``                   the request's creator (owner)    ``run_failed``
``commerce.alert.raised``                       active owners and members        ``commerce_alerts``
                                               (``commerce:operate``)
``tyre_alerts.raised``                         active members (``tyre:read``)    ``tyre_alerts``
=============================================  ==============================  =====================

Recipients are resolved as the application role inside the event's tenant and workspace
(RLS applies) and must still hold an active membership whose role grants the permission
to see the item; each recipient's notification preferences (``push_enabled`` and the
kind's switch) and devices are read inside that recipient's own user context. A push
carries a fixed title and sentence ("Approval needed") and ids for the deep link,
never a task title, a prompt, a tool argument or a pull request title.

Fan-out is bounded (``ANUM_PUSH_MAX_RECIPIENTS`` recipients, ``ANUM_PUSH_MAX_DEVICES_PER_USER``
devices each), at most ten messages are in flight, each is retried with backoff
(``anum_api/push_fcm.py``) and a device whose token FCM reports as unregistered is
deleted. The queue is in-process and bounded (``ANUM_PUSH_MAX_PENDING``): a push is a
reminder, not the record. The approval, the task and the developer request stay in the
API, where every client lists them, so a push lost to a restart loses nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from .audit import AuditRecord
from .authorization import ROLE_PERMISSIONS, Permission, Role
from .dependencies import _unit_of_work, repository_context, tenant_context
from .push_fcm import FcmSender, PushMessage, SendOutcome, ServiceAccount, parse_service_account
from .repository import AnumRepository
from .schemas import ApprovalStatus, DomainEvent, RiskLevel, TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings

logger = logging.getLogger("anum.push")

router = APIRouter(prefix="/api/v1", tags=["push"])

SYSTEM_ACTOR = "system:push"
REGISTRATION_PATTERN = r"^[A-Za-z0-9_:.\-]+$"
# Mirrors main.TWO_PERSON_RISK_LEVELS (a test keeps them equal; main is not imported here).
TWO_PERSON_RISK_LEVELS = frozenset({RiskLevel.HIGH})
MAX_IN_FLIGHT = 10


# --------------------------------------------------------------------------- models


class PushPlatform(StrEnum):
    ANDROID = "android"
    IOS = "ios"


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: PushPlatform
    token: str = Field(min_length=20, max_length=4096, pattern=REGISTRATION_PATTERN, repr=False)
    label: str | None = Field(default=None, max_length=80, description="Shown in the device list, e.g. 'Android phone'")
    language: Literal["en", "ar"] | None = Field(default=None, description="The language the app shows; pushes use its text")


class PushDevice(BaseModel):
    """A registered phone as clients see it: never the token, only its last four characters."""

    id: str
    platform: PushPlatform
    label: str | None = None
    token_hint: str
    language: str | None = None
    created_at: datetime
    updated_at: datetime
    last_sent_at: datetime | None = None


class StoredPushDevice(PushDevice):
    tenant_id: str
    workspace_id: str
    user_id: str
    token_ciphertext: str = Field(repr=False)
    token_sha256: str

    def view(self) -> PushDevice:
        return PushDevice(**self.model_dump(include=set(PushDevice.model_fields)))


class PushStatus(BaseModel):
    available: bool
    provider: str = "fcm"
    project_id: str | None = None
    message: str


# --------------------------------------------------------------------------- storage


class PushDeviceStore(Protocol):
    """The devices of the user in ``context`` (tenant, workspace and user)."""

    def list(self, context: TenantContext) -> list[StoredPushDevice]: ...
    def find_by_digest(self, context: TenantContext, digest: str) -> StoredPushDevice | None: ...
    def save(self, device: StoredPushDevice) -> StoredPushDevice: ...
    def delete(self, context: TenantContext, device_id: str) -> bool: ...
    def mark_sent(self, context: TenantContext, device_ids: Sequence[str], at: datetime) -> None: ...


def _mine(device: StoredPushDevice, context: TenantContext) -> bool:
    return (device.tenant_id, device.workspace_id, device.user_id) == (
        context.tenant_id,
        context.workspace_id,
        context.user_id,
    )


class InMemoryPushDeviceStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._items: dict[str, StoredPushDevice] = {}
        self._lock = RLock()

    def list(self, context: TenantContext) -> list[StoredPushDevice]:
        with self._lock:
            found = [item.model_copy() for item in self._items.values() if _mine(item, context)]
        return sorted(found, key=lambda item: (item.created_at, item.id))

    def find_by_digest(self, context: TenantContext, digest: str) -> StoredPushDevice | None:
        return next((item for item in self.list(context) if item.token_sha256 == digest), None)

    def save(self, device: StoredPushDevice) -> StoredPushDevice:
        with self._lock:
            self._items[device.id] = device.model_copy()
        return device

    def delete(self, context: TenantContext, device_id: str) -> bool:
        with self._lock:
            found = self._items.get(device_id)
            if found is None or not _mine(found, context):
                return False
            del self._items[device_id]
            return True

    def mark_sent(self, context: TenantContext, device_ids: Sequence[str], at: datetime) -> None:
        with self._lock:
            for device_id in device_ids:
                found = self._items.get(device_id)
                if found is not None and _mine(found, context):
                    self._items[device_id] = found.model_copy(update={"last_sent_at": at})

    def all_for_user(self, tenant_id: str, user_id: str) -> list[StoredPushDevice]:
        """Every workspace's devices of one user (privacy export and deletion)."""
        with self._lock:
            return [
                item.model_copy()
                for item in self._items.values()
                if item.tenant_id == tenant_id and item.user_id == user_id
            ]

    def delete_for_user(self, tenant_id: str, user_id: str) -> int:
        with self._lock:
            doomed = [key for key, item in self._items.items() if item.tenant_id == tenant_id and item.user_id == user_id]
            for key in doomed:
                del self._items[key]
            return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


push_device_store = InMemoryPushDeviceStore()


@contextmanager
def open_push_device_store(context: TenantContext) -> Iterator[PushDeviceStore]:
    """One unit of work over the devices of ``context.user_id`` (``anum.user_id`` is set)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.push_repository import SqlAlchemyPushDeviceStore

        return SqlAlchemyPushDeviceStore(session)

    with open_scoped_store(context, push_device_store, sql_store, user_scoped=True) as store:
        yield store


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- status


def configured_account() -> ServiceAccount | None:
    raw = settings.fcm_service_account_json.get_secret_value().strip() if settings.fcm_service_account_json else ""
    if not raw:
        return None
    try:
        return parse_service_account(raw)
    except ValueError:  # Settings validated it at startup; a test may have swapped it.
        return None


def push_status() -> PushStatus:
    account = configured_account()
    if account is None:
        return PushStatus(
            available=False,
            message=(
                "Phone push is off on this server: the operator has not set "
                "ANUM_FCM_SERVICE_ACCOUNT_JSON. Approvals and results still appear in the app."
            ),
        )
    return PushStatus(
        available=True,
        project_id=account.project_id,
        message="Phone push is on. Each phone must allow notifications and be registered.",
    )


# --------------------------------------------------------------------------- API


def _audit(repository: AnumRepository, context: TenantContext, action: str, device: PushDevice) -> None:
    # Platform and device id only: never the token, its digest or its hint.
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=device.id,
            outcome="success",
            correlation_id=device.id,
            created_at=utc_now(),
            metadata={"platform": device.platform.value},
        )
    )


@router.get("/push/status", response_model=PushStatus)
async def get_push_status(context: TenantContext = Depends(tenant_context)) -> PushStatus:
    """Whether this server can send phone push at all. Open to every member."""
    return push_status()


@router.get("/me/devices", response_model=list[PushDevice])
async def list_my_devices(context: TenantContext = Depends(tenant_context)) -> list[PushDevice]:
    """The caller's own phones registered in this workspace (newest last)."""
    with open_push_device_store(context) as store:
        return [device.view() for device in store.list(context)]


@router.post("/me/devices", response_model=PushDevice, status_code=status.HTTP_201_CREATED)
async def register_my_device(
    payload: DeviceRegistration,
    response: Response,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> PushDevice:
    """Register (or refresh) the caller's phone for push in this workspace.

    The same token again updates the existing row (200). Beyond
    ``ANUM_PUSH_MAX_DEVICES_PER_USER`` phones the least recently registered one is removed.
    """
    if configured_account() is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=push_status().message)
    from .secret_box import secret_cipher

    digest = token_digest(payload.token)
    now = utc_now()
    with open_push_device_store(context) as store:
        existing = store.find_by_digest(context, digest)
        if existing is not None:
            # Re-encrypted with the current first key, so a phone that opens the app after
            # an ANUM_SECRETS_KEY rotation carries its token over to the new key.
            saved = store.save(
                existing.model_copy(
                    update={
                        "platform": payload.platform,
                        "label": payload.label,
                        "language": payload.language or existing.language,
                        "updated_at": now,
                        "token_ciphertext": secret_cipher().encrypt(payload.token),
                    }
                )
            )
            response.status_code = status.HTTP_200_OK
            return saved.view()
        current = store.list(context)
        overflow = len(current) - settings.push_max_devices_per_user + 1
        for stale in sorted(current, key=lambda item: (item.updated_at, item.id))[: max(0, overflow)]:
            store.delete(context, stale.id)
        device = StoredPushDevice(
            id=new_id("device"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            platform=payload.platform,
            label=payload.label,
            language=payload.language,
            token_ciphertext=secret_cipher().encrypt(payload.token),
            token_sha256=digest,
            token_hint=payload.token[-4:],
            created_at=now,
            updated_at=now,
        )
        saved = store.save(device)
    view = saved.view()
    _audit(repository, context, "push.device.registered", view)
    return view


@router.delete("/me/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_my_device(
    device_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> Response:
    """Remove one of the caller's own phones; another user's device is simply not found."""
    with open_push_device_store(context) as store:
        found = next((item for item in store.list(context) if item.id == device_id), None)
        if found is None or not store.delete(context, device_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    _audit(repository, context, "push.device.removed", found.view())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- delivery


class PushKind(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    TASK_COMPLETED = "task_completed"
    RUN_FAILED = "run_failed"
    DEVELOPER_READY = "developer_ready"
    DEVELOPER_FAILED = "developer_failed"
    COMMERCE_ALERT = "commerce_alert"
    TYRE_ALERTS = "tyre_alerts"
    # Team work (docs/team-work.md#notifications): assignments, comments, reminders, follow-ups.
    TEAM_WORK = "team_work"
    AUTOMATION_NOTICE = "automation_notice"
    AUTOMATION_FAILED = "automation_failed"
    # The post-close SIMULATED trading report (docs/finance-trading-intel.md#daily-report).
    FINANCE_REPORT = "finance_report"
    # In-app calls (docs/in-app-calls.md#phone-push): a teammate is ringing, or rang.
    INCOMING_CALL = "incoming_call"
    MISSED_CALL = "missed_call"


# Which notification preference switch gates each kind.
PREFERENCE: dict[PushKind, str] = {
    PushKind.APPROVAL_REQUIRED: "approval_required",
    PushKind.TASK_COMPLETED: "task_completed",
    PushKind.RUN_FAILED: "run_failed",
    PushKind.DEVELOPER_READY: "approval_required",
    PushKind.DEVELOPER_FAILED: "run_failed",
    PushKind.COMMERCE_ALERT: "commerce_alerts",
    PushKind.TYRE_ALERTS: "tyre_alerts",
    # No switch of its own: phone push for team work follows the push channel switch.
    PushKind.TEAM_WORK: "push_enabled",
    # A notify step's push was asked for by the automation's owner; push_enabled still applies.
    PushKind.AUTOMATION_NOTICE: "push_enabled",
    PushKind.AUTOMATION_FAILED: "automation_failed",
    # No switch of its own: follows the push channel switch, like team work.
    PushKind.FINANCE_REPORT: "push_enabled",
    PushKind.INCOMING_CALL: "push_enabled",
    PushKind.MISSED_CALL: "push_enabled",
}

# Fixed text: a push never carries the task, the prompt, the tool call or a PR title.
TEXT: dict[PushKind, tuple[str, str]] = {
    PushKind.APPROVAL_REQUIRED: ("Approval needed", "An action is waiting for your decision."),
    PushKind.TASK_COMPLETED: ("Task finished", "A task you started has finished."),
    PushKind.RUN_FAILED: ("Task failed", "A task you started could not finish."),
    PushKind.DEVELOPER_READY: ("Change ready for review", "A developer request has a pull request ready to merge."),
    PushKind.DEVELOPER_FAILED: ("Developer request stopped", "A developer request failed or its checks did not pass."),
    PushKind.COMMERCE_ALERT: ("Store alert", "A sales channel needs your attention: low stock, a late order, returns or a price."),
    PushKind.TYRE_ALERTS: ("Tyre alerts", "New tyre alerts need attention."),
    PushKind.TEAM_WORK: ("Team work", "Something in your team work needs you. Open ANUM to see it."),
    PushKind.AUTOMATION_NOTICE: ("Automation update", "An automation has a message for you. Open ANUM to read it."),
    PushKind.AUTOMATION_FAILED: ("Automation failed", "An automation could not finish."),
    PushKind.FINANCE_REPORT: ("Daily trading report", "Your simulated trading report for today is ready. No real order was placed."),
    PushKind.INCOMING_CALL: ("Incoming call", "A teammate is calling you. Open ANUM to answer."),
    PushKind.MISSED_CALL: ("Missed call", "A teammate tried to call you in ANUM."),
}

# The same fixed text in Arabic, for phones whose app shows Arabic (``push_devices.language``).
TEXT_AR: dict[PushKind, tuple[str, str]] = {
    PushKind.APPROVAL_REQUIRED: ("مطلوب موافقة", "إجراء ينتظر قرارك."),
    PushKind.TASK_COMPLETED: ("انتهت المهمة", "انتهت مهمة بدأتها."),
    PushKind.RUN_FAILED: ("فشلت المهمة", "تعذر إكمال مهمة بدأتها."),
    PushKind.DEVELOPER_READY: ("تغيير جاهز للمراجعة", "طلب تطوير لديه طلب سحب جاهز للدمج."),
    PushKind.DEVELOPER_FAILED: ("توقف طلب التطوير", "فشل طلب تطوير أو لم تنجح فحوصه."),
    PushKind.COMMERCE_ALERT: ("تنبيه المتجر", "قناة بيع تحتاج إلى انتباهك: مخزون منخفض أو طلب متأخر أو مرتجعات أو سعر."),
    PushKind.TYRE_ALERTS: ("تنبيهات الإطارات", "تنبيهات إطارات جديدة تحتاج إلى انتباه."),
    PushKind.TEAM_WORK: ("عمل الفريق", "شيء في عمل فريقك يحتاج إليك. افتح ANUM لتراه."),
    PushKind.AUTOMATION_NOTICE: ("تحديث أتمتة", "لدى أتمتة رسالة لك. افتح ANUM لقراءتها."),
    PushKind.AUTOMATION_FAILED: ("فشلت الأتمتة", "تعذر إكمال أتمتة."),
    PushKind.FINANCE_REPORT: ("تقرير التداول اليومي", "تقرير التداول المحاكى لليوم جاهز. لم يُنفذ أي أمر حقيقي."),
    PushKind.INCOMING_CALL: ("مكالمة واردة", "زميل يتصل بك. افتح ANUM للرد."),
    PushKind.MISSED_CALL: ("مكالمة فائتة", "حاول زميل الاتصال بك في ANUM."),
}


def push_text(kind: PushKind, language: str | None) -> tuple[str, str]:
    """The fixed title and body of a kind in the phone's language (English when unknown)."""
    return (TEXT_AR if language == "ar" else TEXT)[kind]

# Notification kinds that stay in the app (the inbox row and the web bell): their
# ``notification.created`` event never becomes a phone push. A visitor checked in for a host
# (docs/ANUM_MINE_HQ_RECEPTION.md#host-notification) is one: owner rule, no autonomous email,
# SMS or push.
IN_APP_ONLY_KINDS = frozenset({"visitor_arrived"})

EVENT_KINDS: dict[str, PushKind] = {
    "approval.requested": PushKind.APPROVAL_REQUIRED,
    "agent_run.completed": PushKind.TASK_COMPLETED,
    "agent_run.failed": PushKind.RUN_FAILED,
    "developer_request.ready_for_review": PushKind.DEVELOPER_READY,
    "developer_request.failed": PushKind.DEVELOPER_FAILED,
    "commerce.alert.raised": PushKind.COMMERCE_ALERT,
    "tyre_alerts.raised": PushKind.TYRE_ALERTS,
    "notification.created": PushKind.TEAM_WORK,
    "automation.notification": PushKind.AUTOMATION_NOTICE,
    "automation_run.failed": PushKind.AUTOMATION_FAILED,
    "app_call.ringing": PushKind.INCOMING_CALL,
}


@dataclass(frozen=True)
class PushIntent:
    kind: PushKind
    tenant_id: str
    workspace_id: str
    event_id: str
    task_id: str | None = None
    approval_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None
    alert_id: str | None = None
    work_item_id: str | None = None
    # Team work names its one recipient in the event (the in-app notification's owner).
    recipient: str | None = None
    automation_run_id: str | None = None
    workflow_id: str | None = None
    app_call_id: str | None = None
    # Automation pushes: the users the event names (notify_user_ids).
    named_users: tuple[str, ...] = ()

    def data(self) -> dict[str, str]:
        """Deep-link ids only (FCM data values must be strings)."""
        values = {
            "anum_kind": self.kind.value,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "developer_request_id": self.request_id,
            "commerce_alert_id": self.alert_id,
            "work_item_id": self.work_item_id,
            "automation_run_id": self.automation_run_id,
            "workflow_id": self.workflow_id,
            "app_call_id": self.app_call_id,
        }
        return {key: value for key, value in values.items() if value}

    def collapse_id(self) -> str:
        if self.kind == PushKind.TYRE_ALERTS:
            return f"tyre_alerts:{self.workspace_id}"
        if self.app_call_id:
            return f"app_call:{self.app_call_id}:{self.kind.value}"
        return self.approval_id or self.request_id or self.alert_id or self.work_item_id or self.automation_run_id or self.task_id or self.event_id


def intent_for(event: DomainEvent) -> PushIntent | None:
    kind = EVENT_KINDS.get(event.type)
    if kind is None or not event.workspace_id:
        return None
    task_id = event.payload.get("task_id")
    task_id = str(task_id) if isinstance(task_id, str) and task_id else None
    common: dict[str, Any] = {
        "kind": kind,
        "tenant_id": event.tenant_id,
        "workspace_id": event.workspace_id,
        "event_id": event.id,
    }
    if kind in {PushKind.AUTOMATION_NOTICE, PushKind.AUTOMATION_FAILED}:
        if kind == PushKind.AUTOMATION_NOTICE and not event.payload.get("push"):
            return None  # in-app only
        users = event.payload.get("notify_user_ids")
        named = tuple(str(user) for user in users if isinstance(user, str)) if isinstance(users, list) else ()
        if not named:
            return None
        workflow = event.payload.get("workflow_id")
        return PushIntent(
            **common,
            automation_run_id=event.subject,
            workflow_id=str(workflow) if isinstance(workflow, str) else None,
            named_users=named,
        )
    if kind == PushKind.APPROVAL_REQUIRED:
        return PushIntent(**common, approval_id=event.subject, task_id=task_id)
    if kind == PushKind.TYRE_ALERTS:
        return PushIntent(**common)
    if kind == PushKind.INCOMING_CALL:
        recipient = event.payload.get("recipient")
        call_id = event.payload.get("call_id")
        if not isinstance(recipient, str) or not recipient or not isinstance(call_id, str) or not call_id:
            return None
        return PushIntent(**common, recipient=recipient, app_call_id=call_id)
    if kind == PushKind.TEAM_WORK:
        recipient = event.payload.get("recipient")
        item = event.payload.get("work_item_id")
        if not isinstance(recipient, str) or not recipient:
            return None
        if event.payload.get("kind") in IN_APP_ONLY_KINDS:
            return None  # the inbox row only, never a phone push
        if event.payload.get("kind") == "missed_call":
            return PushIntent(**{**common, "kind": PushKind.MISSED_CALL}, recipient=recipient)
        if event.payload.get("kind") == "finance_daily_report":
            # Not team work: its own kind and text, opening Finance on the phone.
            return PushIntent(**{**common, "kind": PushKind.FINANCE_REPORT}, recipient=recipient)
        return PushIntent(**common, recipient=recipient, work_item_id=item if isinstance(item, str) and item else None)
    if kind in {PushKind.TASK_COMPLETED, PushKind.RUN_FAILED}:
        if task_id is None:
            return None
        return PushIntent(**common, run_id=event.subject, task_id=task_id)
    if kind == PushKind.COMMERCE_ALERT:
        return PushIntent(**common, alert_id=event.subject)
    return PushIntent(**common, request_id=event.subject)


def _system_context(intent: PushIntent, user_id: str = SYSTEM_ACTOR) -> TenantContext:
    return TenantContext(tenant_id=intent.tenant_id, workspace_id=intent.workspace_id, user_id=user_id, roles=[])


def _allowed(role: str, permission: Permission) -> bool:
    try:
        return permission in ROLE_PERMISSIONS.get(Role(role), frozenset())
    except ValueError:
        return False


def resolve_recipients(intent: PushIntent, *, limit: int | None = None) -> list[str]:
    """Who may see (and, for approvals, decide) the item, read inside its tenant and workspace."""
    limit = limit or settings.push_max_recipients
    context = _system_context(intent)
    with _unit_of_work(context, require_membership=False) as repository:
        members = {member.user_id: member.role for member in repository.list_memberships(context) if member.active}
        recipients: list[str] = []
        if intent.kind == PushKind.APPROVAL_REQUIRED:
            approval = repository.get_approval(intent.approval_id or "", context)
            if approval is None or approval.status != ApprovalStatus.PENDING:
                return []
            excluded: set[str] = set()
            if approval.risk_level in TWO_PERSON_RISK_LEVELS and repository.get_approval_policy(context).two_person_rule:
                task = repository.get_task(approval.task_id, context)
                excluded = {user for user in (approval.requested_by, task.created_by if task else None) if user}
            recipients = [
                user for user, role in members.items()
                if user not in excluded and _allowed(role, Permission.APPROVAL_DECIDE)
            ]
        elif intent.kind in {PushKind.INCOMING_CALL, PushKind.MISSED_CALL}:
            if intent.recipient and _allowed(members.get(intent.recipient, ""), Permission.CALL_USE):
                recipients = [intent.recipient]
        elif intent.kind == PushKind.TEAM_WORK:
            if intent.recipient and _allowed(members.get(intent.recipient, ""), Permission.WORK_READ):
                recipients = [intent.recipient]
        elif intent.kind == PushKind.FINANCE_REPORT:
            if intent.recipient and _allowed(members.get(intent.recipient, ""), Permission.FINANCE_READ):
                recipients = [intent.recipient]
        elif intent.kind in {PushKind.AUTOMATION_NOTICE, PushKind.AUTOMATION_FAILED}:
            recipients = [
                user for user in intent.named_users if _allowed(members.get(user, ""), Permission.AUTOMATION_READ)
            ]
        elif intent.kind in {PushKind.TASK_COMPLETED, PushKind.RUN_FAILED}:
            task = repository.get_task(intent.task_id or "", context)
            creator = task.created_by if task is not None else None
            if creator and _allowed(members.get(creator, ""), Permission.TASK_READ):
                recipients = [creator]
        elif intent.kind == PushKind.COMMERCE_ALERT:
            # Those who can act on it (docs/commerce.md#alerts); viewers see it in the app.
            recipients = [user for user, role in members.items() if _allowed(role, Permission.COMMERCE_OPERATE)]
        elif intent.kind == PushKind.TYRE_ALERTS:
            recipients = [user for user, role in members.items() if _allowed(role, Permission.TYRE_READ)]
        else:
            from .developer import open_developer_store

            with open_developer_store(context) as store:
                request = store.get(intent.request_id or "", context)
            creator = request.created_by if request is not None else None
            if creator and _allowed(members.get(creator, ""), Permission.DEVELOPER_MANAGE):
                recipients = [creator]
    return sorted(set(recipients))[:limit]


@dataclass(frozen=True)
class _Target:
    user_id: str
    device_id: str
    token: str
    digest: str
    language: str | None = None


def _targets_for(intent: PushIntent, user_id: str) -> list[_Target]:
    """The user's devices, if their preferences in this workspace want this kind of push."""
    from .onboarding import NotificationPreferences, open_notification_preference_store
    from .secret_box import SecretDecryptionError, secret_cipher

    context = _system_context(intent, user_id)
    with open_notification_preference_store(context) as preferences_store:
        preferences = preferences_store.get(context) or NotificationPreferences()
    if not preferences.push_enabled or not getattr(preferences, PREFERENCE[intent.kind]):
        return []
    with open_push_device_store(context) as store:
        devices = store.list(context)[-settings.push_max_devices_per_user :]
    targets: list[_Target] = []
    for device in devices:
        try:
            token = secret_cipher().decrypt(device.token_ciphertext)
        except SecretDecryptionError:
            logger.warning("push_token_unreadable device=%s", device.id)
            continue
        targets.append(_Target(user_id, device.id, token, device.token_sha256, device.language))
    return targets


def _settle(intent: PushIntent, user_id: str, sent: list[str], invalid: list[str]) -> None:
    context = _system_context(intent, user_id)
    with open_push_device_store(context) as store:
        for device_id in invalid:
            store.delete(context, device_id)
        if sent:
            store.mark_sent(context, sent, utc_now())


@dataclass
class DeliveryReport:
    recipients: int = 0
    sent: int = 0
    removed: int = 0
    failed: int = 0


class PushSender(Protocol):
    async def send(self, message: PushMessage) -> SendOutcome: ...
    async def close(self) -> None: ...


def build_sender() -> PushSender | None:
    account = configured_account()
    if account is None:
        return None
    return FcmSender(account, timeout=settings.push_timeout_seconds, max_attempts=settings.push_max_attempts)


class PushDispatcher:
    """Turns committed events into pushes, off the request path, in one background task.

    ``submit`` is thread-safe (events are committed in request threads); it does nothing
    while push is off. ``run_pending`` delivers everything queued (tests call it directly).
    """

    def __init__(self, sender_factory: Callable[[], PushSender | None] = build_sender) -> None:
        self._sender_factory = sender_factory
        self._sender: PushSender | None = None
        self._queue: deque[PushIntent] = deque()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.dropped = 0
        self.delivered = DeliveryReport()

    @property
    def enabled(self) -> bool:
        return configured_account() is not None

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def submit(self, events: Iterable[DomainEvent]) -> None:
        if not self.enabled:
            return
        intents = [intent for intent in (intent_for(event) for event in events) if intent is not None]
        if not intents:
            return
        with self._lock:
            self._queue.extend(intents)
            while len(self._queue) > settings.push_max_pending:
                self._queue.popleft()
                self.dropped += 1
                logger.warning("push_queue_full; dropped the oldest notification")
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # pragma: no cover - loop shutting down
                pass

    def _take(self) -> PushIntent | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def _ensure_sender(self) -> PushSender | None:
        if self._sender is None:
            self._sender = self._sender_factory()
        return self._sender

    async def run_pending(self) -> DeliveryReport:
        report = DeliveryReport()
        while (intent := self._take()) is not None:
            try:
                one = await self.deliver(intent)
            except Exception:  # one event must never stop the others
                logger.exception("push_delivery_failed kind=%s event=%s", intent.kind.value, intent.event_id)
                continue
            for name in ("recipients", "sent", "removed", "failed"):
                setattr(report, name, getattr(report, name) + getattr(one, name))
                setattr(self.delivered, name, getattr(self.delivered, name) + getattr(one, name))
        return report

    async def deliver(self, intent: PushIntent) -> DeliveryReport:
        sender = self._ensure_sender()
        report = DeliveryReport()
        if sender is None:
            return report
        recipients = await asyncio.to_thread(resolve_recipients, intent)
        report.recipients = len(recipients)
        seen: set[str] = set()
        targets: list[_Target] = []
        for user_id in recipients:
            for target in await asyncio.to_thread(_targets_for, intent, user_id):
                if target.digest not in seen:  # one phone shared by two members buzzes once
                    seen.add(target.digest)
                    targets.append(target)
        gate = asyncio.Semaphore(MAX_IN_FLIGHT)

        async def one(target: _Target) -> SendOutcome:
            title, body = push_text(intent.kind, target.language)
            async with gate:
                return await sender.send(
                    PushMessage(token=target.token, title=title, body=body, data=intent.data(), collapse_id=intent.collapse_id())
                )

        outcomes = await asyncio.gather(*(one(target) for target in targets))
        by_user: dict[str, tuple[list[str], list[str]]] = {}
        for target, outcome in zip(targets, outcomes, strict=True):
            sent, invalid = by_user.setdefault(target.user_id, ([], []))
            if outcome == SendOutcome.SENT:
                sent.append(target.device_id)
                report.sent += 1
            elif outcome == SendOutcome.UNREGISTERED:
                invalid.append(target.device_id)
                report.removed += 1
            else:
                report.failed += 1
        for user_id, (sent, invalid) in by_user.items():
            await asyncio.to_thread(_settle, intent, user_id, sent, invalid)
        logger.info(
            "push_delivered kind=%s recipients=%s sent=%s removed=%s failed=%s",
            intent.kind.value,
            report.recipients,
            report.sent,
            report.removed,
            report.failed,
            extra={"anum_push": {"event": "delivered", "kind": intent.kind.value, "event_id": intent.event_id}},
        )
        return report

    def start(self) -> None:
        if self._task is not None or not self.enabled:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="anum-push-dispatcher")
        if self.pending:
            self._wake.set()

    async def stop(self, *, timeout: float = 5.0) -> None:
        task, self._task = self._task, None
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        if task is not None:
            try:
                # Let the event being delivered finish; queued ones are dropped (see module doc).
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.pending:
            logger.warning("push dispatcher stopped with %s notifications not sent", self.pending)
        if self._sender is not None:
            await self._sender.close()
            self._sender = None
        self._loop = None
        self._wake = None

    async def _run(self) -> None:
        wake = self._wake
        if wake is None:
            raise RuntimeError("Push dispatcher started without a wake event")
        while not self._stopping:
            await wake.wait()
            wake.clear()
            if self._stopping:
                break
            await self.run_pending()


push_dispatcher = PushDispatcher()
