/**
 * Where each building's billboard stands and what it is made of (docs/ANUM_MINE_HQ_BILLBOARDS.md). Pure geometry, no DOM, no data:
 * the only lettering is the building name, which the stage draws into the building's own sign cell of the sign atlas (the board is
 * textured with that cell, so it reads "ANUM MINE" over the name, right to left in Arabic, like the sign on the facade).
 *
 * One merged mesh for the whole campus (134 triangles per board), deterministic: the same placements give the same boards.
 */
import { MAT, MeshBuilder, hex, type Rgb } from '../lib/hqGeometry.ts';
import type { Placement } from '../lib/campusModel.ts';
import type { Vec3 } from '../lib/headquartersProjection.ts';
import type { Aabb } from '../lib/hqMath.ts';

export type BoardSite = Readonly<{
  workspaceId: string;
  /** Foot of the board's centre on the ground. */
  x: number; z: number;
  /** Unit vector the board faces (away from its building). */
  nx: number; nz: number;
  width: number; height: number;
  /** Bottom edge of the panel above the ground. */
  clearance: number;
  /** Where the label card hangs: above the middle of the board. */
  anchor: Vec3;
  /** Box around the whole board, for picking. */
  box: Aabb;
}>;

export type SiteBuilding = Readonly<{ workspaceId: string; box: Aabb; door: Vec3; signIndex: number }>;
export type SiteContext = Readonly<{
  placements: readonly Placement[];
  buildings: readonly SiteBuilding[];
  monument: Vec3;
  extentRadius: number;
  /** Radius of the canal ring (the edge of the usable campus), or 0. */
  canalRadius: number;
  signCount: number;
  /** Extra round areas the boards keep out of (plaza, fountain), as x, z, radius. */
  avoid?: ReadonlyArray<readonly [number, number, number]>;
}>;

// A copy of `signAtlasLayout` (hqTextures.ts): importing that module would give it a chunk of its own and change how the shared
// chunks split; test/hqBillboard.test.ts keeps the copy equal to the original.
const CELL_W = 512, CELL_H = 128, COLS = 2;
const pow2 = (n: number) => { let p = 1; while (p < n) p *= 2; return p; };
export function signCell(count: number, index: number): { u0: number; u1: number; v0: number; v1: number } {
  const width = CELL_W * COLS, height = pow2(Math.max(1, Math.ceil(Math.max(1, count) / COLS)) * CELL_H);
  const col = index % COLS, row = Math.floor(index / COLS);
  return { u0: (col * CELL_W) / width, u1: ((col + 1) * CELL_W) / width, v1: 1 - (row * CELL_H) / height, v0: 1 - ((row + 1) * CELL_H) / height };
}

const PLAZA_CLEAR = 17;
const POST_COLOR = hex('#2b3946');
const PANEL_COLOR = hex('#0e1b26');
const TEAL = hex('#45d0b5');
const LAMP = hex('#ffe3a8');
const CONCRETE = hex('#8d939a');

const hash = (text: string): number => { let h = 2166136261; for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; };

function distToSegment(px: number, pz: number, x0: number, z0: number, x1: number, z1: number): number {
  const dx = x1 - x0, dz = z1 - z0, l2 = dx * dx + dz * dz;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x0) * dx + (pz - z0) * dz) / l2));
  return Math.hypot(px - (x0 + t * dx), pz - (z0 + t * dz));
}

/** Board width grows with the campus so it stays readable from the framing camera of a larger campus. */
export const boardWidth = (extentRadius: number): number => Math.max(9, Math.min(13, 7.5 + extentRadius * 0.15));

/** The direction a building faces: from its centre to its door, on the ground (the front is +z when the door sits straight ahead). */
function facing(p: Placement, door: Vec3): [number, number] {
  const dx = door.x - p.x, dz = door.z - p.z, len = Math.hypot(dx, dz);
  return len < 1e-3 ? [0, 1] : [dx / len, dz / len];
}

