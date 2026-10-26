/**
 * Procedural campus: ground, walkways, a ring road and gate, a canal with bridges, ponds, a tennis
 * court, planting, the central monument and one building per placement (each drawn as a type from
 * `campusBuildings.ts`). Every shape is generated from the placements and a fixed seed, so a campus
 * always looks the same for the same set of buildings. No data other than the layout goes in:
 * names, counts and statuses are HTML overlays drawn from the campus model, and the lettering on
 * the signs is drawn from the real names at render time.
 */
import { MAT, MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import { GROUND, STONE, WATER, bench, flatPoly, lamp } from './hqProps.ts';
import { buildingGarden, globeMonument, campusEnvironment, carPark, carParkSize, pond, ripplesOf, tennisCourt, tree, trafficPlan, truck, van, type AmbientPlan, type Exclusion } from '../hqAssets/environment/index.ts';
import { classifySurfaces } from './hqSurfaces.ts';
import { archetypeOf, drawBuilding, type Archetype } from './campusBuildings.ts';
import { drawExterior } from './campusExteriors.ts';
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
  /** Sign cells: one per building, in placement order. */
  signCount: number;
  /** Radius of the canal ring round the middle of the campus, or 0 when no ring clears every building. */
  canalRadius: number;
  /** The region that casts and receives shadows. */
  shadow: Readonly<{ center: Vec3; radius: number }>;
  /** Soft light halos (x, y, z, radius, r, g, b, strength per halo) for the night glow. */
  halos: Float32Array;
  /** Scenery that moves for its own sake (traffic loops, water ripples); never read from a record. */
  ambient: AmbientPlan;
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
  // The ring road, canal and fence of the environment kit surround the buildings; the ground reaches past the skyline.
  const ringHalf = Math.max(56, extent.radius + 28);
  const fenceHalf = ringHalf + 24 + 14;
  const groundRadius = fenceHalf + 150;
  const rnd = seeded('anum-mine-campus');
  const exec = placements.find((p) => p.kind === 'executive');
  const monument: Vec3 = exec ? { x: exec.x, y: 0, z: exec.z + exec.depth / 2 + 15 } : { x: 0, y: 0, z: 0 };
  const signCount = placements.length;

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
  // Plaza and monument: a globe in a pool on a paved plaza with benches and lamps.
  globeMonument(s, monument.x, monument.z);
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + 0.2;
    bench(s, monument.x + Math.cos(a) * 14.2, monument.z + Math.sin(a) * 14.2, Math.abs(Math.cos(a)) > 0.7 ? 'z' : 'x');
    if (i % 2 === 0) lamp(s, monument.x + Math.cos(a + 0.4) * 15.6, monument.z + Math.sin(a + 0.4) * 15.6);
  }

  // Buildings, their walkways, and the ring road.
  const buildings: CampusBuildingMesh[] = [];
  const buildingHalos: number[] = [];
  const segments: Array<[number, number, number, number]> = [];
  placements.forEach((p, index) => {
    const archetype = archetypeOf(p);
    const asset = drawExterior(p, archetype, index, signCount);
    let mesh: SceneMesh, anchor: Vec3, door: Vec3;
    if (asset) {
      mesh = { vertices: asset.vertices, bounds: asset.bounds, triangles: asset.triangles };
      anchor = asset.anchor; door = asset.door;
      buildingHalos.push(...asset.halos);
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
  // Lawn under each building so it sits on the ground, not above it, and a garden round it.
  s.mat = MAT.grass;
  for (const p of placements) s.plane(p.x - p.width / 2 - 6.5, 0.005, p.z - p.depth / 2 - 6.5, p.width + 13, p.depth + 22, scale(GROUND, 0.9), 0.04);
  s.mat = MAT.flat;
  placements.forEach((p, i) => {
    const bd = buildings[i];
    buildingGarden(s, { x: p.x - p.width / 2, z: p.z - p.depth / 2, w: p.width, d: p.depth + 2 }, { x: bd.door.x, z: bd.door.z }, { seed: 'garden:' + p.workspaceId, lawn: scale(GROUND, 1.0) });
  });
  s.mat = MAT.tile;
  for (const [x0, z0, x1, z1] of segments) { path(s, x0, z0, x1, z1, 4.6, 0.03, mix(STONE, hex('#ffffff'), 0.15), 0.3); path(s, x0, z0, x1, z1, 4.9, 0.026, scale(STONE, 0.7), 0.15); }
  if (extent.radius > 30) {
    const sides = 48;
    const rr = Math.max(26, extent.radius * 0.62);
    for (let i = 0; i < sides; i++) {
      const a0 = (i / sides) * Math.PI * 2, a1 = ((i + 1) / sides) * Math.PI * 2;
      path(s, Math.cos(a0) * rr, Math.sin(a0) * rr, Math.cos(a1) * rr, Math.sin(a1) * rr, 3, 0.025, scale(STONE, 0.9), 0.08);
    }
  }
  s.mat = MAT.flat;

  // Things the scenery must keep out of: every building, the plaza, and what is placed below.
  const exclusions: Exclusion[] = placements.map((p) => ({ x: p.x - p.width / 2 - 3, z: p.z - p.depth / 2 - 3, w: p.width + 6, d: p.depth + 14 }));
  exclusions.push({ x: monument.x - 12, z: monument.z - 12, w: 24, d: 24 });
  const inExclusion = (x: number, z: number, pad: number) => exclusions.some((e) => x > e.x - pad && x < e.x + e.w + pad && z > e.z - pad && z < e.z + e.d + pad);
  const nearWalk = (x: number, z: number, d: number) => segments.some(([x0, z0, x1, z1]) => distanceToSegment(x, z, x0, z0, x1, z1) < d);
  // Ponds in the free ground between the buildings and the ring road.
  const ponds: Array<[number, number, number]> = [];
  const pondCount = Math.min(4, Math.max(1, Math.floor(placements.length / 3)));
  for (let i = 0; i < pondCount * 6 && ponds.length < pondCount; i++) {
    const a = Math.PI * 0.2 + (i / pondCount) * Math.PI * 2 + 0.5 + (i % 7) * 0.13;
    const r = Math.max(24, extent.radius * (0.45 + 0.1 * (i % 3)));
    const x = Math.cos(a) * r, z = Math.sin(a) * r, pr = 5.5 + rnd() * 2.5;
    if (inExclusion(x, z, pr + 3) || nearWalk(x, z, pr + 3) || ponds.some(([px, pz, rr]) => Math.hypot(px - x, pz - z) < rr + pr + 6)) continue;
    ponds.push([x, z, pr]);
    pond(s, x, z, pr, { seed: 'pond' + i });
    exclusions.push({ x: x - pr - 2, z: z - pr - 2, w: pr * 2 + 4, d: pr * 2 + 4 });
  }
  // Car parks beside buildings (west side) where the ground is free, with a few cars and a van.
  for (const p of placements) {
    if (p.kind === 'executive' || rnd() < 0.35) continue;
    const size = carParkSize(3);
    const cx = p.x - p.width / 2 - 6 - size.w / 2, cz = p.z - p.depth / 2 + size.d / 2;
    if (inExclusion(cx, cz, 2) || nearWalk(cx, cz, 6) || Math.hypot(cx, cz) > ringHalf - 8) continue;
    carPark(s, cx, cz, { cols: 3, rot: 0, seed: 'lot' + p.workspaceId, fill: 0.7 });
    exclusions.push({ x: cx - size.w / 2, z: cz - size.d / 2, w: size.w, d: size.d });
  }
  // The ground-level kit: ring road, pavements, lamps, tree lines, hedges, canal with bridges, gate house, fence, skyline.
  campusEnvironment(s, {
    cx: 0, cz: 0, halfW: ringHalf, halfD: ringHalf, seed: 'anum-mine', exclusions, density: placements.length > 10 ? 0.6 : 0.8,
    skyline: { from: Math.PI * 0.05, to: Math.PI * 1.95, count: 22 },
  });
  // Trees in the free ground inside the ring.
  const treeCount = Math.min(90, 30 + placements.length * 6);
  for (let i = 0, placed = 0; placed < treeCount && i < treeCount * 12; i++) {
    const a = rnd() * Math.PI * 2, r = 8 + Math.sqrt(rnd()) * (ringHalf - 14);
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (inExclusion(x, z, 2) || nearWalk(x, z, 4)) continue;
    tree(s, x, z, { seed: 'in' + i });
    placed += 1;
  }
  // A tennis court in free ground inside the ring road.
  let court: [number, number] | null = null;
  for (let i = 0; i < 40 && !court; i++) {
    const x = -ringHalf * 0.6 + (i % 8) * (ringHalf * 0.17), z = (i < 20 ? -1 : 1) * (ringHalf - 22 - Math.floor(i / 8) * 0.01);
    if (!inExclusion(x, z, 14) && !nearWalk(x, z, 16) && !ponds.some(([px, pz, rr]) => Math.hypot(px - x, pz - z) < rr + 18)) court = [x, z];
  }
  if (court) { tennisCourt(s, court[0], court[1], { rot: 0, fenced: true, floodlights: true }); exclusions.push({ x: court[0] - 18, z: court[1] - 18, w: 36, d: 36 }); }
  // A truck yard with a van, beside the south approach road.
  truck(s, ringHalf * 0.55, ringHalf - 24, { rot: Math.PI / 2 });
  van(s, ringHalf * 0.55 + 7, ringHalf - 24, { rot: Math.PI / 2 });
  // Walkway lamps.
  for (const [x0, z0, x1, z1] of segments) {
    const len = Math.hypot(x1 - x0, z1 - z0);
    const count = Math.floor(len / 14);
    const nx = -(z1 - z0) / (len || 1), nz = (x1 - x0) / (len || 1);
    for (let i = 1; i <= count; i++) {
      const t = i / (count + 1);
      lamp(s, x0 + (x1 - x0) * t + nx * 2.6, z0 + (z1 - z0) * t + nz * 2.6);
    }
  }
  const scenery = new MeshBuilder();
  scenery.append(classifySurfaces(s.toFloat32()));
  const total = scenery.triangleCount + buildings.reduce((n, bd) => n + bd.mesh.triangles, 0);
  return {
    scenery: freeze(scenery), buildings, monument: { x: monument.x, y: 10.2, z: monument.z }, extent, groundRadius, triangles: total, signCount, canalRadius: ringHalf + 24,
    shadow: { center: { x: 0, y: 0, z: 0 }, radius: ringHalf * 1.2 + 6 },
    halos: new Float32Array([...s.halos, ...buildingHalos]),
    ambient: {
      ...trafficPlan({ cx: 0, cz: 0, halfW: ringHalf, halfD: ringHalf, cornerRadius: 16 }, { seed: 'anum-mine' }),
      ripples: ripplesOf([{ x: monument.x, z: monument.z, r: 9 }, ...ponds.map(([x, z, r]) => ({ x, z, r }))]),
    },
  };
}
