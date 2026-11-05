/**
 * Vegetation variety for the free ground of the campus (docs/ANUM_MINE_HQ_CENTER_PLAZA.md, "Planting"): a mix of trees of every
 * species, shrubs, ornamental grass clumps and agave-like succulents, each with its own size and one of a few leaf tints. Every pick
 * comes from a seed and an index (`plantAt`), never from a random number per frame or per visit, so the same campus always grows the
 * same plants in the same places. The plants are decoration only: nothing here stands for a record.
 */
import { MAT, MeshBuilder, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { NOMINAL_HEIGHT, TREE_SPECIES, shrub, tree, type TreeSpecies } from './planting.ts';

export type PlantKind = 'tree' | 'shrub' | 'grass' | 'succulent';

/** Leaf tints (multipliers on the species' own greens): as grown, fresh spring, deep shade, dusty olive, sun warmed. */
export const PLANT_TINTS: readonly Rgb[] = [[1, 1, 1], [1.08, 1.12, 0.9], [0.84, 0.9, 0.86], [1.02, 1.0, 0.84], [1.14, 1.04, 0.8]];

/** How often each kind is picked (sums to 1). Trees stay the majority; small plants fill in between them. */
export const PLANT_WEIGHTS: Readonly<Record<PlantKind, number>> = { tree: 0.56, shrub: 0.18, grass: 0.16, succulent: 0.1 };

/** The size range per kind, as a factor on the kind's nominal size. */
export const PLANT_SCALE: Readonly<Record<PlantKind, readonly [number, number]>> = { tree: [0.75, 1.3], shrub: [0.7, 1.4], grass: [0.7, 1.3], succulent: [0.75, 1.35] };

export type PlantPick = Readonly<{ kind: PlantKind; species: TreeSpecies | null; scale: number; tint: Rgb; rot: number }>;

/** The plant at index `i` of a seeded planting: kind, tree species (trees only), size factor, leaf tint and heading. Pure. */
export function plantAt(seed: string, i: number): PlantPick {
  const rnd = seeded(`${seed}#${i}`);
  const roll = rnd();
  let kind: PlantKind = 'tree';
  let acc = 0;
  for (const k of ['tree', 'shrub', 'grass', 'succulent'] as const) { acc += PLANT_WEIGHTS[k]; if (roll < acc) { kind = k; break; } }
  const species = kind === 'tree' ? TREE_SPECIES[Math.min(TREE_SPECIES.length - 1, Math.floor(rnd() * TREE_SPECIES.length))] : null;
  const [lo, hi] = PLANT_SCALE[kind];
  const s = lo + (hi - lo) * rnd();
  const tint = PLANT_TINTS[Math.min(PLANT_TINTS.length - 1, Math.floor(rnd() * PLANT_TINTS.length))];
  return { kind, species, scale: s, tint, rot: rnd() * Math.PI * 2 };
}

const GRASS = mix([0.42, 0.55, 0.3], [0.62, 0.66, 0.4], 0.4);
const AGAVE = [0.4, 0.56, 0.52] as const;

/** An ornamental grass clump: three slim tufts (upright cones) that sway with the wind. About 30 triangles. */
export function grassClump(b: MeshBuilder, x: number, z: number, h: number, tint: Rgb, rot = 0): void {
  const c: Rgb = [GRASS[0] * tint[0], GRASS[1] * tint[1], GRASS[2] * tint[2]];
  b.withMat(MAT.foliage, () => {
    for (let k = 0; k < 3; k++) {
      const a = rot + (k * Math.PI * 2) / 3;
      b.prism(x + Math.cos(a) * 0.22, 0, z + Math.sin(a) * 0.22, 0.2, 0, h * (0.8 + 0.12 * k), 5, scale(c, 0.9 + 0.08 * k));
    }
  });
}

/** An agave-like succulent: a rosette of pointed leaves, each a folded blade. About 20 triangles. */
export function succulent(b: MeshBuilder, x: number, z: number, r: number, tint: Rgb, rot = 0): void {
  const c: Rgb = [AGAVE[0] * tint[0], AGAVE[1] * tint[1], AGAVE[2] * tint[2]];
  const leaves = 7;
  b.withMat(MAT.foliage, () => {
    for (let k = 0; k < leaves; k++) {
      const a = rot + (k / leaves) * Math.PI * 2;
      const dx = Math.cos(a), dz = Math.sin(a), sx = -dz * r * 0.16, sz = dx * r * 0.16;
      const tip = [x + dx * r, r * (0.55 + 0.25 * (k % 2)), z + dz * r];
      const ridge = [x + dx * r * 0.3, r * 0.22, z + dz * r * 0.3];
      const base = [x, 0.02, z];
      b.tri(base, [ridge[0] + sx, ridge[1] - 0.04, ridge[2] + sz], tip, scale(c, 1.05));
      b.tri(base, tip, [ridge[0] - sx, ridge[1] - 0.04, ridge[2] - sz], scale(c, 0.85));
    }
  });
}

/** Plants one pick at (x, z) (`seed` keeps the tree's own variant stable). */
export function plant(b: MeshBuilder, x: number, z: number, pick: PlantPick, seed: string): void {
  if (pick.kind === 'tree' && pick.species) tree(b, x, z, { species: pick.species, height: NOMINAL_HEIGHT[pick.species] * pick.scale, rot: pick.rot, tint: pick.tint, seed });
  else if (pick.kind === 'shrub') shrub(b, x, z, 0.85 * pick.scale, seed);
  else if (pick.kind === 'grass') grassClump(b, x, z, 1.1 * pick.scale, pick.tint, pick.rot);
  else succulent(b, x, z, 0.9 * pick.scale, pick.tint, pick.rot);
}

/** How far a pick keeps from walkways and buildings (a small plant may stand nearer than a tree). */
export const plantClearance = (pick: PlantPick): number => (pick.kind === 'tree' ? 2 : 1);
