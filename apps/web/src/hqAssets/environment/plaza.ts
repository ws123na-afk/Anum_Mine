/**
 * The campus heart and the building surrounds: a globe monument in a pool on a paved plaza, and a garden
 * for each building (paved apron with a paving pattern, clipped hedges, lawn beds with trees and shrubs).
 * Decoration only: the globe is a plain world map in two blues and greens, with no text and no data.
 */
import { MeshBuilder, MAT, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { STONE, TEAL, WARM, flatPoly } from '../../lib/hqProps.ts';
import { scoped, type AssetResult } from './common.ts';
import { circlePath, ribbon } from './paths.ts';
import { hedgeRun, shrub, topiary, tree } from './planting.ts';

const COPING = hex('#d6d0c1');
const POOL_WALL = hex('#a9a395');
const OCEAN = hex('#2f78c4');
const LAND = hex('#4aa06a');

export const GLOBE_MONUMENT_TRIANGLES_CAP = 2400;

/** One world map cell: land where two low-frequency waves agree. Deterministic and purely decorative. */
function isLand(lat: number, lon: number): boolean {
  const v = Math.sin(lon * 2.1 + 0.6) * Math.cos(lat * 2.6 - 0.3) + 0.55 * Math.sin(lon * 4.7 - lat * 3.1 + 1.2) + 0.25 * Math.cos(lon * 7.3 + lat * 5.9);
  return v > 0.55;
}

/** A globe on a pedestal in a round pool: paving rings, a coping ring, water jets, two lit meridian rings and a night glow. */
export function globeMonument(b: MeshBuilder, cx: number, cz: number, o: { plazaRadius?: number } = {}): AssetResult {
  const R = o.plazaRadius ?? 17;
  return scoped(b, [], (m) => {
    // Paving: concentric bands in alternating tones, so the plaza reads as laid stone and not as a flat disc.
    m.withMat(MAT.tile, () => {
      const bands: Array<[number, Rgb]> = [[R, mix(STONE, hex('#ffffff'), 0.22)], [R * 0.86, scale(STONE, 0.84)], [R * 0.74, mix(STONE, hex('#ffffff'), 0.3)], [R * 0.6, scale(STONE, 0.78)]];
      bands.forEach(([r, c], i) => m.disc(cx, 0.02 + i * 0.004, cz, r, 40, c));
      // Radial joints between the paving bands.
      for (let i = 0; i < 24; i++) {
        const a = (i / 24) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a), w = 0.06;
        flatPoly(m, [[cx + c * R * 0.62 - s * w, cz + s * R * 0.62 + c * w], [cx + c * R * 0.98 - s * w, cz + s * R * 0.98 + c * w], [cx + c * R * 0.98 + s * w, cz + s * R * 0.98 - c * w], [cx + c * R * 0.62 + s * w, cz + s * R * 0.62 - c * w]], 0.04, scale(STONE, 0.7));
      }
    });
    // Pool: water, an inner wall and a raised coping ring.
    const pool = circlePath(cx, cz, 9.2, 36);
    m.withMat(MAT.water, () => m.disc(cx, 0.1, cz, 9.0, 36, hex('#2a7ca8'), 0.08));
    m.withMat(MAT.concrete, () => {
      ribbon(m, pool, true, 0, 0.1, 0, 0.5, POOL_WALL, 'left');
      ribbon(m, pool, true, 0, 0.5, 0.7, 0.5, COPING);
      ribbon(m, pool, true, 0.7, 0.5, 0.7, 0.02, scale(STONE, 0.9), 'right');
    });
    // Plinth steps and the pedestal.
    m.withMat(MAT.concrete, () => {
      m.prism(cx, 0.1, cz, 4.8, 4.8, 0.9, 28, mix(STONE, hex('#ffffff'), 0.2));
      m.prism(cx, 1.0, cz, 3.7, 3.5, 0.55, 28, COPING);
      m.prism(cx, 1.55, cz, 1.5, 1.2, 1.5, 12, scale(STONE, 0.78));
    });
    m.prism(cx, 3.0, cz, 2.1, 2.1, 0.16, 28, hex('#d9b25f'), { emissive: 0.9 });
    // The globe: smooth normals, ocean and land colours, lit from inside at night.
    const gy = 6.3, gr = 3.1, LAT = 10, LON = 20;
    const vert = (i: number, j: number) => {
      const la = -Math.PI / 2 + (i / LAT) * Math.PI, lo = (j / LON) * Math.PI * 2;
      const n = [Math.cos(la) * Math.cos(lo), Math.sin(la), Math.cos(la) * Math.sin(lo)];
      return { p: [cx + n[0] * gr, gy + n[1] * gr, cz + n[2] * gr], n };
    };
    m.withMat(MAT.paint, () => {
      for (let i = 0; i < LAT; i++) {
        for (let j = 0; j < LON; j++) {
          const la = -Math.PI / 2 + ((i + 0.5) / LAT) * Math.PI, lo = ((j + 0.5) / LON) * Math.PI * 2;
          const land = isLand(la, lo);
          const col = land ? mix(LAND, hex('#8fc47a'), 0.3 * Math.sin(lo * 3 + la * 2) ** 2) : mix(OCEAN, hex('#6cb4ee'), 0.35 * Math.max(0, Math.cos(la)));
          const a = vert(i, j), c = vert(i, j + 1), d = vert(i + 1, j + 1), e = vert(i + 1, j);
          const em = land ? 0.3 : 0.55;
          if (i > 0) m.triN(a.p, d.p, c.p, a.n, d.n, c.n, col, col, col, em);
          if (i < LAT - 1) m.triN(a.p, e.p, d.p, a.n, e.n, d.n, col, col, col, em);
        }
      }
    });
    // Two thin lit rings round the globe (a tilted equator and a meridian).
    for (const tilt of [0.42, 1.9]) {
      const ringPts = (k: number) => {
        const a = (k / 28) * Math.PI * 2, r = gr + 0.22;
        const x = Math.cos(a) * r, y = Math.sin(a) * r;
        return [cx + x * Math.cos(tilt), gy + y, cz + x * Math.sin(tilt)];
      };
      for (let k = 0; k < 28; k++) {
        const p = ringPts(k), q = ringPts(k + 1);
        m.quad([p[0], p[1] - 0.05, p[2]], [q[0], q[1] - 0.05, q[2]], [q[0], q[1] + 0.05, q[2]], [p[0], p[1] + 0.05, p[2]], hex('#cfeaff'), 0.95);
        m.quad([p[0], p[1] + 0.05, p[2]], [q[0], q[1] + 0.05, q[2]], [q[0], q[1] - 0.05, q[2]], [p[0], p[1] - 0.05, p[2]], hex('#cfeaff'), 0.95);
      }
    }
    // Water jets round the pool and lit rim studs.
    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * Math.PI * 2;
      m.prism(cx + Math.cos(a) * 7.4, 0.1, cz + Math.sin(a) * 7.4, 0.14, 0.03, 2.0 + (i % 2) * 0.7, 5, hex('#dff3ff'), { emissive: 0.9, capTop: false });
      m.prism(cx + Math.cos(a + 0.22) * 9.6, 0.5, cz + Math.sin(a + 0.22) * 9.6, 0.09, 0.09, 0.14, 5, TEAL, { emissive: 0.95 });
    }
    // Light pools: the globe, the pool rim and the plaza.
    b.halo(cx, gy, cz, 17, hex('#7fc4ff'), 1.5);
    b.halo(cx, 0.8, cz, 15, hex('#6fd0e8'), 0.8);
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2 + 0.3;
      b.halo(cx + Math.cos(a) * 12.5, 0.9, cz + Math.sin(a) * 12.5, 5.5, WARM, 0.7);
    }
  });
}

