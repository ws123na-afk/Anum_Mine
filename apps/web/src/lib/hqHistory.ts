/**
 * Office history panel: the pure rules (query building, paging, scopes, source references).
 * Server: services/api/anum_api/office_history_api.py (contract: packages/contracts/src/officeHistory.ts,
 * spec: docs/ANUM_MINE_HQ_HISTORY_PANEL.md).
 *
 * Data truth: every row is one stored audit record. Nothing here infers an event, a movement or a
 * past state. A scope (room or agent) only keeps the loaded records that name that room, desk or
 * agent; a record that does not name it is not guessed into the view.
 */
import type { OfficeHistoryCategory, OfficeHistoryEvidence, OfficeHistoryPage } from '@anum/contracts';

export const HISTORY_PATH = '/api/v1/office-history';
export const PAGE_SIZE = 60;
/** Underlying pages read for one "load" before the panel stops and offers to continue. */
export const MAX_PAGES_PER_LOAD = 6;
/** Records a load tries to add before it stops early. */
export const WANT_PER_LOAD = 25;

export const CATEGORY_IDS: readonly OfficeHistoryCategory[] = [
  'all', 'task', 'agent_run', 'approval', 'meeting', 'office', 'team_work', 'workspace', 'membership', 'automation', 'autonomy', 'other',
];

/** The audit action prefix of each category; `all` and `other` have none, so the CSV export is not offered for them. */
export const CATEGORY_ACTION_PREFIX: Readonly<Partial<Record<OfficeHistoryCategory, string>>> = {
  task: 'task.', agent_run: 'agent_run.', approval: 'approval.', meeting: 'agent_meeting.', office: 'office.',
  team_work: 'work_item.', workspace: 'workspace.', membership: 'workspace_member.', automation: 'automation.', autonomy: 'autonomy.',
};

export interface HistoryFilters {
  q: string;
  /** Exact actor id (server filter). */
  actor: string;
  category: OfficeHistoryCategory;
  /** Local calendar days, `YYYY-MM-DD`, both inclusive. */
  from: string;
  to: string;
}
export const EMPTY_FILTERS: HistoryFilters = { q: '', actor: '', category: 'all', from: '', to: '' };

export type HistoryScope =
  | { kind: 'office' }
  | { kind: 'room'; roomId: string; label: string; deskIds: readonly string[] }
  | { kind: 'agent'; agentId: string; name: string }
  | { kind: 'correlation'; correlationId: string };

export const OFFICE_SCOPE: HistoryScope = { kind: 'office' };

export type QueryResult = { ok: true; path: string; since: string | null; until: string | null } | { ok: false; error: 'badFrom' | 'badTo' | 'reversed' | 'tooLong' };

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Start of a local calendar day; null when the text is not a real date. */
export function localDay(day: string): Date | null {
  if (!DAY.test(day)) return null;
  const [y, m, d] = day.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d ? date : null;
}

/** The API's date window: `since` is inclusive, `until` exclusive, so the last day shown to people is inclusive via the next midnight. */
export function dateWindow(filters: Pick<HistoryFilters, 'from' | 'to'>): { ok: true; since: string | null; until: string | null } | { ok: false; error: 'badFrom' | 'badTo' | 'reversed' } {
  let since: Date | null = null;
  let until: Date | null = null;
  if (filters.from) { since = localDay(filters.from); if (!since) return { ok: false, error: 'badFrom' }; }
  if (filters.to) {
    const end = localDay(filters.to);
    if (!end) return { ok: false, error: 'badTo' };
    until = new Date(end.getFullYear(), end.getMonth(), end.getDate() + 1);
  }
  if (since && until && since >= until) return { ok: false, error: 'reversed' };
  return { ok: true, since: since ? since.toISOString() : null, until: until ? until.toISOString() : null };
}

/** The request path for one page. A correlation scope uses the dedicated route; every filter is a server filter. */
export function historyQuery(filters: HistoryFilters, scope: HistoryScope, cursor: string | null, limit = PAGE_SIZE): QueryResult {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  if (scope.kind === 'correlation') {
    return { ok: true, path: `${HISTORY_PATH}/correlations/${encodeURIComponent(scope.correlationId)}?${params}`, since: null, until: null };
  }
  const window = dateWindow(filters);
  if (!window.ok) return window;
  const q = filters.q.trim();
  const actor = filters.actor.trim();
  if (q.length > 200 || actor.length > 160) return { ok: false, error: 'tooLong' };
  if (filters.category !== 'all') params.set('category', filters.category);
  if (q) params.set('q', q);
  if (actor) params.set('actor', actor);
  if (window.since) params.set('since', window.since);
  if (window.until) params.set('until', window.until);
  return { ok: true, path: `${HISTORY_PATH}?${params}`, since: window.since, until: window.until };
}

