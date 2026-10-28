/**
 * The plan impostor atlas: every storey of every planned building drawn top-down into one small RGBA texture, one tile per
 * storey (a row per building, a column per storey). The glass shader looks through a pane into this atlas (interior mapping),
 * so what shows behind a facade pane is the room that really sits there on that floor.
 *
 * Pixel format (RGBA8, nearest filtering):
 *   rgb  albedo of the surface seen from above (room floor by kind, corridor, desk top, screen, a seated figure, a wall)
 *   a    0 = outside the plan; otherwise bit 7 = partition wall, bits 0..6 = 1 + light * 126 (how lit that spot is)
 *
 * Data truth: a screen is bright only for a desk whose agent presence says working or in a meeting; a seated figure is a dot
 * only where the registry assigns an agent; a room is lit only when one of its desks is lit. Vacant, idle and unknown stay dim.
 * Pure and deterministic: the same plans give the same bytes, so the unit runner pins it and a revision label says when to redraw.
 */
import type { SceneFloor } from './headquartersProjection.ts';
import { turnRect, unturnXZ } from './campusFacing.ts';
import { PLAN_CORE, PLINTH, STOREY_PITCH, hash32, layoutKey, occupancyKey, type PlanFrame, type PlanInput } from './hqPlanFrame.ts';

export const MAX_PLAN_BUILDINGS = 16;
export const PLAN_TILE = 128;
export const PROBE_DEPTH = 2.8;

type Px = readonly [number, number, number];
const c = (r: number, g: number, b: number): Px => [r, g, b];

/** Floor albedo per room kind (the same family as the interior pipeline's floors). */
export const KIND_FLOOR: Readonly<Record<string, Px>> = {
  office: c(228, 220, 203), meeting: c(220, 207, 178), reception: c(232, 220, 198), workshop: c(195, 200, 200), warehouse: c(182, 184, 180),
  lounge: c(210, 220, 196), command: c(207, 211, 218),
};
const FALLBACK_FLOOR = c(224, 218, 204);
export const CORRIDOR = c(198, 196, 190);
export const LOBBY = c(227, 220, 203);
const WALL = c(238, 236, 230);
const WOOD = c(138, 106, 74);
const STEEL = c(90, 98, 108);
const SCREEN_ON = c(112, 190, 255);
const SCREEN_OFF = c(30, 36, 44);
const SHIRTS: readonly Px[] = [c(47, 111, 176), c(138, 63, 92), c(61, 138, 107), c(194, 122, 43), c(91, 75, 154), c(43, 138, 160), c(168, 69, 59), c(74, 96, 115)];

export const kindFloor = (kind: string): Px => KIND_FLOOR[kind] ?? FALLBACK_FLOOR;

/** Light levels: a vacant room, a room with somebody at work, the shared corridor. */
export const LIGHT = Object.freeze({ vacant: 0.26, occupied: 0.8, corridor: 0.32, screenOn: 1 });

export const encodeAlpha = (light: number, wall: boolean): number => (wall ? 128 : 0) + 1 + Math.round(Math.max(0, Math.min(1, light)) * 126);
export const decodeAlpha = (a: number): Readonly<{ outside: boolean; wall: boolean; light: number }> =>
  a === 0 ? { outside: true, wall: false, light: 0 } : { outside: false, wall: a >= 128, light: ((a & 127) - 1) / 126 };

/** `turn` is the building's quarter turns on the campus (`campusFacing.ts`); the tile stays in the building's own frame and turns with it. */
export type PlanEntry = Readonly<{ origin: Readonly<{ x: number; z: number }>; turn?: number; frame: PlanFrame; input: PlanInput }>;

export type GlassPlanBuilding = Readonly<{
  /** World rectangle the tile covers: x0, z0, x1, z1 (the plan union footprint on the ground, after the turn). */
  rect: readonly [number, number, number, number];
  /** Where the tile's middle stands, its quarter turns and its own size (width along the front, depth across it): the lookup's frame. */
  origin: readonly [number, number];
  turn: number;
  size: readonly [number, number];
  storeys: number;
  /** Height of floor 0 and the storey pitch. */
  baseY: number;
  pitch: number;
  layoutKey: string;
}>;

export type GlassPlanData = Readonly<{
  /** Changes when the layout or the occupancy of any building changes. */
  revision: string;
  /** Changes only with a layout (an `office.layout_changed` led to different rooms or desks). */
  layoutRevision: string;
  tile: number; cols: number; rows: number; width: number; height: number;
  data: Uint8Array;
  buildings: readonly GlassPlanBuilding[];
}>;

