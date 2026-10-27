/** Green Concrete client (contract: packages/contracts/src/officeGreenConcrete.ts, spec: docs/ANUM_MINE_GREEN_CONCRETE.md).
 * A preview stores nothing. Only `confirm` writes, and it always sends `input_mode: "click"` with the hash the person was shown. */
import type { GreenConcreteConfirm, GreenConcretePreview, GreenConcreteResult } from '@anum/contracts';

export type GreenRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class GreenConcreteApiError extends Error { constructor(message: string) { super(message); this.name = 'GreenConcreteApiError'; } }

export const BASE = '/api/v1/office/green-concrete';
const HASH = /^[0-9a-f]{64}$/;
type Scope = () => { tenantId: string; workspaceId: string };

export function createGreenConcreteClient(request: GreenRequester, scope: Scope) {
  return {
    async preview(): Promise<GreenConcretePreview> {
      const started = scope();
      const body = await request<GreenConcretePreview>(`${BASE}/preview`, { method: 'GET' });
      const now = scope();
      if (!body || body.version !== 1 || !HASH.test(body.payload_hash) || !Array.isArray(body.people)) throw new GreenConcreteApiError('The plan is not version 1');
      if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId || body.workspace_id !== started.workspaceId) {
        throw new GreenConcreteApiError('The answer belongs to another workspace');
      }
      return body;
    },
    /** Confirms exactly what `preview` returned. Only call it from a click handler. */
    async confirm(preview: GreenConcretePreview): Promise<GreenConcreteResult> {
      const body: GreenConcreteConfirm = { payload_hash: preview.payload_hash, input_mode: 'click', timezone: preview.timezone };
      const result = await request<GreenConcreteResult>(`${BASE}/confirm`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!result || result.version !== 1 || typeof result.outcome !== 'string') throw new GreenConcreteApiError('The result is not version 1');
      return result;
    },
  };
}

/** The confirm button needs a person who may confirm and something to create. */
export function canConfirm(preview: GreenConcretePreview | null, busy: boolean): boolean {
  if (!preview || busy || !preview.can_confirm) return false;
  const c = preview.counts.create;
  return c.buildings + c.floors + c.rooms + c.desks + c.agents + preview.counts.seat > 0;
}
