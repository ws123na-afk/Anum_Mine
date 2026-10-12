// Favorites, knowledge base and reports API (docs/knowledge-base.md, docs/reports.md).
import type {
  Favorite, FavoritePage, FavoriteType, KnowledgeAnswer, KnowledgeCollection, KnowledgeItem, KnowledgeItemKind,
  KnowledgeItemPage, KnowledgeReindexResult, KnowledgeSearchResult, Report, ReportCreatedByKind, ReportGenerator,
  ReportPage, ReportShareResult, SearchMode, Visibility,
} from '@anum/contracts';
import { apiBaseUrl, apiRequest, authHeaders } from './api';
import { createSchedule, createWorkflow } from './automationApi';
import { apiErrorFromResponse } from './errors';
import { t } from './i18n';
import { downloadName } from './library';

const enc = encodeURIComponent;
const query = (params: Record<string, string | number | null | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : '';
};

async function send(path: string, method: 'DELETE'): Promise<void> {
  const response = await fetch(`${apiBaseUrl}${path}`, { method, headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
}

// ---- Favorites ----
export const listFavorites = (type: FavoriteType | null, offset = 0, limit = 30) =>
  apiRequest<FavoritePage>(`/api/v1/favorites${query({ item_type: type, offset, limit })}`, { method: 'GET' });
export const addFavorite = (type: FavoriteType, id: string) =>
  apiRequest<Favorite>('/api/v1/favorites', { method: 'POST', body: JSON.stringify({ item_type: type, item_id: id }) });
export const removeFavorite = (type: FavoriteType, id: string) => send(`/api/v1/favorites${query({ item_type: type, item_id: id })}`, 'DELETE');

// ---- Knowledge base ----
export const listCollections = () => apiRequest<KnowledgeCollection[]>('/api/v1/knowledge/collections', { method: 'GET' });
export const getCollection = (id: string) => apiRequest<KnowledgeCollection>(`/api/v1/knowledge/collections/${enc(id)}`, { method: 'GET' });
export const createCollection = (body: { name: string; description: string; visibility: Visibility }) =>
  apiRequest<KnowledgeCollection>('/api/v1/knowledge/collections', { method: 'POST', body: JSON.stringify(body) });
export const updateCollection = (id: string, body: { name?: string; description?: string }) =>
  apiRequest<KnowledgeCollection>(`/api/v1/knowledge/collections/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteCollection = (id: string) => send(`/api/v1/knowledge/collections/${enc(id)}`, 'DELETE');
export const listItems = (id: string, filters: { q?: string; tag?: string; kind?: KnowledgeItemKind | '' }, offset = 0, limit = 50) =>
  apiRequest<KnowledgeItemPage>(`/api/v1/knowledge/collections/${enc(id)}/items${query({ ...filters, offset, limit })}`, { method: 'GET' });
export const getItem = (id: string) => apiRequest<KnowledgeItem>(`/api/v1/knowledge/items/${enc(id)}`, { method: 'GET' });
export const createNote = (collectionId: string, body: { title: string; content: string; tags: string[] }) =>
  apiRequest<KnowledgeItem>(`/api/v1/knowledge/collections/${enc(collectionId)}/notes`, { method: 'POST', body: JSON.stringify(body) });
export const linkFile = (collectionId: string, fileId: string, tags: string[]) =>
  apiRequest<KnowledgeItem>(`/api/v1/knowledge/collections/${enc(collectionId)}/files`, { method: 'POST', body: JSON.stringify({ file_id: fileId, tags }) });
export const updateItem = (id: string, body: { title?: string; tags?: string[]; content?: string }) =>
  apiRequest<KnowledgeItem>(`/api/v1/knowledge/items/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteItem = (id: string) => send(`/api/v1/knowledge/items/${enc(id)}`, 'DELETE');
export const reindexCollection = (id: string) =>
  apiRequest<KnowledgeReindexResult>(`/api/v1/knowledge/collections/${enc(id)}/reindex`, { method: 'POST' });
export const searchKnowledge = (q: string, collectionId: string | null, mode: SearchMode = 'hybrid') =>
  apiRequest<KnowledgeSearchResult>(`/api/v1/knowledge/search${query({ q, collection_id: collectionId, mode })}`, { method: 'GET' });
export const askKnowledge = (question: string, collectionId: string | null) =>
  apiRequest<KnowledgeAnswer>('/api/v1/knowledge/ask', { method: 'POST', body: JSON.stringify({ question, collection_id: collectionId }) });

/** Upload a document into a collection; the name goes in the query so any language works. */
export async function uploadDocument(collectionId: string, file: File, tags: string[]): Promise<KnowledgeItem> {
  const headers: Record<string, string> = { ...(await authHeaders()), 'content-type': file.type || 'application/octet-stream' };
  if (tags.length) headers['x-item-tags'] = tags.join(',');
  const response = await fetch(`${apiBaseUrl}/api/v1/knowledge/collections/${enc(collectionId)}/documents${query({ name: file.name })}`, {
    method: 'POST', headers, body: file,
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.json() as Promise<KnowledgeItem>;
}

// ---- Reports ----
export interface ReportQuery { type?: string; q?: string; created_by_kind?: ReportCreatedByKind | '' }
export const listReports = (filters: ReportQuery, offset = 0, limit = 20) =>
  apiRequest<ReportPage>(`/api/v1/reports${query({ ...filters, offset, limit })}`, { method: 'GET' });
export const getReport = (id: string) => apiRequest<Report>(`/api/v1/reports/${enc(id)}`, { method: 'GET' });
export const listGenerators = () => apiRequest<ReportGenerator[]>('/api/v1/reports/generators', { method: 'GET' });
export const generateReport = (body: { type: ReportGenerator['type']; period_days: number; visibility: Visibility }) =>
  apiRequest<Report>('/api/v1/reports/generate', { method: 'POST', body: JSON.stringify(body) });
export const updateReport = (id: string, body: { title?: string; visibility?: Visibility }) =>
  apiRequest<Report>(`/api/v1/reports/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
export const deleteReport = (id: string) => send(`/api/v1/reports/${enc(id)}`, 'DELETE');

/** Save the report's CSV or Markdown export; resolves with the file name. */
export async function downloadReport(report: Report, format: 'csv' | 'md'): Promise<string> {
  const response = await fetch(`${apiBaseUrl}/api/v1/reports/${enc(report.id)}/export${query({ format })}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const blob = await response.blob();
  const name = downloadName(response.headers.get('content-disposition'), `${report.type}.${format}`);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

/** "Schedule": an automation workflow with one `report.generate` step, and its schedule. */
export async function scheduleReport(type: ReportGenerator['type'], periodDays: number, cron: string, timezone: string): Promise<string> {
  const name = t('reports.schedule.workflowName', { type: t(`reports.type.${type}`) });
  const workflow = await createWorkflow({
    name, description: t('reports.schedule.workflowDescription'), overlap_policy: 'skip',
    steps: [{ id: 'report', name, action: 'report.generate', input: { type, period_days: periodDays }, max_attempts: 3 }],
  });
  await createSchedule({ workflow_id: workflow.id, name, cron, timezone, enabled: true });
  return name;
}


/** Ask to send the report by email from a connected mailbox: it waits for approval. */
export const shareReportByEmail = (id: string, body: { account_id: string; to: string[]; note: string; include_table: boolean }) =>
  apiRequest<ReportShareResult>(`/api/v1/reports/${enc(id)}/share/email`, { method: 'POST', body: JSON.stringify(body) });
/** Ask to send the report's figures as a WhatsApp reply: it waits for approval. */
export const shareReportByWhatsApp = (id: string, body: { conversation_id: string; note: string }) =>
  apiRequest<ReportShareResult>(`/api/v1/reports/${enc(id)}/share/whatsapp`, { method: 'POST', body: JSON.stringify(body) });
