// Organisation export and deletion, and workspace deletion (docs/organisation-lifecycle.md).
// Pure helpers: map the API's snake_case page to camelCase, status labels and tones, the typed
// confirmation check and the time left before a purge. `pnpm --filter @anum/web test:unit` runs them.
import { formatBytes } from './files.ts';
import { t, type MessageKey } from './i18n.ts';

export type LifecycleKind = 'organisation_export' | 'organisation_deletion' | 'workspace_deletion';
export type ExportStatus = 'queued' | 'running' | 'ready' | 'failed' | 'expired';
export type DeletionStatus = 'scheduled' | 'cancelled' | 'purging' | 'completed' | 'failed';
export type LifecycleStatus = ExportStatus | DeletionStatus;
export type StatusTone = 'success' | 'warning' | 'danger' | 'neutral';

/** A job row as the API sends it. */
export interface ApiLifecycleJob {
  id: string;
  kind: LifecycleKind;
  status: LifecycleStatus;
  workspace_id: string | null;
  requested_by: string | null;
  requested_at: string;
  purge_after: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancelled_at: string | null;
  cancelled_by: string | null;
  expires_at: string | null;
  archive_bytes: number | null;
  archive_sha256: string | null;
  row_count: number | null;
  file_count: number | null;
  error: string | null;
}

/** `GET /api/v1/organization/lifecycle`. */
export interface ApiLifecyclePage {
  can_manage_organisation: boolean;
  can_delete_workspace: boolean;
  organisation_confirmation: string;
  workspace_confirmation: string;
  cooling_off_hours: number;
  export_link_ttl_seconds: number;
  export_retention_hours: number;
  exports: ApiLifecycleJob[] | null;
  organisation_deletion: ApiLifecycleJob | null;
  workspace_deletion: ApiLifecycleJob | null;
}

export interface ApiExportLink { url: string; expires_at: string }

export interface LifecycleJob {
  id: string;
  kind: LifecycleKind;
  status: LifecycleStatus;
  workspaceId: string | null;
  requestedBy: string | null;
  requestedAt: string;
  purgeAfter: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  cancelledAt: string | null;
  cancelledBy: string | null;
  expiresAt: string | null;
  archiveBytes: number | null;
  archiveSha256: string | null;
  rowCount: number | null;
  fileCount: number | null;
  error: string | null;
}

export interface LifecyclePage {
  canManageOrganisation: boolean;
  canDeleteWorkspace: boolean;
  organisationConfirmation: string;
  workspaceConfirmation: string;
  coolingOffHours: number;
  exportLinkTtlSeconds: number;
  exportRetentionHours: number;
  exports: LifecycleJob[];
  organisationDeletion: LifecycleJob | null;
  workspaceDeletion: LifecycleJob | null;
}

export interface ExportLink { url: string; expiresAt: string }

export function mapJob(job: ApiLifecycleJob): LifecycleJob {
  return {
    id: job.id,
    kind: job.kind,
    status: job.status,
    workspaceId: job.workspace_id ?? null,
    requestedBy: job.requested_by ?? null,
    requestedAt: job.requested_at,
    purgeAfter: job.purge_after ?? null,
    startedAt: job.started_at ?? null,
    finishedAt: job.finished_at ?? null,
    cancelledAt: job.cancelled_at ?? null,
    cancelledBy: job.cancelled_by ?? null,
    expiresAt: job.expires_at ?? null,
    archiveBytes: job.archive_bytes ?? null,
    archiveSha256: job.archive_sha256 ?? null,
    rowCount: job.row_count ?? null,
    fileCount: job.file_count ?? null,
    error: job.error ?? null,
  };
}

