/**
 * Procedural building cutaway: floor slabs, rooms as zones, glazed partitions, desks and the
 * furniture that belongs to each room kind, with landscaping round the plinth. Geometry comes from
 * the persisted room geometry (`SceneFloor`), never from a template, so a saved layout always shows
 * as saved. Seated figures are generic (no likeness) and appear only at desks with an assignment;
 * the HTML agent card carries the name, role and status pill.
 */
import { MAT, MeshBuilder, hex, mix, scale, seeded, type ModelSlot, type Rgb } from './hqGeometry.ts';
import type { RoomGeometry, SceneDesk, SceneFloor, Vec3 } from './headquartersProjection.ts';
import type { SceneMesh } from './campusScene.ts';
import type { Aabb } from './hqMath.ts';
import { ASPHALT, STONE, TEAL, WARM, anumMark, bench, bush, cypress, flowerBed, hedge, lamp, lbox, palm, planter, roundTree } from './hqProps.ts';
import { signBoard } from './campusBuildings.ts';
import { car as envCar } from '../hqAssets/environment/index.ts';
import { signAtlasLayout } from './hqTextures.ts';
import { buildInteriorRoom, interiorKindFor, type StationSpec, type StationState } from '../hqAssets/interior/index.ts';
import { classifySurfaces } from './hqSurfaces.ts';
import { placementsFromSlots, type ModelGroup, type ModelPlacement } from './hqModels.ts';

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
  /** Sign cells: one, for the entrance sign on the front lawn (the stage draws the real building name there). */
  signCount: number;
  /** The region that casts and receives shadows. */
  shadow: Readonly<{ center: Vec3; radius: number }>;
  triangles: number;
  /** Where loadable models go (sofas, and a seated person only at a desk with a registry assignment). */
  models: readonly ModelPlacement[];
  /** The procedural pieces a loaded model replaces; the renderer draws one only while its group could not be loaded. */
  fallbacks: Readonly<Record<ModelGroup, SceneMesh>>;
  /** Soft light halos (x, y, z, radius, r, g, b, strength per halo) for the night glow. */
  halos: Float32Array;
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
const WALNUT = hex('#5a3c28');
const DARK = hex('#26313b');
const MARBLE = hex('#ece8df');
const SCREEN: Record<DeskVisual, { color: Rgb; emissive: number }> = {
  working: { color: hex('#58f0cf'), emissive: 1 },
  waiting: { color: hex('#ffc861'), emissive: 0.95 },
  idle: { color: hex('#6aa7ff'), emissive: 0.55 },
  unknown: { color: hex('#8b95a5'), emissive: 0.3 },
  assigned: { color: hex('#8b95a5'), emissive: 0.25 },
  vacant: { color: hex('#232c36'), emissive: 0 },
};

const STATION_OF: Readonly<Record<DeskVisual, StationState>> = {
  working: 'working', waiting: 'waiting', idle: 'idle', unknown: 'unknown', vacant: 'vacant', assigned: 'unknown',
};

const freeze = (b: MeshBuilder): SceneMesh => ({ vertices: b.toFloat32(), bounds: b.bounds(), triangles: b.triangleCount });

/** A soft contact shadow under furniture: a slightly larger, darker patch of the floor. */
function contact(b: MeshBuilder, x: number, z: number, w: number, d: number, y: number, floor: Rgb): void {
  b.plane(x - 0.1, y, z - 0.08, w + 0.2, d + 0.2, scale(floor, 0.62), 0.28);
}

function plant(b: MeshBuilder, x: number, y: number, z: number, s = 1, tone = 0): void {
  b.prism(x, y, z, 0.2 * s, 0.17 * s, 0.4 * s, 6, tone ? hex('#c9c2b2') : hex('#8a6a4a'));
  b.blob(x, y + 0.85 * s, z, 0.36 * s, 0.46 * s, 0.36 * s, hex('#3f8a55'), 6);
  b.blob(x + 0.12 * s, y + 1.1 * s, z - 0.06 * s, 0.24 * s, 0.3 * s, 0.24 * s, hex('#5fa862'), 5);
}

/** An office chair facing local +z of the given quarter turn. */
function chair(b: MeshBuilder, x: number, y: number, z: number, rot: number, color: Rgb, k = 1): void {
  lbox(b, x, z, rot, -0.21 * k, y + 0.42 * k, -0.2 * k, 0.42 * k, 0.07 * k, 0.42 * k, color);
  lbox(b, x, z, rot, -0.21 * k, y + 0.5 * k, -0.24 * k, 0.42 * k, 0.5 * k, 0.06 * k, scale(color, 0.9));
  b.prism(x, y, z, 0.05 * k, 0.04 * k, 0.42 * k, 5, DARK);
  b.disc(x, y + 0.02, z, 0.24 * k, 5, DARK);
}

function sofa(b: MeshBuilder, x: number, y: number, z: number, rot: number, w: number, color: Rgb): void {
  lbox(b, x, z, rot, -w / 2, y, -0.4, w, 0.3, 0.8, scale(color, 0.7));
  lbox(b, x, z, rot, -w / 2 + 0.06, y + 0.3, -0.34, w - 0.12, 0.14, 0.7, color, { grad: 0.1 });
  lbox(b, x, z, rot, -w / 2, y + 0.3, -0.4, w, 0.6, 0.18, scale(color, 0.85));
  lbox(b, x, z, rot, -w / 2, y + 0.3, -0.4, 0.16, 0.38, 0.8, scale(color, 0.8));
  lbox(b, x, z, rot, w / 2 - 0.16, y + 0.3, -0.4, 0.16, 0.38, 0.8, scale(color, 0.8));
}

