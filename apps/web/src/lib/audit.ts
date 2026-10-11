// Audit log and workspace administration: wire mapping, filter handling and display logic.
// docs/governance-and-scale.md (Audit log), docs/identity.md (Workspace lifecycle).
// Pure functions, so `pnpm --filter @anum/web test:unit` can exercise them without a DOM.
import { t } from './i18n.ts';
import type { AuditFilters, AuditPage, AuditRecord, MembersSummary, WorkspaceInfo, WorkspacePermissions, WorkspaceRole } from '@anum/contracts';

export const AUDIT_PAGE_SIZE = 50;
export const MEMBER_PAGE_SIZE = 50;

// ------------------------------------------------------------------ wire shapes

export interface ApiAuditRecord {
  id: string; tenant_id: string; workspace_id: string; actor: string; action: string; target: string;
  outcome: string; correlation_id: string; created_at: string; metadata: Record<string, unknown> | null;
}
export interface ApiAuditPage { items: ApiAuditRecord[]; next_cursor: string | null; retention?: string }
export interface ApiMembersSummary { total: number; active: number; deactivated: number; active_owners: number; pending_invitations: number }
export interface ApiWorkspace { id: string; tenant_id: string; name: string; created_at: string; updated_at: string; archived_at?: string | null; archived_by?: string | null }
export interface ApiWorkspacePermissions {
  workspace_id: string; role: string | null; organization_admin: boolean; can_manage_workspace: boolean;
  can_create_workspace: boolean; can_manage_members: boolean; can_read_audit: boolean; can_export_audit: boolean;
}

/** The API's retention text, or empty when it sent none (the view then shows `audit.defaultRetention`). */

export function mapAuditRecord(value: ApiAuditRecord): AuditRecord {
  return {
    id: value.id, tenantId: value.tenant_id, workspaceId: value.workspace_id, actor: value.actor, action: value.action,
    target: value.target, outcome: value.outcome, correlationId: value.correlation_id, createdAt: value.created_at,
    metadata: value.metadata && typeof value.metadata === 'object' ? value.metadata : {},
  };
}

export function mapAuditPage(value: ApiAuditPage): AuditPage {
  return { items: value.items.map(mapAuditRecord), nextCursor: value.next_cursor || null, retention: value.retention?.trim() || '' };
}

export function mapMembersSummary(value: ApiMembersSummary): MembersSummary {
  return { total: value.total, active: value.active, deactivated: value.deactivated, activeOwners: value.active_owners, pendingInvitations: value.pending_invitations };
}

export function mapWorkspace(value: ApiWorkspace): WorkspaceInfo {
  return { id: value.id, tenantId: value.tenant_id, name: value.name, createdAt: value.created_at, updatedAt: value.updated_at, archivedAt: value.archived_at ?? null, archivedBy: value.archived_by ?? null };
}

const ROLE_VALUES: readonly string[] = ['owner', 'member', 'viewer'];

export function mapPermissions(value: ApiWorkspacePermissions): WorkspacePermissions {
  return {
    workspaceId: value.workspace_id,
    role: value.role && ROLE_VALUES.includes(value.role) ? value.role as WorkspaceRole : null,
    organizationAdmin: value.organization_admin === true,
    canManageWorkspace: value.can_manage_workspace === true,
    canCreateWorkspace: value.can_create_workspace === true,
    canManageMembers: value.can_manage_members === true,
    canReadAudit: value.can_read_audit === true,
    canExportAudit: value.can_export_audit === true,
  };
}

// ------------------------------------------------------------------ filters

/** What the filter form holds: dates are `YYYY-MM-DD` from `<input type="date">`, in local time. */
export interface AuditFilterDraft { actor: string; action: string; target: string; outcome: string; from: string; to: string; q: string; correlationId: string }
export const EMPTY_FILTER_DRAFT: AuditFilterDraft = { actor: '', action: '', target: '', outcome: '', from: '', to: '', q: '', correlationId: '' };
export type DraftResult<T> = { ok: true; value: T } | { ok: false; error: string };

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Start of a local calendar day as an ISO instant; null when the text is not a real date. */
export function localDayStart(day: string): Date | null {
  if (!DATE.test(day)) return null;
  const [y, m, d] = day.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? date : null;
}

/**
 * Turn the form into API filters. The "to" day is inclusive for people, so the API's exclusive
 * `until` is the start of the next day.
 */
export function auditFilters(draft: AuditFilterDraft): DraftResult<AuditFilters> {
  const value: AuditFilters = {};
  const text = (key: keyof AuditFilters, raw: string, max: number, label: string): string | null => {
    const trimmed = raw.trim();
    if (!trimmed) return null;
    if (trimmed.length > max) return t('audit.tooLong', { label, max });
    value[key] = trimmed;
    return null;
  };
  const errors = [
    text('actor', draft.actor, 160, t('audit.actor')),
    text('action', draft.action, 160, t('audit.action')),
    text('target', draft.target, 200, t('audit.target')),
    text('outcome', draft.outcome, 40, t('audit.outcome')),
    text('correlationId', draft.correlationId, 160, t('audit.correlationId')),
    text('q', draft.q, 200, t('common.search')),
  ].filter(Boolean);
  if (errors.length) return { ok: false, error: errors[0] as string };
  let since: Date | null = null;
  let until: Date | null = null;
  if (draft.from) {
    since = localDayStart(draft.from);
    if (!since) return { ok: false, error: t('audit.badStart') };
    value.since = since.toISOString();
  }
  if (draft.to) {
    const end = localDayStart(draft.to);
    if (!end) return { ok: false, error: t('audit.badEnd') };
    until = new Date(end.getFullYear(), end.getMonth(), end.getDate() + 1);
    value.until = until.toISOString();
  }
  if (since && until && since >= until) return { ok: false, error: t('audit.reversed') };
  return { ok: true, value };
}

