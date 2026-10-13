"""Approval routing: who may approve an approval, decided by its amount and conditions.

See ``docs/approvals-and-risk.md#approval-routing``. An organization approval rule
(``POST /organization/approval-rules``) may carry

* ``conditions``: an AED amount band (``amount_above`` and/or ``amount_at_most``), an
  optional percentage of the order value (``percent_of_order_at_most``) and a ``disputed``
  flag (``any``, ``yes``, ``no``). The amount is read from the tool arguments
  (``amount_field``, or the first of :data:`AMOUNT_KEYS`), and the amounts of the task's
  other pending or approved approvals that the same rule matches are **added**, so an
  action cannot be split into smaller ones to stay under a threshold.
* ``approver_steps``: one or two steps (sequential approval). Each step lists who may
  approve it: ``owner`` (workspace owners), ``org_admin`` (organization administrators),
  ``role:owner`` / ``role:member`` (a workspace role) or ``user:<user id>`` (a named
  member). Step 2 must be approved by a different person after step 1.

Matching is fail closed:

* Whether a call needs approval at all is decided by the rule's tool pattern alone
  (``tool_governance.match_governance``): a refund always pauses whatever its amount.
  The conditions only choose **who** decides.
* An amount that is missing, not a number, or in another currency counts as above every
  threshold: an ``amount_at_most`` band (a delegation) never applies, an ``amount_above``
  band always does. A missing or zero order value means the percentage is unknown, so a
  ``percent_of_order_at_most`` band does not apply.
* When rules match the tool but none of their conditions hold, the approval falls back
  to the workspace owners.

Everything here is pure: the caller supplies the rules (read in the tenant's RLS scope),
the call's arguments, the related approvals and the decider's context.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .schemas import ApprovalRouting, ApprovalStepView, RiskLevel, RouteReason, TenantContext
from .tool_governance import GovernanceApprovalRule, pattern_matches, required_approvals

CURRENCY = "AED"
__all__ = ["ApprovalRouting", "ApprovalStepView", "RouteReason"]
AMOUNT_KEYS = ("amount", "refund_amount", "discount_amount", "total", "value", "spend", "cost")
ORDER_VALUE_KEYS = ("order_total", "order_value", "order_amount", "subtotal")
MAX_AMOUNT = 100_000_000
MAX_STEPS = 2
APPROVER_PATTERN = re.compile(r"^(owner|org_admin|role:(owner|member)|user:[A-Za-z0-9_.@:+|-]{1,160})$")
FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63})?$")
# Workspace roles that may ever decide; viewers never approve, even when named.
DECIDING_ROLES = frozenset({"owner", "member"})


class ApprovalConditions(BaseModel):
    """When a routed approval rule applies (all set conditions must hold)."""

    amount_field: str | None = Field(default=None, max_length=129)
    currency: Literal["AED"] = CURRENCY
    amount_above: float | None = Field(default=None, ge=0, le=MAX_AMOUNT)
    amount_at_most: float | None = Field(default=None, ge=0, le=MAX_AMOUNT)
    order_value_field: str | None = Field(default=None, max_length=129)
    percent_of_order_at_most: float | None = Field(default=None, gt=0, le=100)
    disputed: Literal["any", "yes", "no"] = "any"

    @field_validator("amount_field", "order_value_field")
    @classmethod
    def _field_name(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not FIELD_PATTERN.match(value):
            raise ValueError("use an argument name such as amount or order.total")
        return value

    @model_validator(mode="after")
    def _band(self) -> ApprovalConditions:
        if (
            self.amount_above is not None
            and self.amount_at_most is not None
            and self.amount_at_most <= self.amount_above
        ):
            raise ValueError("amount_at_most must be larger than amount_above")
        return self

    @property
    def is_empty(self) -> bool:
        return (
            self.amount_above is None
            and self.amount_at_most is None
            and self.percent_of_order_at_most is None
            and self.disputed == "any"
        )


class ApproverStep(BaseModel):
    """One step of a routed approval: anyone listed may approve it."""

    approvers: list[str] = Field(min_length=1, max_length=20)

    @field_validator("approvers")
    @classmethod
    def _approvers(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for approver in value:
            approver = approver.strip()
            if approver.startswith("user:"):
                approver = "user:" + approver[5:].strip()
            else:
                approver = approver.lower()
            if not APPROVER_PATTERN.match(approver):
                raise ValueError(
                    "approvers are owner, org_admin, role:owner, role:member or user:<user id>"
                )
            if approver not in cleaned:
                cleaned.append(approver)
        return cleaned


class Route(BaseModel):
    """The internal route: steps of approver sets (a decider must satisfy every set)."""

    routed: bool = False
    rules: list[str] = Field(default_factory=list)
    steps: list[list[list[str]]] = Field(default_factory=list)
    reasons: list[RouteReason] = Field(default_factory=list)
    fallback: bool = False
    two_person: bool = False

    @property
    def required(self) -> int:
        return max(1, len(self.steps))


class RelatedAction(BaseModel):
    """Another pending or approved approval of the same task (for the no-splitting sum)."""

    action: str
    target: str | None = None
    risk_level: RiskLevel
    arguments: dict[str, Any] = Field(default_factory=dict)


def _lookup(arguments: Mapping[str, Any], field: str) -> Any:
    head, _, tail = field.partition(".")
    value = arguments.get(head)
    if tail:
        return value.get(tail) if isinstance(value, Mapping) else None
    return value


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        text = repr(value)
    elif isinstance(value, str):
        text = value.replace(",", "").replace("AED", "").replace("aed", "").strip()
    else:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return number


def amount_of(arguments: Mapping[str, Any], field: str | None = None) -> Decimal | None:
    """The call's AED amount, or None when unknown (missing, not a number, other currency)."""
    keys = (field,) if field else AMOUNT_KEYS
    amount = None
    used = None
    for key in keys:
        amount = _decimal(_lookup(arguments, key))
        if amount is not None:
            used = key
            break
    if amount is None or used is None:
        return None
    currency = _lookup(arguments, f"{used}_currency") if "." not in used else None
    currency = currency or arguments.get("currency")
    if currency is not None and str(currency).strip().upper() != CURRENCY:
        return None
    return amount