function shelf(b: MeshBuilder, x: number, y: number, z: number, w: number, rot: number, rnd: () => number): void {
  lbox(b, x, z, rot, -w / 2, y, -0.18, w, 1.9, 0.36, hex('#6b5238'), { skip: ['north'] });
  const books = [hex('#c0504d'), hex('#4f81bd'), hex('#9bbb59'), hex('#f2c14e'), hex('#e8e4d8'), hex('#8064a2')];
  for (let lvl = 0; lvl < 4; lvl++) {
    lbox(b, x, z, rot, -w / 2 + 0.04, y + 0.28 + lvl * 0.44, -0.18, w - 0.08, 0.04, 0.34, hex('#8a6a4a'));
    let at = -w / 2 + 0.08;
    while (at < w / 2 - 0.2) {
      const bw = 0.07 + rnd() * 0.1;
      lbox(b, x, z, rot, at, y + 0.32 + lvl * 0.44, -0.16, bw, 0.28 + rnd() * 0.1, 0.24, books[Math.floor(rnd() * books.length)]);
      at += bw + 0.02;
    }
  }
}

function tyreStack(b: MeshBuilder, x: number, y: number, z: number, n: number): void {
  for (let i = 0; i < n; i++) {
    b.prism(x, y + i * 0.25, z, 0.42, 0.42, 0.24, 10, hex('#1b1f24'));
    b.prism(x, y + i * 0.25 + 0.241, z, 0.2, 0.2, 0.004, 8, hex('#9aa5b1'));
  }
}

function crate(b: MeshBuilder, x: number, y: number, z: number, w: number, h: number, d: number, color: Rgb): void {
  b.box(x, y, z, w, h, d, color, { grad: 0.12 });
  b.box(x + w * 0.45, y, z - 0.005, w * 0.1, h, d + 0.01, scale(color, 0.78));
}

function forklift(b: MeshBuilder, x: number, y: number, z: number, rot: number): void {
  const body = hex('#e8902b');
  lbox(b, x, z, rot, -0.45, y + 0.25, -0.7, 0.9, 0.55, 1.5, body, { grad: 0.12 });
  lbox(b, x, z, rot, -0.45, y + 0.8, -0.7, 0.9, 0.08, 0.9, DARK);
  for (const sx of [-0.45, 0.4]) lbox(b, x, z, rot, sx, y + 0.8, -0.7, 0.05, 0.85, 0.05, DARK);
  for (const sx of [-0.45, 0.4]) lbox(b, x, z, rot, sx, y + 0.8, 0.1, 0.05, 0.85, 0.05, DARK);
  lbox(b, x, z, rot, -0.45, y + 1.62, -0.7, 0.9, 0.05, 0.85, DARK);
  lbox(b, x, z, rot, -0.3, y + 0.15, 0.82, 0.6, 1.5, 0.08, DARK);
  lbox(b, x, z, rot, -0.34, y + 0.06, 0.9, 0.1, 0.06, 0.8, hex('#9aa5b1'));
  lbox(b, x, z, rot, 0.24, y + 0.06, 0.9, 0.1, 0.06, 0.8, hex('#9aa5b1'));
  lbox(b, x, z, rot, -0.2, y + 0.55, -0.65, 0.4, 0.04, 0.4, hex('#31465a'));
  for (const sx of [-0.5, 0.4]) for (const wz of [-0.5, 0.35]) lbox(b, x, z, rot, sx, y, wz, 0.12, 0.34, 0.34, hex('#161b21'));
}

const SHIRTS = ['#2f6fb0', '#8a3f5c', '#3d8a6b', '#c27a2b', '#5b4b9a', '#2b8aa0', '#a8453b', '#4a6073', '#e9ecef', '#1f2937'].map(hex);
const SKINS = ['#f0c7a0', '#d9a77e', '#b9825a', '#8a5a3b', '#e8b890'].map(hex);
const HAIRS = ['#1d1a18', '#3a2a1f', '#6a4a2a', '#222a33', '#7a5a3a'].map(hex);
const TROUSERS = ['#26313b', '#3a3f4a', '#4a4036', '#1f2a44'].map(hex);

