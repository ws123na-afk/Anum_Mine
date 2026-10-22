/**
 * Procedural low-poly campus: ground, walkways, water, trees, the central monument and one
 * building per placement. Every shape is generated from the placements and a fixed seed, so a
 * campus always looks the same for the same set of buildings. No data other than the layout goes
 * in: names, counts and statuses are HTML overlays drawn from the campus model, never geometry.
 */
import { MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import { campusExtent, type CampusBuildingKind, type Placement } from './campusModel.ts';
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type SceneMesh = Readonly<{ vertices: Float32Array; bounds: Aabb; triangles: number }>;
export type CampusBuildingMesh = Readonly<{
  workspaceId: string; mesh: SceneMesh; box: Aabb; anchor: Vec3; door: Vec3;
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
const GROUND = hex('#3c5a3f');
const STONE = hex('#b9b4a6');
const WATER = hex('#2c6f99');
const WINDOW_WARM = hex('#ffcf7a');
const GLASS_DARK = hex('#1c2c3b');

const freeze = (b: MeshBuilder): SceneMesh => ({ vertices: b.toFloat32(), bounds: b.bounds(), triangles: b.triangleCount });

function path(b: MeshBuilder, x0: number, z0: number, x1: number, z1: number, width: number, y: number, color: Rgb): void {
  const dx = x1 - x0, dz = z1 - z0, len = Math.hypot(dx, dz);
  if (len < 1e-6) return;
  const nx = (-dz / len) * (width / 2), nz = (dx / len) * (width / 2);
  b.quad([x0 - nx, y, z0 - nz], [x1 - nx, y, z1 - nz], [x1 + nx, y, z1 + nz], [x0 + nx, y, z0 + nz], color);
}

function distanceToSegment(px: number, pz: number, x0: number, z0: number, x1: number, z1: number): number {
  const dx = x1 - x0, dz = z1 - z0, l2 = dx * dx + dz * dz;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x0) * dx + (pz - z0) * dz) / l2));
  return Math.hypot(px - (x0 + t * dx), pz - (z0 + t * dz));
}

function palm(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  const lean = (rnd() - 0.5) * 0.6;
  b.prism(x, 0, z, 0.22, 0.14, h * 0.55, 5, hex('#6b5238'), { capTop: false });
  b.prism(x + lean, h * 0.55, z, 0.14, 0.1, h * 0.45, 5, hex('#6b5238'), { capTop: false });
  const topY = h;
  for (let i = 0; i < 7; i++) {
    const a = (i / 7) * Math.PI * 2 + rnd() * 0.3;
    const len = 1.9 + rnd() * 0.5;
    const cx = x + lean, cz = z;
    const tipX = cx + Math.cos(a) * len, tipZ = cz + Math.sin(a) * len;
    const sx = Math.sin(a) * 0.45, sz = -Math.cos(a) * 0.45;
    b.tri([cx - sx, topY, cz - sz], [cx + sx, topY, cz + sz], [tipX, topY - 0.9, tipZ], scale(hex('#2f7d46'), 0.8 + rnd() * 0.3));
    b.tri([cx + sx, topY, cz + sz], [cx - sx, topY, cz - sz], [tipX, topY - 0.9, tipZ], scale(hex('#2f7d46'), 0.6 + rnd() * 0.2));
  }
}

function roundTree(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  b.prism(x, 0, z, 0.25, 0.2, h * 0.45, 5, hex('#5d4630'), { capTop: false });
  const leaf = mix(hex('#2f6a43'), hex('#4f8f52'), rnd());
  b.blob(x, h * 0.62, z, h * 0.34, h * 0.3, h * 0.34, leaf, 7);
  b.blob(x + h * 0.12, h * 0.78, z - h * 0.06, h * 0.22, h * 0.2, h * 0.22, scale(leaf, 1.12), 6);
}

