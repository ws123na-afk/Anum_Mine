/**
 * Procedural low-poly campus: ground, walkways, water, trees, the central monument and one
 * building per placement. Every shape is generated from the placements and a fixed seed, so a
 * campus always looks the same for the same set of buildings. No data other than the layout goes
 * in: names, counts and statuses are HTML overlays drawn from the campus model, never geometry.
 */
import { MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import {
  ASPHALT, GROUND, STONE, TEAL, WARM, WATER, WINDOW_BASE, acUnit, antenna, anumMark, bench, flatPoly, blobShadow, bush, car, castShadow, cypress, flowerBed, hedge, lamp, palm,
  parkingLot, planter, pool, roofGarden, roundTree, skylineTower, solarRow, truck, waterTank,
} from './hqProps.ts';
import { campusExtent, type CampusBuildingKind, type Placement } from './campusModel.ts';
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type SceneMesh = Readonly<{ vertices: Float32Array; bounds: Aabb; triangles: number }>;
export type CampusBuildingMesh = Readonly<{
  workspaceId: string; mesh: SceneMesh; box: Aabb; anchor: Vec3; door: Vec3;
  /** Where the entrance sign (ANUM MINE and the building name) is pinned. */
  sign: Vec3;
}>;
export type CampusScene = Readonly<{
  scenery: SceneMesh;
  buildings: readonly CampusBuildingMesh[];
  monument: Vec3;
  extent: ReturnType<typeof campusExtent>;
  groundRadius: number;
  triangles: number;
}>;


const ACCENT: Record<CampusBuildingKind, Rgb> = {
  executive: hex('#d9b25f'), business: hex('#45d0b5'), department: hex('#6aa7ff'), personal: hex('#b48cff'),
};
const WALL: Record<CampusBuildingKind, Rgb> = {
  executive: hex('#cfd6de'), business: hex('#c9d3d0'), department: hex('#c7d0dd'), personal: hex('#d4cfdc'),
};
const PODIUM = 0.5;

const freeze = (b: MeshBuilder): SceneMesh => ({ vertices: b.toFloat32(), bounds: b.bounds(), triangles: b.triangleCount });

function path(b: MeshBuilder, x0: number, z0: number, x1: number, z1: number, width: number, y: number, color: Rgb, emissive = 0): void {
  const dx = x1 - x0, dz = z1 - z0, len = Math.hypot(dx, dz);
  if (len < 1e-6) return;
  const nx = (-dz / len) * (width / 2), nz = (dx / len) * (width / 2);
  flatPoly(b, [[x0 - nx, z0 - nz], [x1 - nx, z1 - nz], [x1 + nx, z1 + nz], [x0 + nx, z0 + nz]], y, color, emissive);
}

function distanceToSegment(px: number, pz: number, x0: number, z0: number, x1: number, z1: number): number {
  const dx = x1 - x0, dz = z1 - z0, l2 = dx * dx + dz * dz;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x0) * dx + (pz - z0) * dz) / l2));
  return Math.hypot(px - (x0 + t * dx), pz - (z0 + t * dz));
}

type Block = Readonly<{ x: number; z: number; w: number; d: number; levels: number; y: number; levelH: number }>;

/** One lit window: glass by day, a warm pane at night. `u0..u1` run along the facade, `place` maps u to the facade's (x, z). */
function windowPane(b: MeshBuilder, rnd: () => number, y0: number, y1: number, q: readonly [number, number], r: readonly [number, number], lit: number): void {
  const roll = rnd();
  if (roll < 0.1) { // dark room
    b.quad([q[0], y0, q[1]], [q[0], y1, q[1]], [r[0], y1, r[1]], [r[0], y0, r[1]], hex('#9fb4c8'), WINDOW_BASE);
    return;
  }
  const cool = roll > 0.86;
  const tone = cool ? mix(hex('#cfe6ff'), hex('#8fd0ff'), rnd()) : mix(WARM, hex('#ffe9b8'), rnd() * 0.7);
  const glow = WINDOW_BASE + (roll < 0.3 ? 0.45 : 0.8 + 0.15 * lit);
  const top = scale(tone, 1), bot = scale(tone, 0.72 * lit + 0.2);
  b.quadV([q[0], y0, q[1]], [q[0], y1, q[1]], [r[0], y1, r[1]], [r[0], y0, r[1]], bot, top, top, bot, glow);
}