/** A generic seated professional facing the desk (towards -z): no likeness, the same look for the same seed. */
function person(b: MeshBuilder, x: number, y: number, z: number, seed: string, k: number): void {
  const rnd = seeded(seed);
  const shirt = SHIRTS[Math.floor(rnd() * SHIRTS.length)], skin = SKINS[Math.floor(rnd() * SKINS.length)], hair = HAIRS[Math.floor(rnd() * HAIRS.length)];
  const trousers = TROUSERS[Math.floor(rnd() * TROUSERS.length)];
  const long = rnd() < 0.35;
  const u = 1.3 * k;
  const hipY = y + 0.5 * k;
  // Thighs forward under the desk, shins down, shoes.
  for (const sx of [-0.12, 0.06]) {
    b.box(x + sx * u, hipY, z - 0.5 * u, 0.12 * u, 0.14 * u, 0.62 * u, trousers);
    b.box(x + sx * u, y + 0.04, z - 0.52 * u, 0.12 * u, hipY - y - 0.04, 0.12 * u, trousers);
    b.box(x + sx * u - 0.01 * u, y, z - 0.6 * u, 0.14 * u, 0.06, 0.2 * u, hex('#14181d'));
  }
  // Torso, shoulders, arms reaching the keyboard.
  b.box(x - 0.2 * u, hipY + 0.14 * u, z - 0.08 * u, 0.4 * u, 0.58 * u, 0.24 * u, shirt, { grad: 0.18 });
  for (const sx of [-0.31, 0.19]) {
    b.box(x + sx * u, hipY + 0.34 * u, z - 0.06 * u, 0.12 * u, 0.4 * u, 0.14 * u, shirt);
    b.box(x + sx * u, hipY + 0.34 * u, z - 0.46 * u, 0.12 * u, 0.1 * u, 0.42 * u, shirt);
    b.box(x + sx * u, hipY + 0.34 * u, z - 0.54 * u, 0.12 * u, 0.1 * u, 0.1 * u, skin);
  }
  const neckY = hipY + 0.72 * u;
  b.box(x - 0.05 * u, neckY, z - 0.02 * u, 0.1 * u, 0.1 * u, 0.1 * u, skin);
  b.blob(x, neckY + 0.28 * u, z + 0.0 * u, 0.15 * u, 0.18 * u, 0.15 * u, skin, 8);
  b.blob(x, neckY + 0.36 * u, z + 0.03 * u, 0.165 * u, 0.12 * u, 0.165 * u, hair, 8);
  if (long) b.box(x - 0.15 * u, neckY - 0.18 * u, z + 0.04 * u, 0.3 * u, 0.4 * u, 0.08 * u, hair);
}

function desk(b: MeshBuilder, d: SceneDesk, y: number, spacing: number, state: DeskVisual, seed: string | undefined, floor: Rgb): void {
  // Desks are drawn larger than life so they read from the campus-height camera: a work surface, a monitor on a stand, a keyboard and a chair.
  const k = Math.max(0.7, Math.min(1.35, spacing / 1.05));
  const w = 1.5 * k, dep = 0.8 * k, x = d.position.x - w / 2, z = d.position.z - dep / 2;
  const h = 0.78 * k;
  contact(b, x - 0.1, z - 0.1, w + 0.2, dep + 1.7 * k, y + 0.012, floor);
  b.withMat(MAT.wood, () => b.box(x, y + h, z, w, 0.09 * k, dep, WOOD, { grad: 0.1 }));
  for (const [lx, lz] of [[0.06, 0.06], [w - 0.14, 0.06], [0.06, dep - 0.14], [w - 0.14, dep - 0.14]] as const) b.box(x + lx, y, z + lz, 0.08, h, 0.08, DARK);
  b.box(x + 0.04, y + h * 0.35, z + dep - 0.06, w - 0.08, h * 0.55, 0.04, scale(WOOD, 0.85));
  const screen = SCREEN[state];
  b.box(x + w * 0.46, y + h + 0.09 * k, z + dep * 0.18, w * 0.08, 0.16 * k, 0.08, DARK);
  b.box(x + w * 0.22, y + h + 0.25 * k, z + dep * 0.12, w * 0.56, 0.46 * k, 0.06, DARK);
  b.box(x + w * 0.25, y + h + 0.29 * k, z + dep * 0.12 + 0.05, w * 0.5, 0.38 * k, 0.03, screen.color, { emissive: screen.emissive, shade: false });
  b.box(x + w * 0.28, y + h + 0.09 * k, z + dep * 0.55, w * 0.44, 0.03 * k, dep * 0.22, hex('#aab4be'));
  b.box(x + w * 0.76, y + h + 0.09 * k, z + dep * 0.55, w * 0.08, 0.03 * k, dep * 0.16, hex('#aab4be'));
  // A mug, a paper stack and a task lamp.
  b.prism(x + w * 0.88, y + h + 0.09 * k, z + dep * 0.3, 0.05 * k, 0.045 * k, 0.1 * k, 6, hex('#e8e4d8'));
  b.box(x + w * 0.06, y + h + 0.09 * k, z + dep * 0.5, 0.2 * k, 0.03 * k, 0.28 * k, hex('#f2f0ea'));
  b.box(x + w * 0.08, y + h + 0.09 * k, z + dep * 0.1, 0.04 * k, 0.3 * k, 0.04 * k, DARK);
  b.box(x + w * 0.05, y + h + 0.38 * k, z + dep * 0.08, 0.16 * k, 0.04 * k, 0.08 * k, hex('#ffe9b8'), { emissive: 0.6, shade: false });
  // Chair behind the desk, towards +z, and the person in it.
  const cz = z + dep + 0.45 * k;
  chair(b, x + w * 0.5, y, cz, 2, hex('#31465a'), k);
  if (seed) person(b, x + w * 0.5, y, cz - 0.02 * k, seed, k);
  // A soft pool of light under the workstation.
  b.plane(x - 0.15, y + 0.016, z - 0.1, w + 0.3, dep + 1.5 * k, mix(hex('#ffffff'), screen.color, 0.25), 0.1);
}

function wallArt(b: MeshBuilder, x: number, y: number, z: number, w: number, tone: Rgb): void {
  b.box(x, y, z, w, 0.62, 0.04, hex('#1b2733'));
  b.box(x + 0.05, y + 0.05, z + 0.03, w - 0.1, 0.52, 0.02, tone, { emissive: 0.55, shade: false });
}

