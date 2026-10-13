// Team work, assistants and organisations: pure helpers for the views (docs/team-work.md,
// docs/customer-organisations.md). No DOM and no fetch, so test/team.test.ts runs them directly.
import type {
  AssistantProfile,
  AssigneeType,
  OrganisationHealth,
  TeamPerson,
  WorkAssignee,
  WorkItem,
  WorkItemCreateBody,
  WorkPriority,
  WorkStatus,
} from '@anum/contracts';

export const STATUSES: WorkStatus[] = ['todo', 'in_progress', 'blocked', 'done', 'cancelled'];
export const OPEN_STATUSES: WorkStatus[] = ['todo', 'in_progress', 'blocked'];
export const BOARD_COLUMNS: WorkStatus[] = ['todo', 'in_progress', 'blocked', 'done'];
export const PRIORITIES: WorkPriority[] = ['urgent', 'high', 'normal', 'low'];

export interface AssigneeOption { value: string; label: string; type: AssigneeType; id: string }

/** "user:abc" style values for one <select>; ANUM and enabled assistants after the people. */
export function assigneeOptions(people: TeamPerson[], assistants: AssistantProfile[], anumLabel: string): AssigneeOption[] {
  return [
    ...people.filter((p) => p.assignable).map((p) => ({ value: `user:${p.user_id}`, label: p.display_name, type: 'user' as const, id: p.user_id })),
    { value: 'anum:anum', label: anumLabel, type: 'anum' as const, id: 'anum' },
    ...assistants.filter((a) => a.enabled).map((a) => ({ value: `assistant:${a.id}`, label: a.name, type: 'assistant' as const, id: a.id })),
  ];
}

export function assigneeValue(type: AssigneeType | null, id: string | null): string {
  return type && id ? `${type}:${id}` : '';
}

export function parseAssignee(value: string): WorkAssignee | null {
  const at = value.indexOf(':');
  if (at <= 0) return null;
  const type = value.slice(0, at);
  if (type !== 'user' && type !== 'assistant' && type !== 'anum') return null;
  const id = value.slice(at + 1);
  return id ? { type, id } : null;
}

/** A `<input type="datetime-local">` value ("2026-10-15T17:00") as ISO 8601 with this browser's offset. */
export function localInputToIso(value: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const [, y, mo, d, h, mi] = match.map(Number);
  const date = new Date(y, mo - 1, d, h, mi);
  if (Number.isNaN(date.getTime()) || date.getMonth() !== mo - 1) return null;
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const pad = (n: number) => String(Math.abs(n)).padStart(2, '0');
  return `${y}-${pad(mo)}-${pad(d)}T${pad(h)}:${pad(mi)}:00${sign}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`;
}

/** An ISO instant as a `datetime-local` value in this browser's time zone. */
export function isoToLocalInput(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export type DueState = 'none' | 'overdue' | 'today' | 'soon' | 'later' | 'closed';

/** How urgent the due date looks: overdue, today, within three days, later. */
export function dueState(item: Pick<WorkItem, 'due_at' | 'status'>, now: Date = new Date()): DueState {
  if (!item.due_at) return 'none';
  if (!OPEN_STATUSES.includes(item.status)) return 'closed';
  const due = new Date(item.due_at);
  if (due.getTime() < now.getTime()) return 'overdue';
  const sameDay = due.toDateString() === now.toDateString();
  if (sameDay) return 'today';
  return due.getTime() - now.getTime() <= 3 * 86_400_000 ? 'soon' : 'later';
}

export interface DraftErrors { title?: 'required'; estimate?: 'range'; due?: 'invalid'; followUp?: 'invalid' }

export interface WorkDraft { title: string; description: string; assignee: string; priority: WorkPriority; due: string; followUp: string; estimate: string; checklist: string }

export const emptyDraft = (): WorkDraft => ({ title: '', description: '', assignee: '', priority: 'normal', due: '', followUp: '', estimate: '', checklist: '' });

/** Validate a new-work form like the API does; returns the body or the field errors. */
export function draftBody(draft: WorkDraft): { body: WorkItemCreateBody | null; errors: DraftErrors } {
  const errors: DraftErrors = {};
  const title = draft.title.trim().replace(/\s+/g, ' ');
  if (!title) errors.title = 'required';
  let estimate: number | null = null;
  if (draft.estimate.trim()) {
    estimate = Number(draft.estimate);
    if (!Number.isFinite(estimate) || estimate < 0 || estimate > 1000) errors.estimate = 'range';
  }
  const due = draft.due ? localInputToIso(draft.due) : null;
  if (draft.due && !due) errors.due = 'invalid';
  const followUp = draft.followUp ? localInputToIso(draft.followUp) : null;
  if (draft.followUp && !followUp) errors.followUp = 'invalid';
  if (Object.keys(errors).length) return { body: null, errors };
  const checklist = draft.checklist.split('\n').map((line) => line.trim()).filter(Boolean).slice(0, 50);
  return {
    body: {
      title: title.slice(0, 200), description: draft.description.trim(), priority: draft.priority,
      assignee: parseAssignee(draft.assignee), due_at: due, follow_up_at: followUp, estimate_hours: estimate, checklist,
    },
    errors,
  };
}

/** The list query for a view ("mine", the board column, or a search). */
export function workQuery(params: { scope?: 'all' | 'mine' | 'created' | 'anum'; status?: string; q?: string; sort?: string; limit?: number; offset?: number; overdue?: boolean; assigneeType?: AssigneeType; assigneeId?: string }): string {
  const query = new URLSearchParams();
  if (params.scope && params.scope !== 'all') query.set('scope', params.scope);
  if (params.status) query.set('status', params.status);
  if (params.q?.trim()) query.set('q', params.q.trim());
  if (params.sort) query.set('sort', params.sort);
  if (params.overdue) query.set('overdue', 'true');
  if (params.assigneeType) query.set('assignee_type', params.assigneeType);
  if (params.assigneeId) query.set('assignee_id', params.assigneeId);
  query.set('limit', String(params.limit ?? 50));
  if (params.offset) query.set('offset', String(params.offset));
  return `/api/v1/work-items?${query}`;
}

/** Workload bar tone: over capacity, near it, fine, or unknown capacity. */
export function loadTone(percent: number | null): 'over' | 'high' | 'ok' | 'unknown' {
  if (percent === null) return 'unknown';
  if (percent > 100) return 'over';
  return percent >= 80 ? 'high' : 'ok';
}

export function healthTone(health: OrganisationHealth): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  return health === 'active' ? 'success' : health === 'attention' ? 'dangerTone' : health === 'setting_up' ? 'warning' : 'neutral';
}

