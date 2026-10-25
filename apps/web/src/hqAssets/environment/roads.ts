/**
 * Roads, walkways and car parks. A ring road with kerbs, pavements, edge lines, a dashed centre
 * line and zebra crossings; paved walkways with tile joints; a car park with marked bays, end
 * islands, lamps and parked cars (seeded fill, never a crowd of anything else).
 *
 * Paths run clockwise on screen (x east, z south): positive offsets point inward on a ring.
 */
import { MeshBuilder, hex, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { ASPHALT, STONE } from '../../lib/hqProps.ts';
import { Frame, pick, scoped, type AssetResult, type GlowPoint } from './common.ts';
import { car, CAR_PALETTE } from './vehicles.ts';
import { lamp } from './furniture.ts';
import { crossMarks, dashedRibbon, lengths, ribbon, roundedRectPath, subPath, type P2 } from './paths.ts';

export const ROAD_Y = 0.02;
export const PAINT_Y = 0.03;
export const KERB_Y = 0.17;
export const WALK_Y = 0.15;
const LINE_WHITE = hex('#e4e4dc');
const LINE_YELLOW = hex('#e3c35a');
const KERB = hex('#c8c3b6');
const PAVER = hex('#cfc7b4');
const JOINT = hex('#aaa393');

export type RoadOptions = {
  width?: number; closed?: boolean; pavement?: boolean; y?: number;
  /** Arc-length ranges [from, to] where kerbs, pavements and edge lines are left out (junctions). */
  gaps?: ReadonlyArray<readonly [number, number]>;
  /** Zebra crossings at these arc lengths. */
  crossings?: readonly number[];
  /** Centre line: dashed (default), solid or none. */
  centre?: 'dashed' | 'solid' | 'none';
};
export const ROAD_TRIANGLES_PER_METRE_CAP = 6;

/** Pieces of [0, total] that are not inside a gap. */
function openSections(total: number, gaps: ReadonlyArray<readonly [number, number]>): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let cur = 0;
  for (const g of [...gaps].sort((p, q) => p[0] - q[0])) {
    if (g[0] > cur) out.push([cur, g[0]]);
    cur = Math.max(cur, g[1]);
  }
  if (cur < total) out.push([cur, total]);
  return out;
}

/**
 * A road along a path: asphalt, white edge lines, a centre line, kerbs on both sides (top and the
 * face that looks at the road), paved pavements with joints and optional zebra crossings.
 */
export function road(b: MeshBuilder, pts: readonly P2[], o: RoadOptions = {}): AssetResult {
  const w = o.width ?? 9, closed = o.closed ?? false, y = o.y ?? ROAD_Y, hw = w / 2;
  return scoped(b, [], (m) => {
    const { total } = lengths(pts, closed);
    ribbon(m, pts, closed, -hw, y, hw, y, scale(ASPHALT, 1.05));
    const py = y + (PAINT_Y - ROAD_Y);
    const pieces = (o.gaps && o.gaps.length) || !closed ? openSections(total, o.gaps ?? []).map(([s0, s1]) => subPath(pts, closed, s0, s1)) : [];
    const edge = (fn: (p: readonly P2[], c: boolean) => void) => {
      if (pieces.length) for (const p of pieces) { if (p.length >= 2) fn(p, false); } else fn(pts, closed);
    };
    edge((p, c) => { for (const side of [-1, 1]) ribbon(m, p, c, side * (hw - 0.55) - 0.07, py, side * (hw - 0.55) + 0.07, py, LINE_WHITE); });
    if ((o.centre ?? 'dashed') === 'dashed') dashedRibbon(m, pts, closed, 0, 0.07, py, 3, 4.5, LINE_YELLOW);
    else if (o.centre === 'solid') ribbon(m, pts, closed, -0.07, py, 0.07, py, LINE_YELLOW);
    const ky = y + (KERB_Y - ROAD_Y);
    edge((p, c) => {
      ribbon(m, p, c, -hw - 0.35, ky, -hw, ky, KERB);
      ribbon(m, p, c, hw, ky, hw + 0.35, ky, KERB);
      ribbon(m, p, c, -hw, y, -hw, ky, scale(KERB, 0.8), 'right');
      ribbon(m, p, c, hw, y, hw, ky, scale(KERB, 0.8), 'left');
      if (o.pavement !== false) {
        const wy = y + (WALK_Y - ROAD_Y);
        for (const side of [-1, 1]) {
          const a = side * (hw + 0.35), e = side * (hw + 0.35 + 2.4);
          ribbon(m, p, c, Math.min(a, e), wy, Math.max(a, e), wy, PAVER);
          crossMarks(m, p, c, Math.min(a, e), Math.max(a, e), wy + 0.004, 2.6, 0.06, JOINT);
        }
      }
    });
    for (const s of o.crossings ?? []) {
      const seg = subPath(pts, closed, s - 1.6, s + 1.6);
      for (let off = -hw + 0.9; off < hw - 0.6; off += 0.85) ribbon(m, seg, false, off - 0.28, py + 0.002, off + 0.28, py + 0.002, LINE_WHITE);
    }
  });
}

