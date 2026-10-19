import type { AgentRun, Approval, ApprovalRouteReason, DomainEvent, SignInOptions, TenantContext, Task, UserPreferencesUpdate } from '@anum/contracts';
import { parseSignInOptions } from './signIn';
import { accessToken, currentClaims, oidcEnabled } from './auth';
import { decisionBody } from './approvals';
import { apiErrorFrom, apiErrorFromResponse } from './errors';
import { getLocale, t } from './i18n';
import type { ApiFile, ApiFileLink, ApiFilePreview, ApiFileUsage, SortValue } from './files';
import { filesListPath } from './files';
import type { ApiCurrentMembership } from './policy';
import type { SkillVerification } from './skillSigning';
import * as workspaces from './workspaces';
import * as capabilities from './capabilities';
import type { ModelConfig, ModelConfigBody, ModelConnectionTest } from './modelConfig';
import * as chatMemory from './chatMemory';
import * as push from './push';
import { parseSourceNames, type SourceName } from './retrieval';
import { taskTitleFrom } from './tasks';

// The one API base URL for every module. Deployments compile VITE_ANUM_API_URL in (apps/web/Dockerfile, infra/oracle/compose.yaml).
export const apiBaseUrl = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

export const defaultTenantContext: TenantContext = {
  tenantId: 'tenant_local',
  workspaceId: 'workspace_foundation',
  userId: 'user_local',
  // Local development only (ANUM_AUTH_MODE=headers): the workspace owner who is also the
  // organization administrator, like the Keycloak realm's DEV-ONLY `dev` user.
  roles: ['owner', 'member', 'org_admin', 'platform_operator'],
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
  created_by?: string | null;
  archived_at?: string | null;
  archived_by?: string | null;
  agent_id?: string | null;
}

