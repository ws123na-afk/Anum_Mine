"""HTTP routes for the autonomy policy (docs/autonomy.md).

Who may do what (checked here, on the server, never from the client):

* Read settings, guardrails, rules, proposals and the activity feed: any member.
* Change the workspace's autonomy settings, add a rule that raises autonomy, delete a
  rule, accept or decline a proposal: workspace owners (``policy:manage``).
* Change the organization guardrails: organization administrators
  (``organization_policy:manage``).
* "Always ask for this" (a rule that only tightens): anyone who can run tasks.
* Undo an action: an owner (``approval:decide``) or the person whose task did it.

Every change writes an audit record in the same transaction; the decisions themselves
are made by the runtime (``anum_api.autonomy``), outside the model.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent_tools import ToolCall, ToolPolicy, ToolPolicyOutcome, ToolRegistry
from .audit import AuditRecord
from .authorization import Permission
from .autonomy import (
    LEVEL_RANK,
    MESSAGE_KINDS,
    PROTECTED_KINDS,
    RECOMMENDED,
    ActionFilter,
    ActionKind,
    ActionStatus,
    AutonomousAction,
    AutonomyLevel,
    AutonomyProposal,
    AutonomyRule,
    AutonomyStore,
    EffectiveAutonomy,
    OrganizationGuardrails,
    ProposalStatus,
    RuleSource,
    ToolCategory,
    UnknownActionCursorError,
    WorkspaceAutonomySettings,
    category_of,
    effective,
    floor_violations,
    kind_of,
    pattern_key,
    store_of,
)
from .dependencies import has_permission, repository_context, require_permission, tenant_context
from .errors import ErrorCode, error_response
from .events import CanonicalEventName, create_event
from .repository import AnumRepository
from .schemas import RiskLevel, TenantContext, new_id, utc_now

router = APIRouter(prefix="/api/v1/autonomy", tags=["autonomy"])

OWNER_REQUIRED = "Only a workspace owner can change autonomy for this workspace. Ask an owner of this workspace."
TIGHTEN_REQUIRED = "Viewers cannot change autonomy. Ask a workspace member or owner to tighten it."
UNDO_REQUIRED = "Only a workspace owner or the person whose task did this can undo it."
FEED_MAX = 100


def _store(repository: AnumRepository) -> AutonomyStore:
    store = store_of(repository)
    if store is None:  # every real repository has one; a test double might not
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Autonomy storage is unavailable")
    return store


def _registry(request: Request) -> ToolRegistry:
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The tool registry is not ready")
    return registry


def _require(context: TenantContext, permission: Permission, message: str) -> None:
    if not has_permission(context, permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


def _audit(
    repository: AnumRepository,
    context: TenantContext,
    action: str,
    target: str,
    metadata: dict[str, Any],
    *,
    outcome: str = "success",
    correlation_id: str | None = None,
) -> None:
    repository.record_audit(
        AuditRecord(
            id=new_id("audit"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor=context.user_id,
            action=action,
            target=target,
            outcome=outcome,
            correlation_id=correlation_id or new_id("autonomy"),
            created_at=utc_now(),
            metadata=metadata,
        )
    )


# -- settings and guardrails -----------------------------------------------------------------


class WorkspaceAutonomyUpdate(WorkspaceAutonomySettings):
    """The full workspace settings (``updated_by``/``updated_at`` are set by the server)."""

    model_config = ConfigDict(extra="forbid")


class GuardrailsUpdate(OrganizationGuardrails):
    model_config = ConfigDict(extra="forbid")


class CategoryView(BaseModel):
    category: ToolCategory
    level: AutonomyLevel = Field(description="The workspace's chosen level (act_silently when never set)")
    ceiling: AutonomyLevel | None = Field(description="The organization's ceiling, if any")
    effective: AutonomyLevel


class KindRecommendation(BaseModel):
    kind: ActionKind
    recommended: AutonomyLevel
    protected: bool
    note: str


class AutonomyOverview(BaseModel):
    settings: WorkspaceAutonomySettings
    guardrails: OrganizationGuardrails
    effective: EffectiveAutonomy
    categories: list[CategoryView]
    recommendations: list[KindRecommendation]
    pending_proposals: int
    can_manage_workspace: bool
    can_manage_guardrails: bool
    can_tighten: bool


_NOTES = {
    ActionKind.READ: "Reading and analysis run silently.",
    ActionKind.DRAFT: "Drafts run silently; nothing is sent.",
    ActionKind.CREATE: "Creating internal items acts and notifies you.",
    ActionKind.REPLY: "Replies to known recipients with an approved template act and notify you; anything else asks.",
    ActionKind.SEND: "Messages to known recipients with an approved template act and notify you; first contact and bulk sends ask.",
    ActionKind.ARCHIVE: "Archiving acts and notifies you, with Undo.",
    ActionKind.PAUSE: "Pausing acts and notifies you, with Undo.",
    ActionKind.UPDATE: "Changes ask until you allow the exact pattern.",
    ActionKind.OTHER: "Actions ANUM cannot classify always ask.",
}


def _recommendations() -> list[KindRecommendation]:
    items = []
    for kind in ActionKind:
        protected = kind in PROTECTED_KINDS
        items.append(
            KindRecommendation(
                kind=kind,
                recommended=AutonomyLevel.ASK_ALWAYS if protected else RECOMMENDED.get(kind, AutonomyLevel.ASK_ALWAYS),
                protected=protected,
                note=_NOTES.get(kind, "Money, prices, stock, deleting and merging code always ask."),
            )
        )
    return items


def _overview(context: TenantContext, store: AutonomyStore) -> AutonomyOverview:
    settings = store.get_settings(context) or WorkspaceAutonomySettings()
    guardrails = store.get_guardrails(context) or OrganizationGuardrails()
    cfg = effective(settings, guardrails)
    return AutonomyOverview(
        settings=settings,
        guardrails=guardrails,
        effective=cfg,
        categories=[
            CategoryView(
                category=category,
                level=settings.category_levels.get(category, AutonomyLevel.ACT_SILENTLY),
                ceiling=guardrails.category_ceilings.get(category),
                effective=cfg.category_levels[category],
            )
            for category in ToolCategory
        ],
        recommendations=_recommendations(),
        pending_proposals=len(store.list_proposals(context, ProposalStatus.PENDING)),
        can_manage_workspace=has_permission(context, Permission.POLICY_MANAGE),
        can_manage_guardrails=has_permission(context, Permission.ORG_POLICY_MANAGE),
        can_tighten=has_permission(context, Permission.TASK_RUN),
    )


@router.get("/settings", response_model=AutonomyOverview)
async def get_autonomy(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AutonomyOverview:
    require_permission(context, Permission.POLICY_READ)
    return _overview(context, _store(repository))


@router.put("/settings", response_model=AutonomyOverview)
async def put_autonomy(
    payload: WorkspaceAutonomyUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AutonomyOverview:
    """Owners: replace the workspace's autonomy settings, within the organization's floors."""
    _require(context, Permission.POLICY_MANAGE, OWNER_REQUIRED)
    store = _store(repository)
    guardrails = store.get_guardrails(context) or OrganizationGuardrails()
    problems = floor_violations(payload, guardrails)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Above the organization's guardrails: " + "; ".join(problems)
            + ". Lower these values, or ask an organization administrator to change the guardrails.",
        )
    before = store.get_settings(context) or WorkspaceAutonomySettings()
    saved = store.save_settings(
        context,
        WorkspaceAutonomySettings(**payload.model_dump(exclude={"updated_by", "updated_at"}), updated_by=context.user_id, updated_at=utc_now()),
    )
    _audit(
        repository,
        context,
        "autonomy.settings_updated",
        context.workspace_id,
        {
            "before": before.model_dump(mode="json", exclude={"updated_by", "updated_at"}),
            "after": saved.model_dump(mode="json", exclude={"updated_by", "updated_at"}),
        },
    )
    return _overview(context, store)


@router.get("/guardrails", response_model=OrganizationGuardrails)
async def get_guardrails(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> OrganizationGuardrails:
    require_permission(context, Permission.ORGANIZATION_READ)
    return _store(repository).get_guardrails(context) or OrganizationGuardrails()


@router.put("/guardrails", response_model=OrganizationGuardrails)
async def put_guardrails(
    payload: GuardrailsUpdate,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> OrganizationGuardrails:
    """Organization administrators: the floors every workspace of the tenant keeps."""
    require_permission(context, Permission.ORG_POLICY_MANAGE)
    store = _store(repository)
    before = store.get_guardrails(context) or OrganizationGuardrails()
    saved = store.save_guardrails(
        context,
        OrganizationGuardrails(**payload.model_dump(exclude={"updated_by", "updated_at"}), updated_by=context.user_id, updated_at=utc_now()),
    )
    _audit(
        repository,
        context,
        "autonomy.guardrails_updated",
        context.tenant_id,
        {
            "before": before.model_dump(mode="json", exclude={"updated_by", "updated_at"}),
            "after": saved.model_dump(mode="json", exclude={"updated_by", "updated_at"}),
        },
    )
    return saved


# -- rules ------------------------------------------------------------------------------------


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=160)
    template: str | None = Field(default=None, max_length=120)
    level: AutonomyLevel


def _check_raise(
    level: AutonomyLevel,
    category: ToolCategory,
    kind: ActionKind,
    risk: RiskLevel,
    template: str | None,
    cfg: EffectiveAutonomy,
) -> str | None:
    """Why a rule at ``level`` cannot be allowed (None when it can)."""
    if level == AutonomyLevel.ASK_ALWAYS:
        return None
    if kind in PROTECTED_KINDS:
        return "ANUM always asks before money, prices, stock, deleting and merging code; no rule can change that."
    if kind in MESSAGE_KINDS and not template:
        return "Messages can act on their own only with a template; free text written by ANUM always asks."
    if level == AutonomyLevel.ACT_SILENTLY and risk != RiskLevel.LOW:
        return "Acting silently is only for low-risk tools; choose act and notify."
    ceiling = cfg.category_levels.get(category, AutonomyLevel.ACT_SILENTLY)
    if LEVEL_RANK[level] > LEVEL_RANK[ceiling]:
        return f"{category.value} actions can be at most {ceiling.value.replace('_', ' ')} in this workspace."
    return None


@router.get("/rules", response_model=list[AutonomyRule])
async def list_rules(
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[AutonomyRule]:
    require_permission(context, Permission.POLICY_READ)
    return _store(repository).list_rules(context)


def _save_rule(
    repository: AnumRepository,
    context: TenantContext,
    store: AutonomyStore,
    *,
    tool: str,
    template: str | None,
    category: ToolCategory,
    kind: ActionKind,
    level: AutonomyLevel,
    source: RuleSource,
) -> AutonomyRule:
    key = pattern_key(tool, template)
    previous = next((rule for rule in store.list_rules(context) if rule.pattern_key == key), None)
    rule = store.save_rule(
        AutonomyRule(
            id=new_id("arule"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            pattern_key=key,
            tool=tool,
            template=template,
            category=category,
            action_kind=kind,
            level=level,
            source=source,
            created_by=context.user_id,
            created_at=utc_now(),
        )
    )
    _audit(
        repository,
        context,
        "autonomy.rule_created",
        rule.id,
        {
            "pattern_key": key,
            "tool": tool,
            "template": template,
            "level": level.value,
            "source": source.value,
            "replaced_level": previous.level.value if previous else None,
        },
    )
    return rule


@router.post("/rules", response_model=AutonomyRule, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreate,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AutonomyRule:
    """Owners set a level for one pattern; anyone who runs tasks can make one always ask."""
    if payload.level == AutonomyLevel.ASK_ALWAYS:
        _require(context, Permission.TASK_RUN, TIGHTEN_REQUIRED)
    else:
        _require(context, Permission.POLICY_MANAGE, OWNER_REQUIRED)
    definition = _registry(request).definition(payload.tool)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No tool named {payload.tool!r} is available in this deployment.",
        )
    store = _store(repository)
    template = (payload.template or "").strip() or None
    category, kind = category_of(payload.tool, definition), kind_of(payload.tool, definition)
    cfg = effective(store.get_settings(context) or WorkspaceAutonomySettings(), store.get_guardrails(context) or OrganizationGuardrails())
    refusal = _check_raise(payload.level, category, kind, definition.risk_level, template, cfg)
    if refusal:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refusal)
    source = RuleSource.TIGHTEN if payload.level == AutonomyLevel.ASK_ALWAYS else RuleSource.MANUAL
    return _save_rule(repository, context, store, tool=payload.tool, template=template, category=category, kind=kind, level=payload.level, source=source)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> None:
    """Owners: remove a rule (the pattern goes back to the category level and defaults)."""
    _require(context, Permission.POLICY_MANAGE, OWNER_REQUIRED)
    store = _store(repository)
    rule = store.get_rule(rule_id, context)
    if rule is None or not store.delete_rule(rule_id, context):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    _audit(repository, context, "autonomy.rule_deleted", rule_id, {"pattern_key": rule.pattern_key, "level": rule.level.value})


# -- proposals ("ask once, remember") ----------------------------------------------------------


@router.get("/proposals", response_model=list[AutonomyProposal])
async def list_proposals(
    status_filter: ProposalStatus | None = Query(default=ProposalStatus.PENDING, alias="status"),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> list[AutonomyProposal]:
    require_permission(context, Permission.POLICY_READ)
    return _store(repository).list_proposals(context, status_filter)


def _pending_proposal(store: AutonomyStore, proposal_id: str, context: TenantContext) -> AutonomyProposal:
    proposal = store.get_proposal(proposal_id, context)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This proposal was already {proposal.status.value}.")
    return proposal


@router.post("/proposals/{proposal_id}/accept", response_model=AutonomyRule)
async def accept_proposal(
    proposal_id: str,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AutonomyRule:
    _require(context, Permission.POLICY_MANAGE, OWNER_REQUIRED)
    store = _store(repository)
    proposal = _pending_proposal(store, proposal_id, context)
    definition = _registry(request).definition(proposal.tool)
    cfg = effective(store.get_settings(context) or WorkspaceAutonomySettings(), store.get_guardrails(context) or OrganizationGuardrails())
    refusal = (
        f"The tool {proposal.tool} is no longer available."
        if definition is None
        else _check_raise(proposal.proposed_level, proposal.category, proposal.action_kind, definition.risk_level, proposal.template, cfg)
    )
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)
    rule = _save_rule(
        repository, context, store,
        tool=proposal.tool, template=proposal.template, category=proposal.category, kind=proposal.action_kind,
        level=proposal.proposed_level, source=RuleSource.PROPOSAL,
    )
    proposal.status, proposal.decided_by, proposal.decided_at = ProposalStatus.ACCEPTED, context.user_id, utc_now()
    store.save_proposal(proposal)
    _audit(repository, context, "autonomy.proposal_accepted", proposal.id, {"pattern_key": proposal.pattern_key, "rule_id": rule.id})
    return rule


@router.post("/proposals/{proposal_id}/decline", response_model=AutonomyProposal)
async def decline_proposal(
    proposal_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AutonomyProposal:
    """Owners: keep asking for this pattern; ANUM will not propose it again."""
    _require(context, Permission.POLICY_MANAGE, OWNER_REQUIRED)
    store = _store(repository)
    proposal = _pending_proposal(store, proposal_id, context)
    proposal.status, proposal.decided_by, proposal.decided_at = ProposalStatus.DECLINED, context.user_id, utc_now()
    store.save_proposal(proposal)
    _audit(repository, context, "autonomy.proposal_declined", proposal.id, {"pattern_key": proposal.pattern_key})
    return proposal


# -- activity feed and undo --------------------------------------------------------------------


class ActionView(BaseModel):
    id: str
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
    status: ActionStatus
    requested_by: str
    created_at: datetime
    reversible: bool
    undo_available: bool = Field(description="Reversible, not undone, and the undo window is still open")
    undo_expires_at: datetime | None = None
    undo_token: str | None = Field(default=None, description="Present only when the caller may undo it now")
    can_undo: bool
    undone_by: str | None = None
    undone_at: datetime | None = None
    undo_error: str | None = None
    pattern_level: AutonomyLevel | None = Field(default=None, description="The rule's level for this pattern, if one exists")


class ActionPage(BaseModel):
    items: list[ActionView]
    next_cursor: str | None = None


def _may_undo(context: TenantContext, action: AutonomousAction) -> bool:
    if has_permission(context, Permission.APPROVAL_DECIDE):
        return True
    return action.requested_by == context.user_id and has_permission(context, Permission.TASK_RUN)


def _view(context: TenantContext, action: AutonomousAction, rules: dict[str, AutonomyLevel], now: datetime) -> ActionView:
    available = action.undoable(now)
    allowed = _may_undo(context, action)
    return ActionView(
        **action.model_dump(
            include={
                "id", "task_id", "run_id", "tool", "category", "action_kind", "level", "pattern_key", "template",
                "summary", "target", "status", "requested_by", "created_at", "undo_expires_at", "undone_by",
                "undone_at", "undo_error",
            }
        ),
        reversible=action.undo_tool is not None,
        undo_available=available,
        undo_token=action.undo_token if available and allowed else None,
        can_undo=allowed,
        pattern_level=rules.get(action.pattern_key),
    )


@router.get("/actions", response_model=ActionPage)
async def list_actions(
    category: ToolCategory | None = None,
    level: AutonomyLevel | None = None,
    status_filter: ActionStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=FEED_MAX),
    cursor: str | None = Query(default=None, max_length=80),
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ActionPage:
    """Everything ANUM did on its own in this workspace, newest first (any member)."""
    require_permission(context, Permission.TASK_READ)
    store = _store(repository)
    filters = ActionFilter(category=category, level=level, status=status_filter, query=(q or "").strip() or None)
    try:
        items = store.list_actions(context, filters, limit=limit + 1, after=cursor or None)
    except UnknownActionCursorError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown or invalid cursor") from None
    rules = {rule.pattern_key: rule.level for rule in store.list_rules(context)}
    now = utc_now()
    page = items[:limit]
    return ActionPage(
        items=[_view(context, action, rules, now) for action in page],
        next_cursor=page[-1].id if len(items) > limit else None,
    )


@router.get("/actions/{action_id}", response_model=ActionView)
async def get_action(
    action_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ActionView:
    """One action (a notification's Undo reads its token here; any member)."""
    require_permission(context, Permission.TASK_READ)
    store = _store(repository)
    action = store.get_action(action_id, context)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    rules = {rule.pattern_key: rule.level for rule in store.list_rules(context)}
    return _view(context, action, rules, utc_now())


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    undo_token: str = Field(min_length=8, max_length=80)


@router.post(
    "/actions/{action_id}/undo",
    response_model=ActionView,
    responses={502: {"description": "The connected service refused or did not answer; the action is still in effect"}},
)
async def undo_action(
    action_id: str,
    payload: UndoRequest,
    request: Request,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> ActionView | JSONResponse:
    """Reverse one action through its tool's compensating action, within the undo window."""
    store = _store(repository)
    action = store.get_action(action_id, context, for_update=True)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    if not _may_undo(context, action):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=UNDO_REQUIRED)
    if action.status == ActionStatus.UNDONE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This action was already undone.")
    if action.undo_tool is None or action.undo_token is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This action cannot be undone from ANUM.")
    if not hmac.compare_digest(action.undo_token, payload.undo_token):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The undo does not match this action; reload the activity and try again.")
    now = utc_now()
    if not action.undoable(now):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The undo window has closed; reverse it in the connected service if you still need to.",
        )
    registry = _registry(request)
    call = ToolCall(name=action.undo_tool, arguments=action.undo_arguments)
    decision = ToolPolicy(registry.names).evaluate(
        call, registry.definition(call.name), context, repository.get_approval_policy(context), repository.get_tool_governance(context)
    )
    if decision.outcome == ToolPolicyOutcome.BLOCK:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The undo is not allowed: {decision.reason}")
    try:
        result = await registry.execute(call, context)
        failure = None if result.status not in {"failed", "blocked"} else result.summary
    except Exception as exc:  # noqa: BLE001 - any failure is reported, the action stays done
        failure = f"The connected service did not answer ({type(exc).__name__})."
    if failure is not None:
        action.undo_error = failure[:500]
        store.save_action(action)
        _audit(repository, context, "autonomy.undo_failed", action.id, {"tool": action.tool, "undo_tool": action.undo_tool}, outcome="failure", correlation_id=action.task_id)
        # Returned, not raised, so the recorded error and audit record commit.
        return error_response(
            request,
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"The undo did not go through: {failure} The action is still in effect; try again.",
        )
    action.status, action.undone_by, action.undone_at, action.undo_error = ActionStatus.UNDONE, context.user_id, now, None
    store.save_action(action)
    repository.record_event(
        create_event(
            CanonicalEventName.AUTONOMY_ACTION_UNDONE,
            context,
            action.id,
            {"action_id": action.id, "task_id": action.task_id, "tool": action.tool},
            correlation_id=action.task_id,
        ).event
    )
    _audit(
        repository, context, "autonomy.action_undone", action.id,
        {"tool": action.tool, "undo_tool": action.undo_tool, "task_id": action.task_id}, correlation_id=action.task_id,
    )
    rules = {rule.pattern_key: rule.level for rule in store.list_rules(context)}
    return _view(context, action, rules, now)


@router.post("/actions/{action_id}/tighten", response_model=AutonomyRule, status_code=status.HTTP_201_CREATED)
async def tighten_action(
    action_id: str,
    context: TenantContext = Depends(tenant_context),
    repository: AnumRepository = Depends(repository_context),
) -> AutonomyRule:
    """"Always ask for this": a rule that makes exactly this pattern ask from now on."""
    _require(context, Permission.TASK_RUN, TIGHTEN_REQUIRED)
    store = _store(repository)
    action = store.get_action(action_id, context)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return _save_rule(
        repository, context, store,
        tool=action.tool, template=action.template, category=action.category, kind=action.action_kind,
        level=AutonomyLevel.ASK_ALWAYS, source=RuleSource.TIGHTEN,
    )
