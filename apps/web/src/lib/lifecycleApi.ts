// Organisation export and deletion, and workspace deletion, over the live API
// (docs/organisation-lifecycle.md, API). Every permission check is the API's.
import { apiBaseUrl, apiRequest } from './api';
import {
  absoluteUrl,
  mapDeletedOrganisation,
  sortDeleted,
  type ApiDeletedOrganisation,
  type DeletedOrganisation,
  mapExportLink,
  mapJob,
  mapLifecycle,
  type ApiExportLink,
  type ApiLifecycleJob,
  type ApiLifecyclePage,
  type ExportLink,
  type LifecycleJob,
  type LifecyclePage,
} from './lifecycle';

const base = '/api/v1/organization/lifecycle';

/** The platform operator's deleted organisations (receipts only), newest first. */
export async function getDeletedOrganisations(): Promise<DeletedOrganisation[]> {
  const rows = await apiRequest<ApiDeletedOrganisation[]>('/api/v1/platform/organizations/deleted', { method: 'GET' });
  return sortDeleted(rows.map(mapDeletedOrganisation));
}

export async function getLifecycle(): Promise<LifecyclePage> {
  return mapLifecycle(await apiRequest<ApiLifecyclePage>(base, { method: 'GET' }));
}

/** Queues an export (`202`); `409` while another export is in progress or the organisation is being deleted. */
export async function requestExport(): Promise<LifecycleJob> {
  return mapJob(await apiRequest<ApiLifecycleJob>(`${base}/exports`, { method: 'POST' }));
}

/** A signed, short-lived download link; `409` until ready, `410` once expired. The URL is absolute. */
export async function createExportLink(jobId: string): Promise<ExportLink> {
  const link = mapExportLink(await apiRequest<ApiExportLink>(`${base}/exports/${encodeURIComponent(jobId)}/link`, { method: 'POST' }));
  return { ...link, url: absoluteUrl(apiBaseUrl, link.url) };
}

/** Gets a fresh link and starts the download; the signed token is the credential, so a plain link works. */
export async function downloadExport(jobId: string): Promise<ExportLink> {
  const link = await createExportLink(jobId);
  const anchor = document.createElement('a');
  anchor.href = link.url;
  anchor.rel = 'noopener';
  anchor.download = '';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  return link;
}

export async function scheduleOrganisationDeletion(confirm: string): Promise<LifecycleJob> {
  return mapJob(await apiRequest<ApiLifecycleJob>(`${base}/deletion`, { method: 'POST', body: JSON.stringify({ confirm }) }));
}

export async function cancelOrganisationDeletion(): Promise<LifecycleJob> {
  return mapJob(await apiRequest<ApiLifecycleJob>(`${base}/deletion/cancel`, { method: 'POST' }));
}

export async function scheduleWorkspaceDeletion(confirmName: string): Promise<LifecycleJob> {
  return mapJob(await apiRequest<ApiLifecycleJob>('/api/v1/workspaces/current/deletion', { method: 'POST', body: JSON.stringify({ confirm_name: confirmName }) }));
}

export async function cancelWorkspaceDeletion(): Promise<LifecycleJob> {
  return mapJob(await apiRequest<ApiLifecycleJob>('/api/v1/workspaces/current/deletion/cancel', { method: 'POST' }));
}
