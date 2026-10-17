// Email Center phase 2 over the live API (docs/email.md#email-center): flags, moves and delete for
// good on the caller's own mailbox. Every call matches services/api/anum_api/mailbox/api.py.
import { apiRequest, type ApiApproval } from './api';
import { chunkUids, type MailRole } from './mailActions';

const base = '/api/v1/mail/accounts';
const id = encodeURIComponent;

/** `ActionResult` of the flag, move and delete routes. */
export interface MailActionResult { status: 'done' | 'waiting_approval'; summary: string; approval: ApiApproval | null }

/** Read or unread, starred or not (`POST /accounts/{id}/flags`), in calls of at most 200 uids. */
export async function setMailFlag(accountId: string, folder: string, uids: number[], flag: 'seen' | 'flagged', value: boolean): Promise<void> {
  for (const chunk of chunkUids(uids)) {
    await apiRequest<MailActionResult>(`${base}/${id(accountId)}/flags`, { method: 'POST', body: JSON.stringify({ folder, uids: chunk, flag, value }) });
  }
}

/** Archive, junk, trash or back to the inbox (`POST /accounts/{id}/move`; `to` is a role or a folder id). */
export async function moveMail(accountId: string, folder: string, uids: number[], to: MailRole | string): Promise<void> {
  for (const chunk of chunkUids(uids)) {
    await apiRequest<MailActionResult>(`${base}/${id(accountId)}/move`, { method: 'POST', body: JSON.stringify({ folder, uids: chunk, to }) });
  }
}

/**
 * Delete for good from Trash or Junk (`POST /accounts/{id}/delete`). The API never deletes here: it
 * answers with an approval bound to exactly these messages (payload hash), decided by the owner.
 */
export const requestMailDelete = (accountId: string, folder: string, uids: number[]): Promise<MailActionResult> =>
  apiRequest<MailActionResult>(`${base}/${id(accountId)}/delete`, { method: 'POST', body: JSON.stringify({ folder, uids }) });
