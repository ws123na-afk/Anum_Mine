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
    # Autonomy (docs/autonomy.md): the tool's category (email, whatsapp, commerce,
    # developer, files, tasks, general) and kind of action (read, draft, reply, send,
    # archive, pause, create, update, delete, price_change, stock_change, payment,
    # merge_code, other). Undeclared values are guessed from the name, cautiously.
    category: str | None = None
    action_kind: str | None = None
    # A reversible tool names its compensating tool; the result's ``output["undo"]``
    # holds that tool's arguments, and the undo stays available this many seconds.
    undo_tool: str | None = None
    undo_window_seconds: int = Field(default=0, ge=0, le=7 * 24 * 3600)
    # Set by tools that deliver to people outside ANUM ("email", "whatsapp"): the generic
    # delivery path (anum_api/delivery.py) finds them by channel.
    delivery_channel: str | None = Field(default=None, pattern=r"^[a-z_]{2,30}$")


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
    # What the runtime records in the audit log for this call (``tool.call``). Tools
    # that set it keep it to counts and outcomes: never query text, results or secrets.
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


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
    # True only when approval is required by the platform's high-risk default alone, so
    # the workspace's autonomy policy may let the call act on its own (docs/autonomy.md).
    lowerable: bool = False
    # The autonomy decision applied by the runtime (``autonomy.AutonomyDecision``).
    autonomy: dict[str, Any] | None = None


ToolHandler = Callable[[ToolCall, TenantContext], Awaitable[ToolResult]]
# What a connector knows about one call for autonomy (returns ``autonomy.AutonomyFacts``).
AutonomyFactsProvider = Callable[[ToolCall, TenantContext], Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._facts: dict[str, AutonomyFactsProvider] = {}

    def register(
        self, definition: ToolDefinition, handler: ToolHandler, *, autonomy_facts: AutonomyFactsProvider | None = None
    ) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler
        if autonomy_facts is not None:
            self._facts[definition.name] = autonomy_facts

    def autonomy_facts(self, call: ToolCall, context: TenantContext) -> Any:
        """The tool module's facts for autonomy (``autonomy.AutonomyFacts``), or None.

        Read in the caller's tenant context; a failure propagates, so a call never acts
        on a guess (docs/autonomy.md).
        """
        provider = self._facts.get(call.name)
        return provider(call, context) if provider is not None else None

    @property
    def names(self) -> set[str]:
        return set(self._definitions)

    def definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    async def execute(self, call: ToolCall, context: TenantContext) -> ToolResult:
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolResult(status="blocked", summary=f"Unknown tool: {call.name}")
        # The last check before anything leaves ANUM: a recipient on the contact
        # suppression list is never sent to, whichever path reached the tool
        # (docs/privacy.md#contact-suppression).
        from .suppression import audit_refusal, refusal_reason, suppressed_for_call

        hits = suppressed_for_call(context, call.name, call.arguments, self._definitions.get(call.name))
        if hits:
            audit_refusal(context, call.name, hits, "execute")
            return ToolResult(
                status="blocked",
                summary="Not sent: " + refusal_reason(hits),
                audit_metadata={"outcome": "suppressed", "recipients": [hit.masked for hit in hits]},
            )
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
                # An organization rule or a workspace two-person rule keeps it asking.
                lowerable=not match.requires_approval
                and not (workspace_policy is not None and workspace_policy.two_person_rule),
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


def default_tool_registry(
    external_handler: ToolHandler | None = None,
    web_search_handler: ToolHandler | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="anum.respond",
            description="Create an internal response without external side effects.",
            risk_level=RiskLevel.LOW,
            idempotent=True,
            category="general",
            action_kind="read",
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
            category="general",
            action_kind="other",
        ),
        external_handler or _external_action,
    )
    # Team work (docs/team-work.md#assigning-in-words): creates an assigned work item from
    # the user's own sentence and notifies the assignee. Imported lazily (it needs the API's
    # stores), so registries that never call it stay light.
    from .team_work import TEAM_ASSIGN_DEFINITION, assign_from_command

    registry.register(TEAM_ASSIGN_DEFINITION, assign_from_command)
    if web_search_handler is not None:
        # Registered only when ANUM_SEARXNG_URL is set (anum_api/web_search.py): without
        # it the tool does not exist, so no skill can select it.
        registry.register(
            ToolDefinition(
                name="web_search",
                description="Search the web through the deployment's private SearXNG; read-only.",
                risk_level=RiskLevel.LOW,
                timeout_seconds=30,
                idempotent=True,
                target=getattr(web_search_handler, "target_host", None),
                category="general",
                action_kind="read",
            ),
            web_search_handler,
        )
    return registry
