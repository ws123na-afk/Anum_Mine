"""Autonomy policy: when ANUM acts on its own, and how it tells you (docs/autonomy.md).

ANUM's tool policy (``agent_tools.ToolPolicy``, platform rules plus organization
governance) decides whether a tool call is blocked, needs approval or may run. This
module adds a second, workspace-level layer on top of that decision, evaluated by the
runtime outside the model:

* **Levels** per tool category (``email``, ``whatsapp``, ``commerce``, ``developer``,
  ``files``, ``tasks``, ``general``): ``ask_always``, ``act_and_notify`` (do it, then
  notify with an undo window when the action is reversible) and ``act_silently`` (low
  risk only). The category level is a ceiling over ANUM's recommended default for the
  kind of action (reading and drafting run silently, archiving and pausing act and
  notify, a reply to a known recipient with an approved template acts and notifies,
  everything else asks).
* **Protected actions always ask**, whatever the level or any rule: money, prices,
  stock, deleting, merging code, first contact with a new recipient, bulk sends, and
  anything above the workspace thresholds (amount, recipients, price change %).
* **Guardrails** set by organization administrators are floors a workspace cannot go
  below: a hard spending limit and blocked recipients/domains (blocked even with an
  approval), category ceilings, the most a workspace may let run on its own (amount,
  recipients, price change %), daily action caps and quiet hours.
* **Rules** for exact patterns (a tool, optionally with a template): an owner can let
  "order-status replies on WhatsApp" act and notify, and anyone who runs tasks can
  tighten a pattern to "always ask". ANUM proposes a rule after the same pattern has
  been approved several times ("ask once, remember"); it never raises autonomy silently.
* **Activity**: every action ANUM took on its own is recorded (with an undo token when
  the tool reported how to reverse it) and announced with an ``autonomy.action_taken``
  event; ``POST /autonomy/actions/{id}/undo`` runs the tool's compensating action.

Autonomy can lower a platform ``high``-risk approval only for patterns the policy
allows, never one an organization approval rule or policy pack requires, never with the
workspace's two-person rule or medium-risk approval switch in force, and it can never
unblock anything.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable
from datetime import datetime, time, timedelta
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import RiskLevel, TenantContext, new_id, utc_now

if TYPE_CHECKING:
    from .agent_tools import ToolCall, ToolDefinition, ToolPolicyDecision


class ToolCategory(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    COMMERCE = "commerce"
    DEVELOPER = "developer"
    FILES = "files"
    TASKS = "tasks"
    GENERAL = "general"


class AutonomyLevel(StrEnum):
    ASK_ALWAYS = "ask_always"
    ACT_AND_NOTIFY = "act_and_notify"
    ACT_SILENTLY = "act_silently"


LEVEL_RANK = {AutonomyLevel.ASK_ALWAYS: 0, AutonomyLevel.ACT_AND_NOTIFY: 1, AutonomyLevel.ACT_SILENTLY: 2}


def lowest(*levels: AutonomyLevel | None) -> AutonomyLevel:
    present = [level for level in levels if level is not None]
    return min(present, key=LEVEL_RANK.__getitem__) if present else AutonomyLevel.ASK_ALWAYS


class ActionKind(StrEnum):
    READ = "read"
    DRAFT = "draft"
    CREATE = "create"
    REPLY = "reply"
    SEND = "send"
    ARCHIVE = "archive"
    PAUSE = "pause"
    UPDATE = "update"
    DELETE = "delete"
    PRICE_CHANGE = "price_change"
    STOCK_CHANGE = "stock_change"
    PAYMENT = "payment"
    MERGE_CODE = "merge_code"
    OTHER = "other"


# Always ask, whatever the level, rule or approval history.
PROTECTED_KINDS = frozenset(
    {ActionKind.DELETE, ActionKind.PRICE_CHANGE, ActionKind.STOCK_CHANGE, ActionKind.PAYMENT, ActionKind.MERGE_CODE}
)
MESSAGE_KINDS = frozenset({ActionKind.REPLY, ActionKind.SEND})
# Actions without side effects: not counted against daily caps, not held by quiet hours.
NO_EFFECT_KINDS = frozenset({ActionKind.READ, ActionKind.DRAFT})

# ANUM's recommendation for each kind before workspace levels and rules.
RECOMMENDED: dict[ActionKind, AutonomyLevel] = {
    ActionKind.READ: AutonomyLevel.ACT_SILENTLY,
    ActionKind.DRAFT: AutonomyLevel.ACT_SILENTLY,
    ActionKind.CREATE: AutonomyLevel.ACT_AND_NOTIFY,
    ActionKind.ARCHIVE: AutonomyLevel.ACT_AND_NOTIFY,
    ActionKind.PAUSE: AutonomyLevel.ACT_AND_NOTIFY,
    # Replies and sends act and notify only to known recipients with an approved template.
    ActionKind.REPLY: AutonomyLevel.ACT_AND_NOTIFY,
    ActionKind.SEND: AutonomyLevel.ACT_AND_NOTIFY,
}

DEFAULT_RECIPIENTS_THRESHOLD = 5
DEFAULT_DAILY_CAP = 200
DEFAULT_PROPOSAL_AFTER = 3
MAX_LIST_ITEMS = 500
MAX_ACTION_SUMMARY = 500

_CATEGORY_PREFIXES: tuple[tuple[tuple[str, ...], ToolCategory], ...] = (
    (("email", "gmail", "outlook", "zoho", "mail"), ToolCategory.EMAIL),
    (("whatsapp", "wa"), ToolCategory.WHATSAPP),
    (("commerce", "amazon", "noon", "shop", "store", "listing", "listings", "orders"), ToolCategory.COMMERCE),
    (("developer", "github", "git", "code"), ToolCategory.DEVELOPER),
    (("files", "file", "drive"), ToolCategory.FILES),
    (("tasks", "task", "todo"), ToolCategory.TASKS),
)
# Checked in order against the words of the tool's last name segment (``update_price`` ->
# ``update``, ``price``); a word matches a name word that starts with it (``reprice`` does
# not, ``pricing`` does). The first kind found wins, so effects are found before reads.
_KIND_WORDS: tuple[tuple[tuple[str, ...], ActionKind], ...] = (
    (("merge",), ActionKind.MERGE_CODE),
    (("delete", "remove", "purge", "destroy", "trash"), ActionKind.DELETE),
    (("refund", "pay", "charge", "transfer", "purchase", "spend", "invoice"), ActionKind.PAYMENT),
    (("price", "pricing", "reprice"), ActionKind.PRICE_CHANGE),
    (("stock", "inventory", "quantity", "restock"), ActionKind.STOCK_CHANGE),
    (("draft",), ActionKind.DRAFT),
    (("reply",), ActionKind.REPLY),
    (("send", "message", "broadcast", "post", "notify"), ActionKind.SEND),
    (("archive", "label", "snooze"), ActionKind.ARCHIVE),
    (("pause", "unlist", "deactivate"), ActionKind.PAUSE),
    (("create", "add", "schedule"), ActionKind.CREATE),
    (("update", "edit", "set", "change", "rename", "move", "mark"), ActionKind.UPDATE),
    (("read", "list", "get", "search", "fetch", "find", "summar", "analy", "lookup", "status", "view"), ActionKind.READ),
)

_RECIPIENT_KEYS = ("to", "recipient", "recipients", "cc", "bcc", "phone", "phone_number", "to_phone", "address")
_AMOUNT_KEYS = ("amount", "total", "spend", "cost", "refund_amount")
_TEMPLATE_KEYS = ("template", "template_id", "template_name")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def category_of(name: str, definition: ToolDefinition | None) -> ToolCategory:
    declared = getattr(definition, "category", None)
    if declared:
        try:
            return ToolCategory(declared)
        except ValueError:
            return ToolCategory.GENERAL
    head = name.lower().replace("-", "_").split(".", 1)[0].split("_", 1)[0]
    for prefixes, category in _CATEGORY_PREFIXES:
        if head in prefixes:
            return category
    return ToolCategory.GENERAL


def kind_of(name: str, definition: ToolDefinition | None) -> ActionKind:
    """The tool's declared ``action_kind``, else a guess from its name.

    A guessed ``read`` counts only for tools the platform rates low risk: a medium or
    high-risk tool whose name merely sounds like a read is ``other`` (always asks).
    """
    declared = getattr(definition, "action_kind", None)
    if declared:
        try:
            return ActionKind(declared)
        except ValueError:
            return ActionKind.OTHER
    words = [word for word in re.split(r"[^a-z0-9]+", name.lower().rsplit(".", 1)[-1]) if word]
    for prefixes, kind in _KIND_WORDS:
        if any(word.startswith(prefix) for word in words for prefix in prefixes):
            if kind == ActionKind.READ and getattr(definition, "risk_level", None) != RiskLevel.LOW:
                return ActionKind.OTHER
            return kind
    return ActionKind.OTHER


def normalize_recipient(value: str) -> str:
    """Lower-case e-mail addresses; phone numbers as ``+`` and digits only."""
    text = value.strip().lower()
    if "@" in text:
        return text.strip("<>")
    digits = re.sub(r"\D", "", text)
    return f"+{digits}" if digits else text


def _recipients(arguments: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in _RECIPIENT_KEYS:
        value = arguments.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict):
                item = item.get("email") or item.get("phone") or item.get("address")
            if isinstance(item, str):
                for part in item.split(",") if "@" in item else [item]:
                    if part.strip():
                        found.append(normalize_recipient(part))
    return sorted(set(found))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def recipient_hash(context: TenantContext, category: str, recipient: str) -> str:
    """What is stored for a known recipient: never the address itself."""
    material = f"{context.tenant_id}\x1f{context.workspace_id}\x1f{category}\x1f{recipient}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class ActionProfile(BaseModel):
    """What a tool call is, for autonomy: from the tool definition and its arguments."""

    tool: str
    category: ToolCategory
    kind: ActionKind
    risk_level: RiskLevel
    target: str | None = None
    recipients: list[str] = Field(default_factory=list)
    amount: float | None = None
    price_change_percent: float | None = None
    template: str | None = None
    reversible: bool = False

    @property
    def pattern_key(self) -> str:
        return pattern_key(self.tool, self.template)


def pattern_key(tool: str, template: str | None) -> str:
    return f"{tool}|template={template}" if template else tool


def profile_call(call: ToolCall, definition: ToolDefinition | None) -> ActionProfile:
    arguments = call.arguments or {}
    amount = next((_number(arguments.get(key)) for key in _AMOUNT_KEYS if _number(arguments.get(key)) is not None), None)
    change = _number(arguments.get("price_change_percent"))
    if change is None:
        old = _number(arguments.get("old_price", arguments.get("current_price")))
        new = _number(arguments.get("new_price"))
        if old and new is not None:
            change = (new - old) / old * 100
    template = next((str(arguments[key]).strip()[:120] for key in _TEMPLATE_KEYS if arguments.get(key)), None)
    return ActionProfile(
        tool=call.name,
        category=category_of(call.name, definition),
        kind=kind_of(call.name, definition),
        risk_level=definition.risk_level if definition is not None else RiskLevel.BLOCKED,
        target=getattr(definition, "target", None),
        recipients=_recipients(arguments),
        amount=amount,
        price_change_percent=change,
        template=template or None,
        reversible=bool(getattr(definition, "undo_tool", None)),
    )


class AutonomyFacts(BaseModel):
    """What the tool's own module knows about one call, for autonomy (docs/autonomy.md).

    A connector registers a facts function with its tools (``ToolRegistry.register``);
    the runtime asks it at every evaluation, in the caller's tenant context. It can only
    make a message's recipients *known* (they wrote to us first: a WhatsApp customer who
    messaged the business, the sender of the email being answered) and name the
    category's approved templates when the connector owns that list (WhatsApp's auto-send
    list). It never lifts any other check.
    """

    model_config = ConfigDict(frozen=True)

    # Normalized recipients this call answers (``normalize_recipient``).
    known_recipients: frozenset[str] = frozenset()
    # The approved templates for this call's category; ``None`` keeps the workspace list.
    approved_templates: tuple[str, ...] | None = None
    # Where an owner approves templates for this category, named when a template asks.
    templates_source: str | None = None


# -- settings ------------------------------------------------------------------------


class QuietHours(BaseModel):
    """Local time window when nothing with an effect runs on its own (it asks instead)."""

    model_config = ConfigDict(extra="forbid")

    start: str = Field(description="HH:MM, 24-hour")
    end: str = Field(description="HH:MM, 24-hour; before start means the window crosses midnight")
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not _HHMM.match(value):
            raise ValueError("use HH:MM (24-hour), for example 22:00")
        return value

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown time zone; use an IANA name such as Asia/Riyadh") from exc
        return value

    def contains(self, moment: datetime) -> bool:
        local = moment.astimezone(ZoneInfo(self.timezone)).time()
        start, end = time.fromisoformat(self.start), time.fromisoformat(self.end)
        if start == end:
            return False
        if start < end:
            return start <= local < end
        return local >= start or local < end


def _clean_list(values: Iterable[str], *, domains: bool = False) -> list[str]:
    cleaned = []
    for value in values:
        text = value.strip().lower().lstrip("@") if domains else normalize_recipient(value)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:MAX_LIST_ITEMS]


class OrganizationGuardrails(BaseModel):
    """Tenant-wide floors set by organization administrators (``organization_policy:manage``)."""

    model_config = ConfigDict(extra="forbid")

    spending_limit: float | None = Field(default=None, ge=0, description="Above this amount an action is blocked, even with approval")
    max_auto_amount: float | None = Field(default=None, ge=0, description="The highest amount threshold a workspace may set")
    max_auto_recipients: int | None = Field(default=None, ge=0, le=10_000)
    max_auto_price_change_percent: float | None = Field(default=None, ge=0, le=1000)
    category_ceilings: dict[ToolCategory, AutonomyLevel] = Field(default_factory=dict)
    daily_caps: dict[str, int] = Field(default_factory=dict, description="Category or tool name -> autonomous actions per day")
    quiet_hours: QuietHours | None = None
    blocked_recipients: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    updated_by: str | None = None
    updated_at: datetime | None = None

    @field_validator("blocked_recipients")
    @classmethod
    def _recipients(cls, value: list[str]) -> list[str]:
        return _clean_list(value)

    @field_validator("blocked_domains")
    @classmethod
    def _domains(cls, value: list[str]) -> list[str]:
        return _clean_list(value, domains=True)

    @field_validator("daily_caps")
    @classmethod
    def _caps(cls, value: dict[str, int]) -> dict[str, int]:
        return _validate_caps(value)


class WorkspaceAutonomySettings(BaseModel):
    """Per-workspace autonomy (owners, ``policy:manage``)."""

    model_config = ConfigDict(extra="forbid")

    category_levels: dict[ToolCategory, AutonomyLevel] = Field(default_factory=dict)
    amount_threshold: float = Field(default=0, ge=0, description="An action carrying a larger amount asks")
    recipients_threshold: int = Field(default=DEFAULT_RECIPIENTS_THRESHOLD, ge=0, le=10_000)
    price_change_percent_threshold: float = Field(default=0, ge=0, le=1000)
    daily_caps: dict[str, int] = Field(default_factory=dict)
    quiet_hours: QuietHours | None = None
    blocked_recipients: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    approved_templates: list[str] = Field(default_factory=list)
    proposal_after: int = Field(default=DEFAULT_PROPOSAL_AFTER, ge=2, le=50)
    updated_by: str | None = None
    updated_at: datetime | None = None

    @field_validator("blocked_recipients")
    @classmethod
    def _recipients(cls, value: list[str]) -> list[str]:
        return _clean_list(value)

    @field_validator("blocked_domains")
    @classmethod
    def _domains(cls, value: list[str]) -> list[str]:
        return _clean_list(value, domains=True)

    @field_validator("approved_templates")
    @classmethod
    def _templates(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip()[:120]
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned[:MAX_LIST_ITEMS]

    @field_validator("daily_caps")
    @classmethod
    def _caps(cls, value: dict[str, int]) -> dict[str, int]:
        return _validate_caps(value)


def _validate_caps(value: dict[str, int]) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    for key, cap in value.items():
        name = key.strip().lower()
        if not name or len(name) > 120:
            raise ValueError("daily cap keys are a category or tool name of at most 120 characters")
        if not isinstance(cap, int) or cap < 0 or cap > 100_000:
            raise ValueError("daily caps are whole numbers from 0 to 100000")
        cleaned[name] = cap
    if len(cleaned) > 100:
        raise ValueError("at most 100 daily caps")
    return cleaned


def floor_violations(settings: WorkspaceAutonomySettings, guardrails: OrganizationGuardrails) -> list[str]:
    """Why a workspace setting would go below the organization's floors (empty when fine)."""
    problems: list[str] = []
    if guardrails.max_auto_amount is not None and settings.amount_threshold > guardrails.max_auto_amount:
        problems.append(f"the amount threshold can be at most {guardrails.max_auto_amount:g}")
    if guardrails.max_auto_recipients is not None and settings.recipients_threshold > guardrails.max_auto_recipients:
        problems.append(f"the recipients threshold can be at most {guardrails.max_auto_recipients}")
    if (
        guardrails.max_auto_price_change_percent is not None
        and settings.price_change_percent_threshold > guardrails.max_auto_price_change_percent
    ):
        problems.append(f"the price change threshold can be at most {guardrails.max_auto_price_change_percent:g}%")
    for category, level in settings.category_levels.items():
        ceiling = guardrails.category_ceilings.get(category)
        if ceiling is not None and LEVEL_RANK[level] > LEVEL_RANK[ceiling]:
            problems.append(f"{category.value} can be at most {ceiling.value}")
    for key, cap in settings.daily_caps.items():
        limit = guardrails.daily_caps.get(key)
        if limit is not None and cap > limit:
            problems.append(f"the daily cap for {key} can be at most {limit}")
    return problems


