import json
from collections.abc import Awaitable, Callable, Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .prompt_provenance import Provenance, label_untrusted
from .schemas import RiskLevel, TenantContext, WorkspaceApprovalPolicy
from .tool_governance import ToolGovernance, match_governance


class ToolDefinition(BaseModel):
    name: str
    description: str
    risk_level: RiskLevel
    required_roles: frozenset[str] = Field(default_factory=frozenset)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    idempotent: bool = False
    # The configured integration target (host only: no scheme, credentials, port,
    # path or query), shown on approvals (threat model G2). None for internal tools.
    target: str | None = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """What a tool returned. ``output`` is untrusted data (threat model G3).

    ``truncated`` is true when the integration's response body exceeded
    ``ANUM_TOOL_RESPONSE_MAX_BYTES`` and only its first bytes were kept.
    """

    status: str
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False


class ToolPolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


class ToolPolicyDecision(BaseModel):
    outcome: ToolPolicyOutcome
    reason: str
    risk_level: RiskLevel
    # Governance rules (approval rules, policy pack versions) that shaped the decision.
    governance_rules: list[str] = Field(default_factory=list)


ToolHandler = Callable[[ToolCall, TenantContext], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    @property
    def names(self) -> set[str]:
        return set(self._definitions)

    def definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    async def execute(self, call: ToolCall, context: TenantContext) -> ToolResult:
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolResult(status="blocked", summary=f"Unknown tool: {call.name}")
        return await handler(call, context)


class ToolPolicy:
    def __init__(self, allowed_tools: Iterable[str] | None = None) -> None:
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None

    def evaluate(
        self,
        call: ToolCall,
        definition: ToolDefinition | None,
        context: TenantContext,
        workspace_policy: WorkspaceApprovalPolicy | None = None,
        governance: ToolGovernance | None = None,
    ) -> ToolPolicyDecision:
        """Decide a call outside the model.

        Order: unregistered, outside the allowlist, missing role and ``blocked`` tools are
        blocked; a matching governance ``deny`` blocks; ``high`` risk needs approval; a
        matching governance approval rule or ``require_approval`` policy rule needs
        approval; ``medium`` risk needs approval when the workspace policy says so;
        everything else is allowed. Governance can only add restrictions.
        """
        if definition is None:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.BLOCK,
                reason="The requested tool is not registered.",
                risk_level=RiskLevel.BLOCKED,
            )
        if self.allowed_tools is not None and call.name not in self.allowed_tools:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.BLOCK,
                reason="The tool is outside the runtime allowlist.",
                risk_level=RiskLevel.BLOCKED,
            )
        roles = {role.lower() for role in context.roles}
        if definition.required_roles and not roles.intersection(definition.required_roles):
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.BLOCK,
                reason="The actor does not have a role required by this tool.",
                risk_level=RiskLevel.BLOCKED,
            )
        if definition.risk_level == RiskLevel.BLOCKED:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.BLOCK,
                reason="The tool is prohibited by runtime policy.",
                risk_level=RiskLevel.BLOCKED,
            )
        match = match_governance(
            governance, tool=call.name, target=definition.target, risk_level=definition.risk_level
        )
        if match.deny:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.BLOCK,
                reason="An organization policy pack denies this action.",
                risk_level=RiskLevel.BLOCKED,
                governance_rules=[rule.label for rule in match.deny],
            )
        if definition.risk_level == RiskLevel.HIGH:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.REQUIRE_APPROVAL,
                reason="External or high-impact actions require explicit approval.",
                risk_level=definition.risk_level,
                governance_rules=match.approval_labels,
            )
        if match.requires_approval:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.REQUIRE_APPROVAL,
                reason="An organization approval rule requires approval for this action.",
                risk_level=definition.risk_level,
                governance_rules=match.approval_labels,
            )
        if (
            definition.risk_level == RiskLevel.MEDIUM
            and workspace_policy is not None
            and workspace_policy.medium_risk_requires_approval
        ):
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.REQUIRE_APPROVAL,
                reason="This workspace requires approval for medium-risk actions.",
                risk_level=definition.risk_level,
            )
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.ALLOW,
            reason="The tool is low risk and within the actor's scope.",
            risk_level=definition.risk_level,
        )


async def _respond(call: ToolCall, _: TenantContext) -> ToolResult:
    text = str(call.arguments.get("text", "")).strip()
    return ToolResult(
        status="succeeded",
        # The model's answer is the task result; only fall back to a generic line when empty.
        summary=text or "Prepared an internal ANUM response.",
        output={"text": text},
    )


async def _external_action(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(
        status="succeeded",
        summary="Executed the approved external action through the mock adapter.",
        output={"action": call.arguments.get("action", "external_action")},
    )


# Characters of tool output placed into a prompt; the stored body is already capped in bytes.
TOOL_OUTPUT_PROMPT_MAX_CHARS = 16_000


def tool_output_prompt_block(
    call: ToolCall,
    result: ToolResult,
    *,
    target: str | None = None,
    max_chars: int = TOOL_OUTPUT_PROMPT_MAX_CHARS,
) -> str:
    """The tool's output as an untrusted, provenance-labeled prompt block.

    Every place that feeds tool output back into a model prompt (multi-step runs)
    must go through this, together with ``prompt_provenance.UNTRUSTED_DATA_RULES``.
    The output is serialized as JSON so nothing in it is mistaken for prompt text.
    """
    serialized = json.dumps(
        {"status": result.status, "summary": result.summary, "output": result.output},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    origin = f"{call.name}@{target}" if target else call.name
    return label_untrusted(
        serialized,
        source=Provenance.TOOL_OUTPUT,
        origin=origin,
        truncated=result.truncated,
        max_chars=max_chars,
    )


def default_tool_registry(external_handler: ToolHandler | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="anum.respond",
            description="Create an internal response without external side effects.",
            risk_level=RiskLevel.LOW,
            idempotent=True,
        ),
        _respond,
    )
    registry.register(
        ToolDefinition(
            name="external.action",
            description="Perform a mock external action after explicit approval.",
            risk_level=RiskLevel.HIGH,
            required_roles=frozenset({"owner", "member"}),
            target=getattr(external_handler, "target_host", None),
        ),
        external_handler or _external_action,
    )
    return registry
