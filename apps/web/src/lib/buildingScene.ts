/**
 * Procedural building cutaway: floor slabs, rooms as zones, glazed partitions, desks and the
 * furniture that belongs to each room kind. Geometry comes from the persisted room geometry
 * (`SceneFloor`), never from a template, so a saved layout always shows as saved. People are not
 * drawn as characters: a desk shows its monitor state (working, waiting, idle, offline, vacant)
 * and the HTML agent card carries the name, role and status pill.
 */
import { MeshBuilder, hex, mix, scale, type Rgb } from './hqGeometry.ts';
import type { RoomGeometry, SceneDesk, SceneFloor, Vec3 } from './headquartersProjection.ts';
import type { SceneMesh } from './campusScene.ts';
import type { Aabb } from './hqMath.ts';

export const FLOOR_PITCH = 4.2;
export const WALL_HEIGHT = 3.0;
const SLAB = 0.3;

export type DeskVisual = 'working' | 'waiting' | 'idle' | 'unknown' | 'vacant' | 'assigned';

export type BuildingSceneOptions = Readonly<{
  /** Index into `floors` of the floor shown in full. */
  activeIndex: number;
  /** Draw every floor from the ground up to the top (exploded vertical stack) instead of cutting above the active one. */
  showAll?: boolean;
  selectedRoomId?: string | null;
  /** Visual state per desk id; missing means "assigned or vacant by registry only". */
  deskState?: ReadonlyMap<string, DeskVisual>;
  /** Rooms with a live meeting: the screen and table light up. */
  meetingRoomIds?: ReadonlySet<string>;
}>;

export type BuildingRoomHandle = Readonly<{ id: string; floorIndex: number; box: Aabb; anchor: Vec3 }>;
export type BuildingDeskHandle = Readonly<{ id: string; roomId: string; floorIndex: number; anchor: Vec3 }>;

export type BuildingScene = Readonly<{
  opaque: SceneMesh;
  glass: SceneMesh;
  rooms: readonly BuildingRoomHandle[];
  desks: readonly BuildingDeskHandle[];
  bounds: Aabb;
  center: Vec3;
  triangles: number;
}>;

const FLOOR_BY_KIND: Record<string, Rgb> = {
  office: hex('#cfd8dc'), meeting: hex('#d9cdb4'), reception: hex('#e0d5c3'),
  workshop: hex('#b7c0c4'), warehouse: hex('#a9b0b2'), lounge: hex('#cdd7c8'),
};
const ACCENT_BY_KIND: Record<string, Rgb> = {
  office: hex('#45d0b5'), meeting: hex('#f2b54c'), reception: hex('#d9b25f'),
  workshop: hex('#6aa7ff'), warehouse: hex('#8aa0b3'), lounge: hex('#7fd39a'),
};
const WOOD = hex('#8a6a4a');
const DARK = hex('#26313b');
const SCREEN: Record<DeskVisual, { color: Rgb; emissive: number }> = {
  working: { color: hex('#58f0cf'), emissive: 1 },
  waiting: { color: hex('#ffc861'), emissive: 0.95 },
  idle: { color: hex('#6aa7ff'), emissive: 0.55 },
  unknown: { color: hex('#8b95a5'), emissive: 0.3 },
  assigned: { color: hex('#8b95a5'), emissive: 0.25 },
  vacant: { color: hex('#232c36'), emissive: 0 },
};

const freeze = (b: MeshBuilder): SceneMesh => ({ vertices: b.toFloat32(), bounds: b.bounds(), triangles: b.triangleCount });

