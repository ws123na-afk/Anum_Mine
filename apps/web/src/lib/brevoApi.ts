// Brevo email campaigns over the live API (docs/brevo.md). Permissions are the server's: everyone in
// the workspace reads, members and owners write drafts and ask to send, owners connect and send tests.
import type { Approval } from '@anum/contracts';
import { apiRequest, apiRequestVoid, mapApproval, type ApiApproval } from './api';
import type { Campaign, ContactList, Draft, DraftBody, DraftStatus, Page, Sender, Status } from './brevo';
import { draftsPath } from './brevo';

const BASE = '/api/v1/brevo';
const json = (body: unknown) => ({ body: JSON.stringify(body) });
const id = (value: string | number) => encodeURIComponent(String(value));

export const getStatus = (): Promise<Status> => apiRequest(`${BASE}/status`, { method: 'GET' });
export const saveKey = (apiKey: string): Promise<Status> => apiRequest(`${BASE}/connection`, { method: 'PUT', ...json({ api_key: apiKey }) });
export const checkConnection = (): Promise<Status> => apiRequest(`${BASE}/connection/check`, { method: 'POST' });
export const removeConnection = (): Promise<Status> => apiRequest(`${BASE}/connection`, { method: 'DELETE' });

export const listLists = (limit = 50, offset = 0): Promise<Page<ContactList>> => apiRequest(`${BASE}/lists?limit=${limit}&offset=${offset}`, { method: 'GET' });
export const listSenders = (): Promise<{ items: Sender[] }> => apiRequest(`${BASE}/senders`, { method: 'GET' });
export function listCampaigns(options: { limit?: number; offset?: number; status?: string } = {}): Promise<Page<Campaign>> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 20), offset: String(options.offset ?? 0) });
  if (options.status) params.set('status', options.status);
  return apiRequest(`${BASE}/campaigns?${params.toString()}`, { method: 'GET' });
}
export const getCampaign = (campaignId: number): Promise<Campaign> => apiRequest(`${BASE}/campaigns/${id(campaignId)}`, { method: 'GET' });

export const listDrafts = (filter: { query?: string; status?: DraftStatus | ''; limit?: number; offset?: number } = {}): Promise<Page<Draft>> =>
  apiRequest(draftsPath(filter), { method: 'GET' });
export const getDraft = (draftId: string): Promise<Draft> => apiRequest(`${BASE}/drafts/${id(draftId)}`, { method: 'GET' });
export const createDraft = (body: DraftBody): Promise<Draft> => apiRequest(`${BASE}/drafts`, { method: 'POST', ...json(body) });
export const updateDraft = (draftId: string, body: DraftBody): Promise<Draft> => apiRequest(`${BASE}/drafts/${id(draftId)}`, { method: 'PUT', ...json(body) });
export const deleteDraft = (draftId: string): Promise<void> => apiRequestVoid(`${BASE}/drafts/${id(draftId)}`, { method: 'DELETE' });
export const sendTest = (draftId: string): Promise<Draft> => apiRequest(`${BASE}/drafts/${id(draftId)}/test`, { method: 'POST' });
export const askToSend = (draftId: string, scheduledAt: string | null): Promise<Draft> =>
  apiRequest(`${BASE}/drafts/${id(draftId)}/send`, { method: 'POST', ...json({ scheduled_at: scheduledAt }) });
export const withdraw = (draftId: string): Promise<Draft> => apiRequest(`${BASE}/drafts/${id(draftId)}/cancel`, { method: 'POST' });

export function approvalOf(value: Record<string, unknown> | null): Approval | null {
  return value ? mapApproval(value as unknown as ApiApproval) : null;
}