class EffectiveAutonomy(BaseModel):
    """The workspace settings with the organization's floors applied."""

    category_levels: dict[ToolCategory, AutonomyLevel]
    amount_threshold: float
    recipients_threshold: int
    price_change_percent_threshold: float
    spending_limit: float | None
    daily_caps: dict[str, int]
    quiet_hours: list[QuietHours]
    blocked_recipients: list[str]
    blocked_domains: list[str]
    approved_templates: list[str]
    proposal_after: int


def effective(settings: WorkspaceAutonomySettings, guardrails: OrganizationGuardrails) -> EffectiveAutonomy:
    levels = {}
    for category in ToolCategory:
        levels[category] = lowest(
            settings.category_levels.get(category, AutonomyLevel.ACT_SILENTLY),
            guardrails.category_ceilings.get(category),
        )

    def capped(value: float, ceiling: float | None) -> float:
        return value if ceiling is None else min(value, ceiling)

    caps: dict[str, int] = {}
    for key in set(settings.daily_caps) | set(guardrails.daily_caps):
        values = [cap for cap in (settings.daily_caps.get(key), guardrails.daily_caps.get(key)) if cap is not None]
        caps[key] = min(values)
    return EffectiveAutonomy(
        category_levels=levels,
        amount_threshold=capped(settings.amount_threshold, guardrails.max_auto_amount),
        recipients_threshold=int(capped(settings.recipients_threshold, guardrails.max_auto_recipients)),
        price_change_percent_threshold=capped(
            settings.price_change_percent_threshold, guardrails.max_auto_price_change_percent
        ),
        spending_limit=guardrails.spending_limit,
        daily_caps=caps,
        quiet_hours=[window for window in (guardrails.quiet_hours, settings.quiet_hours) if window is not None],
        blocked_recipients=sorted(set(guardrails.blocked_recipients) | set(settings.blocked_recipients)),
        blocked_domains=sorted(set(guardrails.blocked_domains) | set(settings.blocked_domains)),
        approved_templates=settings.approved_templates,
        proposal_after=settings.proposal_after,
    )


