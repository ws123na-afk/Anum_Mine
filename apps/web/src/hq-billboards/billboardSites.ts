/**
 * Where each building's billboard stands and what it is made of (docs/ANUM_MINE_HQ_BILLBOARDS.md). Pure geometry, no DOM, no data:
 * the faces are mapped onto the board's cell of the live board atlas, which `billboardTexture.ts` draws (name, what the building does, the
 * live counts and the verified time, right to left in Arabic mode).
 *
 * One merged mesh for the whole campus, deterministic: the same placements give the same boards.
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

export type SiteBuilding = Readonly<{ workspaceId: string; box: Aabb; door: Vec3 }>;
export type SiteContext = Readonly<{
  placements: readonly Placement[];
  buildings: readonly SiteBuilding[];
  monument: Vec3;
  extentRadius: number;
  /** Radius of the canal ring (the edge of the usable campus), or 0. */
  canalRadius: number;
  /** The centre (`CampusScene.plaza`): the plaza and roundabout radius, the walkways to the doors and the avenues. Boards keep clear of all of it. */
  plaza?: Readonly<{ outer: number; spokes: ReadonlyArray<Readonly<{ points: ReadonlyArray<readonly [number, number]> }>>; avenues: ReadonlyArray<Readonly<{ from: readonly [number, number]; to: readonly [number, number] }>> }>;
  /** Extra round areas the boards keep out of (plaza, fountain), as x, z, radius. */
  avoid?: ReadonlyArray<readonly [number, number, number]>;
}>;

/**
 * The board atlas: one canvas for every board of the campus, a cell of 512 x 256 per board (256 x 128 on the lowest quality tier, or smaller on a very large campus, so the
 * canvas never grows past 2048 x 1024). The live details are drawn into the cell of the board (`billboardTexture.ts`); the mesh maps each
 * face onto its cell. Pure, so the mesh and the painter agree without importing each other.
 */
export type BoardCell = Readonly<{ u0: number; u1: number; v0: number; v1: number }>;
export type BoardAtlasLayout = Readonly<{ width: number; height: number; cellW: number; cellH: number; cols: number; rows: number; capacity: number; cell: (index: number) => BoardCell; origin: (index: number) => Readonly<{ x: number; y: number }> }>;
const ATLAS_W = 2048;
const pow2 = (n: number) => { let p = 1; while (p < n) p *= 2; return p; };
export function boardAtlasLayout(count: number, maxCell: 512 | 256 = 512): BoardAtlasLayout {
  // The richest cell whose atlas stays within 2048 x 1024, and no richer than the quality tier allows (hqQualityTiers `boardCell`).
  let cellW: number = maxCell, cellH = maxCell / 2;
  while (cellW > 64 && (ATLAS_W / cellW) * (1024 / cellH) < count) { cellW /= 2; cellH /= 2; }
  const cols = ATLAS_W / cellW;
  const rows = Math.max(1, Math.ceil(Math.max(1, count) / cols));
  const height = Math.min(1024, pow2(rows * cellH));
  const capacity = cols * Math.floor(height / cellH);
  const slot = (index: number) => ((index % capacity) + capacity) % capacity;
  return {
    width: ATLAS_W, height, cellW, cellH, cols, rows, capacity,
    origin: (index) => ({ x: (slot(index) % cols) * cellW, y: Math.floor(slot(index) / cols) * cellH }),
    cell: (index) => {
      const col = slot(index) % cols, row = Math.floor(slot(index) / cols);
      return { u0: (col * cellW) / ATLAS_W, u1: ((col + 1) * cellW) / ATLAS_W, v1: 1 - (row * cellH) / height, v0: 1 - ((row + 1) * cellH) / height };
    },
  };
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

/** The face is twice as wide as it is high: the name above, what the building does, the live counts and the verified time (`billboardTexture.ts`). */
export const BOARD_ASPECT = 0.5;
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
  const height = width * BOARD_ASPECT;
  const clearance = 3;
  const monument = ctx.monument;
  const plazaR = ctx.plaza ? ctx.plaza.outer + 2 : PLAZA_CLEAR;
  // Every path on the ground as a segment with the clearance a post needs: the walkways, the avenues, and the old straight walk when no plaza is given.
  const paths: Array<[number, number, number, number, number]> = [];
  if (ctx.plaza) {
    for (const sp of ctx.plaza.spokes) for (let i = 0; i + 1 < sp.points.length; i++) paths.push([sp.points[i][0], sp.points[i][1], sp.points[i + 1][0], sp.points[i + 1][1], 3]);
    for (const a of ctx.plaza.avenues) paths.push([a.from[0], a.from[1], a.to[0], a.to[1], 6]);
  } else for (const b of ctx.buildings) paths.push([b.door.x, b.door.z, monument.x, monument.z + 10, 3.4]);
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
        if (Math.hypot(sx - monument.x, sz - monument.z) < plazaR) bad += 100;
        if ((ctx.avoid ?? []).some(([ax, az, ar]) => Math.hypot(sx - ax, sz - az) < ar + 1.5)) bad += 100;
        if (Math.hypot(sx, sz) > limit) bad += 100;
        // The board stands on posts three metres up, so only its two posts and its middle have to clear the paths on the ground.
        if (Math.abs(u) < width / 2 - 1.5 && Math.abs(u) > 1e-6) continue;
        if (paths.some(([x0, z0, x1, z1, clear]) => distToSegment(sx, sz, x0, z0, x1, z1) < clear)) bad += 1;
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
export function boardMesh(sites: readonly BoardSite[]): BillboardMesh {
  const b = new MeshBuilder();
  const layout = boardAtlasLayout(sites.length);
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
    // A metal frame down both sides, so the lit face reads as a mounted board and not a floating picture.
    b.mat = MAT.metal;
    for (const u of [-hw - 0.08, hw + 0.08]) frameBox(b, f, u - 0.1, u + 0.1, -0.3, 0.3, y0 - 0.06, y1 + 0.06, POST_COLOR);
    b.mat = MAT.flat;
    // Two lamp heads on short arms above the panel, pointing at it.
    for (const u of [-hw * 0.5, hw * 0.5]) {
      frameBox(b, f, u - 0.05, u + 0.05, 0.1, 0.6, y1 + 0.02, y1 + 0.12, POST_COLOR);
      frameBox(b, f, u - 0.14, u + 0.14, 0.5, 0.74, y1 + 0.0, y1 + 0.18, LAMP, 0.85);
    }
    // The lettered faces: the board's own cell of the live board atlas (`billboardTexture.ts` draws the name and the details into it); the back face mirrors the direction.
    const cell = layout.cell(i);
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

/** A board this near the camera's eye (metres) shows its close view (docs/ANUM_MINE_HQ_BILLBOARDS.md, "Near and far"). */
export const CLOSE_M = 60;

/** The buildings whose board is within `near` metres of the camera's eye, sorted (a stable key for the page to compare). */
export function closeBoards(layer: Readonly<{ sites: readonly BoardSite[] }>, eye: Readonly<{ x: number; y: number; z: number }>, near = CLOSE_M): string[] {
  return layer.sites.filter((s) => Math.hypot(s.anchor.x - eye.x, s.anchor.y - eye.y, s.anchor.z - eye.z) <= near).map((s) => s.workspaceId).sort();
}
