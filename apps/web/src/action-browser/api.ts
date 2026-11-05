// API calls of the action browser (docs/action-browser.md#api). Part of the lazy chunk.
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from '../lib/api';
import { apiErrorFromResponse } from '../lib/errors';
import type { BrowserSession, Credential, CredentialInput, InputEvent } from '../lib/actionBrowser';

const BASE = '/api/v1/action-browser';

export const listCredentials = () => apiRequest<Credential[]>(`${BASE}/credentials`, { method: 'GET' });
export const createCredential = (body: CredentialInput) => apiRequest<Credential>(`${BASE}/credentials`, { method: 'POST', body: JSON.stringify(body) });
export const updateCredential = (id: string, body: CredentialInput) =>
  apiRequest<Credential>(`${BASE}/credentials/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) });
export const deleteCredential = (id: string) => apiRequestVoid(`${BASE}/credentials/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const sessionsForTask = (taskId: string) => apiRequest<BrowserSession[]>(`${BASE}/sessions?task_id=${encodeURIComponent(taskId)}`, { method: 'GET' });
export const stopSession = (id: string) => apiRequest<BrowserSession>(`${BASE}/sessions/${encodeURIComponent(id)}/stop`, { method: 'POST' });
export const sendInput = (id: string, event: InputEvent) =>
  apiRequest<{ outcome: string }>(`${BASE}/sessions/${encodeURIComponent(id)}/input`, { method: 'POST', body: JSON.stringify(event) });

/** The live picture as an object URL (the caller revokes it), or null when there is none now. */
export async function fetchFrame(id: string): Promise<string | null> {
  const response = await fetch(`${apiBaseUrl}${BASE}/sessions/${encodeURIComponent(id)}/frame`, { headers: await authHeaders(), cache: 'no-store' });
  if (response.status === 409) return null;
  if (!response.ok) throw await apiErrorFromResponse(response);
  return URL.createObjectURL(await response.blob());
}

