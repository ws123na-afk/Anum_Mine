"""Sales demo workspace (docs/demo-workspace.md).

An owner creates or resets a workspace called "Demo" in their own tenant, filled with
clearly labelled sample data across the modules: tasks, approvals, email threads (stored
locally, never sent), calendar events, business accounts, leads, opportunities and
proposals, marketing drafts, files and a knowledge collection.

* ``GET /api/v1/demo`` tells an owner whether the Demo workspace exists and how many
  sample records it holds.
* ``POST /api/v1/demo/reset`` creates the Demo workspace when it is missing (the caller
  becomes its owner), removes the sample records it holds and fills it again.

Rules:

* The Demo workspace id is derived from the tenant id (``ws_demo_<hash>``), so it is the
  same on every call and never collides with another tenant's. The ``ws_demo_`` prefix is
  reserved: ``POST /api/v1/organization/workspaces`` refuses it.
* Every sample record is tagged in ``demo_records`` (migration ``0076_demo_records``)
  inside the Demo workspace's RLS scope. Reset removes exactly the tagged records through
  each module's own service functions; records somebody added by hand stay. Tasks and
  approvals have no delete in ANUM, so reset archives demo tasks and expires their
  pending approvals instead.
* Nothing is sent from a Demo workspace: :func:`send_refusal` is checked by the tool
  registry before every tool call and by the routes that send without a tool (Email
  Center delivery and forwarding, Brevo test sends, WhatsApp test messages, browser and AI
  phone calls, marketing publishing).
* Seeding goes through the public store and service functions, never raw SQL, and every
  unit of work runs in the Demo workspace's tenant context.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .audit import AuditRecord
from .authorization import Permission
from .dependencies import _unit_of_work, has_permission, repository_context, tenant_context
from .events import CanonicalEventName, create_event
from .repository import AnumRepository
from .schemas import (
    Approval,
    ApprovalStatus,
    RiskLevel,
    Task,
    TaskPriority,
    TaskStatus,
    TenantContext,
    Workspace,
    WorkspaceMembership,
    new_id,
    utc_now,
)
from .scoped_store import open_scoped_store

router = APIRouter(prefix="/api/v1", tags=["demo"])

DEMO_PREFIX = "ws_demo_"
DEMO_NAME = "Demo"
LABEL = "Demo: "
OWNER_ONLY = (
    "Only a workspace owner can create or reset the demo workspace. "
    "Ask an owner of this workspace to do it."
)
SEND_REFUSED = (
    "Not sent: this is the demo workspace, where nothing leaves ANUM. "
    "Switch to a real workspace to send email, messages, campaigns or calls."
)
# Tool kinds that reach people or systems outside ANUM (autonomy.ActionKind values).
_OUTWARD_KINDS = {"send", "reply", "publish", "payment", "price_change", "stock_change", "merge_code", "other"}
# Kinds that never reach anyone outside ANUM, whatever integration the tool reads from.
_ALLOWED_KINDS = {"read", "draft"}
_RECIPIENT_KEYS = ("to", "cc", "bcc", "recipient", "recipients", "phone", "phone_number", "to_phone")


def demo_workspace_id(tenant_id: str) -> str:
    """The tenant's Demo workspace id: stable, and unique to the tenant."""
    return DEMO_PREFIX + hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]


def is_demo_workspace(tenant_id: str, workspace_id: str) -> bool:
    return workspace_id == demo_workspace_id(tenant_id)


def is_demo(context: TenantContext) -> bool:
    return is_demo_workspace(context.tenant_id, context.workspace_id)


def is_reserved_workspace_id(workspace_id: str) -> bool:
    return workspace_id.startswith(DEMO_PREFIX)


