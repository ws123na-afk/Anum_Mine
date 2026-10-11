// Privacy API (docs/privacy.md): GET /api/v1/privacy, GET /api/v1/privacy/export, POST /api/v1/privacy/delete.
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { exportFileName, type DeleteResult, type PrivacySummary } from './privacy';

export function getPrivacySummary(): Promise<PrivacySummary> {
  return apiRequest<PrivacySummary>('/api/v1/privacy', { method: 'GET' });
}

/** Download the caller's export as a file; resolves with its name and size once saved. */
export async function downloadMyData(): Promise<{ name: string; bytes: number }> {
  const response = await fetch(`${apiBaseUrl}/api/v1/privacy/export`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const blob = await response.blob();
  const name = exportFileName(response.headers.get('content-disposition'));
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
  // Let the browser start the download before the object URL goes away.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { name, bytes: blob.size };
}

export function deleteMyData(confirm: string): Promise<DeleteResult> {
  return apiRequest<DeleteResult>('/api/v1/privacy/delete', { method: 'POST', body: JSON.stringify({ confirm }) });
}
