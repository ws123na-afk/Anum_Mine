/**
 * The loadable (glTF) models of the headquarters scenes: what exists, where the files are, how they
 * are licensed, and which scene slots they may fill. Pure: no three.js and no DOM, so the unit runner
 * covers the truth rules. The renderer (`hqModelLayer.ts`) loads the files lazily with the 3D chunk.
 *
 * TRUTH RULES (the same as `hqAssets/interior/figures.ts`, enforced here and by `test/hqModels.test.ts`):
 *  - a person model is placed only for a slot the interior builders created for a station that carries
 *    an `occupantSeed` (a registry assignment or a live presence record); nothing here invents one;
 *  - the look of a person comes from a hash of that seed, never from a name;
 *  - when a model cannot be loaded, the scene keeps drawing the procedural piece it was meant to replace.
 */
import type { ModelSlot } from './hqGeometry.ts';

export type ModelGroup = 'sofa' | 'figure';

export type ModelKey = 'sofa' | 'armchair' | 'person_0' | 'person_1' | 'person_2' | 'person_3' | 'person_4';

export type ModelLicense = 'CC0-1.0' | 'CC-BY-4.0';

export type ModelSpec = Readonly<{
  key: ModelKey;
  group: ModelGroup;
  /** File name under `public/hq/models/`. */
  file: string;
  /** Bytes on disk as shipped (checked by the unit test against the file). */
  bytes: number;
  license: ModelLicense;
  author: string;
  /** Where the original came from, for the NOTICE file. */
  source: string;
  /** Footprint of the placed model in metres (width, depth) at scale 1 after the loader normalised it: sofas to 1 m wide, people to 0.95 m seated height. */
  footprint: readonly [number, number];
  /** Extra turn in radians so the front of the file faces local +z. */
  yawOffset: number;
  /** For a person: the animation clip held as the seated pose. */
  seatedClip?: string;
}>;

export const MODEL_SPECS: Readonly<Record<ModelKey, ModelSpec>> = {
  sofa: {
    key: 'sofa', group: 'sofa', file: 'sofa.glb', bytes: 100376, license: 'CC-BY-4.0', author: 'Wayfair, LLC (Eric Chadwick)',
    source: 'KhronosGroup/glTF-Sample-Assets, Models/GlamVelvetSofa', footprint: [1, 0.42], yawOffset: 0,
  },
  armchair: {
    key: 'armchair', group: 'sofa', file: 'armchair.glb', bytes: 321764, license: 'CC-BY-4.0', author: 'Wayfair, LLC (Eric Chadwick)',
    source: 'KhronosGroup/glTF-Sample-Assets, Models/SheenChair', footprint: [1, 0.9], yawOffset: 0,
  },
  person_0: { key: 'person_0', group: 'figure', file: 'person_0.glb', bytes: 55080, license: 'CC0-1.0', author: 'Kenney', source: 'Kenney Mini Characters (character-male-d)', footprint: [0.55, 0.55], yawOffset: 0, seatedClip: 'sit' },
  person_1: { key: 'person_1', group: 'figure', file: 'person_1.glb', bytes: 55224, license: 'CC0-1.0', author: 'Kenney', source: 'Kenney Mini Characters (character-male-e)', footprint: [0.55, 0.55], yawOffset: 0, seatedClip: 'sit' },
  person_2: { key: 'person_2', group: 'figure', file: 'person_2.glb', bytes: 56268, license: 'CC0-1.0', author: 'Kenney', source: 'Kenney Mini Characters (character-female-d)', footprint: [0.55, 0.55], yawOffset: 0, seatedClip: 'sit' },
  person_3: { key: 'person_3', group: 'figure', file: 'person_3.glb', bytes: 55592, license: 'CC0-1.0', author: 'Kenney', source: 'Kenney Mini Characters (character-female-e)', footprint: [0.55, 0.55], yawOffset: 0, seatedClip: 'sit' },
  person_4: { key: 'person_4', group: 'figure', file: 'person_4.glb', bytes: 56320, license: 'CC0-1.0', author: 'Kenney', source: 'Kenney Mini Characters (character-female-f)', footprint: [0.55, 0.55], yawOffset: 0, seatedClip: 'sit' },
};

export const PERSON_KEYS: readonly ModelKey[] = ['person_0', 'person_1', 'person_2', 'person_3', 'person_4'];

/** The model a slot's `model` string names, or null for anything this catalogue does not know. */
export function modelFor(model: string): ModelKey | null {
  return model in MODEL_SPECS ? (model as ModelKey) : null;
}

/** FNV-1a, so the same seed (an agent id) always picks the same look. */
export function seedHash(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

/** A placed model, ready for the renderer: world position, yaw in radians, uniform scale. */
export type ModelPlacement = Readonly<{
  /** Stable id: group, index and the slot position, never anything from a person. */
  id: string;
  group: ModelGroup;
  key: ModelKey;
  x: number; y: number; z: number;
  yaw: number;
  scale: number;
  /** Footprint for the soft contact shadow, in metres (width and depth). */
  footprint: readonly [number, number];
}>;

/** Quarter turns to a yaw in radians, matching `Local.pt` (rot 0 faces +z, rot 1 faces -x). */
export const yawOfQuarterTurns = (rot: number): number => -(((rot % 4) + 4) % 4) * (Math.PI / 2);

/**
 * Turn the slots the builders noted into placements. People: only a slot that carries a non-empty
 * seed becomes a person placement (the interior builders create one only for a station with an
 * `occupantSeed`; this check is the second lock). Furniture needs no seed.
 */
export function placementsFromSlots(slots: readonly ModelSlot[]): ModelPlacement[] {
  const out: ModelPlacement[] = [];
  slots.forEach((slot, index) => {
    if (slot.model === 'person') {
      const seed = slot.seed?.trim();
      if (!seed) return;
      const key = PERSON_KEYS[seedHash(seed) % PERSON_KEYS.length];
      const spec = MODEL_SPECS[key];
      const scale = 1;
      out.push({
        id: `figure:${index}:${slot.x.toFixed(2)},${slot.z.toFixed(2)}`, group: 'figure', key,
        x: slot.x, y: slot.y, z: slot.z, yaw: yawOfQuarterTurns(slot.rot), scale, footprint: spec.footprint,
      });
      return;
    }
    const key = modelFor(slot.model);
    if (!key || MODEL_SPECS[key].group !== 'sofa') return;
    const spec = MODEL_SPECS[key];
    const scale = slot.width;
    out.push({
      id: `sofa:${index}:${slot.x.toFixed(2)},${slot.z.toFixed(2)}`, group: 'sofa', key,
      x: slot.x, y: slot.y, z: slot.z, yaw: yawOfQuarterTurns(slot.rot), scale, footprint: [slot.width * 1.05, slot.width * spec.footprint[1]],
    });
  });
  return out;
}

/** The groups present in a set of placements. */
export function groupsOf(placements: readonly ModelPlacement[]): ReadonlySet<ModelGroup> {
  return new Set(placements.map((p) => p.group));
}