# -- rules, proposals and activity ---------------------------------------------------------


class RuleSource(StrEnum):
    MANUAL = "manual"
    PROPOSAL = "proposal"
    TIGHTEN = "tighten"


class AutonomyRule(BaseModel):
    """The level for one exact pattern (a tool, optionally with a template)."""

    id: str
    tenant_id: str
    workspace_id: str
    pattern_key: str
    tool: str
    template: str | None = None
    category: ToolCategory
    action_kind: ActionKind
    level: AutonomyLevel
    source: RuleSource
    created_by: str
    created_at: datetime


class ProposalStatus(StrEnum):
    COLLECTING = "collecting"
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class AutonomyProposal(BaseModel):
    """ANUM's suggestion to raise autonomy for a pattern that was approved repeatedly."""

    id: str
    tenant_id: str
    workspace_id: str
    pattern_key: str
    tool: str
    template: str | None = None
    category: ToolCategory
    action_kind: ActionKind
    proposed_level: AutonomyLevel = AutonomyLevel.ACT_AND_NOTIFY
    approvals: int = 0
    status: ProposalStatus = ProposalStatus.COLLECTING
    last_approved_at: datetime | None = None
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None


class ActionStatus(StrEnum):
    DONE = "done"
    UNDONE = "undone"


class AutonomousAction(BaseModel):
    """One action ANUM took without asking."""

    id: str
    tenant_id: str
    workspace_id: str
    task_id: str
    run_id: str
    tool: str
    category: ToolCategory
    action_kind: ActionKind
    level: AutonomyLevel
    pattern_key: str
    template: str | None = None
    summary: str
    target: str | None = None
    status: ActionStatus = ActionStatus.DONE
    requested_by: str
    undo_tool: str | None = None
    undo_arguments: dict[str, Any] = Field(default_factory=dict)
    undo_token: str | None = None
    undo_expires_at: datetime | None = None
    undone_by: str | None = None
    undone_at: datetime | None = None
    undo_error: str | None = None
    created_at: datetime

    def undoable(self, now: datetime) -> bool:
        return (
            self.status == ActionStatus.DONE
            and self.undo_tool is not None
            and self.undo_expires_at is not None
            and now < self.undo_expires_at
        )