function furnish(b: MeshBuilder, room: RoomGeometry, y: number, accent: Rgb, live: boolean, floor: Rgb, rnd: () => number): void {
  const { x, z, width: w, depth: d } = room;
  const inset = 0.35;
  switch (room.kind) {
    case 'meeting': {
      const tw = Math.max(1.6, w * 0.55), td = Math.max(0.9, d * 0.3);
      const tx = x + (w - tw) / 2, tz = z + (d - td) / 2;
      contact(b, tx, tz, tw, td, y + 0.012, floor);
      b.box(tx, y + 0.72, tz, tw, 0.09, td, live ? mix(WALNUT, accent, 0.25) : WALNUT, { grad: 0.1 });
      b.box(tx + tw * 0.2, y, tz + td / 2 - 0.1, 0.2, 0.72, 0.2, DARK);
      b.box(tx + tw * 0.8 - 0.2, y, tz + td / 2 - 0.1, 0.2, 0.72, 0.2, DARK);
      const seats = Math.max(2, Math.min(8, Math.floor(tw / 0.7)));
      for (let i = 0; i < seats; i++) {
        const sx = tx + 0.35 + (i * (tw - 0.7)) / Math.max(1, seats - 1);
        chair(b, sx, y, tz - 0.34, 0, hex('#3a4a5a'));
        chair(b, sx, y, tz + td + 0.34, 2, hex('#3a4a5a'));
      }
      b.box(x + inset, y + 0.9, z + 0.12, w - inset * 2, 1.3, 0.06, hex('#10161d'));
      b.box(x + inset + 0.08, y + 0.98, z + 0.17, w - inset * 2 - 0.16, 1.14, 0.03, live ? hex('#58f0cf') : hex('#1b3a52'), { emissive: live ? 0.95 : 0.35, shade: false });
      b.box(x + inset, y, z + 0.14, w - inset * 2, 0.7, 0.4, hex('#d9d3c4'));
      plant(b, x + 0.5, y, z + d - 0.6);
      plant(b, x + w - 0.5, y, z + d - 0.6, 1, 1);
      break;
    }
    case 'reception': {
      // Brand wall behind the desk: dark walnut slats with the ANUM mark, then a marble desk with a return.
      b.box(x + w * 0.1, y, z + 0.1, w * 0.8, 2.6, 0.1, WALNUT, { grad: 0.1 });
      for (let i = 0; i < 9; i++) b.box(x + w * 0.1 + (i + 0.5) * (w * 0.8 / 9), y, z + 0.2, 0.03, 2.6, 0.03, scale(WALNUT, 0.7));
      anumMark(b, x + w / 2, y + 1.5, z + 0.22, 0.9, accent, 0.95);
      contact(b, x + w * 0.15, z + d * 0.5, w * 0.7, 0.9, y + 0.012, floor);
      b.box(x + w * 0.2, y, z + d * 0.55, w * 0.6, 1.05, 0.55, MARBLE, { grad: 0.1 });
      b.box(x + w * 0.2, y + 1.05, z + d * 0.55, w * 0.6, 0.06, 0.62, accent, { emissive: 0.4 });
      b.box(x + w * 0.2, y, z + d * 0.55 - 0.9, 0.55, 1.05, 0.95, MARBLE, { grad: 0.1 });
      b.box(x + w * 0.45, y + 1.11, z + d * 0.55 + 0.1, 0.5, 0.32, 0.04, DARK);
      b.box(x + w * 0.47, y + 1.14, z + d * 0.55 + 0.14, 0.46, 0.26, 0.02, hex('#58f0cf'), { emissive: 0.7, shade: false });
      // Waiting area: a sofa, an armchair and a coffee table on a rug.
      b.box(x + inset, y + 0.015, z + d - 1.9, Math.min(2.6, w * 0.45), 0.015, 1.5, mix(hex('#2f4b66'), accent, 0.2), { emissive: 0.4, skip: ['bottom'] });
      sofa(b, x + 1.4, y, z + d - 1.3, 0, 1.8, hex('#4a6073'));
      lbox(b, x + 1.4, z + d - 1.9, 0, -0.45, y, 0.0, 0.9, 0.3, 0.5, hex('#e8e4d8'));
      plant(b, x + w - 0.5, y, z + 0.6);
      plant(b, x + 0.5, y, z + 0.6, 1, 1);
      break;
    }
    case 'warehouse': {
      const rows = Math.max(1, Math.floor((w - 0.6) / 1.5));
      const crates = [hex('#c79b62'), hex('#b5834d'), hex('#d4ad78'), hex('#9a7a52')];
      for (let i = 0; i < rows; i++) {
        const rx = x + 0.4 + i * ((w - 0.8) / rows);
        contact(b, rx, z + 0.5, 0.9, d - 1.4, y + 0.012, floor);
        for (let lvl = 0; lvl < 3; lvl++) {
          b.box(rx, y + 0.3 + lvl * 0.8, z + 0.5, 0.9, 0.06, d - 1.4, hex('#e8902b'));
          for (let c = 0; c < 3; c++) {
            if (rnd() < 0.18) continue;
            crate(b, rx + 0.05 + (c % 2) * 0.05, y + 0.36 + lvl * 0.8, z + 0.6 + c * ((d - 1.8) / 3), 0.78, 0.38 + rnd() * 0.12, (d - 1.8) / 3 - 0.08, crates[Math.floor(rnd() * crates.length)]);
          }
        }
        b.box(rx, y, z + 0.5, 0.06, 2.5, 0.06, hex('#2f6fb0'));
        b.box(rx + 0.84, y, z + 0.5, 0.06, 2.5, 0.06, hex('#2f6fb0'));
        b.box(rx, y, z + d - 0.94, 0.06, 2.5, 0.06, hex('#2f6fb0'));
        b.box(rx + 0.84, y, z + d - 0.94, 0.06, 2.5, 0.06, hex('#2f6fb0'));
      }
      // Safety markings and a forklift in the aisle at the front.
      b.plane(x + 0.3, y + 0.02, z + d - 0.7, w - 0.6, 0.12, hex('#e8d36a'), 0.3);
      forklift(b, x + w * 0.5, y, z + d - 0.2, 0);
      break;
    }
    case 'workshop': {
      contact(b, x + w * 0.1, z + d * 0.3, w * 0.8, 0.9, y + 0.012, floor);
      b.box(x + w * 0.1, y + 0.8, z + d * 0.3, w * 0.8, 0.1, 0.8, hex('#9aa5b1'));
      b.box(x + w * 0.1, y, z + d * 0.3 + 0.1, 0.1, 0.8, 0.6, DARK);
      b.box(x + w * 0.9 - 0.1, y, z + d * 0.3 + 0.1, 0.1, 0.8, 0.6, DARK);
      b.box(x + w * 0.1, y + 0.3, z + d * 0.3 + 0.1, w * 0.8, 0.06, 0.6, DARK);
      for (let i = 0; i < 4; i++) b.box(x + w * 0.15 + i * (w * 0.2), y + 0.9, z + d * 0.3 + 0.25, 0.22, 0.14, 0.3, [hex('#e8902b'), hex('#c0504d'), hex('#6aa7ff'), hex('#9aa5b1')][i]);
      // Wall display and a tool board.
      b.box(x + 0.4, y + 0.9, z + 0.12, w - 0.8, 1.4, 0.06, hex('#10161d'));
      b.box(x + 0.48, y + 0.98, z + 0.17, w - 0.96, 1.24, 0.03, hex('#6aa7ff'), { emissive: 0.7, shade: false });
      for (let i = 0; i < 6; i++) b.box(x + 0.7 + i * ((w - 1.6) / 6), y + 1.0, z + 0.2, (w - 1.6) / 9, 0.3 + (i % 3) * 0.28, 0.02, hex('#c9f0ff'), { emissive: 0.8, shade: false });
      tyreStack(b, x + 0.7, y, z + d - 0.7, 4);
      tyreStack(b, x + 1.7, y, z + d - 0.7, 3);
      tyreStack(b, x + w - 0.8, y, z + d - 0.8, 5);
      break;
    }
    case 'lounge': {
      b.box(x + 0.35, y + 0.015, z + d * 0.25, w - 0.7, 0.015, d * 0.5, mix(hex('#5d8f78'), accent, 0.3), { emissive: 0.4, skip: ['bottom'] });
      sofa(b, x + w * 0.5, y, z + d - 0.6, 0, Math.min(2.4, w * 0.5), hex('#5d8f78'));
      sofa(b, x + 0.7, y, z + d * 0.5, 3, 1.5, hex('#4a6073'));
      lbox(b, x + w * 0.5, z + d * 0.55, 0, -0.5, y, -0.3, 1.0, 0.34, 0.6, hex('#d9d3c4'));
      shelf(b, x + w - 0.7, y, z + 0.5, 1.3, 0, rnd);
      plant(b, x + w - 0.5, y, z + d - 0.6);
      plant(b, x + 0.5, y, z + 0.6, 1.1);
      b.prism(x + 0.5, y, z + d - 0.6, 0.05, 0.05, 1.4, 5, DARK);
      b.blob(x + 0.5, y + 1.5, z + d - 0.6, 0.22, 0.18, 0.22, WARM, 6);
      break;
    }
    default: {
      // Office: a storage wall, a printer stand, a whiteboard, a water cooler and plants. The desks come from the registry.
      shelf(b, x + 0.9, y, z + 0.5, 1.5, 0, rnd);
      if (w >= 6) {
        b.box(x + w * 0.3, y + 1.0, z + 0.12, w * 0.24, 1.0, 0.05, hex('#f4f6f8'), { emissive: 0.35, shade: false });
        b.box(x + w * 0.58, y + 1.0, z + 0.12, w * 0.24, 1.0, 0.05, hex('#6aa7ff'), { emissive: 0.5, shade: false });
        wallArt(b, x + w - 1.3, y + 1.1, z + 0.12, 0.9, accent);
      }
      b.box(x + w - 0.9, y, z + 0.4, 0.6, 0.8, 0.5, hex('#d9d3c4'));
      b.box(x + w - 0.85, y + 0.8, z + 0.45, 0.5, 0.18, 0.4, hex('#9aa5b1'));
      b.box(x + w - 0.5, y, z + d - 0.7, 0.34, 1.0, 0.34, hex('#dfe6ea'));
      b.box(x + w - 0.46, y + 1.0, z + d - 0.66, 0.26, 0.3, 0.26, hex('#9fd4e6'), { emissive: 0.2 });
      plant(b, x + 0.45, y, z + d - 0.45);
      break;
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
  const rnd = seeded('anum-building-interior');
  const slots: ModelSlot[] = [];
  const fallback: Record<ModelGroup, MeshBuilder> = { figure: new MeshBuilder(), sofa: new MeshBuilder() };

  for (let fi = 0; fi <= last && fi < floors.length; fi++) {
    const floor = floors[fi];
    const y = fi * FLOOR_PITCH;
    const isActive = fi === active;
    const ghost = !isActive;
    const slabTone = ghost ? hex('#8d98a3') : hex('#b7c0c8');
    // Slab and plinth edge in the accent of the building.
    opaque.box(fx0, y - SLAB, fz0, fx1 - fx0, SLAB, fz1 - fz0, slabTone);
    opaque.box(fx0 - 0.15, y - SLAB, fz0 - 0.15, fx1 - fx0 + 0.3, 0.08, 0.15, hex('#45d0b5'), { emissive: 0.5, shade: false });
    if (!ghost) {
      // Corridor tiles: pale stone with a joint line every three metres.
      opaque.withMat(MAT.tile, () => opaque.plane(fx0, y + 0.004, fz0, fx1 - fx0, fz1 - fz0, mix(slabTone, hex('#d9dde0'), 0.55), 0.35));
      for (let gx = Math.ceil(fx0 / 3) * 3; gx < fx1; gx += 3) opaque.plane(gx, y + 0.008, fz0, 0.04, fz1 - fz0, hex('#98a2ab'), 0.3);
      for (let gz = Math.ceil(fz0 / 3) * 3; gz < fz1; gz += 3) opaque.plane(fx0, y + 0.008, gz, fx1 - fx0, 0.04, hex('#98a2ab'), 0.3);
    }
    for (const room of floor.rooms) {
      const accent = ACCENT_BY_KIND[room.kind] ?? ACCENT_BY_KIND.office;
      const selected = options.selectedRoomId === room.id;
      const live = options.meetingRoomIds?.has(room.id) === true;
      const tile = FLOOR_BY_KIND[room.kind] ?? FLOOR_BY_KIND.office;
      const color = selected ? mix(tile, accent, 0.5) : ghost ? scale(tile, 0.85) : mix(tile, accent, 0.3);
      opaque.withMat(room.kind === 'warehouse' || room.kind === 'workshop' ? MAT.concrete : room.kind === 'lounge' || room.kind === 'meeting' ? MAT.wood : MAT.tile, () => opaque.box(room.x, y, room.z, room.width, 0.06, room.depth, color, { skip: ['bottom'], shade: false, emissive: ghost ? 0.05 : selected ? 0.95 : 0.75 }));
      if (!ghost && room.width > 3 && room.depth > 3) {
        // A rug in the zone colour marks the working area of the room; tile joints make the floor read as a floor.
        opaque.box(room.x + 0.5, y + 0.06, room.z + 0.5, room.width - 1, 0.015, room.depth - 1.3, mix(tile, accent, selected ? 0.8 : 0.55), { skip: ['bottom'], shade: false, emissive: 0.5 });
        const joint = scale(color, 0.88);
        for (let gx = room.x + 1.5; gx < room.x + room.width - 0.2; gx += 1.5) opaque.plane(gx, y + 0.062, room.z, 0.03, room.depth, joint, 0.55);
        for (let gz = room.z + 1.5; gz < room.z + room.depth - 0.2; gz += 1.5) opaque.plane(room.x, y + 0.062, gz, room.width, 0.03, joint, 0.55);
      }
      // Accent strip along the front edge: it makes each zone read at a glance.
      opaque.box(room.x, y + 0.06, room.z + room.depth - 0.08, room.width, 0.04, 0.08, accent, { emissive: selected ? 1 : 0.45, shade: false });
      // Walls: solid base to waist height, glass above with slim mullions, a top rail. Open at the doorway in the middle of the south wall.
      const wall = (x: number, z: number, w: number, d: number) => {
        opaque.withMat(MAT.concrete, () => opaque.box(x, y + 0.06, z, w, 1.45, d, hex('#eef1f3'), { emissive: 0.22, grad: 0.2 }));
        opaque.box(x - 0.01, y + 0.06, z - 0.01, w + 0.02, 0.12, d + 0.02, hex('#c9d1d6'));
        glass.box(x, y + 1.51, z, w, WALL_HEIGHT - 1.51, d, hex('#9fd4e6'), { shade: false });
        opaque.box(x, y + WALL_HEIGHT, z, w, 0.06, d, accent, { emissive: selected ? 0.9 : 0.35, shade: false });
        if (!ghost) {
          const long = Math.max(w, d);
          for (let m = 0.9; m < long - 0.3; m += 1.4) {
            if (w > d) opaque.box(x + m, y + 1.51, z - 0.01, 0.05, WALL_HEIGHT - 1.51, d + 0.02, hex('#59656f'));
            else opaque.box(x - 0.01, y + 1.51, z + m, w + 0.02, WALL_HEIGHT - 1.51, 0.05, hex('#59656f'));
          }
        }
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
        if (room.width > 4.5) {
          wallArt(opaque, room.x + room.width * 0.12, y + 0.8, room.z + t, 0.8, mix(accent, hex('#ffffff'), 0.2));
          wallArt(opaque, room.x + room.width * 0.7, y + 0.8, room.z + t, 0.8, hex('#f2c14e'));
        }
        const placed = floor.desks.filter((dk) => dk.roomId === room.id);
        const interior = interiorKindFor(room.kind, room.label);
        if (interior) {
          // The furnished room comes from the interior assets. Desks are the registry's: one station each, and a
          // seated figure only where the registry (or presence) says someone is assigned to the desk.
          const stations: StationSpec[] = placed.map((dk) => ({
            id: dk.id, x: dk.position.x, z: dk.position.z, rot: 0,
            state: STATION_OF[options.deskState?.get(dk.id) ?? (dk.agentId ? 'assigned' : 'vacant')],
            occupantSeed: options.people?.get(dk.id) ?? null,
          }));
          const scratch = new MeshBuilder();
          buildInteriorRoom(scratch, interior, { x: room.x, y: y + 0.06, z: room.z, width: room.width, depth: room.depth }, { seed: room.id, accent, floor: color, live, detail: floors.length > 2 || all.length > 14 ? 'lite' : 'full', stations });
          const split = scratch.toFloat32Split(['figure', 'sofa']);
          opaque.append(classifySurfaces(split.rest));
          fallback.figure.append(classifySurfaces(split.byTag.get('figure')!));
          fallback.sofa.append(classifySurfaces(split.byTag.get('sofa')!));
          slots.push(...scratch.slots);
          opaque.halos.push(...scratch.halos);
        } else {
          furnish(opaque, room, y + 0.06, accent, live, color, rnd);
          const spacing = placed.length > 1 ? Math.min(room.width / (Math.min(5, Math.ceil(Math.sqrt(placed.length))) + 1), 1.6) : 1.3;
          for (const dk of placed) {
            const state: DeskVisual = options.deskState?.get(dk.id) ?? (dk.agentId ? 'assigned' : 'vacant');
            desk(opaque, dk, y + 0.06, spacing, state, options.people?.get(dk.id), color);
          }
        }
        for (const dk of placed) desks.push({ id: dk.id, roomId: room.id, floorIndex: fi, anchor: { x: dk.position.x, y: y + 1.55, z: dk.position.z } });
        rooms.push({
          id: room.id, floorIndex: fi,
          box: { min: { x: room.x, y, z: room.z }, max: { x: room.x + room.width, y: y + WALL_HEIGHT, z: room.z + room.depth } },
          anchor: { x: room.x + room.width / 2, y: y + WALL_HEIGHT + 0.5, z: room.z + room.depth * 0.45 },
        });
      }
    }
    if (!ghost) {
      // The building's outer skin on its two back sides (north and west): a solid base, glass above, posts and a roof-line rail.
      const skin = (x: number, z: number, w: number, d: number) => {
        opaque.box(x, y, z, w, 1.1, d, hex('#e6eaed'), { grad: 0.15, emissive: 0.15 });
        glass.box(x, y + 1.1, z, w, WALL_HEIGHT + 0.4 - 1.1, d, hex('#9fd4e6'), { shade: false });
        opaque.box(x, y + WALL_HEIGHT + 0.4, z, w, 0.1, d, hex('#59656f'));
        const long = Math.max(w, d);
        for (let m = 0; m <= long; m += 2.2) {
          if (w > d) opaque.box(Math.min(x + m, x + w - 0.08), y + 1.1, z, 0.08, WALL_HEIGHT + 0.4 - 1.1, d, hex('#3c4852'));
          else opaque.box(x, y + 1.1, Math.min(z + m, z + d - 0.08), w, WALL_HEIGHT + 0.4 - 1.1, 0.08, hex('#3c4852'));
        }
      };
      skin(fx0, fz0, fx1 - fx0, 0.2);
      skin(fx0, fz0, 0.2, fz1 - fz0);
      // The two near sides stay open to the camera: a low frameless glass balustrade with a brushed steel rail and posts.
      const rail = (x: number, z: number, w: number, d: number) => {
        glass.box(x, y, z, w, 1.05, d, hex('#9fd4e6'), { shade: false });
        opaque.withMat(MAT.metal, () => {
          opaque.box(x - 0.02, y + 1.05, z - 0.02, w + 0.04, 0.06, d + 0.04, hex('#aeb8c2'));
          const long = Math.max(w, d);
          for (let m = 0; m <= long; m += 2.4) {
            if (w > d) opaque.box(Math.min(x + m, x + w - 0.06), y, z - 0.02, 0.06, 1.05, d + 0.04, hex('#8d98a3'));
            else opaque.box(x - 0.02, y, Math.min(z + m, z + d - 0.06), w + 0.04, 1.05, 0.06, hex('#8d98a3'));
          }
        });
      };
      rail(fx0, fz1 - 0.1, fx1 - fx0, 0.05);
      rail(fx1 - 0.1, fz0, 0.05, fz1 - fz0);
      // Low planters along the front and the east edge.
      for (let px = fx0 + 1; px < fx1 - 0.8; px += 3.2) planter(opaque, px, fz1 - 0.35, 0.6, rnd);
      for (let pz = fz0 + 1; pz < fz1 - 0.8; pz += 3.2) planter(opaque, fx1 - 0.35, pz, 0.6, rnd);
    }
  }

  // The plinth stands in a lawn: stone apron, planting, a car park and an entrance sign wall.
  const pad = 3;
  const reach = 10;
  const lawn = hex('#4a7a48');
  opaque.box(fx0 - reach, -1.0, fz0 - reach, fx1 - fx0 + reach * 2, 0.97, fz1 - fz0 + reach * 2, hex('#3a3329'));
  opaque.withMat(MAT.grass, () => opaque.plane(fx0 - reach, -0.02, fz0 - reach, fx1 - fx0 + reach * 2, fz1 - fz0 + reach * 2, lawn, 0.07));
  opaque.withMat(MAT.tile, () => {
    opaque.plane(fx0 - 2.6, -0.015, fz0 - 2.6, fx1 - fx0 + 5.2, fz1 - fz0 + 5.2, scale(STONE, 0.95));
    opaque.plane(fx0 - 0.6, -0.01, fz0 - 0.6, fx1 - fx0 + 1.2, fz1 - fz0 + 1.2, scale(STONE, 0.7));
  });
  const land = seeded('anum-building-landscape');
  const cx = (fx0 + fx1) / 2;
  // Car park on the front lawn: asphalt, bay lines and a few parked cars.
  const bays = Math.max(3, Math.min(8, Math.floor((fx1 - fx0) / 3.4)));
  const lotX = cx - (bays * 3) / 2, lotZ = fz1 + 8.6;
  opaque.withMat(MAT.asphalt, () => opaque.plane(lotX - 0.4, -0.012, lotZ - 0.3, bays * 3 + 0.8, 5.4, ASPHALT));
  const palette = [hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#e8ebee'), hex('#1f2429')];
  for (let i = 0; i <= bays; i++) opaque.plane(lotX + i * 3 - 0.05, -0.008, lotZ, 0.1, 4.6, hex('#d8d8d0'));
  for (let i = 0; i < bays; i++) {
    if (land() < 0.35) continue;
    envCar(opaque, lotX + i * 3 + 1.5, lotZ + 2.3, { color: palette[Math.floor(land() * palette.length)], rot: land() < 0.5 ? 0 : Math.PI, detail: 'low', seed: 'bc' + i });
  }
  // Entrance sign wall: a low stone wall carrying the lettering board (the real building name over the wordmark).
  const signZ = fz1 + 3.0;
  opaque.withMat(MAT.concrete, () => opaque.box(cx - 3.4, 0, signZ - 0.1, 6.8, 0.9, 0.5, hex('#d7d2c4'), { grad: 0.1 }));
  signBoard(opaque, cx, 0.92, signZ - 0.05, 5.2, 1.3, signAtlasLayout(1).cell(0), TEAL);
  // Planting round the edges, clear of the car park and the sign.
  const spots = 34;
  for (let i = 0; i < spots; i++) {
    const side = Math.floor(land() * 4);
    const t = land();
    const off = 4.6 + land() * (reach - 5.8);
    const px = side === 0 ? fx0 + t * (fx1 - fx0) : side === 1 ? fx0 + t * (fx1 - fx0) : side === 2 ? fx0 - off : fx1 + off;
    const pz = side === 0 ? fz0 - off : side === 1 ? fz1 + off : fz0 + t * (fz1 - fz0);
    if (side === 1 && px > lotX - 2 && px < lotX + bays * 3 + 2) continue;
    const roll = land();
    if (roll < 0.35) palm(opaque, px, pz, 5 + land() * 2.4, land);
    else if (roll < 0.65) roundTree(opaque, px, pz, 3.4 + land() * 2, land);
    else if (roll < 0.8) cypress(opaque, px, pz, 3.6 + land() * 2.4, land);
    else bush(opaque, px, pz, 0.7 + land() * 0.5, land);
  }
  hedge(opaque, fx0 - 2.4, fz1 + 2.4, Math.max(2, cx - 3.6 - (fx0 - 2.4)), 0.7, 0.7);
  hedge(opaque, cx + 3.6, fz1 + 2.4, Math.max(2, fx1 + 2.4 - (cx + 3.6)), 0.7, 0.7);
  flowerBed(opaque, fx0 - 2.2, fz1 + 3.4, 3.2, 1.5, land);
  flowerBed(opaque, fx1 - 1.0, fz1 + 3.4, 3.2, 1.5, land);
  bench(opaque, fx0 + 3, fz1 + 1.8, 'x');
  bench(opaque, fx1 - 4, fz1 + 1.8, 'x');
  for (const [lx, lz] of [[fx0 - 2.4, fz0 - 2.4], [fx1 + 2.4, fz0 - 2.4], [fx0 - 2.4, fz1 + 2.4], [fx1 + 2.4, fz1 + 2.4]] as const) lamp(opaque, lx, lz);

  const ob = freeze(opaque), gb = freeze(glass);
  const bounds: Aabb = {
    min: { x: fx0 - pad, y: -0.9, z: fz0 - pad },
    max: { x: fx1 + pad, y: last * FLOOR_PITCH + WALL_HEIGHT + 1, z: fz1 + pad },
  };
  return {
    opaque: ob, glass: gb, rooms, desks, bounds,
    center: { x: (fx0 + fx1) / 2, y: (active * FLOOR_PITCH) / 2, z: (fz0 + fz1) / 2 },
    signCount: 1,
    shadow: { center: { x: cx, y: 0, z: (fz0 + fz1) / 2 }, radius: Math.hypot(fx1 - fx0 + reach * 2, fz1 - fz0 + reach * 2) * 0.5 },
    // Everything built, including the stand-ins (the renderer draws them only while a model could not be loaded).
    triangles: ob.triangles + gb.triangles + fallback.figure.triangleCount + fallback.sofa.triangleCount,
    models: placementsFromSlots(slots),
    fallbacks: { figure: freeze(fallback.figure), sofa: freeze(fallback.sofa) },
    halos: new Float32Array(opaque.halos),
  };
}

/** Camera target and distance that frame the active floor of a built scene. */
export function frameBuilding(scene: BuildingScene, activeIndex: number): { target: Vec3; distance: number } {
  const width = scene.bounds.max.x - scene.bounds.min.x;
  const depth = scene.bounds.max.z - scene.bounds.min.z;
  return {
    target: { x: scene.center.x, y: activeIndex * FLOOR_PITCH * 0.6, z: scene.center.z },
    distance: Math.min(260, Math.max(14, Math.max(width, depth) * 0.95)),
  };
}
