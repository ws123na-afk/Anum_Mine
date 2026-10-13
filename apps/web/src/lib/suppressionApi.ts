// The contact suppression list over the live API (docs/privacy.md#contact-suppression).
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { exportName, type SuppressionChannel, type SuppressionDraft, type SuppressionEntry, type SuppressionPage } from './suppression';

const BASE = '/api/v1/contact-suppressions';

export function listSuppressions(query: { q?: string; channel?: SuppressionChannel | ''; limit: number; offset: number }): Promise<SuppressionPage> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  if (query.q?.trim()) params.set('q', query.q.trim());
  if (query.channel) params.set('channel', query.channel);
  return apiRequest<SuppressionPage>(`${BASE}?${params.toString()}`, { method: 'GET' });
}

/** Adds an entry; `created` is false when the contact was already on the list (200). */
export async function addSuppression(body: SuppressionDraft): Promise<{ entry: SuppressionEntry; created: boolean }> {
  const response = await fetch(`${apiBaseUrl}${BASE}`, {
    method: 'POST', headers: { ...(await authHeaders()), 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return { entry: await response.json() as SuppressionEntry, created: response.status === 201 };
}

export const removeSuppression = (id: string, reason: string): Promise<void> =>
  apiRequestVoid(`${BASE}/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ reason }) });

/** Downloads the CSV (owners only) and returns the file name. */
export async function downloadSuppressions(): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/export`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const name = exportName(response.headers.get('content-disposition'));
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}
