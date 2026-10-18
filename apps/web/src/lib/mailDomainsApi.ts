// Mail domains and aliases over the live API (docs/email.md#mail-domains).
import { apiRequest } from './api';
import type { DnsReport, DomainChangeBody, MailDomainsView, PendingDomainChange } from './mailDomains';

const BASE = '/api/v1/platform/mail/domains';

export const getMailDomains = (): Promise<MailDomainsView> => apiRequest(BASE, { method: 'GET' });
export const getDomainDns = (domain: string): Promise<DnsReport> => apiRequest(`${BASE}/${encodeURIComponent(domain)}/dns`, { method: 'GET' });
export const askDomainChange = (body: DomainChangeBody): Promise<{ status: 'waiting_approval'; summary: string; approval: PendingDomainChange['approval'] }> =>
  apiRequest(`${BASE}/changes`, { method: 'POST', body: JSON.stringify(body) });
export const withdrawDomainChange = (approvalId: string): Promise<void> => apiRequest(`${BASE}/changes/${encodeURIComponent(approvalId)}`, { method: 'DELETE' });
