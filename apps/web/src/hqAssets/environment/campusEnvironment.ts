/**
 * `campusEnvironment`: a ready composition of the ground level scenery around a campus: ring
 * road with junction gaps, pavements, lamps, tree lines, hedges, a canal with two bridges, an
 * approach road with a gate house, boundary fence and a skyline backdrop.
 *
 * Buildings, the monument and the car parks, courts and pitches are not placed here: call the
 * individual builders for those, using `exclusions` to keep trees and beds out of their footprints.
 */
import { MeshBuilder, seeded } from '../../lib/hqGeometry.ts';
import { appendMesh, type AssetResult, type GlowPoint, type InstanceLike } from './common.ts';
import { canal, bridge } from './water.ts';
import { ringRoad, ringPath, road, ringEdgeMidpoints } from './roads.ts';
import { lampLine, fence, gateHouse, bench } from './furniture.ts';
import { treeLine, hedgeRun, flowerBed, type TreeSpecies } from './planting.ts';
import { skylineBackdrop } from './skyline.ts';
import { roundedRectPath, lengths, directionAt } from './paths.ts';

/** An axis-aligned ground rectangle that scenery must stay out of (a building footprint, a plaza). */
export type Exclusion = Readonly<{ x: number; z: number; w: number; d: number }>;

export type CampusEnvironmentOptions = {
  cx: number; cz: number; halfW: number; halfD: number;
  seed?: string;
  exclusions?: readonly Exclusion[];
  /** Skyline arc (radians). Default: the northern half. */
  skyline?: { from?: number; to?: number; count?: number } | false;
  species?: readonly TreeSpecies[];
  /** 0.25 to 1.5: scales how many trees and lamps are planted (1 is about 24 000 triangles for 220 x 160 m). */
  density?: number;
};

export const CAMPUS_ENVIRONMENT_TRIANGLES_CAP = 26000;

/** Gap between the ring road centre line and the canal centre line, and the canal width. */
export const CANAL_OFFSET = 24;
export const CANAL_WIDTH = 9;

const inside = (ex: readonly Exclusion[], x: number, z: number, pad = 2): boolean => ex.some((e) => x > e.x - pad && x < e.x + e.w + pad && z > e.z - pad && z < e.z + e.d + pad);

export type CampusEnvironmentResult = AssetResult & Readonly<{
  /** Where the approach road meets the gate house and the bridge centres, for callers that route traffic. */
  anchors: Readonly<{ gate: { x: number; z: number }; southBridge: { x: number; z: number }; eastBridge: { x: number; z: number } }>;
  instances: readonly InstanceLike[];
}>;

