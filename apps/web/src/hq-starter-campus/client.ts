/** Starter campus client (contract: packages/contracts/src/officeStarterCampus.ts, spec: docs/ANUM_MINE_STARTER_CAMPUS.md).
 * A preview stores nothing. Only `confirm` writes, and it always sends `input_mode: "click"` with the hash the
 * person was shown and exactly the buildings that hash was made for. */
import type { StarterBuilding, StarterCampusConfirm, StarterCampusPreview, StarterCampusResult, StarterCounts } from '@anum/contracts';

export type StarterRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class StarterCampusApiError extends Error { constructor(message: string) { super(message); this.name = 'StarterCampusApiError'; } }

export const BASE = '/api/v1/office/starter-campus';
const HASH = /^[0-9a-f]{64}$/;
const KEY = /^[a-z0-9-]{1,40}$/;

type Scope = () => { tenantId: string; workspaceId: string };

export function createStarterCampusClient(request: StarterRequester, scope: Scope) {
  return {
    /** `keys` null means every building. The answer is refused when it belongs to another workspace. */
    async preview(keys: string[] | null): Promise<StarterCampusPreview> {
      if (keys && (keys.length === 0 || !keys.every((k) => KEY.test(k)))) throw new StarterCampusApiError('Invalid building selection');
      const started = scope();
      const query = keys ? `?${new URLSearchParams({ buildings: keys.join(',') }).toString()}` : '';
      const body = await request<StarterCampusPreview>(`${BASE}/preview${query}`, { method: 'GET' });
      const now = scope();
      if (!body || body.version !== 1 || !HASH.test(body.payload_hash) || !Array.isArray(body.buildings)) throw new StarterCampusApiError('The plan is not version 1');
      if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId || body.workspace_id !== started.workspaceId) {
        throw new StarterCampusApiError('The answer belongs to another workspace');
      }
      return body;
    },
    /** Confirms exactly what `preview` returned. Only call it from a click handler. */
    async confirm(preview: StarterCampusPreview): Promise<StarterCampusResult> {
      const body: StarterCampusConfirm = { payload_hash: preview.payload_hash, input_mode: 'click', buildings: preview.selected, timezone: preview.timezone };
      const result = await request<StarterCampusResult>(`${BASE}/confirm`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!result || result.version !== 1 || !Array.isArray(result.buildings)) throw new StarterCampusApiError('The result is not version 1');
      return result;
    },
  };
}

/** Buildings the owner can still tick: those with something to create. */
export function selectable(buildings: StarterBuilding[]): string[] {
  return buildings.filter((b) => b.status !== 'complete').map((b) => b.key);
}

export function hasWork(counts: StarterCounts): boolean {
  return counts.buildings + counts.floors + counts.rooms + counts.desks > 0;
}

/** The confirm button needs a person who may confirm, a plan for exactly the ticked buildings, and something to create. */
export function canConfirm(preview: StarterCampusPreview | null, ticked: ReadonlySet<string>, busy: boolean, loading: boolean): boolean {
  if (!preview || busy || loading || !preview.can_confirm || ticked.size === 0) return false;
  const same = preview.selected.length === ticked.size && preview.selected.every((k) => ticked.has(k));
  return same && hasWork(preview.totals.create);
}

/** Rooms and desks to show for one building, in the order the server planned them. */
export function deskLabels(room: { desks: { label: string }[] }): string {
  return room.desks.map((d) => d.label).join(', ');
}
