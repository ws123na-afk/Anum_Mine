/**
 * The structure that makes the building view read as a building standing on the ground (docs/ANUM_MINE_HQ_CUTAWAY.md#structure):
 * a stone podium the ground floor sits on with entrance steps down to the site, a soft contact shadow where it meets the ground,
 * and between the floors the columns, ring beams and terrace roofs that carry every slab. It is architecture only: nothing here
 * comes from or implies data. Pure geometry on `MeshBuilder`, so the unit runner checks that every slab is carried.
 */
import { MAT, MeshBuilder, VERTEX_FLOATS, hex, scale, type Rgb } from './hqGeometry.ts';

/** Height of the podium: the ground floor is at y = 0 on top of it and the site (lawn, paths, car park) is at y = -PLINTH. */
export const PLINTH = 0.6;
/** How far the podium reaches past the facade at the sides and the back, and at the front (the entrance terrace). */
export const PODIUM_EDGE = 1.2;
export const PODIUM_FRONT = 1.8;
/** Entrance steps: risers of PLINTH / STEPS, treads of TREAD metres, down from the front of the podium. */
export const STEPS = 4;
export const TREAD = 0.35;
/** Square columns and the downstand beam under each slab edge. */
export const COLUMN = 0.4;
export const BEAM = 0.35;
/** Largest gap between two columns along a slab edge. */
const SPAN = 7.5;

export type Rect = Readonly<{ x0: number; z0: number; x1: number; z1: number }>;
export type Column = Readonly<{ floorIndex: number; x: number; z: number; y0: number; y1: number }>;
/** What carries what: read by the tests, and by anything that wants the ground or the podium (labels, walkers). */
export type StructureFacts = Readonly<{
  /** The site level (lawn, apron, car park). */
  ground: number;
  /** The podium: its plan and its bottom (on the ground) and top (the ground floor level). */
  plinth: Rect & Readonly<{ y0: number; y1: number }>;
  /** The entrance steps: their plan, down from the podium front to the ground. */
  steps: Rect & Readonly<{ y0: number; y1: number }>;
  /** Each drawn floor's slab: plan, underside and top. */
  slabs: readonly (Rect & Readonly<{ floorIndex: number; y0: number; y1: number }>)[];
  /** Every column drawn: it stands on its floor (y0) and carries the slab above (y1). */
  columns: readonly Column[];
  /** The service core of each floor that has one: it runs from the floor to the underside of the slab above. */
  cores: readonly (Rect & Readonly<{ floorIndex: number; y0: number; y1: number }>)[];
}>;

export const union = (a: Rect, b: Rect): Rect => ({ x0: Math.min(a.x0, b.x0), z0: Math.min(a.z0, b.z0), x1: Math.max(a.x1, b.x1), z1: Math.max(a.z1, b.z1) });

/**
 * The slab of each floor: its own plan grown to hold every floor above it, so a floor never overhangs the one under it (a larger
 * upper floor makes the lower ones larger; a smaller one sits on the floor below with a terrace round it).
 */
export function slabPlans(plans: readonly Rect[]): Rect[] {
  const out: Rect[] = plans.map((p) => ({ ...p }));
  for (let i = out.length - 2; i >= 0; i--) out[i] = union(out[i], out[i + 1]);
  return out;
}

/** The parts of `outer` that `inner` (inside it) does not cover: up to four strips. */
export function ringOf(outer: Rect, inner: Rect | null): Rect[] {
  if (!inner) return [outer];
  const out: Rect[] = [
    { x0: outer.x0, z0: outer.z0, x1: outer.x1, z1: inner.z0 },
    { x0: outer.x0, z0: inner.z1, x1: outer.x1, z1: outer.z1 },
    { x0: outer.x0, z0: inner.z0, x1: inner.x0, z1: inner.z1 },
    { x0: inner.x1, z0: inner.z0, x1: outer.x1, z1: inner.z1 },
  ];
  return out.filter((r) => r.x1 - r.x0 > 0.05 && r.z1 - r.z0 > 0.05);
}