export function campusEnvironment(b: MeshBuilder, o: CampusEnvironmentOptions): CampusEnvironmentResult {
  const rnd = seeded(o.seed ?? 'campus-environment');
  const ex = o.exclusions ?? [];
  const density = Math.min(1.5, Math.max(0.25, o.density ?? 1));
  const m = new MeshBuilder();
  const glow: GlowPoint[] = [];
  const instances: InstanceLike[] = [];
  const ringO = { cx: o.cx, cz: o.cz, halfW: o.halfW, halfD: o.halfD, width: 9, cornerRadius: 16 };
  // Ring road with junctions on the south (edge 1) and east (edge 0) sides, crossings on the others.
  ringRoad(m, { ...ringO, junctionEdges: [0, 1], crosswalkEdges: [2, 3], junctionHalfWidth: 5.5 });
  const ring = ringPath(ringO);
  // Lamps on the inner pavement, trees outside and inside, a clipped hedge inside the lamps.
  glow.push(...lampLine(m, ring, { closed: true, offset: 4.4 + 2.4, spacing: 36 / density, kind: 'street' }).glow);
  for (const [offset, species, spacing] of [[-(4.5 + 2.4 + 3), o.species ?? ['datePalm'], 20], [4.5 + 2.4 + 6.5, o.species ?? ['datePalm', 'oak', 'acacia', 'olive', 'flowering'], 28]] as const) {
    const t = treeLine(m, ring, { closed: true, offset, spacing: spacing / density, species, seed: `${o.seed ?? 'env'}-${offset}` });
    instances.push(...t.instances);
  }
  hedgeRun(m, roundedRectPath(o.cx, o.cz, o.halfW - 9.4, o.halfD - 9.4, 12, 5), { closed: true, height: 0.9, width: 0.8, seed: `${o.seed ?? 'env'}-hedge` });
  // Canal around the campus and two bridges where the junctions are.
  const cHalfW = o.halfW + CANAL_OFFSET, cHalfD = o.halfD + CANAL_OFFSET;
  const canalPath = roundedRectPath(o.cx, o.cz, cHalfW, cHalfD, 30, 7);
  canal(m, canalPath, { width: CANAL_WIDTH, closed: true, seed: o.seed });
  const southZ = o.cz + cHalfD, eastX = o.cx + cHalfW;
  const southBridge = { x: o.cx, z: southZ }, eastBridge = { x: eastX, z: o.cz };
  bridge(m, southBridge.x, southBridge.z, { rot: 0, length: CANAL_WIDTH + 3.2, width: 8, style: 'arch' });
  bridge(m, eastBridge.x, eastBridge.z, { rot: Math.PI / 2, length: CANAL_WIDTH + 3.2, width: 8, style: 'arch' });
  // Approach roads from the ring to just beyond the canal, with a gate house on the south one.
  const gateZ = southZ + 20;
  road(m, [[o.cx, o.cz + o.halfD], [o.cx, gateZ + 6]], { width: 8, y: 0.024, centre: 'dashed', crossings: [] });
  road(m, [[o.cx + o.halfW, o.cz], [eastX + 22, o.cz]], { width: 8, y: 0.024 });
  const gate = { x: o.cx, z: gateZ };
  glow.push(...gateHouse(m, gate.x - 2.1, gate.z, { rot: 0, open: false, laneWidth: 4.2 }).glow);
  // Boundary fence outside the canal with gaps for both approach roads.
  const fHalfW = cHalfW + 14, fHalfD = cHalfD + 14;
  const fencePath = roundedRectPath(o.cx, o.cz, fHalfW, fHalfD, 20, 5);
  const mids = ringEdgeMidpoints(fencePath);
  fence(m, fencePath, { kind: 'rail', height: 1.1, postSpacing: 6, closed: true, gaps: [[mids[0] - 6, mids[0] + 6], [mids[1] - 6, mids[1] + 6]] });
  // Benches and beds on the inner pavement, away from exclusions.
  const { total } = lengths(ring, true);
  for (let s = 18; s < total; s += 52) {
    const d = directionAt(ring, true, s);
    const off = 4.5 + 2.4 + 0.6 + 1.2;
    const x = d.p[0] - d.dz * off, z = d.p[1] + d.dx * off;
    if (inside(ex, x, z, 3)) continue;
    bench(m, x, z, { rot: Math.atan2(d.dz, -d.dx) + Math.PI / 2 });
  }
  for (let s = 35; s < total; s += 78) {
    const d = directionAt(ring, true, s);
    const off = 4.5 + 2.4 + 4;
    const x = d.p[0] - d.dz * off, z = d.p[1] + d.dx * off;
    if (inside(ex, x, z, 3)) continue;
    flowerBed(m, x, z, 5, 2.2, { rot: Math.atan2(d.dx, d.dz), pattern: rnd() < 0.5 ? 'bands' : 'mixed', seed: `${o.seed ?? 'env'}-bed-${s}` });
  }
  if (o.skyline !== false) {
    const sk = o.skyline ?? {};
    skylineBackdrop(m, { cx: o.cx, cz: o.cz, radius: Math.max(fHalfW, fHalfD) + 120, fromAngle: sk.from ?? Math.PI, toAngle: sk.to ?? Math.PI * 2, count: sk.count ?? 18, seed: `${o.seed ?? 'env'}-sky` });
  }
  appendMesh(b, m);
  return { triangles: m.triangleCount, glow, bounds: m.bounds(), anchors: { gate, southBridge, eastBridge }, instances };
}
