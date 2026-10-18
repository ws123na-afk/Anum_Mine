// Email Center phase 3 part A over the live API (docs/email.md#email-center): shares, sender
// identities and contacts. Every call matches services/api/anum_api/mailbox/sharing_api.py.
import { apiRequest, apiRequestVoid } from './api';
import type { MailGrant, MailGrantAccess, MailIdentity, MailContact, MailContactInput, MailContactPage, MailShareCandidate } from './mailDirectory';

const base = '/api/v1/mail';
const id = encodeURIComponent;

export const listMailGrants = (accountId: string): Promise<MailGrant[]> => apiRequest(`${base}/accounts/${id(accountId)}/grants`, { method: 'GET' });
export const listShareCandidates = (accountId: string): Promise<MailShareCandidate[]> => apiRequest(`${base}/accounts/${id(accountId)}/share-candidates`, { method: 'GET' });
export const shareMailbox = (accountId: string, granteeUserId: string, access: MailGrantAccess): Promise<MailGrant> =>
  apiRequest(`${base}/accounts/${id(accountId)}/grants`, { method: 'POST', body: JSON.stringify({ grantee_user_id: granteeUserId, access }) });
export const updateMailGrant = (grantId: string, access: MailGrantAccess): Promise<MailGrant> =>
  apiRequest(`${base}/grants/${id(grantId)}`, { method: 'PATCH', body: JSON.stringify({ access }) });
/** The owner stops sharing, or the person it was shared with leaves. */
export const deleteMailGrant = (grantId: string): Promise<void> => apiRequestVoid(`${base}/grants/${id(grantId)}`, { method: 'DELETE' });

export const listMailIdentities = (accountId: string): Promise<MailIdentity[]> => apiRequest(`${base}/accounts/${id(accountId)}/identities`, { method: 'GET' });
export const createMailIdentity = (accountId: string, body: { address: string; display_name?: string; signature?: string; is_default?: boolean }): Promise<MailIdentity> =>
  apiRequest(`${base}/accounts/${id(accountId)}/identities`, { method: 'POST', body: JSON.stringify(body) });
export const updateMailIdentity = (identityId: string, body: { display_name?: string | null; signature?: string; is_default?: boolean }): Promise<MailIdentity> =>
  apiRequest(`${base}/identities/${id(identityId)}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteMailIdentity = (identityId: string): Promise<void> => apiRequestVoid(`${base}/identities/${id(identityId)}`, { method: 'DELETE' });

export function listMailContacts(opts: { q?: string; limit?: number; cursor?: string | null } = {}, signal?: AbortSignal): Promise<MailContactPage> {
  const query = new URLSearchParams({ limit: String(opts.limit ?? 25) });
  if (opts.q) query.set('q', opts.q);
  if (opts.cursor) query.set('cursor', opts.cursor);
  return apiRequest(`${base}/contacts?${query}`, { method: 'GET', signal });
}
export const createMailContact = (body: MailContactInput): Promise<MailContact> => apiRequest(`${base}/contacts`, { method: 'POST', body: JSON.stringify(body) });
export const updateMailContact = (contactId: string, body: Partial<MailContactInput>): Promise<MailContact> =>
  apiRequest(`${base}/contacts/${id(contactId)}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteMailContact = (contactId: string): Promise<void> => apiRequestVoid(`${base}/contacts/${id(contactId)}`, { method: 'DELETE' });
