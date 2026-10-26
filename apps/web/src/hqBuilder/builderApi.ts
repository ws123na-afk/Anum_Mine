// Calls of the office builder API (docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md, "Office builder API").
// The transport is injected, like officeSpaceClient.ts, so the unit tests need no browser. Scope
// (tenant and workspace) comes from the request headers; it is never part of a body.
import type {
  OfficeBuildingUpdate, OfficeDeskUpdate, OfficeFloorLayoutRequest, OfficeFloorUpdate, OfficeRoomCopyRequest,
  OfficeRoomFromTemplateRequest, OfficeRoomTeam, OfficeRoomUpdate, OfficeTemplateCatalogue,
} from '@anum/contracts';
import type { OfficeBuilding, OfficeDesk, OfficeFloor, OfficeRequester, OfficeRoom, OfficeVoidRequester } from '../lib/officeSpaceClient.ts';

const ROOT = '/api/v1/office-spaces';
const enc = encodeURIComponent;
const json = (body: unknown): string => JSON.stringify(body);

export interface LayoutSaved { floor_id: string; rooms: OfficeRoom[]; desks: OfficeDesk[] }
export interface RoomWithDesks { room: OfficeRoom; desks: OfficeDesk[] }

export function makeOfficeId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function createBuilderApi(request: OfficeRequester, remove: OfficeVoidRequester) {
  const revisionQuery = (revision: number) => `?${new URLSearchParams({ expected_revision: String(revision) }).toString()}`;
  return {
    templates: () => request<OfficeTemplateCatalogue>(`${ROOT}/templates`, { method: 'GET' }),
    team: (roomId: string) => request<OfficeRoomTeam>(`${ROOT}/rooms/${enc(roomId)}/team`, { method: 'GET' }),
    /** The one atomic save of staged placement: all rooms and desks, or none. */
    saveLayout: (floorId: string, body: OfficeFloorLayoutRequest) =>
      request<LayoutSaved>(`${ROOT}/floors/${enc(floorId)}/layout`, { method: 'PUT', body: json(body) }),
    patchBuilding: (id: string, body: OfficeBuildingUpdate) => request<OfficeBuilding>(`${ROOT}/buildings/${enc(id)}`, { method: 'PATCH', body: json(body) }),
    patchFloor: (id: string, body: OfficeFloorUpdate) => request<OfficeFloor>(`${ROOT}/floors/${enc(id)}`, { method: 'PATCH', body: json(body) }),
    patchRoom: (id: string, body: OfficeRoomUpdate) => request<OfficeRoom>(`${ROOT}/rooms/${enc(id)}`, { method: 'PATCH', body: json(body) }),
    patchDesk: (id: string, body: OfficeDeskUpdate) => request<OfficeDesk>(`${ROOT}/desks/${enc(id)}`, { method: 'PATCH', body: json(body) }),
    assignDesk: (id: string, agentId: string | null, revision: number) =>
      request<OfficeDesk>(`${ROOT}/desks/${enc(id)}/assignment`, { method: 'PATCH', body: json({ agent_id: agentId, expected_revision: revision }) }),
    createRoom: (body: { id: string; floor_id: string; name: string; kind: OfficeRoom['kind']; capacity: number }) =>
      request<OfficeRoom>(`${ROOT}/rooms`, { method: 'POST', body: json(body) }),
    createDesk: (body: { id: string; room_id: string; label: string }) => request<OfficeDesk>(`${ROOT}/desks`, { method: 'POST', body: json(body) }),
    fromTemplate: (body: OfficeRoomFromTemplateRequest) => request<RoomWithDesks>(`${ROOT}/rooms/from-template`, { method: 'POST', body: json(body) }),
    copyRoom: (id: string, body: OfficeRoomCopyRequest) => request<RoomWithDesks>(`${ROOT}/rooms/${enc(id)}/copy`, { method: 'POST', body: json(body) }),
    deleteRoom: (id: string, revision: number) => remove(`${ROOT}/rooms/${enc(id)}${revisionQuery(revision)}`, { method: 'DELETE' }),
    deleteDesk: (id: string, revision: number) => remove(`${ROOT}/desks/${enc(id)}${revisionQuery(revision)}`, { method: 'DELETE' }),
    deleteFloor: (id: string, revision: number) => remove(`${ROOT}/floors/${enc(id)}${revisionQuery(revision)}`, { method: 'DELETE' }),
    deleteBuilding: (id: string, revision: number) => remove(`${ROOT}/buildings/${enc(id)}${revisionQuery(revision)}`, { method: 'DELETE' }),
  };
}
export type BuilderApi = ReturnType<typeof createBuilderApi>;