class Tile {
  private readonly data: Uint8Array;
  private readonly stride: number;
  private readonly ox: number;
  private readonly oy: number;
  readonly T: number;
  readonly frame: PlanFrame;
  constructor(data: Uint8Array, stride: number, ox: number, oy: number, T: number, frame: PlanFrame) {
    this.data = data; this.stride = stride; this.ox = ox; this.oy = oy; this.T = T; this.frame = frame;
  }
  /** Plan metres to a pixel column / row inside the tile. */
  px(x: number): number { return ((x - this.frame.rect.x0) / this.frame.w) * this.T; }
  pz(z: number): number { return ((z - this.frame.rect.z0) / this.frame.d) * this.T; }
  put(i: number, j: number, rgb: Px, a: number): void {
    if (i < 0 || j < 0 || i >= this.T || j >= this.T) return;
    const k = ((this.oy + j) * this.stride + this.ox + i) * 4;
    this.data[k] = rgb[0]; this.data[k + 1] = rgb[1]; this.data[k + 2] = rgb[2]; this.data[k + 3] = a;
  }
  /** Fills the plan rectangle (metres) with a colour; always at least one pixel so a thin feature survives. */
  rect(x0: number, z0: number, x1: number, z1: number, rgb: Px, a: number): void {
    const i0 = Math.floor(this.px(x0)), i1 = Math.max(i0 + 1, Math.ceil(this.px(x1)));
    const j0 = Math.floor(this.pz(z0)), j1 = Math.max(j0 + 1, Math.ceil(this.pz(z1)));
    for (let j = j0; j < j1; j++) for (let i = i0; i < i1; i++) this.put(i, j, rgb, a);
  }
  /** One pixel wide outline of a plan rectangle. */
  outline(x0: number, z0: number, x1: number, z1: number, rgb: Px, a: number, gap: Readonly<{ side: string; u: number; w: number }> | null): void {
    const i0 = Math.floor(this.px(x0)), i1 = Math.ceil(this.px(x1)) - 1, j0 = Math.floor(this.pz(z0)), j1 = Math.ceil(this.pz(z1)) - 1;
    const isGap = (side: string, along: number) => gap !== null && gap.side === side && Math.abs(along - gap.u) <= gap.w / 2;
    for (let i = i0; i <= i1; i++) {
      if (!isGap('north', i)) this.put(i, j0, rgb, a);
      if (!isGap('south', i)) this.put(i, j1, rgb, a);
    }
    for (let j = j0; j <= j1; j++) {
      if (!isGap('west', j)) this.put(i0, j, rgb, a);
      if (!isGap('east', j)) this.put(i1, j, rgb, a);
    }
  }
}

function shirtOf(id: string): Px {
  return SHIRTS[parseInt(hash32(id).slice(0, 4), 16) % SHIRTS.length];
}

function paintStorey(tile: Tile, floor: SceneFloor, rect: Readonly<{ x0: number; z0: number; x1: number; z1: number }>, core: Readonly<{ x: number; z: number }> | null, input: PlanInput): void {
  const wallA = encodeAlpha(LIGHT.corridor, true);
  // The slab: shared corridor floor.
  tile.rect(rect.x0, rect.z0, rect.x1, rect.z1, CORRIDOR, encodeAlpha(LIGHT.corridor, false));
  // The service core: lobby stone, the lift shaft (dark) and the stair well.
  if (core) {
    tile.rect(core.x, core.z, core.x + PLAN_CORE.w, core.z + PLAN_CORE.d, LOBBY, encodeAlpha(LIGHT.corridor, false));
    tile.rect(core.x + 1.9, core.z, core.x + PLAN_CORE.w, core.z + 4.0, STEEL, encodeAlpha(0.2, false));
    tile.outline(core.x, core.z, core.x + PLAN_CORE.w, core.z + PLAN_CORE.d, WALL, wallA, { side: 'west', u: tile.pz(core.z + PLAN_CORE.d - 1.5), w: tile.T * (2.4 / tile.frame.d) });
  }
  const deskById = new Map(floor.desks.map((d) => [d.id, d]));
  for (const room of floor.rooms) {
    const occupied = room.deskIds.some((id) => input.lit.has(id));
    const light = occupied ? LIGHT.occupied : LIGHT.vacant;
    const a = encodeAlpha(light, false);
    tile.rect(room.x, room.z, room.x + room.width, room.z + room.depth, kindFloor(room.kind), a);
    if (room.kind === 'meeting') tile.rect(room.x + room.width * 0.22, room.z + room.depth * 0.3, room.x + room.width * 0.78, room.z + room.depth * 0.7, WOOD, a);
    if (room.kind === 'command') {
      // The wall of screens on the north side: bright only while somebody in the room is at work.
      tile.rect(room.x + 0.5, room.z + 0.15, room.x + room.width - 0.5, room.z + 0.75, occupied ? SCREEN_ON : SCREEN_OFF, encodeAlpha(occupied ? LIGHT.screenOn : light, false));
    }
    for (const id of room.deskIds) {
      const desk = deskById.get(id);
      if (!desk) continue;
      const { x, z } = desk.position;
      tile.rect(x - 0.6, z - 0.35, x + 0.6, z + 0.35, WOOD, a);
      const on = input.lit.has(id);
      tile.rect(x - 0.3, z - 0.3, x + 0.3, z - 0.05, on ? SCREEN_ON : SCREEN_OFF, encodeAlpha(on ? LIGHT.screenOn : light, false));
      if (input.seated.has(id)) tile.rect(x - 0.25, z + 0.45, x + 0.25, z + 0.95, shirtOf(desk.agentId ?? id), encodeAlpha(on ? 0.9 : light, false));
    }
    // Partition walls: one pixel, with a gap where the door is (the south wall when no side is stored).
    const side = room.doorSide ?? 'south';
    const gapAlong = side === 'north' || side === 'south' ? tile.px(room.x + room.width / 2) : tile.pz(room.z + room.depth / 2);
    const metres = 1.5 * (tile.T / (side === 'north' || side === 'south' ? tile.frame.w : tile.frame.d));
    tile.outline(room.x, room.z, room.x + room.width, room.z + room.depth, WALL, encodeAlpha(light, true), { side, u: gapAlong, w: Math.max(2, metres) });
  }
}