/** One site per building, clear of every building, walkway, the plaza and the other boards. */
export function boardSites(ctx: SiteContext): BoardSite[] {
  const width = boardWidth(ctx.extentRadius);
  const height = width / 4;
  const clearance = 3;
  const monument = ctx.monument;
  const walkTarget: [number, number] = [monument.x, monument.z + 10];
  const ring = ctx.extentRadius > 30 ? Math.max(26, ctx.extentRadius * 0.62) : 0;
  const limit = ctx.canalRadius > 0 ? ctx.canalRadius - 10 : Infinity;
  const sites: BoardSite[] = [];
  ctx.placements.forEach((p, index) => {
    const own = ctx.buildings[index];
    const [nx, nz] = facing(p, own.door);
    const rx = nz, rz = -nx;
    const side0 = hash(p.workspaceId) % 2 === 0 ? 1 : -1;
    const frontX = p.x + nx * (p.depth / 2), frontZ = p.z + nz * (p.depth / 2);
    // Candidates, best first: beside the walkway end in front of the garden, then nearer the facade, to either side.
    const half = width / 2;
    // Door based (the first eight): beside the walkway end in front of the garden. Facade based (the rest, nearest first): a grid in front
    // of and beside the building, for the building whose door is crowded (the plaza, a walkway or a neighbour is in the way).
    const cands: Array<[number, number]> = [];
    for (const [out, lat] of [[2.6, half + 3.4], [2.6, half + 5.4], [2.6, half + 8], [-1, half + 4]] as const) for (const s of [side0, -side0]) cands.push([out, lat * s]);
    const grid: Array<[number, number, number]> = [];
    for (const out of [3, 6, 9, 12, 15, 18, 22]) for (const lat of [half + 3, half + 5.5, half + 8, half + 11, half + 15]) for (const s of [side0, -side0]) grid.push([out * 0.7 + lat, out, lat * s]);
    // Beside the building, level with its front half, for a building hemmed in at the front.
    for (const out of [-3, -7, -11]) for (const lat of [p.width / 2 + half + 4.5, p.width / 2 + half + 7.5]) for (const s of [side0, -side0]) grid.push([40 + Math.abs(out) + lat, out, lat * s]);
    grid.sort((a, b) => a[0] - b[0]);
    for (const [, out, lat] of grid) cands.push([out, lat]);
    // Penalty of a candidate: a building, the plaza, another board or the campus edge is a hard fault (100 each); a post or the middle
    // of the board on a walkway or the ring path is a soft one (1 each). The first candidate with no fault wins, else the least faulty.
    const penalty = (cx: number, cz: number): number => {
      let bad = 0;
      for (let u = -width / 2; u <= width / 2 + 1e-6; u += width / 4) {
        const sx = cx + rx * u, sz = cz + rz * u;
        if (ctx.buildings.some((other) => sx > other.box.min.x - 2 && sx < other.box.max.x + 2 && sz > other.box.min.z - 2 && sz < other.box.max.z + 2)) bad += 100;
        if (Math.hypot(sx - monument.x, sz - monument.z) < PLAZA_CLEAR) bad += 100;
        if ((ctx.avoid ?? []).some(([ax, az, ar]) => Math.hypot(sx - ax, sz - az) < ar + 1.5)) bad += 100;
        if (Math.hypot(sx, sz) > limit) bad += 100;
        // The board stands on posts three metres up, so only its two posts and its middle have to clear the paths on the ground.
        if (Math.abs(u) < width / 2 - 1.5 && Math.abs(u) > 1e-6) continue;
        if (ctx.buildings.some((b) => distToSegment(sx, sz, b.door.x, b.door.z, walkTarget[0], walkTarget[1]) < 3)) bad += 1;
        if (ring > 0 && Math.abs(Math.hypot(sx, sz) - ring) < 2.5) bad += 1;
      }
      if (sites.some((s) => Math.hypot(s.x - cx, s.z - cz) < width + 1)) bad += 100;
      return bad;
    };
    let pick: [number, number] | null = null;
    let best = Infinity;
    for (let i = 0; i < cands.length && best > 0; i++) {
      const [out, lat] = cands[i];
      // The first four pairs (eight candidates) measure from the door, the rest from the facade.
      const doorBased = i < 8;
      const bx = doorBased ? own.door.x : frontX, bz = doorBased ? own.door.z : frontZ;
      const cx = bx + nx * out + rx * lat, cz = bz + nz * out + rz * lat;
      const bad = penalty(cx, cz);
      if (bad < best) { best = bad; pick = [cx, cz]; }
    }
    if (!pick) pick = [own.door.x + nx * 2.6, own.door.z + nz * 2.6];
    const [x, z] = pick;
    const top = clearance + height;
    const hw = width / 2 + 0.25;
    const corners = [[-hw, -0.4], [hw, -0.4], [hw, 0.4], [-hw, 0.4]].map(([u, v]) => [x + rx * u + nx * v, z + rz * u + nz * v]);
    const box: Aabb = {
      min: { x: Math.min(...corners.map((c) => c[0])), y: 0, z: Math.min(...corners.map((c) => c[1])) },
      max: { x: Math.max(...corners.map((c) => c[0])), y: top + 0.4, z: Math.max(...corners.map((c) => c[1])) },
    };
    sites.push({ workspaceId: p.workspaceId, x, z, nx, nz, width, height, clearance, anchor: { x, y: top + 0.7, z }, box });
  });
  return sites;
}

type Frame = Readonly<{ x: number; z: number; nx: number; nz: number; rx: number; rz: number }>;
const at = (f: Frame, u: number, v: number, y: number): [number, number, number] => [f.x + f.rx * u + f.nx * v, y, f.z + f.rz * u + f.nz * v];

