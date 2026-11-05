/**
 * Planting: six tree species (instanced from cached templates), shrubs, trimmed hedges, topiary
 * and flower beds. Trees are built once per species and variant at a nominal height and stamped
 * with a scale and heading, so a campus of hundreds of trees pays for each species once.
 */
import { MAT, MeshBuilder, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { STONE, blobShadow } from '../../lib/hqProps.ts';
import { Frame, makeTemplate, pick, range, scoped, stamp, type AssetResult, type GlowPoint, type Placement, type Template } from './common.ts';
import { lengths, ribbon, directionAt, type P2 } from './paths.ts';

export type TreeSpecies = 'datePalm' | 'oak' | 'cypress' | 'acacia' | 'olive' | 'flowering';
export const TREE_SPECIES: readonly TreeSpecies[] = ['datePalm', 'oak', 'cypress', 'acacia', 'olive', 'flowering'];
export const NOMINAL_HEIGHT: Record<TreeSpecies, number> = { datePalm: 9, oak: 7, cypress: 8, acacia: 6, olive: 4, flowering: 5 };
export const TREE_VARIANTS = 3;
/** Triangle cap for one template of any species. */
export const TREE_TRIANGLES_CAP = 520;

const BARK = hex('#5d4630');
const GREENS: Record<TreeSpecies, readonly [Rgb, Rgb]> = {
  datePalm: [hex('#27703d'), hex('#5aa44c')],
  oak: [hex('#245a38'), hex('#4a8a48')],
  cypress: [hex('#1d4a33'), hex('#356f45')],
  acacia: [hex('#587a38'), hex('#85a548')],
  olive: [hex('#667f5c'), hex('#8ea483')],
  flowering: [hex('#2a6c42'), hex('#4a9054')],
};

/** One tapered limb between two points (a 5 sided prism leaning from a to b). */
function limb(b: MeshBuilder, a: readonly [number, number, number], c: readonly [number, number, number], r0: number, r1: number, color: Rgb): void {
  const n = 5;
  const ax = c[0] - a[0], ay = c[1] - a[1], az = c[2] - a[2];
  const len = Math.hypot(ax, ay, az) || 1;
  // Build in a frame whose y axis is the limb: pick two perpendicular unit vectors.
  const ux = ax / len, uy = ay / len, uz = az / len;
  let px = -uz, py = 0, pz = ux;
  const pl = Math.hypot(px, py, pz);
  if (pl < 1e-6) { px = 1; py = 0; pz = 0; } else { px /= pl; py /= pl; pz /= pl; }
  const qx = uy * pz - uz * py, qy = uz * px - ux * pz, qz = ux * py - uy * px;
  const ring = (p: readonly number[], r: number, i: number): number[] => {
    const t = (i / n) * Math.PI * 2;
    return [p[0] + (px * Math.cos(t) + qx * Math.sin(t)) * r, p[1] + (py * Math.cos(t) + qy * Math.sin(t)) * r, p[2] + (pz * Math.cos(t) + qz * Math.sin(t)) * r];
  };
  for (let i = 0; i < n; i++) {
    const j = i + 1;
    const sh = scale(color, 0.72 + 0.28 * Math.cos(((i + 0.5) / n) * Math.PI * 2 - 0.9));
    b.quad(ring(a, r0, j), ring(a, r0, i), ring(c, r1, i), ring(c, r1, j), sh);
  }
}

/** One arched palm frond: it rises, then droops, in four segments that narrow to a tip. */
function frond(b: MeshBuilder, cx: number, cy: number, cz: number, ang: number, len: number, droop: number, wid: number, col: Rgb): void {
  const dx = Math.cos(ang), dz = Math.sin(ang), sx = -dz, sz = dx;
  const at = (t: number): [number, number, number] => [cx + dx * len * t, cy + Math.sin(t * Math.PI * 0.55) * len * 0.3 - droop * t * t, cz + dz * len * t];
  const half = (t: number) => wid * (t < 0.3 ? 0.55 + t * 1.5 : 1 - (t - 0.3) * 1.2);
  let prev = at(0), pw = half(0) * 0.4;
  const keep = b.mat;
  b.mat = MAT.foliage;
  for (let i = 1; i <= 4; i++) {
    const t = i / 4, p = at(t), w = i === 4 ? 0.02 : half(t);
    const shade = scale(col, 1.12 - 0.3 * t);
    b.quad([prev[0] - sx * pw, prev[1], prev[2] - sz * pw], [prev[0] + sx * pw, prev[1], prev[2] + sz * pw], [p[0] + sx * w, p[1], p[2] + sz * w], [p[0] - sx * w, p[1], p[2] - sz * w], shade);
    prev = p; pw = w;
  }
  b.mat = keep;
}

/** A foliage puff: smooth, lumpy and round, so a crown reads as leaves and not as a stack of faceted stones. */
function puff(b: MeshBuilder, x: number, y: number, z: number, rx: number, ry: number, rz: number, color: Rgb, seed: number, lat = 4, lon = 8): void {
  b.withMat(MAT.foliage, () => b.smoothBlob(x, y, z, rx, ry, rz, color, { lat, lon, lump: 0.13, tone: 0.2, seed }));
}

function buildTree(b: MeshBuilder, sp: TreeSpecies, variant: number): void {
  const rnd = seeded(`${sp}-${variant}`);
  const [g0, g1] = GREENS[sp];
  const leaf = () => mix(g0, g1, rnd());
  const H = NOMINAL_HEIGHT[sp];
  let n = 1;
  if (sp === 'datePalm') {
    const lean = (rnd() - 0.5) * 1.2, lean2 = (rnd() - 0.5) * 0.8;
    const p0: [number, number, number] = [0, 0, 0], p1: [number, number, number] = [lean * 0.4, H * 0.35, lean2 * 0.3], p2: [number, number, number] = [lean * 0.8, H * 0.7, lean2 * 0.7], p3: [number, number, number] = [lean, H, lean2];
    limb(b, p0, p1, 0.3, 0.24, BARK); limb(b, p1, p2, 0.24, 0.2, scale(BARK, 1.08)); limb(b, p2, p3, 0.2, 0.16, BARK);
    const count = 13 + Math.floor(rnd() * 3);
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2 + rnd() * 0.3;
      frond(b, lean, H, lean2, a, 2.7 + rnd() * 0.9, 1.5 + rnd() * 1.1, 0.5, leaf());
    }
    b.smoothBlob(lean, H - 0.05, lean2, 0.42, 0.34, 0.42, hex('#6e5a3e'), { lat: 3, lon: 6, lump: 0.1 });
    for (let i = 0; i < 3; i++) b.smoothBlob(lean + Math.cos(i * 2.1) * 0.5, H - 0.55, lean2 + Math.sin(i * 2.1) * 0.5, 0.22, 0.3, 0.22, hex('#c07a2c'), { lat: 2, lon: 5, lump: 0.05 });
  } else if (sp === 'oak') {
    limb(b, [0, 0, 0], [0.1, H * 0.4, 0], 0.4, 0.28, BARK);
    limb(b, [0.1, H * 0.35, 0], [-1.2, H * 0.62, 0.4], 0.2, 0.12, BARK);
    limb(b, [0.1, H * 0.38, 0], [1.3, H * 0.66, -0.3], 0.2, 0.12, BARK);
    const spots: Array<[number, number, number, number]> = [[0, 0.7, 0, 2.2], [-1.6, 0.62, 0.5, 1.7], [1.7, 0.66, -0.4, 1.8], [0.2, 0.9, 0.2, 1.7], [-0.4, 0.55, -1.5, 1.5], [0.9, 0.58, 1.4, 1.4]];
    for (const [x, y, z, r] of spots) puff(b, x + (rnd() - 0.5) * 0.4, H * y, z + (rnd() - 0.5) * 0.4, r, r * 0.78, r, leaf(), n++);
  } else if (sp === 'cypress') {
    b.prism(0, 0, 0, 0.22, 0.18, H * 0.14, 5, BARK, { capTop: false });
    b.withMat(MAT.foliage, () => b.smoothBlob(0, H * 0.56, 0, H * 0.15, H * 0.44, H * 0.15, leaf(), { lat: 6, lon: 7, lump: 0.07, tone: 0.2, seed: 3 }));
  } else if (sp === 'acacia') {
    limb(b, [0, 0, 0], [0.5, H * 0.5, 0.2], 0.3, 0.2, BARK);
    limb(b, [0.5, H * 0.5, 0.2], [-1.2, H * 0.78, 0.4], 0.16, 0.1, BARK);
    limb(b, [0.5, H * 0.5, 0.2], [1.9, H * 0.82, -0.2], 0.16, 0.1, BARK);
    limb(b, [0.5, H * 0.5, 0.2], [0.6, H * 0.88, -1.6], 0.14, 0.09, BARK);
    for (const [x, z, r] of [[0.4, 0, 3.2], [-1.6, 0.5, 2.3], [2.2, -0.3, 2.4], [0.6, -1.9, 2.1]] as const) puff(b, x, H * 0.88 + rnd() * 0.3, z, r, 0.7, r * 0.92, leaf(), n++);
  } else if (sp === 'olive') {
    limb(b, [0, 0, 0], [0.3, H * 0.4, 0.1], 0.28, 0.2, hex('#7a6a58'));
    limb(b, [0.3, H * 0.4, 0.1], [-0.5, H * 0.65, 0.2], 0.16, 0.1, hex('#7a6a58'));
    limb(b, [0.3, H * 0.4, 0.1], [1.0, H * 0.68, -0.2], 0.16, 0.1, hex('#7a6a58'));
    for (const [x, y, z, r] of [[0, 0.78, 0, 1.5], [-1.0, 0.7, 0.3, 1.1], [1.2, 0.72, -0.3, 1.2], [0.2, 0.92, -0.5, 1.0]] as const) puff(b, x, H * y, z, r, r * 0.72, r, leaf(), n++);
  } else {
    limb(b, [0, 0, 0], [0.1, H * 0.45, 0], 0.3, 0.2, BARK);
    limb(b, [0.1, H * 0.4, 0], [-0.9, H * 0.65, 0.3], 0.14, 0.09, BARK);
    limb(b, [0.1, H * 0.4, 0], [1.0, H * 0.68, -0.2], 0.14, 0.09, BARK);
    const bloom = pick(rnd, [hex('#d9468a'), hex('#e8663d'), hex('#b25ad4'), hex('#f0c04e')]);
    for (const [x, y, z, r] of [[0, 0.78, 0, 1.9], [-1.2, 0.68, 0.3, 1.4], [1.4, 0.7, -0.3, 1.5]] as const) puff(b, x, H * y, z, r, r * 0.76, r, leaf(), n++);
    for (let i = 0; i < 9; i++) {
      const a = rnd() * Math.PI * 2, rr = 0.9 + rnd() * 1.4;
      b.withMat(MAT.foliage, () => b.smoothBlob(Math.cos(a) * rr, H * (0.7 + rnd() * 0.22), Math.sin(a) * rr, 0.4, 0.3, 0.4, mix(bloom, hex('#ffffff'), rnd() * 0.25), { lat: 2, lon: 5, lump: 0.05, tone: 0.1, seed: n++ }));
    }
  }
}

