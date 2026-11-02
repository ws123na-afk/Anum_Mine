/** Offices and agents: client and pure helpers (contract packages/contracts/src/officeStaffing.ts, spec
 * docs/ANUM_MINE_OFFICE_STAFFING.md). Reads change nothing; only `apply` changes agents, always with
 * `input_mode: "click"` and the hash of each action the person reviewed. */
import type {
  StaffingAction, StaffingApplyResult, StaffingOffice, StaffingOfficePage, StaffingRole, StaffingSourceState,
} from '@anum/contracts';

export type StaffingRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class StaffingApiError extends Error { constructor(message: string) { super(message); this.name = 'StaffingApiError'; } }

export const BASE = '/api/v1/office/staffing';
export const PAGE_SIZE = 12;
export const MAX_ACTIONS = 20;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const HASH = /^[0-9a-f]{64}$/;
type Scope = () => { tenantId: string; workspaceId: string };

export function createStaffingClient(request: StaffingRequester, scope: Scope) {
  const sameScope = (started: ReturnType<Scope>) => {
    const now = scope();
    if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId) throw new StaffingApiError('The answer belongs to another workspace');
  };
  const office = (id: string) => {
    if (!ID.test(id)) throw new StaffingApiError('Invalid office');
    return `${BASE}/offices/${encodeURIComponent(id)}`;
  };
  return {
    async list(q: string, offset: number, limit = PAGE_SIZE): Promise<StaffingOfficePage> {
      const started = scope();
      const params = new URLSearchParams({ limit: String(limit), offset: String(Math.max(0, offset)) });
      if (q.trim()) params.set('q', q.trim().slice(0, 100));
      const body = await request<StaffingOfficePage>(`${BASE}/offices?${params}`, { method: 'GET' });
      if (!body || body.version !== 1 || !Array.isArray(body.items)) throw new StaffingApiError('The offices response is not version 1');
      if (body.workspace_id && body.workspace_id !== started.workspaceId) throw new StaffingApiError('The answer belongs to another workspace');
      sameScope(started);
      return body;
    },
    async office(id: string, lang: 'en' | 'ar'): Promise<StaffingOffice> {
      const started = scope();
      const body = await request<StaffingOffice>(`${office(id)}?lang=${lang}`, { method: 'GET' });
      if (!body || body.version !== 1 || !Array.isArray(body.roles) || body.building_id !== id) throw new StaffingApiError('The office response is not version 1');
      sameScope(started);
      return body;
    },
    async apply(id: string, lang: 'en' | 'ar', actions: Pick<StaffingAction, 'id' | 'hash'>[]): Promise<StaffingApplyResult> {
      if (actions.length < 1 || actions.length > MAX_ACTIONS) throw new StaffingApiError('Choose between 1 and 20 changes');
      if (actions.some((a) => !HASH.test(a.hash))) throw new StaffingApiError('A change has no valid hash');
      return request<StaffingApplyResult>(`${office(id)}/apply`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ input_mode: 'click', lang, actions: actions.map((a) => ({ id: a.id, hash: a.hash })) }),
      });
    },
  };
}
export type StaffingClient = ReturnType<typeof createStaffingClient>;

/** Actions in groups of at most 20 (one request each). Every action carries its own hash, so a later group
 * is not refused because an earlier one changed the office. */
export function chunks<T>(items: readonly T[], size = MAX_ACTIONS): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

export async function applyAll(client: StaffingClient, id: string, lang: 'en' | 'ar', actions: readonly StaffingAction[],
  onProgress?: (done: number, total: number) => void): Promise<StaffingApplyResult['results']> {
  const results: StaffingApplyResult['results'] = [];
  for (const group of chunks(actions)) {
    const answer = await client.apply(id, lang, group);
    results.push(...answer.results);
    onProgress?.(results.length, actions.length);
  }
  return results;
}

export interface ResultTotals { created: number; updated: number; other: number; stale: number }
export function totals(results: StaffingApplyResult['results']): ResultTotals {
  const created = results.filter((r) => r.outcome === 'created' || r.outcome === 'resumed').length;
  const updated = results.filter((r) => r.outcome === 'updated').length;
  return { created, updated, other: results.length - created - updated, stale: results.filter((r) => r.outcome === 'stale').length };
}

export const localText = (text: { en: string; ar: string | null }, locale: string): string => (locale === 'ar' && text.ar) || text.en;

export const coverageRatio = (staffed: number, total: number): number => (total <= 0 ? 0 : Math.round((staffed / total) * 100));

/** The shell view that connects a source, or null when only the server operator can (a setting). */
export function connectView(source: { state: StaffingSourceState; view: string | null }): string | null {
  return source.state === 'not_connected' ? source.view : null;
}

export function roleFor(office: StaffingOffice, key: string): StaffingRole | undefined {
  return office.roles.find((r) => r.key === key);
}

/** Roles grouped by the office's departments, in the blueprint's order. */
export function rolesByUnit(office: StaffingOffice): { id: string; name: { en: string; ar: string }; roles: StaffingRole[] }[] {
  return office.units.map((u) => ({ ...u, roles: office.roles.filter((r) => r.unit === u.id) })).filter((u) => u.roles.length > 0);
}

/** Clamp a page offset after the total shrank (a search, a deleted building). */
export function clampOffset(offset: number, total: number, size = PAGE_SIZE): number {
  if (total <= 0) return 0;
  const last = Math.floor((total - 1) / size) * size;
  return Math.min(Math.max(0, offset), last);
}

/** The command centre opens on its "Offices and agents" tab once after a link from the headquarters. */
export const EXEC_TAB_KEY = 'anum.executive.tab';
export function rememberOfficesTab(): void { try { sessionStorage.setItem(EXEC_TAB_KEY, 'offices'); } catch { /* storage blocked: the default tab opens */ } }
export function takeExecutiveTab(): 'offices' | 'workspaces' {
  try { return sessionStorage.getItem(EXEC_TAB_KEY) === 'offices' ? 'offices' : 'workspaces'; } catch { return 'workspaces'; }
}
/** Called after the page mounted: a render React throws away must not consume the choice. */
export function forgetExecutiveTab(): void { try { sessionStorage.removeItem(EXEC_TAB_KEY); } catch { /* storage blocked */ } }