export const BUILDING_GARDEN_TRIANGLES_CAP = 9000;

type GardenBox = Readonly<{ x: number; z: number; w: number; d: number }>;

/**
 * The garden of one building: paving bands in the apron, hedged lawn beds either side of the entrance
 * walk with a palm and two trees each, shrubs and topiary along the sides. `door` is the walkway end in front.
 */
export function buildingGarden(b: MeshBuilder, foot: GardenBox, door: { x: number; z: number }, o: { seed?: string; lawn?: Rgb } = {}): AssetResult {
  const rnd = seeded(o.seed ?? 'garden');
  const lawn = o.lawn ?? hex('#3d6b3f');
  return scoped(b, [], (m) => {
    const front = foot.z + foot.d;
    const apron = 5.0;
    // Paving bands across the apron in front of the building, a lighter walk to the door.
    m.withMat(MAT.tile, () => {
      for (let i = 0; i < 4; i++) {
        const z = front + 1.2 + i * 2.6;
        m.plane(foot.x - 2, 0.03 + i * 0.002, z, foot.w + 4, 1.2, i % 2 ? scale(STONE, 0.86) : mix(STONE, hex('#ffffff'), 0.2));
      }
      m.plane(door.x - 2.4, 0.045, front, 4.8, door.z - front + 0.2, mix(STONE, hex('#ffffff'), 0.32));
    });
    // Lawn beds either side of the walk, bordered by clipped hedges, each with a palm, a tree and topiary.
    const bedD = Math.max(5, Math.min(11, door.z - front - 1));
    for (const side of [-1, 1] as const) {
      const bx0 = side < 0 ? foot.x - 1.5 : door.x + 3.4;
      const bx1 = side < 0 ? door.x - 3.4 : foot.x + foot.w + 1.5;
      const bw = bx1 - bx0;
      if (bw < 3) continue;
      const bz = door.z - bedD - 0.5;
      m.withMat(MAT.grass, () => m.plane(bx0, 0.034, bz, bw, bedD, lawn));
      hedgeRun(m, [[bx0, bz], [bx1, bz], [bx1, bz + bedD], [bx0, bz + bedD]], { closed: true, height: 0.75, width: 0.6, seed: `${o.seed}-h${side}` });
      tree(m, bx0 + bw * 0.5, bz + bedD * 0.5, { species: 'datePalm', height: 9 + rnd() * 2, seed: `${o.seed}-p${side}` });
      if (bw > 8) {
        tree(m, bx0 + bw * 0.2, bz + bedD * 0.7, { species: rnd() < 0.5 ? 'oak' : 'flowering', height: 5.4, seed: `${o.seed}-a${side}` });
        topiary(m, bx0 + bw * 0.85, bz + bedD * 0.3, 0.7);
      }
      shrub(m, bx0 + 1, bz + bedD - 1, 0.8, o.seed);
    }
    // Side and back planting: a tree row along each flank and a few shrubs.
    for (const sx of [foot.x - apron, foot.x + foot.w + apron]) {
      for (let z = foot.z + 3; z < front - 2; z += 7.5) {
        tree(m, sx + (rnd() - 0.5) * 1.2, z, { species: rnd() < 0.5 ? 'datePalm' : 'oak', seed: `${o.seed}-s${sx.toFixed(0)}-${z.toFixed(0)}` });
        if (rnd() < 0.7) shrub(m, sx + (sx < foot.x ? 1.6 : -1.6), z + 2, 0.7, o.seed);
      }
    }
    for (let x = foot.x + 3; x < foot.x + foot.w - 2; x += 8) tree(m, x, foot.z - 4.5, { species: rnd() < 0.5 ? 'cypress' : 'olive', seed: `${o.seed}-b${x.toFixed(0)}` });
    b.halo(door.x, 2.4, front + 2, 8, WARM, 0.6);
  });
}