def order_value_of(arguments: Mapping[str, Any], field: str | None = None) -> Decimal | None:
    keys = (field,) if field else ORDER_VALUE_KEYS
    for key in keys:
        value = _decimal(_lookup(arguments, key))
        if value is not None and value > 0:
            return value
    return None


def is_disputed(arguments: Mapping[str, Any]) -> bool:
    value = arguments.get("disputed")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"true", "yes", "1", "disputed"}


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01')):f}".rstrip("0").rstrip(".") if value % 1 else f"{value:.0f}"


def _limit(value: float) -> Decimal:
    return Decimal(repr(value))


def conditions_of(rule: GovernanceApprovalRule) -> ApprovalConditions | None:
    if not rule.conditions:
        return None
    try:
        parsed = ApprovalConditions.model_validate(rule.conditions)
    except ValueError:
        # A stored condition the runtime cannot read never delegates: treat the rule as
        # an owner-only band (fail closed).
        return ApprovalConditions(amount_above=0)
    return None if parsed.is_empty else parsed


def steps_of(rule: GovernanceApprovalRule) -> list[list[str]]:
    steps: list[list[str]] = []
    for raw in rule.approver_steps or []:
        try:
            steps.append(ApproverStep.model_validate({"approvers": list(raw)}).approvers)
        except ValueError:
            steps.append(["owner"])
    return steps[:MAX_STEPS]


def _legacy_steps(rule: GovernanceApprovalRule) -> list[list[list[str]]]:
    """A rule without approver steps, as steps: owners (``approval:decide``) holding a listed role."""
    roles = [role.strip().lower() for role in rule.required_roles if role.strip()]
    tokens = ["owner" if role == "owner" else f"role:{role}" for role in roles]
    sets = [["owner"]] if not tokens or tokens == ["owner"] else [["owner"], tokens]
    return [sets for _ in range(required_approvals(rule.minimum_approvers))]