const templates = new Map<string, Template>();

/** The cached template of a species and variant (0 to TREE_VARIANTS - 1). */
export function treeTemplate(sp: TreeSpecies, variant = 0): Template {
  const v = ((variant % TREE_VARIANTS) + TREE_VARIANTS) % TREE_VARIANTS;
  const key = `${sp}:${v}`;
  let t = templates.get(key);
  if (!t) { t = makeTemplate(key, (b) => buildTree(b, sp, v)); templates.set(key, t); }
  return t;
}

export type TreeOptions = { species?: TreeSpecies; height?: number; rot?: number; variant?: number; seed?: string; shadow?: boolean; tint?: Rgb };

/** Plants one tree (stamped from the cached template, with a ground shadow). */
export function tree(b: MeshBuilder, x: number, z: number, o: TreeOptions = {}): AssetResult {
  const rnd = seeded(o.seed ?? `tree-${x.toFixed(1)}-${z.toFixed(1)}`);
  const sp = o.species ?? pick(rnd, TREE_SPECIES);
  const t = treeTemplate(sp, o.variant ?? Math.floor(rnd() * TREE_VARIANTS));
  const h = o.height ?? NOMINAL_HEIGHT[sp] * range(rnd, 0.85, 1.15);
  const sc = h / NOMINAL_HEIGHT[sp];
  return scoped(b, [], (m) => {
    if (o.shadow !== false) blobShadow(m, x, z, (sp === 'cypress' ? 0.18 : sp === 'datePalm' ? 0.28 : 0.4) * h, h * 0.9, 0.024);
    stamp(m, t, { x, z, rot: o.rot ?? rnd() * Math.PI * 2, scale: sc, tint: o.tint });
  });
}