def send_refusal(context: TenantContext, tool_name: str | None = None, definition: Any = None,
                 arguments: dict[str, Any] | None = None) -> str | None:
    """Why this call may not run in a Demo workspace, or None.

    Without a tool name it answers for a direct send (a route that sends without a tool).
    For a tool the decision is the action kind (``autonomy.kind_of``: the declared kind, else
    a cautious guess from the name), never the mere fact that it talks to an integration:

    * a tool that delivers to people (``delivery_channel``) is refused;
    * reads and drafts run, even against an integration (web search, Brevo lists, reports and
      drafts), so the demo stays explorable;
    * outward kinds are refused: sends, replies, publishing, payments, price and stock
      changes, merges and unknown effects (``other``);
    * any other change (create, update, archive, delete, pause) runs unless its arguments
      name recipients.
    """
    if not is_demo(context):
        return None
    if tool_name is None:
        return SEND_REFUSED
    from .autonomy import kind_of

    if getattr(definition, "delivery_channel", None):
        return SEND_REFUSED
    kind = kind_of(tool_name, definition).value
    if kind in _ALLOWED_KINDS:
        return None
    if kind in _OUTWARD_KINDS:
        return SEND_REFUSED
    if arguments and any(arguments.get(key) for key in _RECIPIENT_KEYS):
        return SEND_REFUSED
    return None


def refuse_send_in_demo(context: TenantContext) -> None:
    """403 for a route that would send something outside ANUM from a Demo workspace."""
    if is_demo(context):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SEND_REFUSED)


# ------------------------------------------------------------------------------ the demo tag


class DemoStore(Protocol):
    def tag(self, kind: str, record_id: str) -> None: ...
    def tagged(self) -> list[tuple[str, str, datetime]]: ...
    def untag(self, kind: str, record_id: str) -> None: ...


class InMemoryDemoStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: dict[tuple[str, str], list[tuple[str, str, datetime]]] = {}

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def scoped(self, context: TenantContext) -> _ScopedDemoStore:
        return _ScopedDemoStore(self, (context.tenant_id, context.workspace_id))


class _ScopedDemoStore:
    def __init__(self, root: InMemoryDemoStore, key: tuple[str, str]) -> None:
        self.root, self.key = root, key

    def tag(self, kind: str, record_id: str) -> None:
        with self.root._lock:
            rows = self.root._rows.setdefault(self.key, [])
            if not any(k == kind and r == record_id for k, r, _ in rows):
                rows.append((kind, record_id, utc_now()))

    def tagged(self) -> list[tuple[str, str, datetime]]:
        with self.root._lock:
            return list(self.root._rows.get(self.key, []))

    def untag(self, kind: str, record_id: str) -> None:
        with self.root._lock:
            rows = self.root._rows.get(self.key, [])
            self.root._rows[self.key] = [row for row in rows if not (row[0] == kind and row[1] == record_id)]


demo_store = InMemoryDemoStore()


@contextmanager
def open_demo_store(context: TenantContext) -> Iterator[DemoStore]:
    """One unit of work on the demo tags of the context's workspace (RLS in PostgreSQL)."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.demo_repository import SqlAlchemyDemoStore

        return SqlAlchemyDemoStore(session, context)

    with open_scoped_store(context, demo_store.scoped(context), sql_store) as store:
        yield store


# ------------------------------------------------------------------------------ API


class DemoStatus(BaseModel):
    workspace_id: str
    name: str
    exists: bool
    records: int
    counts: dict[str, int]
    filled_at: datetime | None
    sends_blocked: bool = True


def _require_owner(context: TenantContext) -> None:
    roles = {role.strip().lower() for role in context.roles}
    if "owner" not in roles or not has_permission(context, Permission.WORKSPACE_CREATE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=OWNER_ONLY)


def _demo_context(context: TenantContext) -> TenantContext:
    return context.model_copy(update={"workspace_id": demo_workspace_id(context.tenant_id), "roles": ["owner"]})


def _status(context: TenantContext, repository: AnumRepository) -> DemoStatus:
    demo = _demo_context(context)
    # Read in the Demo workspace's own RLS scope: the request's session sees only its workspace.
    del repository
    with _unit_of_work(demo, require_membership=False) as unit:
        workspace = unit.get_workspace(demo.workspace_id, demo)
    if workspace is None:
        return DemoStatus(workspace_id=demo.workspace_id, name=DEMO_NAME, exists=False, records=0, counts={}, filled_at=None)
    with open_demo_store(demo) as store:
        rows = store.tagged()
    counts: dict[str, int] = {}
    for kind, _, _ in rows:
        counts[kind] = counts.get(kind, 0) + 1
    return DemoStatus(
        workspace_id=demo.workspace_id,
        name=workspace.name,
        exists=True,
        records=len(rows),
        counts=counts,
        filled_at=max((created for _, _, created in rows), default=None),
    )


@router.get("/demo", response_model=DemoStatus)
def demo_status(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DemoStatus:
    """Whether the tenant's Demo workspace exists and what it holds. Owners only."""
    _require_owner(context)
    return _status(context, repository)


