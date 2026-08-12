from .model_gateway import MockModelGateway
from .events import CanonicalEventName, create_event
from .repository import AnumRepository
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
            approval = Approval(
                id=new_id("approval"),
                task_id=task.id,
                action="high_risk_tool_execution",
                risk_level=RiskLevel.HIGH,
                status=ApprovalStatus.PENDING,
                reason="The task appears to request an external, destructive, publishing, spending, or permission-changing action.",
                created_at=utc_now(),
            )
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
            self.repository.save_approval(approval)
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

        task.status = TaskStatus.COMPLETED
        task.updated_at = utc_now()
        run.status = TaskStatus.COMPLETED
        run.result = "Approved high-risk action completed by the Phase 1 mock runtime."
        run.updated_at = utc_now()
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="tool_result",
                summary="Executed approved high-risk mock action.",
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
