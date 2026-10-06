from datetime import datetime, timedelta

from .agent_planning import AgentPlanner
from .agent_skills import SkillRegistry, default_skill_registry
from .agent_tools import (
    ToolCall,
    ToolPolicy,
    ToolPolicyDecision,
    ToolPolicyOutcome,
    ToolRegistry,
    ToolResult,
    default_tool_registry,
)
from .approval_integrity import display_arguments, is_expired, payload_hash
from .audit import AuditRecord
from .events import CanonicalEventName, create_event
from .model_gateway import ModelGateway
from .repository import AnumRepository
from .schemas import (
    AgentRun,
    AgentRunStep,
    Approval,
    ApprovalStatus,
    RunPhase,
    Task,
    TaskStatus,
    TenantContext,
    WorkspaceApprovalPolicy,
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
        approval_ttl_seconds: float | None = None,
    ) -> None:
        if approval_ttl_seconds is None:
            from .settings import settings

            approval_ttl_seconds = settings.approval_ttl_seconds
        self.approval_ttl = timedelta(seconds=approval_ttl_seconds)
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

    def workspace_policy(self, context: TenantContext) -> WorkspaceApprovalPolicy:
        """The workspace's approval policy (defaults when none is stored)."""
        return self.repository.get_approval_policy(context)

    def evaluate(self, call: ToolCall, context: TenantContext) -> ToolPolicyDecision:
        """Tool policy for ``call`` under the workspace's approval policy (outside the model)."""
        return self.tool_policy.evaluate(
            call, self.tools.definition(call.name), context, self.workspace_policy(context)
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
        decision = self.evaluate(call, context)
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
            _, approval = self._pause_for_approval(task, run, context, call, decision)
            return approval
        return None

    async def execute_checkpoint(self, task: Task, run: AgentRun, context: TenantContext) -> AgentRun:
        """Execute the planned tool call of a ``tool_ready`` run that policy allows."""
        if run.checkpoint.phase != RunPhase.TOOL_READY or not run.checkpoint.tool_call:
            raise ValueError("Run has no executable checkpoint")
        call = ToolCall.model_validate(run.checkpoint.tool_call)
        self._mark_executing(task, run)
        result = await self.tools.execute(call, context)
        return self._complete(task, run, context, result)

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
            if approval.status == ApprovalStatus.EXPIRED:
                self._fail(
                    task,
                    run,
                    context,
                    "The approval expired before anyone approved it; the action was not executed.",
                    approval.id,
                )
                return None
            if approval.status != ApprovalStatus.APPROVED:
                self._fail(task, run, context, "High-risk action was not approved.", approval.id)
                return None
            if (
                approval.expires_at is not None
                and approval.decided_at is not None
                and approval.decided_at > approval.expires_at
            ):
                self._fail(
                    task,
                    run,
                    context,
                    "The approval was decided after it expired; the action was not executed.",
                    approval.id,
                )
                return None
            # Bind the decision to the payload: the checkpointed call must still hash to
            # what the approver was shown. Anything else is never executed.
            current_hash = self.checkpoint_payload_hash(task, run)
            if approval.payload_hash is None or current_hash != approval.payload_hash:
                self._reject_payload_mismatch(task, run, context, approval, current_hash)
                return None
            call = ToolCall.model_validate(run.checkpoint.tool_call)
            # The approver was shown this integration target; refuse if it has changed.
            definition = self.tools.definition(call.name)
            current_target = definition.target if definition is not None else None
            if approval.target is not None and approval.target != current_target:
                self._reject_target_mismatch(task, run, context, approval, current_target)
                return None
            decision = self.evaluate(call, context)
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
            decision = self.evaluate(call, context)
            if decision.outcome == ToolPolicyOutcome.BLOCK:
                self._fail(task, run, context, decision.reason)
                return None
            if decision.outcome == ToolPolicyOutcome.REQUIRE_APPROVAL:
                self._pause_for_approval(task, run, context, call, decision)
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
        return self._complete(task, run, context, result, approval_id)

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
        decision = self.evaluate(call, context)
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
        return self._complete(task, run, context, result)

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

    def checkpoint_payload_hash(self, task: Task, run: AgentRun) -> str | None:
        """The payload hash of the run's checkpointed tool call (``None`` without one)."""
        if not run.checkpoint.tool_call:
            return None
        call = ToolCall.model_validate(run.checkpoint.tool_call)
        return payload_hash(call, task_id=task.id, run_id=run.id, step_id=run.checkpoint.last_step_id)

    def expire_if_due(
        self, task: Task, approval: Approval, context: TenantContext, now: datetime | None = None
    ) -> bool:
        """Mark a lapsed pending approval ``expired`` (and record it). True if it lapsed."""
        now = now or utc_now()
        if not is_expired(approval, now):
            return False
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        self.repository.save_approval(approval)
        self._record_event(
            CanonicalEventName.APPROVAL_EXPIRED.value,
            context,
            approval.id,
            {"task_id": task.id},
            task.id,
        )
        self._audit(
            context,
            "approval.expired",
            approval.id,
            "expired",
            task.id,
            {
                "task_id": task.id,
                "tool": approval.action,
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            },
            actor="system",
        )
        return True

    def _reject_payload_mismatch(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        approval: Approval,
        current_hash: str | None,
    ) -> None:
        self._audit(
            context,
            "approval.payload_mismatch",
            approval.id,
            "denied",
            task.id,
            {
                "task_id": task.id,
                "run_id": run.id,
                "tool": approval.action,
                "approved_hash": approval.payload_hash,
                "checkpoint_hash": current_hash,
                "decided_by": approval.decided_by,
            },
        )
        self._fail(
            task,
            run,
            context,
            "The tool call no longer matches what was approved (payload hash mismatch); "
            "the action was not executed.",
            approval.id,
        )

    def _reject_target_mismatch(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        approval: Approval,
        current_target: str | None,
    ) -> None:
        self._audit(
            context,
            "approval.target_mismatch",
            approval.id,
            "denied",
            task.id,
            {
                "task_id": task.id,
                "run_id": run.id,
                "tool": approval.action,
                "approved_target": approval.target,
                "current_target": current_target,
                "decided_by": approval.decided_by,
            },
        )
        self._fail(
            task,
            run,
            context,
            "The integration target changed after approval "
            f"(approved {approval.target}, now {current_target or 'none'}); "
            "the action was not executed.",
            approval.id,
        )

    def _audit(
        self,
        context: TenantContext,
        action: str,
        target: str,
        outcome: str,
        correlation_id: str,
        metadata: dict[str, object],
        *,
        actor: str | None = None,
    ) -> None:
        self.repository.record_audit(
            AuditRecord(
                id=new_id("audit"),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor=actor or context.user_id,
                action=action,
                target=target,
                outcome=outcome,
                correlation_id=correlation_id,
                created_at=utc_now(),
                metadata=metadata,
            )
        )

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
        decision: ToolPolicyDecision,
    ) -> tuple[AgentRun, Approval]:
        created_at = utc_now()
        definition = self.tools.definition(call.name)
        approval = Approval(
            id=new_id("approval"),
            task_id=task.id,
            action=call.name,
            risk_level=decision.risk_level,
            status=ApprovalStatus.PENDING,
            reason=f"{decision.reason} Proposed action: {task.prompt[:240]}",
            created_at=created_at,
            run_id=run.id,
            step_id=run.checkpoint.last_step_id,
            arguments=display_arguments(call.arguments),
            payload_hash=self.checkpoint_payload_hash(task, run)
            or payload_hash(call, task_id=task.id, run_id=run.id, step_id=run.checkpoint.last_step_id),
            expires_at=created_at + self.approval_ttl,
            requested_by=context.user_id,
            target=definition.target if definition is not None else None,
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
        requested_payload = {
            "task_id": task.id,
            "tool": call.name,
            "risk_level": approval.risk_level.value,
        }
        if approval.target:
            requested_payload["target"] = approval.target
        self._record_event(
            "approval.requested",
            context,
            approval.id,
            requested_payload,
            task.id,
        )
        return run, approval

    def _complete(
        self,
        task: Task,
        run: AgentRun,
        context: TenantContext,
        result: ToolResult,
        approval_id: str | None = None,
    ) -> AgentRun:
        result_summary = result.summary
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
                    metadata={"status": result.status, "truncated": result.truncated},
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