/** Walls, floor bands, fins, windows, roof edge and rooftop plant for one block of a building. Returns the roof height. */
function drawBlock(b: MeshBuilder, k: Block, wall: Rgb, accent: Rgb, rnd: () => number, roof: 'flat' | 'crown' = 'flat', shadow = true): number {
  const { x, z, w, d, levels, y, levelH } = k;
  const h = levels * levelH;
  const fin = scale(wall, 0.92);
  // Dark glass core: lighter towards the top (sky reflection), darker at the base.
  b.box(x, y, z, w, h, d, hex('#27435f'), { skip: ['top', 'bottom'], grad: 0.5 });
  if (shadow) castShadow(b, x - 0.4, z - 0.4, w + 0.8, d + 0.8, Math.max(8, Math.min(30, y + h)), 0.022);
  const corner = (cx: number, cz: number) => b.box(cx - 0.45, y, cz - 0.45, 0.9, h, 0.9, wall, { grad: 0.25 });
  corner(x, z); corner(x + w, z); corner(x, z + d); corner(x + w, z + d);
  for (let l = 0; l < levels; l++) {
    const ly = y + l * levelH;
    b.box(x - 0.16, ly, z - 0.16, w + 0.32, 0.3, d + 0.32, wall);
    const lobby = l === 0 && y < 1;
    const wy = ly + (lobby ? 0.4 : levelH * 0.2), wh = lobby ? levelH * 0.74 : levelH * 0.58;
    const lit = 0.55 + 0.45 * ((l + 1) / levels);
    const facade = (len: number, place: (u: number) => readonly [number, number], finAt: (u: number) => void) => {
      const cols = Math.max(2, Math.floor((len - 1.6) / 2.1));
      const gap = (len - 1.6) / cols;
      for (let i = 0; i <= cols; i++) finAt(0.8 + i * gap);
      for (let i = 0; i < cols; i++) {
        const u0 = 0.8 + i * gap + gap * 0.1, u1 = 0.8 + (i + 1) * gap - gap * 0.1;
        windowPane(b, rnd, wy, wy + wh, place(u0), place(u1), lit);
        // Sill under each window so the glass reads as framed.
        const q = place(u0), r = place(u1);
        b.quad([q[0], wy - 0.08, q[1]], [q[0], wy, q[1]], [r[0], wy, r[1]], [r[0], wy - 0.08, r[1]], hex('#0f1a26'));
      }
    };
    facade(w, (u) => [x + u, z - 0.03] as const, (u) => b.box(x + u - 0.08, ly + 0.3, z - 0.22, 0.16, levelH - 0.3, 0.22, fin, { skip: ['top', 'bottom'] }));
    facade(w, (u) => [x + u, z + d + 0.03] as const, (u) => b.box(x + u - 0.08, ly + 0.3, z + d, 0.16, levelH - 0.3, 0.22, fin, { skip: ['top', 'bottom'] }));
    facade(d, (u) => [x - 0.03, z + u] as const, (u) => b.box(x - 0.22, ly + 0.3, z + u - 0.08, 0.22, levelH - 0.3, 0.16, fin, { skip: ['top', 'bottom'] }));
    facade(d, (u) => [x + w + 0.03, z + u] as const, (u) => b.box(x + w, ly + 0.3, z + u - 0.08, 0.22, levelH - 0.3, 0.16, fin, { skip: ['top', 'bottom'] }));
  }
  const ry = y + h;
  b.box(x - 0.5, ry, z - 0.5, w + 1.0, 0.5, d + 1.0, wall);
  for (const [bx, bz, bw, bd] of [[x - 0.5, z - 0.5, w + 1.0, 0.3], [x - 0.5, z + d + 0.2, w + 1.0, 0.3], [x - 0.5, z - 0.5, 0.3, d + 1.0], [x + w + 0.2, z - 0.5, 0.3, d + 1.0]] as const) {
    b.box(bx, ry + 0.5, bz, bw, 0.4, bd, accent, { emissive: 0.55 });
  }
  if (roof === 'flat' && w > 6 && d > 6) {
    b.plane(x + 0.4, ry + 0.52, z + 0.4, w - 0.8, d - 0.8, mix(wall, hex('#8d9aa5'), 0.5));
    acUnit(b, x + w * 0.12, ry + 0.5, z + d * 0.16, Math.min(3.2, w * 0.24), Math.min(2.2, d * 0.2));
    acUnit(b, x + w * 0.42, ry + 0.5, z + d * 0.16, Math.min(2.6, w * 0.2), Math.min(2.2, d * 0.2));
    waterTank(b, x + w * 0.84, ry + 0.5, z + d * 0.22);
    antenna(b, x + w * 0.9, ry + 0.5, z + d * 0.8, 3.4 + rnd() * 2);
    if (rnd() < 0.55) solarRow(b, x + w * 0.1, ry + 0.5, z + d * 0.6, Math.max(2, Math.min(5, Math.floor((w * 0.8) / 2.4))), 2.0, 1.7);
    else roofGarden(b, x + w * 0.1, ry + 0.5, z + d * 0.52, w * 0.55, d * 0.36, rnd);
    // A glass skylight over the stair core.
    b.box(x + w * 0.62, ry + 0.5, z + d * 0.5, w * 0.2, 0.7, d * 0.2, hex('#7fb9d6'), { emissive: 0.35 });
  }
  return ry + 0.9;
}

