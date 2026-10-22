/** ANUM MINE campus and workstation API adapter (contract: packages/contracts/src/officeCampus.ts,
 * officeWorkstation.ts). Transport is injected so the contract is testable under Node. It sends
 * no tenant or workspace ids in bodies; the server derives scope from the authenticated request. */
import type {
  CampusProfile, CampusProfileInput, CampusResponse, OfficeWorkstationWork,
} from '@anum/contracts';

export type CampusRequester = <T>(path: string, init: RequestInit) => Promise<T>;
export class CampusApiError extends Error {
  constructor(message: string) { super(message); this.name = 'CampusApiError'; }
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/;
const assertId = (id: string): string => {
  if (!ID.test(id)) throw new CampusApiError('Invalid identifier');
  return id;
};

export function campusPath(): string { return '/api/v1/office/campus'; }
export function workstationWorkPath(deskId: string): string {
  return `/api/v1/office/workstations/${encodeURIComponent(assertId(deskId))}/work`;
}

export function createCampusClient(request: CampusRequester, scope: () => { tenantId: string; workspaceId: string }) {
  const sameScope = <T,>(started: { tenantId: string; workspaceId: string }, value: T): T => {
    const now = scope();
    if (now.tenantId !== started.tenantId || now.workspaceId !== started.workspaceId) {
      throw new CampusApiError('Workspace changed while loading. Reload before continuing.');
    }
    return value;
  };
  const loadCampus = async (): Promise<CampusResponse> => {
    const started = scope();
    const data = await request<CampusResponse>(campusPath(), { method: 'GET' });
    if (!data || data.version !== 1 || !Array.isArray(data.buildings)) throw new CampusApiError('Campus response is not version 1');
    if (data.tenant_id !== started.tenantId) throw new CampusApiError('Campus belongs to another organisation');
    const seen = new Set<string>();
    for (const b of data.buildings) {
      if (seen.has(b.workspace_id)) throw new CampusApiError('Campus returned a repeated workspace');
      seen.add(b.workspace_id);
    }
    return sameScope(started, data);
  };
  const loadWork = async (deskId: string): Promise<OfficeWorkstationWork> => {
    const started = scope();
    const data = await request<OfficeWorkstationWork>(workstationWorkPath(deskId), { method: 'GET' });
    if (!data || data.version !== 1 || !Array.isArray(data.tasks)) throw new CampusApiError('Workstation response is not version 1');
    if (data.workspace_id !== started.workspaceId) throw new CampusApiError('Workstation belongs to another workspace');
    if (data.desk.id !== deskId) throw new CampusApiError('Workstation response is for another desk');
    return sameScope(started, data);
  };
  const saveProfile = (input: CampusProfileInput): Promise<CampusProfile> =>
    request<CampusProfile>(`${campusPath()}/profile`, { method: 'PUT', body: JSON.stringify(input) });
  return { loadCampus, loadWork, saveProfile };
}