class ActionFilter(BaseModel):
    category: ToolCategory | None = None
    level: AutonomyLevel | None = None
    status: ActionStatus | None = None
    query: str | None = None


class AutonomyStore(Protocol):
    def get_guardrails(self, context: TenantContext) -> OrganizationGuardrails | None: ...
    def save_guardrails(self, context: TenantContext, guardrails: OrganizationGuardrails) -> OrganizationGuardrails: ...
    def get_settings(self, context: TenantContext) -> WorkspaceAutonomySettings | None: ...
    def save_settings(self, context: TenantContext, settings: WorkspaceAutonomySettings) -> WorkspaceAutonomySettings: ...
    def list_rules(self, context: TenantContext) -> list[AutonomyRule]: ...
    def get_rule(self, rule_id: str, context: TenantContext) -> AutonomyRule | None: ...
    def save_rule(self, rule: AutonomyRule) -> AutonomyRule: ...
    def delete_rule(self, rule_id: str, context: TenantContext) -> bool: ...
    def list_proposals(self, context: TenantContext, status: ProposalStatus | None = None) -> list[AutonomyProposal]: ...
    def get_proposal(self, proposal_id: str, context: TenantContext) -> AutonomyProposal | None: ...
    def find_proposal(self, key: str, context: TenantContext) -> AutonomyProposal | None: ...
    def save_proposal(self, proposal: AutonomyProposal) -> AutonomyProposal: ...
    def add_action(self, action: AutonomousAction) -> AutonomousAction: ...
    def get_action(self, action_id: str, context: TenantContext, *, for_update: bool = False) -> AutonomousAction | None: ...
    def save_action(self, action: AutonomousAction) -> AutonomousAction: ...
    def list_actions(
        self, context: TenantContext, filters: ActionFilter, *, limit: int, after: str | None = None
    ) -> list[AutonomousAction]: ...
    def count_actions_since(self, context: TenantContext, since: datetime, *, category: str | None, tool: str | None) -> int: ...
    def known_recipients(self, context: TenantContext, category: str, hashes: list[str]) -> set[str]: ...
    def remember_recipients(self, context: TenantContext, category: str, hashes: list[str], now: datetime) -> None: ...