/** Points along a rectangle's edge: the corners and enough between them that no gap is longer than SPAN. */
function edgePoints(r: Rect): Array<[number, number]> {
  const pts: Array<[number, number]> = [];
  const h = COLUMN / 2;
  const along = (a: number, b: number) => { const n = Math.max(1, Math.ceil((b - a) / SPAN)); return Array.from({ length: n + 1 }, (_, i) => a + ((b - a) * i) / n); };
  const xs = along(r.x0 + h, r.x1 - h), zs = along(r.z0 + h, r.z1 - h);
  for (const x of xs) { pts.push([x, r.z0 + h], [x, r.z1 - h]); }
  for (const z of zs.slice(1, -1)) { pts.push([r.x0 + h, z], [r.x1 - h, z]); }
  return pts;
}

/**
 * The columns of one floor: on its own slab edge (they carry the outer walls and the terrace roof) and under the corners of the
 * slab above where that slab is smaller, so every corner of every slab stands on a column. They run from the floor (`y`) to the
 * underside of the slab above (`top`).
 */
export function columnsOf(floorIndex: number, slab: Rect, above: Rect | null, y: number, top: number): Column[] {
  const pts = edgePoints(slab);
  if (above) {
    const h = COLUMN / 2;
    for (const [x, z] of [[above.x0 + h, above.z0 + h], [above.x1 - h, above.z0 + h], [above.x0 + h, above.z1 - h], [above.x1 - h, above.z1 - h]] as const) pts.push([x, z]);
  }
  const out: Column[] = [];
  for (const [x, z] of pts) if (!out.some((c) => Math.abs(c.x - x) < 0.6 && Math.abs(c.z - z) < 0.6)) out.push({ floorIndex, x, z, y0: y, y1: top });
  return out;
}

const CONCRETE = hex('#d9d5cb');

/** Draws columns as concrete posts (no top or bottom face: they meet a slab at both ends). */
export function drawColumns(b: MeshBuilder, cols: readonly Column[], tone: Rgb = CONCRETE): void {
  b.withMat(MAT.concrete, () => {
    for (const c of cols) b.box(c.x - COLUMN / 2, c.y0, c.z - COLUMN / 2, COLUMN, c.y1 - c.y0, COLUMN, tone, { skip: ['top', 'bottom'], grad: 0.12 });
  });
}

/** The downstand beam under a slab's edges (`y` is the slab underside): it frames each floor like the section of a real frame. */
export function drawRingBeam(b: MeshBuilder, r: Rect, y: number, tone: Rgb = CONCRETE): void {
  const w = 0.3;
  b.withMat(MAT.concrete, () => {
    b.box(r.x0, y - BEAM, r.z0, r.x1 - r.x0, BEAM, w, tone, { skip: ['top'] });
    b.box(r.x0, y - BEAM, r.z1 - w, r.x1 - r.x0, BEAM, w, tone, { skip: ['top'] });
    b.box(r.x0, y - BEAM, r.z0 + w, w, BEAM, r.z1 - r.z0 - 2 * w, tone, { skip: ['top'] });
    b.box(r.x1 - w, y - BEAM, r.z0 + w, w, BEAM, r.z1 - r.z0 - 2 * w, tone, { skip: ['top'] });
  });
}

/** The podium plan and the plan of the steps for a ground floor slab, entrance in the middle of the front (+z). */
export function podiumOf(slab: Rect, entranceX: number): Readonly<{ plinth: Rect; steps: Rect }> {
  const plinth = { x0: slab.x0 - PODIUM_EDGE, z0: slab.z0 - PODIUM_EDGE, x1: slab.x1 + PODIUM_EDGE, z1: slab.z1 + PODIUM_FRONT };
  const half = Math.min(2.4, (slab.x1 - slab.x0) / 4);
  return { plinth, steps: { x0: entranceX - half, z0: plinth.z1, x1: entranceX + half, z1: plinth.z1 + (STEPS - 1) * TREAD } };
}

