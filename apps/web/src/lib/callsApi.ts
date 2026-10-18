// Phone calls API client (docs/calls.md#api). JSON calls share api.ts's request (auth headers, error
// envelope); the server checks call:read, call:log and call:manage on every route.
import { apiRequest, apiRequestVoid } from './api';
import {
  callsPath, contactsPath,
  type ApiAiCall, type ApiBrowserCall, type ApiCall, type ApiCallsSetup, type ApiContact, type ApiOptOut, type ApiProvider,
  type ConsentMethod, type Direction, type Outcome, type Page,
} from './calls';

const base = '/api/v1/calls';
const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { 'content-type': 'application/json' } }) });
const id = (value: string) => encodeURIComponent(value);

export const getSetup = () => apiRequest<ApiCallsSetup>(`${base}/setup`, { method: 'GET' });

export const listContacts = (options: { query?: string; favourites?: boolean; offset?: number; limit?: number }) =>
  apiRequest<Page<ApiContact>>(contactsPath(options), { method: 'GET' });
export const getContact = (contactId: string) => apiRequest<ApiContact>(`${base}/contacts/${id(contactId)}`, { method: 'GET' });
export interface ContactInput { name: string; phone: string; company?: string | null; email?: string | null; notes?: string | null; favourite?: boolean; language?: 'auto' | 'en' | 'ar' }
export const createContact = (body: ContactInput) => apiRequest<ApiContact>(`${base}/contacts`, json('POST', body));
export const updateContact = (contactId: string, body: Partial<ContactInput>) => apiRequest<ApiContact>(`${base}/contacts/${id(contactId)}`, json('PATCH', body));
export const deleteContact = (contactId: string) => apiRequestVoid(`${base}/contacts/${id(contactId)}`, { method: 'DELETE' });
export const recordConsent = (contactId: string, body: { method: ConsentMethod; note?: string; confirmed: boolean }) =>
  apiRequest<ApiContact>(`${base}/contacts/${id(contactId)}/ai-consent`, json('PUT', body));
export const withdrawConsent = (contactId: string) => apiRequest<ApiContact>(`${base}/contacts/${id(contactId)}/ai-consent`, json('DELETE'));

export const listCalls = (options: { query?: string; direction?: Direction | ''; outcome?: Outcome | ''; contactId?: string; mine?: boolean; offset?: number; limit?: number }) =>
  apiRequest<Page<ApiCall>>(callsPath(options), { method: 'GET' });
export const getCall = (callId: string) => apiRequest<ApiCall>(`${base}/log/${id(callId)}`, { method: 'GET' });
export interface CallInput { contact_id?: string; phone?: string; direction: Direction; outcome: Outcome; started_at?: string; duration_seconds?: number | null; notes?: string }
export const logCall = (body: CallInput) => apiRequest<ApiCall>(`${base}/log`, json('POST', { kind: 'phone', ...body }));
export const updateCall = (callId: string, body: Partial<Omit<CallInput, 'phone'>>) => apiRequest<ApiCall>(`${base}/log/${id(callId)}`, json('PATCH', body));
export const deleteCall = (callId: string) => apiRequestVoid(`${base}/log/${id(callId)}`, { method: 'DELETE' });
export const summarize = (callId: string) => apiRequest<ApiCall>(`${base}/log/${id(callId)}/summarize`, json('POST'));
export const saveFollowUp = (callId: string, body: { text: string; due_date?: string | null }) => apiRequest<ApiCall>(`${base}/log/${id(callId)}/follow-up`, json('PUT', body));
export const clearFollowUp = (callId: string) => apiRequest<ApiCall>(`${base}/log/${id(callId)}/follow-up`, json('DELETE'));
export const followUpTask = (callId: string) => apiRequest<ApiCall>(`${base}/log/${id(callId)}/follow-up/task`, json('POST'));

export const listOptOuts = (offset = 0) => apiRequest<Page<ApiOptOut>>(`${base}/opt-outs?limit=25&offset=${offset}`, { method: 'GET' });
export const addOptOut = (body: { phone: string; note?: string }) => apiRequest<ApiOptOut>(`${base}/opt-outs`, json('POST', body));
export const removeOptOut = (optOutId: string, reason: string) => apiRequestVoid(`${base}/opt-outs/${id(optOutId)}`, json('DELETE', { reason }));

export const getProvider = () => apiRequest<ApiProvider>(`${base}/provider`, { method: 'GET' });
export const saveProvider = (body: Record<string, unknown>) => apiRequest<ApiProvider>(`${base}/provider`, json('PUT', body));
export const checkProvider = () => apiRequest<ApiProvider>(`${base}/provider/check`, json('POST'));
export const deleteProvider = () => apiRequestVoid(`${base}/provider`, { method: 'DELETE' });
export const prepareBrowserCall = (body: { contact_id?: string; phone?: string; record: boolean; recording_consent: boolean; language: 'en' | 'ar' }) =>
  apiRequest<ApiBrowserCall>(`${base}/provider/calls`, json('POST', body));

export const listAiCalls = (offset = 0) => apiRequest<Page<ApiAiCall>>(`${base}/ai-calls?limit=25&offset=${offset}`, { method: 'GET' });
export const requestAiCall = (body: { contact_id: string; purpose: string; script: string; language: 'en' | 'ar'; max_seconds: number }) =>
  apiRequest<ApiAiCall>(`${base}/ai-calls`, json('POST', body));
export const cancelAiCall = (requestId: string) => apiRequest<ApiAiCall>(`${base}/ai-calls/${id(requestId)}/cancel`, json('POST'));
