/** Governed agent provisioning client (contract: packages/contracts/src/officeProvisioning.ts, spec:
 * docs/ANUM_MINE_AGENT_PROVISIONING.md). Nothing is created by a read; only `confirm` creates, and it
 * always sends `input_mode: "click"` with the hash the person was shown. */
import type {
  ProvisioningConfirm, ProvisioningGap, ProvisioningGaps, ProvisioningProposal, ProvisioningResult,
  ProvisioningTemplate, ProvisioningTemplateId,
} from '@anum/contracts';

export type ProvisioningRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class ProvisioningApiError extends Error { constructor(message: string) { super(message); this.name = 'ProvisioningApiError'; } }

export const BASE = '/api/v1/office/provisioning';
const TEMPLATE = /^[a-z][a-z0-9_]{0,39}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const HASH = /^[0-9a-f]{64}$/;

type Scope = () => { tenantId: string; workspaceId: string };

export function createProvisioningClient(request: ProvisioningRequester, scope: Scope) {
  const guard = <T extends { workspace_id?: string }>(started: ReturnType<Scope>, body: T): T => {
    const now = scope();
    if (now.workspaceId !== started.workspaceId || now.tenantId !== started.tenantId || (body.workspace_id && body.workspace_id !== started.workspaceId)) {
      throw new ProvisioningApiError('The answer belongs to another workspace');
    }
    return body;
  };
  const path = (id: string, query: Record<string, string | null | undefined>) => {
    if (!TEMPLATE.test(id)) throw new ProvisioningApiError('Invalid role');
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) if (v) params.set(k, v);
    const q = params.toString();
    return `${BASE}/proposals/${id}${q ? `?${q}` : ''}`;
  };
  return {
    async gaps(): Promise<ProvisioningGaps> {
      const started = scope();
      const body = await request<ProvisioningGaps>(`${BASE}/gaps`, { method: 'GET' });
      if (!body || body.version !== 1 || !Array.isArray(body.gaps) || !Array.isArray(body.coverage)) throw new ProvisioningApiError('The gaps response is not version 1');
      return guard(started, body);
    },
    async templates(): Promise<ProvisioningTemplate[]> {
      return (await request<{ items: ProvisioningTemplate[] }>(`${BASE}/templates`, { method: 'GET' })).items;
    },
    async proposal(id: ProvisioningTemplateId, lang: 'en' | 'ar', roomId?: string | null): Promise<ProvisioningProposal> {
      if (roomId && !ID.test(roomId)) throw new ProvisioningApiError('Invalid room');
      const body = await request<ProvisioningProposal>(path(id, { lang, room_id: roomId }), { method: 'GET' });
      if (!body || body.version !== 1 || !HASH.test(body.payload_hash)) throw new ProvisioningApiError('The proposal is not version 1');
      return body;
    },
    /** Confirms exactly what `proposal` returned. Only call it from a click handler. */
    async confirm(proposal: ProvisioningProposal, roomId?: string | null): Promise<ProvisioningResult> {
      const body: ProvisioningConfirm = { payload_hash: proposal.payload_hash, input_mode: 'click', lang: proposal.content.lang };
      if (roomId) body.room_id = roomId;
      return request<ProvisioningResult>(`${BASE}/proposals/${proposal.content.template_id}/confirm`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
    },
  };
}

/** Gaps first (critical, then warning), then the other roles. */
export function orderedGaps(gaps: ProvisioningGap[]): ProvisioningGap[] {
  return [...gaps].sort((a, b) => (a.severity === b.severity ? a.template_id.localeCompare(b.template_id) : a.severity === 'critical' ? -1 : 1));
}

export function missingCount(proposal: ProvisioningProposal): number {
  return proposal.content.missing.length;
}

/** The confirm button is enabled only for a person who may confirm and only when the room choice is valid. */
export function canConfirm(proposal: ProvisioningProposal | null, busy: boolean): boolean {
  return !!proposal && proposal.can_confirm && !busy;
}
