/**
 * Procedural campus: ground, walkways, a ring road and gate, a canal with bridges, ponds, a tennis
 * court, planting, the central monument and one building per placement (each drawn as a type from
 * `campusBuildings.ts`). Every shape is generated from the placements and a fixed seed, so a campus
 * always looks the same for the same set of buildings. No data other than the layout goes in:
 * names, counts and statuses are HTML overlays drawn from the campus model, and the lettering on
 * the signs is drawn from the real names at render time.
 */
import { MAT, MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import { GROUND, STONE, flatPoly, lamp } from './hqProps.ts';
import { buildingGarden, centerPlaza, roundabout, avenue, campusEnvironment, carPark, carParkSize, pond, ripplesOf, tennisCourt, plant, plantAt, plantClearance, trafficPlan, truck, van, type SceneryPlan, type Exclusion } from '../hqAssets/environment/index.ts';
import { routeSpokes, SPOKE_WIDTH, type Spoke } from './campusSpokes.ts';
import { classifySurfaces } from './hqSurfaces.ts';
import { archetypeOf, type Archetype } from './campusBuildings.ts';
import { drawExterior, type ExteriorShape } from './campusExteriors.ts';
import { campusExtent, PLAZA, type Placement } from './campusModel.ts';
import { footprintRect, turnHalos, turnRect, turnVertices, turnXZ, unturnXZ, worldSize } from './campusFacing.ts';
import type { Aabb } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type SceneMesh = Readonly<{ vertices: Float32Array; bounds: Aabb; triangles: number }>;
export type CampusBuildingMesh = Readonly<{
  workspaceId: string; mesh: SceneMesh; box: Aabb; anchor: Vec3; door: Vec3;
  /**
   * For a leaned building (`Placement.yaw`): its own rectangle, grown like `box` (middle, half sizes in world axes before the lean, the lean).
   * The box round a leaned building holds its door; the work motion's yard and obstacle tests use this instead (`hqWorkMotion.Lean2`).
   */
  lean?: Readonly<{ cx: number; cz: number; hw: number; hd: number; yaw: number }>;
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
  /** The centre: plaza and fountain radii, the avenues and the walkway from the roundabout to each building's door. */
  plaza: Readonly<{ center: Vec3; basin: number; paved: number; roadCentre: number; roadWidth: number; outer: number; spokes: readonly Spoke[]; avenues: ReadonlyArray<Readonly<{ from: readonly [number, number]; to: readonly [number, number] }>> }>;
  /** Scenery that moves for its own sake (traffic loops, water ripples); never read from a record. */
  motion: SceneryPlan;
}>;

const freeze = (b: MeshBuilder): SceneMesh => ({ vertices: b.toFloat32(), bounds: b.bounds(), triangles: b.triangleCount });

function path(b: MeshBuilder, x0: number, z0: number, x1: number, z1: number, width: number, y: number, color: Rgb, emissive = 0): void {
  const dx = x1 - x0, dz = z1 - z0, len = Math.hypot(dx, dz);
  if (len < 1e-6) return;
  const nx = (-dz / len) * (width / 2), nz = (dx / len) * (width / 2);
  flatPoly(b, [[x0 - nx, z0 - nz], [x1 - nx, z1 - nz], [x1 + nx, z1 + nz], [x0 + nx, z0 + nz]], y, color, emissive);
}

/**
 * Draws `fn` in a building's own frame (its middle at the origin, its front on +z) and turns the result to the building on the campus:
 * the lawn, the garden and the car park follow the building's turn the same way its exterior does.
 */
function inFrame(s: MeshBuilder, p: Placement, fn: (m: MeshBuilder) => void): void {
  const m = new MeshBuilder();
  m.mat = s.mat;
  fn(m);
  const v = m.toFloat32();
  turnVertices(v, p.turn, p.x, p.z, p.yaw ?? 0);
  s.append(v);
  s.halos.push(...turnHalos(m.halos, p.turn, p.x, p.z, p.yaw ?? 0));
}

/** A rectangle in a building's own frame as a world exclusion (x, z, w, d). */
function worldBox(p: Placement, x0: number, z0: number, x1: number, z1: number): Exclusion {
  const r = turnRect({ x0, z0, x1, z1 }, p.turn, p.x, p.z, p.yaw ?? 0);
  return { x: r.x0, z: r.z0, w: r.x1 - r.x0, d: r.z1 - r.z0 };
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

/** `draw` may replace the generic exterior of a building (the shell generated from its floor plans); returning undefined keeps the generic one. */
export type CampusSceneOptions = Readonly<{ draw?: (p: Placement, archetype: Archetype, signIndex: number, signCount: number) => ExteriorShape | undefined }>;

export function buildCampusScene(placements: readonly Placement[], options: CampusSceneOptions = {}): CampusScene {
  const extent = campusExtent(placements);
  // The ring road, canal and fence of the environment kit surround the buildings; the ground reaches past the skyline.
  const ringHalf = Math.max(56, extent.radius + 28);
  const fenceHalf = ringHalf + 24 + 14;
  const groundRadius = fenceHalf + 150;
  const rnd = seeded('anum-mine-campus');
  const monument: Vec3 = { x: 0, y: 0, z: 0 };
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
  // The centre: a big three tier fountain on a paved plaza with benches, lamps and planting, a roundabout round it and two avenues to the ring road.
  centerPlaza(s, 0, 0, { basin: PLAZA.basin, paved: PLAZA.paved });

  // Buildings, their walkways, and the ring road.
  const buildings: CampusBuildingMesh[] = [];
  const buildingHalos: number[] = [];
  placements.forEach((p, index) => {
    const archetype = archetypeOf(p);
    const asset = options.draw?.(p, archetype, index, signCount) ?? drawExterior(p, archetype, index, signCount);
    const mesh: SceneMesh = { vertices: asset.vertices, bounds: asset.bounds, triangles: asset.triangles };
    const anchor: Vec3 = asset.anchor, door: Vec3 = asset.door;
    buildingHalos.push(...asset.halos);
    // The pick box is the turned footprint (the click area, the walker's facade and the flight's obstacle).
    const f = footprintRect(p);
    buildings.push({
      workspaceId: p.workspaceId, mesh, anchor, door, signIndex: index, archetype,
      box: { min: { x: f.x0 - 1.2, y: 0, z: f.z0 - 1.2 }, max: { x: f.x1 + 1.2, y: mesh.bounds.max.y, z: f.z1 + 1.2 } },
      ...(p.yaw ? { lean: { cx: p.x, cz: p.z, hw: worldSize({ ...p, yaw: 0 }).w / 2 + 1.2, hd: worldSize({ ...p, yaw: 0 }).d / 2 + 1.2, yaw: p.yaw } } : {}),
    });
  });
  // Walkways from the roundabout to every door, routed round the buildings.
  const spokes = routeSpokes(
    buildings.map((bd) => ({ id: bd.workspaceId, x: bd.door.x, z: bd.door.z })),
    // A leaned building is tested by its own turned rectangle, not the box round it (its door would sit inside that box).
    placements.map((p) => ({ id: p.workspaceId, ...footprintRect(p), ...(p.yaw ? { cx: p.x, cz: p.z, hw: worldSize({ ...p, yaw: 0 }).w / 2, hd: worldSize({ ...p, yaw: 0 }).d / 2, yaw: p.yaw } : {}) })),
    ringHalf - 6,
  );
  const segments: Array<[number, number, number, number]> = [];
  for (const sp of spokes) for (let i = 0; i + 1 < sp.points.length; i++) segments.push([sp.points[i][0], sp.points[i][1], sp.points[i + 1][0], sp.points[i + 1][1]]);
  // Lawn under each building so it sits on the ground, not above it, and a garden round it.
  // Both are drawn in the building's own frame (front garden on +z) and turned with it.
  s.mat = MAT.grass;
  for (const p of placements) inFrame(s, p, (m) => m.plane(-p.width / 2 - 6.5, 0.005, -p.depth / 2 - 6.5, p.width + 13, p.depth + 22, scale(GROUND, 0.9), 0.04));
  s.mat = MAT.flat;
  placements.forEach((p, i) => {
    const bd = buildings[i];
    const [dx, dz] = unturnXZ(bd.door.x - p.x, bd.door.z - p.z, p.turn, p.yaw ?? 0);
    inFrame(s, p, (m) => buildingGarden(m, { x: -p.width / 2, z: -p.depth / 2, w: p.width, d: p.depth + 2 }, { x: dx, z: dz }, { seed: 'garden:' + p.workspaceId, lawn: scale(GROUND, 1.0) }));
  });
  s.mat = MAT.tile;
  const stone = mix(STONE, hex('#ffffff'), 0.15), edge = scale(STONE, 0.7);
  for (const [x0, z0, x1, z1] of segments) { path(s, x0, z0, x1, z1, SPOKE_WIDTH, 0.03, stone, 0.3); path(s, x0, z0, x1, z1, SPOKE_WIDTH + 0.3, 0.026, edge, 0.15); }
  // Round the bends of a walkway so the strips join without a notch.
  for (const sp of spokes) for (let i = 1; i + 1 < sp.points.length; i++) { s.disc(sp.points[i][0], 0.0305, sp.points[i][1], SPOKE_WIDTH / 2, 12, stone, 0.3); s.disc(sp.points[i][0], 0.0265, sp.points[i][1], SPOKE_WIDTH / 2 + 0.15, 12, edge, 0.15); }
  // The roundabout round the plaza: its mouths are the two avenues (south and east), and each walkway crosses it on a zebra.
  const avenues = [
    { from: [0, PLAZA.roadCentre + 3.5] as const, to: [0, ringHalf - 4.4] as const },
    { from: [PLAZA.roadCentre + 3.5, 0] as const, to: [ringHalf - 4.4, 0] as const },
  ];
  roundabout(s, 0, 0, { radius: PLAZA.roadCentre, width: PLAZA.roadWidth, mouths: [0, Math.PI / 2], crossings: spokes.map((sp) => Math.atan2(sp.points[0][1], sp.points[0][0])) });
  for (const av of avenues) avenue(s, av.from, av.to, { width: PLAZA.roadWidth });
  s.mat = MAT.flat;

  // Things the scenery must keep out of: every building, the plaza, and what is placed below.
  const exclusions: Exclusion[] = placements.map((p) => worldBox(p, -p.width / 2 - 3, -p.depth / 2 - 3, p.width / 2 + 3, p.depth / 2 + 11));
  exclusions.push({ x: -PLAZA.outer - 1, z: -PLAZA.outer - 1, w: PLAZA.outer * 2 + 2, d: PLAZA.outer * 2 + 2 });
  exclusions.push({ x: -PLAZA.corridor, z: 0, w: PLAZA.corridor * 2, d: ringHalf }, { x: 0, z: -PLAZA.corridor, w: ringHalf, d: PLAZA.corridor * 2 });
  const inExclusion = (x: number, z: number, pad: number) => exclusions.some((e) => x > e.x - pad && x < e.x + e.w + pad && z > e.z - pad && z < e.z + e.d + pad);
  const nearWalk = (x: number, z: number, d: number) => segments.some(([x0, z0, x1, z1]) => distanceToSegment(x, z, x0, z0, x1, z1) < d);
  // Ponds in the free ground between the buildings and the ring road.
  const ponds: Array<[number, number, number]> = [];
  const pondCount = Math.min(4, Math.max(1, Math.floor(placements.length / 3)));
  for (let i = 0; i < pondCount * 40 && ponds.length < pondCount; i++) {
    const a = rnd() * Math.PI * 2;
    const r = 36 + rnd() * Math.max(4, ringHalf - 36 - 18);
    const x = Math.cos(a) * r, z = Math.sin(a) * r, pr = 5.5 + rnd() * 2.5;
    if (Math.hypot(x, z) > ringHalf - 16 - pr || inExclusion(x, z, pr + 3) || nearWalk(x, z, pr + 3) || ponds.some(([px, pz, rr]) => Math.hypot(px - x, pz - z) < rr + pr + 6)) continue;
    ponds.push([x, z, pr]);
    pond(s, x, z, pr, { seed: 'pond' + ponds.length });
    exclusions.push({ x: x - pr - 2, z: z - pr - 2, w: pr * 2 + 4, d: pr * 2 + 4 });
  }
  // Car parks beside buildings (on the building's own left, seen from the front) where the ground is free, with a few cars and a van.
  for (const p of placements) {
    if (p.kind === 'executive' || rnd() < 0.35) continue;
    const size = carParkSize(3);
    const lx = -p.width / 2 - 6 - size.w / 2, lz = -p.depth / 2 + size.d / 2;
    const [ox, oz] = turnXZ(lx, lz, p.turn, p.yaw ?? 0);
    const cx = p.x + ox, cz = p.z + oz;
    if (inExclusion(cx, cz, 2) || nearWalk(cx, cz, 6) || Math.hypot(cx, cz) > ringHalf - 8) continue;
    const box = worldBox(p, lx - size.w / 2, lz - size.d / 2, lx + size.w / 2, lz + size.d / 2);
    // The bays turn with the building; the corners must clear the walkways too, not just the middle.
    if (nearWalk(box.x, box.z, 3) || nearWalk(box.x + box.w, box.z + box.d, 3) || nearWalk(box.x, box.z + box.d, 3) || nearWalk(box.x + box.w, box.z, 3)) continue;
    inFrame(s, p, (m) => carPark(m, lx, lz, { cols: 3, rot: 0, seed: 'lot' + p.workspaceId, fill: 0.7 }));
    exclusions.push(box);
  }
  // The ground-level kit: ring road, pavements, lamps, tree lines, hedges, canal with bridges, gate house, fence, skyline.
  campusEnvironment(s, {
    cx: 0, cz: 0, halfW: ringHalf, halfD: ringHalf, seed: 'anum-mine', exclusions, density: placements.length > 10 ? 0.6 : 0.8,
    skyline: { from: Math.PI * 0.05, to: Math.PI * 1.95, count: 22 },
  });
  // Planting in the free ground inside the ring: trees of every species, shrubs, grass clumps and succulents, each with its own size and
  // leaf tint, all picked from a seed and the index (`plantAt`), so the same campus always grows the same plants.
  const plantCount = Math.min(120, 40 + placements.length * 8);
  for (let i = 0, placed = 0; placed < plantCount && i < plantCount * 12; i++) {
    const a = rnd() * Math.PI * 2, r = 8 + Math.sqrt(rnd()) * (ringHalf - 14);
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    const pick = plantAt('anum-mine-planting', placed);
    const clear = plantClearance(pick);
    if (inExclusion(x, z, clear) || nearWalk(x, z, clear + 2)) continue;
    plant(s, x, z, pick, 'in' + i);
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
  for (const [tx, tz] of [[ringHalf * 0.55, ringHalf - 24], [-ringHalf * 0.55, ringHalf - 24], [ringHalf * 0.55, -(ringHalf - 24)], [-ringHalf * 0.55, -(ringHalf - 24)]]) {
    if (inExclusion(tx, tz, 10) || inExclusion(tx + 7, tz, 6) || nearWalk(tx, tz, 8)) continue;
    truck(s, tx, tz, { rot: Math.PI / 2 });
    van(s, tx + 7, tz, { rot: Math.PI / 2 });
    break;
  }
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
    scenery: freeze(scenery), buildings, monument: { x: 0, y: 11.6, z: 0 }, extent, groundRadius, triangles: total, signCount, canalRadius: ringHalf + 24,
    plaza: { center: monument, basin: PLAZA.basin, paved: PLAZA.paved, roadCentre: PLAZA.roadCentre, roadWidth: PLAZA.roadWidth, outer: PLAZA.outer, spokes, avenues },
    shadow: { center: { x: 0, y: 0, z: 0 }, radius: ringHalf * 1.2 + 6 },
    halos: new Float32Array([...s.halos, ...buildingHalos]),
    motion: {
      ...trafficPlan({ cx: 0, cz: 0, halfW: ringHalf, halfD: ringHalf, cornerRadius: 16 }, { seed: 'anum-mine', rich: true, roundabout: { cx: 0, cz: 0, radius: PLAZA.roadCentre } }),
      ripples: ripplesOf([{ x: 0, z: 0, r: PLAZA.basin }, ...ponds.map(([x, z, r]) => ({ x, z, r }))]),
    },
  };
}
