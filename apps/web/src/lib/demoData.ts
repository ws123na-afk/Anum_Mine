import type { AgentRun, Approval, Task } from '@anum/contracts';

const now = new Date().toISOString();

export const demoTask: Task = {
  id: 'task_demo_phase_1',
  title: 'Prepare ANUM Phase 1 slice',
  prompt: 'Create the first governed agent task flow.',
  status: 'waiting_approval',
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  createdAt: now,
  updatedAt: now,
};

export const demoRun: AgentRun = {
  id: 'run_demo_phase_1',
  taskId: demoTask.id,
  status: 'waiting_approval',
  steps: [
    {
      id: 'step_model',
      type: 'model_call',
      summary: 'Mock model prepared a deterministic task plan.',
      createdAt: now,
    },
    {
      id: 'step_approval',
      type: 'approval_wait',
      summary: 'Runtime paused before a high-risk action.',
      createdAt: now,
    },
  ],
};

export const demoApproval: Approval = {
  id: 'approval_demo_phase_1',
  taskId: demoTask.id,
  action: 'high_risk_tool_execution',
  riskLevel: 'high',
  status: 'pending',
  reason: 'Publishing or external side effects require explicit approval.',
  createdAt: now,
};
