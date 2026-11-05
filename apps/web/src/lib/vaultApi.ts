// The Document vault over the live API (`/api/v1/documents`, docs/documents.md#api).
import type { DocumentAccessGrant, DocumentExtraction, DocumentLink, VaultCategory, VaultDocument, VaultDocumentPatch, VaultSettings } from '@anum/contracts';
import { apiBaseUrl, apiRequest, apiRequestVoid, authHeaders } from './api';
import { ApiError, apiErrorFrom, apiErrorFromResponse } from './errors';
import { t } from './i18n';
import type { VaultDevice, VaultDeviceCreated, VaultDocumentDetail, VaultDocumentPage, VaultMeta, VaultReviewItem, VaultVersionPage } from './vault';

const BASE = '/api/v1/documents';
const id = encodeURIComponent;
const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const getMeta = () => apiRequest<VaultMeta>(`${BASE}/meta`, { method: 'GET' });
export const listDocuments = (query: string) => apiRequest<VaultDocumentPage>(`${BASE}?${query}`, { method: 'GET' });
export const getReview = () => apiRequest<VaultReviewItem[]>(`${BASE}/review`, { method: 'GET' });
export const getDocument = (documentId: string) => apiRequest<VaultDocumentDetail>(`${BASE}/${id(documentId)}`, { method: 'GET' });
export const patchDocument = (documentId: string, patch: VaultDocumentPatch) => apiRequest<VaultDocument>(`${BASE}/${id(documentId)}`, { method: 'PATCH', ...json(patch) });
export const trashDocument = (documentId: string) => apiRequest<VaultDocument>(`${BASE}/${id(documentId)}`, { method: 'DELETE' });
export const deleteForever = (documentId: string) => apiRequestVoid(`${BASE}/${id(documentId)}?permanent=true`, { method: 'DELETE' });
export const restoreDocument = (documentId: string) => apiRequest<VaultDocument>(`${BASE}/${id(documentId)}/restore`, { method: 'POST' });
export const revealNumber = (documentId: string) => apiRequest<{ number: string }>(`${BASE}/${id(documentId)}/number`, { method: 'POST' });
export const getVersions = (documentId: string) => apiRequest<VaultVersionPage>(`${BASE}/${id(documentId)}/versions`, { method: 'GET' });
export const restoreVersion = (documentId: string, versionId: string) => apiRequest<VaultDocument>(`${BASE}/${id(documentId)}/versions/${id(versionId)}/restore`, { method: 'POST' });
export const deleteVersion = (documentId: string, versionId: string) => apiRequestVoid(`${BASE}/${id(documentId)}/versions/${id(versionId)}`, { method: 'DELETE' });
export const share = (documentId: string, userId: string) => apiRequest<DocumentAccessGrant[]>(`${BASE}/${id(documentId)}/access`, { method: 'POST', ...json({ user_id: userId }) });
export const unshare = (documentId: string, userId: string) => apiRequest<DocumentAccessGrant[]>(`${BASE}/${id(documentId)}/access/${id(userId)}`, { method: 'DELETE' });
export const getPeople = () => apiRequest<{ user_id: string }[]>(`${BASE}/people`, { method: 'GET' });
export const addLink = (documentId: string, body: { target_type: string; target_id: string; label: string }) => apiRequest<DocumentLink>(`${BASE}/${id(documentId)}/links`, { method: 'POST', ...json(body) });
export const removeLink = (documentId: string, linkId: string) => apiRequestVoid(`${BASE}/${id(documentId)}/links/${id(linkId)}`, { method: 'DELETE' });
export const extract = (documentId: string) => apiRequest<DocumentExtraction>(`${BASE}/${id(documentId)}/extract`, { method: 'POST' });
export const confirmExtraction = (extractionId: string, fields: Record<string, string>, items?: DocumentExtraction['items']) =>
  apiRequest<VaultDocument>(`${BASE}/extractions/${id(extractionId)}/confirm`, { method: 'POST', ...json({ fields, items }) });
export const rejectExtraction = (extractionId: string) => apiRequest<DocumentExtraction>(`${BASE}/extractions/${id(extractionId)}/reject`, { method: 'POST' });
export const saveSettings = (body: Pick<VaultSettings, 'reminder_days' | 'trash_days' | 'ocr_receipts' | 'ocr_identity_dates'>) =>
  apiRequest<VaultSettings>(`${BASE}/settings`, { method: 'PUT', ...json(body) });
export const addCategory = (label: string) => apiRequest<VaultCategory>(`${BASE}/categories`, { method: 'POST', ...json({ label }) });
export const deleteCategory = (categoryId: string) => apiRequestVoid(`${BASE}/categories/${id(categoryId)}`, { method: 'DELETE' });
export const getDevices = () => apiRequest<VaultDevice[]>(`${BASE}/devices`, { method: 'GET' });
export const createDevice = (name: string) => apiRequest<VaultDeviceCreated>(`${BASE}/devices`, { method: 'POST', ...json({ name }) });
export const revokeDevice = (deviceId: string) => apiRequestVoid(`${BASE}/devices/${id(deviceId)}`, { method: 'DELETE' });

/** A short-lived signed link (absolute URL) for a download or an inline preview. */
export async function link(documentId: string, disposition: 'attachment' | 'inline'): Promise<string> {
  const made = await apiRequest<{ url: string; expires_at: string }>(`${BASE}/${id(documentId)}/link`, { method: 'POST', ...json({ disposition }) });
  return `${apiBaseUrl}${made.url}`;
}

/** The preview bytes through an inline link (images and PDFs). */
export async function previewBytes(documentId: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(await link(documentId, 'inline'), { signal });
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.blob();
}

/** Save the current version (or an older one) from a same-origin blob URL. */
export async function download(documentId: string, name: string, versionId?: string): Promise<void> {
  const query = versionId ? `?version_id=${id(versionId)}` : '';
  const response = await fetch(`${apiBaseUrl}${BASE}/${id(documentId)}/content${query}`, { headers: await authHeaders() });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = name;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function send<T>(path: string, file: File, onProgress?: (loaded: number) => void): Promise<T> {
  return authHeaders().then((headers) => new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${apiBaseUrl}${path}`);
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.setRequestHeader('content-type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (event) => onProgress?.(event.loaded);
    xhr.onload = () => {
      let body: unknown;
      try { body = JSON.parse(xhr.responseText); } catch { body = undefined; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(apiErrorFrom(xhr.status, body));
    };
    xhr.onerror = () => reject(new Error(t('vault.upload.lost')));
    xhr.send(file);
  }));
}

export interface UploadOptions { title: string; category: string; access_class: 'private' | 'workspace'; allowDuplicate?: boolean }

export function upload(file: File, options: UploadOptions, onProgress?: (loaded: number) => void): Promise<VaultDocument> {
  const params = new URLSearchParams({ name: file.name, title: options.title, category: options.category, access_class: options.access_class });
  if (options.allowDuplicate) params.set('allow_duplicate', 'true');
  return send<VaultDocument>(`${BASE}?${params.toString()}`, file, onProgress);
}

export const uploadVersion = (documentId: string, file: File) => send<VaultDocument>(`${BASE}/${id(documentId)}/versions`, file);

/** The existing document's id when an upload was refused as a duplicate. */
export function duplicateOf(error: unknown): { id: string; inTrash: boolean } | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const found = error.details.find((detail) => detail.type === 'duplicate');
  if (!found) return null;
  return { id: found.message, inTrash: error.details.some((detail) => detail.type === 'duplicate_status' && detail.message === 'deleted') };
}