export function filtersActive(filters: HistoryFilters): boolean {
  return filters.q.trim() !== '' || filters.actor.trim() !== '' || filters.category !== 'all' || filters.from !== '' || filters.to !== '';
}

export function countActive(filters: HistoryFilters, scope: HistoryScope): number {
  return [filters.q.trim(), filters.actor.trim(), filters.category !== 'all', filters.from, filters.to, scope.kind === 'room' || scope.kind === 'agent'].filter(Boolean).length;
}

// --------------------------------------------------------------------------- scopes

function mentions(value: unknown, ids: ReadonlySet<string>, depth = 0): boolean {
  if (depth > 7 || value === null || value === undefined) return false;
  if (typeof value === 'string') return ids.has(value);
  if (Array.isArray(value)) return value.some((entry) => mentions(entry, ids, depth + 1));
  if (typeof value === 'object') return Object.values(value as Record<string, unknown>).some((entry) => mentions(entry, ids, depth + 1));
  return false;
}

/** The ids a scope looks for. */
export function scopeIds(scope: HistoryScope): ReadonlySet<string> | null {
  if (scope.kind === 'room') return new Set([scope.roomId, ...scope.deskIds]);
  if (scope.kind === 'agent') return new Set([scope.agentId]);
  return null;
}

/** True when the record names the scope's room, desk or agent as actor, target or anywhere in its recorded values. */
export function relatesTo(record: OfficeHistoryEvidence, scope: HistoryScope): boolean {
  const ids = scopeIds(scope);
  if (!ids) return true;
  return ids.has(record.actor) || ids.has(record.target) || mentions(record.metadata, ids);
}

// --------------------------------------------------------------------------- paging

export interface HistoryState {
  items: readonly OfficeHistoryEvidence[];
  /** The cursor of the next underlying page; null when the end of the recorded history was reached. */
  cursor: string | null;
  /** Underlying records the server has paged through (pages read, not rows). */
  pagesRead: number;
  /** True when a load stopped on the page limit with records left to read. */
  paused: boolean;
}
export const EMPTY_STATE: HistoryState = { items: [], cursor: null, pagesRead: 0, paused: false };

export function mergeItems(existing: readonly OfficeHistoryEvidence[], incoming: readonly OfficeHistoryEvidence[]): OfficeHistoryEvidence[] {
  const seen = new Set(existing.map((item) => item.id));
  const merged = [...existing];
  for (const item of incoming) if (!seen.has(item.id)) { seen.add(item.id); merged.push(item); }
  return merged;
}

export type PageFetcher = (cursor: string | null) => Promise<OfficeHistoryPage>;

/**
 * Reads pages until `want` more matching records were added, the history ends, or `maxPages` pages were
 * read. The server pages underlying records before it applies the category, so a page can be empty
 * while `next_cursor` is set; this loop keeps following the cursor instead of showing a false end.
 */
export async function loadMore(
  fetchPage: PageFetcher, state: HistoryState, scope: HistoryScope,
  options: { want?: number; maxPages?: number; first?: boolean; isCurrent?: () => boolean } = {},
): Promise<HistoryState> {
  const want = options.want ?? WANT_PER_LOAD;
  const maxPages = options.maxPages ?? MAX_PAGES_PER_LOAD;
  let items = [...state.items];
  let cursor = options.first ? null : state.cursor;
  let pagesRead = state.pagesRead;
  let added = 0;
  let pages = 0;
  for (;;) {
    const page = await fetchPage(cursor);
    if (options.isCurrent && !options.isCurrent()) return state;
    pages += 1;
    pagesRead += 1;
    const before = items.length;
    items = mergeItems(items, page.items.filter((item) => relatesTo(item, scope)));
    added += items.length - before;
    cursor = page.next_cursor;
    if (cursor === null) return { items, cursor: null, pagesRead, paused: false };
    if (added >= want) return { items, cursor, pagesRead, paused: false };
    if (pages >= maxPages) return { items, cursor, pagesRead, paused: true };
  }
}

// --------------------------------------------------------------------------- source references

export type SourceKind = 'task' | 'run' | 'approval' | 'meeting' | 'room' | 'desk' | 'floor' | 'building' | 'agent';
export interface SourceRef { kind: SourceKind; id: string; /** True when an existing read route can show a summary of it. */ readable: boolean }

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/;
const READABLE: ReadonlySet<SourceKind> = new Set(['task', 'run', 'approval', 'meeting']);
const KEYS: readonly (readonly [string, SourceKind])[] = [
  ['task_id', 'task'], ['run_id', 'run'], ['agent_run_id', 'run'], ['approval_id', 'approval'], ['meeting_id', 'meeting'], ['agent_id', 'agent'],
];
const RESOURCE: Readonly<Record<string, SourceKind>> = { rooms: 'room', desks: 'desk', floors: 'floor', buildings: 'building' };

