/** Green Concrete client (contract: packages/contracts/src/officeGreenConcrete.ts, spec: docs/ANUM_MINE_GREEN_CONCRETE.md).
 * A preview stores nothing. Only `confirm` writes, and it always sends `input_mode: "click"` with the hash the person was shown. */
import type { AvatarStyle, GreenConcreteConfirm, GreenConcretePreview, GreenConcreteResult, GreenLooks, OFFICE_GREEN_CONCRETE_VERSION } from '@anum/contracts';

export type GreenRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class GreenConcreteApiError extends Error { constructor(message: string) { super(message); this.name = 'GreenConcreteApiError'; } }

export const BASE = '/api/v1/office/green-concrete';
const HASH = /^[0-9a-f]{64}$/;
/** The contract version this client reads (a type check keeps it equal to the contract's). */
const VERSION: typeof OFFICE_GREEN_CONCRETE_VERSION = 2;
type Scope = () => { tenantId: string; workspaceId: string };

export function createGreenConcreteClient(request: GreenRequester, scope: Scope) {
  return {
    async preview(looks?: GreenLooks): Promise<GreenConcretePreview> {
      const started = scope();
      const body = await request<GreenConcretePreview>(`${BASE}/preview${previewQuery(looks)}`, { method: 'GET' });
      const now = scope();
      if (!body || body.version !== VERSION || !HASH.test(body.payload_hash) || !Array.isArray(body.people)) throw new GreenConcreteApiError(`The plan is not version ${VERSION}`);
      if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId || body.workspace_id !== started.workspaceId) {
        throw new GreenConcreteApiError('The answer belongs to another workspace');
      }
      return body;
    },
    /** Confirms exactly what `preview` returned. Only call it from a click handler. */
    async confirm(preview: GreenConcretePreview, looks?: GreenLooks): Promise<GreenConcreteResult> {
      const body: GreenConcreteConfirm = { payload_hash: preview.payload_hash, input_mode: 'click', timezone: preview.timezone, ...(looks ? { default_look: looks.default_look, appearance: looks.appearance } : {}) };
      const result = await request<GreenConcreteResult>(`${BASE}/confirm`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!result || result.version !== VERSION || typeof result.outcome !== 'string') throw new GreenConcreteApiError(`The result is not version ${VERSION}`);
      return result;
    },
  };
}

/** The query that makes the preview for the owner's looks: the same looks go with the confirm, so the hash matches. */
export function previewQuery(looks?: GreenLooks): string {
  if (!looks) return '';
  const q = new URLSearchParams({ default_look: looks.default_look });
  for (const [key, style] of Object.entries(looks.appearance)) q.append('look', `${key}:${style}`);
  return `?${q.toString()}`;
}

/** The looks the owner can pick for a person, in the order of the dropdown (neutral first: it is the default). */
export const LOOK_CHOICES: readonly AvatarStyle[] = ['neutral', 'masculine', 'feminine'];

/** The confirm button needs a person who may confirm and something to create. */
export function canConfirm(preview: GreenConcretePreview | null, busy: boolean): boolean {
  if (!preview || busy || !preview.can_confirm) return false;
  const c = preview.counts.create;
  return c.buildings + c.floors + c.rooms + c.desks + c.agents + preview.counts.seat > 0;
}