class UnknownActionCursorError(LookupError):
    """A feed cursor that names no action in the caller's workspace."""


def _matches(action: AutonomousAction, filters: ActionFilter) -> bool:
    if filters.category is not None and action.category != filters.category:
        return False
    if filters.level is not None and action.level != filters.level:
        return False
    if filters.status is not None and action.status != filters.status:
        return False
    if filters.query:
        needle = filters.query.lower()
        haystack = " ".join(filter(None, [action.summary, action.tool, action.template or ""])).lower()
        if needle not in haystack:
            return False
    return True


class InMemoryAutonomyStore:
    """``ANUM_REPOSITORY_BACKEND=memory`` (local and tests): lost on restart."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.clear()

    def clear(self) -> None:
        self.guardrails: dict[str, OrganizationGuardrails] = {}
        self.settings: dict[tuple[str, str], WorkspaceAutonomySettings] = {}
        self.rules: dict[str, AutonomyRule] = {}
        self.proposals: dict[str, AutonomyProposal] = {}
        self.actions: dict[str, AutonomousAction] = {}
        self.recipients: dict[tuple[str, str, str], set[str]] = {}

    @staticmethod
    def _scope(item: Any, context: TenantContext) -> bool:
        return item.tenant_id == context.tenant_id and item.workspace_id == context.workspace_id

    def get_guardrails(self, context: TenantContext) -> OrganizationGuardrails | None:
        return self.guardrails.get(context.tenant_id)

    def save_guardrails(self, context: TenantContext, guardrails: OrganizationGuardrails) -> OrganizationGuardrails:
        with self._lock:
            self.guardrails[context.tenant_id] = guardrails
        return guardrails

    def get_settings(self, context: TenantContext) -> WorkspaceAutonomySettings | None:
        return self.settings.get((context.tenant_id, context.workspace_id))

    def save_settings(self, context: TenantContext, settings: WorkspaceAutonomySettings) -> WorkspaceAutonomySettings:
        with self._lock:
            self.settings[(context.tenant_id, context.workspace_id)] = settings
        return settings

    def list_rules(self, context: TenantContext) -> list[AutonomyRule]:
        return sorted((rule for rule in self.rules.values() if self._scope(rule, context)), key=lambda r: (r.created_at, r.id))

    def get_rule(self, rule_id: str, context: TenantContext) -> AutonomyRule | None:
        rule = self.rules.get(rule_id)
        return rule if rule is not None and self._scope(rule, context) else None

    def save_rule(self, rule: AutonomyRule) -> AutonomyRule:
        with self._lock:
            for existing in list(self.rules.values()):
                if (
                    existing.tenant_id == rule.tenant_id
                    and existing.workspace_id == rule.workspace_id
                    and existing.pattern_key == rule.pattern_key
                    and existing.id != rule.id
                ):
                    del self.rules[existing.id]
            self.rules[rule.id] = rule
        return rule

    def delete_rule(self, rule_id: str, context: TenantContext) -> bool:
        with self._lock:
            if self.get_rule(rule_id, context) is None:
                return False
            del self.rules[rule_id]
            return True

    def list_proposals(self, context: TenantContext, status: ProposalStatus | None = None) -> list[AutonomyProposal]:
        items = [p for p in self.proposals.values() if self._scope(p, context) and (status is None or p.status == status)]
        return sorted(items, key=lambda p: (p.created_at, p.id), reverse=True)

    def get_proposal(self, proposal_id: str, context: TenantContext) -> AutonomyProposal | None:
        proposal = self.proposals.get(proposal_id)
        return proposal if proposal is not None and self._scope(proposal, context) else None

    def find_proposal(self, key: str, context: TenantContext) -> AutonomyProposal | None:
        return next((p for p in self.proposals.values() if self._scope(p, context) and p.pattern_key == key), None)

    def save_proposal(self, proposal: AutonomyProposal) -> AutonomyProposal:
        with self._lock:
            self.proposals[proposal.id] = proposal
        return proposal

    def add_action(self, action: AutonomousAction) -> AutonomousAction:
        with self._lock:
            self.actions[action.id] = action
        return action

    def get_action(self, action_id: str, context: TenantContext, *, for_update: bool = False) -> AutonomousAction | None:
        action = self.actions.get(action_id)
        return action.model_copy(deep=True) if action is not None and self._scope(action, context) else None

    def save_action(self, action: AutonomousAction) -> AutonomousAction:
        with self._lock:
            self.actions[action.id] = action
        return action

    def list_actions(
        self, context: TenantContext, filters: ActionFilter, *, limit: int, after: str | None = None
    ) -> list[AutonomousAction]:
        items = sorted(
            (a for a in self.actions.values() if self._scope(a, context)),
            key=lambda a: (a.created_at, a.id),
            reverse=True,
        )
        if after:
            ids = [a.id for a in items]
            if after not in ids:
                raise UnknownActionCursorError(after)
            items = items[ids.index(after) + 1 :]
        return [a for a in items if _matches(a, filters)][:limit]

    def count_actions_since(self, context: TenantContext, since: datetime, *, category: str | None, tool: str | None) -> int:
        return sum(
            1
            for a in self.actions.values()
            if self._scope(a, context)
            and a.created_at >= since
            and a.action_kind not in NO_EFFECT_KINDS
            and (category is None or a.category == category)
            and (tool is None or a.tool == tool)
        )

    def known_recipients(self, context: TenantContext, category: str, hashes: list[str]) -> set[str]:
        known = self.recipients.get((context.tenant_id, context.workspace_id, category), set())
        return {value for value in hashes if value in known}

    def remember_recipients(self, context: TenantContext, category: str, hashes: list[str], now: datetime) -> None:
        with self._lock:
            self.recipients.setdefault((context.tenant_id, context.workspace_id, category), set()).update(hashes)


autonomy_memory_store = InMemoryAutonomyStore()


def store_of(repository: Any) -> AutonomyStore | None:
    """The autonomy store bound to ``repository``'s unit of work (None if it has none)."""
    getter = getattr(repository, "autonomy_store", None)
    return getter() if callable(getter) else None