function desk(b: MeshBuilder, d: SceneDesk, y: number, spacing: number, state: DeskVisual): void {
  const s = Math.max(0.55, Math.min(1, spacing / 1.3));
  const w = 1.15 * s, dep = 0.62 * s, x = d.position.x - w / 2, z = d.position.z - dep / 2;
  b.box(x, y + 0.7 * s, z, w, 0.07, dep, WOOD);
  b.box(x + 0.05, y, z + 0.05, 0.07, 0.7 * s, 0.07, DARK);
  b.box(x + w - 0.12, y, z + 0.05, 0.07, 0.7 * s, 0.07, DARK);
  b.box(x + 0.05, y, z + dep - 0.12, 0.07, 0.7 * s, 0.07, DARK);
  b.box(x + w - 0.12, y, z + dep - 0.12, 0.07, 0.7 * s, 0.07, DARK);
  const screen = SCREEN[state];
  b.box(x + w * 0.3, y + 0.77 * s, z + 0.08 * s, w * 0.4, 0.34 * s, 0.04, DARK);
  b.box(x + w * 0.32, y + 0.8 * s, z + 0.12 * s, w * 0.36, 0.28 * s, 0.02, screen.color, { emissive: screen.emissive, shade: false });
  // Chair behind the desk (towards +z).
  b.box(x + w * 0.3, y + 0.28 * s, z + dep + 0.1, w * 0.4, 0.07, w * 0.36, hex('#3a4a5a'));
  b.box(x + w * 0.3, y + 0.35 * s, z + dep + 0.1 + w * 0.3, w * 0.4, 0.5 * s, 0.07, hex('#3a4a5a'));
  b.prism(x + w * 0.5, y, z + dep + 0.1 + w * 0.18, 0.05, 0.05, 0.28 * s, 5, DARK);
}

function furnish(
  b: MeshBuilder, room: RoomGeometry, y: number, accent: Rgb, live: boolean,
): void {
  const { x, z, width: w, depth: d } = room;
  const inset = 0.35;
  switch (room.kind) {
    case 'meeting': {
      const tw = Math.max(1.6, w * 0.5), td = Math.max(0.9, d * 0.28);
      const tx = x + (w - tw) / 2, tz = z + (d - td) / 2;
      b.box(tx, y + 0.72, tz, tw, 0.09, td, live ? mix(WOOD, accent, 0.25) : WOOD);
      b.box(tx + tw / 2 - 0.12, y, tz + td / 2 - 0.12, 0.24, 0.72, 0.24, DARK);
      const seats = Math.max(2, Math.min(10, Math.floor(tw / 0.62)));
      for (let i = 0; i < seats; i++) {
        const sx = tx + 0.3 + (i * (tw - 0.6)) / Math.max(1, seats - 1);
        b.box(sx - 0.2, y + 0.3, tz - 0.5, 0.4, 0.07, 0.4, hex('#3a4a5a'));
        b.box(sx - 0.2, y + 0.3, tz + td + 0.1, 0.4, 0.07, 0.4, hex('#3a4a5a'));
      }
      b.box(x + inset, y + 0.9, z + 0.12, w - inset * 2, 1.3, 0.06, live ? hex('#58f0cf') : hex('#1b2733'), { emissive: live ? 0.95 : 0.15, shade: false });
      break;
    }
    case 'reception': {
      b.box(x + w * 0.2, y, z + d * 0.55, w * 0.6, 1.05, 0.55, hex('#e9e2d3'));
      b.box(x + w * 0.2, y + 1.05, z + d * 0.55, w * 0.6, 0.06, 0.62, accent, { emissive: 0.4 });
      b.box(x + inset, y, z + inset, 1.2, 0.45, 0.6, hex('#4a6073'));
      b.box(x + inset, y + 0.45, z + inset, 1.2, 0.45, 0.14, hex('#4a6073'));
      break;
    }
    case 'warehouse': {
      const rows = Math.max(1, Math.floor((w - 0.6) / 1.5));
      for (let i = 0; i < rows; i++) {
        const rx = x + 0.4 + i * ((w - 0.8) / rows);
        for (let lvl = 0; lvl < 3; lvl++) {
          b.box(rx, y + 0.3 + lvl * 0.8, z + 0.5, 0.9, 0.06, d - 1.0, hex('#8aa0b3'));
          b.box(rx + 0.1, y + 0.36 + lvl * 0.8, z + 0.7 + (lvl % 2) * 0.2, 0.3, 0.4, 0.4, hex('#c79b62'));
        }
        b.box(rx, y, z + 0.5, 0.06, 2.5, 0.06, DARK);
        b.box(rx + 0.84, y, z + 0.5, 0.06, 2.5, 0.06, DARK);
      }
      break;
    }
    case 'workshop': {
      b.box(x + w * 0.1, y + 0.8, z + d * 0.3, w * 0.8, 0.1, 0.8, hex('#9aa5b1'));
      b.box(x + w * 0.1, y, z + d * 0.3 + 0.1, 0.1, 0.8, 0.6, DARK);
      b.box(x + w * 0.9 - 0.1, y, z + d * 0.3 + 0.1, 0.1, 0.8, 0.6, DARK);
      b.box(x + 0.4, y + 0.9, z + 0.12, w - 0.8, 1.4, 0.06, hex('#6aa7ff'), { emissive: 0.7, shade: false });
      break;
    }
    case 'lounge': {
      b.box(x + 0.4, y + 0.3, z + d * 0.55, w * 0.5, 0.4, 0.8, hex('#5d8f78'));
      b.box(x + 0.4, y + 0.7, z + d * 0.55 + 0.7, w * 0.5, 0.5, 0.14, hex('#5d8f78'));
      b.prism(x + w - 0.6, y, z + 0.6, 0.22, 0.18, 0.45, 6, hex('#8a6a4a'));
      b.blob(x + w - 0.6, y + 0.85, z + 0.6, 0.4, 0.45, 0.4, hex('#3f8a55'), 6);
      break;
    }
    default: {
      b.prism(x + 0.45, y, z + d - 0.45, 0.2, 0.17, 0.4, 6, hex('#8a6a4a'));
      b.blob(x + 0.45, y + 0.78, z + d - 0.45, 0.34, 0.4, 0.34, hex('#3f8a55'), 6);
    }
  }
}