export function mapLifecycle(page: ApiLifecyclePage): LifecyclePage {
  return {
    canManageOrganisation: Boolean(page.can_manage_organisation),
    canDeleteWorkspace: Boolean(page.can_delete_workspace),
    organisationConfirmation: page.organisation_confirmation,
    workspaceConfirmation: page.workspace_confirmation,
    coolingOffHours: page.cooling_off_hours,
    exportLinkTtlSeconds: page.export_link_ttl_seconds,
    exportRetentionHours: page.export_retention_hours,
    exports: (page.exports ?? []).map(mapJob),
    organisationDeletion: page.organisation_deletion ? mapJob(page.organisation_deletion) : null,
    workspaceDeletion: page.workspace_deletion ? mapJob(page.workspace_deletion) : null,
  };
}

export function mapExportLink(link: ApiExportLink): ExportLink {
  return { url: link.url, expiresAt: link.expires_at };
}

const STATUSES: readonly LifecycleStatus[] = ['queued', 'running', 'ready', 'failed', 'expired', 'scheduled', 'cancelled', 'purging', 'completed'];

/** The catalog key of a status label; an unknown status reads as `lifecycle.status.unknown`. */
export function statusLabelKey(status: string): MessageKey {
  return (STATUSES as readonly string[]).includes(status) ? `lifecycle.status.${status}` as MessageKey : 'lifecycle.status.unknown';
}

export function statusLabel(status: string): string {
  return t(statusLabelKey(status));
}

export function statusTone(status: string): StatusTone {
  switch (status) {
    case 'ready': case 'completed': return 'success';
    case 'queued': case 'running': case 'scheduled': case 'purging': return 'warning';
    case 'failed': return 'danger';
    default: return 'neutral';
  }
}

/** The typed confirmation must equal the expected text exactly, ignoring outer spaces. */
export function confirmationMatches(typed: string, expected: string): boolean {
  return expected.length > 0 && typed.trim() === expected;
}

/** An export still being built: the page polls while one exists. */
export function isActiveExport(job: LifecycleJob): boolean {
  return job.status === 'queued' || job.status === 'running';
}

export function hasActiveExport(exports: readonly LifecycleJob[]): boolean {
  return exports.some(isActiveExport);
}

/** A deletion still open (it can be cancelled while `scheduled`). */
export function isOpenDeletion(job: LifecycleJob | null): job is LifecycleJob {
  return job !== null && (job.status === 'scheduled' || job.status === 'purging');
}

/** Whether a new export may be requested: no export in progress and the organisation is not being purged. */
export function canRequestExport(page: LifecyclePage): boolean {
  return page.canManageOrganisation && !hasActiveExport(page.exports) && page.organisationDeletion?.status !== 'purging';
}

/** Whether the page should keep polling: an export is being built or a purge is running. */
export function shouldPoll(page: LifecyclePage): boolean {
  return hasActiveExport(page.exports) || page.organisationDeletion?.status === 'purging' || page.workspaceDeletion?.status === 'purging';
}

/** An archive size for a person; `-` when there is none yet. */
export function archiveSize(bytes: number | null): string {
  return bytes === null || !Number.isFinite(bytes) || bytes < 0 ? '-' : formatBytes(bytes);
}

/** The cooling-off period as words: whole days when divisible by 24, otherwise hours. */
export function coolingOffText(hours: number): string {
  if (hours >= 24 && hours % 24 === 0) return t('lifecycle.days', { count: hours / 24 });
  return t('lifecycle.hours', { count: hours });
}

export interface TimeLeft { days: number; hours: number; minutes: number; due: boolean }

/** Time left until `purgeAfter` (an ISO date) from `now`; `due` once it has passed. */
export function timeUntil(purgeAfter: string, now: number = Date.now()): TimeLeft {
  const target = Date.parse(purgeAfter);
  const left = Number.isNaN(target) ? 0 : target - now;
  if (left <= 0) return { days: 0, hours: 0, minutes: 0, due: true };
  const minutesTotal = Math.ceil(left / 60_000);
  return { days: Math.floor(minutesTotal / 1440), hours: Math.floor((minutesTotal % 1440) / 60), minutes: minutesTotal % 60, due: false };
}