/** Draws the atlas of up to 16 buildings (more are left out and keep the generic look). */
export function buildGlassPlan(entries: readonly PlanEntry[]): GlassPlanData {
  const list = entries.slice(0, MAX_PLAN_BUILDINGS);
  const cols = Math.max(1, ...list.map((e) => e.frame.storeys.length));
  const rows = Math.max(1, list.length);
  const tile = Math.max(32, Math.min(PLAN_TILE, Math.floor(2048 / Math.max(cols, rows))));
  const width = cols * tile, height = rows * tile;
  const data = new Uint8Array(width * height * 4);
  const buildings: GlassPlanBuilding[] = [];
  const layoutParts: string[] = [], occupancyParts: string[] = [];
  list.forEach((e, row) => {
    const { frame, input } = e;
    e.frame.storeys.forEach((s, col) => {
      const t = new Tile(data, width, col * tile, row * tile, tile, frame);
      paintStorey(t, input.floors[s.floorIndex], s.rect, s.core, input);
    });
    const key = layoutKey(input.floors);
    layoutParts.push(key);
    occupancyParts.push(occupancyKey(input));
    const turn = (((e.turn ?? 0) % 4) + 4) % 4;
    const world = turnRect({ x0: -frame.w / 2, z0: -frame.d / 2, x1: frame.w / 2, z1: frame.d / 2 }, turn, e.origin.x, e.origin.z);
    buildings.push({
      rect: [world.x0, world.z0, world.x1, world.z1], origin: [e.origin.x, e.origin.z], turn, size: [frame.w, frame.d],
      storeys: frame.storeys.length, baseY: PLINTH, pitch: STOREY_PITCH, layoutKey: key,
    });
  });
  const layoutRevision = hash32(layoutParts.join('/'));
  // Where each tile stands and how it is turned is part of the revision: the uniforms change with it even when the bytes do not.
  const placed = buildings.map((b) => `${b.origin[0].toFixed(2)},${b.origin[1].toFixed(2)},${b.turn}`).join('/');
  return { revision: hash32(`${layoutRevision}#${occupancyParts.join('/')}#${tile}#${placed}`), layoutRevision, tile, cols, rows, width, height, data, buildings };
}

export type PixelSample = Readonly<{ rgb: Px; outside: boolean; wall: boolean; light: number; storey: number }>;

/** One texel of a building's storey at a world position (what the shader's `hq_planAt` reads). */
export function samplePlan(plan: GlassPlanData, bi: number, storey: number, x: number, z: number): PixelSample {
  const b = plan.buildings[bi];
  // Back into the building's own frame (the shader's `hq_planAt` does the same), then across its tile.
  const [lx, lz] = unturnXZ(x - b.origin[0], z - b.origin[1], b.turn);
  const qx = lx / b.size[0] + 0.5, qz = lz / b.size[1] + 0.5;
  const T = plan.tile;
  const inside = qx >= 0 && qx <= 1 && qz >= 0 && qz <= 1;
  const i = Math.min(T - 1, Math.max(0, Math.floor(Math.min(0.997, Math.max(0.003, qx)) * T)));
  const j = Math.min(T - 1, Math.max(0, Math.floor(Math.min(0.997, Math.max(0.003, qz)) * T)));
  const k = ((bi * T + j) * plan.width + storey * T + i) * 4;
  const a = inside ? plan.data[k + 3] : 0;
  const d = decodeAlpha(a);
  return { rgb: [plan.data[k], plan.data[k + 1], plan.data[k + 2]], outside: d.outside, wall: d.wall, light: d.light, storey };
}

/** The storey a height falls in (clamped to the building). */
export function storeyAt(plan: GlassPlanData, bi: number, y: number): number {
  const b = plan.buildings[bi];
  return Math.max(0, Math.min(b.storeys - 1, Math.floor((y - b.baseY) / b.pitch)));
}

/**
 * The cheap lookup of the software path and the reference for the tests: the texel `PROBE_DEPTH` metres inside the facade,
 * straight behind a pane at (x, y, z) whose outward normal is (nx, nz). The hardware path starts from the same pane and
 * marches along the view ray instead; looking straight on, both read this texel.
 */
export function sampleBehindPane(plan: GlassPlanData, bi: number, x: number, y: number, z: number, nx: number, nz: number): PixelSample {
  return samplePlan(plan, bi, storeyAt(plan, bi, y), x - nx * PROBE_DEPTH, z - nz * PROBE_DEPTH);
}
