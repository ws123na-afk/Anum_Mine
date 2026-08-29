from .agent_planning import AgentPlanner
from .agent_skills import SkillRegistry, default_skill_registry
from .agent_tools import (
    ToolCall,
    ToolPolicy,
    ToolPolicyOutcome,
    ToolRegistry,
    default_tool_registry,
)
from .events import CanonicalEventName, create_event
from .model_gateway import ModelGateway
from .repository import AnumRepository
from .schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RiskLevel,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    new_id,
    utc_now,
)


class AgentRuntime:
    def __init__(
        self,
        model_gateway: ModelGateway,
        repository: AnumRepository,
        *,
        skills: SkillRegistry | None = None,
        tools: ToolRegistry | None = None,
        tool_policy: ToolPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.tools = tools or default_tool_registry()
        self.skills = skills or default_skill_registry()
        self.tool_policy = tool_policy or ToolPolicy(self.tools.names)
        self.planner = AgentPlanner(model_gateway, self.skills, self.tools)

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

        planned = await self.planner.plan(task)
        model_step = AgentRunStep(
                id=new_id("step"),
                type="model_call",
                summary=planned.model_response.text,
                created_at=utc_now(),
                metadata={
                    "usage": planned.model_response.usage.model_dump(),
                    "selected_skills": planned.plan.skill_ids,
                },
            )
        run.steps.append(model_step)

        call = planned.plan.tool_calls[0]
        decision = self.tool_policy.evaluate(call, self.tools.definition(call.name), context)
        proposal_step = AgentRunStep(
                id=new_id("step"),
                type="tool_proposal",
                summary=f"Proposed tool call: {call.name}",
                created_at=utc_now(),
                metadata={
                    "tool": call.name,
                    "risk_level": decision.risk_level.value,
                    "policy_outcome": decision.outcome.value,
                },
            )
        run.steps.append(proposal_step)
        run.checkpoint.phase = RunPhase.TOOL_READY
        run.checkpoint.version += 1
        run.checkpoint.selected_skills = list(planned.plan.skill_ids)
        run.checkpoint.tool_call = call.model_dump(mode="json")
        run.checkpoint.last_step_id = proposal_step.id
        self.repository.save_task(task)
        self.repository.save_run(run)

        if decision.outcome == ToolPolicyOutcome.BLOCK:
            return self._fail_blocked(task, run, context, decision.reason)
        if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
            return self._pause_for_approval(task, run, context, call, decision.reason)

        self._mark_executing(task, run)
        result = await self.tools.execute(call, context)
        return self._complete(task, run, context, result.summary), None

    async def resume_run(
        self, task: Task, run: AgentRun, context: TenantContext
    ) -> AgentRun:
        if task.status == TaskStatus.CANCELLED or run.checkpoint.phase == RunPhase.CANCELLED:
            raise ValueError("Cancelled runs cannot be resumed")
        if run.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise ValueError("Terminal runs cannot be resumed")
        if run.status == TaskStatus.WAITING_APPROVAL:
            raise ValueError("Run requires an approval decision")
        if run.checkpoint.phase != RunPhase.TOOL_READY or not run.checkpoint.tool_call:
            raise ValueError("Run has no executable checkpoint")

        call = ToolCall.model_validate(run.checkpoint.tool_call)
        decision = self.tool_policy.evaluate(call, self.tools.definition(call.name), context)
        if decision.outcome == ToolPolicyOutcome.BLOCK:
            self._fail(task, run, context, decision.reason)
            return run
        if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
            _, approval = self._pause_for_approval(task, run, context, call, decision.reason)
            run.checkpoint.approval_id = approval.id
            return run

        self._mark_executing(task, run)
        self._record_event(
            "agent_run.resumed", context, run.id,
            {"task_id": task.id, "checkpoint_version": str(run.checkpoint.version)}, task.id,
        )
        result = await self.tools.execute(call, context)
        return self._complete(task, run, context, result.summary)

    async def resume_after_approval(
        self,
        task: Task,
        run: AgentRun,
        approval: Approval,
        context: TenantContext,
    ) -> AgentRun:
        if approval.status != ApprovalStatus.APPROVED:
            self._fail(task, run, context, "High-risk action was not approved.", approval.id)
            return run

        call = (
            ToolCall.model_validate(run.checkpoint.tool_call)
            if run.checkpoint.tool_call
            else ToolCall(name=approval.action, arguments={"action": task.prompt})
        )
        decision = self.tool_policy.evaluate(call, self.tools.definition(call.name), context)
        if decision.outcome == ToolPolicyOutcome.BLOCK:
            self._fail(
                task,
                run,
                context,
                f"Approved action blocked during policy re-evaluation: {decision.reason}",
                approval.id,
            )
            return run

        self._mark_executing(task, run)
        result = await self.tools.execute(call, context)
        self._complete(task, run, context, result.summary, approval.id)
        return run

    def _mark_executing(self, task: Task, run: AgentRun) -> None:
        task.status = run.status = TaskStatus.RUNNING
        run.checkpoint.phase = RunPhase.EXECUTING
        run.checkpoint.version += 1
        task.updated_at = run.updated_at = utc_now()
        self.repository.save_task(task)
        self.repository.save_run(run)

    def _pause_for_approval(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        call: ToolCall,
        reason: str,
    ) -> tuple[AgentRun, Approval]:
        approval = Approval(
            id=new_id("approval"),
            task_id=task.id,
            action=call.name,
            risk_level=RiskLevel.HIGH,
            status=ApprovalStatus.PENDING,
            reason=f"{reason} Proposed action: {task.prompt[:240]}",
            created_at=utc_now(),
        )
        task.status = run.status = TaskStatus.WAITING_APPROVAL
        run.checkpoint.phase = RunPhase.WAITING_APPROVAL
        run.checkpoint.version += 1
        run.checkpoint.approval_id = approval.id
        task.updated_at = run.updated_at = utc_now()
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="approval_wait",
                summary=f"Paused for approval before calling {call.name}.",
                created_at=utc_now(),
                metadata={"approval_id": approval.id, "tool": call.name},
            )
        )
        self.repository.save_approval(approval)
        self._record_event(
            "approval.requested",
            context,
            approval.id,
            {"task_id": task.id, "tool": call.name},
            task.id,
        )
        return run, approval

    def _complete(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        result_summary: str,
        approval_id: str | None = None,
    ) -> AgentRun:
        task.status = run.status = TaskStatus.COMPLETED
        run.checkpoint.phase = RunPhase.COMPLETED
        run.checkpoint.version += 1
        task.updated_at = run.updated_at = utc_now()
        run.result = result_summary
        run.steps.extend(
            [
                AgentRunStep(
                    id=new_id("step"),
                    type="tool_result",
                    summary=result_summary,
                    created_at=utc_now(),
                ),
                AgentRunStep(
                    id=new_id("step"),
                    type="final",
                    summary=result_summary,
                    created_at=utc_now(),
                ),
            ]
        )
        payload = {"task_id": task.id}
        if approval_id:
            payload["approval_id"] = approval_id
        self._record_event("agent_run.completed", context, run.id, payload, task.id)
        return run

    def _fail_blocked(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        reason: str,
    ) -> tuple[AgentRun, None]:
        self._fail(task, run, context, reason)
        return run, None

    def _fail(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        reason: str,
        approval_id: str | None = None,
    ) -> None:
        task.status = run.status = TaskStatus.FAILED
        run.checkpoint.phase = RunPhase.FAILED
        run.checkpoint.version += 1
        task.updated_at = run.updated_at = utc_now()
        run.steps.append(
            AgentRunStep(
                id=new_id("step"),
                type="tool_result",
                summary=reason,
                created_at=utc_now(),
            )
        )
        payload = {"task_id": task.id}
        if approval_id:
            payload["approval_id"] = approval_id
        self._record_event("agent_run.failed", context, run.id, payload, task.id)

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
