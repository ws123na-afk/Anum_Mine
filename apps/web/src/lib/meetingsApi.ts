// Calls & Meetings API client (docs/meetings.md). JSON calls share api.ts's request (auth headers,
// error envelope); uploads use XMLHttpRequest for progress and cancel, as Files does.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFrom, apiErrorFromResponse } from './errors';
import { t } from './i18n';
import {
  meetingsListPath, transcriptPath, uploadQuery,
  type ApiExternalMeeting, type ApiMeeting, type ApiMeetingPage, type ApiMeetingSetup, type ApiTranscriptPage,
  type ConnectorProvider, type MeetingLanguage, type MeetingStatus, type ApiConnectorStatus,
} from './meetings';

const base = '/api/v1/meetings';
const one = (id: string, suffix = '') => `${base}/${encodeURIComponent(id)}${suffix}`;
const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const getSetup = () => apiRequest<ApiMeetingSetup>(`${base}/setup`, { method: 'GET' });
export const listMeetings = (options: { query?: string; status?: MeetingStatus | ''; offset?: number; limit?: number }) =>
  apiRequest<ApiMeetingPage>(meetingsListPath(options), { method: 'GET' });
export const getMeeting = (id: string) => apiRequest<ApiMeeting>(one(id), { method: 'GET' });
export const updateMeeting = (id: string, body: { title?: string; agent_skill_id?: string; clear_agent?: boolean; language?: MeetingLanguage }) =>
  apiRequest<ApiMeeting>(one(id), { method: 'PATCH', ...json(body) });
export async function deleteMeeting(id: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${one(id)}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}
export const getTranscript = (id: string, options: { query?: string; speaker?: string; offset?: number; limit?: number }) =>
  apiRequest<ApiTranscriptPage>(transcriptPath(id, options), { method: 'GET' });
export const reprocess = (id: string) => apiRequest<ApiMeeting>(one(id, '/process'), { method: 'POST' });
export const updateActionItem = (id: string, itemId: string, body: { text?: string; owner?: string; clear_owner?: boolean; due_date?: string; clear_due_date?: boolean; done?: boolean }) =>
  apiRequest<ApiMeeting>(one(id, `/action-items/${encodeURIComponent(itemId)}`), { method: 'PATCH', ...json(body) });
export const createTaskFromItem = (id: string, itemId: string) =>
  apiRequest<ApiMeeting>(one(id, `/action-items/${encodeURIComponent(itemId)}/task`), { method: 'POST' });
export const saveFollowUp = (id: string, body: { to: string[]; subject: string; body: string }) =>
  apiRequest<ApiMeeting>(one(id, '/follow-up'), { method: 'PUT', ...json(body) });
/** Ask to send the saved draft from a connected mailbox: an email reply draft plus an approval. */
export const sendFollowUp = (id: string, accountId: string) => apiRequest<ApiMeeting>(one(id, '/follow-up/send'), { method: 'POST', ...json({ account_id: accountId }) });

export const authorizeConnector = (provider: ConnectorProvider) =>
  apiRequest<{ authorize_url: string }>(`${base}/connectors/${provider}/authorize`, { method: 'GET' });
export const finishConnector = (code: string, state: string) =>
  apiRequest<ApiConnectorStatus>(`${base}/connectors/callback`, { method: 'POST', ...json({ code, state }) });
export async function disconnectConnector(provider: ConnectorProvider): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${base}/connectors/${provider}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}
export const listExternal = (provider: ConnectorProvider) =>
  apiRequest<ApiExternalMeeting[]>(`${base}/connectors/${provider}/meetings`, { method: 'GET' });
export const importExternal = (provider: ConnectorProvider, body: { external_id: string; language: MeetingLanguage; agent_skill_id?: string }) =>
  apiRequest<ApiMeeting>(`${base}/connectors/${provider}/import`, { method: 'POST', ...json(body) });

/** Upload a recording or transcript file with progress; `signal` cancels it. */
export async function uploadMeetingFile(
  kind: 'recording' | 'transcript',
  file: File,
  options: { title?: string; language?: MeetingLanguage; agentSkillId?: string },
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal,
): Promise<ApiMeeting> {
  const headers = await authHeaders();
  return new Promise<ApiMeeting>((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Upload cancelled', 'AbortError')); return; }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${apiBaseUrl}${base}/${kind === 'recording' ? 'recordings' : 'transcripts'}?${uploadQuery(file.name, options)}`);
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.setRequestHeader('content-type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (event) => onProgress?.(event.loaded, event.lengthComputable ? event.total : file.size);
    xhr.onload = () => {
      let body: unknown;
      try { body = JSON.parse(xhr.responseText); } catch { body = undefined; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as ApiMeeting);
      else reject(apiErrorFrom(xhr.status, body));
    };
    xhr.onerror = () => reject(new Error(t('meetings.import.lost')));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    xhr.send(file);
  });
}

/** Installed agents (skills) a meeting can be linked to. */
export async function listAgents(): Promise<string[]> {
  const installs = await apiRequest<{ skill_id: string; enabled: boolean }[]>('/api/v1/skills/installations', { method: 'GET' });
  return [...new Set(installs.map((item) => item.skill_id))].sort();
}