/** An organisation id suggested from its name: lowercase letters, digits, "_" and "-", 3 to 80 characters. */
export function slugify(name: string): string {
  const slug = name.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 80);
  return slug.length >= 3 ? slug : slug ? `${slug}_org`.slice(0, 80) : '';
}

export const TENANT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{2,79}$/;
export const EMAIL_PATTERN = /^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$/;

/**
 * The first admin's invitation link. The token travels in the fragment (never sent to a server);
 * `tenant` names the new organisation so the web client sends it as x-tenant-id when accepting.
 */
export function organisationInvitationLink(baseUrl: string, token: string, workspaceId: string, tenantId: string): string {
  const base = baseUrl.replace(/#.*$/, '');
  const fragment = new URLSearchParams({ invitation: token, workspace: workspaceId, tenant: tenantId });
  return `${base}#${fragment.toString()}`;
}

/** Desktop notification decision for a `notification.created` event (pure, so it is testable). */
export function shouldNotifyDesktop(input: { recipient: unknown; me: string | null; hidden: boolean; desktopEnabled: boolean; permission: string }): boolean {
  return input.recipient === input.me && input.me !== null && input.hidden && input.desktopEnabled && input.permission === 'granted';
}

/** Which board column a status belongs to (cancelled items are listed under Done). */
export function boardColumn(status: WorkStatus): WorkStatus {
  return status === 'cancelled' ? 'done' : status;
}

/** The Team page's band (docs/team-work.md#dashboard): open, in progress, overdue and unassigned work across the team. */
export function workloadTotals(workload: { rows: readonly { open: number; in_progress: number; blocked: number; overdue: number; due_this_week: number }[]; unassigned_open: number }) {
  const sum = (key: 'open' | 'in_progress' | 'blocked' | 'overdue' | 'due_this_week') => workload.rows.reduce((total, row) => total + row[key], 0);
  return { open: sum('open') + workload.unassigned_open, inProgress: sum('in_progress'), blocked: sum('blocked'), overdue: sum('overdue'), dueThisWeek: sum('due_this_week'), unassigned: workload.unassigned_open, people: workload.rows.length };
}

/**
 * The Assistants page's band: assistants switched on, and their open, overdue, due-this-week and
 * finished work. `done` is null while any assistant's finished count is unknown (never shown as 0).
 */
export function assistantTotals(
  assistants: readonly { id: string; enabled: boolean }[],
  rows: readonly { assignee_type: string; assignee_id: string; open: number; overdue: number; due_this_week: number }[],
  done: Readonly<Record<string, number | null>>,
) {
  const ids = new Set(assistants.map((a) => a.id));
  const mine = rows.filter((row) => row.assignee_type === 'assistant' && ids.has(row.assignee_id));
  const finished = assistants.map((a) => done[a.id]);
  return {
    total: assistants.length, enabled: assistants.filter((a) => a.enabled).length,
    open: mine.reduce((sum, row) => sum + row.open, 0), overdue: mine.reduce((sum, row) => sum + row.overdue, 0), dueWeek: mine.reduce((sum, row) => sum + row.due_this_week, 0),
    done: finished.some((value) => value === null || value === undefined) ? null : finished.reduce<number>((sum, value) => sum + (value ?? 0), 0),
  };
}

/** Whether an assistant matches the table's search: name, department, keywords or tools. */
export function assistantMatches(a: { name: string; department: string; routing_keywords: readonly string[]; allowed_tools: readonly string[] }, query: string, department: (d: string) => string = (d) => d): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [a.name, department(a.department), a.department, ...a.routing_keywords, ...a.allowed_tools].some((value) => value.toLowerCase().includes(q));
}

/** The platform console's band: organisations, those needing attention, and their summed counts. */
export function organisationTotals(orgs: readonly { health: string; workspaces: number; active_members: number; pending_invitations: number; open_work_items: number; overdue_work_items: number }[]) {
  const sum = (key: 'workspaces' | 'active_members' | 'pending_invitations' | 'open_work_items' | 'overdue_work_items') => orgs.reduce((total, org) => total + org[key], 0);
  return { count: orgs.length, attention: orgs.filter((org) => org.health === 'attention').length, workspaces: sum('workspaces'), members: sum('active_members'), invitations: sum('pending_invitations'), openWork: sum('open_work_items'), overdue: sum('overdue_work_items') };
}

/** Whether an organisation matches the console's search: its name or tenant id. */
export function organisationMatches(org: { display_name: string; tenant_id: string }, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || org.display_name.toLowerCase().includes(q) || org.tenant_id.toLowerCase().includes(q);
}