export function buildBuildingScene(floors: readonly SceneFloor[], options: BuildingSceneOptions): BuildingScene {
  const opaque = new MeshBuilder();
  const glass = new MeshBuilder();
  const rooms: BuildingRoomHandle[] = [];
  const desks: BuildingDeskHandle[] = [];
  const active = Math.max(0, Math.min(floors.length - 1, options.activeIndex));
  const last = options.showAll ? floors.length - 1 : active;
  // One footprint for the whole building so the floors stack as one volume.
  const all = floors.flatMap((f) => f.rooms);
  const fx0 = all.length ? Math.min(...all.map((r) => r.x)) - 1.2 : -8;
  const fz0 = all.length ? Math.min(...all.map((r) => r.z)) - 1.2 : -6;
  const fx1 = all.length ? Math.max(...all.map((r) => r.x + r.width)) + 1.2 : 8;
  const fz1 = all.length ? Math.max(...all.map((r) => r.z + r.depth)) + 1.2 : 6;

  for (let fi = 0; fi <= last && fi < floors.length; fi++) {
    const floor = floors[fi];
    const y = fi * FLOOR_PITCH;
    const isActive = fi === active;
    const ghost = !isActive;
    // Slab and plinth edge in the accent of the building.
    opaque.box(fx0, y - SLAB, fz0, fx1 - fx0, SLAB, fz1 - fz0, ghost ? hex('#8d98a3') : hex('#b7c0c8'));
    opaque.box(fx0 - 0.15, y - SLAB, fz0 - 0.15, fx1 - fx0 + 0.3, 0.08, 0.15, hex('#45d0b5'), { emissive: 0.5, shade: false });
    for (const room of floor.rooms) {
      const accent = ACCENT_BY_KIND[room.kind] ?? ACCENT_BY_KIND.office;
      const selected = options.selectedRoomId === room.id;
      const live = options.meetingRoomIds?.has(room.id) === true;
      const tile = FLOOR_BY_KIND[room.kind] ?? FLOOR_BY_KIND.office;
      const color = selected ? mix(tile, accent, 0.35) : ghost ? scale(tile, 0.85) : tile;
      opaque.box(room.x, y, room.z, room.width, 0.06, room.depth, color, { skip: ['bottom'], shade: false, emissive: ghost ? 0.05 : selected ? 0.55 : 0.3 });
      // Accent strip along the front edge: it makes each zone read at a glance.
      opaque.box(room.x, y + 0.06, room.z + room.depth - 0.08, room.width, 0.04, 0.08, accent, { emissive: selected ? 1 : 0.45, shade: false });
      // Walls: solid base to waist height, glass above, a top rail. Open at the doorway in the middle of the south wall.
      const wall = (x: number, z: number, w: number, d: number) => {
        opaque.box(x, y + 0.06, z, w, 0.9, d, hex('#dfe5e8'), { emissive: 0.12 });
        glass.box(x, y + 0.96, z, w, WALL_HEIGHT - 0.96, d, hex('#9fd4e6'), { shade: false });
        opaque.box(x, y + WALL_HEIGHT, z, w, 0.06, d, accent, { emissive: selected ? 0.9 : 0.35, shade: false });
      };
      const t = 0.1;
      wall(room.x, room.z, room.width, t);
      wall(room.x, room.z, t, room.depth);
      wall(room.x + room.width - t, room.z, t, room.depth);
      const door = Math.min(1.2, room.width * 0.3);
      const left = (room.width - door) / 2;
      wall(room.x, room.z + room.depth - t, left, t);
      wall(room.x + left + door, room.z + room.depth - t, room.width - left - door, t);
      if (!ghost) {
        furnish(opaque, room, y + 0.06, accent, live);
        const placed = floor.desks.filter((dk) => dk.roomId === room.id);
        const spacing = placed.length > 1 ? Math.min(room.width / (Math.min(5, Math.ceil(Math.sqrt(placed.length))) + 1), 1.6) : 1.3;
        for (const dk of placed) {
          const state: DeskVisual = options.deskState?.get(dk.id) ?? (dk.agentId ? 'assigned' : 'vacant');
          desk(opaque, dk, y + 0.06, spacing, state);
          desks.push({ id: dk.id, roomId: room.id, floorIndex: fi, anchor: { x: dk.position.x, y: y + 1.55, z: dk.position.z } });
        }
        rooms.push({
          id: room.id, floorIndex: fi,
          box: { min: { x: room.x, y, z: room.z }, max: { x: room.x + room.width, y: y + WALL_HEIGHT, z: room.z + room.depth } },
          anchor: { x: room.x + room.width / 2, y: y + WALL_HEIGHT + 0.5, z: room.z + room.depth * 0.45 },
        });
      }
    }
  }
  // Ground apron and a base so the building does not float.
  const pad = 3;
  opaque.box(fx0 - pad, -0.9, fz0 - pad, fx1 - fx0 + pad * 2, 0.6, fz1 - fz0 + pad * 2, hex('#2b3a46'));
  opaque.box(fx0 - pad, -0.31, fz0 - pad, fx1 - fx0 + pad * 2, 0.02, fz1 - fz0 + pad * 2, hex('#33485a'), { skip: ['bottom', 'north', 'south', 'east', 'west'], shade: false });
  const ob = freeze(opaque), gb = freeze(glass);
  const bounds: Aabb = {
    min: { x: fx0 - pad, y: -0.9, z: fz0 - pad },
    max: { x: fx1 + pad, y: last * FLOOR_PITCH + WALL_HEIGHT + 1, z: fz1 + pad },
  };
  return {
    opaque: ob, glass: gb, rooms, desks, bounds,
    center: { x: (fx0 + fx1) / 2, y: (active * FLOOR_PITCH) / 2, z: (fz0 + fz1) / 2 },
    triangles: ob.triangles + gb.triangles,
  };
}

/** Camera target and distance that frame the active floor of a built scene. */
export function frameBuilding(scene: BuildingScene, activeIndex: number): { target: Vec3; distance: number } {
  const width = scene.bounds.max.x - scene.bounds.min.x;
  const depth = scene.bounds.max.z - scene.bounds.min.z;
  return {
    target: { x: scene.center.x, y: activeIndex * FLOOR_PITCH * 0.6, z: scene.center.z },
    distance: Math.min(220, Math.max(16, Math.max(width, depth) * 1.05)),
  };
}