function truck(b: MeshBuilder, x: number, z: number, cab: Rgb): void {
  b.box(x - 1.2, 0.55, z - 4.4, 2.4, 3.0, 6.6, hex('#dfe4ea'), { grad: 0.18 });
  b.box(x - 1.15, 0.55, z + 2.4, 2.3, 2.2, 2.3, cab, { grad: 0.2 });
  b.box(x - 0.95, 1.9, z + 4.72, 1.9, 0.8, 0.06, hex('#7fb9d6'), { shade: false });
  b.box(x - 0.9, 0.8, z + 4.72, 0.5, 0.25, 0.08, WINDOW_WARM, { emissive: 1, shade: false });
  b.box(x + 0.4, 0.8, z + 4.72, 0.5, 0.25, 0.08, WINDOW_WARM, { emissive: 1, shade: false });
  for (const wz of [-3.4, -1.4, 3.4]) for (const sx of [-1.25, 1.05]) b.box(x + sx, 0, z + wz, 0.2, 0.9, 0.9, hex('#1c2128'));
}

function car(b: MeshBuilder, x: number, z: number, color: Rgb): void {
  b.box(x - 0.9, 0.3, z - 2, 1.8, 0.75, 4, color, { grad: 0.2 });
  b.box(x - 0.78, 1.05, z - 0.9, 1.56, 0.6, 2.0, mix(color, hex('#9fd4e6'), 0.55));
  b.box(x - 0.7, 0.5, z + 2.0, 0.4, 0.2, 0.05, WINDOW_WARM, { emissive: 1, shade: false });
  b.box(x + 0.3, 0.5, z + 2.0, 0.4, 0.2, 0.05, WINDOW_WARM, { emissive: 1, shade: false });
  for (const wz of [-1.2, 1.2]) for (const sx of [-1, 0.8]) b.box(x + sx, 0, z + wz, 0.2, 0.55, 0.6, hex('#1c2128'));
}

function lamp(b: MeshBuilder, x: number, z: number): void {
  b.disc(x, 0.045, z, 2.6, 12, mix(GROUND, WINDOW_WARM, 0.1), 0.22);
  b.prism(x, 0, z, 0.1, 0.08, 2.6, 5, hex('#2a313b'), { capTop: false });
  b.box(x - 0.22, 2.6, z - 0.22, 0.44, 0.3, 0.44, WINDOW_WARM, { emissive: 1, shade: false });
}

type Block = Readonly<{ x: number; z: number; w: number; d: number; levels: number; y: number; levelH: number }>;

/** Walls, floor bands, warm windows and a roof for one block of a building. Returns the roof height. */
function drawBlock(b: MeshBuilder, k: Block, wall: Rgb, accent: Rgb, rnd: () => number, roof: 'flat' | 'crown' = 'flat'): number {
  const { x, z, w, d, levels, y, levelH } = k;
  const h = levels * levelH;
  // Dark glass core: lighter towards the top (sky reflection), darker at the base.
  b.box(x, y, z, w, h, d, hex('#25405c'), { skip: ['top', 'bottom'], grad: 0.5 });
  const corner = (cx: number, cz: number) => b.box(cx - 0.35, y, cz - 0.35, 0.7, h, 0.7, wall, { grad: 0.25 });
  corner(x, z); corner(x + w, z); corner(x, z + d); corner(x + w, z + d);
  for (let l = 0; l < levels; l++) {
    const ly = y + l * levelH;
    b.box(x - 0.12, ly, z - 0.12, w + 0.24, 0.26, d + 0.24, wall);
    const wy = ly + levelH * 0.2, wh = levelH * 0.58;
    const lit = 0.55 + 0.45 * ((l + 1) / levels);
    const facade = (len: number, place: (u: number) => readonly [number, number]) => {
      const cols = Math.max(2, Math.floor((len - 1.6) / 2.1));
      const gap = (len - 1.6) / cols;
      for (let i = 0; i < cols; i++) {
        if (rnd() < 0.1) continue;
        const u0 = 0.8 + i * gap + gap * 0.1, u1 = 0.8 + (i + 1) * gap - gap * 0.1;
        const q = place(u0), r = place(u1);
        const tone = mix(WINDOW_WARM, hex('#ffe9b8'), rnd() * 0.7);
        const top = scale(tone, 1), bot = scale(tone, 0.72 * lit + 0.2);
        b.quadV([q[0], wy, q[1]], [q[0], wy + wh, q[1]], [r[0], wy + wh, r[1]], [r[0], wy, r[1]], bot, top, top, bot, 0.9);
        // Mullion under each window: a thin dark sill so the glass reads as framed.
        b.quad([q[0], wy - 0.07, q[1]], [q[0], wy, q[1]], [r[0], wy, r[1]], [r[0], wy - 0.07, r[1]], hex('#0f1a26'));
      }
    };
    facade(w, (u) => [x + u, z - 0.03] as const);
    facade(w, (u) => [x + u, z + d + 0.03] as const);
    facade(d, (u) => [x - 0.03, z + u] as const);
    facade(d, (u) => [x + w + 0.03, z + u] as const);
  }
  const ry = y + h;
  b.box(x - 0.45, ry, z - 0.45, w + 0.9, 0.45, d + 0.9, wall);
  for (const [bx, bz, bw, bd] of [[x - 0.45, z - 0.45, w + 0.9, 0.28], [x - 0.45, z + d + 0.17, w + 0.9, 0.28], [x - 0.45, z - 0.45, 0.28, d + 0.9], [x + w + 0.17, z - 0.45, 0.28, d + 0.9]] as const) {
    b.box(bx, ry + 0.45, bz, bw, 0.32, bd, accent, { emissive: 0.55 });
  }
  if (roof === 'flat') {
    b.box(x + w * 0.15, ry + 0.45, z + d * 0.2, w * 0.26, 1.0, d * 0.28, scale(wall, 0.85));
    b.box(x + w * 0.58, ry + 0.45, z + d * 0.5, w * 0.2, 0.7, d * 0.2, scale(wall, 0.75));
    // Solar panels: a row of tilted dark slabs.
    for (let i = 0; i < 3; i++) b.box(x + w * 0.12 + i * (w * 0.2), ry + 0.45, z + d * 0.72, w * 0.16, 0.25, d * 0.14, hex('#1b3b66'), { emissive: 0.08 });
  }
  return ry + 0.8;
}

