/** Staff my campus client (contract: packages/contracts/src/officeStaffDesks.ts, spec: docs/ANUM_MINE_STAFF_DESKS.md).
 * A preview stores nothing. Only `confirm` and `turnOn` write, and both always send `input_mode: "click"` with the
 * hash the person was shown for exactly that building or that selection. */
import type {
  StaffBatch, StaffDesk, StaffDesksConfirm, StaffDesksPreview, StaffDesksPreviewRequest, StaffDesksResult, StaffDeskResult,
  StaffKeepEmptyResult, StaffPausedPage, StaffRoom, StaffTurnOnPreview, StaffTurnOnResult,
} from '@anum/contracts';

export type StaffRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class StaffDesksApiError extends Error { constructor(message: string) { super(message); this.name = 'StaffDesksApiError'; } }

export const BASE = '/api/v1/office/staff-desks';
const HASH = /^[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const JSON_HEADERS = { 'content-type': 'application/json' };

type Scope = () => { tenantId: string; workspaceId: string };

export function createStaffDesksClient(request: StaffRequester, scope: Scope) {
  const guard = <T extends { workspace_id?: string }>(started: ReturnType<Scope>, body: T): T => {
    const now = scope();
    if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId || (body.workspace_id && body.workspace_id !== started.workspaceId)) {
      throw new StaffDesksApiError('The answer belongs to another workspace');
    }
    return body;
  };
  const ids = (values: string[]) => { if (!values.every((v) => ID.test(v))) throw new StaffDesksApiError('Invalid id'); return values; };
  const post = <T>(path: string, body: unknown) => request<T>(`${BASE}${path}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) });
  return {
    async preview(req: StaffDesksPreviewRequest): Promise<StaffDesksPreview> {
      const started = scope();
      const body: StaffDesksPreviewRequest = {
        exclude_rooms: ids(req.exclude_rooms), lang: req.lang,
        ...(req.buildings ? { buildings: ids(req.buildings) } : {}),
        ...(req.max_agents != null ? { max_agents: req.max_agents } : {}),
      };
      const out = await post<StaffDesksPreview>('/preview', body);
      if (!out || out.version !== 1 || !Array.isArray(out.buildings) || !Array.isArray(out.batches)) throw new StaffDesksApiError('The plan is not version 1');
      for (const b of out.batches) if (!HASH.test(b.payload_hash)) throw new StaffDesksApiError('The plan has an invalid hash');
      return guard(started, out);
    },
    /** Confirms exactly one building's batch from `preview`. Only call it from a click handler. */
    async confirm(batch: StaffBatch, excludeRooms: string[], lang: 'en' | 'ar'): Promise<StaffDesksResult> {
      const body: StaffDesksConfirm = {
        payload_hash: batch.payload_hash, input_mode: 'click', building_id: batch.building_id, exclude_rooms: ids(excludeRooms),
        quota: batch.quota, lang,
      };
      const out = await post<StaffDesksResult>('/confirm', body);
      if (!out || out.version !== 1 || !Array.isArray(out.results)) throw new StaffDesksApiError('The result is not version 1');
      return out;
    },
    async keepEmpty(deskIds: string[], keep: boolean): Promise<StaffKeepEmptyResult> {
      return request<StaffKeepEmptyResult>(`${BASE}/keep-empty`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ desk_ids: ids(deskIds), keep_empty: keep }) });
    },
    async paused(offset: number, limit: number): Promise<StaffPausedPage> {
      return request<StaffPausedPage>(`${BASE}/paused?${new URLSearchParams({ offset: String(offset), limit: String(limit) })}`, { method: 'GET' });
    },
    async turnOnPreview(agentIds: string[]): Promise<StaffTurnOnPreview> {
      return post<StaffTurnOnPreview>('/turn-on/preview', { agent_ids: ids(agentIds) });
    },
    /** Only call it from a click handler, with the preview of exactly this selection. */
    async turnOn(agentIds: string[], preview: StaffTurnOnPreview): Promise<StaffTurnOnResult> {
      if (!HASH.test(preview.payload_hash)) throw new StaffDesksApiError('Invalid hash');
      return post<StaffTurnOnResult>('/turn-on', { agent_ids: ids(agentIds), payload_hash: preview.payload_hash, input_mode: 'click' });
    },
  };
}

/** The confirm button needs a person who may confirm and at least one desk to staff. */
export function canConfirm(preview: StaffDesksPreview | null, busy: boolean, loading: boolean): boolean {
  return Boolean(preview && !busy && !loading && preview.can_confirm && preview.batches.length > 0);
}

export interface FlatDesk {
  buildingId: string; buildingName: string; room: StaffRoom; desk: StaffDesk;
}

/** Every desk of the selected buildings in server order, for the searchable, paged list. */
export function flatten(preview: StaffDesksPreview): FlatDesk[] {
  const out: FlatDesk[] = [];
  for (const b of preview.buildings) for (const room of b.rooms) for (const desk of room.desks) out.push({ buildingId: b.id, buildingName: b.name, room, desk });
  return out;
}

export function pageOf<T>(items: T[], page: number, size: number): { items: T[]; pages: number; page: number } {
  const pages = Math.max(1, Math.ceil(items.length / size));
  const at = Math.min(Math.max(0, page), pages - 1);
  return { items: items.slice(at * size, at * size + size), pages, page: at };
}

export function summarize(results: StaffDeskResult[]): Record<StaffDeskResult['status'], number> {
  const out = { created: 0, seated_existing: 0, skipped: 0, failed: 0 };
  for (const r of results) out[r.status] += 1;
  return out;
}

export const DESK_STATES = ['plan', 'resume', 'occupied', 'keep_empty', 'over_limit', 'over_cap', 'no_role', 'excluded', 'agent_elsewhere'] as const;

export function money(value: number, locale: 'en' | 'ar'): string {
  return `USD ${new Intl.NumberFormat(locale === 'ar' ? 'ar-u-nu-latn' : 'en', { maximumFractionDigits: 2 }).format(value)}`;
}
