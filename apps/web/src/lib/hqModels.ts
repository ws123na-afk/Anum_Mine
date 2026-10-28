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
import { figureLook, type FigureLook } from './hqAvatar.ts';

export type ModelGroup = 'sofa' | 'figure';

export type LegacyKey = 'sofa' | 'armchair' | 'person_0' | 'person_1' | 'person_2' | 'person_3' | 'person_4';
/** A person drawn by the character kit (`hqAssets/characters/`): `fig:` plus the shape code (see `figureShape`). Nothing to download. */
export type FigureKey = `fig:${string}`;
export type ModelKey = LegacyKey | FigureKey;

export type ModelLicense = 'CC0-1.0' | 'CC-BY-4.0';

export type ModelSpec = Readonly<{
  key: LegacyKey;
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

export const MODEL_SPECS: Readonly<Record<LegacyKey, ModelSpec>> = {
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

export const isFigureKey = (key: string): key is FigureKey => key.startsWith('fig:');

/** What the renderer needs to know about any key: legacy files and kit figures alike. A kit figure is built in the browser, so it is 0 bytes. */
export function specOf(key: ModelKey): Readonly<{ group: ModelGroup; bytes: number; footprint: readonly [number, number]; yawOffset: number; kit: boolean }> {
  if (isFigureKey(key)) return { group: 'figure', bytes: 0, footprint: [0.6, 0.6], yawOffset: 0, kit: true };
  const spec = MODEL_SPECS[key];
  return { group: spec.group, bytes: spec.bytes, footprint: spec.footprint, yawOffset: spec.yawOffset, kit: false };
}

const SEATED_POSES = ['typing', 'reading', 'phone', 'talking', 'leaning'];
const STANDING_POSES = ['neutral', 'presenting', 'arms-crossed', 'phone', 'walking'];

/**
 * The shape code of a person for the character kit: the build is the owner's chosen avatar style (or the neutral default
 * drawn from the id), the hair shape and outfit are the person's avatar fields, and the variant is a hash of the record id
 * (cosmetic variety only). No name, no description, nothing else is read. The kit parses this string
 * (`hqAssets/characters/spec.ts`, a unit test keeps the two in step).
 */
export function figureShape(look: FigureLook, seed: string, pose: string | undefined, standing: boolean): FigureKey {
  const a = look.avatar;
  const posture = (standing ? STANDING_POSES : SEATED_POSES).includes(pose ?? '') ? pose : standing ? 'neutral' : 'typing';
  const outfit = a.outfit === 'formal' ? 'f' : a.outfit === 'uniform' ? 'u' : 's';
  const flags = `${outfit}${look.role === 'field' ? 'v' : ''}${look.headset ? 'h' : ''}${look.badge ? 'b' : ''}`;
  return `fig:${a.avatar_style[0]}:${a.hair_style}:${standing ? 't' : 's'}.${posture}:${flags}:${(seedHash(seed) % 1296000).toString(36)}`;
}

/** The model a slot's `model` string names, or null for anything this catalogue does not know. */
export function modelFor(model: string): LegacyKey | null {
  return model in MODEL_SPECS ? (model as LegacyKey) : null;
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
  /** For a person: the avatar style the renderer tints the model with (a choice, or the default drawn from the record id). */
  look?: FigureLook;
  /** For a person: the character kit shape code (see `figureShape`); the renderer builds this figure instead of loading a file. */
  shape?: FigureKey;
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
      // The look is the chosen style, else the default drawn from the seed alone (never a name).
      const look = slot.look ?? figureLook(seed, null, 'office');
      const key = look.modelKey;
      const spec = MODEL_SPECS[key];
      out.push({
        id: `figure:${index}:${slot.x.toFixed(2)},${slot.z.toFixed(2)}`, group: 'figure', key,
        x: slot.x, y: slot.y, z: slot.z, yaw: yawOfQuarterTurns(slot.rot), scale: 1, footprint: spec.footprint, look,
        shape: figureShape(slot.headset === true && !look.headset ? { ...look, headset: true } : look, seed, slot.pose, slot.standing === true),
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