def _evaluate(
    rule: GovernanceApprovalRule,
    conditions: ApprovalConditions,
    arguments: Mapping[str, Any],
    related: Sequence[RelatedAction],
) -> tuple[bool, list[RouteReason]]:
    reasons: list[RouteReason] = []
    applies = True
    amount = amount_of(arguments, conditions.amount_field)
    total = amount
    counted = 0
    if amount is not None:
        for other in related:
            if not pattern_matches(rule.action_pattern, tool=other.action, target=other.target, risk_level=other.risk_level):
                continue
            other_amount = amount_of(other.arguments, conditions.amount_field)
            if other_amount is None:
                total = None
                break
            total = (total or Decimal(0)) + other_amount
            counted += 1
    if conditions.amount_above is not None or conditions.amount_at_most is not None:
        if total is None:
            ok = conditions.amount_at_most is None
            reasons.append(RouteReason(code="amount_unknown", rule=rule.name, applies=ok))
            applies = applies and ok
        else:
            if conditions.amount_above is not None:
                limit = _limit(conditions.amount_above)
                ok = total > limit
                reasons.append(RouteReason(code="amount_above", rule=rule.name, applies=ok, amount=_money(amount),
                                           total=_money(total), threshold=_money(limit), related=counted))
                applies = applies and ok
            if conditions.amount_at_most is not None:
                limit = _limit(conditions.amount_at_most)
                ok = total <= limit
                reasons.append(RouteReason(code="amount_at_most", rule=rule.name, applies=ok, amount=_money(amount),
                                           total=_money(total), threshold=_money(limit), related=counted))
                applies = applies and ok
    if conditions.percent_of_order_at_most is not None:
        order_value = order_value_of(arguments, conditions.order_value_field)
        limit = _limit(conditions.percent_of_order_at_most)
        if total is None or order_value is None:
            reasons.append(RouteReason(code="percent_unknown", rule=rule.name, applies=False, threshold=_money(limit)))
            applies = False
        else:
            percent = (total / order_value * 100).quantize(Decimal("0.01"))
            ok = percent <= limit
            reasons.append(RouteReason(code="percent_at_most", rule=rule.name, applies=ok, percent=_money(percent),
                                       threshold=_money(limit), total=_money(total)))
            applies = applies and ok
    if conditions.disputed != "any":
        disputed = is_disputed(arguments)
        ok = disputed if conditions.disputed == "yes" else not disputed
        reasons.append(RouteReason(code="disputed" if disputed else "not_disputed", rule=rule.name, applies=ok))
        applies = applies and ok
    return applies, reasons


def build_route(
    rules: Iterable[GovernanceApprovalRule],
    arguments: Mapping[str, Any] | None,
    related: Sequence[RelatedAction] = (),
) -> Route:
    """The route for an approval whose tool the ``rules`` already match (by pattern).

    ``routed`` is False when no matching rule has conditions or approver steps: the
    original approval-rule behaviour applies unchanged (``tool_governance``).
    """
    matched = list(rules)
    arguments = arguments or {}
    if not any(conditions_of(rule) is not None or rule.approver_steps for rule in matched):
        return Route()
    route = Route(routed=True)
    applicable: list[tuple[GovernanceApprovalRule, list[list[list[str]]]]] = []
    for rule in matched:
        conditions = conditions_of(rule)
        if conditions is not None:
            applies, reasons = _evaluate(rule, conditions, arguments, related)
            route.reasons.extend(reasons)
            if not applies:
                continue
        else:
            route.reasons.append(RouteReason(code="rule", rule=rule.name))
        explicit = steps_of(rule)
        steps = [[step] for step in explicit] if explicit else _legacy_steps(rule)
        if not explicit and rule.minimum_approvers >= 2:
            route.two_person = True
        applicable.append((rule, steps))
    if not applicable:
        route.fallback = True
        route.reasons.append(RouteReason(code="no_band"))
        route.steps = [[["owner"]]]
        return route
    route.rules = [rule.name for rule, _ in applicable]
    depth = max(len(steps) for _, steps in applicable)
    for index in range(depth):
        sets: list[list[str]] = []
        for _, steps in applicable:
            if index < len(steps):
                for approver_set in steps[index]:
                    if approver_set not in sets:
                        sets.append(approver_set)
        route.steps.append(sets)
    return route


