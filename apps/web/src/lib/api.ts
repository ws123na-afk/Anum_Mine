import type { AgentRun, Approval, DomainEvent, TenantContext, Task } from '@anum/contracts';

const apiBaseUrl = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

export const defaultTenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  roles: ['owner', 'member'],
};

export interface ApiTask {
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

export interface ApiAgentRun {
  id: string;
  task_id: string;
  status: AgentRun['status'];
  steps: ApiAgentRunStep[];
  result?: string;
}

export interface ApiApproval {
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
  return decideApproval(approvalId, 'approve');
}

export interface MemoryNote { id: string; task_id: string; content: string; provenance: { source_type: string; source_id: string | null; created_by_user_id: string; created_at: string }; retention: { kind: string; expires_at: string | null }; created_at: string; }
export interface FileRecord { id: string; name: string; content_type: string; size_bytes: number; sha256: string; created_by: string; created_at: string; }
export interface SkillVersion { id: string; skill_id: string; version: string; name: string; description: string; required_tools: string[]; risk_level: string; created_at: string; }
export interface SkillInstallation { id: string; skill_id: string; version: string; approved_tools: string[]; enabled: boolean; installed_at: string; }
export interface WorkspaceInfo { id: string; tenant_id: string; name: string; created_at: string; updated_at: string; }
export interface OnboardingStatus { complete: boolean; tenant: { id: string; name: string } | null; workspace: WorkspaceInfo | null; membership: { role: string } | null; model_configured: boolean; }
export interface ModelConfig { provider: string; model: string; base_url: string; credential_configured: boolean; credential_hint: string | null; updated_at: string; }
export interface NotificationPreferences { task_completed: boolean; approval_required: boolean; run_failed: boolean; automation_failed: boolean; email_enabled: boolean; desktop_enabled: boolean; }

export async function ensureLocalSession(): Promise<void> {
  if (sessionStorage.getItem('anum_access_token')) return;
  const response = await fetch(`${apiBaseUrl}/api/v1/auth/local/session`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ tenant_id: defaultTenantContext.tenantId, workspace_id: defaultTenantContext.workspaceId, user_id: defaultTenantContext.userId }) });
  if (!response.ok) return;
  const result = await response.json() as { access_token: string }; sessionStorage.setItem('anum_access_token', result.access_token);
}
export async function getOnboarding(): Promise<OnboardingStatus> { return request('/api/v1/onboarding', { method: 'GET' }); }
export async function completeOnboarding(organizationName: string, workspaceName: string): Promise<OnboardingStatus> { return request('/api/v1/onboarding', { method: 'PUT', body: JSON.stringify({ organization_name: organizationName, workspace_name: workspaceName }) }); }
export async function getModelConfig(): Promise<ModelConfig> { return request('/api/v1/model-config', { method: 'GET' }); }
export async function saveModelConfig(provider: string, model: string, baseUrl: string, apiKey?: string): Promise<ModelConfig> { return request('/api/v1/model-config', { method: 'PUT', body: JSON.stringify({ provider, model, base_url: baseUrl, api_key: apiKey || null }) }); }
export async function getNotificationPreferences(): Promise<NotificationPreferences> { return request('/api/v1/notification-preferences', { method: 'GET' }); }
export async function saveNotificationPreferences(value: NotificationPreferences): Promise<NotificationPreferences> { return request('/api/v1/notification-preferences', { method: 'PUT', body: JSON.stringify(value) }); }

export async function getTasks(): Promise<Task[]> { return (await request<ApiTask[]>('/api/v1/tasks', { method: 'GET' })).map(mapTask); }
export async function getApprovals(): Promise<Approval[]> { return (await request<ApiApproval[]>('/api/v1/approvals', { method: 'GET' })).map(mapApproval); }
export async function getRun(runId: string): Promise<AgentRun> { return mapRun(await request<ApiAgentRun>(`/api/v1/agent-runs/${runId}`, { method: 'GET' })); }
export async function resumeRun(runId: string): Promise<RunTaskResult> {
  const result = await request<ApiRunTaskResponse>(`/api/v1/agent-runs/${runId}/resume`, { method: 'POST' });
  return { task: mapTask(result.task), run: mapRun(result.run), approval: result.approval ? mapApproval(result.approval) : null };
}
export async function getMemories(): Promise<MemoryNote[]> { return request('/api/v1/memories', { method: 'GET' }); }
export async function createMemory(taskId: string, content: string): Promise<MemoryNote> { return request('/api/v1/memories', { method: 'POST', body: JSON.stringify({ task_id: taskId, content, source_type: 'user', retention: { kind: 'task' } }) }); }
export async function deleteMemory(id: string): Promise<void> { await requestVoid(`/api/v1/memories/${id}`, { method: 'DELETE' }); }
export async function getFiles(): Promise<FileRecord[]> { return request('/api/v1/files', { method: 'GET' }); }
export async function uploadFile(file: File): Promise<FileRecord> {
  return request('/api/v1/files', { method: 'POST', body: file, headers: { 'content-type': file.type || 'application/octet-stream', 'x-file-name': file.name } });
}
export async function deleteFile(id: string): Promise<void> { await requestVoid(`/api/v1/files/${id}`, { method: 'DELETE' }); }
export async function downloadFile(id: string, name: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/files/${id}/content`, { headers: tenantHeaders() });
  if (!response.ok) throw new Error(`ANUM API request failed: ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
}
export async function getSkillVersions(): Promise<SkillVersion[]> { return request('/api/v1/skills/versions', { method: 'GET' }); }
export async function getSkillInstallations(): Promise<SkillInstallation[]> { return request('/api/v1/skills/installations', { method: 'GET' }); }
export async function installSkill(skillId: string, version: string, approvedTools: string[]): Promise<SkillInstallation> { return request('/api/v1/skills/installations', { method: 'POST', body: JSON.stringify({ skill_id: skillId, version, approved_tools: approvedTools }) }); }
export async function getCurrentWorkspace(): Promise<WorkspaceInfo> { return request('/api/v1/workspaces/current', { method: 'GET' }); }

