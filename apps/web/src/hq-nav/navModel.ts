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
      id: floor.id, name: floor.name, level: floor.level, rooms: floor.rooms.length,
      assigned: floor.desks.filter((d) => d.agentId).length, kinds,
    };
  });
  return rows.sort((a, b) => b.level - a.level || a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------- quick-view strip

export type StripStop =
  | Readonly<{ id: 'campus'; kind: 'campus' }>
  | Readonly<{ id: string; kind: 'building'; workspaceId: string; name: string; accessible: boolean; current: boolean }>
  | Readonly<{ id: string; kind: 'room'; roomId: string; floorId: string; floorName: string; label: string; roomKind: string }>;

export const MAX_ROOM_STOPS = 12;
/** When the open building has more rooms than this, the strip lists one room per room kind instead of every room. */
export const ALL_ROOMS_UP_TO = 8;

const KIND_RANK: Record<string, number> = { reception: 0, office: 1, meeting: 2, workshop: 3, warehouse: 4, lounge: 5 };
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
    id: `room:${room.id}`, kind: 'room' as const, roomId: room.id, floorId: floor.id, floorName: floor.name, label: room.label, roomKind: room.kind,
  }));
}

/** The whole strip: the campus aerial, one stop per building, then the rooms of the open building. */
export function stripStops(campus: Campus | null, building: SceneBuilding | null, currentWorkspaceId: string): StripStop[] {
  const stops: StripStop[] = [{ id: 'campus', kind: 'campus' }];
  if (campus) {
    const order = campus.buildings.slice().sort((a, b) => (a.kind === 'executive' ? -1 : 0) - (b.kind === 'executive' ? -1 : 0) || a.name.localeCompare(b.name) || a.workspace_id.localeCompare(b.workspace_id));
    for (const b of order) {
      if (b.archived) continue;
      stops.push({ id: `building:${b.workspace_id}`, kind: 'building', workspaceId: b.workspace_id, name: b.name, accessible: b.accessible, current: b.workspace_id === currentWorkspaceId });
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

/** Tags for a campus card from real data only: the building type (when an owner set it), floors and rooms, and (open building) departments. */
export function cardTags(b: CampusBuilding, departments: readonly string[], limit = 3): Array<{ kind: 'type' | 'floors' | 'rooms' | 'department'; value: string | number }> {
  const tags: Array<{ kind: 'type' | 'floors' | 'rooms' | 'department'; value: string | number }> = [];
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
  const rooms = floor.rooms.map((r): PlanRect => ({ id: r.id, label: r.label, kind: r.kind, x: ox + (r.x - minX) * fit, y: oy + (r.z - minZ) * fit, w: r.width * fit, h: r.depth * fit }));
  const desks = floor.desks.map((d): PlanDot => ({ id: d.id, x: ox + (d.position.x - minX) * fit, y: oy + (d.position.z - minZ) * fit, assigned: d.agentId !== null }));
  return { width: size, height, rooms, desks };
}

// ---------------------------------------------------------------- people at their posts

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
}>;

/**
 * The agents assigned to desks on a floor, with the state their presence record gives. A desk with nobody assigned
 * is left out ("No one assigned" is the empty state); an assignment without a presence record says "Not connected".
 * A private agent keeps its desk but never its name.
 */
export function peopleAtPosts(floor: SceneFloor | null, presence: HqPresenceIndex | null): PersonRow[] {
  if (!floor) return [];
  const roomLabel = new Map(floor.rooms.map((r) => [r.id, r.label]));
  const rows: PersonRow[] = [];
  for (const desk of floor.desks) {
    const record = presenceForDesk(presence, desk);
    if (!desk.agentId && !record) continue;
    const hidden = record?.reason === 'agent_not_visible' || (record?.agentId.startsWith('desk:') ?? false);
    const base = { key: desk.id, roomLabel: roomLabel.get(desk.roomId) ?? '', desk };
    if (hidden) { rows.push({ ...base, agentId: null, name: null, role: null, status: record?.status ?? null, stale: false, state: 'private' }); continue; }
    if (!record) { rows.push({ ...base, agentId: desk.agentId, name: desk.agent?.name || null, role: null, status: null, stale: false, state: 'not_connected' }); continue; }
    rows.push({ ...base, agentId: record.agentId, name: record.name || desk.agent?.name || null, role: record.role, status: record.status, stale: record.stale === true, state: 'known' });
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