export type RingRoadOptions = {
  cx: number; cz: number; halfW: number; halfD: number;
  width?: number; cornerRadius?: number;
  /** Edges (0 right, 1 bottom, 2 left, 3 top) that carry a zebra crossing at their midpoint. */
  crosswalkEdges?: readonly number[];
  /** Edges that have a junction gap (no kerb, pavement or edge line) at their midpoint. */
  junctionEdges?: readonly number[];
  junctionHalfWidth?: number;
  pavement?: boolean;
};
export const RING_ROAD_TRIANGLES_CAP = 1100;

/** The path of a ring road: a clockwise rounded rectangle (positive offsets point inward). */
export function ringPath(o: Pick<RingRoadOptions, 'cx' | 'cz' | 'halfW' | 'halfD' | 'cornerRadius'>): P2[] {
  return roundedRectPath(o.cx, o.cz, o.halfW, o.halfD, o.cornerRadius ?? 14, 6);
}

/** Arc-length midpoints of the four straight edges of a ring built by `roundedRectPath` (right, bottom, left, top). */
export function ringEdgeMidpoints(path: readonly P2[]): number[] {
  const { at } = lengths(path, true);
  const edges: Array<{ s: number; len: number }> = [];
  for (let i = 0; i < path.length; i++) {
    const len = at[i + 1] - at[i];
    edges.push({ s: at[i] + len / 2, len });
  }
  const longest = [...edges].sort((a, c) => c.len - a.len).slice(0, 4).sort((a, c) => a.s - c.s);
  return longest.map((e) => e.s);
}

export function ringRoad(b: MeshBuilder, o: RingRoadOptions): AssetResult {
  const path = ringPath(o);
  const mids = ringEdgeMidpoints(path);
  const at = (e: number) => mids[((e % 4) + 4) % 4];
  const jw = o.junctionHalfWidth ?? 5;
  return road(b, path, {
    width: o.width ?? 9, closed: true, pavement: o.pavement,
    gaps: (o.junctionEdges ?? []).map((e) => [at(e) - jw, at(e) + jw] as const),
    crossings: (o.crosswalkEdges ?? []).map(at),
  });
}

export type WalkwayOptions = { width?: number; tile?: number; edge?: boolean; color?: Rgb; y?: number };
export const WALKWAY_TRIANGLES_PER_METRE_CAP = 2.2;

/** A paved walkway along an open or closed path with tile joints and slim edge kerbs. */
export function walkway(b: MeshBuilder, pts: readonly P2[], o: WalkwayOptions & { closed?: boolean } = {}): AssetResult {
  const w = o.width ?? 2.6, y = o.y ?? WALK_Y, closed = o.closed ?? false;
  return scoped(b, [], (m) => {
    const hw = w / 2;
    ribbon(m, pts, closed, -hw, y, hw, y, o.color ?? PAVER);
    crossMarks(m, pts, closed, -hw, hw, y + 0.004, o.tile ?? 2.4, 0.05, JOINT);
    if (o.edge !== false) {
      for (const side of [-1, 1]) {
        ribbon(m, pts, closed, side * hw, y, side * (hw + 0.14), y + 0.04, scale(STONE, 1.05));
      }
    }
  });
}

/** A zebra crossing: `bars` white strips across a road of width `roadWidth`, centred on (x, z), heading `rot`. */
export function crosswalk(b: MeshBuilder, x: number, z: number, o: { rot?: number; roadWidth?: number; length?: number } = {}): AssetResult {
  return scoped(b, [], (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const rw = o.roadWidth ?? 8, len = o.length ?? 3.2;
    for (let px = -rw / 2 + 0.7; px < rw / 2 - 0.4; px += 0.85) f.quadToward([px - 0.28, PAINT_Y, -len / 2], [px + 0.28, PAINT_Y, -len / 2], [px + 0.28, PAINT_Y, len / 2], [px - 0.28, PAINT_Y, len / 2], LINE_WHITE, [0, 1, 0]);
  });
}

// ---------------------------------------------------------------- car park

export type CarParkOptions = {
  /** Bays per side. */
  cols?: number;
  /** Fill ratio 0..1 (each bay holds a car with this probability). */
  fill?: number;
  seed?: string;
  rot?: number;
  /** Bays marked with teal (charging) and blue (accessible) paint, counted from the left of the first side. */
  chargingBays?: number;
  accessibleBays?: number;
  lamps?: boolean;
  detail?: 'full' | 'low';
};

export const BAY_W = 2.8;
export const BAY_D = 5.4;
export const AISLE = 6.4;

/** Footprint of a car park: width along x, depth along z (two bay rows and the aisle between). */
export function carParkSize(cols: number): { w: number; d: number } {
  return { w: cols * BAY_W + 1.0, d: BAY_D * 2 + AISLE + 1.0 };
}

