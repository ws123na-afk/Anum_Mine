/** ANUM MINE executive command centre adapter (contract: packages/contracts/src/executive.ts).
 * Grant management is owner-only and approval-gated; the overview returns only granted summaries. */
import type {
  ExecutiveGrant, ExecutiveGrantApproveInput, ExecutiveGrantCreateInput, ExecutiveGrantCreated,
  ExecutiveGrantList, ExecutiveGrantNarrowInput, ExecutiveOverview,
} from '@anum/contracts';

export type ExecutiveRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export type ExecutiveVoidRequester = (path: string, init: RequestInit) => Promise<void>;
export class ExecutiveApiError extends Error {
  constructor(message: string) { super(message); this.name = 'ExecutiveApiError'; }
}

const ROOT = '/api/v1/executive';
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const HASH = /^[0-9a-f]{64}$/;
const grantPath = (id: string): string => {
  if (!ID.test(id)) throw new ExecutiveApiError('Invalid grant identifier');
  return `${ROOT}/grants/${encodeURIComponent(id)}`;
};

export function createExecutiveClient(request: ExecutiveRequester, remove: ExecutiveVoidRequester,
  scope: () => { tenantId: string; workspaceId: string }) {
  const overview = async (): Promise<ExecutiveOverview> => {
    const started = scope();
    const data = await request<ExecutiveOverview>(`${ROOT}/overview`, { method: 'GET' });
    if (!data || data.version !== 1 || !Array.isArray(data.workspaces)) throw new ExecutiveApiError('Overview is not version 1');
    if (data.tenant_id !== started.tenantId) throw new ExecutiveApiError('Overview belongs to another organisation');
    const seen = new Set<string>();
    for (const w of data.workspaces) {
      if (seen.has(w.workspace_id)) throw new ExecutiveApiError('Overview repeated a workspace');
      seen.add(w.workspace_id);
      // A workspace that is not granted must carry no summary: refuse a server that leaks one.
      if (w.access !== 'granted' && 'summary' in (w as object)) throw new ExecutiveApiError('Overview leaked data for a workspace without a grant');
    }
    const now = scope();
    if (now.tenantId !== started.tenantId || now.workspaceId !== started.workspaceId) throw new ExecutiveApiError('Workspace changed while loading');
    return data;
  };
  const listGrants = (limit = 50) => request<ExecutiveGrantList>(`${ROOT}/grants?limit=${Math.min(100, Math.max(1, limit))}`, { method: 'GET' });
  const createGrant = (input: ExecutiveGrantCreateInput) =>
    request<ExecutiveGrantCreated>(`${ROOT}/grants`, { method: 'POST', body: JSON.stringify(input) });
  /** `shownHash` must be the hash the owner was shown (`ExecutiveGrant.payload_hash`). */
  const approveGrant = (id: string, input: ExecutiveGrantApproveInput) => {
    if (!HASH.test(input.payload_hash)) throw new ExecutiveApiError('A 64 character payload hash is required');
    return request<ExecutiveGrant>(`${grantPath(id)}/approve`, { method: 'POST', body: JSON.stringify(input) });
  };
  const rejectGrant = (id: string, reason?: string) =>
    request<ExecutiveGrant>(`${grantPath(id)}/reject`, { method: 'POST', body: JSON.stringify(reason ? { reason } : {}) });
  const narrowGrant = (id: string, input: ExecutiveGrantNarrowInput) =>
    request<ExecutiveGrant>(grantPath(id), { method: 'PATCH', body: JSON.stringify(input) });
  const revokeGrant = (id: string) => remove(grantPath(id), { method: 'DELETE' });
  return { overview, listGrants, createGrant, approveGrant, rejectGrant, narrowGrant, revokeGrant };
}
