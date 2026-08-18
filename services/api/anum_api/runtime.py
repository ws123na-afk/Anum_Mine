from .model_gateway import MockModelGateway
from .events import CanonicalEventName, create_event
from .repository import AnumRepository
from .skills_library import DEFAULT_SKILL_REGISTRY
from .schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RiskLevel,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)
from .tools import (
    ToolContract,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolResultStatus,
    execute_approved_tool,
    execute_tool,
)

_HIGH_RISK_TOOL_NAME = "agent_high_risk_action"


async def _run_high_risk_mock_action(
    inputs: dict[str, object], context: ToolExecutionContext
) -> ToolResult:
    # Stands in for a real side-effecting action (sending a message,
    # deleting a record, spending money, ...) until a real tool adapter is
    # registered here for a given deployment - see docs/tools-and-integrations.md.
    # The mediation (risk gate, approval pause, audit trail) is real even
    # though this particular handler is a placeholder.
    return ToolResult(
        status=ToolResultStatus.SUCCESS,
        output={"summary": "Executed approved high-risk mock action."},
    )


def _build_high_risk_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolContract(
            name=_HIGH_RISK_TOOL_NAME,
            description="Placeholder high-risk action gated by approval - see runtime.py.",
            risk_level=RiskLevel.HIGH,
            timeout_seconds=30,
            idempotent=False,
        ),
        _run_high_risk_mock_action,
    )
    return registry


# Module-level: contracts/handlers are stateless, and every AgentRuntime
# instance (one per request/activity - see main.py, workflows/activities.py)
# would otherwise rebuild an identical registry for no reason.
_HIGH_RISK_TOOL_REGISTRY = _build_high_risk_tool_registry()


class AgentRuntime:
    def __init__(self, model_gateway: MockModelGateway, repository: AnumRepository) -> None:
        self.model_gateway = model_gateway
        self.repository = repository

    async def run_task(self, task: Task, context: TenantContext) -> tuple[AgentRun, Approval | None]:
        now = utc_now()
        task.status = TaskStatus.RUNNING
        task.updated_at = now

        run = AgentRun(
            id=new_id("run"),
            task_id=task.id,
            status=TaskStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )

        # Skill selection is advisory only (see skills.py's module docstring):
        # it decides which guidance to consult, not what's actually allowed -
        # tool/memory access is still separately mediated below and by
        # authorization.py. Recorded here purely so the run trace shows why
        # the agent behaved a certain way (docs/skills.md).
        skill = DEFAULT_SKILL_REGISTRY.select_for_task(task.prompt)
        if skill is not None:
            run.steps.append(
                AgentRunStep(
                    id=new_id("step"),
                    type="tool_proposal",
                    summary=f"Selected skill '{skill.name}' (v{skill.version}).",
                    created_at=utc_now(),
                    metadata={"skill_id": skill.id, "skill_version": skill.version},
                )
            )

        model_response = await self.model_gateway.generate_text(task.prompt)
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="model_call",
                summary=model_response.text,
                created_at=utc_now(),
                metadata={"usage": model_response.usage.model_dump()},
            )
        )

        if self._requires_approval(task.prompt):
            tool_context = ToolExecutionContext(
                tenant=context, correlation_id=task.id, actor_id=context.user_id, task_id=task.id
            )
            mediated, _audit_record = await execute_tool(
                _HIGH_RISK_TOOL_REGISTRY,
                _HIGH_RISK_TOOL_NAME,
                {"prompt": task.prompt},
                tool_context,
                repository_for_approval=self.repository,
            )
            assert isinstance(mediated, Approval)  # risk_level=HIGH always yields an Approval
            approval = mediated

            task.status = TaskStatus.WAITING_APPROVAL
            task.updated_at = utc_now()
            run.status = TaskStatus.WAITING_APPROVAL
            run.updated_at = utc_now()
            run.steps.append(
                AgentRunStep(
                    id=new_id("step"),
                    type="approval_wait",
                    summary="Paused for approval before high-risk action.",
                    created_at=utc_now(),
                )
            )
            self._record_event(
                "approval.requested",
                context,
                approval.id,
                {"task_id": task.id},
                correlation_id=task.id,
            )
            return run, approval

        task.status = TaskStatus.COMPLETED
        task.updated_at = utc_now()
        run.status = TaskStatus.COMPLETED
        run.result = "Task completed by the Phase 1 mock ANUM runtime."
        run.updated_at = utc_now()
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="final",
                summary=run.result,
                created_at=utc_now(),
            )
        )
        self._record_event(
            "agent_run.completed",
            context,
            run.id,
            {"task_id": task.id},
            correlation_id=task.id,
        )
        return run, None

    async def resume_after_approval(
        self,
        task: Task,
        run: AgentRun,
        approval: Approval,
        context: TenantContext,
    ) -> AgentRun:
        if approval.status != ApprovalStatus.APPROVED:
            task.status = TaskStatus.FAILED
            run.status = TaskStatus.FAILED
            run.updated_at = utc_now()
            task.updated_at = utc_now()
            run.steps.append(
                AgentRunStep(
                    id=new_id("step"),
                    type="tool_result",
                    summary="High-risk action was not approved.",
                    created_at=utc_now(),
                )
            )
            self._record_event(
                "agent_run.failed",
                context,
                run.id,
                {"task_id": task.id, "approval_id": approval.id},
                correlation_id=task.id,
            )
            return run

        tool_context = ToolExecutionContext(
            tenant=context, correlation_id=task.id, actor_id=context.user_id, task_id=task.id
        )
        tool_result, _audit_record = await execute_approved_tool(
            _HIGH_RISK_TOOL_REGISTRY,
            _HIGH_RISK_TOOL_NAME,
            {"prompt": task.prompt},
            tool_context,
        )

        if tool_result.status != ToolResultStatus.SUCCESS:
            task.status = TaskStatus.FAILED
            run.status = TaskStatus.FAILED
            run.updated_at = utc_now()
            task.updated_at = utc_now()
            run.steps.append(
                AgentRunStep(
                    id=new_id("step"),
                    type="tool_result",
                    summary=tool_result.error_message or "Approved high-risk action failed to execute.",
                    created_at=utc_now(),
                )
            )
            self._record_event(
                "agent_run.failed",
                context,
                run.id,
                {"task_id": task.id, "approval_id": approval.id},
                correlation_id=task.id,
            )
            return run

        task.status = TaskStatus.COMPLETED
        task.updated_at = utc_now()
        run.status = TaskStatus.COMPLETED
        run.result = "Approved high-risk action completed by the Phase 1 mock runtime."
        run.updated_at = utc_now()
        tool_summary = (tool_result.output or {}).get("summary", "Executed approved high-risk mock action.")
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="tool_result",
                summary=str(tool_summary),
                created_at=utc_now(),
            )
        )
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="final",
                summary=run.result,
                created_at=utc_now(),
            )
        )
        self._record_event(
            "agent_run.completed",
            context,
            run.id,
            {"task_id": task.id, "approval_id": approval.id},
            correlation_id=task.id,
        )
        return run

    def _requires_approval(self, prompt: str) -> bool:
        risky_terms = {"delete", "send", "publish", "spend", "pay", "permission", "credential"}
        normalized = prompt.lower()
        return any(term in normalized for term in risky_terms)

    def _record_event(
        self,
        event_type: str,
        context: TenantContext,
        subject: str,
        payload: dict[str, str],
        correlation_id: str,
    ) -> None:
        envelope = create_event(
            CanonicalEventName(event_type),
            context,
            subject,
            payload,
            correlation_id=correlation_id,
        )
        self.repository.record_event(envelope.event)