/**
 * A car park laid out from (x, z) = its centre. Two rows of bays face a central aisle. Cars are
 * seeded: colour, fill, a small jitter in position and angle. Returns the lamp glow points too.
 */
export function carPark(b: MeshBuilder, x: number, z: number, o: CarParkOptions = {}): AssetResult {
  const cols = o.cols ?? 8;
  const rnd = seeded(o.seed ?? 'car-park');
  const fill = o.fill ?? 0.7;
  const glow: GlowPoint[] = [];
  const size = carParkSize(cols);
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const hw = size.w / 2, hd = size.d / 2;
    f.quadToward([-hw, ROAD_Y, -hd], [hw, ROAD_Y, -hd], [hw, ROAD_Y, hd], [-hw, ROAD_Y, hd], scale(ASPHALT, 1.08), [0, 1, 0]);
    // Edge kerb all round.
    f.box(-hw - 0.3, 0, -hd - 0.3, size.w + 0.6, KERB_Y, 0.3, KERB, { skip: ['bottom'] });
    f.box(-hw - 0.3, 0, hd, size.w + 0.6, KERB_Y, 0.3, KERB, { skip: ['bottom'] });
    f.box(-hw - 0.3, 0, -hd, 0.3, KERB_Y, size.d, KERB, { skip: ['bottom'] });
    f.box(hw, 0, -hd, 0.3, KERB_Y, size.d, KERB, { skip: ['bottom'] });
    const x0 = -hw + 0.5;
    const rows = [{ z0: -hd + 0.5, rot: 0 }, { z0: hd - 0.5 - BAY_D, rot: Math.PI }];
    const colors = CAR_PALETTE;
    const kinds = ['sedan', 'hatch', 'suv', 'sedan', 'hatch', 'coupe', 'pickup'] as const;
    rows.forEach((row, ri) => {
      for (let c = 0; c <= cols; c++) {
        const lx = x0 + c * BAY_W;
        f.quadToward([lx - 0.045, PAINT_Y, row.z0], [lx + 0.045, PAINT_Y, row.z0], [lx + 0.045, PAINT_Y, row.z0 + BAY_D], [lx - 0.045, PAINT_Y, row.z0 + BAY_D], LINE_WHITE, [0, 1, 0]);
      }
      // Stop line along the aisle side of each row.
      const zl = ri === 0 ? row.z0 + BAY_D : row.z0;
      f.quadToward([x0, PAINT_Y, zl - 0.04], [x0 + cols * BAY_W, PAINT_Y, zl - 0.04], [x0 + cols * BAY_W, PAINT_Y, zl + 0.04], [x0, PAINT_Y, zl + 0.04], LINE_WHITE, [0, 1, 0]);
      for (let c = 0; c < cols; c++) {
        const cx = x0 + (c + 0.5) * BAY_W;
        const special = ri === 0 && c < (o.accessibleBays ?? 0) + (o.chargingBays ?? 0);
        if (special) {
          const accessible = c < (o.accessibleBays ?? 0);
          const col = accessible ? hex('#2f6fb0') : hex('#2fb89c');
          f.quadToward([cx - 0.5, PAINT_Y + 0.002, row.z0 + 1.4], [cx + 0.5, PAINT_Y + 0.002, row.z0 + 1.4], [cx + 0.5, PAINT_Y + 0.002, row.z0 + 2.4], [cx - 0.5, PAINT_Y + 0.002, row.z0 + 2.4], col, [0, 1, 0], accessible ? 0 : 0.2);
        }
        // Wheel stop at the back of the bay.
        const zStop = ri === 0 ? row.z0 + 0.6 : row.z0 + BAY_D - 0.6;
        f.box(cx - 0.7, 0, zStop - 0.08, 1.4, 0.1, 0.16, hex('#9b9588'), { skip: ['bottom'] });
        if (rnd() > fill) continue;
        const jitter = (rnd() - 0.5) * 0.3;
        const cz = ri === 0 ? row.z0 + BAY_D / 2 + 0.1 : row.z0 + BAY_D / 2 - 0.1;
        const p = f.p(cx + jitter, 0, cz + (rnd() - 0.5) * 0.4);
        car(m, p[0], p[2], { kind: pick(rnd, kinds), color: pick(rnd, colors), rot: (o.rot ?? 0) + row.rot + (rnd() - 0.5) * 0.06 + (rnd() < 0.3 ? Math.PI : 0), detail: o.detail ?? 'low', seed: `${o.seed ?? 'lot'}-${ri}-${c}` });
      }
    });
    if (o.lamps !== false) {
      for (const sx of [-hw + 0.2, hw - 0.2]) {
        const q = f.p(sx, 0, 0);
        glow.push(...lamp(m, q[0], q[2], { kind: 'street', rot: o.rot ?? 0, height: 7 }).glow);
      }
    }
  });
}