# -- evaluation ----------------------------------------------------------------------------


class AutonomyMode(StrEnum):
    ASK = "ask"
    NOTIFY = "notify"
    SILENT = "silent"
    BLOCK = "block"


class Guardrail(StrEnum):
    BLOCKED_RECIPIENT = "blocked_recipient"
    SPENDING_LIMIT = "spending_limit"
    PROTECTED_KIND = "protected_kind"
    FIRST_CONTACT = "first_contact"
    BULK = "bulk"
    AMOUNT = "amount_threshold"
    PRICE_CHANGE = "price_change_threshold"
    QUIET_HOURS = "quiet_hours"
    DAILY_CAP = "daily_cap"
    RULE = "rule"
    LEVEL = "level"
    TEMPLATE = "template"
    POLICY = "policy"


class AutonomyDecision(BaseModel):
    mode: AutonomyMode
    level: AutonomyLevel
    category: ToolCategory
    kind: ActionKind
    pattern_key: str
    template: str | None = None
    recipients: int = 0
    reason: str
    guardrail: Guardrail | None = None
    rule_id: str | None = None
    # True when a repeatedly approved pattern may be proposed for more autonomy.
    proposable: bool = False

    def checkpoint(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _ask(profile: ActionProfile, reason: str, guardrail: Guardrail, *, proposable: bool = False, rule_id: str | None = None) -> AutonomyDecision:
    return AutonomyDecision(
        mode=AutonomyMode.ASK,
        level=AutonomyLevel.ASK_ALWAYS,
        category=profile.category,
        kind=profile.kind,
        pattern_key=profile.pattern_key,
        template=profile.template,
        recipients=len(profile.recipients),
        reason=reason,
        guardrail=guardrail,
        proposable=proposable,
        rule_id=rule_id,
    )


def _blocked_recipient(profile: ActionProfile, cfg: EffectiveAutonomy) -> str | None:
    blocked = set(cfg.blocked_recipients)
    for recipient in profile.recipients:
        if recipient in blocked:
            return recipient
        domain = recipient.rsplit("@", 1)[1] if "@" in recipient else None
        if domain and any(domain == d or domain.endswith("." + d) for d in cfg.blocked_domains):
            return recipient
    return None


def day_start(now: datetime, cfg: EffectiveAutonomy) -> datetime:
    """Midnight today in the first configured quiet-hours time zone (UTC otherwise)."""
    zone = ZoneInfo(cfg.quiet_hours[0].timezone) if cfg.quiet_hours else ZoneInfo("UTC")
    local = now.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def decide(
    profile: ActionProfile,
    cfg: EffectiveAutonomy,
    rules: list[AutonomyRule],
    *,
    now: datetime,
    known: set[str] | None = None,
    count_today: Any = None,
    platform_allows: bool = False,
    facts: AutonomyFacts | None = None,
) -> AutonomyDecision:
    """The autonomy decision for one call (pure; the caller supplies the data).

    ``known`` holds the normalized recipients already contacted in this category;
    ``count_today(category, tool)`` returns today's autonomous actions with an effect.
    Order: blocked recipients and the spending limit block; protected kinds, first
    contact, bulk and thresholds ask; a rule for the exact pattern sets the level (an
    ``ask_always`` rule always asks); otherwise ANUM's recommendation for the kind,
    capped by the category level; quiet hours and daily caps ask; ``act_silently``
    needs a low-risk tool. ``platform_allows`` (the tool policy lets the call run without
    approval) makes the recommendation for kinds without one follow the tool's risk:
    silent when low, notify when medium, so autonomy never asks for what ran before
    unless a level, a rule or a guardrail says so. ``facts`` (from the tool's module) adds
    recipients who wrote to us first and may name the category's approved templates; such
    a list is then the only way a template of that category acts (no rule or suggestion
    adds one beside it). A message without a template always asks, whatever rule exists.
    """
    known = set(known or set())
    if facts is not None:
        known |= set(facts.known_recipients)
    approved_templates = (
        list(facts.approved_templates) if facts is not None and facts.approved_templates is not None else cfg.approved_templates
    )
    template_hint = (
        f" {facts.templates_source}" if facts is not None and facts.templates_source else ""
    )
    blocked = _blocked_recipient(profile, cfg)
    if blocked is not None:
        return AutonomyDecision(
            mode=AutonomyMode.BLOCK,
            level=AutonomyLevel.ASK_ALWAYS,
            category=profile.category,
            kind=profile.kind,
            pattern_key=profile.pattern_key,
            template=profile.template,
            recipients=len(profile.recipients),
            reason="A recipient of this action is on the blocked list; it was not sent.",
            guardrail=Guardrail.BLOCKED_RECIPIENT,
        )
    if cfg.spending_limit is not None and profile.amount is not None and profile.amount > cfg.spending_limit:
        return AutonomyDecision(
            mode=AutonomyMode.BLOCK,
            level=AutonomyLevel.ASK_ALWAYS,
            category=profile.category,
            kind=profile.kind,
            pattern_key=profile.pattern_key,
            template=profile.template,
            recipients=len(profile.recipients),
            reason=(
                f"The amount {profile.amount:g} is above the organization's spending limit of "
                f"{cfg.spending_limit:g}; only an organization administrator can raise it."
            ),
            guardrail=Guardrail.SPENDING_LIMIT,
        )
    if profile.kind in PROTECTED_KINDS:
        return _ask(profile, f"ANUM always asks before this kind of action ({profile.kind.value.replace('_', ' ')}).", Guardrail.PROTECTED_KIND)
    # Approvals of this pattern count towards a proposal ("ask once, remember") only for
    # actions ANUM understands; messages need a template (free text written by the model
    # always asks). Per-call guardrails (first contact, bulk, thresholds) still count: a
    # rule for the pattern never lifts them.
    proposable = profile.kind != ActionKind.OTHER and (profile.kind not in MESSAGE_KINDS or profile.template is not None)
    # A connector that owns its category's approved templates (WhatsApp's auto-send list)
    # is the one place a template is allowed: no rule or suggestion adds one beside it.
    connector_templates = facts is not None and facts.approved_templates is not None
    if connector_templates and profile.kind in MESSAGE_KINDS:
        proposable = False
    if profile.kind in MESSAGE_KINDS and not profile.recipients:
        return _ask(profile, "ANUM cannot tell who this message goes to, so it asks.", Guardrail.FIRST_CONTACT)
    if profile.kind in MESSAGE_KINDS and profile.recipients and any(r not in known for r in profile.recipients):
        return _ask(profile, "First contact with a new recipient always asks.", Guardrail.FIRST_CONTACT, proposable=proposable)
    if len(profile.recipients) > cfg.recipients_threshold:
        return _ask(
            profile,
            f"Sending to {len(profile.recipients)} recipients is above the threshold of {cfg.recipients_threshold}.",
            Guardrail.BULK,
            proposable=proposable,
        )
    if profile.amount is not None and profile.amount > cfg.amount_threshold:
        return _ask(
            profile, f"The amount {profile.amount:g} is above the threshold of {cfg.amount_threshold:g}.", Guardrail.AMOUNT,
            proposable=proposable,
        )
    if profile.price_change_percent is not None and abs(profile.price_change_percent) > cfg.price_change_percent_threshold:
        return _ask(
            profile,
            f"A price change of {abs(profile.price_change_percent):.1f}% is above the threshold of {cfg.price_change_percent_threshold:g}%.",
            Guardrail.PRICE_CHANGE,
            proposable=proposable,
        )

    if profile.kind in MESSAGE_KINDS and profile.template is None:
        # Free text written for this message: no level, rule or history lets it go alone.
        return _ask(profile, "Free-text messages always ask before they are sent.", Guardrail.TEMPLATE)
    if connector_templates and profile.kind in MESSAGE_KINDS and profile.template not in approved_templates:
        return _ask(
            profile, "Messages act on their own only to known recipients with an approved template." + template_hint, Guardrail.TEMPLATE
        )

    ceiling = cfg.category_levels.get(profile.category, AutonomyLevel.ACT_SILENTLY)
    rule = next((r for r in rules if r.pattern_key == profile.pattern_key), None)
    if rule is not None:
        if rule.level == AutonomyLevel.ASK_ALWAYS:
            return _ask(profile, "You chose to always be asked before this action.", Guardrail.RULE, rule_id=rule.id)
        level = lowest(rule.level, ceiling)
        guardrail = Guardrail.RULE
    else:
        recommended = RECOMMENDED.get(profile.kind)
        if recommended is None:
            recommended = (
                (AutonomyLevel.ACT_SILENTLY if profile.risk_level == RiskLevel.LOW else AutonomyLevel.ACT_AND_NOTIFY)
                if platform_allows
                else AutonomyLevel.ASK_ALWAYS
            )
        if profile.kind in MESSAGE_KINDS and profile.template not in approved_templates:
            return _ask(
                profile,
                "Messages act on their own only to known recipients with an approved template." + template_hint,
                Guardrail.TEMPLATE,
                proposable=proposable,
            )
        level = lowest(recommended, ceiling)
        guardrail = Guardrail.LEVEL
    if level == AutonomyLevel.ASK_ALWAYS:
        reason = (
            f"This workspace asks before {profile.category.value} actions."
            if LEVEL_RANK[ceiling] == 0
            else "ANUM asks before this kind of action until you allow it."
        )
        return _ask(profile, reason, Guardrail.LEVEL, proposable=proposable, rule_id=rule.id if rule else None)

    if profile.kind not in NO_EFFECT_KINDS:
        window = next((w for w in cfg.quiet_hours if w.contains(now)), None)
        if window is not None:
            return _ask(
                profile, f"Quiet hours ({window.start} to {window.end}, {window.timezone}): nothing runs on its own.",
                Guardrail.QUIET_HOURS, proposable=proposable,
            )
        if count_today is not None:
            for key, scope in ((profile.tool.lower(), "tool"), (profile.category.value, "category")):
                cap = cfg.daily_caps.get(key, DEFAULT_DAILY_CAP if scope == "category" else None)
                if cap is None:
                    continue
                done = count_today(category=profile.category.value if scope == "category" else None, tool=profile.tool if scope == "tool" else None)
                if done >= cap:
                    return _ask(
                        profile, f"The daily cap of {cap} autonomous {key} actions is reached.", Guardrail.DAILY_CAP,
                        proposable=proposable,
                    )

    if level == AutonomyLevel.ACT_SILENTLY and profile.risk_level != RiskLevel.LOW:
        level = AutonomyLevel.ACT_AND_NOTIFY
    mode = AutonomyMode.SILENT if level == AutonomyLevel.ACT_SILENTLY else AutonomyMode.NOTIFY
    return AutonomyDecision(
        mode=mode,
        level=level,
        category=profile.category,
        kind=profile.kind,
        pattern_key=profile.pattern_key,
        template=profile.template,
        recipients=len(profile.recipients),
        reason=(
            "Acted on its own within this workspace's autonomy settings."
            if mode == AutonomyMode.SILENT
            else "Acted on its own and notified you within this workspace's autonomy settings."
        ),
        guardrail=guardrail,
        rule_id=rule.id if rule else None,
    )


class AutonomyEngine:
    """Reads the workspace's autonomy data through the run's repository and decides."""

    def __init__(self, store: AutonomyStore) -> None:
        self.store = store

    def config(self, context: TenantContext) -> EffectiveAutonomy:
        return effective(
            self.store.get_settings(context) or WorkspaceAutonomySettings(),
            self.store.get_guardrails(context) or OrganizationGuardrails(),
        )

    def evaluate(
        self,
        profile: ActionProfile,
        context: TenantContext,
        *,
        now: datetime | None = None,
        platform_allows: bool = False,
        facts: AutonomyFacts | None = None,
    ) -> AutonomyDecision:
        now = now or utc_now()
        cfg = self.config(context)
        hashes = {recipient_hash(context, profile.category.value, r): r for r in profile.recipients}
        known_hashes = self.store.known_recipients(context, profile.category.value, list(hashes)) if hashes else set()
        known = {hashes[h] for h in known_hashes}
        since = day_start(now, cfg)

        def count_today(*, category: str | None, tool: str | None) -> int:
            return self.store.count_actions_since(context, since, category=category, tool=tool)

        return decide(
            profile, cfg, self.store.list_rules(context),
            now=now, known=known, count_today=count_today, platform_allows=platform_allows, facts=facts,
        )


def apply_autonomy(
    base: ToolPolicyDecision,
    profile: ActionProfile,
    decision: AutonomyDecision,
) -> ToolPolicyDecision:
    """Combine the tool policy's decision with autonomy (autonomy never unblocks)."""
    from .agent_tools import ToolPolicyOutcome

    if base.outcome == ToolPolicyOutcome.BLOCK:
        return base
    info = decision.checkpoint()
    if decision.mode == AutonomyMode.BLOCK:
        return base.model_copy(
            update={"outcome": ToolPolicyOutcome.BLOCK, "reason": decision.reason, "risk_level": RiskLevel.BLOCKED, "autonomy": info}
        )
    acts = decision.mode in {AutonomyMode.NOTIFY, AutonomyMode.SILENT}
    if base.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
        if acts and base.lowerable:
            return base.model_copy(update={"outcome": ToolPolicyOutcome.ALLOW, "reason": decision.reason, "autonomy": info})
        if acts:
            # The policy itself (an organization rule, the workspace's switches) asks.
            info = {**info, "mode": AutonomyMode.ASK.value, "guardrail": Guardrail.POLICY.value, "proposable": False}
        return base.model_copy(update={"autonomy": info})
    if not acts:
        return base.model_copy(
            update={"outcome": ToolPolicyOutcome.REQUIRE_APPROVAL, "reason": decision.reason, "risk_level": profile.risk_level, "autonomy": info}
        )
    return base.model_copy(update={"autonomy": info})


def new_undo_token() -> str:
    return f"undo_{secrets.token_urlsafe(24)}"


def record_autonomous_action(
    store: AutonomyStore,
    context: TenantContext,
    *,
    task_id: str,
    run_id: str,
    profile: ActionProfile,
    decision: dict[str, Any],
    summary: str,
    undo_tool: str | None,
    undo_arguments: dict[str, Any] | None,
    undo_window_seconds: int,
    now: datetime,
) -> AutonomousAction:
    reversible = bool(undo_tool and undo_arguments is not None and undo_window_seconds > 0)
    action = AutonomousAction(
        id=new_id("auto"),
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        task_id=task_id,
        run_id=run_id,
        tool=profile.tool,
        category=profile.category,
        action_kind=profile.kind,
        level=AutonomyLevel(decision.get("level", AutonomyLevel.ACT_AND_NOTIFY.value)),
        pattern_key=profile.pattern_key,
        template=profile.template,
        summary=(summary or profile.tool)[:MAX_ACTION_SUMMARY],
        target=profile.target,
        requested_by=context.user_id,
        undo_tool=undo_tool if reversible else None,
        undo_arguments=dict(undo_arguments or {}) if reversible else {},
        undo_token=new_undo_token() if reversible else None,
        undo_expires_at=now + timedelta(seconds=undo_window_seconds) if reversible else None,
        created_at=now,
    )
    return store.add_action(action)


def observe_approval(
    store: AutonomyStore,
    context: TenantContext,
    profile: ActionProfile,
    decision: dict[str, Any] | None,
    *,
    now: datetime,
) -> AutonomyProposal | None:
    """Count an approved, executed pattern; returns the proposal when it just became pending.

    Only patterns autonomy could ever allow are counted (not protected kinds, not free-text
    messages, not ones a policy or a "always ask" rule keeps asking). A declined or accepted
    proposal is never raised again.
    """
    if not decision or not decision.get("proposable"):
        return None
    cfg = effective(store.get_settings(context) or WorkspaceAutonomySettings(), store.get_guardrails(context) or OrganizationGuardrails())
    if LEVEL_RANK[cfg.category_levels.get(profile.category, AutonomyLevel.ACT_SILENTLY)] == 0:
        return None  # the category itself is set (or capped) to ask: proposing would be refused
    if any(rule.pattern_key == profile.pattern_key for rule in store.list_rules(context)):
        return None
    proposal = store.find_proposal(profile.pattern_key, context)
    if proposal is None:
        proposal = AutonomyProposal(
            id=new_id("aprop"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            pattern_key=profile.pattern_key,
            tool=profile.tool,
            template=profile.template,
            category=profile.category,
            action_kind=profile.kind,
            created_at=now,
        )
    if proposal.status != ProposalStatus.COLLECTING:
        return None
    proposal.approvals += 1
    proposal.last_approved_at = now
    became_pending = proposal.approvals >= cfg.proposal_after
    if became_pending:
        proposal.status = ProposalStatus.PENDING
        proposal.created_at = now
    store.save_proposal(proposal)
    return proposal if became_pending else None
