/**
 * Reception visitor register: the pure rules behind the Reception panel and the 3D reception room
 * (docs/ANUM_MINE_HQ_RECEPTION.md, owner decision O4). No effects, no DOM: the unit runner covers it.
 *
 * Data truth: every visitor shown is a stored visit of `GET /api/v1/office/visitors`; the 3D room shows only the number
 * of visitors the server counts as checked in now (`/summary`), never a name and never an invented figure. With no
 * answer the room shows no visitor line at all.
 */
import type { Visit, VisitCreateInput, VisitPage, VisitStatus, VisitUpdateInput } from '@anum/contracts';

export const VISIT_STATUSES: readonly VisitStatus[] = ['expected', 'checked_in', 'checked_out', 'cancelled'];
export const PAGE_SIZE = 25;
export const LIMITS = { name: 120, company: 120, purpose: 200 } as const;
export type Scope = 'today' | 'all';

/** Local midnight to the next local midnight of `now`, as ISO strings (the server compares instants). */
export function dayWindow(now: Date): { since: string; until: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return { since: start.toISOString(), until: end.toISOString() };
}

export interface ListQuery { q: string; status: VisitStatus | null; scope: Scope; cursor: string | null; now: Date; limit?: number }

/** The list path for one page. */
export function visitQuery(query: ListQuery): string {
  const p = new URLSearchParams();
  const q = query.q.trim();
  if (q) p.set('q', q.slice(0, 100));
  if (query.status) p.set('status', query.status);
  if (query.scope === 'today') { const w = dayWindow(query.now); p.set('since', w.since); p.set('until', w.until); }
  p.set('limit', String(query.limit ?? PAGE_SIZE));
  if (query.cursor) p.set('cursor', query.cursor);
  return `/api/v1/office/visitors?${p.toString()}`;
}

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0);

function parseVisit(raw: unknown): Visit | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id), name = str(r.visitor_name), purpose = str(r.purpose), expected = str(r.expected_at);
  const status = VISIT_STATUSES.find((s) => s === r.status);
  if (!id || !name || !purpose || !expected || !status || typeof r.revision !== 'number') return null;
  const host = r.host && typeof r.host === 'object' ? (r.host as Record<string, unknown>) : null;
  const hostKind = host && (host.kind === 'user' || host.kind === 'agent') ? host.kind : null;
  return {
    id, visitor_name: name, company: str(r.company), purpose, status, expected_at: expected,
    host: hostKind && str(host?.id) ? { kind: hostKind, id: str(host?.id) as string } : null,
    checked_in_at: str(r.checked_in_at), checked_out_at: str(r.checked_out_at), cancelled_at: str(r.cancelled_at),
    building_id: str(r.building_id), room_id: str(r.room_id), revision: r.revision,
    created_by: str(r.created_by) ?? '', updated_by: str(r.updated_by) ?? '', created_at: str(r.created_at) ?? expected, updated_at: str(r.updated_at) ?? expected,
  };
}

/** A page of the list; malformed rows are dropped, never patched with guesses. */
export function parseVisitPage(raw: unknown): VisitPage {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const items = (Array.isArray(r.items) ? r.items : []).map(parseVisit).filter((v): v is Visit => v !== null);
  const c = (r.counts && typeof r.counts === 'object' ? r.counts : {}) as Record<string, unknown>;
  return {
    version: 1, items, next_cursor: str(r.next_cursor), total: num(r.total),
    counts: { expected: num(c.expected), checked_in: num(c.checked_in), checked_out: num(c.checked_out), cancelled: num(c.cancelled) },
    can_manage: r.can_manage === true, as_of: str(r.as_of) ?? '',
  };
}

export { parseSummary, receptionCounts, type ReceptionRoom } from './hqReceptionCount.ts';

/** What a visit can do next. Writes are for owners only (`canManage`). */
export function actionsFor(v: Pick<Visit, 'status'>, canManage: boolean): { checkIn: boolean; checkOut: boolean; cancel: boolean; edit: boolean; remove: boolean } {
  if (!canManage) return { checkIn: false, checkOut: false, cancel: false, edit: false, remove: false };
  return { checkIn: v.status === 'expected', checkOut: v.status === 'checked_in', cancel: v.status === 'expected', edit: v.status === 'expected' || v.status === 'checked_in', remove: true };
}