export type InstanceSet = Readonly<{ template: Template; placements: readonly Placement[] }>;
export type PlantingResult = AssetResult & Readonly<{ instances: readonly InstanceSet[] }>;

/**
 * Trees along a path every `spacing` metres with seeded jitter. `species` is a list to pick from.
 * Also returns the instance lists (template plus placements) for a renderer with hardware instancing.
 */
export function treeLine(
  b: MeshBuilder, pts: readonly P2[], o: { spacing?: number; species?: readonly TreeSpecies[]; closed?: boolean; offset?: number; jitter?: number; seed?: string; maxTrees?: number } = {},
): PlantingResult {
  const rnd = seeded(o.seed ?? 'tree-line');
  const closed = o.closed ?? false;
  const sp = o.species ?? ['datePalm'];
  const { total } = lengths(pts, closed);
  const sets = new Map<string, { template: Template; placements: Placement[] }>();
  const res = scoped(b, [], (m) => {
    let planted = 0;
    for (let s = (o.spacing ?? 8) * 0.5; s < total && planted < (o.maxTrees ?? Infinity); s += o.spacing ?? 8) {
      planted++;
      const d = directionAt(pts, closed, s);
      const off = o.offset ?? 0, jit = o.jitter ?? 0.6;
      const x = d.p[0] + -d.dz * off + (rnd() - 0.5) * jit, z = d.p[1] + d.dx * off + (rnd() - 0.5) * jit;
      const species = pick(rnd, sp);
      const t = treeTemplate(species, Math.floor(rnd() * TREE_VARIANTS));
      const h = NOMINAL_HEIGHT[species] * range(rnd, 0.85, 1.12);
      const placement: Placement = { x, z, rot: rnd() * Math.PI * 2, scale: h / NOMINAL_HEIGHT[species] };
      blobShadow(m, x, z, (species === 'cypress' ? 0.18 : 0.34) * h, h * 0.9, 0.024);
      stamp(m, t, placement);
      const e = sets.get(t.id) ?? { template: t, placements: [] };
      e.placements.push(placement);
      sets.set(t.id, e);
    }
  });
  return { ...res, instances: [...sets.values()] };
}

