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

export interface ApiAgentRunStep {
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

export async function getTask(taskId: string): Promise<Task> {
  return mapTask(await request<ApiTask>(`/api/v1/tasks/${taskId}`));
}

export interface ApiDomainEvent {
  id: string;
  type: string;
  version: number;
  tenant_id: string;
  workspace_id?: string | null;
  subject: string;
  correlation_id: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export async function listEvents(): Promise<DomainEvent[]> {
  const events = await request<ApiDomainEvent[]>('/api/v1/events');
  return events.map(mapEvent);
}

export function mapEvent(event: ApiDomainEvent): DomainEvent {
  return {
    id: event.id,
    type: event.type,
    version: event.version,
    tenantId: event.tenant_id,
    workspaceId: event.workspace_id ?? undefined,
    subject: event.subject,
    correlationId: event.correlation_id,
    createdAt: event.created_at,
    payload: event.payload,
  };
}

export async function getAgentRun(runId: string): Promise<AgentRun> {
  return mapRun(await request<ApiAgentRun>(`/api/v1/agent-runs/${runId}`));
}

export async function cancelTask(taskId: string): Promise<Task> {
  const task = await request<ApiTask>(`/api/v1/tasks/${taskId}/cancel`, {
    method: 'POST',
  });
  return mapTask(task);
}

export async function listApprovals(): Promise<Approval[]> {
  const approvals = await request<ApiApproval[]>('/api/v1/approvals');
  return approvals.map(mapApproval);
}

export async function decideApproval(
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

export async function approveTask(approvalId: string): Promise<ApprovalDecisionResult> {
  return decideApproval(approvalId, 'approve');
}

export type RetentionKind = 'task' | 'expires_at' | 'indefinite';

export interface MemoryProvenance {
  sourceType: string;
  sourceId: string | null;
  createdByUserId: string;
  createdAt: string;
  metadata: Record<string, unknown>;
}

export interface RetentionPolicy {
  kind: RetentionKind;
  expiresAt: string | null;
}

export interface MemoryNote {
  id: string;
  tenantId: string;
  workspaceId: string;
  taskId: string;
  content: string;
  provenance: MemoryProvenance;
  retention: RetentionPolicy;
  createdAt: string;
}

export interface ApiMemoryNote {
  id: string;
  tenant_id: string;
  workspace_id: string;
  task_id: string;
  content: string;
  provenance: {
    source_type: string;
    source_id: string | null;
    created_by_user_id: string;
    created_at: string;
    metadata: Record<string, unknown>;
  };
  retention: {
    kind: RetentionKind;
    expires_at: string | null;
  };
  created_at: string;
}

export function mapMemory(note: ApiMemoryNote): MemoryNote {
  return {
    id: note.id,
    tenantId: note.tenant_id,
    workspaceId: note.workspace_id,
    taskId: note.task_id,
    content: note.content,
    provenance: {
      sourceType: note.provenance.source_type,
      sourceId: note.provenance.source_id,
      createdByUserId: note.provenance.created_by_user_id,
      createdAt: note.provenance.created_at,
      metadata: note.provenance.metadata,
    },
    retention: {
      kind: note.retention.kind,
      expiresAt: note.retention.expires_at,
    },
    createdAt: note.created_at,
  };
}

export interface MemoryListFilters {
  taskId?: string;
  query?: string;
  sourceTypes?: string[];
  includeExpired?: boolean;
}

export async function listMemories(filters: MemoryListFilters = {}): Promise<MemoryNote[]> {
  const params = new URLSearchParams();
  if (filters.taskId) params.set('task_id', filters.taskId);
  if (filters.query) params.set('query', filters.query);
  if (filters.includeExpired) params.set('include_expired', 'true');
  for (const sourceType of filters.sourceTypes ?? []) {
    params.append('source_type', sourceType);
  }
  const search = params.toString();
  const notes = await request<ApiMemoryNote[]>(`/api/v1/memories${search ? `?${search}` : ''}`);
  return notes.map(mapMemory);
}

export interface CreateMemoryInput {
  taskId: string;
  content: string;
  sourceType: string;
  sourceId?: string;
  sourceMetadata?: Record<string, unknown>;
  retention?: { kind: RetentionKind; expiresAt?: string };
}

export async function createMemory(input: CreateMemoryInput): Promise<MemoryNote> {
  const note = await request<ApiMemoryNote>('/api/v1/memories', {
    method: 'POST',
    body: JSON.stringify({
      task_id: input.taskId,
      content: input.content,
      source_type: input.sourceType,
      source_id: input.sourceId,
      source_metadata: input.sourceMetadata ?? {},
      retention: input.retention
        ? { kind: input.retention.kind, expires_at: input.retention.expiresAt ?? null }
        : undefined,
    }),
  });
  return mapMemory(note);
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await request<void>(`/api/v1/memories/${memoryId}`, { method: 'DELETE' });
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly correlationId?: string;

  constructor(message: string, status: number, code?: string, correlationId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
  }
}

interface ErrorEnvelope {
  error: { code: string; message: string; correlation_id: string };
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
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
    const envelope = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    if (envelope?.error) {
      throw new ApiError(envelope.error.message, response.status, envelope.error.code, envelope.error.correlation_id);
    }
    throw new ApiError(`ANUM API request failed: ${response.status}`, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export function mapTask(task: ApiTask): Task {
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

export function mapRun(run: ApiAgentRun): AgentRun {
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

export function mapApproval(approval: ApiApproval): Approval {
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
