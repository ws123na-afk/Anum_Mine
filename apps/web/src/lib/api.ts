import type { AgentRun, Approval, TenantContext, Task } from '@anum/contracts';

const apiBaseUrl = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

export const defaultTenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

interface ApiTask {
  id: string;
  title: string;
  prompt: string;
  status: Task['status'];
  tenant_id: string;
  workspace_id: string;
  created_at: string;
  updated_at: string;
}

interface ApiAgentRunStep {
  id: string;
  type: AgentRun['steps'][number]['type'];
  summary: string;
  created_at: string;
}

interface ApiAgentRun {
  id: string;
  task_id: string;
  status: AgentRun['status'];
  steps: ApiAgentRunStep[];
  result?: string;
}

interface ApiApproval {
  id: string;
  task_id: string;
  action: string;
  risk_level: Approval['riskLevel'];
  status: Approval['status'];
  reason: string;
  created_at: string;
}

interface ApiRunTaskResponse {
  task: ApiTask;
  run: ApiAgentRun;
  approval: ApiApproval | null;
}

interface ApiApprovalDecisionResponse {
  task: ApiTask;
  run: ApiAgentRun | null;
  approval: ApiApproval;
}

export interface RunTaskResult {
  task: Task;
  run: AgentRun;
  approval: Approval | null;
}

export interface ApprovalDecisionResult {
  task: Task;
  run: AgentRun | null;
  approval: Approval;
}

export async function createAndRunTask(prompt: string): Promise<RunTaskResult> {
  const created = await request<ApiTask>('/api/v1/tasks', {
    method: 'POST',
    body: JSON.stringify({ title: 'Web task', prompt }),
  });
  const result = await request<ApiRunTaskResponse>(`/api/v1/tasks/${created.id}/run`, {
    method: 'POST',
  });

  return {
    task: mapTask(result.task),
    run: mapRun(result.run),
    approval: result.approval ? mapApproval(result.approval) : null,
  };
}

export async function cancelTask(taskId: string): Promise<Task> {
  const task = await request<ApiTask>(`/api/v1/tasks/${taskId}/cancel`, {
    method: 'POST',
  });
  return mapTask(task);
}

export async function approveTask(approvalId: string): Promise<ApprovalDecisionResult> {
  const result = await request<ApiApprovalDecisionResponse>(`/api/v1/approvals/${approvalId}/approve`, {
    method: 'POST',
  });
  return {
    task: mapTask(result.task),
    run: result.run ? mapRun(result.run) : null,
    approval: mapApproval(result.approval),
  };
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      'x-tenant-id': defaultTenantContext.tenantId,
      'x-workspace-id': defaultTenantContext.workspaceId,
      'x-user-id': defaultTenantContext.userId,
      'x-user-roles': defaultTenantContext.roles.join(','),
      ...init.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`ANUM API request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

function mapTask(task: ApiTask): Task {
  return {
    id: task.id,
    title: task.title,
    prompt: task.prompt,
    status: task.status,
    tenantId: task.tenant_id,
    workspaceId: task.workspace_id,
    createdAt: task.created_at,
    updatedAt: task.updated_at,
  };
}

function mapRun(run: ApiAgentRun): AgentRun {
  return {
    id: run.id,
    taskId: run.task_id,
    status: run.status,
    result: run.result,
    steps: run.steps.map((step) => ({
      id: step.id,
      type: step.type,
      summary: step.summary,
      createdAt: step.created_at,
    })),
  };
}

function mapApproval(approval: ApiApproval): Approval {
  return {
    id: approval.id,
    taskId: approval.task_id,
    action: approval.action,
    riskLevel: approval.risk_level,
    status: approval.status,
    reason: approval.reason,
    createdAt: approval.created_at,
  };
}