export function hasFilters(filters: AuditFilters): boolean {
  return Object.values(filters).some((value) => typeof value === 'string' && value.length > 0);
}

const QUERY_KEYS: [keyof AuditFilters, string][] = [['actor', 'actor'], ['action', 'action'], ['target', 'target'], ['outcome', 'outcome'], ['correlationId', 'correlation_id'], ['since', 'since'], ['until', 'until'], ['q', 'q']];

/** Query string for the list and the export (no leading `?`). */
export function auditQuery(filters: AuditFilters, extra: Record<string, string | number | null | undefined> = {}): string {
  const params = new URLSearchParams();
  for (const [key, name] of QUERY_KEYS) {
    const value = filters[key];
    if (value) params.set(name, value);
  }
  for (const [key, value] of Object.entries(extra)) if (value !== null && value !== undefined && value !== '') params.set(key, String(value));
  return params.toString();
}

/** Action filter options: the recorded actions plus a "family.*" prefix for each family with several. */
export function actionOptions(actions: readonly string[]): { value: string; label: string }[] {
  const families = new Map<string, number>();
  for (const action of actions) {
    const dot = action.indexOf('.');
    if (dot > 0) families.set(action.slice(0, dot), (families.get(action.slice(0, dot)) ?? 0) + 1);
  }
  const options: { value: string; label: string }[] = [];
  for (const [family, count] of [...families].sort(([a], [b]) => a.localeCompare(b))) {
    if (count > 1) options.push({ value: `${family}.*`, label: t('audit.actionFamily', { family: actionLabel(family) }, { isolate: false }) });
  }
  for (const action of [...actions].sort()) options.push({ value: action, label: action });
  return options;
}

// ------------------------------------------------------------------ display

/** "workspace_member.role_change" -> "Workspace member role change". */
export function actionLabel(action: string): string {
  const words = action.replace(/\*$/, '').replace(/[._]+/g, ' ').trim();
  return words ? words[0].toUpperCase() + words.slice(1) : action;
}

export function outcomeTone(outcome: string): 'success' | 'dangerTone' | 'warning' {
  if (outcome === 'success') return 'success';
  if (/fail|denied|refused|error|blocked/i.test(outcome)) return 'dangerTone';
  return 'warning';
}

/** Metadata as sorted key/value rows; nested values are shown as compact JSON. */
export function metadataRows(metadata: Record<string, unknown>): { key: string; value: string }[] {
  return Object.keys(metadata).sort().map((key) => {
    const value = metadata[key];
    return { key, value: value === null ? 'null' : typeof value === 'object' ? JSON.stringify(value) : String(value) };
  });
}

/** Filename from `Content-Disposition`, else a safe default. */
export function exportFilename(disposition: string | null, format: 'csv' | 'json'): string {
  const match = disposition?.match(/filename="?([^";]+)"?/i);
  const name = match?.[1]?.trim();
  return name && /^[\w.@-]+$/.test(name) ? name : `audit-export.${format}`;
}

// ------------------------------------------------------------------ members and workspaces

export type MemberStatusFilter = 'all' | 'active' | 'deactivated';
export type InvitationStatusFilter = 'all' | 'pending' | 'accepted' | 'revoked' | 'expired';

export function memberQuery(options: { q: string; status: MemberStatusFilter; role: WorkspaceRole | 'all'; cursor?: string | null; limit?: number }): string {
  const params = new URLSearchParams();
  if (options.q.trim()) params.set('q', options.q.trim());
  if (options.status !== 'all') params.set('status', options.status);
  if (options.role !== 'all') params.set('role', options.role);
  params.set('limit', String(options.limit ?? MEMBER_PAGE_SIZE));
  if (options.cursor) params.set('cursor', options.cursor);
  return params.toString();
}

/** The only active owner: the API refuses to demote or deactivate them (409). */
export function isOnlyActiveOwner(summary: MembersSummary | null, member: { active: boolean; role: WorkspaceRole }): boolean {
  return member.active && member.role === 'owner' && summary !== null && summary.activeOwners <= 1;
}

/** Validate a workspace name the way the API does (1 to 160 characters after collapsing spaces). */
export function workspaceName(raw: string): DraftResult<string> {
  const name = raw.split(/\s+/).filter(Boolean).join(' ');
  if (!name) return { ok: false, error: t('workspaceAdmin.nameRequired') };
  if (name.length > 160) return { ok: false, error: t('workspaceAdmin.nameTooLong') };
  return { ok: true, value: name };
}

/** Optional workspace id: empty lets the API generate one. */
export function workspaceIdInput(raw: string): DraftResult<string | null> {
  const value = raw.trim();
  if (!value) return { ok: true, value: null };
  return /^[A-Za-z0-9_-]{3,80}$/.test(value) ? { ok: true, value } : { ok: false, error: t('workspaceAdmin.idInvalid') };
}

/** Archiving needs the exact current name typed (the API checks it too). */
export function archiveConfirmed(typed: string, name: string): boolean {
  return typed.trim() === name;
}
