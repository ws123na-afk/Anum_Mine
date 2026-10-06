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

    def new_run(self, task: Task, *, status: TaskStatus = TaskStatus.RUNNING) -> AgentRun:
        """A fresh run for ``task`` at the planning checkpoint (not yet saved)."""
        now = utc_now()
        return AgentRun(
            id=new_id("run"),
            task_id=task.id,
            status=status,
            created_at=now,
            updated_at=now,
        )

    async def run_task(self, task: Task, context: TenantContext) -> tuple[AgentRun, Approval | None]:
        """Plan and, when policy allows, execute in one call (the inline backend)."""
        run = self.new_run(task)
        approval = await self.plan_run(task, run, context)
        if run.checkpoint.phase == RunPhase.TOOL_READY:
            await self.execute_checkpoint(task, run, context)
        return run, approval

    async def plan_run(self, task: Task, run: AgentRun, context: TenantContext) -> Approval | None:
        """Plan ``run`` and persist the tool-ready checkpoint.

        Afterwards the run is at ``tool_ready`` (policy allows the call), paused at
        ``waiting_approval`` (the returned approval), or ``failed`` (policy blocks it).
        Nothing is saved until planning finishes, so a crash mid-planning leaves the
        run at ``planning`` and planning simply runs again.
        """
        if run.checkpoint.phase != RunPhase.PLANNING:
            raise ValueError("Run has already been planned")
        task.status = run.status = TaskStatus.RUNNING
        task.updated_at = run.updated_at = utc_now()

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
            self._fail(task, run, context, decision.reason)
            return None
        if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
            _, approval = self._pause_for_approval(task, run, context, call, decision.reason)
            return approval
        return None

    async def execute_checkpoint(self, task: Task, run: AgentRun, context: TenantContext) -> AgentRun:
        """Execute the planned tool call of a ``tool_ready`` run that policy allows."""
        if run.checkpoint.phase != RunPhase.TOOL_READY or not run.checkpoint.tool_call:
            raise ValueError("Run has no executable checkpoint")
        call = ToolCall.model_validate(run.checkpoint.tool_call)
        self._mark_executing(task, run)
        result = await self.tools.execute(call, context)
        return self._complete(task, run, context, result.summary)

    def check_resumable(self, task: Task, run: AgentRun) -> None:
        """Raise ``ValueError`` unless ``run`` is stranded at an executable checkpoint."""
        if task.status == TaskStatus.CANCELLED or run.checkpoint.phase == RunPhase.CANCELLED:
            raise ValueError("Cancelled runs cannot be resumed")
        if run.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise ValueError("Terminal runs cannot be resumed")
        if run.status == TaskStatus.WAITING_APPROVAL:
            raise ValueError("Run requires an approval decision")
        if run.checkpoint.phase != RunPhase.TOOL_READY or not run.checkpoint.tool_call:
            raise ValueError("Run has no executable checkpoint")

    async def resume_run(
        self, task: Task, run: AgentRun, context: TenantContext, *, record_resume: bool = True
    ) -> AgentRun:
        self.check_resumable(task, run)
        call = self.begin_execution(task, run, context, record_resume=record_resume)
        if call is None:
            return run
        return await self.finish_execution(task, run, context, call)

    def begin_execution(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        *,
        approval: Approval | None = None,
        record_resume: bool = False,
    ) -> ToolCall | None:
        """Re-check policy for the checkpointed call and mark the run ``executing``.

        Returns the call to execute, or ``None`` when the run was failed (blocked or
        rejected) or paused for approval instead. Durable workers commit this state
        before running the tool, so a crash mid-tool is visible as ``executing``.
        """
        if approval is not None:
            if approval.status != ApprovalStatus.APPROVED:
                self._fail(task, run, context, "High-risk action was not approved.", approval.id)
                return None
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
                return None
        else:
            if not run.checkpoint.tool_call:
                raise ValueError("Run has no executable checkpoint")
            call = ToolCall.model_validate(run.checkpoint.tool_call)
            decision = self.tool_policy.evaluate(call, self.tools.definition(call.name), context)
            if decision.outcome == ToolPolicyOutcome.BLOCK:
                self._fail(task, run, context, decision.reason)
                return None
            if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
                self._pause_for_approval(task, run, context, call, decision.reason)
                return None

        self._mark_executing(task, run)
        if record_resume:
            self._record_event(
                "agent_run.resumed", context, run.id,
                {"task_id": task.id, "checkpoint_version": str(run.checkpoint.version)}, task.id,
            )
        return call

    async def finish_execution(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        call: ToolCall,
        approval_id: str | None = None,
    ) -> AgentRun:
        """Execute ``call`` through the tool registry and complete the run."""
        result = await self.tools.execute(call, context)
        return self._complete(task, run, context, result.summary, approval_id)

    async def recover_interrupted_execution(
        self, task: Task, run: AgentRun, context: TenantContext
    ) -> AgentRun:
        """Settle a run whose worker stopped while its tool call was executing.

        The tool may or may not have taken effect. A call to a tool declared
        ``idempotent`` that policy still allows without approval is executed again
        (at-least-once delivery). Anything else, including every approved high-risk
        call, is never repeated automatically; the run fails so a person can check
        the outcome and decide.
        """
        if run.checkpoint.phase != RunPhase.EXECUTING or not run.checkpoint.tool_call:
            raise ValueError("Run is not interrupted mid-execution")
        call = ToolCall.model_validate(run.checkpoint.tool_call)
        definition = self.tools.definition(call.name)
        decision = self.tool_policy.evaluate(call, definition, context)
        if decision.outcome != ToolPolicyOutcome.ALLOW or definition is None or not definition.idempotent:
            self._fail(
                task,
                run,
                context,
                "The worker stopped while this action was executing and its outcome is "
                "unknown. It was not repeated automatically; check the target and run the "
                "task again if needed.",
                run.checkpoint.approval_id,
            )
            return run
        self._record_event(
            "agent_run.resumed", context, run.id,
            {"task_id": task.id, "checkpoint_version": str(run.checkpoint.version)}, task.id,
        )
        run.checkpoint.version += 1
        result = await self.tools.execute(call, context)
        return self._complete(task, run, context, result.summary)

    async def resume_after_approval(
        self,
        task: Task,
        run: AgentRun,
        approval: Approval,
        context: TenantContext,
    ) -> AgentRun:
        call = self.begin_execution(task, run, context, approval=approval)
        if call is None:
            return run
        await self.finish_execution(task, run, context, call, approval.id)
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
