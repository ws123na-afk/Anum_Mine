// Unified inbox over the live API (docs/email.md). Owners and members read and act; owners connect.
import type { Approval } from '@anum/contracts';
import { apiBaseUrl, apiRequest, authHeaders, mapApproval, type ApiApproval } from './api';
import { apiErrorFromResponse } from './errors';
import {
  messagesQuery, type ActionResult, type AnalyzeResult, type ApiApprovalLike, type DraftCreateResult, type EmailAccount, type EmailDraft,
  type EmailProvider, type EmailSetup, type Filters, type InboxSummary, type MessageDetail, type MessagePage,
} from './email';

const base = '/api/v1/email';
const id = encodeURIComponent;

export const getEmailSetup = (): Promise<EmailSetup> => apiRequest(`${base}/setup`, { method: 'GET' });
export const listEmailAccounts = (): Promise<EmailAccount[]> => apiRequest(`${base}/accounts`, { method: 'GET' });
/** Unread, high-priority and per-category counts for the summary band. */
export const getEmailSummary = (): Promise<InboxSummary> => apiRequest(`${base}/summary`, { method: 'GET' });
/** Recent reply drafts across the visible mailboxes, newest first (the API allows at most 100). */
export const listEmailDrafts = (limit = 50): Promise<EmailDraft[]> => apiRequest(`${base}/drafts?limit=${limit}`, { method: 'GET' });
export const syncEmailAccount = (accountId: string): Promise<EmailAccount> => apiRequest(`${base}/accounts/${id(accountId)}/sync`, { method: 'POST' });
export const disconnectEmailAccount = (accountId: string): Promise<{ revoked_at_provider: boolean; detail: string }> =>
  apiRequest(`${base}/accounts/${id(accountId)}`, { method: 'DELETE' });

export const startEmailConnect = (provider: EmailProvider, visibility: 'workspace' | 'private'): Promise<{ authorization_url: string; expires_at: string }> =>
  apiRequest(`${base}/oauth/${provider}/start`, { method: 'POST', body: JSON.stringify({ visibility }) });
export const completeEmailConnect = (connection: string): Promise<EmailAccount> =>
  apiRequest(`${base}/oauth/complete`, { method: 'POST', body: JSON.stringify({ connection }) });

export const listEmailMessages = (filters: Filters, before?: string | null): Promise<MessagePage> =>
  apiRequest(`${base}/messages?${messagesQuery(filters, before)}`, { method: 'GET' });
export const getEmailMessage = (messageId: string): Promise<MessageDetail> => apiRequest(`${base}/messages/${id(messageId)}`, { method: 'GET' });
export const markEmailRead = (messageId: string, read: boolean): Promise<ActionResult> =>
  apiRequest(`${base}/messages/${id(messageId)}/read`, { method: 'POST', body: JSON.stringify({ read }) });
export const archiveEmail = (messageId: string): Promise<ActionResult> => apiRequest(`${base}/messages/${id(messageId)}/archive`, { method: 'POST' });
export const analyzeEmail = (messageId: string): Promise<AnalyzeResult> => apiRequest(`${base}/messages/${id(messageId)}/analyze`, { method: 'POST' });
export const createEmailDraft = (messageId: string, body: { body?: string; instructions?: string }): Promise<DraftCreateResult> =>
  apiRequest(`${base}/messages/${id(messageId)}/drafts`, { method: 'POST', body: JSON.stringify(body) });
export const updateEmailDraft = (draftId: string, body: { to?: string[]; cc?: string[]; subject?: string; body?: string }): Promise<EmailDraft> =>
  apiRequest(`${base}/drafts/${id(draftId)}`, { method: 'PATCH', body: JSON.stringify(body) });
export async function discardEmailDraft(draftId: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${base}/drafts/${id(draftId)}`, { method: 'DELETE', headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}
export const requestSendApproval = (draftId: string): Promise<{ draft: EmailDraft; approval: ApiApprovalLike }> =>
  apiRequest(`${base}/drafts/${id(draftId)}/send-approval`, { method: 'POST' });

/** The approval in the client's shape (for api.approveTask, which sends its payload hash). */
export function toApproval(approval: ApiApprovalLike): Approval {
  return mapApproval(approval as unknown as ApiApproval);
}

/** Download an attachment through the API (never opened inline). */
export async function downloadAttachment(messageId: string, attachmentId: string, filename: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${base}/messages/${id(messageId)}/attachments/${id(attachmentId)}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