@router.post("/demo/reset", response_model=DemoStatus)
def reset_demo(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> DemoStatus:
    """Create the Demo workspace if needed, remove its sample records and fill it again."""
    _require_owner(context)
    demo = _demo_context(context)
    _ensure_workspace(context, demo, repository)
    removed = wipe_demo_records(demo)
    created = seed_demo_records(demo)
    with _unit_of_work(demo, require_membership=False) as unit:
        unit.record_audit(
            AuditRecord(
                id=new_id("audit"),
                tenant_id=demo.tenant_id,
                workspace_id=demo.workspace_id,
                actor=context.user_id,
                action="demo.reset",
                target=demo.workspace_id,
                outcome="success",
                correlation_id=new_id("correlation"),
                created_at=utc_now(),
                metadata={"removed": removed, "created": created, "from_workspace": context.workspace_id},
            )
        )
    return _status(context, repository)


def _ensure_workspace(context: TenantContext, demo: TenantContext, repository: AnumRepository) -> None:
    """The Demo workspace and the caller's owner membership in it, in its own RLS scope."""
    if repository.get_tenant(context.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Finish onboarding your organization first")
    now = utc_now()
    with _unit_of_work(demo, require_membership=False) as unit:
        if unit.get_workspace(demo.workspace_id, demo) is None:
            unit.create_workspace(
                Workspace(id=demo.workspace_id, tenant_id=demo.tenant_id, name=DEMO_NAME, created_at=now, updated_at=now)
            )
            unit.record_event(
                create_event(
                    CanonicalEventName.WORKSPACE_CREATED,
                    demo,
                    demo.workspace_id,
                    {"workspace_id": demo.workspace_id, "name": DEMO_NAME, "created_from": context.workspace_id},
                    created_at=now,
                ).event
            )
        membership = unit.get_membership(demo)
        if membership is None or not membership.active or membership.role != "owner":
            unit.save_membership(
                WorkspaceMembership(
                    tenant_id=demo.tenant_id,
                    workspace_id=demo.workspace_id,
                    user_id=demo.user_id,
                    role="owner",
                    active=True,
                    created_at=membership.created_at if membership else now,
                    updated_at=now,
                )
            )


# ------------------------------------------------------------------------------ wipe


def wipe_demo_records(demo: TenantContext) -> int:
    """Remove every tagged record of the Demo workspace; untagged records stay."""
    from .business.store import open_business_store
    from .calendar_api import open_calendar_store
    from .email_inbox import open_email_store
    from .files import remove_workspace_file
    from .knowledge_base import open_knowledge_store
    from .marketing.store import open_marketing_store

    with open_demo_store(demo) as store:
        rows = [(kind, record_id) for kind, record_id, _ in store.tagged()]
    by_kind: dict[str, list[str]] = {}
    for kind, record_id in rows:
        by_kind.setdefault(kind, []).append(record_id)
    done: list[tuple[str, str]] = []

    def each(kind: str) -> list[str]:
        ids = by_kind.get(kind, [])
        done.extend((kind, record_id) for record_id in ids)
        return ids

    with open_knowledge_store(demo) as kb:
        for record_id in each("kb_collections"):
            kb.delete_collection(demo, record_id)
    for record_id in each("files"):
        remove_workspace_file(demo, record_id)
    with open_calendar_store(demo) as calendar:
        for record_id in each("calendar_events"):
            calendar.delete(record_id)
    with open_email_store(demo) as email:
        for record_id in each("email_accounts"):
            email.delete_account(record_id)
    with open_marketing_store(demo) as marketing:
        for record_id in each("marketing_posts"):
            marketing.delete_post(demo, record_id)
        for record_id in each("marketing_campaigns"):
            marketing.delete_campaign(demo, record_id)
        # Social accounts have no delete; the demo account stays, never connected.
        each("marketing_accounts")
    with open_business_store(demo) as business:
        for kind in ("proposal_versions", "proposals", "opportunities", "leads", "contacts", "accounts"):
            for record_id in each(f"business_{kind}"):
                business.delete(kind, record_id)
    now = utc_now()
    with _unit_of_work(demo, require_membership=False) as unit:
        for record_id in each("approvals"):
            approval = unit.get_approval_for_update(record_id, demo)
            if approval is not None and approval.status == ApprovalStatus.PENDING:
                approval.status = ApprovalStatus.EXPIRED
                approval.decided_at = now
                approval.decision_reason = "Demo data reset"
                unit.save_approval(approval)
        for record_id in each("tasks"):
            task = unit.get_task_for_update(record_id, demo)
            if task is not None and task.archived_at is None:
                if task.status == TaskStatus.WAITING_APPROVAL:
                    task.status = TaskStatus.CANCELLED
                task.archived_at = task.updated_at = now
                task.archived_by = demo.user_id
                unit.save_task(task)
    with open_demo_store(demo) as store:
        for kind, record_id in done:
            if kind != "marketing_accounts":
                store.untag(kind, record_id)
    return len([row for row in done if row[0] != "marketing_accounts"])


# ------------------------------------------------------------------------------ seed


class _Tagger:
    """Write-ahead demo tags: each record's tag is committed before the record is written.

    A seed that fails part way therefore never leaves an untagged sample record behind: the
    next reset removes whatever was written (a tag whose record never got written is simply
    skipped) and seeds again from the start, so nothing is duplicated.
    """

    def __init__(self, demo: TenantContext) -> None:
        self.demo = demo
        self.count = 0

    def id(self, kind: str, prefix: str) -> str:
        record_id = new_id(prefix)
        self.fixed(kind, record_id)
        return record_id

    def fixed(self, kind: str, record_id: str) -> str:
        with open_demo_store(self.demo) as store:
            store.tag(kind, record_id)
        if kind != "marketing_accounts":
            self.count += 1
        return record_id


def seed_demo_records(demo: TenantContext) -> int:
    """Fill the Demo workspace with labelled sample records, tagging each one first."""
    tag = _Tagger(demo)
    now = utc_now().replace(microsecond=0)
    for step in (_seed_tasks, _seed_email, _seed_calendar, _seed_business, _seed_marketing, _seed_files_and_knowledge):
        step(demo, now, tag)
    return tag.count


_TASKS = (
    ("Prepare the Q4 fleet renewal summary", "Summarise the renewal status of the Al Noor Logistics fleet contract for the sales meeting.", TaskPriority.HIGH, 2),
    ("Draft a follow-up to Riyadh Cold Chain", "Draft a short follow-up after Tuesday's demo; do not send it.", TaskPriority.MEDIUM, 1),
    ("Collect tyre inspection photos", "List the inspection photos still missing for the October audit.", TaskPriority.LOW, 5),
    ("Review the pricing sheet", "Check the 2027 pricing sheet against the approved discounts.", TaskPriority.MEDIUM, 3),
)


def _seed_tasks(demo: TenantContext, now: datetime, tag: _Tagger) -> None:
    with _unit_of_work(demo, require_membership=False) as unit:
        for title, prompt, priority, due_days in _TASKS:
            task = Task(
                id=tag.id("tasks", "task"),
                title=LABEL + title,
                prompt=prompt,
                status=TaskStatus.CREATED,
                tenant_id=demo.tenant_id,
                workspace_id=demo.workspace_id,
                created_at=now,
                updated_at=now,
                created_by=demo.user_id,
                priority=priority,
                due_at=now + timedelta(days=due_days),
            )
            unit.create_task(task)
            unit.record_event(
                create_event(CanonicalEventName.TASK_CREATED, demo, task.id, {"title": task.title},
                             correlation_id=task.id, created_at=now).event
            )
        # Two decisions waiting in the approval queue, proposed by a sample teammate so the
        # owner may decide them under the two-person rule. Sending stays blocked anyway.
        for action, reason, arguments in (
            ("email.send", "Send the renewal offer to Al Noor Logistics",
             {"to": "procurement@alnoor.example.com", "subject": "Demo: Fleet renewal offer"}),
            ("marketing.publish", "Publish the winter safety post",
             {"platform": "linkedin", "title": "Demo: Winter tyre safety"}),
        ):
            task = Task(
                id=tag.id("tasks", "task"),
                title=LABEL + reason,
                prompt=reason,
                status=TaskStatus.WAITING_APPROVAL,
                tenant_id=demo.tenant_id,
                workspace_id=demo.workspace_id,
                created_at=now,
                updated_at=now,
                created_by="demo_teammate",
                priority=TaskPriority.HIGH,
            )
            unit.create_task(task)
            approval = Approval(
                id=tag.id("approvals", "approval"),
                task_id=task.id,
                action=action,
                risk_level=RiskLevel.HIGH,
                status=ApprovalStatus.PENDING,
                reason=LABEL + reason,
                created_at=now,
                arguments=arguments,
                payload_hash=secrets.token_hex(32),
                expires_at=now + timedelta(days=7),
                requested_by="demo_teammate",
            )
            unit.save_approval(approval)


_THREADS = (
    ("Fleet renewal: updated quote", "Salma Al Harbi", "salma@alnoor.example.com",
     "Thanks for the demo. Could you send the updated quote for 120 trucks before Thursday?"),
    ("Pilot results from the Jeddah depot", "Omar Haddad", "omar@coldchain.example.com",
     "The tyre pressure pilot cut roadside stops by 18 percent. Happy to share the full report."),
    ("Invoice question for September", "Accounts team", "accounts@supplier.example.com",
     "We noticed two lines on invoice 2026-09-114 that look duplicated. Can you confirm?"),
)


def _seed_email(demo: TenantContext, now: datetime, tag: _Tagger) -> None:
    from .email_inbox import AccountStatus, EmailAccount, EmailMessage, EmailProvider, open_email_store

    account = EmailAccount(
        id=tag.id("email_accounts", "mailacct"),
        tenant_id=demo.tenant_id,
        workspace_id=demo.workspace_id,
        provider=EmailProvider.GMAIL,
        address="sales@demo.example.com",
        display_name="Demo mailbox (sample data, never sends)",
        connected_by=demo.user_id,
        # Never connected: background sync skips accounts that need sign-in, and the demo
        # workspace refuses every send anyway.
        status=AccountStatus.REAUTH_REQUIRED,
        created_at=now,
        updated_at=now,
    )
    messages = []
    for index, (subject, name, address, body) in enumerate(_THREADS):
        thread = new_id("thread")
        received = now - timedelta(hours=3 * index + 1)
        messages.append(
            EmailMessage(
                id=new_id("mail"),
                tenant_id=demo.tenant_id,
                workspace_id=demo.workspace_id,
                account_id=account.id,
                provider_message_id=f"demo-{thread}",
                thread_id=thread,
                subject=LABEL + subject,
                from_address=address,
                from_name=name,
                to=[account.address],
                snippet=body,
                labels=["INBOX"],
                received_at=received,
                is_read=index > 0,
                created_at=now,
                updated_at=now,
            )
        )
    with open_email_store(demo) as store:
        store.add_account(account)
        store.upsert_messages(messages)


def _seed_calendar(demo: TenantContext, now: datetime, tag: _Tagger) -> None:
    from .calendar_api import Attendee, CalendarEvent, EventKind, open_calendar_store

    start = now.replace(minute=0, second=0) + timedelta(days=1)
    for offset, hours, title, kind, attendee in (
        (0, 1, "Fleet renewal call with Al Noor Logistics", EventKind.CALL, ("Salma Al Harbi", "Fleet Manager")),
        (1, 2, "Product demo for Riyadh Cold Chain", EventKind.MEETING, ("Omar Haddad", "Operations Lead")),
        (3, 1, "Pricing review", EventKind.REVIEW, None),
    ):
        starts = start + timedelta(days=offset, hours=offset * 2)
        event = CalendarEvent(
            id=tag.id("calendar_events", "event"),
            tenant_id=demo.tenant_id,
            workspace_id=demo.workspace_id,
            title=LABEL + title,
            starts_at=starts,
            ends_at=starts + timedelta(hours=hours),
            kind=kind,
            description="Sample event in the demo workspace. No invitations are sent.",
            attendees=[Attendee(id=new_id("attendee"), name=attendee[0], role=attendee[1])] if attendee else [],
            source="demo",
            created_by=demo.user_id,
            created_at=now,
            updated_at=now,
        )
        with open_calendar_store(demo) as store:
            store.add(event)


def _seed_business(demo: TenantContext, now: datetime, tag: _Tagger) -> None:
    from .business.models import (
        Account,
        Contact,
        Lead,
        LeadState,
        LineItem,
        Opportunity,
        Proposal,
        ProposalContent,
        ProposalVersion,
        Stage,
    )
    from .business.service import content_hash, name_key
    from .business.store import open_business_store

    base = {"tenant_id": demo.tenant_id, "workspace_id": demo.workspace_id, "created_at": now, "updated_at": now}
    people = {"owner_id": demo.user_id, "created_by": demo.user_id}
    with open_business_store(demo) as store:
        for name, industry, city, contact, lead_title, state, stage, value in (
            ("Al Noor Logistics", "Logistics", "Riyadh", ("Salma Al Harbi", "Fleet Manager", "salma@alnoor.example.com"),
             "Fleet renewal, 120 trucks", LeadState.QUALIFIED, Stage.QUOTE, Decimal("480000")),
            ("Riyadh Cold Chain", "Food distribution", "Riyadh", ("Omar Haddad", "Operations Lead", "omar@coldchain.example.com"),
             "Tyre pressure monitoring pilot", LeadState.RESEARCHED, Stage.DEMO, Decimal("95000")),
            ("Gulf Coast Transport", "Transport", "Dammam", ("Huda Saleh", "Procurement", "huda@gulfcoast.example.com"),
             "Maintenance plan for 40 buses", LeadState.DISCOVERED, Stage.DISCOVERY, None),
        ):
            account_name = LABEL + name
            account = store.add("accounts", Account(id=tag.id("business_accounts", "acct"), name=account_name, name_key=name_key(account_name),
                                                    industry=industry, country="Saudi Arabia", city=city,
                                                    notes="Sample account in the demo workspace.", **base, **people))
            person = store.add("contacts", Contact(id=tag.id("business_contacts", "contact"), account_id=account.id, name=contact[0],
                                                   role_title=contact[1], email=contact[2], created_by=demo.user_id, **base))
            lead = store.add("leads", Lead(id=tag.id("business_leads", "lead"), account_id=account.id, contact_id=person.id,
                                           title=LABEL + lead_title, state=state, next_step="Prepare the next meeting",
                                           **base, **people))
            opportunity = store.add("opportunities", Opportunity(id=tag.id("business_opportunities", "opp"), lead_id=lead.id, account_id=account.id,
                                                                 name=LABEL + lead_title, stage=stage, value_amount=value,
                                                                 assumption_note="Sample value for the demo.", **base, **people))
            if stage != Stage.QUOTE:
                continue
            content = ProposalContent(
                summary="Tyre supply and maintenance for 120 trucks over 24 months.",
                scope="Supply, fitting and monthly inspection at the Riyadh and Jeddah depots.",
                deliverables=["Tyre supply", "Monthly inspections", "Quarterly fleet report"],
                line_items=[LineItem(description="Tyre supply and fitting", quantity=Decimal("120"), unit_price=Decimal("3200")),
                            LineItem(description="Monthly inspection service", quantity=Decimal("24"), unit_price=Decimal("4000"))],
                payment_terms="30 days from invoice.",
                valid_until=date.fromordinal(now.date().toordinal() + 30),
            )
            proposal = store.add("proposals", Proposal(id=tag.id("business_proposals", "prop"), opportunity_id=opportunity.id, account_id=account.id,
                                                       title=LABEL + "Fleet renewal proposal", created_by=demo.user_id, **base))
            version = store.add("proposal_versions", ProposalVersion(
                **content.model_dump(), id=tag.id("business_proposal_versions", "propv"), tenant_id=demo.tenant_id, workspace_id=demo.workspace_id,
                proposal_id=proposal.id, version=1, content_hash=content_hash(content), created_by=demo.user_id, created_at=now))


def _seed_marketing(demo: TenantContext, now: datetime, tag: _Tagger) -> None:
    from .marketing.models import AccountStatus, Campaign, Language, Platform, Post, Provider, SocialAccount
    from .marketing.store import open_marketing_store

    account_id = "demo_social_" + demo.workspace_id[len(DEMO_PREFIX):]
    with open_marketing_store(demo) as store:
        tag.fixed("marketing_accounts", account_id)
        if store.get_account(demo, account_id) is None:
            store.add_account(SocialAccount(
                id=account_id, tenant_id=demo.tenant_id, workspace_id=demo.workspace_id, platform=Platform.LINKEDIN,
                provider=Provider.LINKEDIN, external_id=account_id, display_name="Demo company page (not connected)",
                status=AccountStatus.NEEDS_REAUTH, connected_by=demo.user_id, created_at=now, updated_at=now))
        campaign = store.add_campaign(Campaign(
            id=tag.id("marketing_campaigns", "mkc"), tenant_id=demo.tenant_id, workspace_id=demo.workspace_id, name=LABEL + "Winter fleet safety",
            objective="Awareness", product="Tyre inspection service",
            brief="Remind fleet managers to check tread depth and pressure before the winter rains.",
            platforms=[Platform.LINKEDIN], created_by=demo.user_id, created_at=now, updated_at=now))
        group = new_id("mkg")
        for language, title, text in (
            (Language.EN, "Winter tyre safety", "Rain is coming. Check tread depth and pressure on every truck this week."),
            (Language.AR, "سلامة الإطارات في الشتاء", "موسم الأمطار قادم. افحص عمق المداس وضغط الإطارات في كل شاحنة هذا الأسبوع."),
        ):
            post = store.add_post(Post(
                id=tag.id("marketing_posts", "mkp"), tenant_id=demo.tenant_id, workspace_id=demo.workspace_id, campaign_id=campaign.id,
                group_id=group, account_id=account_id, platform=Platform.LINKEDIN, language=language,
                title=LABEL + title, text=text, hashtags=["fleetsafety"], created_by=demo.user_id,
                created_at=now, updated_at=now))


_FILES = (
    ("Demo - Fleet renewal brief.md", "# Demo: Fleet renewal brief\n\nAl Noor Logistics runs 120 trucks between Riyadh and Jeddah. "
     "Their contract renews in January. Priorities: fewer roadside stops, one monthly report, 30-day payment terms.\n"),
    ("Demo - Pricing sheet 2027.csv", "item,unit,price_sar\nTyre supply and fitting,tyre,3200\nMonthly inspection,month,4000\n"
     "Quarterly fleet report,quarter,1500\n"),
    ("Demo - Objection handling.md", "# Demo: Objection handling\n\n1. Price: compare the cost per kilometre, not the tyre price.\n"
     "2. Switching effort: we fit at your depot overnight.\n3. Proof: share the Jeddah pilot results.\n"),
)


def _seed_files_and_knowledge(demo: TenantContext, now: datetime, tag: _Tagger) -> None:
    from .files import FileRecord, file_store, open_file_metadata_store, workspace_object_key
    from .knowledge_base import Collection, ItemKind, _new_item, open_knowledge_store
    from .settings import settings

    records = []
    for name, body in _FILES:
        content = body.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        file_id = tag.id("files", "file")
        key = workspace_object_key(demo.tenant_id, demo.workspace_id, "files", file_id, digest)
        record = FileRecord(
            id=file_id, tenant_id=demo.tenant_id, workspace_id=demo.workspace_id, name=name,
            content_type="text/csv" if name.endswith(".csv") else "text/markdown", size_bytes=len(content),
            sha256=digest, storage_key=key, created_by=demo.user_id, created_at=utc_now())
        file_store.storage.put(key, content, record.content_type)
        try:
            with open_file_metadata_store(demo) as store:
                store.add(record, settings.files_workspace_quota_bytes)
        except BaseException:
            file_store.storage.delete(key)
            raise
        records.append(record)
    collection = Collection(
        id=tag.id("kb_collections", "kbc"), tenant_id=demo.tenant_id, workspace_id=demo.workspace_id,
        name=LABEL + "Sales playbook",
        description="Sample collection in the demo workspace: the brief, the pricing sheet and objection handling.",
        created_by=demo.user_id, created_at=now, updated_at=now)
    with open_knowledge_store(demo) as store:
        store.save_collection(collection)
        for record in records:
            store.save_item(_new_item(demo, collection, ItemKind.FILE, record.id, record.name, ["demo", "sales"]))
