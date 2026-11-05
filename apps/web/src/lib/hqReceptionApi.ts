/**
 * Calls of the reception visitor register (`/api/v1/office/visitors`, docs/ANUM_MINE_HQ_RECEPTION.md). The server decides
 * who may read (task:read) and write (workspace owners, on a click); every write sends the revision the person saw.
 */
import type { Visit, VisitCreateInput, VisitPage, VisitSummary, VisitUpdateInput } from '@anum/contracts';
import { parseSummary, parseVisitPage } from './hqReception.ts';

export type Requester = <T>(path: string, init: RequestInit) => Promise<T>;
export type VoidRequester = (path: string, init: RequestInit) => Promise<void>;
const BASE = '/api/v1/office/visitors';
const enc = encodeURIComponent;

export function createReceptionClient(request: Requester, requestVoid: VoidRequester) {
  const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
  return {
    async page(path: string): Promise<VisitPage> { return parseVisitPage(await request<unknown>(path, { method: 'GET' })); },
    async summary(): Promise<VisitSummary | null> { return parseSummary(await request<unknown>(`${BASE}/summary`, { method: 'GET' })); },
    create(body: VisitCreateInput): Promise<Visit> { return request<Visit>(BASE, json('POST', body)); },
    update(id: string, body: VisitUpdateInput): Promise<Visit> { return request<Visit>(`${BASE}/${enc(id)}`, json('PATCH', body)); },
    move(id: string, action: 'check-in' | 'check-out' | 'cancel', revision: number): Promise<Visit> {
      return request<Visit>(`${BASE}/${enc(id)}/${action}`, json('POST', { expected_revision: revision, input_mode: 'click' }));
    },
    remove(id: string, revision: number): Promise<void> {
      return requestVoid(`${BASE}/${enc(id)}?expected_revision=${revision}&input_mode=click`, { method: 'DELETE' });
    },
  };
}
export type ReceptionClient = ReturnType<typeof createReceptionClient>;