/** "6 days and 3 hours left", "45 minutes left", or "Due now" once the purge may start. */
export function timeLeftText(purgeAfter: string, now: number = Date.now()): string {
  const left = timeUntil(purgeAfter, now);
  if (left.due) return t('lifecycle.left.due');
  if (left.days > 0) {
    return left.hours > 0
      ? t('lifecycle.left.daysHours', { days: t('lifecycle.days', { count: left.days }), hours: t('lifecycle.hours', { count: left.hours }) })
      : t('lifecycle.left.one', { amount: t('lifecycle.days', { count: left.days }) });
  }
  if (left.hours > 0) return t('lifecycle.left.one', { amount: t('lifecycle.hours', { count: left.hours }) });
  return t('lifecycle.left.one', { amount: t('lifecycle.minutes', { count: left.minutes }) });
}

// --- Deleted organisations in the platform console (read only).

export type IdentityStatus = 'pending' | 'removed' | 'not_configured' | 'failed';

/** `GET /api/v1/platform/organizations/deleted`, one row. */
export interface ApiDeletedOrganisation {
  tenant_id: string;
  display_name: string | null;
  plan: string | null;
  requested_by: string;
  requested_at: string;
  purge_started_at: string;
  deleted_at: string | null;
  status: 'purging' | 'deleted';
  row_count: number;
  file_count: number;
  counts: Record<string, number>;
  identity_cleanup: { status: IdentityStatus; removed: number; detached: number; finished_at: string | null };
}

export interface DeletedOrganisation {
  tenantId: string;
  name: string;
  plan: string | null;
  requestedBy: string;
  requestedAt: string;
  purgeStartedAt: string;
  deletedAt: string | null;
  deleting: boolean;
  rows: number;
  files: number;
  counts: { table: string; rows: number }[];
  identity: { status: IdentityStatus; removed: number; detached: number };
}

/** Maps a receipt; tables sorted by rows deleted (largest first), empty ones left out. */
export function mapDeletedOrganisation(row: ApiDeletedOrganisation): DeletedOrganisation {
  const counts = Object.entries(row.counts ?? {})
    .filter(([, rows]) => rows > 0)
    .map(([table, rows]) => ({ table, rows }))
    .sort((a, b) => b.rows - a.rows || a.table.localeCompare(b.table));
  return {
    tenantId: row.tenant_id,
    name: row.display_name || row.tenant_id,
    plan: row.plan,
    requestedBy: row.requested_by,
    requestedAt: row.requested_at,
    purgeStartedAt: row.purge_started_at,
    deletedAt: row.deleted_at,
    deleting: row.status !== 'deleted',
    rows: row.row_count,
    files: row.file_count,
    counts,
    identity: { status: row.identity_cleanup.status, removed: row.identity_cleanup.removed, detached: row.identity_cleanup.detached },
  };
}

/** Newest deletion first (a purge still running counts from when it started). */
export function sortDeleted(rows: readonly DeletedOrganisation[]): DeletedOrganisation[] {
  const at = (row: DeletedOrganisation) => Date.parse(row.deletedAt ?? row.purgeStartedAt) || 0;
  return [...rows].sort((a, b) => at(b) - at(a) || a.tenantId.localeCompare(b.tenantId));
}

/** The sentence for what happened to the sign-in accounts. */
export function identityText(identity: DeletedOrganisation['identity']): string {
  if (identity.status === 'removed') return t('lifecycle.deleted.identity.removed', { removed: identity.removed, detached: identity.detached });
  return t(`lifecycle.deleted.identity.${identity.status}` as MessageKey);
}

/** The API answers a relative download path; make it absolute against the API base. */
export function absoluteUrl(base: string, url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  return `${base.replace(/\/+$/, '')}/${url.replace(/^\/+/, '')}`;
}
