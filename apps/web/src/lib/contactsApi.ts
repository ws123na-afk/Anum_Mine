// The Contacts directory over the live API (docs/contacts.md#api). The server decides who may read
// (any of call:read, business:read, whatsapp:read, email:read), write (business:write) and manage (business:manage).
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { apiErrorFromResponse } from './errors';
import { exportName, type BuildReport, type Channel, type ImportDecision, type ImportMapping, type ImportPreview, type ImportResult, type PeopleFilters, type PeoplePage, type PersonBody, type PersonDetail, type ProposalPage } from './contacts';

const BASE = '/api/v1/contacts';
const id = encodeURIComponent;
const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body), headers: body === undefined ? undefined : { 'content-type': 'application/json' } });

export function listPeople(filters: PeopleFilters, offset: number, limit: number): Promise<PeoplePage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset), sort: filters.sort });
  if (filters.q.trim()) params.set('q', filters.q.trim());
  if (filters.source) params.set('source', filters.source);
  if (filters.dnc) params.set('dnc', filters.dnc);
  return apiRequest<PeoplePage>(`${BASE}?${params}`, { method: 'GET' });
}
/** The command palette's people: a short name or number search. */
export const searchPeople = async (query: string, limit = 5) => (await listPeople({ q: query, source: '', dnc: '', sort: 'name' }, 0, limit)).items;
export const getPerson = (personId: string) => apiRequest<PersonDetail>(`${BASE}/${id(personId)}`, { method: 'GET' });
export const createPerson = (body: PersonBody) => apiRequest<PersonDetail>(BASE, json('POST', body));
export const updatePerson = (personId: string, body: Partial<PersonBody>) => apiRequest<PersonDetail>(`${BASE}/${id(personId)}`, json('PATCH', body));
export const deletePerson = (personId: string) => apiRequestVoid(`${BASE}/${id(personId)}`, { method: 'DELETE' });
export const mergePeople = (keepId: string, otherId: string) => apiRequest<PersonDetail>(`${BASE}/${id(keepId)}/merge`, json('POST', { other_id: otherId }));
export const setDoNotContact = (personId: string, channel: Channel, reason: string) => apiRequestVoid(`${BASE}/${id(personId)}/do-not-contact`, json('PUT', { channel, reason }));
export const clearDoNotContact = (personId: string, channel: Channel, reason: string) => apiRequestVoid(`${BASE}/${id(personId)}/do-not-contact/${channel}`, json('DELETE', { reason }));
export function listProposals(offset: number, limit: number, state: 'open' | 'dismissed' | 'merged' = 'open'): Promise<ProposalPage> {
  return apiRequest<ProposalPage>(`${BASE}/proposals?${new URLSearchParams({ state, limit: String(limit), offset: String(offset) })}`, { method: 'GET' });
}
export const dismissProposal = (proposalId: string) => apiRequest<ProposalPage>(`${BASE}/proposals/${id(proposalId)}/dismiss`, { method: 'POST' });
export const rebuildDirectory = () => apiRequest<BuildReport>(`${BASE}/directory/rebuild`, { method: 'POST' });

/** Downloads the CSV (owners only) and returns the file name. */
export async function downloadPeople(): Promise<string> {
  const response = await fetch(`${apiBaseUrl}${BASE}/export`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const name = exportName(response.headers.get('content-disposition'));
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

/** What importing a CSV file would do, row by row (owners only); nothing is written. */
export const previewImport = (csv: string, fileName: string, mapping: ImportMapping | null) =>
  apiRequest<ImportPreview>(`${BASE}/import/preview`, json('POST', { csv, file_name: fileName, ...(mapping ? { mapping } : {}) }));
/** Imports the previewed file with every row's decision, all of it or nothing (owners only). */
export const applyImport = (csv: string, fileName: string, preview: ImportPreview, decisions: ImportDecision[]) =>
  apiRequest<ImportResult>(`${BASE}/import/apply`, json('POST', { csv, file_name: fileName, mapping: preview.mapping, fingerprint: preview.fingerprint, decisions }));
