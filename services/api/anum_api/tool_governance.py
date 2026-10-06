"""Organization governance rules as seen by the runtime's tool policy (threat model A4).

Governance approval rules (``POST /organization/approval-rules``) and the rules of
active policy packs (``POST /policy-packs``) are tenant-level settings. The repository
loads the enabled approval rules and the active packs' rules for the caller's tenant
(inside its RLS scope) into a ``ToolGovernance`` snapshot; ``ToolPolicy.evaluate`` and
the approval decision route consult it, outside the model.

Rules can only make a call stricter: a ``deny`` blocks it, ``require_approval`` (and
every enabled approval rule) pauses it for approval, and ``allow`` never weakens a
platform decision (high risk still needs approval, a blocked tool stays blocked).

Patterns (``ApprovalRule.action_pattern`` and ``PolicyRule.action``), case-insensitive
shell-style globs (``*``, ``?``, ``[...]``):

- ``<glob>`` or ``tool:<glob>``: the tool name, e.g. ``external.*``.
- ``target:<glob>`` or ``integration:<glob>``: the configured integration host the tool
  contacts (``ToolDefinition.target``); internal tools without a target never match.
- ``risk:<level>``: tools at or above ``low``, ``medium`` or ``high`` risk.

Policy pack rule ``conditions`` narrow a rule: ``risk_level`` (a level or a list of
levels, exact match) and ``target`` (a glob or a list of globs). A condition key the
runtime does not understand makes the rule match (fail closed), so a typo can never
silently disable a ``deny`` or ``require_approval`` rule.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from .schemas import RiskLevel

RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.BLOCKED: 3}
KNOWN_CONDITIONS = frozenset({"risk_level", "target"})
TOOL_PREFIXES = ("tool:",)
TARGET_PREFIXES = ("target:", "integration:")
RISK_PREFIX = "risk:"


class GovernanceApprovalRule(BaseModel):
    """An enabled organization approval rule (``approval_rules``)."""

    id: str
    name: str
    action_pattern: str
    minimum_approvers: int = Field(default=1, ge=1)
    required_roles: list[str] = Field(default_factory=lambda: ["owner"])


class GovernancePolicyRule(BaseModel):
    """One rule of an active policy pack, with the pack version it came from."""

    pack_id: str
    pack_name: str
    pack_version: int
    action: str
    effect: str
    conditions: dict[str, Any] = Field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"policy_pack:{self.pack_name}@v{self.pack_version}:{self.action}"


class ToolGovernance(BaseModel):
    """The tenant's governance rules that apply to tool calls (empty: no rules)."""

    approval_rules: list[GovernanceApprovalRule] = Field(default_factory=list)
    policy_rules: list[GovernancePolicyRule] = Field(default_factory=list)


class GovernanceMatch(BaseModel):
    """Which governance rules matched one call."""

    deny: list[GovernancePolicyRule] = Field(default_factory=list)
    require_approval: list[GovernancePolicyRule] = Field(default_factory=list)
    approval_rules: list[GovernanceApprovalRule] = Field(default_factory=list)

    @property
    def requires_approval(self) -> bool:
        return bool(self.require_approval or self.approval_rules)

    @property
    def approval_labels(self) -> list[str]:
        return [f"approval_rule:{rule.name}" for rule in self.approval_rules] + [
            rule.label for rule in self.require_approval
        ]


def _rule_field(rule: object, name: str, default: Any = None) -> Any:
    if isinstance(rule, dict):
        return rule.get(name, default)
    return getattr(rule, name, default)


