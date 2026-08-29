from collections.abc import Awaitable, Callable, Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .schemas import RiskLevel, TenantContext


class ToolDefinition(BaseModel):
    name: str
    description: str
    risk_level: RiskLevel
    required_roles: frozenset[str] = Field(default_factory=frozenset)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    idempotent: bool = False


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    status: str
    summary: str
    output: dict[str, Any] = Field(default_factory=dict)


class ToolPolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


class ToolPolicyDecision(BaseModel):
    outcome: ToolPolicyOutcome
    reason: str
    risk_level: RiskLevel


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
    ) -> ToolPolicyDecision:
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
        if definition.risk_level == RiskLevel.HIGH:
            return ToolPolicyDecision(
                outcome=ToolPolicyOutcome.REQUIRE_APPROVAL,
                reason="External or high-impact actions require explicit approval.",
                risk_level=definition.risk_level,
            )
        return ToolPolicyDecision(
            outcome=ToolPolicyOutcome.ALLOW,
            reason="The tool is low risk and within the actor's scope.",
            risk_level=definition.risk_level,
        )


async def _respond(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(
        status="succeeded",
        summary="Prepared an internal ANUM response.",
        output={"text": str(call.arguments.get("text", ""))},
    )


async def _external_action(call: ToolCall, _: TenantContext) -> ToolResult:
    return ToolResult(
        status="succeeded",
        summary="Executed the approved external action through the mock adapter.",
        output={"action": call.arguments.get("action", "external_action")},
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
        ),
        external_handler or _external_action,
    )
    return registry
