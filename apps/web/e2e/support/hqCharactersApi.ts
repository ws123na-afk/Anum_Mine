import { TENANT_A } from './hqApi';

// A small campus for the character specs and the review screenshots (docs/ANUM_MINE_HQ_PEOPLE_CHARACTERS.md): Tyre Pulse with a workshop,
// a warehouse and an office, and Executive HQ with a boardroom and an executive office. Every desk carries a plainly fake agent id and nobody
// is invented: a figure is drawn only because a desk has an assigned agent. Names, ids and rooms are test data.
const stamp = '2026-10-20T08:00:00Z';
const base = (id: string) => ({ id, revision: 1, tenant_id: TENANT_A.tenantId, workspace_id: TENANT_A.workspaceId, created_at: stamp, updated_at: stamp });

export interface CharacterRoom { id: string; name: string; kind: 'office' | 'meeting' | 'reception' | 'workshop' | 'warehouse' | 'lounge' | 'command'; box: [number, number, number, number]; seats: number }

const BUILDINGS: { id: string; name: string; floor: string; rooms: CharacterRoom[] }[] = [
  { id: 'ch-tyre', name: 'Tyre Pulse', floor: 'Ground floor: Workshop, Warehouse and Logistics', rooms: [
    { id: 'ch-tyre-workshop', name: 'Workshop', kind: 'workshop', box: [0, 0, 14, 10], seats: 4 },
    { id: 'ch-tyre-warehouse', name: 'Warehouse', kind: 'warehouse', box: [16, 0, 14, 10], seats: 3 },
    { id: 'ch-tyre-logistics', name: 'Logistics', kind: 'office', box: [0, 12, 14, 9], seats: 4 },
  ] },
  { id: 'ch-exec', name: 'Executive HQ', floor: 'Floor 3: Executive and Strategy', rooms: [
    { id: 'ch-exec-board', name: 'Boardroom', kind: 'meeting', box: [0, 0, 16, 10], seats: 6 },
    { id: 'ch-exec-office', name: 'Executive Office', kind: 'office', box: [18, 0, 14, 10], seats: 3 },
  ] },
];

export function characterAgents(): string[] { return BUILDINGS.flatMap((b) => b.rooms.flatMap((r) => Array.from({ length: r.seats }, (_, i) => `agent_ch_${r.id.replace('ch-', '').replace(/-/g, '_')}_${i + 1}`))); }

export function characterRegistry() {
  const buildings = BUILDINGS.map((b) => ({ ...base(b.id), name: b.name, timezone: 'Asia/Dubai', latitude: null, longitude: null }));
  const floors = BUILDINGS.map((b, i) => ({ ...base(`${b.id}-l${i}`), building_id: b.id, level: i === 0 ? 0 : 3, name: b.floor }));
  const rooms = BUILDINGS.flatMap((b, i) => b.rooms.map((r) => ({
    ...base(r.id), floor_id: `${b.id}-l${i}`, name: r.name, kind: r.kind, capacity: r.seats + 2,
    scene_x: r.box[0], scene_z: r.box[1], scene_width: r.box[2], scene_depth: r.box[3], team_department: null,
  })));
  const ids = characterAgents();
  let n = 0;
  const desks = BUILDINGS.flatMap((b) => b.rooms.flatMap((r) => Array.from({ length: r.seats }, (_, i) => ({ ...base(`${r.id}-d${i + 1}`), room_id: r.id, label: `${r.name} Desk ${i + 1}`, agent_id: ids[n++] }))));
  return { buildings, floors, rooms, desks };
}