interface ApiAgentRunStep {
  id: string;
  type: AgentRun['steps'][number]['type'];
  summary: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface ApiAgentRun {
  id: string;
  task_id: string;
  status: AgentRun['status'];
  steps: ApiAgentRunStep[];
  result?: string | null;
  created_at?: string;
  updated_at?: string;
  checkpoint?: { phase?: string };
}

export interface ApiApproval {
  id: string;
  task_id: string;
  action: string;
  risk_level: Approval['riskLevel'];
  status: Approval['status'];
  reason: string;
  created_at: string;
  run_id?: string | null;
  step_id?: string | null;
  arguments?: Record<string, unknown>;
  payload_hash?: string | null;
  expires_at?: string | null;
  decided_at?: string | null;
  decided_by?: string | null;
  decision_reason?: string | null;
  requested_by?: string | null;
  target?: string | null;
  required_approvals?: number;
  approvers?: { user_id: string; approved_at: string; reason?: string | null }[];
  routing?: ApiApprovalRouting | null;
  suppressed?: { recipient: string; channel: string; purpose: string }[];
}

export interface ApiApprovalRouting {
  routed: boolean;
  rules?: string[];
  steps?: { approvers: string[][]; approved_by?: string | null }[];
  current_step?: number;
  reasons?: ApprovalRouteReason[];
  fallback?: boolean;
  can_approve: boolean;
  can_reject: boolean;
  refusal?: string | null;
  summary?: string;
}

/** `GET /api/v1/me/workspace-memberships` row (docs/identity.md, My workspaces). */
export interface ApiCallerMembership {
  tenant_id: string;
  workspace_id: string;
  workspace_name?: string | null;
  role: string;
  status: 'active';
  archived?: boolean;
}

export interface ApiRunTaskResponse {
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

/** Create a task and run it; with `agentId` it runs as that agent (docs/agents.md). */
export async function createAndRunTask(prompt: string, title = taskTitleFrom(prompt), agentId?: string): Promise<RunTaskResult> {
  const created = await request<ApiTask>('/api/v1/tasks', {
    method: 'POST',
    body: JSON.stringify(agentId ? { title, prompt, agent_id: agentId } : { title, prompt }),
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

/** Approve exactly what was displayed: the request carries the approval's payload hash. */
export async function approveTask(approval: Approval, reason?: string): Promise<ApprovalDecisionResult> {
  return decideApproval(approval, 'approve', reason);
}

// task_id is null for a memory remembered from a chat; owner_user_id marks it private to its owner.
export interface MemoryNote { id: string; task_id: string | null; content: string; provenance: { source_type: string; source_id: string | null; created_by_user_id: string; created_at: string; metadata?: Record<string, unknown> }; retention: { kind: string; expires_at: string | null }; created_at: string; owner_user_id?: string | null; updated_at?: string | null; }
export type FileRecord = ApiFile;
export interface SkillVersion { id: string; skill_id: string; version: string; name: string; description: string; required_tools: string[]; risk_level: string; created_at: string; verification?: SkillVerification | null; signed_by?: string | null; signed_at?: string | null }
export interface SkillInstallation { id: string; skill_id: string; version: string; approved_tools: string[]; enabled: boolean; installed_at: string; verification?: SkillVerification | null; disabled_reason?: string | null }
export interface WorkspaceInfo { id: string; tenant_id: string; name: string; created_at: string; updated_at: string; }
export interface OnboardingStatus { complete: boolean; tenant: { id: string; name: string } | null; workspace: WorkspaceInfo | null; membership: { role: string } | null; model_configured: boolean; }
export type { ModelConfig, ModelConnectionTest } from './modelConfig';
export type NotificationPreferences = push.NotificationPreferences;

/** Local development session; a no-op when the app signs in through OIDC. */
export async function ensureLocalSession(): Promise<void> {
  if (oidcEnabled) return;
  if (sessionStorage.getItem('anum_access_token')) return;
  const response = await fetch(`${apiBaseUrl}/api/v1/auth/local/session`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ tenant_id: currentTenantId(), workspace_id: currentScope().workspaceId, user_id: defaultTenantContext.userId }) });
  if (!response.ok) return;
  const result = await response.json() as { access_token: string }; sessionStorage.setItem('anum_access_token', result.access_token);
}
/** `GET /api/v1/auth/sign-in-options`: public, sent without a token or workspace (docs/identity.md#google-and-apple-sign-in). */
export async function getSignInOptions(): Promise<SignInOptions> {
  const response = await fetch(`${apiBaseUrl}/api/v1/auth/sign-in-options`, { method: 'GET' });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return parseSignInOptions(await response.json());
}
export async function getOnboarding(): Promise<OnboardingStatus> { return request('/api/v1/onboarding', { method: 'GET' }); }
export async function completeOnboarding(organizationName: string, workspaceName: string): Promise<OnboardingStatus> { return request('/api/v1/onboarding', { method: 'PUT', body: JSON.stringify({ organization_name: organizationName, workspace_name: workspaceName }) }); }
export async function getModelConfig(): Promise<ModelConfig> { return request('/api/v1/model-config', { method: 'GET' }); }
/** PUT /api/v1/model-config with a full body (Claude's routing fields included; src/lib/modelConfig.ts). */
export async function saveModelConfigBody(body: ModelConfigBody): Promise<ModelConfig> { return request('/api/v1/model-config', { method: 'PUT', body: JSON.stringify(body) }); }
export async function testModelConfig(): Promise<ModelConnectionTest> { return request('/api/v1/model-config/test', { method: 'POST' }); }
export async function getNotificationPreferences(): Promise<NotificationPreferences> { return request('/api/v1/notification-preferences', { method: 'GET' }); }
export async function saveNotificationPreferences(value: NotificationPreferences): Promise<NotificationPreferences> { return request('/api/v1/notification-preferences', { method: 'PUT', body: JSON.stringify(value) }); }

/** One task by id (a run's "Open task" link). */
export async function getTask(taskId: string): Promise<Task> { return mapTask(await request<ApiTask>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { method: 'GET' })); }
export async function getTasks(): Promise<Task[]> { return (await request<ApiTask[]>('/api/v1/tasks', { method: 'GET' })).map(mapTask); }
export async function getApprovals(): Promise<Approval[]> { return (await request<ApiApproval[]>('/api/v1/approvals', { method: 'GET' })).map(mapApproval); }
/** One page of `GET /api/v1/tasks` after `cursor` (the last task id already shown; the API answers 400 for an unknown one), newest first. */
export async function getTaskPage(cursor: string, limit = 50): Promise<Task[]> {
  const query = new URLSearchParams({ limit: String(limit), cursor });
  return (await request<ApiTask[]>(`/api/v1/tasks?${query}`, { method: 'GET' })).map(mapTask);
}
/** The task's most recent agent run (`GET /api/v1/tasks/{id}/latest-run`); 404 when it never ran. */
export async function getLatestRun(taskId: string): Promise<AgentRun> { return mapRun(await request<ApiAgentRun>(`/api/v1/tasks/${encodeURIComponent(taskId)}/latest-run`, { method: 'GET' })); }
export async function getRun(runId: string): Promise<AgentRun> { return mapRun(await request<ApiAgentRun>(`/api/v1/agent-runs/${runId}`, { method: 'GET' })); }
export async function resumeRun(runId: string): Promise<RunTaskResult> {
  const result = await request<ApiRunTaskResponse>(`/api/v1/agent-runs/${runId}/resume`, { method: 'POST' });
  return { task: mapTask(result.task), run: mapRun(result.run), approval: result.approval ? mapApproval(result.approval) : null };
}
export async function getMemories(): Promise<MemoryNote[]> { return request('/api/v1/memories', { method: 'GET' }); }
export async function createMemory(taskId: string, content: string): Promise<MemoryNote> { return request('/api/v1/memories', { method: 'POST', body: JSON.stringify({ task_id: taskId, content, source_type: 'user', retention: { kind: 'task' } }) }); }
export async function deleteMemory(id: string): Promise<void> { await requestVoid(`/api/v1/memories/${id}`, { method: 'DELETE' }); }
/** One page of workspace files (docs/files.md): search, sort and the keyset cursor (last id shown). */
export async function getFiles(options: { query?: string; sort?: SortValue; cursor?: string | null; limit?: number } = {}): Promise<FileRecord[]> { return request(filesListPath(options), { method: 'GET' }); }
export async function getFile(id: string): Promise<FileRecord> { return request(`/api/v1/files/${encodeURIComponent(id)}`, { method: 'GET' }); }
export async function getFileUsage(): Promise<ApiFileUsage> { return request('/api/v1/files/usage', { method: 'GET' }); }
/** Names of a run's sources in one bounded lookup (`path` from retrieval.sourceNamesPath). */
export async function getSourceNames(path: string): Promise<SourceName[]> { return parseSourceNames(await request<unknown>(path, { method: 'GET' })); }
/**
 * Upload one file with progress and cancel. XMLHttpRequest, because fetch reports no upload
 * progress. The name goes in the query (URL-encoded), so non-ASCII names work; `signal` aborts.
 */
export async function uploadFile(file: File, onProgress?: (loaded: number, total: number) => void, signal?: AbortSignal): Promise<FileRecord> {
  const headers = await authHeaders();
  return new Promise<FileRecord>((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Upload cancelled', 'AbortError')); return; }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${apiBaseUrl}/api/v1/files?${new URLSearchParams({ name: file.name }).toString()}`);
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.setRequestHeader('content-type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (event) => onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size);
    xhr.onload = () => {
      let body: unknown;
      try { body = JSON.parse(xhr.responseText); } catch { body = undefined; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as FileRecord);
      else reject(apiErrorFrom(xhr.status, body));
    };
    xhr.onerror = () => reject(new Error(t('files.uploadLost')));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}
export async function renameFile(id: string, name: string): Promise<FileRecord> { return request(`/api/v1/files/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ name }) }); }
export async function reindexFile(id: string): Promise<FileRecord> { return request(`/api/v1/files/${encodeURIComponent(id)}/reindex`, { method: 'POST' }); }
export async function getFilePreview(id: string): Promise<ApiFilePreview> { return request(`/api/v1/files/${encodeURIComponent(id)}/preview`, { method: 'GET' }); }
export async function deleteFile(id: string): Promise<void> { await requestVoid(`/api/v1/files/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
/** A short-lived link that needs no bearer token (`inline` for previews). The URL is absolute. */
export async function createFileLink(id: string, disposition: 'attachment' | 'inline' = 'attachment'): Promise<ApiFileLink> {
  const link = await request<ApiFileLink>(`/api/v1/files/${encodeURIComponent(id)}/links`, { method: 'POST', body: JSON.stringify({ disposition }) });
  return { ...link, url: `${apiBaseUrl}${link.url}` };
}
/** The bytes behind a short-lived inline link (image and PDF previews). */
export async function fetchFileBytes(id: string, signal?: AbortSignal): Promise<Blob> {
  const link = await createFileLink(id, 'inline');
  const response = await fetch(link.url, { signal });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.blob();
}
/**
 * Download through a short-lived link. The bytes (at most the upload limit) are saved from a
 * same-origin blob URL, which browsers and the desktop webview both treat as a download.
 */
export async function downloadFile(id: string, name: string): Promise<void> {
  const link = await createFileLink(id, 'attachment');
  const response = await fetch(link.url);
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = name;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function getSkillVersions(): Promise<SkillVersion[]> { return request('/api/v1/skills/versions', { method: 'GET' }); }
export async function getSkillInstallations(): Promise<SkillInstallation[]> { return request('/api/v1/skills/installations', { method: 'GET' }); }
export async function installSkill(skillId: string, version: string, approvedTools: string[]): Promise<SkillInstallation> { return request('/api/v1/skills/installations', { method: 'POST', body: JSON.stringify({ skill_id: skillId, version, approved_tools: approvedTools }) }); }
export async function setSkillEnabled(skillId: string, enabled: boolean, acknowledgeFlagged = false): Promise<SkillInstallation> { return request(`/api/v1/skills/installations/${encodeURIComponent(skillId)}`, { method: 'PATCH', body: JSON.stringify(acknowledgeFlagged ? { enabled, acknowledge_flagged: true } : { enabled }) }); }
export async function uninstallSkill(skillId: string): Promise<void> { await requestVoid(`/api/v1/skills/installations/${encodeURIComponent(skillId)}`, { method: 'DELETE' }); }
export async function searchMemories(query: string, onlyChats = false): Promise<MemoryNote[]> { return request(capabilities.memorySearchPath(query, undefined, onlyChats), { method: 'GET' }); }
/** The caller's own preferences, including "Remember things from my chats". */
export async function getMyPreferences(): Promise<chatMemory.UserPreferences> { return request(chatMemory.PREFERENCES_PATH, { method: 'GET' }); }
export async function saveMyPreferences(rememberConversations: boolean): Promise<chatMemory.UserPreferences> { return request(chatMemory.PREFERENCES_PATH, { method: 'PUT', body: JSON.stringify({ remember_conversations: rememberConversations } satisfies UserPreferencesUpdate) }); }
/** Whether this server can send phone push at all (ANUM_FCM_SERVICE_ACCOUNT_JSON). */
export async function getPushStatus(): Promise<push.PushStatus> { return request(push.PUSH_STATUS_PATH, { method: 'GET' }); }
/** The caller's own phones registered for push in this workspace (never their tokens). */
export async function listMyDevices(): Promise<push.PushDevice[]> { return request(push.MY_DEVICES_PATH, { method: 'GET' }); }
export async function removeMyDevice(id: string): Promise<void> { await requestVoid(push.deviceDeletePath(id), { method: 'DELETE' }); }
/** Deletes every memory remembered from the caller's own chats in this workspace. */
export async function deleteMyChatMemories(): Promise<{ deleted: number }> { return request(chatMemory.CHAT_MEMORIES_PATH, { method: 'DELETE' }); }
export async function getRetrievalStatus(): Promise<capabilities.RetrievalIndexStatus> { return request('/api/v1/retrieval/status', { method: 'GET' }); }
export async function reindexRetrieval(): Promise<capabilities.ReindexResult> { return request('/api/v1/retrieval/index', { method: 'POST' }); }
export async function getCurrentWorkspace(): Promise<WorkspaceInfo> { return request('/api/v1/workspaces/current', { method: 'GET' }); }

export interface IntegrationHealth {
  id: string;
  name: string;
  kind: 'database' | 'identity' | 'event_bus' | 'workflow' | 'cache' | 'object_storage' | 'search';
  status: 'connected' | 'degraded' | 'configured' | 'disabled';
  endpoint: string;
  latency_ms: number | null;
  detail: string;
  credentials: { configured: boolean; source: string; scopes: string[]; expires_at: string | null };
}

export async function rejectTask(approval: Approval, reason?: string): Promise<ApprovalDecisionResult> {
  return decideApproval(approval, 'reject', reason);
}

export async function getIntegrations(): Promise<IntegrationHealth[]> {
  return request<IntegrationHealth[]>('/api/v1/integrations', { method: 'GET' });
}

export async function streamEvents(
  onEvent: (event: DomainEvent) => void,
  signal: AbortSignal,
  onConnected: () => void,
  lastEventId?: string,
): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/v1/events/stream`, {
    headers: {
      ...(await authHeaders()),
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
  approval: Approval,
  decision: 'approve' | 'reject',
  reason?: string,
): Promise<ApprovalDecisionResult> {
  const body = decisionBody(approval, reason);
  const result = await request<ApiApprovalDecisionResponse>(`/api/v1/approvals/${encodeURIComponent(approval.id)}/${decision}`, {
    method: 'POST',
    ...(body ? { body: JSON.stringify(body) } : {}),
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
      ...(await authHeaders()),
      'content-type': 'application/json',
      ...init.headers,
    },
  });

  if (!response.ok) {
    // Surface the API's error envelope (e.g. "Could not reach Ollama ...", or 402 model_budget_exceeded)
    // as an ApiError that keeps the status and code.
    throw await apiErrorFromResponse(response);
  }

  return response.json() as Promise<T>;
}

/** The shared JSON request (auth headers, error envelope) for feature modules such as adminApi.ts. */
export const apiRequest = request;
/** The same for routes that answer 204 with no body (DELETE). */
export const apiRequestVoid = (path: string, init: RequestInit): Promise<void> => requestVoid(path, init);

async function requestVoid(path: string, init: RequestInit): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, headers: { ...(await authHeaders()), ...init.headers } });
  if (!response.ok) throw await apiErrorFromResponse(response);
}


const configuredWorkspaceId = import.meta.env.VITE_ANUM_WORKSPACE_ID as string | undefined;

function browserStorage(): workspaces.KeyValueStorage | null {
  try { return window.localStorage; } catch { return null; }
}

function claimText(name: string): string | null {
  const value = (currentClaims() ?? {})[name];
  return typeof value === 'string' && value ? value : null;
}

/** Remembered workspaces are kept per tenant and user (workspace ids only, never tokens). */
function workspaceMemoryKey(tenantId: string = currentTenantId()): string {
  return workspaces.memoryKey(tenantId, oidcEnabled ? claimText('sub') ?? '' : defaultTenantContext.userId);
}

/** The organisation the token (or the local development session) starts in. */
function homeTenantId(): string {
  return oidcEnabled ? claimText('tenant_id') ?? '' : defaultTenantContext.tenantId;
}

/** Storage key of the selected organisation, per home tenant and user (docs/customer-organisations.md#switching). */
function tenantMemoryKey(): string {
  return `anum.tenant.${homeTenantId() || 'unknown'}.${(oidcEnabled ? claimText('sub') : defaultTenantContext.userId) || 'unknown'}`;
}

/** The organisation every request acts in: the user's switch, else the home tenant. */
export function currentTenantId(): string {
  try {
    const selected = window.localStorage.getItem(tenantMemoryKey());
    if (selected && workspaces.isWorkspaceId(selected)) return selected;
  } catch { /* storage blocked: the home tenant */ }
  return homeTenantId();
}

function rememberTenant(tenantId: string): void {
  try {
    if (tenantId === homeTenantId()) window.localStorage.removeItem(tenantMemoryKey());
    else window.localStorage.setItem(tenantMemoryKey(), tenantId);
  } catch { /* this page only */ }
}

const readWorkspaceMemory = () => workspaces.readMemory(browserStorage(), workspaceMemoryKey());
const writeWorkspaceMemory = (memory: workspaces.WorkspaceMemory) => workspaces.writeMemory(browserStorage(), workspaceMemoryKey(), memory);

/** Defaults the client knows without a selection: the token's workspace and the configured one. */
function defaultWorkspaces(): (string | null | undefined)[] {
  return oidcEnabled ? [claimText('workspace_id'), configuredWorkspaceId] : [defaultTenantContext.workspaceId];
}

/**
 * The tenant and workspace the app is acting in. The workspace is the user's selection, else the
 * token's `workspace_id` claim, else VITE_ANUM_WORKSPACE_ID, else the development default.
 */
export function currentScope(): { tenantId: string; workspaceId: string } {
  const memory = readWorkspaceMemory();
  const tenantId = currentTenantId();
  if (!oidcEnabled) return { tenantId, workspaceId: workspaces.resolveWorkspace(memory, null, null, defaultTenantContext.workspaceId) };
  // The token's default workspace belongs to its home tenant only.
  const claimWorkspace = tenantId === homeTenantId() ? claimText('workspace_id') : null;
  return {
    tenantId,
    workspaceId: workspaces.resolveWorkspace(memory, claimWorkspace, tenantId === homeTenantId() ? configuredWorkspaceId : undefined, defaultTenantContext.workspaceId),
  };
}

/**
 * What the workspace switcher can offer: the caller's memberships from the API when they were
 * loaded (`listMyWorkspaces`), plus what this client knows by itself (src/lib/workspaces.ts).
 */
export function workspaceOptions(memberships: workspaces.MembershipEntry[] | null = null): workspaces.WorkspaceOption[] {
  return workspaces.workspaceOptions(currentScope().workspaceId, defaultWorkspaces(), readWorkspaceMemory(), memberships);
}

/**
 * Whether to offer the platform console in the navigation: the token's realm roles (OIDC) or the
 * development roles. A hint only; the API decides every request (docs/customer-organisations.md).
 */
export function platformOperatorHint(): boolean {
  if (!oidcEnabled) return defaultTenantContext.roles.includes('platform_operator') && currentTenantId() === defaultTenantContext.tenantId;
  const claims = currentClaims() ?? {};
  const realm = (claims as { realm_access?: { roles?: unknown } }).realm_access?.roles;
  const roles = [...(Array.isArray(realm) ? realm : []), ...(Array.isArray((claims as { roles?: unknown }).roles) ? (claims as { roles: unknown[] }).roles : [])];
  return roles.includes('platform_operator') && currentTenantId() === homeTenantId();
}

/** The signed-in user's id: the token's subject with OIDC, the development user otherwise. */
export function currentUserId(): string | null {
  return oidcEnabled ? claimText('sub') : defaultTenantContext.userId;
}

/** The caller's active memberships in every workspace of their tenant. */
export async function listMyWorkspaces(): Promise<workspaces.MembershipEntry[]> {
  return workspaces.membershipEntries(await request<ApiCallerMembership[]>('/api/v1/me/workspace-memberships', { method: 'GET' }));
}

/** Remember a workspace the user joined (an accepted invitation) so the switcher lists it. */
export function rememberWorkspace(workspaceId: string): void {
  writeWorkspaceMemory(workspaces.remember(readWorkspaceMemory(), workspaceId));
}

/** Drop a remembered workspace from the switcher list (never the current one). */
export function forgetWorkspace(workspaceId: string): void {
  writeWorkspaceMemory(workspaces.forget(readWorkspaceMemory(), workspaceId));
}

/**
 * Switch the workspace every later request names in `x-workspace-id`. The API decides first:
 * a local development session is re-issued by `/auth/local/workspace/switch`, which checks the
 * membership; otherwise `/workspace-memberships/current` must answer with an active membership
 * in the target. A refusal (403) leaves the current workspace selected.
 */
export async function switchWorkspace(workspaceId: string): Promise<string> {
  const target = workspaceId.trim();
  if (!workspaces.isWorkspaceId(target)) throw new Error('A workspace ID is 3 to 80 letters, digits, hyphens or underscores.');
  if (!oidcEnabled && sessionStorage.getItem('anum_access_token')) {
    const session = await request<{ access_token: string }>('/api/v1/auth/local/workspace/switch', { method: 'POST', body: JSON.stringify({ workspace_id: target }) });
    sessionStorage.setItem('anum_access_token', session.access_token);
  } else {
    const membership = await request<ApiCurrentMembership>('/api/v1/workspace-memberships/current', { method: 'GET', headers: { 'x-workspace-id': target } });
    if (!membership.active || membership.workspace_id !== target) throw new Error(`You do not have an active membership in ${target}.`);
  }
  writeWorkspaceMemory(workspaces.select(readWorkspaceMemory(), target));
  return target;
}

/**
 * Switch to another organisation the caller belongs to (docs/customer-organisations.md#switching).
 * The API decides first: the local development session is re-issued by `/auth/local/tenant/switch`;
 * otherwise `/workspace-memberships/current` must confirm an active membership in the target
 * tenant and workspace (with OIDC the token must list the tenant in `tenant_ids`).
 */
export async function switchOrganisation(tenantId: string, workspaceId: string): Promise<{ tenantId: string; workspaceId: string }> {
  if (!workspaces.isWorkspaceId(tenantId) || !workspaces.isWorkspaceId(workspaceId)) throw new Error('Organisation and workspace IDs are 3 to 80 letters, digits, hyphens or underscores.');
  if (!oidcEnabled && sessionStorage.getItem('anum_access_token')) {
    const session = await request<{ access_token: string }>('/api/v1/auth/local/tenant/switch', { method: 'POST', body: JSON.stringify({ tenant_id: tenantId, workspace_id: workspaceId }) });
    sessionStorage.setItem('anum_access_token', session.access_token);
  } else {
    const membership = await request<ApiCurrentMembership>('/api/v1/workspace-memberships/current', { method: 'GET', headers: { 'x-tenant-id': tenantId, 'x-workspace-id': workspaceId } });
    if (!membership.active || membership.workspace_id !== workspaceId) throw new Error(`You do not have an active membership in ${workspaceId}.`);
  }
  rememberTenant(tenantId);
  writeWorkspaceMemoryFor(tenantId, workspaces.select(workspaces.readMemory(browserStorage(), workspaceMemoryKey(tenantId)), workspaceId));
  return { tenantId, workspaceId };
}

function writeWorkspaceMemoryFor(tenantId: string, memory: workspaces.WorkspaceMemory): void {
  workspaces.writeMemory(browserStorage(), workspaceMemoryKey(tenantId), memory);
}

/**
 * Identity headers for every API call; every call names its workspace in `x-workspace-id`.
 * With OIDC: the Keycloak access token and the selected workspace (the API takes the tenant from
 * the token). Locally: the development session token plus the header-asserted context
 * ANUM_AUTH_MODE=headers accepts.
 */
export async function authHeaders(): Promise<Record<string, string>> {
  const workspaceId = currentScope().workspaceId;
  // The API writes its refusals and the Privacy notes in the page language (docs/web-localization.md).
  const language = { 'accept-language': getLocale() };
  if (oidcEnabled) {
    const token = await accessToken();
    const tenantId = currentTenantId();
    // Another organisation than the token's home tenant is named explicitly; the API accepts it
    // only when the token lists it in tenant_ids.
    return { ...(token ? { authorization: `Bearer ${token}` } : {}), ...(tenantId && tenantId !== homeTenantId() ? { 'x-tenant-id': tenantId } : {}), 'x-workspace-id': workspaceId, ...language };
  }
  const token = sessionStorage.getItem('anum_access_token');
  return { 'x-tenant-id': currentTenantId(), 'x-workspace-id': workspaceId, 'x-user-id': defaultTenantContext.userId, 'x-user-roles': defaultTenantContext.roles.join(','), ...(token ? { authorization: `Bearer ${token}` } : {}), ...language };
}

/** The caller's membership in the current workspace, for showing their role (read-only). */
export async function getCurrentMembership(): Promise<ApiCurrentMembership> {
  return request('/api/v1/workspace-memberships/current', { method: 'GET' });
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
    createdBy: task.created_by ?? null,
    archivedAt: task.archived_at ?? null,
    agentId: task.agent_id ?? null,
    archivedBy: task.archived_by ?? null,
  };
}

export function mapRun(run: ApiAgentRun): AgentRun {
  return {
    id: run.id,
    taskId: run.task_id,
    status: run.status,
    result: run.result ?? undefined,
    createdAt: run.created_at,
    updatedAt: run.updated_at,
    phase: run.checkpoint?.phase,
    steps: run.steps.map((step) => ({
      id: step.id,
      type: step.type,
      summary: step.summary,
      createdAt: step.created_at,
      metadata: step.metadata ?? {},
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
    runId: approval.run_id ?? null,
    stepId: approval.step_id ?? null,
    arguments: approval.arguments ?? {},
    payloadHash: approval.payload_hash ?? null,
    expiresAt: approval.expires_at ?? null,
    decidedAt: approval.decided_at ?? null,
    decidedBy: approval.decided_by ?? null,
    decisionReason: approval.decision_reason ?? null,
    requestedBy: approval.requested_by ?? null,
    target: approval.target ?? null,
    requiredApprovals: approval.required_approvals ?? 1,
    approvers: (approval.approvers ?? []).map((approver) => ({ userId: approver.user_id, approvedAt: approver.approved_at, reason: approver.reason ?? null })),
    routing: approval.routing ? mapRouting(approval.routing) : null,
    suppressed: approval.suppressed ?? [],
  };
}

function mapRouting(routing: ApiApprovalRouting): NonNullable<Approval['routing']> {
  return {
    routed: routing.routed,
    rules: routing.rules ?? [],
    steps: (routing.steps ?? []).map((step) => ({ approvers: step.approvers, approvedBy: step.approved_by ?? null })),
    currentStep: routing.current_step ?? 0,
    reasons: routing.reasons ?? [],
    fallback: routing.fallback ?? false,
    canApprove: routing.can_approve,
    canReject: routing.can_reject,
    refusal: (routing.refusal ?? null) as NonNullable<Approval['routing']>['refusal'],
    summary: routing.summary ?? '',
  };
}
