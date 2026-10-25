/**
 * Detailed people for the headquarters interiors.
 *
 * USE RULE: these figures are only for a place that a persisted record backs: a desk with an
 * assigned agent (`seatedFigure`) or an assigned standing post. No room builder in this folder ever
 * places one by itself, there are no crowds and no "walking staff". The look comes from a string seed
 * (use the agent id) so the same agent always looks the same; it is never a likeness of anyone.
 *
 * Frame: `x`, `z` is the hip centre (seated) or the point between the feet (standing), `y` the floor.
 * `rot` is the quarter turn (0..3) of the direction the figure faces; rot 0 faces +z.
 */
import { MeshBuilder, hex, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { Local, type P3 } from './local.ts';
import type { FigureLook } from '../../lib/hqAvatar.ts';

export type SeatedPose = 'typing' | 'reading' | 'phone' | 'talking' | 'leaning';
export type StandingPose = 'neutral' | 'presenting' | 'arms-crossed' | 'phone' | 'walking';

export type FigureOptions = Readonly<{
  seed: string;
  rot?: number;
  /** Overall size factor, 1 is about 1.75 m standing. */
  size?: number;
  /** Wear a call-centre headset (customer care desks). */
  headset?: boolean;
  /** The avatar style for this record (a choice or the default from the id): colours and hair of the procedural figure and the model tints. */
  look?: FigureLook;
}>;

export type SeatedOptions = FigureOptions & Readonly<{ pose?: SeatedPose }>;
export type StandingOptions = FigureOptions & Readonly<{ pose?: StandingPose }>;

/** Measured ceilings, enforced by the unit test. */
export const FIGURE_TRIANGLE_CAP = 380;

const SHIRTS = ['#e9ecef', '#2f6fb0', '#8a3f5c', '#3d8a6b', '#c27a2b', '#5b4b9a', '#2b8aa0', '#a8453b', '#d8d2c4', '#7c93a8'].map(hex);
const JACKETS = ['#1f2937', '#2b3a4f', '#3a3f4a', '#4a4036', '#1d3b34'].map(hex);
const SKINS = ['#f0c7a0', '#d9a77e', '#b9825a', '#8a5a3b', '#e8b890', '#6e452d'].map(hex);
const HAIRS = ['#1d1a18', '#3a2a1f', '#6a4a2a', '#222a33', '#7a5a3a', '#a89c8c'].map(hex);
const TROUSERS = ['#26313b', '#3a3f4a', '#4a4036', '#1f2a44', '#5a5a5a'].map(hex);
const SHOES = hex('#14181d');

type Look = Readonly<{
  shirt: Rgb; jacket: Rgb | null; tie: Rgb | null; skin: Rgb; hair: Rgb; trousers: Rgb;
  hairStyle: 'short' | 'long' | 'bun' | 'cap'; glasses: boolean; skirt: boolean;
}>;

const HAIR_SHAPE: Record<string, Look['hairStyle']> = { long: 'long', swept: 'long', bun: 'bun', spiky: 'cap', parted: 'short' };

/** The procedural look of a styled figure: the same colours and hair family as the model tints. */
function styledLook(seed: string, v: FigureLook): Look {
  const r = seeded(`figure:${seed}`);
  const formal = v.avatar.outfit === 'formal';
  return {
    shirt: hex(formal ? v.trim : v.top), jacket: formal || v.avatar.outfit === 'uniform' ? hex(v.top) : null,
    tie: formal ? hex(v.accent) : null, skin: hex(v.skin), hair: hex(v.hair), trousers: hex(v.bottom),
    hairStyle: HAIR_SHAPE[v.avatar.hair_style] ?? 'short', glasses: r() < 0.2, skirt: false,
  };
}

function lookFor(seed: string, styled?: FigureLook): Look {
  if (styled) return styledLook(seed, styled);
  const r = seeded(`figure:${seed}`);
  const pick = <T,>(a: readonly T[]): T => a[Math.floor(r() * a.length)];
  const styleRoll = r();
  return {
    shirt: pick(SHIRTS),
    jacket: r() < 0.45 ? pick(JACKETS) : null,
    tie: r() < 0.3 ? pick([hex('#a8453b'), hex('#2f4b66'), hex('#c9a24a')]) : null,
    skin: pick(SKINS), hair: pick(HAIRS), trousers: pick(TROUSERS),
    hairStyle: styleRoll < 0.4 ? 'short' : styleRoll < 0.65 ? 'long' : styleRoll < 0.8 ? 'bun' : 'cap',
    glasses: r() < 0.28,
    skirt: false,
  };
}

function head(L: Local, look: Look, cy: number, cz: number, headset: boolean, tilt = 0): void {
  const { skin, hair } = look;
  L.limb([0, cy - 0.2, cz - 0.02 - tilt * 0.4], [0, cy - 0.08, cz], 0.09, 0.09, 0.085, 0.085, skin);
  L.blob(0, cy + 0.02, cz + 0.01, 0.092, 0.115, 0.1, skin, 8);
  // Ears, nose and eyes.
  L.box(-0.1, cy - 0.01, cz - 0.01, 0.018, 0.05, 0.03, scale(skin, 0.92));
  L.box(0.082, cy - 0.01, cz - 0.01, 0.018, 0.05, 0.03, scale(skin, 0.92));
  L.box(-0.012, cy - 0.03, cz + 0.095, 0.024, 0.04, 0.025, scale(skin, 0.95));
  L.box(-0.05, cy + 0.015, cz + 0.088, 0.022, 0.014, 0.012, hex('#1b1f24'));
  L.box(0.028, cy + 0.015, cz + 0.088, 0.022, 0.014, 0.012, hex('#1b1f24'));
  L.box(-0.03, cy - 0.065, cz + 0.085, 0.06, 0.012, 0.012, scale(skin, 0.7));
  switch (look.hairStyle) {
    case 'long':
      L.blob(0, cy + 0.07, cz - 0.005, 0.1, 0.075, 0.108, hair, 8);
      L.box(-0.105, cy - 0.17, cz - 0.1, 0.21, 0.26, 0.07, hair);
      break;
    case 'bun':
      L.blob(0, cy + 0.07, cz - 0.005, 0.1, 0.07, 0.108, hair, 8);
      L.blob(0, cy + 0.15, cz - 0.09, 0.05, 0.05, 0.05, hair, 6);
      break;
    case 'cap':
      L.blob(0, cy + 0.065, cz - 0.01, 0.097, 0.05, 0.103, hair, 8);
      break;
    default:
      L.blob(0, cy + 0.07, cz - 0.005, 0.1, 0.07, 0.11, hair, 8);
      L.box(-0.1, cy + 0.02, cz - 0.08, 0.2, 0.08, 0.05, hair);
  }
  if (look.glasses) {
    L.box(-0.07, cy + 0.01, cz + 0.095, 0.058, 0.04, 0.012, hex('#4a5560'));
    L.box(0.012, cy + 0.01, cz + 0.095, 0.058, 0.04, 0.012, hex('#4a5560'));
    L.box(-0.012, cy + 0.025, cz + 0.095, 0.024, 0.01, 0.012, hex('#4a5560'));
  }
  if (headset) {
    L.box(-0.108, cy + 0.0, cz - 0.02, 0.03, 0.06, 0.06, hex('#20262d'));
    L.box(0.078, cy + 0.0, cz - 0.02, 0.03, 0.06, 0.06, hex('#20262d'));
    L.box(-0.1, cy + 0.12, cz - 0.04, 0.2, 0.025, 0.04, hex('#20262d'));
    L.limb([-0.1, cy - 0.04, cz + 0.02], [-0.04, cy - 0.07, cz + 0.1], 0.015, 0.015, 0.014, 0.014, hex('#20262d'));
    L.box(-0.055, cy - 0.08, cz + 0.095, 0.03, 0.025, 0.02, hex('#20262d'));
  }
}

function arm(L: Local, look: Look, side: -1 | 1, shoulder: P3, elbow: P3, wrist: P3): void {
  const sleeve = look.jacket ?? look.shirt;
  L.limb(shoulder, elbow, 0.085, 0.085, 0.07, 0.07, sleeve);
  L.limb(elbow, wrist, 0.07, 0.07, 0.055, 0.055, sleeve, { cap: true });
  const dir: P3 = [wrist[0] - elbow[0], wrist[1] - elbow[1], wrist[2] - elbow[2]];
  const len = Math.hypot(...dir) || 1;
  const hand: P3 = [wrist[0] + (dir[0] / len) * 0.08, wrist[1] + (dir[1] / len) * 0.08, wrist[2] + (dir[2] / len) * 0.08];
  L.limb(wrist, hand, 0.06, 0.035, 0.05, 0.03, look.skin, { cap: true });
  void side;
}

function torso(L: Local, look: Look, hip: P3, chest: P3, shoulderY: number, shoulderZ: number): void {
  const body = look.jacket ?? look.shirt;
  L.limb(hip, chest, 0.34, 0.22, 0.4, 0.24, body, { helper: [0, 0, 1] });
  // Shoulders, collar, shirt front and tie.
  L.box(-0.215, shoulderY - 0.07, shoulderZ - 0.1, 0.43, 0.08, 0.2, body);
  if (look.jacket) {
    L.box(-0.05, shoulderY - 0.28, shoulderZ + 0.1, 0.1, 0.27, 0.012, look.shirt);
    if (look.tie) L.box(-0.018, shoulderY - 0.32, shoulderZ + 0.113, 0.036, 0.27, 0.01, look.tie);
  } else {
    L.box(-0.07, shoulderY - 0.04, shoulderZ + 0.09, 0.14, 0.03, 0.02, scale(look.shirt, 0.88));
  }
  void hip;
}

/** A seated professional. Chair, desk and screen are drawn by the desk station, not here. */
export function seatedFigure(b: MeshBuilder, x: number, y: number, z: number, o: SeatedOptions): void {
  // A loadable person model may replace this figure: the slot carries the seed (the assignment), the tagged triangles are the fallback.
  b.slots.push({ model: 'person', x, y, z, rot: o.rot ?? 0, width: 0.5, seed: o.seed, pose: o.pose ?? 'typing', headset: o.headset === true || o.look?.headset === true, look: o.look });
  b.tagged('figure', () => drawSeated(b, x, y, z, o));
}

function drawSeated(b: MeshBuilder, x: number, y: number, z: number, o: SeatedOptions): void {
  const look = lookFor(o.seed, o.look);
  const k = o.size ?? 1;
  const pose = o.pose ?? 'typing';
  const lean = pose === 'leaning' ? -0.1 : pose === 'reading' ? 0.1 : 0.04;
  const sh = 1.02, hipY = 0.5;
  const w = scaled(new Local(b, x, z, o.rot ?? 0, y), k);
  // Legs: thighs forward, shins down, shoes.
  for (const sx of [-1, 1]) {
    const hx = sx * 0.1;
    w.limb([hx, hipY + 0.04, 0], [hx * 1.15, hipY + 0.03, 0.44], 0.15, 0.14, 0.12, 0.11, look.trousers);
    w.limb([hx * 1.15, hipY + 0.02, 0.44], [hx * 1.2, 0.08, 0.5], 0.11, 0.11, 0.08, 0.08, look.trousers);
    w.box(hx * 1.2 - 0.055, 0, 0.4, 0.11, 0.07, 0.27, SHOES);
  }
  w.box(-0.18, hipY - 0.02, -0.14, 0.36, 0.14, 0.3, look.trousers);
  torso(w, look, [0, hipY + 0.08, -0.02], [0, sh - 0.02, lean], sh, lean);
  const hz = lean + 0.02;
  head(w, look, sh + 0.2, hz + (pose === 'reading' ? 0.06 : 0), o.headset === true || o.look?.headset === true);
  const sL: P3 = [-0.22, sh - 0.04, lean - 0.01], sR: P3 = [0.22, sh - 0.04, lean - 0.01];
  switch (pose) {
    case 'reading': {
      arm(w, look, -1, sL, [-0.23, 0.78, 0.1], [-0.12, 0.84, 0.42]);
      arm(w, look, 1, sR, [0.23, 0.78, 0.1], [0.12, 0.84, 0.42]);
      w.box(-0.14, 0.82, 0.38, 0.28, 0.012, 0.22, hex('#f2f0ea'));
      break;
    }
    case 'phone': {
      arm(w, look, -1, sL, [-0.24, 0.78, 0.06], [-0.1, 0.8, 0.4]);
      arm(w, look, 1, sR, [0.25, 0.8, 0.1], [0.14, 1.12, 0.14]);
      w.box(0.09, 1.1, 0.12, 0.035, 0.12, 0.03, hex('#12161b'));
      break;
    }
    case 'talking': {
      arm(w, look, -1, sL, [-0.24, 0.78, 0.06], [-0.1, 0.8, 0.4]);
      arm(w, look, 1, sR, [0.26, 0.82, 0.12], [0.34, 0.98, 0.42]);
      break;
    }
    case 'leaning': {
      arm(w, look, -1, sL, [-0.3, 0.78, 0.0], [-0.3, 0.7, 0.3]);
      arm(w, look, 1, sR, [0.3, 0.78, 0.0], [0.3, 0.7, 0.3]);
      break;
    }
    default: {
      arm(w, look, -1, sL, [-0.24, 0.8, 0.1], [-0.1, 0.8, 0.46]);
      arm(w, look, 1, sR, [0.24, 0.8, 0.1], [0.1, 0.8, 0.46]);
    }
  }
}

/** A standing professional at an assigned post (a reception point, a bench, a presenter spot). */
export function standingFigure(b: MeshBuilder, x: number, y: number, z: number, o: StandingOptions): void {
  const look = lookFor(o.seed, o.look);
  const k = o.size ?? 1;
  const pose = o.pose ?? 'neutral';
  const w = scaled(new Local(b, x, z, o.rot ?? 0, y), k);
  const hipY = 0.92, sh = 1.48;
  const step = pose === 'walking' ? 0.2 : 0;
  for (const sx of [-1, 1]) {
    const hx = sx * 0.09, f = sx * step;
    w.limb([hx, hipY, 0], [hx * 1.1, 0.08, f], 0.15, 0.15, 0.09, 0.09, look.trousers);
    w.box(hx * 1.1 - 0.055, 0, f - 0.06, 0.11, 0.07, 0.27, SHOES);
  }
  w.box(-0.18, hipY - 0.06, -0.1, 0.36, 0.16, 0.22, look.trousers);
  torso(w, look, [0, hipY + 0.04, 0], [0, sh - 0.04, 0.0], sh, 0);
  head(w, look, sh + 0.22, 0.02, o.headset === true || o.look?.headset === true);
  const sL: P3 = [-0.22, sh - 0.05, 0], sR: P3 = [0.22, sh - 0.05, 0];
  switch (pose) {
    case 'presenting':
      arm(w, look, -1, sL, [-0.28, 1.15, 0.04], [-0.3, 0.98, 0.3]);
      arm(w, look, 1, sR, [0.32, 1.28, 0.12], [0.44, 1.38, 0.46]);
      break;
    case 'arms-crossed':
      arm(w, look, -1, sL, [-0.26, 1.15, 0.08], [0.1, 1.2, 0.2]);
      arm(w, look, 1, sR, [0.26, 1.15, 0.08], [-0.1, 1.12, 0.2]);
      break;
    case 'phone':
      arm(w, look, -1, sL, [-0.26, 1.15, 0.0], [-0.24, 0.92, 0.04]);
      arm(w, look, 1, sR, [0.27, 1.25, 0.06], [0.14, 1.52, 0.12]);
      w.box(0.09, 1.52, 0.1, 0.035, 0.12, 0.03, hex('#12161b'));
      break;
    case 'walking':
      arm(w, look, -1, sL, [-0.26, 1.15, 0.02], [-0.28, 0.95, 0.24]);
      arm(w, look, 1, sR, [0.26, 1.15, 0.02], [0.28, 0.95, -0.2]);
      break;
    default:
      arm(w, look, -1, sL, [-0.25, 1.15, 0.0], [-0.26, 0.92, 0.04]);
      arm(w, look, 1, sR, [0.25, 1.15, 0.0], [0.26, 0.92, 0.04]);
  }
}

/** A frame wrapper that scales every local coordinate and size by `k` (about the origin on the floor). */
function scaled(L: Local, k: number): Local {
  if (k === 1) return L;
  return new Proxy(L, {
    get(target, prop, receiver) {
      if (prop === 'box') return (lx: number, ly: number, lz: number, w: number, h: number, d: number, c: Rgb, o?: object) => target.box(lx * k, ly * k, lz * k, w * k, h * k, d * k, c, o);
      if (prop === 'blob') return (lx: number, ly: number, lz: number, rx: number, ry: number, rz: number, c: Rgb, s?: number) => target.blob(lx * k, ly * k, lz * k, rx * k, ry * k, rz * k, c, s);
      if (prop === 'limb') return (p0: P3, p1: P3, w0: number, d0: number, w1: number, d1: number, c: Rgb, o?: object) =>
        target.limb([p0[0] * k, p0[1] * k, p0[2] * k], [p1[0] * k, p1[1] * k, p1[2] * k], w0 * k, d0 * k, w1 * k, d1 * k, c, o);
      return Reflect.get(target, prop, receiver);
    },
  });
}