def tool_governance_from(approval_rules: Iterable[Any], policy_packs: Iterable[Any]) -> ToolGovernance:
    """Build the snapshot from stored approval rules and policy packs.

    Accepts the governance API models or the SQLAlchemy records (same attribute names;
    pack rules may be models or JSON objects). Disabled approval rules and inactive
    packs are left out.
    """
    approvals = [
        GovernanceApprovalRule(
            id=rule.id,
            name=rule.name,
            action_pattern=rule.action_pattern,
            minimum_approvers=rule.minimum_approvers,
            required_roles=list(rule.required_roles),
        )
        for rule in approval_rules
        if rule.enabled
    ]
    policies = [
        GovernancePolicyRule(
            pack_id=pack.id,
            pack_name=pack.name,
            pack_version=pack.version,
            action=str(_rule_field(rule, "action", "")),
            # PolicyEffect is a StrEnum, so str() gives its value; JSON rows hold the string.
            effect=str(_rule_field(rule, "effect", "")),
            conditions=dict(_rule_field(rule, "conditions", None) or {}),
        )
        for pack in policy_packs
        if pack.active
        for rule in pack.rules
    ]
    return ToolGovernance(approval_rules=approvals, policy_rules=policies)


def _risk(value: object) -> RiskLevel | None:
    try:
        return RiskLevel(str(value).strip().lower())
    except ValueError:
        return None


def pattern_matches(pattern: str, *, tool: str, target: str | None, risk_level: RiskLevel) -> bool:
    """Whether a rule pattern selects the call (see the module docstring)."""
    text = pattern.strip().lower()
    if text.startswith(RISK_PREFIX):
        threshold = _risk(text[len(RISK_PREFIX):])
        # An unknown level is a misconfigured rule: match (fail closed).
        return threshold is None or RISK_ORDER[risk_level] >= RISK_ORDER[threshold]
    for prefix in TARGET_PREFIXES:
        if text.startswith(prefix):
            return target is not None and fnmatchcase(target.lower(), text[len(prefix):])
    for prefix in TOOL_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return fnmatchcase(tool.lower(), text)


def _as_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value]
    return [str(value)]


def conditions_match(conditions: dict[str, Any], *, target: str | None, risk_level: RiskLevel) -> bool:
    if set(conditions) - KNOWN_CONDITIONS:
        return True
    if "risk_level" in conditions:
        levels = {_risk(item) for item in _as_list(conditions["risk_level"])}
        if None not in levels and risk_level not in levels:
            return False
    if "target" in conditions:
        if target is None:
            return False
        if not any(fnmatchcase(target.lower(), glob.strip().lower()) for glob in _as_list(conditions["target"])):
            return False
    return True


def match_governance(
    governance: ToolGovernance | None,
    *,
    tool: str,
    target: str | None,
    risk_level: RiskLevel,
) -> GovernanceMatch:
    """The governance rules that select a call to ``tool`` at ``target`` and ``risk_level``."""
    result = GovernanceMatch()
    if governance is None:
        return result
    for rule in governance.policy_rules:
        if not pattern_matches(rule.action, tool=tool, target=target, risk_level=risk_level):
            continue
        if not conditions_match(rule.conditions, target=target, risk_level=risk_level):
            continue
        effect = rule.effect.strip().lower()
        if effect == "deny":
            result.deny.append(rule)
        elif effect == "require_approval":
            result.require_approval.append(rule)
        elif effect != "allow":
            # An effect the runtime does not know is treated as deny (fail closed).
            result.deny.append(rule)
    result.approval_rules = [
        rule
        for rule in governance.approval_rules
        if pattern_matches(rule.action_pattern, tool=tool, target=target, risk_level=risk_level)
    ]
    return result


def decision_requirements(match: GovernanceMatch) -> tuple[int, set[str] | None]:
    """Approvers and decider roles the matched approval rules demand at decision time.

    Returns ``(minimum_approvers, allowed_roles)``: the largest ``minimum_approvers`` of
    the matched rules (1 without any) and the roles a decider must hold to satisfy every
    matched rule (the intersection of their ``required_roles``; ``None`` without any).
    """
    minimum = max((rule.minimum_approvers for rule in match.approval_rules), default=1)
    allowed: set[str] | None = None
    for rule in match.approval_rules:
        roles = {role.strip().lower() for role in rule.required_roles if role.strip()}
        if not roles:
            continue
        allowed = roles if allowed is None else allowed & roles
    return minimum, allowed