function hipRoof(b: MeshBuilder, x: number, z: number, w: number, d: number, y: number, h: number, color: Rgb): void {
  const o = 0.6;
  const ax = x + w / 2, az = z + d / 2;
  const c = [[x - o, z - o], [x + w + o, z - o], [x + w + o, z + d + o], [x - o, z + d + o]];
  for (let i = 0; i < 4; i++) {
    const p = c[i], q = c[(i + 1) % 4];
    b.tri([p[0], y, p[1]], [q[0], y, q[1]], [ax, y + h, az], scale(color, 0.7 + 0.12 * i));
  }
}

function buildingMesh(p: Placement, seed: string): { mesh: SceneMesh; anchor: Vec3; door: Vec3 } {
  const b = new MeshBuilder();
  const rnd = seeded(seed);
  const accent = ACCENT[p.kind];
  const wall = WALL[p.kind];
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2;
  const W = p.width, D = p.depth;
  const podium = 0.5;
  b.box(x0 - 1.2, 0, z0 - 1.2, W + 2.4, podium, D + 2.4, STONE);
  const y0 = podium;
  const levelH = (p.height - podium - 1.2) / (p.kind === 'executive' ? p.levels : p.levels);
  let top = 0;
  let doorX = p.x;
  const L = p.levels;
  if (p.kind === 'executive') {
    // Two-storey glass podium, a slim tower and a stepped crown: the one building that reads as the tower.
    const baseH = 4.4;
    top = Math.max(top, drawBlock(b, { x: x0, z: z0, w: W, d: D, levels: 1, y: y0, levelH: baseH }, wall, accent, rnd));
    const tw = W * 0.7, td = D * 0.7, tx = x0 + (W - tw) / 2, tz = z0 + (D - td) / 2;
    const towerLevels = Math.max(2, L - 1);
    const tLevelH = (p.height - y0 - baseH - 4.5) / towerLevels;
    top = Math.max(top, drawBlock(b, { x: tx, z: tz, w: tw, d: td, levels: towerLevels, y: y0 + baseH + 0.6, levelH: Math.max(2.8, tLevelH) }, wall, accent, rnd, 'crown'));
    const cy = y0 + baseH + 0.6 + towerLevels * Math.max(2.8, tLevelH) + 0.8;
    top = Math.max(top, drawBlock(b, { x: tx + tw * 0.2, z: tz + td * 0.2, w: tw * 0.6, d: td * 0.6, levels: 2, y: cy, levelH: 2.8 }, wall, accent, rnd, 'crown'));
    const my = cy + 5.6 + 0.8;
    b.box(p.x - 1.6, my, p.z - 1.6, 3.2, 1.0, 3.2, accent, { emissive: 0.7 });
    b.prism(p.x, my + 1.0, p.z, 0.2, 0.05, 7, 5, hex('#9aa5b1'), { capTop: false });
    b.box(p.x - 0.2, my + 8, p.z - 0.2, 0.4, 0.4, 0.4, hex('#ff5a5a'), { emissive: 1, shade: false });
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
    hipRoof(b, x0 + 0.8, z0 + 0.8, W - 1.6, D - 1.6, ry, 2.4, mix(accent, hex('#3a2f4a'), 0.55));
    top = ry + 2.6;
  }
  // Entrance canopy and a warm door on the south face of the main block.
  const doorZ = z0 + (p.kind === 'business' ? D * 0.8 : p.kind === 'personal' ? D - 0.8 : p.kind === 'executive' ? D : D);
  b.box(doorX - 2.4, y0 + 3.1, doorZ, 4.8, 0.3, 2.2, accent, { emissive: 0.4 });
  b.box(doorX - 1.1, y0, doorZ + 0.02, 2.2, 3.0, 0.12, WINDOW_WARM, { emissive: 1, shade: false });
  b.box(doorX - 2.3, y0, doorZ + 1.9, 0.18, 3.1, 0.18, wall);
  b.box(doorX + 2.1, y0, doorZ + 1.9, 0.18, 3.1, 0.18, wall);
  b.box(doorX - 2.4, 0, doorZ + 0.2, 4.8, 0.3, 2.6, mix(STONE, hex('#ffffff'), 0.2));
  return {
    mesh: freeze(b),
    anchor: { x: p.x, y: top + 1.2, z: p.z },
    door: { x: doorX, y: 0, z: doorZ + 3.2 },
  };
}

