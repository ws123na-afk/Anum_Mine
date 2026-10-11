// Developer mode over the live API (docs/developer-mode.md). Every route is owner-only on the server.
import type { Approval } from '@anum/contracts';
import { apiRequest, mapApproval, type ApiApproval } from './api';
import type { DevRequest, DevSetup } from './developer';

export function getDeveloperSetup(): Promise<DevSetup> {
  return apiRequest('/api/v1/developer/setup', { method: 'GET' });
}

export function listDeveloperRequests(options: { limit?: number; before?: string; query?: string } = {}): Promise<DevRequest[]> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) });
  if (options.before) params.set('before', options.before);
  if (options.query?.trim()) params.set('q', options.query.trim());
  return apiRequest(`/api/v1/developer/requests?${params.toString()}`, { method: 'GET' });
}

export function getDeveloperRequest(id: string, refresh = false): Promise<DevRequest> {
  return apiRequest(`/api/v1/developer/requests/${encodeURIComponent(id)}${refresh ? '?refresh=true' : ''}`, { method: 'GET' });
}

export function createDeveloperRequest(body: { text: string; allow_protected_paths: boolean }, source: 'text' | 'voice'): Promise<DevRequest> {
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