function hipRoof(b: MeshBuilder, x: number, z: number, w: number, d: number, y: number, h: number, color: Rgb): void {
  const o = 0.7;
  const ax = x + w / 2, az = z + d / 2;
  const c = [[x - o, z - o], [x + w + o, z - o], [x + w + o, z + d + o], [x - o, z + d + o]];
  for (let i = 0; i < 4; i++) {
    const p = c[i], q = c[(i + 1) % 4];
    b.tri([p[0], y, p[1]], [q[0], y, q[1]], [ax, y + h, az], scale(color, 0.7 + 0.12 * i));
  }
  b.box(ax - 0.5, y + h * 0.4, az - 0.5, 1, h * 0.6, 1, scale(color, 0.6));
}

function buildingMesh(p: Placement, seed: string): { mesh: SceneMesh; anchor: Vec3; door: Vec3; sign: Vec3 } {
  const b = new MeshBuilder();
  const rnd = seeded(seed);
  const accent = ACCENT[p.kind];
  // The finish varies per building (white, sandstone or slate) so a campus does not read as clones.
  const style = Math.floor(rnd() * 3);
  const base = WALL[p.kind];
  const wall = style === 1 ? mix(base, hex('#d8c9a8'), 0.7) : style === 2 ? mix(base, hex('#8a93a0'), 0.55) : base;
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2;
  const W = p.width, D = p.depth;
  b.box(x0 - 1.2, 0, z0 - 1.2, W + 2.4, PODIUM, D + 2.4, STONE);
  const y0 = PODIUM;
  const levelH = (p.height - PODIUM - 1.2) / p.levels;
  let top = 0;
  let doorX = p.x;
  const L = p.levels;
  if (p.kind === 'executive') {
    // Two-storey glass podium, a slim tower and a stepped crown: the one building that reads as the tower.
    const baseH = 4.4;
    top = Math.max(top, drawBlock(b, { x: x0, z: z0, w: W, d: D, levels: 1, y: y0, levelH: baseH }, wall, accent, rnd, 'flat', false));
    const tw = W * 0.7, td = D * 0.7, tx = x0 + (W - tw) / 2, tz = z0 + (D - td) / 2;
    const towerLevels = Math.max(2, L - 1);
    const tLevelH = Math.max(2.8, (p.height - y0 - baseH - 4.5) / towerLevels);
    top = Math.max(top, drawBlock(b, { x: tx, z: tz, w: tw, d: td, levels: towerLevels, y: y0 + baseH + 0.6, levelH: tLevelH }, wall, accent, rnd, 'crown'));
    const cy = y0 + baseH + 0.6 + towerLevels * tLevelH + 0.8;
    top = Math.max(top, drawBlock(b, { x: tx + tw * 0.2, z: tz + td * 0.2, w: tw * 0.6, d: td * 0.6, levels: 2, y: cy, levelH: 2.8 }, wall, accent, rnd, 'crown', false));
    const my = cy + 5.6 + 0.8;
    // The ANUM mark, lit, on the tower's south face: the campus landmark.
    anumMark(b, tx + tw / 2, y0 + baseH + 0.6 + (towerLevels * tLevelH) * 0.62, tz + td + 0.34, Math.min(7, tLevelH * 2.2));
    b.box(p.x - 1.6, my, p.z - 1.6, 3.2, 1.0, 3.2, accent, { emissive: 0.7 });
    b.prism(p.x, my + 1.0, p.z, 0.2, 0.05, 7, 5, hex('#9aa5b1'), { capTop: false });
    b.box(p.x - 0.2, my + 8, p.z - 0.2, 0.4, 0.4, 0.4, hex('#ff5a5a'), { emissive: 1, shade: false });
    // Helipad on the podium roof.
    b.prism(x0 + W * 0.18, y0 + baseH + 0.5, z0 + D * 0.82, 2.4, 2.4, 0.08, 16, hex('#3b4452'));
    b.prism(x0 + W * 0.18, y0 + baseH + 0.58, z0 + D * 0.82, 1.9, 1.9, 0.02, 16, hex('#e8d36a'), { emissive: 0.25 });
    top = my + 8.6;
  } else if (p.kind === 'business') {
    // Main block with a lower wing and a glass link in front.
    const mw = W * 0.6;
    top = Math.max(top, drawBlock(b, { x: x0, z: z0, w: mw, d: D * 0.8, levels: L, y: y0, levelH }, wall, accent, rnd));
    const wl = Math.max(1, L - 1);
    top = Math.max(top, drawBlock(b, { x: x0 + mw + 1.2, z: z0 + D * 0.15, w: W - mw - 1.2, d: D * 0.65, levels: wl, y: y0, levelH }, wall, accent, rnd));
    b.box(x0 + mw - 0.2, y0, z0 + D * 0.3, 1.6, levelH * 1.1, D * 0.4, hex('#7fb9d6'), { emissive: 0.45, skip: ['bottom'] });
    doorX = x0 + mw / 2;
  } else if (p.kind === 'department') {
    // Stepped: a full-width base and a narrower upper block.
    const baseLevels = Math.max(1, Math.ceil(L / 2));
    top = Math.max(top, drawBlock(b, { x: x0, z: z0, w: W, d: D, levels: baseLevels, y: y0, levelH }, wall, accent, rnd));
    const upper = L - baseLevels;
    if (upper > 0) top = Math.max(top, drawBlock(b, { x: x0 + W * 0.18, z: z0 + D * 0.12, w: W * 0.64, d: D * 0.7, levels: upper, y: y0 + baseLevels * levelH + 0.9, levelH }, wall, accent, rnd));
  } else {
    // Personal: a low house with a hip roof and a garden wall.
    top = Math.max(top, drawBlock(b, { x: x0 + 0.8, z: z0 + 0.8, w: W - 1.6, d: D - 1.6, levels: Math.max(1, L), y: y0, levelH: levelH * 1.1 }, wall, accent, rnd, 'crown'));
    const ry = y0 + Math.max(1, L) * levelH * 1.1 + 0.5;
    hipRoof(b, x0 + 0.8, z0 + 0.8, W - 1.6, D - 1.6, ry, 2.6, mix(accent, hex('#3a2f4a'), 0.55));
    top = ry + 2.8;
  }
  // Entrance: a deep canopy on two columns, steps, a lit door, planters, and a sign board with the ANUM mark.
  const doorZ = z0 + (p.kind === 'business' ? D * 0.8 : p.kind === 'personal' ? D - 0.8 : D);
  b.box(doorX - 3.0, y0 + 3.2, doorZ, 6, 0.34, 2.8, accent, { emissive: 0.45 });
  b.box(doorX - 3.0, y0 + 3.54, doorZ, 6, 0.12, 2.8, scale(wall, 0.9));
  b.quad([doorX - 1.3, y0, doorZ + 0.05], [doorX + 1.3, y0, doorZ + 0.05], [doorX + 1.3, y0 + 3.0, doorZ + 0.05], [doorX - 1.3, y0 + 3.0, doorZ + 0.05], WARM, WINDOW_BASE + 0.95);
  b.box(doorX - 1.35, y0, doorZ, 0.1, 3.0, 0.14, scale(wall, 0.7));
  b.box(doorX + 1.25, y0, doorZ, 0.1, 3.0, 0.14, scale(wall, 0.7));
  b.box(doorX - 0.05, y0, doorZ, 0.1, 3.0, 0.14, scale(wall, 0.7));
  for (const sx of [-2.8, 2.6]) b.box(doorX + sx, y0, doorZ + 2.4, 0.22, 3.2, 0.22, wall);
  for (let i = 0; i < 3; i++) b.box(doorX - 3.0 + i * 0.35, 0.18 * (3 - i) * 0.5, doorZ + 2.8 + i * 0.35, 6 - i * 0.7, 0.18, 0.5, mix(STONE, hex('#ffffff'), 0.2 + i * 0.05));
  b.box(doorX - 3.4, 0, doorZ + 3.9, 6.8, 0.22, 3.4, mix(STONE, hex('#ffffff'), 0.25));
  // Sign board over the canopy: dark panel, accent rule and the ANUM mark (the name itself is the HTML sign).
  const signY = y0 + 3.9;
  b.box(doorX - 2.6, signY, doorZ + 0.02, 5.2, 1.3, 0.22, hex('#0e1b26'));
  b.box(doorX - 2.6, signY, doorZ + 0.24, 5.2, 0.07, 0.04, accent, { emissive: 0.9, shade: false });
  anumMark(b, doorX - 1.85, signY + 0.7, doorZ + 0.26, 0.95);
  b.box(doorX - 1.1, signY + 0.5, doorZ + 0.25, 3.3, 0.14, 0.03, scale(wall, 1.0), { emissive: 0.8, shade: false });
  b.box(doorX - 1.1, signY + 0.78, doorZ + 0.25, 2.2, 0.1, 0.03, scale(wall, 0.9), { emissive: 0.7, shade: false });
  // Planters, a hedge line and a flower bed in front of the building.
  planter(b, doorX - 4.4, doorZ + 2.2, 1.2, rnd);
  planter(b, doorX + 4.4, doorZ + 2.2, 1.2, rnd);
  hedge(b, x0 - 0.2, doorZ + 6.2, Math.max(2, doorX - 5.6 - (x0 - 0.2)), 0.7, 0.7);
  hedge(b, doorX + 5.6, doorZ + 6.2, Math.max(2, x0 + W + 0.2 - (doorX + 5.6)), 0.7, 0.7);
  flowerBed(b, doorX - 9.0, doorZ + 3.2, 3.2, 1.6, rnd);
  flowerBed(b, doorX + 5.8, doorZ + 3.2, 3.2, 1.6, rnd);
  return {
    mesh: freeze(b),
    anchor: { x: p.x, y: top + 1.2, z: p.z },
    door: { x: doorX, y: 0, z: doorZ + 8 },
    sign: { x: doorX, y: signY + 0.7, z: doorZ + 0.5 },
  };
}