/** A grove: `count` trees scattered over a rectangle (seeded, no two closer than `minGap`). */
export function treeGrove(
  b: MeshBuilder, x: number, z: number, w: number, d: number, count: number, o: { species?: readonly TreeSpecies[]; minGap?: number; seed?: string } = {},
): PlantingResult {
  const rnd = seeded(o.seed ?? 'grove');
  const sp = o.species ?? TREE_SPECIES;
  const gap = o.minGap ?? 3.2;
  const placed: Array<[number, number]> = [];
  const sets = new Map<string, { template: Template; placements: Placement[] }>();
  const res = scoped(b, [], (m) => {
    for (let tries = 0; tries < count * 12 && placed.length < count; tries++) {
      const px = x + rnd() * w, pz = z + rnd() * d;
      if (placed.some((q) => Math.hypot(q[0] - px, q[1] - pz) < gap)) continue;
      placed.push([px, pz]);
      const species = pick(rnd, sp);
      const t = treeTemplate(species, Math.floor(rnd() * TREE_VARIANTS));
      const h = NOMINAL_HEIGHT[species] * range(rnd, 0.8, 1.2);
      const placement: Placement = { x: px, z: pz, rot: rnd() * Math.PI * 2, scale: h / NOMINAL_HEIGHT[species] };
      blobShadow(m, px, pz, (species === 'cypress' ? 0.18 : 0.34) * h, h * 0.9, 0.024);
      stamp(m, t, placement);
      const e = sets.get(t.id) ?? { template: t, placements: [] };
      e.placements.push(placement);
      sets.set(t.id, e);
    }
  });
  return { ...res, instances: [...sets.values()] };
}

// ---------------------------------------------------------------- shrubs and hedges

/** A shrub: a cluster of three to four blobs. */
export function shrub(b: MeshBuilder, x: number, z: number, r: number, seed = 'shrub'): AssetResult {
  const rnd = seeded(`${seed}-${x.toFixed(1)}-${z.toFixed(1)}`);
  return scoped(b, [], (m) => {
    const base = mix(hex('#2b6a40'), hex('#5a9a52'), rnd());
    m.blob(x, r * 0.55, z, r, r * 0.65, r, base, 7);
    m.blob(x + r * 0.55, r * 0.42, z + r * 0.2, r * 0.6, r * 0.45, r * 0.6, scale(base, 1.12), 6);
    m.blob(x - r * 0.5, r * 0.4, z - r * 0.3, r * 0.55, r * 0.42, r * 0.55, scale(base, 0.9), 6);
  });
}