/** The records a history row points at: from its action, its target and the ids it recorded. */
export function sourceRefs(record: OfficeHistoryEvidence): SourceRef[] {
  const found = new Map<string, SourceRef>();
  const add = (kind: SourceKind, id: unknown) => {
    if (typeof id !== 'string' || !ID.test(id)) return;
    const key = `${kind}:${id}`;
    if (!found.has(key)) found.set(key, { kind, id, readable: READABLE.has(kind) });
  };
  const action = record.action;
  if (action.startsWith('task.')) add('task', record.target);
  else if (action.startsWith('approval.')) add('approval', record.target);
  else if (action.startsWith('agent_meeting.')) add('meeting', record.target);
  else if (action.startsWith('agent_run.')) add('run', record.target);
  else if (action.startsWith('office.')) {
    const kind = RESOURCE[String(record.metadata.resource_kind ?? '')];
    if (kind) add(kind, record.target);
  }
  for (const [key, kind] of KEYS) add(kind, record.metadata[key]);
  return [...found.values()];
}

/** Whether the record can be read as an office layout change with before and after values. */
export function hasBeforeAfter(record: OfficeHistoryEvidence): boolean {
  return record.action.startsWith('office.') && ('before' in record.metadata || 'after' in record.metadata);
}

// --------------------------------------------------------------------------- export

/** The audit export filters equal to the visible timeline, or null when the export would not match it. */
export function exportFilters(filters: HistoryFilters, scope: HistoryScope): { actor?: string; action?: string; q?: string; since?: string; until?: string; correlationId?: string } | null {
  if (scope.kind === 'room' || scope.kind === 'agent') return null;
  const out: { actor?: string; action?: string; q?: string; since?: string; until?: string; correlationId?: string } = {};
  if (scope.kind === 'correlation') return { correlationId: scope.correlationId };
  if (filters.category === 'other') return null;
  if (filters.category !== 'all') {
    const prefix = CATEGORY_ACTION_PREFIX[filters.category];
    if (!prefix) return null;
    out.action = `${prefix}*`;
  }
  const window = dateWindow(filters);
  if (!window.ok) return null;
  if (window.since) out.since = window.since;
  if (window.until) out.until = window.until;
  if (filters.q.trim()) out.q = filters.q.trim();
  if (filters.actor.trim()) out.actor = filters.actor.trim();
  return out;
}

// --------------------------------------------------------------------------- display helpers

const DAY_FORMAT = (locale: string, stamp: Date) => new Intl.DateTimeFormat(locale, { dateStyle: 'full' }).format(stamp);

/** Groups records (already newest first) by local calendar day. */
export function groupByDay(items: readonly OfficeHistoryEvidence[], locale: string): { key: string; label: string; items: OfficeHistoryEvidence[] }[] {
  const groups: { key: string; label: string; items: OfficeHistoryEvidence[] }[] = [];
  for (const item of items) {
    const stamp = new Date(item.timestamp);
    const key = Number.isNaN(stamp.getTime()) ? 'unknown' : `${stamp.getFullYear()}-${stamp.getMonth() + 1}-${stamp.getDate()}`;
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.items.push(item);
    else groups.push({ key, label: key === 'unknown' ? '' : DAY_FORMAT(locale, stamp), items: [item] });
  }
  return groups;
}

/** Minimal shape check so a malformed answer fails loudly instead of rendering invented rows. */
export function parseHistoryPage(raw: OfficeHistoryPage, workspaceId: string): OfficeHistoryPage {
  if (!raw || !Array.isArray(raw.items) || typeof raw.workspace_id !== 'string') throw new Error('The history answer is not in the expected shape');
  if (raw.workspace_id !== workspaceId) throw new Error('The history answer belongs to another workspace');
  for (const item of raw.items) {
    if (!item || typeof item.id !== 'string' || typeof item.action !== 'string' || typeof item.timestamp !== 'string'
      || typeof item.correlation_id !== 'string' || typeof item.target !== 'string' || typeof item.actor !== 'string') {
      throw new Error('The history answer contains an invalid record');
    }
    if (item.metadata === null || typeof item.metadata !== 'object') (item as { metadata: Record<string, unknown> }).metadata = {};
  }
  return raw;
}
