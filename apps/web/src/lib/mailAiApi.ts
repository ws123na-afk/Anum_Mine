// AI on the Email Center over the live API (/api/v1/mail/ai, docs/email.md#ai-on-email).
import { apiRequest } from './api';
import type { MailAiDraft, MailAiStatus, MailLabel, MailMessageRef, MailSummary } from './mailAi';

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
