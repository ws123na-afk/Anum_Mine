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

function lamp(b: MeshBuilder, x: number, z: number): void {
  b.prism(x, 0, z, 0.1, 0.08, 2.6, 5, hex('#2a313b'), { capTop: false });
  b.box(x - 0.22, 2.6, z - 0.22, 0.44, 0.3, 0.44, WINDOW_WARM, { emissive: 1, shade: false });
}

function buildingMesh(p: Placement, seed: string): { mesh: SceneMesh; anchor: Vec3; door: Vec3 } {
  const b = new MeshBuilder();
  const rnd = seeded(seed);
  const accent = ACCENT[p.kind];
  const wall = WALL[p.kind];
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2;
  const podium = 0.5;
  // Podium and plinth.
  b.box(x0 - 1.2, 0, z0 - 1.2, p.width + 2.4, podium, p.depth + 2.4, STONE);
  const bodyY = podium;
  const bodyH = p.height - podium - 1.0;
  // Dark glass core that the warm windows sit on.
  b.box(x0, bodyY, z0, p.width, bodyH, p.depth, GLASS_DARK, { skip: ['top', 'bottom'] });
  // Pilasters on the corners give the volume an edge.
  const corner = (cx: number, cz: number) => b.box(cx - 0.4, bodyY, cz - 0.4, 0.8, bodyH, 0.8, wall);
  corner(x0, z0); corner(x0 + p.width, z0); corner(x0, z0 + p.depth); corner(x0 + p.width, z0 + p.depth);
  const levelH = bodyH / p.levels;
  // Floor bands and warm windows per level and per facade.
  for (let l = 0; l < p.levels; l++) {
    const y = bodyY + l * levelH;
    b.box(x0 - 0.15, y, z0 - 0.15, p.width + 0.3, 0.28, p.depth + 0.3, wall);
    const wy = y + levelH * 0.22, wh = levelH * 0.56;
    const facade = (len: number, place: (u: number) => readonly [number, number]) => {
      const cols = Math.max(2, Math.floor((len - 2) / 2.5));
      const gap = (len - 2) / cols;
      for (let i = 0; i < cols; i++) {
        if (rnd() < 0.12) continue; // a few lights off: it reads as a lived-in building
        const u0 = 1 + i * gap + gap * 0.12, u1 = 1 + (i + 1) * gap - gap * 0.12;
        const q = place(u0);
        const r = place(u1);
        const tone = mix(WINDOW_WARM, hex('#ffe8b0'), rnd() * 0.6);
        b.quad([q[0], wy, q[1]], [q[0], wy + wh, q[1]], [r[0], wy + wh, r[1]], [r[0], wy, r[1]], tone, 0.9);
      }
    };
    // North (z0 face), south (z0+depth), west, east. Offsets of 0.03 keep windows in front of the core.
    facade(p.width, (u) => [x0 + u, z0 - 0.03] as const);
    facade(p.width, (u) => [x0 + u, z0 + p.depth + 0.03] as const);
    facade(p.depth, (u) => [x0 - 0.03, z0 + u] as const);
    facade(p.depth, (u) => [x0 + p.width + 0.03, z0 + u] as const);
  }
  // Roof slab, parapet, plant room and a tinted crown in the building's accent colour.
  const roofY = bodyY + bodyH;
  b.box(x0 - 0.5, roofY, z0 - 0.5, p.width + 1, 0.5, p.depth + 1, wall);
  b.box(x0 - 0.5, roofY + 0.5, z0 - 0.5, p.width + 1, 0.35, 0.3, accent, { emissive: 0.5 });
  b.box(x0 - 0.5, roofY + 0.5, z0 + p.depth + 0.2, p.width + 1, 0.35, 0.3, accent, { emissive: 0.5 });
  b.box(x0 - 0.5, roofY + 0.5, z0 - 0.5, 0.3, 0.35, p.depth + 1, accent, { emissive: 0.5 });
  b.box(x0 + p.width + 0.2, roofY + 0.5, z0 - 0.5, 0.3, 0.35, p.depth + 1, accent, { emissive: 0.5 });
  b.box(x0 + p.width * 0.18, roofY + 0.5, z0 + p.depth * 0.2, p.width * 0.28, 1.1, p.depth * 0.3, scale(wall, 0.85));
  b.box(x0 + p.width * 0.6, roofY + 0.5, z0 + p.depth * 0.5, p.width * 0.2, 0.8, p.depth * 0.22, scale(wall, 0.75));
  let top = roofY + 1.6;
  if (p.kind === 'executive') {
    // Stepped crown and mast: the one building that reads as the tower.
    b.box(x0 + p.width * 0.2, roofY + 0.5, z0 + p.depth * 0.2, p.width * 0.6, 2.4, p.depth * 0.6, scale(wall, 0.92));
    b.box(x0 + p.width * 0.3, roofY + 2.9, z0 + p.depth * 0.3, p.width * 0.4, 1.4, p.depth * 0.4, accent, { emissive: 0.55 });
    b.prism(p.x, roofY + 4.3, p.z, 0.22, 0.05, 6, 5, hex('#9aa5b1'), { capTop: false });
    b.box(p.x - 0.18, roofY + 10.2, p.z - 0.18, 0.36, 0.36, 0.36, hex('#ff5a5a'), { emissive: 1, shade: false });
    top = roofY + 11;
  } else if (p.kind === 'personal') {
    // A pitched look: tinted roof lid.
    b.box(x0 + 0.5, roofY + 0.5, z0 + 0.5, p.width - 1, 0.5, p.depth - 1, accent, { emissive: 0.15 });
    top = roofY + 1.6;
  }
  // Entrance canopy and warm door on the south face.
  const doorX = p.x, doorZ = z0 + p.depth;
  b.box(doorX - 2.4, bodyY + 3.1, doorZ, 4.8, 0.3, 2.2, accent, { emissive: 0.35 });
  b.box(doorX - 1.1, bodyY, doorZ + 0.02, 2.2, 3.0, 0.12, WINDOW_WARM, { emissive: 1, shade: false });
  b.box(doorX - 2.3, bodyY, doorZ + 1.9, 0.18, 3.1, 0.18, wall);
  b.box(doorX + 2.1, bodyY, doorZ + 1.9, 0.18, 3.1, 0.18, wall);
  // Steps.
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