export interface Draft {
  name: string; company: string; purpose: string;
  /** `datetime-local` value (local time, no zone). */
  expected: string;
  /** `user:<id>`, `agent:<id>` or empty. */
  host: string;
  /** Room id or empty. */
  room: string;
  arrived: boolean;
}

const pad = (n: number) => String(n).padStart(2, '0');
/** A `datetime-local` value for an instant, in the device's time zone. */
export function toLocalInput(iso: string | Date): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso;
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function emptyDraft(now: Date, room = ''): Draft {
  return { name: '', company: '', purpose: '', expected: toLocalInput(now), host: '', room, arrived: false };
}

export function draftFrom(v: Visit): Draft {
  return { name: v.visitor_name, company: v.company ?? '', purpose: v.purpose, expected: toLocalInput(v.expected_at), host: v.host ? `${v.host.kind}:${v.host.id}` : '', room: v.room_id ?? '', arrived: false };
}

export type DraftField = 'name' | 'company' | 'purpose' | 'expected';
export type DraftProblem = 'required' | 'tooLong' | 'invalidTime' | 'outOfRange';

/** Field problems before anything is sent; the server checks the same rules again. */
export function draftProblems(d: Draft, now: Date): Partial<Record<DraftField, DraftProblem>> {
  const out: Partial<Record<DraftField, DraftProblem>> = {};
  const name = d.name.trim(), purpose = d.purpose.trim();
  if (!name) out.name = 'required'; else if (name.length > LIMITS.name) out.name = 'tooLong';
  if (d.company.trim().length > LIMITS.company) out.company = 'tooLong';
  if (!purpose) out.purpose = 'required'; else if (purpose.length > LIMITS.purpose) out.purpose = 'tooLong';
  const when = new Date(d.expected);
  if (!d.expected || Number.isNaN(when.getTime())) out.expected = 'invalidTime';
  else if (Math.abs(when.getTime() - now.getTime()) > 400 * 86_400_000) out.expected = 'outOfRange';
  return out;
}

function hostParts(host: string): { host_kind: 'user' | 'agent' | null; host_id: string | null } {
  const i = host.indexOf(':');
  const kind = host.slice(0, i);
  return (kind === 'user' || kind === 'agent') && i > 0 && host.length > i + 1 ? { host_kind: kind, host_id: host.slice(i + 1) } : { host_kind: null, host_id: null };
}

const tidy = (s: string) => s.replace(/\s+/g, ' ').trim();

export function createBody(d: Draft, id: string): VisitCreateInput {
  return {
    id, visitor_name: tidy(d.name), company: tidy(d.company) || null, purpose: tidy(d.purpose),
    expected_at: new Date(d.expected).toISOString(), ...hostParts(d.host), room_id: d.room || null, arrived: d.arrived, input_mode: 'click',
  };
}

export function updateBody(d: Draft, revision: number): VisitUpdateInput {
  return {
    visitor_name: tidy(d.name), company: tidy(d.company) || null, purpose: tidy(d.purpose), expected_at: new Date(d.expected).toISOString(),
    ...hostParts(d.host), room_id: d.room || null, expected_revision: revision, input_mode: 'click',
  };
}

/** A client id for one create attempt, so a retry of the same form never adds a second visit. */
export function newVisitId(random: () => string = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`)): string {
  return `visit-${random().replace(/[^A-Za-z0-9]/g, '').slice(0, 32)}`;
}

/** Merges a page into the loaded list: newer copies of a visit replace older ones, order is kept. */
export function mergePage(current: readonly Visit[], next: readonly Visit[]): Visit[] {
  const seen = new Map(current.map((v, i) => [v.id, i] as const));
  const out = [...current];
  for (const v of next) { const i = seen.get(v.id); if (i === undefined) { seen.set(v.id, out.length); out.push(v); } else out[i] = v; }
  return out;
}

/** Replaces one visit in the loaded list after a write (or removes it when `next` is null). */
export function replaceVisit(list: readonly Visit[], id: string, next: Visit | null): Visit[] {
  return next ? list.map((v) => (v.id === id ? next : v)) : list.filter((v) => v.id !== id);
}
