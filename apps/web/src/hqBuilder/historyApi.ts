// Calls of the layout history and template API (docs/ANUM_MINE_LAYOUT_HISTORY.md). The transport is
// injected like builderApi.ts, so the unit tests need no browser. Scope comes from the request headers.
import type {
  OfficeLayoutRestoreRequest, OfficeLayoutRestoreResult, OfficeLayoutRevisionDetail, OfficeLayoutRevisionList,
  OfficeLayoutTemplate, OfficeLayoutTemplateKind, OfficeLayoutTemplateList, OfficeLayoutTemplateSaved,
} from '@anum/contracts';
import type { OfficeBuilding, OfficeFloor, OfficeRequester, OfficeVoidRequester } from '../lib/officeSpaceClient.ts';

const ROOT = '/api/v1/office-layout';
const enc = encodeURIComponent;
const json = (body: unknown): string => JSON.stringify(body);

export interface FloorFromTemplateResult { floor: OfficeFloor; room_ids: string[]; desk_count: number }
export interface BuildingFromTemplateResult { building: OfficeBuilding; floor_ids: string[]; room_count: number; desk_count: number }

export function createHistoryApi(request: OfficeRequester, remove: OfficeVoidRequester) {
  return {
    revisions: (floorId: string, before?: number | null, limit = 30) => {
      const query = new URLSearchParams({ limit: String(limit) });
      if (before) query.set('before', String(before));
      return request<OfficeLayoutRevisionList>(`${ROOT}/floors/${enc(floorId)}/revisions?${query.toString()}`, { method: 'GET' });
    },
    revision: (floorId: string, revisionNo: number, against: 'current' | 'previous') =>
      request<OfficeLayoutRevisionDetail>(`${ROOT}/floors/${enc(floorId)}/revisions/${revisionNo}?against=${against}`, { method: 'GET' }),
    /** A click only action: the body always says so, and the server refuses anything else. */
    restore: (floorId: string, revisionNo: number, body: Omit<OfficeLayoutRestoreRequest, 'input_mode'>) =>
      request<OfficeLayoutRestoreResult>(`${ROOT}/floors/${enc(floorId)}/revisions/${revisionNo}/restore`,
        { method: 'POST', body: json({ ...body, input_mode: 'click' }) }),
    templates: () => request<OfficeLayoutTemplateList>(`${ROOT}/templates`, { method: 'GET' }),
    saveFloorTemplate: (body: { id: string; floor_id: string; name: string }) =>
      request<OfficeLayoutTemplateSaved>(`${ROOT}/templates/from-floor`, { method: 'POST', body: json(body) }),
    saveBuildingTemplate: (body: { id: string; building_id: string; name: string }) =>
      request<OfficeLayoutTemplateSaved>(`${ROOT}/templates/from-building`, { method: 'POST', body: json(body) }),
    renameTemplate: (id: string, name: string, revision: number) =>
      request<OfficeLayoutTemplate>(`${ROOT}/templates/${enc(id)}`, { method: 'PATCH', body: json({ name, expected_revision: revision }) }),
    deleteTemplate: (id: string, revision: number) =>
      remove(`${ROOT}/templates/${enc(id)}?${new URLSearchParams({ expected_revision: String(revision) }).toString()}`, { method: 'DELETE' }),
    floorFromTemplate: (body: { template_id: string; id: string; building_id: string; level: number; name?: string; name_ar?: string }) =>
      request<FloorFromTemplateResult>(`${ROOT}/floors/from-template`, { method: 'POST', body: json(body) }),
    buildingFromTemplate: (body: { template_id: string; id: string; name: string; name_ar?: string; timezone: string }) =>
      request<BuildingFromTemplateResult>(`${ROOT}/buildings/from-template`, { method: 'POST', body: json(body) }),
  };
}
export type HistoryApi = ReturnType<typeof createHistoryApi>;
export type { OfficeLayoutTemplateKind };
