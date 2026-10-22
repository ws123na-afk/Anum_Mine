/**
 * Procedural building cutaway: floor slabs, rooms as zones, glazed partitions, desks and the
 * furniture that belongs to each room kind. Geometry comes from the persisted room geometry
 * (`SceneFloor`), never from a template, so a saved layout always shows as saved. People are not
 * drawn as characters: a desk shows its monitor state (working, waiting, idle, offline, vacant)
 * and the HTML agent card carries the name, role and status pill.
 */
import { MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
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
  /** Desks with someone assigned (registry fact), by desk id; the value seeds a generic figure so the same agent always looks the same. */
  people?: ReadonlyMap<string, string>;
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
  office: hex('#e4dccb'), meeting: hex('#dccfb2'), reception: hex('#e8dcc6'),
  workshop: hex('#c3c8c8'), warehouse: hex('#b6b8b4'), lounge: hex('#d2dcc4'),
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

function desk(b: MeshBuilder, d: SceneDesk, y: number, spacing: number, state: DeskVisual, seed: string | undefined): void {
  // Desks are drawn larger than life so they read from the campus-height camera: a work surface, a monitor on a stand, a keyboard and a chair.
  const k = Math.max(0.7, Math.min(1.35, spacing / 1.05));
  const w = 1.5 * k, dep = 0.8 * k, x = d.position.x - w / 2, z = d.position.z - dep / 2;
  const h = 0.78 * k;
  b.box(x, y + h, z, w, 0.09 * k, dep, WOOD, { grad: 0.1 });
  for (const [lx, lz] of [[0.06, 0.06], [w - 0.14, 0.06], [0.06, dep - 0.14], [w - 0.14, dep - 0.14]] as const) b.box(x + lx, y, z + lz, 0.08, h, 0.08, DARK);
  const screen = SCREEN[state];
  b.box(x + w * 0.46, y + h + 0.09 * k, z + dep * 0.18, w * 0.08, 0.16 * k, 0.08, DARK);
  b.box(x + w * 0.22, y + h + 0.25 * k, z + dep * 0.12, w * 0.56, 0.46 * k, 0.06, DARK);
  b.box(x + w * 0.25, y + h + 0.29 * k, z + dep * 0.12 + 0.05, w * 0.5, 0.38 * k, 0.03, screen.color, { emissive: screen.emissive, shade: false });
  b.box(x + w * 0.28, y + h + 0.09 * k, z + dep * 0.55, w * 0.44, 0.03 * k, dep * 0.22, hex('#aab4be'));
  // Chair behind the desk, towards +z, and the person in it.
  const cz = z + dep + 0.45 * k;
  b.box(x + w * 0.32, y + 0.42 * k, cz - 0.25 * k, w * 0.36, 0.08 * k, 0.5 * k, hex('#31465a'));
  b.box(x + w * 0.32, y + 0.5 * k, cz + 0.2 * k, w * 0.36, 0.62 * k, 0.07 * k, hex('#31465a'));
  b.prism(x + w * 0.5, y, cz, 0.06 * k, 0.05 * k, 0.42 * k, 5, DARK);
  if (seed) person(b, x + w * 0.5, y, cz - 0.02 * k, seed, k);
  // A soft pool of light under the workstation.
  b.plane(x - 0.15, y + 0.012, z - 0.1, w + 0.3, dep + 1.5 * k, mix(hex('#ffffff'), screen.color, 0.25), 0.1);
}

const SHIRTS = ['#2f6fb0', '#8a3f5c', '#3d8a6b', '#c27a2b', '#5b4b9a', '#2b8aa0', '#a8453b', '#4a6073'].map(hex);
const SKINS = ['#f0c7a0', '#d9a77e', '#b9825a', '#8a5a3b', '#e8b890'].map(hex);
const HAIRS = ['#1d1a18', '#3a2a1f', '#6a4a2a', '#222a33', '#7a5a3a'].map(hex);

/** A generic seated professional: no likeness, the same look for the same seed. */
function person(b: MeshBuilder, x: number, y: number, z: number, seed: string, k: number): void {
  const rnd = seeded(seed);
  const shirt = SHIRTS[Math.floor(rnd() * SHIRTS.length)], skin = SKINS[Math.floor(rnd() * SKINS.length)], hair = HAIRS[Math.floor(rnd() * HAIRS.length)];
  const u = 1.35 * k;
  b.box(x - 0.2 * u, y + 0.5 * k, z - 0.1 * u, 0.4 * u, 0.2 * k, 0.55 * u, hex('#26313b'));
  b.box(x - 0.2 * u, y + 0.68 * k, z - 0.04 * u, 0.4 * u, 0.58 * u, 0.24 * u, shirt, { grad: 0.18 });
  b.box(x - 0.32 * u, y + 0.78 * k, z - 0.02 * u, 0.12 * u, 0.4 * u, 0.18 * u, shirt);
  b.box(x + 0.2 * u, y + 0.78 * k, z - 0.02 * u, 0.12 * u, 0.4 * u, 0.18 * u, shirt);
  b.blob(x, y + 0.68 * k + 0.58 * u + 0.19 * u, z + 0.08 * u, 0.17 * u, 0.19 * u, 0.17 * u, skin, 8);
  b.blob(x, y + 0.68 * k + 0.58 * u + 0.27 * u, z + 0.1 * u, 0.18 * u, 0.13 * u, 0.18 * u, hair, 8);
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
      if (w >= 6) {
        b.box(x + w * 0.2, y + 1.0, z + 0.12, w * 0.26, 1.0, 0.05, hex('#6aa7ff'), { emissive: 0.5, shade: false });
        b.box(x + w * 0.54, y + 1.0, z + 0.12, w * 0.26, 1.0, 0.05, hex('#58f0cf'), { emissive: 0.4, shade: false });
      }
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
      const color = selected ? mix(tile, accent, 0.5) : ghost ? scale(tile, 0.85) : mix(tile, accent, 0.3);
      opaque.box(room.x, y, room.z, room.width, 0.06, room.depth, color, { skip: ['bottom'], shade: false, emissive: ghost ? 0.05 : selected ? 0.95 : 0.75 });
      if (!ghost && room.width > 3 && room.depth > 3) {
        // A rug in the zone colour marks the working area of the room.
        opaque.box(room.x + 0.5, y + 0.06, room.z + 0.5, room.width - 1, 0.015, room.depth - 1.3, mix(tile, accent, selected ? 0.8 : 0.55), { skip: ['bottom'], shade: false, emissive: 0.5 });
      }
      // Accent strip along the front edge: it makes each zone read at a glance.
      opaque.box(room.x, y + 0.06, room.z + room.depth - 0.08, room.width, 0.04, 0.08, accent, { emissive: selected ? 1 : 0.45, shade: false });
      // Walls: solid base to waist height, glass above, a top rail. Open at the doorway in the middle of the south wall.
      const wall = (x: number, z: number, w: number, d: number) => {
        opaque.box(x, y + 0.06, z, w, 1.45, d, hex('#eef1f3'), { emissive: 0.22, grad: 0.2 });
        glass.box(x, y + 1.51, z, w, WALL_HEIGHT - 1.51, d, hex('#9fd4e6'), { shade: false });
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
          desk(opaque, dk, y + 0.06, spacing, state, options.people?.get(dk.id));
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
    distance: Math.min(220, Math.max(14, Math.max(width, depth) * 0.9)),
  };
}
