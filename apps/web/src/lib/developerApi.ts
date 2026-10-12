// Developer mode over the live API (docs/developer-mode.md). Every route is owner-only on the server.
import type { Approval } from '@anum/contracts';
import { apiRequest, apiRequestVoid, mapApproval, type ApiApproval } from './api';
import type { DevProject, DevRequest, DevSetup, ProjectPayload, RequestBody, Resolution } from './developer';

export function getDeveloperSetup(): Promise<DevSetup> {
  return apiRequest('/api/v1/developer/setup', { method: 'GET' });
}

export function listDeveloperRequests(options: { limit?: number; before?: string; query?: string; projectId?: string } = {}): Promise<DevRequest[]> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.before) params.set('before', options.before);
  if (options.query?.trim()) params.set('q', options.query.trim());
  if (options.projectId) params.set('project_id', options.projectId);
  return apiRequest(`/api/v1/developer/requests?${params.toString()}`, { method: 'GET' });
}

export function getDeveloperRequest(id: string, refresh = false): Promise<DevRequest> {
  return apiRequest(`/api/v1/developer/requests/${encodeURIComponent(id)}${refresh ? '?refresh=true' : ''}`, { method: 'GET' });
}

export function createDeveloperRequest(body: RequestBody, source: 'text' | 'voice'): Promise<DevRequest> {
  return apiRequest('/api/v1/developer/requests', { method: 'POST', body: JSON.stringify({ ...body, source }) });
}

export function cancelDeveloperRequest(id: string): Promise<DevRequest> {
  return apiRequest(`/api/v1/developer/requests/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
}

/** Ask for the merge: the API answers with the pending approval to show (tool, arguments, payload hash). */
export async function requestMergeApproval(id: string): Promise<{ request: DevRequest; approval: Approval }> {
  const result = await apiRequest<{ request: DevRequest; approval: ApiApproval }>(
    `/api/v1/developer/requests/${encodeURIComponent(id)}/merge-approval`,
    { method: 'POST' },
  );
  return { request: result.request, approval: mapApproval(result.approval) };
}

/** The request's pending merge approval in the client's shape, or null. */
export function pendingApproval(request: DevRequest): Approval | null {
  return request.approval ? mapApproval(request.approval as unknown as ApiApproval) : null;
}

// ---- Projects ----

const projectPath = (id: string) => `/api/v1/developer/projects/${encodeURIComponent(id)}`;

export function listProjects(): Promise<DevProject[]> {
  return apiRequest('/api/v1/developer/projects', { method: 'GET' });
}

/** The token, when present, is sent once and stored encrypted; the answer never contains it. */
export function createProject(body: ProjectPayload): Promise<DevProject> {
  return apiRequest('/api/v1/developer/projects', { method: 'POST', body: JSON.stringify(body) });
}

export function updateProject(id: string, body: Partial<ProjectPayload>): Promise<DevProject> {
  return apiRequest(projectPath(id), { method: 'PATCH', body: JSON.stringify(body) });
}

export function deleteProject(id: string): Promise<void> {
  return apiRequestVoid(projectPath(id), { method: 'DELETE' });
}

/** Test connection: the repository is reachable with the token and has the workflow. */
export function testProject(id: string): Promise<DevProject> {
  return apiRequest(`${projectPath(id)}/test`, { method: 'POST' });
}

/** Opens (or finds) the pull request that adds the workflow; the owner merges it in GitHub. */
export function installWorkflow(id: string): Promise<{ project: DevProject; pull_request_number: number; pull_request_url: string; created: boolean }> {
  return apiRequest(`${projectPath(id)}/install-workflow`, { method: 'POST' });
}

export function getWorkflowTemplate(): Promise<{ file_name: string; path: string; content: string; secrets: string[]; variables: string[] }> {
  return apiRequest('/api/v1/developer/workflow-template', { method: 'GET' });
}

/** Which project a chat or voice request is for. Creates nothing. */
export function resolveDeveloperProject(text: string): Promise<Resolution> {
  return apiRequest('/api/v1/developer/resolve', { method: 'POST', body: JSON.stringify({ text }) });
}
