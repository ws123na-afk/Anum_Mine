// AI on the Email Center over the live API (/api/v1/mail/ai, docs/email.md#ai-on-email).
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import type { MailAiDraft, MailAiStatus, MailLabel, MailMessageRef, MailPageLabels, MailRowLabel, MailSummary, MailWaiting } from './mailAi';

const base = '/api/v1/mail/ai';
const STATUS_TTL_MS = 60_000;
let statusCache: { at: number; value: Promise<MailAiStatus> } | null = null;

/** Whether a model is set up (cached for a minute: every opened message asks). */
export function getMailAiStatus(now: () => number = Date.now): Promise<MailAiStatus> {
  if (statusCache && now() - statusCache.at < STATUS_TTL_MS) return statusCache.value;
  const value = apiRequest<MailAiStatus>(`${base}/status`, { method: 'GET' });
  statusCache = { at: now(), value };
  value.catch(() => { if (statusCache?.value === value) statusCache = null; });
  return value;
}
export function resetMailAiStatus(): void { statusCache = null; }

export const summarizeMail = (ref: MailMessageRef, thread = false): Promise<MailSummary> =>
  apiRequest(`${base}/summarize`, { method: 'POST', body: JSON.stringify({ ...ref, thread }) });
export const classifyMail = (ref: MailMessageRef): Promise<MailLabel> =>
  apiRequest(`${base}/classify`, { method: 'POST', body: JSON.stringify(ref) });
export const draftMailReplyWithAi = (ref: MailMessageRef, kind: 'reply' | 'reply_all', instructions?: string): Promise<MailAiDraft> =>
  apiRequest(`${base}/draft-reply`, { method: 'POST', body: JSON.stringify({ ...ref, kind, ...(instructions ? { instructions } : {}) }) });

/** The caller's stored labels for the shown page (no model call). */
export function listMailLabels(accountId: string, folder: string, rows: { uid: number; message_id: string | null }[]): Promise<MailRowLabel[]> {
  if (!rows.length) return Promise.resolve([]);
  const query = new URLSearchParams({ account_id: accountId, folder, uids: rows.map((r) => r.uid).join(','), message_ids: rows.map((r) => r.message_id ?? '').join('\t') });
  return apiRequest(`${base}/labels?${query}`, { method: 'GET' });
}
export const labelMailPage = (accountId: string, folder: string, uids: number[], force = false): Promise<MailPageLabels> =>
  apiRequest(`${base}/classify-page`, { method: 'POST', body: JSON.stringify({ account_id: accountId, folder, uids, force }) });
/** Ask to archive or export exactly these messages: answers with the approval an owner decides. */
export const requestMailArchive = (accountId: string, folder: string, uids: number[]): Promise<MailWaiting> =>
  apiRequest(`${base}/archive`, { method: 'POST', body: JSON.stringify({ account_id: accountId, folder, uids }) });
export const requestMailExport = (accountId: string, folder: string, uids: number[]): Promise<MailWaiting> =>
  apiRequest(`${base}/export`, { method: 'POST', body: JSON.stringify({ account_id: accountId, folder, uids }) });

/** Download an approved export (one hour after approval, only for the person who asked). */
export async function downloadMailExport(approvalId: string, filename: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${base}/exports/${encodeURIComponent(approvalId)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