/** A box in the board's frame (u along its width, v along the way it faces); every face points out whatever the heading. */
function frameBox(b: MeshBuilder, f: Frame, u0: number, u1: number, v0: number, v1: number, y0: number, y1: number, color: Rgb, emissive = 0): void {
  const c = (u: number, v: number, y: number) => at(f, u, v, y);
  const face = (a: number[], bb: number[], cc: number[], d: number[], out: readonly [number, number, number], shade: number) => {
    const ux = bb[0] - a[0], uy = bb[1] - a[1], uz = bb[2] - a[2], vx = cc[0] - a[0], vy = cc[1] - a[1], vz = cc[2] - a[2];
    const nxp = uy * vz - uz * vy, nyp = uz * vx - ux * vz, nzp = ux * vy - uy * vx;
    const col: Rgb = [Math.min(1, color[0] * shade), Math.min(1, color[1] * shade), Math.min(1, color[2] * shade)];
    if (nxp * out[0] + nyp * out[1] + nzp * out[2] >= 0) b.quad(a, bb, cc, d, col, emissive); else b.quad(a, d, cc, bb, col, emissive);
  };
  face(c(u0, v0, y1), c(u1, v0, y1), c(u1, v1, y1), c(u0, v1, y1), [0, 1, 0], 1);
  face(c(u0, v1, y0), c(u1, v1, y0), c(u1, v1, y1), c(u0, v1, y1), [f.nx, 0, f.nz], 0.85);
  face(c(u0, v0, y0), c(u1, v0, y0), c(u1, v0, y1), c(u0, v0, y1), [-f.nx, 0, -f.nz], 0.7);
  face(c(u1, v0, y0), c(u1, v1, y0), c(u1, v1, y1), c(u1, v0, y1), [f.rx, 0, f.rz], 0.78);
  face(c(u0, v0, y0), c(u0, v1, y0), c(u0, v1, y1), c(u0, v0, y1), [-f.rx, 0, -f.rz], 0.78);
}

export type BillboardMesh = Readonly<{ vertices: Float32Array; bounds: Aabb; triangles: number; halos: Float32Array }>;

/** The merged mesh of every board: posts, plinths, the dark panel, lit edge rules, two lamp heads, and the lettered face on both sides. */
export function boardMesh(sites: readonly BoardSite[], buildings: readonly SiteBuilding[], signCount: number): BillboardMesh {
  const b = new MeshBuilder();
  sites.forEach((s, i) => {
    const f: Frame = { x: s.x, z: s.z, nx: s.nx, nz: s.nz, rx: s.nz, rz: -s.nx };
    const hw = s.width / 2, y0 = s.clearance, y1 = s.clearance + s.height;
    b.mat = MAT.metal;
    for (const u of [-hw + 0.9, hw - 0.9]) {
      frameBox(b, f, u - 0.22, u + 0.22, -0.22, 0.22, 0, y1 - 0.1, POST_COLOR);
      b.mat = MAT.concrete;
      frameBox(b, f, u - 0.55, u + 0.55, -0.55, 0.55, 0, 0.32, CONCRETE);
      b.mat = MAT.metal;
    }
    b.mat = MAT.flat;
    frameBox(b, f, -hw, hw, -0.2, 0.2, y0, y1, PANEL_COLOR);
    // Lit rules along the top and bottom edges, both sides, like the facade sign.
    for (const v of [0.21, -0.21]) {
      const v1 = v > 0 ? v + 0.05 : v - 0.05;
      frameBox(b, f, -hw, hw, Math.min(v, v1), Math.max(v, v1), y0, y0 + 0.12, TEAL, 0.9);
      frameBox(b, f, -hw, hw, Math.min(v, v1), Math.max(v, v1), y1 - 0.12, y1, TEAL, 0.9);
    }
    // Two lamp heads on short arms above the panel, pointing at it.
    for (const u of [-hw * 0.5, hw * 0.5]) {
      frameBox(b, f, u - 0.05, u + 0.05, 0.1, 0.6, y1 + 0.02, y1 + 0.12, POST_COLOR);
      frameBox(b, f, u - 0.14, u + 0.14, 0.5, 0.74, y1 + 0.0, y1 + 0.18, LAMP, 0.85);
    }
    // The lettered faces: the building's own sign cell, front reads left to right facing the board, the back mirrors the direction.
    const sign = buildings.find((x) => x.workspaceId === s.workspaceId)?.signIndex ?? i;
    const cell = signCell(signCount, sign);
    b.mat = MAT.sign;
    const face = (side: 1 | -1) => {
      const v = side * 0.26;
      const left = at(f, -side * hw, v, y0 + 0.14), right = at(f, side * hw, v, y0 + 0.14);
      const leftT = at(f, -side * hw, v, y1 - 0.14), rightT = at(f, side * hw, v, y1 - 0.14);
      b.quadUV(left, right, rightT, leftT, [cell.u0, cell.v0], [cell.u1, cell.v0], [cell.u1, cell.v1], [cell.u0, cell.v1], [1, 1, 1], 0.95);
    };
    face(1);
    face(-1);
    b.halo(s.x + s.nx * 1.5, y0 - 0.2, s.z + s.nz * 1.5, 5.5, TEAL, 0.5);
  });
  const vertices = b.toFloat32();
  return { vertices, bounds: b.bounds(), triangles: b.triangleCount, halos: new Float32Array(b.halos) };
}