export function buildCampusScene(placements: readonly Placement[]): CampusScene {
  const extent = campusExtent(placements);
  const groundRadius = Math.max(120, extent.radius + 70);
  const rnd = seeded('anum-mine-campus');
  const exec = placements.find((p) => p.kind === 'executive');
  const monument: Vec3 = exec ? { x: exec.x, y: 0, z: exec.z + exec.depth / 2 + 15 } : { x: 0, y: 0, z: 0 };

  const s = new MeshBuilder();
  // Ground: a square slab plus a darker verge so the edge never reads as a cut-off.
  s.plane(-groundRadius, -0.05, -groundRadius, groundRadius * 2, groundRadius * 2, GROUND);
  s.plane(-groundRadius, -0.049, -groundRadius, groundRadius * 2, 6, scale(GROUND, 0.85));
  // Distant hills ring (read as a skyline; fog softens them).
  const hillRnd = seeded('anum-hills');
  for (let i = 0; i < 20; i++) {
    const a = (i / 20) * Math.PI * 2 + hillRnd() * 0.2;
    const r = groundRadius * 1.02;
    s.prism(Math.cos(a) * r, -0.05, Math.sin(a) * r, 7 + hillRnd() * 7, 0, 5 + hillRnd() * 6, 6, mix(hex('#4a6a52'), hex('#5f7479'), hillRnd()), { capTop: false });
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
  // Fountain jets and a lit ring around the pond.
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2;
    s.prism(monument.x + Math.cos(a) * 4.7, 0.1, monument.z + Math.sin(a) * 4.7, 0.16, 0.02, 1.6, 4, hex('#bfe6ff'), { emissive: 0.5, capTop: false });
  }
  s.prism(monument.x, 0.12, monument.z, 6.3, 6.3, 0.12, 28, hex('#7fd8ff'), { emissive: 0.8 });
  // Ring road and radial walkways to every front door.
  const buildings: CampusBuildingMesh[] = [];
  const segments: Array<[number, number, number, number]> = [];
  for (const p of placements) {
    const built = buildingMesh(p, 'bld:' + p.workspaceId);
    buildings.push({
      workspaceId: p.workspaceId, mesh: built.mesh, anchor: built.anchor, door: built.door,
      box: { min: { x: p.x - p.width / 2 - 1.2, y: 0, z: p.z - p.depth / 2 - 1.2 }, max: { x: p.x + p.width / 2 + 1.2, y: built.mesh.bounds.max.y, z: p.z + p.depth / 2 + 1.2 } },
    });
    const from: [number, number] = [monument.x, monument.z + 10];
    if (p.kind === 'executive') {
      segments.push([p.x, p.z + p.depth / 2 + 3, monument.x, monument.z - 10]);
    } else {
      segments.push([built.door.x, built.door.z, from[0], from[1]]);
    }
  }
  // Lawn and contact patches under each building so it sits on the ground, not above it.
  for (const p of placements) s.plane(p.x - p.width / 2 - 3.5, 0.005, p.z - p.depth / 2 - 3.5, p.width + 7, p.depth + 7, scale(GROUND, 0.72));
  for (const [x0, z0, x1, z1] of segments) path(s, x0, z0, x1, z1, 3.2, 0.03, STONE);
  if (extent.radius > 30) {
    const sides = 48;
    const rr = Math.max(26, extent.radius * 0.62);
    for (let i = 0; i < sides; i++) {
      const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2;
      path(s, Math.cos(a0) * rr, Math.sin(a0) * rr, Math.cos(a1) * rr, Math.sin(a1) * rr, 3, 0.025, scale(STONE, 0.9));
    }
  }
  // Ponds in the free gaps, then trees and lamps that avoid buildings, paths and water.
  const ponds: Array<[number, number, number]> = [];
  const pondCount = Math.min(3, Math.max(1, Math.floor(placements.length / 3)));
  for (let i = 0; i < pondCount; i++) {
    const a = Math.PI * 0.2 + (i / pondCount) * Math.PI * 2 + 0.5;
    const r = Math.max(24, extent.radius * 0.45);
    ponds.push([Math.cos(a) * r, Math.sin(a) * r, 5 + rnd() * 2.5]);
  }
  const blocked = (x: number, z: number, margin: number): boolean => {
    for (const p of placements) {
      if (Math.abs(x - p.x) < p.width / 2 + margin + 2 && Math.abs(z - p.z) < p.depth / 2 + margin + 2) return true;
    }
    if (Math.hypot(x - monument.x, z - monument.z) < 13) return true;
    for (const [px, pz, pr] of ponds) if (Math.hypot(x - px, z - pz) < pr + margin) return true;
    for (const [x0, z0, x1, z1] of segments) if (distanceToSegment(x, z, x0, z0, x1, z1) < 3 + margin) return true;
    return false;
  };
  for (const [px, pz, pr] of ponds) {
    s.disc(px, 0.015, pz, pr + 0.7, 18, scale(STONE, 0.8));
    s.disc(px, 0.05, pz, pr, 18, WATER, 0.08);
  }
  const treeCount = Math.min(150, 40 + placements.length * 8);
  let placed = 0, attempts = 0;
  while (placed < treeCount && attempts < treeCount * 12) {
    attempts += 1;
    const a = rnd() * Math.PI * 2;
    const r = 8 + Math.sqrt(rnd()) * (groundRadius * 0.72);
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (blocked(x, z, 1.5)) continue;
    if (rnd() < 0.55) palm(s, x, z, 5 + rnd() * 2.4, rnd);
    else roundTree(s, x, z, 3.6 + rnd() * 2.2, rnd);
    placed += 1;
  }
  // A truck yard and a car park at free spots outside the ring road, so the campus reads as a working place.
  const carColors = [hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#3d6b52'), hex('#e8ebee')];
  const spots: Array<[number, number]> = [];
  for (let i = 0; i < 24 && spots.length < 2; i++) {
    const a = Math.PI * 0.15 + i * 0.55, r = extent.radius + 14;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (![0, 1].some((k) => blocked(x + k * 5.5, z, 5)) && !blocked(x, z + 6, 3) && !blocked(x, z - 6, 3) && spots.every(([sx, sz]) => Math.hypot(sx - x, sz - z) > 30)) spots.push([x, z]);
  }
  if (spots[0]) {
    const [yx, yz] = spots[0];
    s.plane(yx - 4, 0.012, yz - 10, 13, 20, scale(STONE, 0.7));
    truck(s, yx, yz, hex('#2f6a8f'));
    truck(s, yx + 5.5, yz, hex('#b4452f'));
  }
  if (spots[1]) {
    const [px, pz] = spots[1];
    s.plane(px - 3, 0.012, pz - 6, 14, 12, scale(STONE, 0.62));
    for (let i = 0; i < 5; i++) car(s, px + i * 2.8, pz, carColors[i % carColors.length]);
  }
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
