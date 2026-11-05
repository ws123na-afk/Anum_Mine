/**
 * Navigation model of the headquarters: the floor list, the quick-view strip, the label-card density
 * rules, the floor plan geometry and the people at their posts.
 *
 * Data truth (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md): everything here is derived from the campus, the
 * saved office layout (buildings, floors, rooms, desks) and the presence index. Nothing is invented: no
 * room kind, floor name or person appears unless a record backs it. Pure functions, no DOM.
 */
import type { Campus, CampusBuilding, StatusKey } from '../lib/campusModel.ts';
import type { RoomGeometry, SceneBuilding, SceneDesk, SceneFloor } from '../lib/headquartersProjection.ts';
import { presenceForDesk, type HqPresenceIndex } from '../lib/hqPresence.ts';
import { roomName, sceneName } from '../lib/registryNames.ts';

// ---------------------------------------------------------------- floors

export type FloorKindCount = Readonly<{ kind: string; count: number }>;
export type FloorEntry = Readonly<{
  id: string;
  name: string;
  level: number;
  rooms: number;
  /** Desks with someone assigned (a registry fact). */
  assigned: number;
  /** Room kinds present on the floor with counts, most common first. */
  kinds: readonly FloorKindCount[];
}>;

/** Floors of a building as the rail lists them: the top floor first, like the building from the street. */
export function floorEntries(building: SceneBuilding | null): FloorEntry[] {
  if (!building) return [];
  const rows = building.floors.map((floor): FloorEntry => {
    const counts = new Map<string, number>();
    for (const room of floor.rooms) counts.set(room.kind, (counts.get(room.kind) ?? 0) + 1);
    const kinds = [...counts.entries()].map(([kind, count]) => ({ kind, count })).sort((a, b) => b.count - a.count || a.kind.localeCompare(b.kind));
    return {
      id: floor.id, name: sceneName(floor), level: floor.level, rooms: floor.rooms.length,
      assigned: floor.desks.filter((d) => d.agentId).length, kinds,
    };
  });
  return rows.sort((a, b) => b.level - a.level || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------- opening a room on any floor

export type RoomMove = Readonly<{ buildingId: string; floorId: string; roomId: string; switchBuilding: boolean; switchFloor: boolean }>;

/**
 * Where the view must go to show a room picked anywhere (the scene, the floor rail, the quick views, the search, the team
 * panel, the history, the flow): the building and the floor that hold the room, and whether the open building or floor
 * must change first. The floor id the caller passes is a hint only: the saved layout decides, so a stale hint never opens
 * the wrong floor. A room no saved floor holds gives null (nothing changes).
 */
export function roomMove(
  buildings: ReadonlyArray<Pick<SceneBuilding, 'id' | 'floors'>>, current: Readonly<{ buildingId: string | null; floorId: string | null }>, roomId: string, floorHint?: string | null,
): RoomMove | null {
  let found: { b: string; f: string } | null = null;
  for (const b of buildings) for (const f of b.floors) {
    if (!f.rooms.some((r) => r.id === roomId)) continue;
    // The hinted floor wins when it really holds the room; else the first floor of the open building, else the first one found.
    const better = !found || (f.id === floorHint) || (found.b !== current.buildingId && b.id === current.buildingId && found.f !== floorHint);
    if (better) found = { b: b.id, f: f.id };
  }
  if (!found) return null;
  const switchBuilding = found.b !== current.buildingId;
  return { buildingId: found.b, floorId: found.f, roomId, switchBuilding, switchFloor: switchBuilding || found.f !== current.floorId };
}

// ---------------------------------------------------------------- quick-view strip

export type StripStop =
  | Readonly<{ id: 'campus'; kind: 'campus' }>
  | Readonly<{ id: string; kind: 'building'; workspaceId: string; name: string; accessible: boolean; current: boolean }>
  | Readonly<{ id: string; kind: 'room'; roomId: string; floorId: string; floorName: string; label: string; roomKind: string }>;

export const MAX_ROOM_STOPS = 12;
/** When the open building has more rooms than this, the strip lists one room per room kind instead of every room. */
export const ALL_ROOMS_UP_TO = 8;

const KIND_RANK: Record<string, number> = { reception: 0, office: 1, meeting: 2, workshop: 3, warehouse: 4, lounge: 5, command: 2 };
const rank = (kind: string) => KIND_RANK[kind] ?? 9;

/**
 * Room stops of the open building: every room when there are few, otherwise the first room of each room kind
 * (so a building with forty offices still gets a short strip). Reception first, then by floor from the ground up.
 */
export function roomStops(building: SceneBuilding | null, max = MAX_ROOM_STOPS): StripStop[] {
  if (!building) return [];
  const rows: Array<{ room: RoomGeometry; floor: SceneFloor }> = [];
  for (const floor of [...building.floors].sort((a, b) => a.level - b.level)) for (const room of floor.rooms) rows.push({ room, floor });
  let chosen = rows;
  if (rows.length > ALL_ROOMS_UP_TO) {
    const seen = new Set<string>();
    chosen = rows.filter(({ room }) => { if (seen.has(room.kind)) return false; seen.add(room.kind); return true; });
  }
  chosen = chosen.slice().sort((a, b) => rank(a.room.kind) - rank(b.room.kind) || a.floor.level - b.floor.level || a.room.label.localeCompare(b.room.label));
  return chosen.slice(0, max).map(({ room, floor }) => ({
    id: `room:${room.id}`, kind: 'room' as const, roomId: room.id, floorId: floor.id, floorName: sceneName(floor), label: roomName(room), roomKind: room.kind,
  }));
}

/** The whole strip: the campus aerial, one stop per building, then the rooms of the open building. */
export function stripStops(campus: Campus | null, building: SceneBuilding | null, currentWorkspaceId: string): StripStop[] {
  const stops: StripStop[] = [{ id: 'campus', kind: 'campus' }];
  if (campus) {
    const order = campus.buildings.slice().sort((a, b) => (a.kind === 'executive' ? -1 : 0) - (b.kind === 'executive' ? -1 : 0) || a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
    for (const b of order) {
      if (b.archived) continue;
      stops.push({ id: `building:${b.workspace_id}`, kind: 'building', workspaceId: b.workspace_id, name: sceneName(b), accessible: b.accessible, current: b.workspace_id === currentWorkspaceId });
    }
  }
  return [...stops, ...roomStops(building)];
}

/** The stop that is showing now: the campus, the selected building, or the selected room; else the open building. */
export function currentStopId(args: Readonly<{ level: 'campus' | 'building'; selectedBuildingId: string | null; selectedRoomId: string | null; currentWorkspaceId: string }>): string {
  if (args.level === 'campus') return args.selectedBuildingId ? `building:${args.selectedBuildingId}` : 'campus';
  if (args.selectedRoomId) return `room:${args.selectedRoomId}`;
  return `building:${args.currentWorkspaceId}`;
}

// ---------------------------------------------------------------- campus label cards

export type CardDensity = 'full' | 'compact' | 'dot';
export const MAX_FULL_CARDS = 6;
export const MAX_COMPACT_CARDS = 14;

/**
 * How much of a building's label card is drawn, so many buildings do not bury the scene. With up to six
 * buildings every card is full. Beyond that the selected building, the open one and the executive tower keep a
 * full card, then the busiest buildings fill the rest of the six; the others are compact (name and count) and,
 * past fourteen buildings, a dot with the name on hover and focus. A card is never removed: it is the way in.
 */
export function cardDensities(
  buildings: readonly CampusBuilding[], args: Readonly<{ selectedId: string | null; currentId: string }>, maxFull = MAX_FULL_CARDS, maxCompact = MAX_COMPACT_CARDS,
): Map<string, CardDensity> {
  const out = new Map<string, CardDensity>();
  if (buildings.length <= maxFull) { for (const b of buildings) out.set(b.workspace_id, 'full'); return out; }
  const pinned = (b: CampusBuilding) => b.workspace_id === args.selectedId || b.workspace_id === args.currentId || b.kind === 'executive';
  const ranked = buildings.slice().sort((a, b) =>
    (pinned(b) ? 1 : 0) - (pinned(a) ? 1 : 0) || (b.totals?.agents ?? -1) - (a.totals?.agents ?? -1) || a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
  ranked.forEach((b, index) => {
    const density: CardDensity = pinned(b) && index < Math.max(maxFull, 1) ? 'full' : index < maxFull ? 'full' : index < maxCompact ? 'compact' : 'dot';
    // A selected or open building is never smaller than compact, however many buildings there are.
    out.set(b.workspace_id, density === 'dot' && pinned(b) ? 'compact' : density);
  });
  return out;
}

/** How many buildings show a name chip at once on a campus (the rest are status dots); a phone-sized stage shows fewer. */
export const MAX_CHIPS = 4;
export const MAX_CHIPS_COMPACT = 2;

/**
 * The quiet campus (docs/ANUM_MINE_HQ_LABELS.md): the 3D boards carry the details, so a building's page label is a small chip (name and status
 * dot) for only a few buildings and a status dot for the rest. Priority: the selected building, the open workspace's building, the executive
 * tower, then the buildings nearest the point the camera looks at. Every building keeps its button (a dot has the same accessible name), so none
 * is ever left out. `at` gives each building's ground position (the placements); a building without one ranks last.
 */
export function quietDensities(
  buildings: readonly CampusBuilding[],
  args: Readonly<{ selectedId: string | null; currentId: string; target: Readonly<{ x: number; z: number }>; at: ReadonlyMap<string, Readonly<{ x: number; z: number }>>; max?: number }>,
): Map<string, CardDensity> {
  const max = args.max ?? MAX_CHIPS;
  const pinned = (b: CampusBuilding): number => (b.workspace_id === args.selectedId ? 3 : b.workspace_id === args.currentId ? 2 : b.kind === 'executive' ? 1 : 0);
  const far = (b: CampusBuilding): number => { const p = args.at.get(b.workspace_id); return p ? Math.hypot(p.x - args.target.x, p.z - args.target.z) : Infinity; };
  const ranked = buildings.slice().sort((a, b) => pinned(b) - pinned(a) || far(a) - far(b) || a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
  const out = new Map<string, CardDensity>();
  ranked.forEach((b, i) => out.set(b.workspace_id, i < max || b.workspace_id === args.selectedId ? 'compact' : 'dot'));
  return out;
}

/** Tags for a campus card from real data only: the building type (when an owner set it), floors and rooms, and (open building) departments. */
export function cardTags(b: CampusBuilding, departments: readonly string[], limit = 3): Array<{ kind: 'type' | 'workspace' | 'floors' | 'rooms' | 'department'; value: string | number }> {
  const tags: Array<{ kind: 'type' | 'workspace' | 'floors' | 'rooms' | 'department'; value: string | number }> = [];
  // A registry building says which workspace it belongs to and then how big it is; the workspace's own building says what it is.
  if (b.workspaceName) { tags.push({ kind: 'workspace', value: b.workspaceName }); if (b.floors) tags.push({ kind: 'floors', value: b.floors }); return tags.slice(0, limit); }
  if (b.kindKnown !== false) tags.push({ kind: 'type', value: b.kind });
  for (const d of departments.slice(0, 1)) { if (tags.length >= limit) break; tags.push({ kind: 'department', value: d }); }
  if (b.floors && tags.length < limit) tags.push({ kind: 'floors', value: b.floors });
  if (b.rooms && tags.length < limit) tags.push({ kind: 'rooms', value: b.rooms });
  return tags.slice(0, limit);
}

// ---------------------------------------------------------------- floor plan

export type PlanRect = Readonly<{ id: string; label: string; kind: string; x: number; y: number; w: number; h: number }>;
export type PlanDot = Readonly<{ id: string; x: number; y: number; assigned: boolean }>;
export type PlanModel = Readonly<{ width: number; height: number; rooms: readonly PlanRect[]; desks: readonly PlanDot[] }>;

/** The floor's saved geometry scaled into a `size` wide box (padding included). Empty floors give no rooms. */
export function planModel(floor: SceneFloor | null, size = 100, pad = 4): PlanModel {
  if (!floor || floor.rooms.length === 0) return { width: size, height: Math.round(size * 0.6), rooms: [], desks: [] };
  const minX = Math.min(...floor.rooms.map((r) => r.x));
  const maxX = Math.max(...floor.rooms.map((r) => r.x + r.width));
  const minZ = Math.min(...floor.rooms.map((r) => r.z));
  const maxZ = Math.max(...floor.rooms.map((r) => r.z + r.depth));
  const spanX = Math.max(1, maxX - minX), spanZ = Math.max(1, maxZ - minZ);
  const scale = (size - pad * 2) / spanX;
  const height = Math.max(Math.round(size * 0.35), Math.min(size * 1.4, Math.round(spanZ * scale + pad * 2)));
  const fit = Math.min(scale, (height - pad * 2) / spanZ);
  const ox = pad + ((size - pad * 2) - spanX * fit) / 2, oy = pad + ((height - pad * 2) - spanZ * fit) / 2;
  const rooms = floor.rooms.map((r): PlanRect => ({ id: r.id, label: roomName(r), kind: r.kind, x: ox + (r.x - minX) * fit, y: oy + (r.z - minZ) * fit, w: r.width * fit, h: r.depth * fit }));
  const desks = floor.desks.map((d): PlanDot => ({ id: d.id, x: ox + (d.position.x - minX) * fit, y: oy + (d.position.z - minZ) * fit, assigned: d.agentId !== null }));
  return { width: size, height, rooms, desks };
}

// ---------------------------------------------------------------- people at their posts

import { deskShiftLine, WORK_STATUSES } from '../lib/hqShifts.ts';

export type PersonRow = Readonly<{
  key: string;
  /** The agent id, or null for a private agent (no name is shown). */
  agentId: string | null;
  name: string | null;
  role: string | null;
  status: StatusKey | null;
  stale: boolean;
  /** `known` has a presence record; `not_connected` has an assignment but no record; `private` is another person's agent. */
  state: 'known' | 'not_connected' | 'private';
  roomLabel: string;
  desk: SceneDesk;
  /** Set when the person is off shift now (their work state is then told as "Off shift"). */
  offShift?: 'off' | 'off_waiting';
}>;

/**
 * The agents assigned to desks on a floor, with the state their presence record gives. A desk with nobody assigned
 * is left out ("No one assigned" is the empty state); an assignment without a presence record says "Not connected".
 * A private agent keeps its desk but never its name.
 */
export function peopleAtPosts(floor: SceneFloor | null, presence: HqPresenceIndex | null): PersonRow[] {
  if (!floor) return [];
  const roomLabel = new Map(floor.rooms.map((r) => [r.id, roomName(r)]));
  const rows: PersonRow[] = [];
  for (const desk of floor.desks) {
    const record = presenceForDesk(presence, desk);
    if (!desk.agentId && !record) continue;
    const hidden = record?.reason === 'agent_not_visible' || (record?.agentId.startsWith('desk:') ?? false);
    const base = { key: desk.id, roomLabel: roomLabel.get(desk.roomId) ?? '', desk };
    if (hidden) { rows.push({ ...base, agentId: null, name: null, role: null, status: record?.status ?? null, stale: false, state: 'private' }); continue; }
    if (!record) { rows.push({ ...base, agentId: desk.agentId, name: desk.agent?.name || null, role: null, status: null, stale: false, state: 'not_connected' }); continue; }
    const line = deskShiftLine(desk.shiftInfo, WORK_STATUSES.has(record.status));
    rows.push({ ...base, agentId: record.agentId, name: record.name || desk.agent?.name || null, role: record.role, status: record.status, stale: record.stale === true, state: 'known',
      ...(line === 'off' || line === 'off_waiting' ? { offShift: line } : {}) });
  }
  return rows.sort((a, b) => a.roomLabel.localeCompare(b.roomLabel) || (a.name === null ? 1 : 0) - (b.name === null ? 1 : 0) || (a.name ?? '').localeCompare(b.name ?? '') || a.key.localeCompare(b.key));
}

/** Departments of the agents in a presence list, most common first (the open building's card tags). */
export function departmentsOf(roles: ReadonlyArray<string | null>, limit = 2): string[] {
  const counts = new Map<string, number>();
  for (const role of roles) { const r = role?.trim(); if (r) counts.set(r, (counts.get(r) ?? 0) + 1); }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, limit).map(([name]) => name);
}

export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '';
  const first = Array.from(parts[0])[0] ?? '';
  const last = parts.length > 1 ? Array.from(parts[parts.length - 1])[0] ?? '' : '';
  return (first + last).toUpperCase();
}