/** A round topiary ball on a short stem in a pot. */
export function topiary(b: MeshBuilder, x: number, z: number, r = 0.7): AssetResult {
  return scoped(b, [], (m) => {
    m.prism(x, 0, z, r * 0.55, r * 0.45, r * 0.5, 8, hex('#8d8777'));
    m.prism(x, r * 0.5, z, 0.05, 0.05, r * 0.5, 4, BARK, { capTop: false });
    m.blob(x, r * 1.5, z, r, r, r, hex('#2f7448'), 8);
  });
}

/**
 * A trimmed hedge along a path: flat top, sloped shoulders, vertical sides, seeded tone changes
 * per section. About 10 triangles per section of the path.
 */
export function hedgeRun(
  b: MeshBuilder, pts: readonly P2[], o: { height?: number; width?: number; closed?: boolean; seed?: string; offset?: number } = {},
): AssetResult {
  const rnd = seeded(o.seed ?? 'hedge');
  const h = o.height ?? 1.0, w = o.width ?? 0.9, closed = o.closed ?? false, off = o.offset ?? 0;
  return scoped(b, [], (m) => {
    const n = closed ? pts.length : pts.length - 1;
    for (let i = 0; i < n; i++) {
      const seg = [pts[i], pts[(i + 1) % pts.length]];
      const col = mix(hex('#2a6a3e'), hex('#3f8248'), rnd());
      const hw = w / 2;
      ribbon(m, seg, false, off - hw + 0.12, h, off + hw - 0.12, h, scale(col, 1.12));
      ribbon(m, seg, false, off - hw, h * 0.72, off - hw + 0.12, h, col);
      ribbon(m, seg, false, off + hw - 0.12, h, off + hw, h * 0.72, col);
      ribbon(m, seg, false, off - hw, 0, off - hw, h * 0.72, scale(col, 0.82), 'left');
      ribbon(m, seg, false, off + hw, 0, off + hw, h * 0.72, scale(col, 0.82), 'right');
    }
  });
}

// ---------------------------------------------------------------- flower beds

export type BedPattern = 'mixed' | 'bands' | 'ring';
const BLOOMS: readonly Rgb[] = [hex('#f2c14e'), hex('#e0646e'), hex('#f4f0e6'), hex('#b48cff'), hex('#f08a3c')];

/** A raised bed with a stone border, soil and seeded flower cones (rot is a heading in radians). */
export function flowerBed(
  b: MeshBuilder, x: number, z: number, w: number, d: number, o: { pattern?: BedPattern; rot?: number; seed?: string } = {},
): AssetResult {
  const rnd = seeded(o.seed ?? `bed-${x.toFixed(1)}-${z.toFixed(1)}`);
  const pattern = o.pattern ?? 'mixed';
  return scoped(b, [], (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const bw = 0.2;
    f.box(-w / 2, 0, -d / 2, w, 0.24, bw, STONE, { skip: ['bottom'] });
    f.box(-w / 2, 0, d / 2 - bw, w, 0.24, bw, STONE, { skip: ['bottom'] });
    f.box(-w / 2, 0, -d / 2 + bw, bw, 0.24, d - 2 * bw, STONE, { skip: ['bottom'] });
    f.box(w / 2 - bw, 0, -d / 2 + bw, bw, 0.24, d - 2 * bw, STONE, { skip: ['bottom'] });
    f.box(-w / 2 + bw, 0, -d / 2 + bw, w - 2 * bw, 0.2, d - 2 * bw, hex('#4a3a2c'), { skip: ['bottom', 'front', 'back', 'left', 'right'] });
    const n = Math.min(36, Math.max(6, Math.floor(w * d * 1.3)));
    const bands = [pick(rnd, BLOOMS), pick(rnd, BLOOMS), pick(rnd, BLOOMS)];
    for (let i = 0; i < n; i++) {
      const u = rnd(), v = rnd();
      const px = -w / 2 + bw + 0.2 + u * (w - 2 * bw - 0.4), pz = -d / 2 + bw + 0.2 + v * (d - 2 * bw - 0.4);
      const col = pattern === 'bands' ? bands[Math.min(2, Math.floor(v * 3))] : pattern === 'ring' ? bands[Math.min(2, Math.floor(Math.hypot(u - 0.5, v - 0.5) * 5))] : pick(rnd, BLOOMS);
      const p = f.p(px, 0, pz);
      m.blob(p[0], 0.32, p[2], 0.2, 0.12, 0.2, hex('#3f7a3f'), 4);
      m.prism(p[0], 0.3, p[2], 0.17, 0, 0.3 + rnd() * 0.15, 5, col);
    }
  });
}