export function buildCampusScene(placements: readonly Placement[]): CampusScene {
  const extent = campusExtent(placements);
  const groundRadius = Math.max(130, extent.radius + 80);
  const rnd = seeded('anum-mine-campus');
  const exec = placements.find((p) => p.kind === 'executive');
  const monument: Vec3 = exec ? { x: exec.x, y: 0, z: exec.z + exec.depth / 2 + 15 } : { x: 0, y: 0, z: 0 };
  const roadR = Math.max(44, extent.radius + 12);

  const s = new MeshBuilder();
  // Ground: a square slab, a tonal lawn mottle and a darker verge so the edge never reads as a cut-off.
  s.plane(-groundRadius, -0.05, -groundRadius, groundRadius * 2, groundRadius * 2, GROUND, 0.07);
  s.plane(-groundRadius, -0.049, -groundRadius, groundRadius * 2, 6, scale(GROUND, 0.85));
  const mottle = seeded('anum-lawn');
  for (let i = 0; i < 46; i++) {
    const a = mottle() * Math.PI * 2, r = Math.sqrt(mottle()) * groundRadius * 0.9;
    s.disc(Math.cos(a) * r, 0.003, Math.sin(a) * r, 7 + mottle() * 12, 14, scale(GROUND, 0.88 + mottle() * 0.3));
  }
  // A far skyline (boxes with lit bands) in place of a hill ring: it reads as a city beyond the campus.
  const sky = seeded('anum-skyline');
  for (let i = 0; i < 46; i++) {
    const a = (i / 46) * Math.PI * 2 + sky() * 0.1;
    const r = groundRadius * (1.0 + sky() * 0.16);
    skylineTower(s, Math.cos(a) * r, Math.sin(a) * r, 6 + sky() * 9, 6 + sky() * 9, 14 + sky() * 46, sky);
  }
  // Plaza and monument.
  s.disc(monument.x, 0.02, monument.z, 11, 28, mix(STONE, hex('#ffffff'), 0.1));
  s.disc(monument.x, 0.06, monument.z, 7.2, 24, mix(STONE, hex('#8a8577'), 0.4));
  s.disc(monument.x, 0.1, monument.z, 6.2, 24, WATER, 0.1);
  s.prism(monument.x, 0, monument.z, 2.2, 1.6, 1.6, 12, STONE);
  s.prism(monument.x, 1.6, monument.z, 0.9, 0.7, 2.6, 8, mix(STONE, hex('#ffffff'), 0.3));
  s.blob(monument.x, 5.4, monument.z, 2.1, 2.1, 2.1, hex('#3a8fb8'), 10);
  s.prism(monument.x, 3.3, monument.z, 2.4, 2.4, 0.18, 24, hex('#d9b25f'), { emissive: 0.6 });
  s.prism(monument.x, 4.1, monument.z, 2.7, 2.7, 0.14, 24, hex('#45d0b5'), { emissive: 0.7 });
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2;
    s.prism(monument.x + Math.cos(a) * 4.7, 0.1, monument.z + Math.sin(a) * 4.7, 0.16, 0.02, 1.6, 4, hex('#bfe6ff'), { emissive: 0.5, capTop: false });
  }
  s.prism(monument.x, 0.12, monument.z, 6.3, 6.3, 0.12, 28, hex('#7fd8ff'), { emissive: 0.8 });
  // Plaza furniture: a ring of benches, flower beds and lamp posts.
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + 0.2;
    bench(s, monument.x + Math.cos(a) * 9.2, monument.z + Math.sin(a) * 9.2, Math.abs(Math.cos(a)) > 0.7 ? 'z' : 'x');
    if (i % 2 === 0) lamp(s, monument.x + Math.cos(a + 0.4) * 10.2, monument.z + Math.sin(a + 0.4) * 10.2);
  }
  // Buildings, their walkways, and the ring road.
  const buildings: CampusBuildingMesh[] = [];
  const segments: Array<[number, number, number, number]> = [];
  for (const p of placements) {
    const built = buildingMesh(p, 'bld:' + p.workspaceId);
    buildings.push({
      workspaceId: p.workspaceId, mesh: built.mesh, anchor: built.anchor, door: built.door, sign: built.sign,
      box: { min: { x: p.x - p.width / 2 - 1.2, y: 0, z: p.z - p.depth / 2 - 1.2 }, max: { x: p.x + p.width / 2 + 1.2, y: built.mesh.bounds.max.y, z: p.z + p.depth / 2 + 1.2 } },
    });
    const from: [number, number] = [monument.x, monument.z + 10];
    if (p.kind === 'executive') segments.push([p.x, p.z + p.depth / 2 + 3, monument.x, monument.z - 10]);
    else segments.push([built.door.x, built.door.z, from[0], from[1]]);
  }
  // Lawn and contact patches under each building so it sits on the ground, not above it.
  for (const p of placements) s.plane(p.x - p.width / 2 - 3.5, 0.005, p.z - p.depth / 2 - 3.5, p.width + 7, p.depth + 7, scale(GROUND, 0.72));
  for (const [x0, z0, x1, z1] of segments) path(s, x0, z0, x1, z1, 3.2, 0.03, STONE, 0.16);
  if (extent.radius > 30) {
    const sides = 48;
    const rr = Math.max(26, extent.radius * 0.62);
    for (let i = 0; i < sides; i++) {
      const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2;
      path(s, Math.cos(a0) * rr, Math.sin(a0) * rr, Math.cos(a1) * rr, Math.sin(a1) * rr, 3, 0.025, scale(STONE, 0.9), 0.14);
    }
  }
  // Ring road: asphalt, kerbs and a dashed centre line.
  const roadSides = 72;
  const gateAngle = Math.PI / 2;
  for (let i = 0; i < roadSides; i++) {
    const a0 = (i / roadSides) * Math.PI * 2, a1 = ((i + 1) / roadSides) * Math.PI * 2;
    const p0: [number, number] = [Math.cos(a0) * roadR, Math.sin(a0) * roadR], p1: [number, number] = [Math.cos(a1) * roadR, Math.sin(a1) * roadR];
    path(s, p0[0], p0[1], p1[0], p1[1], 7.2, 0.035, ASPHALT, 0.06);
    const ko = 4.2;
    for (const k of [-ko, ko]) {
      const q0: [number, number] = [Math.cos(a0) * (roadR + k), Math.sin(a0) * (roadR + k)], q1: [number, number] = [Math.cos(a1) * (roadR + k), Math.sin(a1) * (roadR + k)];
      path(s, q0[0], q0[1], q1[0], q1[1], 0.7, 0.045, scale(STONE, 1.05));
    }
    if (i % 2 === 0) path(s, p0[0], p0[1], (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, 0.22, 0.05, hex('#e8e4d0'), );
  }
  // Moving traffic frozen in place: a few cars on the ring road, nose along the lane.
  const carColors = [hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#3d6b52'), hex('#e8ebee'), hex('#1f2429')];
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2 + 0.3 + rnd() * 0.2;
    if (Math.abs(a % (Math.PI * 2) - gateAngle) < 0.25) continue;
    const lane = i % 2 === 0 ? 1.7 : -1.7;
    const cx = Math.cos(a) * (roadR + lane), cz = Math.sin(a) * (roadR + lane);
    const tx = -Math.sin(a) * (lane > 0 ? -1 : 1), tz = Math.cos(a) * (lane > 0 ? -1 : 1);
    const rot = Math.abs(tx) > Math.abs(tz) ? (tx > 0 ? 3 : 1) : (tz > 0 ? 0 : 2);
    car(s, cx, cz, carColors[i % carColors.length], rot);
  }
  // Entrance gate on the south side: two piers, a lit lintel with the ANUM mark, a short road in, low walls and planting.
  const gz = roadR + 10;
  path(s, 0, roadR, 0, gz, 7.2, 0.036, ASPHALT);
  for (const sx of [-6.2, 4.6]) {
    s.box(sx, 0, gz - 1.2, 1.6, 6.2, 2.4, hex('#b8b0a2'), { grad: 0.12 });
    s.box(sx - 0.2, 6.2, gz - 1.4, 2.0, 0.4, 2.8, hex('#8d867a'));
  }
  s.box(-6.4, 5.4, gz - 1.3, 13.2, 1.6, 2.6, hex('#0e1b26'));
  s.box(-6.4, 5.4, gz + 1.32, 13.2, 0.08, 0.04, TEAL, { emissive: 0.9, shade: false });
  anumMark(s, 0, 6.2, gz + 1.36, 1.1);
  s.box(-1.2, 0, gz + 3, 2.4, 0.5, 0.3, hex('#d7d2c4'));
  for (const [x0, x1] of [[-44, -7], [7, 44]] as const) {
    s.box(x0, 0, gz - 0.4, x1 - x0, 1.1, 0.8, hex('#b8b0a2'));
    for (let x = x0 + 2; x < x1 - 1; x += 3.6) hedge(s, x, gz + 0.5, 2.4, 0.8, 1.0);
  }
  // Ponds in the free gaps, then trees and lamps that avoid buildings, paths, water and the road.
  const ponds: Array<[number, number, number]> = [];
  const pondCount = Math.min(4, Math.max(1, Math.floor(placements.length / 3)));
  for (let i = 0; i < pondCount; i++) {
    const a = Math.PI * 0.2 + (i / pondCount) * Math.PI * 2 + 0.5;
    const r = Math.max(24, extent.radius * 0.45);
    ponds.push([Math.cos(a) * r, Math.sin(a) * r, 5.5 + rnd() * 2.5]);
  }
  const lots: Array<[number, number, number, number]> = [];
  const blocked = (x: number, z: number, margin: number): boolean => {
    for (const p of placements) {
      if (Math.abs(x - p.x) < p.width / 2 + margin + 3 && Math.abs(z - p.z) < p.depth / 2 + margin + 8) return true;
    }
    if (Math.hypot(x - monument.x, z - monument.z) < 13 + margin) return true;
    for (const [px, pz, pr] of ponds) if (Math.hypot(x - px, z - pz) < pr + 3 + margin) return true;
    for (const [x0, z0, x1, z1] of segments) if (distanceToSegment(x, z, x0, z0, x1, z1) < 3 + margin) return true;
    if (Math.abs(Math.hypot(x, z) - roadR) < 5.2 + margin) return true;
    if (Math.abs(x) < 8 + margin && z > roadR - 2 && z < gz + 3) return true;
    for (const [lx, lz, lw, ld] of lots) if (x > lx - margin && x < lx + lw + margin && z > lz - margin && z < lz + ld + margin) return true;
    return false;
  };
  for (const [px, pz, pr] of ponds) pool(s, px, pz, pr, rnd);
  // Car parks beside buildings (west side, nose to the road) where the ground is free.
  for (const p of placements) {
    if (p.kind === 'executive' || rnd() < 0.3) continue;
    const lx = p.x - p.width / 2 - 14.5, lz = p.z - p.depth / 2 + 1;
    if (blocked(lx + 4, lz + 3, 1) || Math.hypot(lx, lz) > roadR - 6) continue;
    lots.push([lx, lz, 9.7, 8]);
    parkingLot(s, lx, lz, 3, 1, rnd, 0.75);
  }
  const treeCount = Math.min(260, 70 + placements.length * 12);
  let placed = 0, attempts = 0;
  while (placed < treeCount && attempts < treeCount * 14) {
    attempts += 1;
    const a = rnd() * Math.PI * 2;
    const r = 8 + Math.sqrt(rnd()) * (groundRadius * 0.74);
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (blocked(x, z, 1.5)) continue;
    const roll = rnd();
    if (roll < 0.42) palm(s, x, z, 5 + rnd() * 2.6, rnd);
    else if (roll < 0.72) roundTree(s, x, z, 3.6 + rnd() * 2.4, rnd);
    else if (roll < 0.86) cypress(s, x, z, 4 + rnd() * 3, rnd);
    else bush(s, x, z, 0.7 + rnd() * 0.6, rnd);
    placed += 1;
  }
  // Palms in a row on both kerbs of the ring road.
  for (let i = 0; i < 44; i++) {
    const a = (i / 44) * Math.PI * 2;
    if (Math.abs(a - gateAngle) < 0.12) continue;
    for (const k of [-6.2, 6.2]) {
      const x = Math.cos(a) * (roadR + k), z = Math.sin(a) * (roadR + k);
      if (!placements.some((p) => Math.abs(x - p.x) < p.width / 2 + 3 && Math.abs(z - p.z) < p.depth / 2 + 3)) palm(s, x, z, 5.4 + rnd() * 1.4, rnd);
    }
  }
  // A truck yard and a larger car park at free spots outside the ring road, so the campus reads as a working place.
  const spots: Array<[number, number]> = [];
  for (let i = 0; i < 30 && spots.length < 3; i++) {
    const a = Math.PI * 0.12 + i * 0.5, r = roadR + 17;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (![0, 1, 2].some((k) => blocked(x + k * 5.5, z, 4)) && !blocked(x, z + 8, 3) && !blocked(x, z - 8, 3) && spots.every(([sx, sz]) => Math.hypot(sx - x, sz - z) > 34)) spots.push([x, z]);
  }
  if (spots[0]) {
    const [yx, yz] = spots[0];
    s.plane(yx - 5, 0.012, yz - 11, 18, 22, scale(STONE, 0.7));
    truck(s, yx, yz, hex('#2f6a8f'));
    truck(s, yx + 6, yz + 1, hex('#b4452f'));
    truck(s, yx + 12, yz - 1, hex('#3d6b52'));
    for (let i = 0; i < 4; i++) s.box(yx - 4.6 + i * 4.5, 0, yz + 6.8, 0.2, 0.2, 3.4, hex('#e8d36a'), { emissive: 0.1 });
  }
  if (spots[1]) parkingLot(s, spots[1][0], spots[1][1], 6, 2, rnd, 0.8);
  if (spots[2]) parkingLot(s, spots[2][0], spots[2][1], 5, 1, rnd, 0.7);
  for (const [x0, z0, x1, z1] of segments) {
    const len = Math.hypot(x1 - x0, z1 - z0);
    const count = Math.floor(len / 14);
    const nx = -(z1 - z0) / (len || 1), nz = (x1 - x0) / (len || 1);
    for (let i = 1; i <= count; i++) {
      const t = i / (count + 1);
      lamp(s, x0 + (x1 - x0) * t + nx * 2.6, z0 + (z1 - z0) * t + nz * 2.6);
    }
  }
  const total = s.triangleCount + buildings.reduce((n, bd) => n + bd.mesh.triangles, 0);
  return { scenery: freeze(s), buildings, monument: { x: monument.x, y: 8.2, z: monument.z }, extent, groundRadius, triangles: total };
}
