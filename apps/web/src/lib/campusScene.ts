/**
 * Procedural campus: ground, walkways, a ring road and gate, a canal with bridges, ponds, a tennis
 * court, planting, the central monument and one building per placement (each drawn as a type from
 * `campusBuildings.ts`). Every shape is generated from the placements and a fixed seed, so a campus
 * always looks the same for the same set of buildings. No data other than the layout goes in:
 * names, counts and statuses are HTML overlays drawn from the campus model, and the lettering on
 * the signs is drawn from the real names at render time.
 */
import { MAT, MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import {
  ASPHALT, GROUND, STONE, TEAL, WATER, anumMark, bench, bridge, bush, canalPiece, car, cypress, flatPoly, hedge, lamp, palm, parkingLot, pool,
  roundTree, skylineTower, tennisCourt, truck,
} from './hqProps.ts';
import { archetypeOf, drawBuilding, type Archetype } from './campusBuildings.ts';
import { drawExterior } from './campusExteriors.ts';
import { signAtlasLayout } from './hqTextures.ts';
import { campusExtent, type Placement } from './campusModel.ts';
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type SceneMesh = Readonly<{ vertices: Float32Array; bounds: Aabb; triangles: number }>;
export type CampusBuildingMesh = Readonly<{
  workspaceId: string; mesh: SceneMesh; box: Aabb; anchor: Vec3; door: Vec3;
  /** Index of this building's sign cell (see `signAtlasLayout`); the stage draws the real name there. */
  signIndex: number;
  archetype: Archetype;
}>;
export type CampusScene = Readonly<{
  scenery: SceneMesh;
  buildings: readonly CampusBuildingMesh[];
  monument: Vec3;
  extent: ReturnType<typeof campusExtent>;
  groundRadius: number;
  triangles: number;
  /** Sign cells: one per building (in placement order) and one for the gate. */
  signCount: number;
  /** Radius of the canal ring round the middle of the campus, or 0 when no ring clears every building. */
  canalRadius: number;
  /** The region that casts and receives shadows. */
  shadow: Readonly<{ center: Vec3; radius: number }>;
}>;

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

/** Parameters t in [0, 1] where the segment crosses the circle of radius r round the origin. */
function circleCrossings(x0: number, z0: number, x1: number, z1: number, r: number): number[] {
  const dx = x1 - x0, dz = z1 - z0;
  const a = dx * dx + dz * dz, bq = 2 * (x0 * dx + z0 * dz), c = x0 * x0 + z0 * z0 - r * r;
  const disc = bq * bq - 4 * a * c;
  if (a < 1e-9 || disc < 0) return [];
  const s = Math.sqrt(disc);
  return [(-bq - s) / (2 * a), (-bq + s) / (2 * a)].filter((t) => t >= 0 && t <= 1);
}

export function buildCampusScene(placements: readonly Placement[]): CampusScene {
  const extent = campusExtent(placements);
  const groundRadius = Math.max(130, extent.radius + 80);
  const rnd = seeded('anum-mine-campus');
  const exec = placements.find((p) => p.kind === 'executive');
  const monument: Vec3 = exec ? { x: exec.x, y: 0, z: exec.z + exec.depth / 2 + 15 } : { x: 0, y: 0, z: 0 };
  const roadR = Math.max(44, extent.radius + 12);
  const signCount = placements.length + 1;

  const s = new MeshBuilder();
  // Ground: a grass slab with a tonal mottle, and a darker verge so the edge never reads as a cut-off.
  s.mat = MAT.grass;
  s.plane(-groundRadius, -0.05, -groundRadius, groundRadius * 2, groundRadius * 2, GROUND, 0.06);
  s.plane(-groundRadius, -0.049, -groundRadius, groundRadius * 2, 6, scale(GROUND, 0.85));
  const mottle = seeded('anum-lawn');
  for (let i = 0; i < 46; i++) {
    const a = mottle() * Math.PI * 2, r = Math.sqrt(mottle()) * groundRadius * 0.9;
    s.disc(Math.cos(a) * r, 0.003, Math.sin(a) * r, 7 + mottle() * 12, 14, scale(GROUND, 0.9 + mottle() * 0.25));
  }
  s.mat = MAT.flat;
  // A far skyline (boxes with lit bands): a city beyond the campus.
  const sky = seeded('anum-skyline');
  for (let i = 0; i < 46; i++) {
    const a = (i / 46) * Math.PI * 2 + sky() * 0.1;
    const r = groundRadius * (1.0 + sky() * 0.16);
    skylineTower(s, Math.cos(a) * r, Math.sin(a) * r, 6 + sky() * 9, 6 + sky() * 9, 14 + sky() * 46, sky);
  }
  // Plaza and monument.
  s.withMat(MAT.tile, () => {
    s.disc(monument.x, 0.02, monument.z, 11, 28, mix(STONE, hex('#ffffff'), 0.1));
    s.disc(monument.x, 0.06, monument.z, 7.2, 24, mix(STONE, hex('#8a8577'), 0.4));
  });
  s.withMat(MAT.water, () => s.disc(monument.x, 0.1, monument.z, 6.2, 24, WATER, 0.05));
  s.withMat(MAT.concrete, () => {
    s.prism(monument.x, 0, monument.z, 2.2, 1.6, 1.6, 12, STONE);
    s.prism(monument.x, 1.6, monument.z, 0.9, 0.7, 2.6, 8, mix(STONE, hex('#ffffff'), 0.3));
  });
  s.withMat(MAT.paint, () => s.blob(monument.x, 5.4, monument.z, 2.1, 2.1, 2.1, hex('#3a8fb8'), 12));
  s.prism(monument.x, 3.3, monument.z, 2.4, 2.4, 0.18, 24, hex('#d9b25f'), { emissive: 0.9 });
  s.prism(monument.x, 4.1, monument.z, 2.7, 2.7, 0.14, 24, hex('#45d0b5'), { emissive: 0.95 });
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2;
    s.prism(monument.x + Math.cos(a) * 4.7, 0.1, monument.z + Math.sin(a) * 4.7, 0.16, 0.02, 1.6, 5, hex('#cfeaff'), { emissive: 0.92, capTop: false });
  }
  s.withMat(MAT.water, () => s.prism(monument.x, 0.12, monument.z, 6.3, 6.3, 0.1, 28, hex('#7fd8ff'), { emissive: 0.4 }));
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + 0.2;
    bench(s, monument.x + Math.cos(a) * 9.2, monument.z + Math.sin(a) * 9.2, Math.abs(Math.cos(a)) > 0.7 ? 'z' : 'x');
    if (i % 2 === 0) lamp(s, monument.x + Math.cos(a + 0.4) * 10.2, monument.z + Math.sin(a + 0.4) * 10.2);
  }

  // Buildings, their walkways, and the ring road.
  const buildings: CampusBuildingMesh[] = [];
  const segments: Array<[number, number, number, number]> = [];
  placements.forEach((p, index) => {
    const archetype = archetypeOf(p);
    const asset = drawExterior(p, archetype, index, signCount);
    let mesh: SceneMesh, anchor: Vec3, door: Vec3;
    if (asset) {
      mesh = { vertices: asset.vertices, bounds: asset.bounds, triangles: asset.triangles };
      anchor = asset.anchor; door = asset.door;
    } else {
      const b = new MeshBuilder();
      const shape = drawBuilding(b, p, 'bld:' + p.workspaceId, index, signCount);
      mesh = freeze(b); anchor = shape.anchor; door = shape.door;
    }
    buildings.push({
      workspaceId: p.workspaceId, mesh, anchor, door, signIndex: index, archetype,
      box: { min: { x: p.x - p.width / 2 - 1.2, y: 0, z: p.z - p.depth / 2 - 1.2 }, max: { x: p.x + p.width / 2 + 1.2, y: mesh.bounds.max.y, z: p.z + p.depth / 2 + 1.2 } },
    });
    const from: [number, number] = [monument.x, monument.z + 10];
    if (p.kind === 'executive') segments.push([p.x, p.z + p.depth / 2 + 3, monument.x, monument.z - 10]);
    else segments.push([door.x, door.z, from[0], from[1]]);
  });
  // Lawn under each building so it sits on the ground, not above it.
  s.mat = MAT.grass;
  for (const p of placements) s.plane(p.x - p.width / 2 - 3.5, 0.005, p.z - p.depth / 2 - 3.5, p.width + 7, p.depth + 7, scale(GROUND, 0.86), 0.04);
  s.mat = MAT.tile;
  for (const [x0, z0, x1, z1] of segments) path(s, x0, z0, x1, z1, 3.2, 0.03, STONE, 0.1);
  if (extent.radius > 30) {
    const sides = 48;
    const rr = Math.max(26, extent.radius * 0.62);
    for (let i = 0; i < sides; i++) {
      const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2;
      path(s, Math.cos(a0) * rr, Math.sin(a0) * rr, Math.cos(a1) * rr, Math.sin(a1) * rr, 3, 0.025, scale(STONE, 0.9), 0.08);
    }
  }
  s.mat = MAT.asphalt;
  // Ring road: asphalt, kerbs and a dashed centre line.
  const roadSides = 72;
  const gateAngle = Math.PI / 2;
  for (let i = 0; i < roadSides; i++) {
    const a0 = (i / roadSides) * Math.PI * 2, a1 = ((i + 1) / roadSides) * Math.PI * 2;
    const p0: [number, number] = [Math.cos(a0) * roadR, Math.sin(a0) * roadR], p1: [number, number] = [Math.cos(a1) * roadR, Math.sin(a1) * roadR];
    path(s, p0[0], p0[1], p1[0], p1[1], 7.2, 0.035, ASPHALT, 0.03);
    for (const k of [-4.2, 4.2]) {
      const q0: [number, number] = [Math.cos(a0) * (roadR + k), Math.sin(a0) * (roadR + k)], q1: [number, number] = [Math.cos(a1) * (roadR + k), Math.sin(a1) * (roadR + k)];
      s.withMat(MAT.tile, () => path(s, q0[0], q0[1], q1[0], q1[1], 0.7, 0.045, scale(STONE, 1.05)));
    }
    if (i % 2 === 0) path(s, p0[0], p0[1], (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, 0.22, 0.05, hex('#e8e4d0'));
  }
  s.mat = MAT.flat;
  // Traffic frozen in place: cars on the ring road, nose along the lane.
  const carColors = [hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#3d6b52'), hex('#e8ebee'), hex('#1f2429')];
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2 + 0.3 + rnd() * 0.2;
    if (Math.abs((a % (Math.PI * 2)) - gateAngle) < 0.25) continue;
    const lane = i % 2 === 0 ? 1.7 : -1.7;
    const cx = Math.cos(a) * (roadR + lane), cz = Math.sin(a) * (roadR + lane);
    const tx = -Math.sin(a) * (lane > 0 ? -1 : 1), tz = Math.cos(a) * (lane > 0 ? -1 : 1);
    const rot = Math.abs(tx) > Math.abs(tz) ? (tx > 0 ? 3 : 1) : (tz > 0 ? 0 : 2);
    car(s, cx, cz, carColors[i % carColors.length], rot);
  }
  // Entrance gate on the south side: two piers, a lit lintel with the lettering cell, a short road in, low walls and planting.
  const gz = roadR + 10;
  s.withMat(MAT.asphalt, () => path(s, 0, roadR, 0, gz, 7.2, 0.036, ASPHALT));
  s.withMat(MAT.concrete, () => {
    for (const sx of [-6.2, 4.6]) {
      s.box(sx, 0, gz - 1.2, 1.6, 6.2, 2.4, hex('#b8b0a2'), { grad: 0.12 });
      s.box(sx - 0.2, 6.2, gz - 1.4, 2.0, 0.4, 2.8, hex('#8d867a'));
    }
    s.box(-6.4, 5.4, gz - 1.3, 13.2, 1.6, 2.6, hex('#0e1b26'));
  });
  const gateCell = signAtlasLayout(signCount).cell(signCount - 1);
  s.withMat(MAT.sign, () => {
    s.quadUV([-4.2, 5.45, gz + 1.32], [3.0, 5.45, gz + 1.32], [3.0, 6.9, gz + 1.32], [-4.2, 6.9, gz + 1.32], [gateCell.u0, gateCell.v0], [gateCell.u1, gateCell.v0], [gateCell.u1, gateCell.v1], [gateCell.u0, gateCell.v1], [1, 1, 1], 0.95);
  });
  s.box(-6.4, 5.4, gz + 1.3, 13.2, 0.08, 0.06, TEAL, { emissive: 0.9, shade: false });
  anumMark(s, -5.2, 6.1, gz + 1.34, 1.1);
  s.withMat(MAT.concrete, () => {
    for (const [x0, x1] of [[-44, -7], [7, 44]] as const) {
      s.box(x0, 0, gz - 0.4, x1 - x0, 1.1, 0.8, hex('#b8b0a2'));
    }
  });
  for (const [x0, x1] of [[-44, -7], [7, 44]] as const) for (let x = x0 + 2; x < x1 - 1; x += 3.6) hedge(s, x, gz + 0.5, 2.4, 0.8, 1.0);

  // A canal ring between the buildings and the road, drawn only where it clears every building and the plaza, with a
  // bridge wherever a walkway crosses it.
  const canalW = 4;
  const canalR = Math.max(24, roadR - 10.5);
  const nearBuilding = (x: number, z: number, margin: number) => placements.some((p) => Math.abs(x - p.x) < p.width / 2 + margin && z > p.z - p.depth / 2 - margin && z < p.z + p.depth / 2 + margin + 7);
  const canalFree = (x: number, z: number) => !nearBuilding(x, z, 5) && Math.hypot(x - monument.x, z - monument.z) > 15;
  let canalPieces = 0;
  {
    const sides = 72;
    for (let i = 0; i < sides; i++) {
      const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2, am = (a0 + a1) / 2;
      const p0: [number, number] = [Math.cos(a0) * canalR, Math.sin(a0) * canalR], p1: [number, number] = [Math.cos(a1) * canalR, Math.sin(a1) * canalR];
      if (!canalFree(p0[0], p0[1]) || !canalFree(p1[0], p1[1]) || !canalFree(Math.cos(am) * canalR, Math.sin(am) * canalR)) continue;
      canalPiece(s, p0[0], p0[1], p1[0], p1[1], canalW);
      canalPieces += 1;
    }
    for (const [x0, z0, x1, z1] of segments) {
      for (const t of circleCrossings(x0, z0, x1, z1, canalR)) {
        const bx = x0 + (x1 - x0) * t, bz = z0 + (z1 - z0) * t;
        const ang = Math.atan2(bz, bx), step = (Math.PI * 2) / sides;
        const onWater = [-1, 0, 1].every((k) => canalFree(Math.cos(ang + k * step) * canalR, Math.sin(ang + k * step) * canalR));
        if (onWater) bridge(s, bx, bz, x1 - x0, z1 - z0, canalW + 3.2, 3.8);
      }
    }
  }
  const canalDrawn = canalPieces > 6 ? canalR : 0;

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
    if (canalDrawn > 0 && Math.abs(Math.hypot(x, z) - canalDrawn) < canalW / 2 + 3 + margin) return true;
    if (Math.abs(x) < 8 + margin && z > roadR - 2 && z < gz + 3) return true;
    for (const [lx, lz, lw, ld] of lots) if (x > lx - margin && x < lx + lw + margin && z > lz - margin && z < lz + ld + margin) return true;
    return false;
  };
  for (const [px, pz, pr] of ponds) pool(s, px, pz, pr, rnd);
  // Car parks beside buildings (west side) where the ground is free.
  for (const p of placements) {
    if (p.kind === 'executive' || rnd() < 0.3) continue;
    const lx = p.x - p.width / 2 - 14.5, lz = p.z - p.depth / 2 + 1;
    if (blocked(lx + 4, lz + 3, 1) || Math.hypot(lx, lz) > roadR - 6) continue;
    lots.push([lx, lz, 9.7, 8]);
    parkingLot(s, lx, lz, 3, 1, rnd, 0.75);
  }
  // A tennis court at the first free spot outside the ring road.
  let court: [number, number, 0 | 1] | null = null;
  for (let i = 0; i < 48 && !court; i++) {
    const a = 0.9 + i * 0.41, r = roadR + 26;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    const rot: 0 | 1 = Math.abs(x) > Math.abs(z) ? 1 : 0;
    if (!blocked(x, z, 14) && !blocked(x + 14, z, 10) && !blocked(x - 14, z, 10) && !blocked(x, z + 12, 10) && !blocked(x, z - 12, 10)) court = [x, z, rot];
  }
  if (court) {
    tennisCourt(s, court[0], court[1], court[2]);
    lots.push([court[0] - 18, court[1] - 18, 36, 36]);
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
  // A truck yard and larger car parks at free spots outside the ring road.
  const spots: Array<[number, number]> = [];
  for (let i = 0; i < 30 && spots.length < 3; i++) {
    const a = Math.PI * 0.12 + i * 0.5, r = roadR + 17;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (![0, 1, 2].some((k) => blocked(x + k * 5.5, z, 4)) && !blocked(x, z + 8, 3) && !blocked(x, z - 8, 3) && spots.every(([sx, sz]) => Math.hypot(sx - x, sz - z) > 34)) spots.push([x, z]);
  }
  if (spots[0]) {
    const [yx, yz] = spots[0];
    s.withMat(MAT.asphalt, () => s.plane(yx - 5, 0.012, yz - 11, 18, 22, scale(ASPHALT, 1.08)));
    truck(s, yx, yz, hex('#2f6a8f'));
    truck(s, yx + 6, yz + 1, hex('#b4452f'));
    truck(s, yx + 12, yz - 1, hex('#3d6b52'));
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
  return {
    scenery: freeze(s), buildings, monument: { x: monument.x, y: 8.2, z: monument.z }, extent, groundRadius, triangles: total, signCount, canalRadius: canalDrawn,
    shadow: { center: { x: 0, y: 0, z: 0 }, radius: Math.max(70, roadR + 22) },
  };
}