def holds(context: TenantContext, approver: str) -> bool:
    """Whether the decider is one of ``approver`` (see :class:`ApproverStep`)."""
    roles = {role.lower() for role in context.roles}
    if approver == "org_admin":
        return "org_admin" in {role.lower() for role in context.organization_roles}
    if not roles & DECIDING_ROLES:
        return False
    if approver == "owner":
        return "owner" in roles
    if approver.startswith("role:"):
        return approver[5:] in roles
    if approver.startswith("user:"):
        return context.user_id == approver[5:]
    return False


def eligible_for(context: TenantContext, step: Sequence[Sequence[str]]) -> bool:
    """A decider must be one of the approvers of every approver set of the step."""
    return bool(step) and all(any(holds(context, approver) for approver in approver_set) for approver_set in step)


def self_approval_refused(context: TenantContext, route: Route, own_request: bool) -> bool:
    """Approvers never approve their own request.

    Anyone who decides only through a delegation (a named member, ``role:member``,
    ``org_admin`` without ownership) may never approve a task they created or a call
    their own run requested. Owners follow the two-person rules (a rule with
    ``minimum_approvers`` 2 or more, or the workspace switch): a single-owner business
    must still be able to approve the actions its own automations propose.
    """
    if not own_request:
        return False
    owner = "owner" in {role.lower() for role in context.roles}
    return route.two_person or not owner


def describe_approvers(approvers: Sequence[str]) -> str:
    labels = []
    for approver in approvers:
        if approver == "owner":
            labels.append("owners")
        elif approver == "org_admin":
            labels.append("organization administrators")
        elif approver.startswith("role:"):
            labels.append(f"{approver[5:]}s")
        else:
            labels.append(approver[5:])
    return " or ".join(labels)


def summary_of(route: Route, step: int) -> str:
    """One English sentence for logs, audit and the API's fallback text."""
    if not route.routed:
        return ""
    parts: list[str] = []
    for reason in route.reasons:
        if not reason.applies or reason.code in {"rule", "no_band"}:
            continue
        if reason.code == "amount_above":
            parts.append(f"{reason.rule}: AED {reason.total} > AED {reason.threshold}")
        elif reason.code == "amount_at_most":
            parts.append(f"{reason.rule}: AED {reason.total} ≤ AED {reason.threshold}")
        elif reason.code == "amount_unknown":
            parts.append(f"{reason.rule}: the amount is not known in AED")
        elif reason.code == "percent_at_most":
            parts.append(f"{reason.rule}: {reason.percent}% ≤ {reason.threshold}% of the order")
        elif reason.code == "disputed":
            parts.append(f"{reason.rule}: disputed")
    if route.fallback:
        parts.append("no delegated band applies")
    index = min(step, len(route.steps) - 1)
    who = " and ".join(describe_approvers(s) for s in route.steps[index]) if route.steps else "owners"
    if len(route.steps) > 1:
        who = f"step {index + 1} of {len(route.steps)}: {who}"
    return f"{'; '.join(parts) or ', '.join(route.rules)}: {who}"


def related_actions(approvals: Iterable[Any], approval_id: str, task_id: str) -> list[RelatedAction]:
    """The task's other pending or approved approvals (for the no-splitting sum)."""
    out: list[RelatedAction] = []
    for other in approvals:
        status = getattr(other.status, "value", other.status)
        if other.id == approval_id or other.task_id != task_id or status not in {"pending", "approved"}:
            continue
        out.append(RelatedAction(action=other.action, target=other.target, risk_level=other.risk_level,
                                 arguments=dict(other.arguments or {})))
    return out
