/**
 * The shape code of a character and the cosmetic variety drawn from it. Pure: no three.js, no DOM.
 *
 * TRUTH RULES (enforced by `test/hqCharacters.test.ts`):
 *  - the build (masculine, feminine, neutral) is ONLY the avatar style the owner chose for the person, or the neutral
 *    default drawn from the record id; it is part of the code, so no function here can read it from anything else;
 *  - everything else (hair length, head covering, garment set, glasses, beard, body width, shoes) is a deterministic
 *    draw from the variant number, which is a hash of the record id. It is decoration, never a claim about the person;
 *  - no function in this folder takes a name.
 */
import { seeded } from '../../lib/hqGeometry.ts';

export type Build = 'm' | 'f' | 'n';
export type Hair = 'spiky' | 'parted' | 'bun' | 'long' | 'swept';
export type SeatedPose = 'typing' | 'reading' | 'phone' | 'talking' | 'leaning';
export type StandingPose = 'neutral' | 'presenting' | 'arms-crossed' | 'phone' | 'walking';
export type OutfitCode = 'f' | 's' | 'u';
export type Headwear = 'none' | 'scarf' | 'cap';
export type Garment = 'shirt' | 'blouse' | 'polo' | 'jacket' | 'skirt' | 'dress' | 'vest';

export type CharacterSpec = Readonly<{
  build: Build;
  hair: Hair;
  standing: boolean;
  pose: SeatedPose | StandingPose;
  outfit: OutfitCode;
  hivis: boolean;
  headset: boolean;
  badge: boolean;
  variant: number;
  /** 0 close up (about a thousand triangles), 1 for a busy scene, 2 for a very crowded one (a few hundred and about a hundred and fifty triangles). */
  lod: 0 | 1 | 2;
}>;

const BUILDS: readonly string[] = ['m', 'f', 'n'];
const HAIRS: readonly string[] = ['spiky', 'parted', 'bun', 'long', 'swept'];
const SEATED: readonly string[] = ['typing', 'reading', 'phone', 'talking', 'leaning'];
const STANDING: readonly string[] = ['neutral', 'presenting', 'arms-crossed', 'phone', 'walking'];

/** `fig:<build>:<hair>:<s|t>.<pose>:<outfit><flags>:<variant in base 36>[:l|:f]`. The entry chunk writes the same format (a test checks it). */
export function shapeCode(s: CharacterSpec): string {
  return `fig:${s.build}:${s.hair}:${s.standing ? 't' : 's'}.${s.pose}:${s.outfit}${s.hivis ? 'v' : ''}${s.headset ? 'h' : ''}${s.badge ? 'b' : ''}:${s.variant.toString(36)}${s.lod === 2 ? ':f' : s.lod === 1 ? ':l' : ''}`;
}

export function parseShape(code: string): CharacterSpec | null {
  const p = code.split(':');
  if (p[0] !== 'fig' || p.length < 6) return null;
  const [, build, hair, posture, flags, variant, lod] = p;
  if (!BUILDS.includes(build) || !HAIRS.includes(hair) || !posture || !flags || !variant) return null;
  const [kind, pose] = posture.split('.');
  const standing = kind === 't';
  if ((kind !== 's' && kind !== 't') || !(standing ? STANDING : SEATED).includes(pose)) return null;
  if (!/^[fsu][vhb]*$/.test(flags)) return null;
  const n = parseInt(variant, 36);
  if (!Number.isFinite(n)) return null;
  return {
    build: build as Build, hair: hair as Hair, standing, pose: pose as SeatedPose | StandingPose, outfit: flags[0] as OutfitCode,
    hivis: flags.includes('v'), headset: flags.includes('h'), badge: flags.includes('b'), variant: n, lod: lod === 'l' ? 1 : lod === 'f' ? 2 : 0,
  };
}

export type Variation = Readonly<{
  headwear: Headwear;
  garment: Garment;
  glasses: boolean;
  beard: boolean;
  /** Overall width of the body, 0.94 to 1.06. */
  width: number;
  longSleeves: boolean;
  /** Shoe colour (sRGB hex). */
  shoe: string;
  /** How much lighter the collar and cuffs are than the top, 0 to 1. */
  trimMix: number;
  /** The eye colour (sRGB hex). */
  eye: string;
  freckles: boolean;
  /** Whether the outfit shows a second colour (a tie, a stripe, a scarf edge). */
  accented: boolean;
}>;

const SHOES = ['#1b1d22', '#2a1f1a', '#3a2c22', '#e8e6e0', '#4a3a2a', '#1f2a3a'];
const EYES = ['#2a1d14', '#3b2a1c', '#1c1c20', '#2f3d4a', '#41513a'];

/** The decoration of a figure, a pure function of the spec (so the same id always dresses the same). */
export function variationOf(s: CharacterSpec): Variation {
  const r = seeded(`character:${s.variant}:${s.build}`);
  const garmentRoll = r(), wear = r(), sight = r(), beardRoll = r(), widthRoll = r(), shoeRoll = r(), eyeRoll = r(), trim = r(), sleeve = r(), fr = r(), acc = r();
  let garment: Garment;
  if (s.outfit === 'u') garment = s.build === 'f' && garmentRoll < 0.3 ? 'blouse' : 'polo';
  else if (s.outfit === 'f') garment = 'jacket';
  else if (s.build === 'm') garment = garmentRoll < 0.55 ? 'shirt' : garmentRoll < 0.8 ? 'polo' : 'vest';
  else if (s.build === 'f') garment = garmentRoll < 0.34 ? 'blouse' : garmentRoll < 0.58 ? 'skirt' : garmentRoll < 0.78 ? 'dress' : 'jacket';
  else garment = garmentRoll < 0.5 ? 'shirt' : garmentRoll < 0.75 ? 'vest' : 'polo';
  const headwear: Headwear = s.build === 'f' ? (wear < 0.2 ? 'scarf' : 'none') : s.build === 'n' ? (wear < 0.07 ? 'scarf' : wear < 0.17 ? 'cap' : 'none') : (wear < 0.1 ? 'cap' : 'none');
  return {
    headwear, garment,
    glasses: sight < 0.24,
    beard: s.build === 'm' && beardRoll < 0.28,
    width: 0.94 + widthRoll * 0.12,
    longSleeves: garment === 'polo' ? false : garment === 'vest' ? sleeve < 0.7 : true,
    shoe: SHOES[Math.floor(shoeRoll * SHOES.length)],
    trimMix: 0.55 + trim * 0.4,
    eye: EYES[Math.floor(eyeRoll * EYES.length)],
    freckles: fr < 0.12,
    accented: acc < 0.5,
  };
}
