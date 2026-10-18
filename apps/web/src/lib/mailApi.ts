// Email Center over the live API (docs/email.md#email-center). The browser never talks to the mail
// server: every call goes to /api/v1/mail, and the mailbox password is sent only to connect.
import type { Approval } from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders, mapApproval, type ApiApproval } from './api';
import { apiErrorFromResponse } from './errors';
import type { MailAccount, MailDetail, MailDraft, MailDraftKind, MailFolder, MailPage, MailSendResult, MailSetup } from './mail';

const base = '/api/v1/mail';
const id = encodeURIComponent;

export const getMailSetup = (): Promise<MailSetup> => apiRequest(`${base}/setup`, { method: 'GET' });
export const connectMailbox = (body: { address: string; password: string; display_name?: string }): Promise<MailAccount> =>
  apiRequest(`${base}/accounts`, { method: 'POST', body: JSON.stringify(body) });
export const updateMailboxPassword = (accountId: string, password: string): Promise<MailAccount> =>
  apiRequest(`${base}/accounts/${id(accountId)}/password`, { method: 'PUT', body: JSON.stringify({ password }) });
export const checkMailbox = (accountId: string): Promise<MailAccount> => apiRequest(`${base}/accounts/${id(accountId)}/check`, { method: 'POST' });
export const disconnectMailbox = (accountId: string): Promise<void> => apiRequestVoid(`${base}/accounts/${id(accountId)}`, { method: 'DELETE' });

export const listMailFolders = (accountId: string): Promise<MailFolder[]> => apiRequest(`${base}/accounts/${id(accountId)}/folders`, { method: 'GET' });
export function listMail(accountId: string, folder: string, opts: { before?: number | null; limit?: number; q?: string; unread?: boolean; flagged?: boolean } = {}): Promise<MailPage> {
  const query = new URLSearchParams({ folder, limit: String(opts.limit ?? 25) });
  if (opts.before) query.set('before', String(opts.before));
  if (opts.q) query.set('q', opts.q);
  if (opts.unread) query.set('unread', 'true');
  if (opts.flagged) query.set('flagged', 'true');
  return apiRequest(`${base}/accounts/${id(accountId)}/messages?${query}`, { method: 'GET' });
}
export const readMail = (accountId: string, folder: string, uid: number): Promise<MailDetail> =>
  apiRequest(`${base}/accounts/${id(accountId)}/message?${new URLSearchParams({ folder, uid: String(uid) })}`, { method: 'GET' });

/** Download one attachment through the API (never opened inline). */
export async function downloadMailAttachment(accountId: string, folder: string, uid: number, index: number, filename: string): Promise<void> {
  const query = new URLSearchParams({ folder, uid: String(uid), index: String(index) });
  const response = await fetch(`${apiBaseUrl}${base}/accounts/${id(accountId)}/attachment?${query}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const listMailDrafts = (accountId?: string): Promise<MailDraft[]> =>
  apiRequest(`${base}/drafts${accountId ? `?account_id=${id(accountId)}` : ''}`, { method: 'GET' });
export const getMailDraft = (draftId: string): Promise<MailDraft> => apiRequest(`${base}/drafts/${id(draftId)}`, { method: 'GET' });
export const createMailDraft = (body: { account_id: string; kind: MailDraftKind; folder?: string; uid?: number }): Promise<MailDraft> =>
  apiRequest(`${base}/drafts`, { method: 'POST', body: JSON.stringify(body) });
export const updateMailDraft = (draftId: string, body: { to?: string[]; cc?: string[]; bcc?: string[]; subject?: string; body?: string }): Promise<MailDraft> =>
  apiRequest(`${base}/drafts/${id(draftId)}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteMailDraft = (draftId: string): Promise<void> => apiRequestVoid(`${base}/drafts/${id(draftId)}`, { method: 'DELETE' });
/** Ask to send, now or at `sendAt` (ISO with a zone): answers with the approval an owner decides with the payload hash shown. */
export const sendMailDraft = (draftId: string, idempotencyKey: string, sendAt?: string): Promise<MailSendResult> =>
  apiRequest(`${base}/drafts/${id(draftId)}/send`, { method: 'POST', body: JSON.stringify({ idempotency_key: idempotencyKey, ...(sendAt ? { send_at: sendAt } : {}) }) });

/** The approval in the client's shape (for api.approveTask, which sends its payload hash). */
export function toApproval(approval: ApiApproval): Approval {
  return mapApproval(approval);
}