const PODIUM = hex('#cfc8b8');
const COPING = hex('#e9e4d8');
const SKIRT = hex('#8f887a');

/**
 * The podium: a stone base from the ground to the ground floor level with a dark skirting course and a pale coping, the entrance
 * steps with their cheek walls, and a contact shadow on the ground round its foot (a band that darkens towards the wall).
 */
export function drawPodium(b: MeshBuilder, plinth: Rect, steps: Rect, ground: Rgb): void {
  const w = plinth.x1 - plinth.x0, d = plinth.z1 - plinth.z0;
  b.withMat(MAT.concrete, () => {
    b.box(plinth.x0, -PLINTH, plinth.z0, w, PLINTH - 0.02, d, PODIUM, { grad: 0.22, skip: ['bottom'] });
    b.box(plinth.x0 - 0.03, -PLINTH, plinth.z0 - 0.03, w + 0.06, 0.12, d + 0.06, SKIRT, { skip: ['top', 'bottom'] });
    b.box(plinth.x0 - 0.04, -0.07, plinth.z0 - 0.04, w + 0.08, 0.05, d + 0.08, COPING, { skip: ['bottom'] });
  });
  // Steps: each tread a riser lower, down to the ground.
  const sw = steps.x1 - steps.x0;
  b.withMat(MAT.tile, () => {
    for (let k = 1; k < STEPS; k++) {
      const top = -k * (PLINTH / STEPS);
      b.box(steps.x0, -PLINTH, steps.z0 + (k - 1) * TREAD, sw, top + PLINTH, TREAD, k % 2 ? hex('#e3ddd0') : hex('#d8d1c2'), { skip: ['bottom'], grad: 0.1 });
    }
  });
  b.withMat(MAT.concrete, () => {
    for (const x of [steps.x0 - 0.3, steps.x1]) b.box(x, -PLINTH, steps.z0 - 0.02, 0.3, PLINTH + 0.05, steps.z1 - steps.z0 + 0.02, PODIUM, { grad: 0.2, skip: ['bottom'] });
  });
  // Contact shadow: dark at the foot of the podium and the steps, fading to the ground colour 0.9 m out.
  const y = -PLINTH + 0.006, s = 0.9, dark = scale(ground, 0.42);
  const band = (ax: number, az: number, bx: number, bz: number, ox: number, oz: number) => {
    const p = [[ax, y, az], [ax + ox, y, az + oz], [bx + ox, y, bz + oz], [bx, y, bz]];
    const ux = p[1][0] - p[0][0], uz = p[1][2] - p[0][2], vx = p[2][0] - p[0][0], vz = p[2][2] - p[0][2];
    // Facing up: wound the way a box top is.
    if (uz * vx - ux * vz > 0) b.quadV(p[0], p[1], p[2], p[3], dark, ground, ground, dark);
    else b.quadV(p[0], p[3], p[2], p[1], dark, dark, ground, ground);
  };
  const { x0, z0, x1, z1 } = plinth;
  band(x0, z0, x1, z0, 0, -s);
  band(x1, z0, x1, z1, s, 0);
  band(x0, z1, x0, z0, -s, 0);
  band(x1, z1, steps.x1 + 0.3, z1, 0, s);
  band(steps.x0 - 0.3, z1, x0, z1, 0, s);
  band(steps.x1 + 0.3, steps.z1, steps.x0 - 0.3, steps.z1, 0, s);
}

/** Moves geometry built at y = 0 down by `dy` (vertices and halos): the site is drawn on the usual ground and set under the podium. */
export function lowered(b: MeshBuilder, dy: number): Readonly<{ vertices: Float32Array; halos: number[] }> {
  const v = b.toFloat32();
  for (let i = 1; i < v.length; i += VERTEX_FLOATS) v[i] -= dy;
  const halos = b.halos.slice();
  for (let i = 1; i < halos.length; i += 8) halos[i] -= dy;
  return { vertices: v, halos };
}