export interface IntegrationHealth {
  id: string;
  name: string;
  kind: 'database' | 'identity' | 'event_bus' | 'workflow' | 'cache' | 'object_storage';
  status: 'connected' | 'degraded' | 'configured' | 'disabled';
  endpoint: string;
  latency_ms: number | null;
  detail: string;
  credentials: { configured: boolean; source: string; scopes: string[]; expires_at: string | null };
}

export async function rejectTask(approvalId: string): Promise<ApprovalDecisionResult> {
  return decideApproval(approvalId, 'reject');
}

export async function getIntegrations(): Promise<IntegrationHealth[]> {
  return request<IntegrationHealth[]>('/api/v1/integrations', { method: 'GET' });
}

export interface AutomationStep { id: string; name: string; action: string; status: string; attempt: number; output: Record<string, unknown>; error: string | null; }
export interface AutomationWorkflow { id: string; name: string; description: string; status: string; version: number; steps: Array<{ id: string; name: string; action: string; input: Record<string, unknown>; max_attempts: number }>; created_at: string; updated_at: string; }
export interface AutomationRun { id: string; workflow_id: string; status: string; idempotency_key: string | null; retry_of: string | null; current_step: number; steps: AutomationStep[]; created_at: string; updated_at: string; }

export async function getAutomationWorkflows(): Promise<AutomationWorkflow[]> { return request('/api/v1/automation/workflows', { method: 'GET' }); }
export async function getAutomationRuns(): Promise<AutomationRun[]> { return request('/api/v1/automation/runs', { method: 'GET' }); }
export async function createAutomationWorkflow(name: string, action: string): Promise<AutomationWorkflow> {
  return request('/api/v1/automation/workflows', { method: 'POST', body: JSON.stringify({ name, description: 'Created from the resumable agent workbench', steps: [{ id: 'execute', name: 'Execute work', action, input: {}, max_attempts: 3 }] }) });
}
export async function startAutomationRun(workflowId: string, idempotencyKey: string): Promise<AutomationRun> { return request(`/api/v1/automation/workflows/${workflowId}/runs`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } }); }
export async function transitionAutomationRun(runId: string, action: 'cancel' | 'resume' | 'retry'): Promise<AutomationRun> { return request(`/api/v1/automation/runs/${runId}/${action}`, { method: 'POST' }); }

export async function streamEvents(
  onEvent: (event: DomainEvent) => void,
  signal: AbortSignal,
  onConnected: () => void,
  lastEventId?: string,
): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/events/stream`, {
    headers: {
      ...tenantHeaders(),
      'x-tenant-id': defaultTenantContext.tenantId,
      'x-workspace-id': defaultTenantContext.workspaceId,
      'x-user-id': defaultTenantContext.userId,
      'x-user-roles': defaultTenantContext.roles.join(','),
      ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
    },
    signal,
  });
  if (!response.ok || !response.body) throw new Error(`ANUM event stream failed: ${response.status}`);
  onConnected();

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (!signal.aborted) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const data = frame.split('\n').find((line) => line.startsWith('data: '));
      if (data) onEvent(JSON.parse(data.slice(6)) as DomainEvent);
    }
  }
}

async function decideApproval(
  approvalId: string,
  decision: 'approve' | 'reject',
): Promise<ApprovalDecisionResult> {
  const result = await request<ApiApprovalDecisionResponse>(`/api/v1/approvals/${approvalId}/${decision}`, {
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
      ...tenantHeaders(),
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

async function requestVoid(path: string, init: RequestInit): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers: { ...tenantHeaders(), ...init.headers } });
  if (!response.ok) throw new Error(`ANUM API request failed: ${response.status}`);
}

function tenantHeaders(): Record<string, string> {
  const token = sessionStorage.getItem('anum_access_token');
  return { 'x-tenant-id': defaultTenantContext.tenantId, 'x-workspace-id': defaultTenantContext.workspaceId, 'x-user-id': defaultTenantContext.userId, 'x-user-roles': defaultTenantContext.roles.join(','), ...(token ? { authorization: `Bearer ${token}` } : {}) };
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
