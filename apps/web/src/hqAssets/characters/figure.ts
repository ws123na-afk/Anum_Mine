/**
 * The character kit: a detailed low-poly person built from lofted limbs, an ellipsoid head with a readable face and
 * real proportions (about seven and a half heads tall), in three builds (masculine, feminine, neutral). Seated poses sit
 * at a desk (the hip joint is 0.5 m above the floor, the same frame as `hqAssets/interior/figures.ts`), standing poses
 * stand on the floor. Origin: the point between the hips on the floor line, the figure faces +z.
 *
 * Every triangle has a role (skin, hair, top, bottom, trim, accent) so the renderer tints it per person, or keeps its own
 * colour (eyes, shoes, a safety vest, a headset). Close up (`lod` 0) a figure is about 1,100 triangles, `lod` 1 about 350.
 * Pure: the three.js bridge is `bake.ts`.
 */
import { ROLE, Mesh, abox, add, dot, ellipsoid, len, lerp, loft, mul, norm, ringOf, rotation, strip, sub, toData, type MeshData, type RoleId, type Ring, type V3 } from './mesh.ts';
import { parseShape, variationOf, type CharacterSpec, type SeatedPose, type StandingPose, type Variation } from './spec.ts';

/** Triangle ceilings, enforced by the unit test: close up and the distant version. */
export const CHARACTER_TRIANGLE_CAP = 1200;
export const CHARACTER_LITE_TRIANGLE_CAP = 500;
export const CHARACTER_FAR_TRIANGLE_CAP = 360;

type Frame = Readonly<{
  sh: number; chestX: number; chestZ: number; waistX: number; waistZ: number; hipX: number; hipZ: number;
  arm: number; neck: number; head: readonly [number, number, number]; thigh: number; calf: number; jaw: number; brow: number; lip: number;
}>;
const FRAMES: Record<'m' | 'f' | 'n', Frame> = {
  m: { sh: 0.215, chestX: 0.19, chestZ: 0.125, waistX: 0.165, waistZ: 0.113, hipX: 0.165, hipZ: 0.118, arm: 0.047, neck: 0.056, head: [0.08, 0.108, 0.094], thigh: 0.09, calf: 0.064, jaw: 0.074, brow: 0.012, lip: 0.006 },
  f: { sh: 0.181, chestX: 0.15, chestZ: 0.118, waistX: 0.118, waistZ: 0.091, hipX: 0.172, hipZ: 0.118, arm: 0.036, neck: 0.041, head: [0.073, 0.102, 0.088], thigh: 0.088, calf: 0.055, jaw: 0.056, brow: 0.007, lip: 0.009 },
  n: { sh: 0.198, chestX: 0.168, chestZ: 0.115, waistX: 0.14, waistZ: 0.1, hipX: 0.158, hipZ: 0.112, arm: 0.041, neck: 0.049, head: [0.076, 0.105, 0.091], thigh: 0.084, calf: 0.059, jaw: 0.065, brow: 0.009, lip: 0.007 },
};

export const rgb = (hex: string): readonly [number, number, number] => [parseInt(hex.slice(1, 3), 16) / 255, parseInt(hex.slice(3, 5), 16) / 255, parseInt(hex.slice(5, 7), 16) / 255];
const K = { sclera: rgb('#f1eee8'), glass: rgb('#3f4852'), headset: rgb('#20262d'), phone: rgb('#12161b'), paper: rgb('#f2f0ea'), vest: rgb('#c9dd3a'), reflect: rgb('#e9edf0'), sole: rgb('#1a1b1e'), whiteSole: rgb('#d9d6ce'), rose: rgb('#a9514e') };

/** The two bone solution: the elbow for a shoulder, a wrist target and the two bone lengths, bending towards `pole`. */
export function elbowFor(s: V3, w: V3, l1: number, l2: number, pole: V3): { elbow: V3; wrist: V3 } {
  const d = sub(w, s);
  const dist0 = len(d);
  const dir = norm(d);
  const dist = Math.min(dist0, (l1 + l2) * 0.992);
  const wrist = dist0 > dist ? add(s, mul(dir, dist)) : w;
  const a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist);
  const h = Math.sqrt(Math.max(0, l1 * l1 - a * a));
  const pv = norm(sub(pole, mul(dir, dot(pole, dir))));
  return { elbow: add(s, add(mul(dir, a), mul(pv, h))), wrist };
}

type Arm = Readonly<{ wrist: V3; pole: V3; hand?: 'flat' | 'open' | 'phone' | 'fist'; handTilt?: number }>;
type Pose = Readonly<{ lean: number; pitch: number; yaw: number; roll: number; left: Arm; right: Arm; step: number; props?: 'paper' | 'phone' }>;

/** Wrist targets in the figure frame; +x is the figure's right. The seated numbers reach the keyboard of the desk stations. */
function poseOf(s: CharacterSpec, H: number): Pose {
  const keys = (x: number): Arm => ({ wrist: [x, 0.8, 0.4], pole: [Math.sign(x) * 0.5, -1, -0.25], hand: 'flat', handTilt: -0.35 });
  if (!s.standing) {
    switch (s.pose as SeatedPose) {
      case 'reading': return { lean: 0.09, pitch: 0.26, yaw: 0, roll: 0, left: { wrist: [-0.13, 0.83, 0.34], pole: [-0.5, -1, -0.2], hand: 'flat', handTilt: -0.3 }, right: { wrist: [0.13, 0.83, 0.34], pole: [0.5, -1, -0.2], hand: 'flat', handTilt: -0.3 }, step: 0, props: 'paper' };
      case 'phone': return { lean: 0.02, pitch: 0.02, yaw: -0.14, roll: -0.1, left: keys(-0.1), right: { wrist: [0.135, 1.1, 0.13], pole: [0.55, -1, 0.1], hand: 'phone', handTilt: 0.3 }, step: 0, props: 'phone' };
      case 'talking': return { lean: 0.04, pitch: 0.02, yaw: 0.16, roll: 0.04, left: keys(-0.11), right: { wrist: [0.3, 1.0, 0.36], pole: [0.6, -1, 0], hand: 'open', handTilt: 0.15 }, step: 0 };
      case 'leaning': return { lean: -0.1, pitch: -0.05, yaw: 0, roll: 0, left: { wrist: [-0.3, 0.74, 0.27], pole: [-0.6, -1, -0.3], hand: 'flat', handTilt: -0.2 }, right: { wrist: [0.3, 0.74, 0.27], pole: [0.6, -1, -0.3], hand: 'flat', handTilt: -0.2 }, step: 0 };
      default: return { lean: 0.04, pitch: 0.08, yaw: 0, roll: 0, left: keys(-0.1), right: keys(0.1), step: 0 };
    }
  }
  const down = (x: number): Arm => ({ wrist: [x, H + 0.03, 0.05], pole: [Math.sign(x) * 0.6, -0.4, -0.5], hand: 'flat', handTilt: -0.9 });
  switch (s.pose as StandingPose) {
    case 'presenting': return { lean: 0, pitch: 0, yaw: 0.12, roll: 0, left: down(-0.27), right: { wrist: [0.4, 1.33, 0.4], pole: [0.6, -1, -0.2], hand: 'open', handTilt: 0.1 }, step: 0 };
    case 'arms-crossed': return { lean: 0, pitch: 0, yaw: 0, roll: 0, left: { wrist: [0.12, 1.2, 0.17], pole: [-0.3, -1, 0.5], hand: 'fist' }, right: { wrist: [-0.12, 1.15, 0.2], pole: [0.3, -1, 0.5], hand: 'fist' }, step: 0 };
    case 'phone': return { lean: 0, pitch: 0.04, yaw: -0.14, roll: -0.08, left: down(-0.27), right: { wrist: [0.125, 1.56, 0.1], pole: [0.8, -0.6, -0.4], hand: 'phone', handTilt: 0 }, step: 0, props: 'phone' };
    case 'walking': return { lean: 0.02, pitch: 0, yaw: 0, roll: 0, left: { wrist: [-0.27, 0.95, 0.24], pole: [-0.5, -1, -0.5], hand: 'flat', handTilt: -0.5 }, right: { wrist: [0.27, 0.95, -0.18], pole: [0.5, -1, -0.5], hand: 'flat', handTilt: -0.5 }, step: 0.2 };
    default: return { lean: 0, pitch: 0, yaw: 0, roll: 0, left: down(-0.27), right: down(0.27), step: 0 };
  }
}

const pt = (x: number, y: number, z: number): V3 => [x, y, z];

export function buildCharacterMesh(spec: CharacterSpec): Mesh {
  const m = new Mesh();
  const B = FRAMES[spec.build];
  const V = variationOf(spec);
  const far = spec.lod === 2;
  const lite = spec.lod >= 1;
  const S = far ? 4 : lite ? 6 : 8;           // segments round a torso
  const L = far ? 4 : lite ? 5 : 7;           // segments round a limb
  const ST = far ? 2 : lite ? 4 : 7;          // stacks of a head
  const H = spec.standing ? 0.9 : 0.5;
  const P = poseOf(spec, H);
  const w = V.width;
  const lean = P.lean;
  const zAt = (h: number): number => lean * (h / 0.54);
  const skin = ROLE.skin, top = ROLE.top, bottom = ROLE.bottom, trim = ROLE.trim, accent = ROLE.accent, keep = ROLE.keep;
  const jacket = V.garment === 'jacket';
  const longDress = V.garment === 'dress';
  const skirt = V.garment === 'skirt';
  const sleeves = V.longSleeves;

  // ---- legs and shoes
  const hx = (spec.build === 'm' ? 0.095 : spec.build === 'f' ? 0.092 : 0.09) * w;
  const legRole: RoleId = longDress ? top : bottom;
  for (const sx of [-1, 1]) {
    const x = sx * hx;
    const stepZ = sx * P.step;
    let hip: V3, knee: V3, ankle: V3;
    if (spec.standing) { hip = pt(x, H, 0); knee = pt(x * 1.08, H - 0.42, stepZ * 0.5 + 0.02); ankle = pt(x * 1.1, 0.085, stepZ); }
    else { hip = pt(x, H + 0.02, -0.01); knee = pt(x * 1.12, H + 0.04, 0.44); ankle = pt(x * 1.2, 0.09, 0.5); }
    const bare = skirt;
    const thighRole: RoleId = skirt ? bottom : legRole;
    // Thigh and shin as tapered lofts; a knee ball hides the bend.
    loft(m, [ringOf(hip, sub(knee, hip), B.thigh * w, B.thigh * w * 0.95), ringOf(knee, sub(knee, hip), B.calf * 1.12 * w, B.calf * 1.1 * w)], L, thighRole, 0.96);
    loft(m, [ringOf(knee, sub(ankle, knee), B.calf * 1.1 * w, B.calf * 1.08 * w), ringOf(ankle, sub(ankle, knee), B.calf * 0.72 * w, B.calf * 0.74 * w)], L, bare && !spec.standing ? skin : legRole, 0.93);
    if (!lite) ellipsoid(m, knee, B.calf * 1.1 * w, B.calf * 1.1 * w, B.calf * 1.1 * w, 6, 2, bare && !spec.standing ? skin : thighRole, 0.97, { from: 0.35, to: 1 });
    // Shoe: three rings along z, a sole under them.
    const sz = ankle[2];
    const shoeY = ankle[1];
    const toe = lite ? 0.1 : 0.13;
    if (lite) abox(m, pt(ankle[0], 0.035, sz + 0.05), 0.08 * w, 0.06, 0.24, keep, rgb(V.shoe));
    else loft(m, [ringOf(pt(ankle[0], shoeY - 0.02, sz - 0.05), [0, 0, 1], 0.036 * w, 0.034, [1, 0, 0]), ringOf(pt(ankle[0], shoeY - 0.04, sz + 0.05), [0, 0, 1], 0.042 * w, 0.03, [1, 0, 0]), ringOf(pt(ankle[0], shoeY - 0.055, sz + toe + 0.05), [0, 0, 1], 0.036 * w, 0.02, [1, 0, 0])], 5, keep, rgb(V.shoe), { capEnd: true });
    if (!lite) abox(m, pt(ankle[0], 0.007, sz + 0.05), 0.088 * w, 0.014, 0.25, keep, V.shoe === '#e8e6e0' ? K.whiteSole : K.sole);
  }

  // ---- the pelvis and the skirt or dress
  const pelvis: Ring[] = [
    ringOf(pt(0, H + 0.14, zAt(0.14) * 0), [0, 1, 0], B.waistX * w * 0.97, B.waistZ * 0.96),
    ringOf(pt(0, H - 0.04, 0.0), [0, 1, 0], B.hipX * w * 1.02, B.hipZ * 1.02),
  ];
  if (!skirt && !longDress) loft(m, pelvis, S, bottom, 0.95);
  else loft(m, pelvis, S, skirt ? bottom : top, 0.95);
  if (skirt || longDress) {
    // A skirt or a long dress (abaya style): seated it lies over the thighs, standing it hangs to the knee or the ankle.
    const hem = longDress ? 0.12 : 0.5;
    if (spec.standing) {
      const wide = longDress ? 0.235 : 0.2;
      loft(m, [ringOf(pt(0, H + 0.05, 0), [0, 1, 0], B.hipX * w * 1.04, B.hipZ * 1.04), ringOf(pt(0, H - 0.25, 0), [0, 1, 0], wide * 0.84 * w, wide * 0.7 * w), ringOf(pt(0, hem, 0), [0, 1, 0], wide * w, wide * 0.82 * w)], S, skirt ? bottom : top, 0.94);
    } else {
      loft(m, [ringOf(pt(0, H + 0.06, 0.0), [0, 0, 1], 0.185 * w, 0.1, [1, 0, 0]), ringOf(pt(0, H + 0.06, 0.22), [0, 0, 1], 0.19 * w, 0.082, [1, 0, 0]), ringOf(pt(0, H + 0.05, 0.43), [0, 0, 1], 0.178 * w, 0.075, [1, 0, 0])], S, skirt ? bottom : top, 0.93, { capEnd: true });
      if (longDress) for (const sx of [-1, 1]) loft(m, [ringOf(pt(sx * hx * 1.12, H + 0.02, 0.45), [0, -1, 0.05], 0.085 * w, 0.075 * w), ringOf(pt(sx * hx * 1.2, 0.3, 0.48), [0, -1, 0.02], 0.088 * w, 0.078 * w), ringOf(pt(sx * hx * 1.2, 0.1, 0.5), [0, -1, 0], 0.092 * w, 0.082 * w)], L, top, 0.9);
    }
  }
  // ---- torso
  const bust = spec.build === 'f' ? 0.014 : 0;
  const ringT = (h: number, rx: number, rz: number, dz = 0): Ring => ringOf(pt(0, H + h, zAt(h) + dz), [0, 1, 0], rx * w, rz);
  const torsoRings: Ring[] = [
    ringT(0.07, B.hipX * 0.985, B.hipZ * 0.99),
    ringT(0.16, B.waistX, B.waistZ),
    ringT(0.37, B.chestX, B.chestZ, bust),
    ringT(0.47, B.sh - 0.03, 0.108),
    ringT(0.535, B.neck * 1.9, 0.07),
  ];
  const bodyRole: RoleId = top;
  const bodyShade = V.garment === 'blouse' ? 1.02 : 1;
  if (!longDress) loft(m, torsoRings, S, bodyRole, bodyShade, { fade: [0.9, 1.05] });
  else loft(m, [ringT(0.07, B.hipX * 1.0, B.hipZ), ...torsoRings.slice(1)], S, top, 1, { fade: [0.9, 1.05] });
  // Shoulders: caps so the arms meet the torso without a seam.
  const shoulderY = H + 0.5;
  const shoulder = (sx: number): V3 => pt(sx * B.sh * w, shoulderY, zAt(0.5) - 0.005);
  if (!lite) for (const sx of [-1, 1]) ellipsoid(m, shoulder(sx), (B.arm + 0.004) * w, B.arm + 0.004, B.arm + 0.004, 6, lite ? 2 : 3, sleeveRole(V), 1, { from: 0.3, to: 1 });

  // Garment details.
  const neckBase = H + 0.54;
  if (jacket) {
    // Lapels and the shirt front, with a tie when the outfit shows a second colour.
    const f = zAt(0.4) + B.chestZ * 0.93;
    m.flat(pt(-0.06 * w, H + 0.52, zAt(0.52) + 0.07), pt(0.06 * w, H + 0.52, zAt(0.52) + 0.07), pt(0, H + 0.2, f + 0.01 + bust * 0.5), trim, 1);
    if (!lite) {
      m.flat(pt(-0.06 * w, H + 0.52, zAt(0.52) + 0.074), pt(-0.12 * w, H + 0.48, zAt(0.48) + 0.1), pt(-0.012, H + 0.22, f + 0.014 + bust * 0.5), top, 0.78);
      m.flat(pt(0.06 * w, H + 0.52, zAt(0.52) + 0.074), pt(0.012, H + 0.22, f + 0.014 + bust * 0.5), pt(0.12 * w, H + 0.48, zAt(0.48) + 0.1), top, 0.78);
    }
    if (V.accented && spec.build !== 'f') m.flat(pt(-0.018, H + 0.5, zAt(0.5) + 0.078), pt(0.018, H + 0.5, zAt(0.5) + 0.078), pt(0, H + 0.2, f + 0.016), accent, 1);
    else if (spec.build === 'f' && !lite) m.flat(pt(-0.045, H + 0.52, zAt(0.52) + 0.073), pt(0.045, H + 0.52, zAt(0.52) + 0.073), pt(0, H + 0.36, f + 0.012 + bust * 0.5), accent, 0.95);
  } else if (V.garment !== 'vest') {
    // A collar ring (and a placket line for a shirt).
    loft(m, [ringOf(pt(0, neckBase - 0.012, zAt(0.54)), [0, 1, 0], B.neck * 1.35, B.neck * 1.3), ringOf(pt(0, neckBase + 0.022, zAt(0.57)), [0, 1, 0], B.neck * 1.22, B.neck * 1.18)], L, trim, 1);
    if (!lite && (V.garment === 'shirt' || V.garment === 'blouse')) m.flat(pt(-0.006, H + 0.5, zAt(0.5) + 0.103), pt(0.006, H + 0.5, zAt(0.5) + 0.103), pt(0, H + 0.1, B.hipZ + 0.01), trim, 0.95);
  } else {
    // A knitted vest over a shirt: a V of shirt, the vest in the top colour.
    m.flat(pt(-0.065 * w, H + 0.53, zAt(0.52) + 0.07), pt(0.065 * w, H + 0.53, zAt(0.52) + 0.07), pt(0, H + 0.32, zAt(0.4) + B.chestZ * 0.95), trim, 1);
    loft(m, [ringOf(pt(0, neckBase - 0.012, zAt(0.54)), [0, 1, 0], B.neck * 1.3, B.neck * 1.26), ringOf(pt(0, neckBase + 0.02, zAt(0.57)), [0, 1, 0], B.neck * 1.2, B.neck * 1.16)], L, trim, 1);
  }
  if (spec.hivis) {
    const vest: Ring[] = [ringT(0.08, B.hipX * 1.0 + 0.012, B.hipZ + 0.012), ringT(0.17, B.waistX + 0.012, B.waistZ + 0.012), ringT(0.37, B.chestX + 0.012, B.chestZ + 0.014, bust), ringT(0.47, B.sh - 0.07, 0.108 + 0.01)];
    loft(m, vest, S, keep, K.vest);
    if (!lite) {
      loft(m, [ringT(0.2, B.waistX + 0.015, B.waistZ + 0.015), ringT(0.235, B.waistX + 0.0165, B.waistZ + 0.0165)], S, keep, K.reflect);
      loft(m, [ringT(0.33, B.chestX + 0.0145, B.chestZ + 0.0165, bust), ringT(0.365, B.chestX + 0.0155, B.chestZ + 0.0175, bust)], S, keep, K.reflect);
    }
  }
  if (spec.badge) {
    // A lanyard and a badge in the accent colour.
    const bz = zAt(0.3) + B.chestZ + 0.02;
    strip(m, pt(-0.045, H + 0.53, zAt(0.5) + 0.075), pt(-0.02, H + 0.3, bz), 0.012, 0.004, accent, 0.9);
    strip(m, pt(0.045, H + 0.53, zAt(0.5) + 0.075), pt(0.02, H + 0.3, bz), 0.012, 0.004, accent, 0.9);
    abox(m, pt(0, H + 0.27, bz + 0.004), 0.07, 0.095, 0.008, keep, K.paper);
    abox(m, pt(0, H + 0.295, bz + 0.009), 0.05, 0.02, 0.004, accent, 1);
  }

  // ---- neck
  loft(m, [ringOf(pt(0, H + 0.49, zAt(0.5)), [0, 1, 0.12], B.neck * w, B.neck * w), ringOf(pt(0, H + 0.64, zAt(0.62) + 0.012), [0, 1, 0.12], B.neck * 0.92 * w, B.neck * 0.9 * w)], L, skin, 0.88);

  // ---- arms
  const rA = B.arm * w;
  const arm = (sx: number, a: Arm): void => {
    const sp = shoulder(sx);
    const l1 = 0.3, l2 = 0.265;
    const { elbow, wrist } = elbowFor(sp, pt(a.wrist[0], a.wrist[1], a.wrist[2]), l1, l2, a.pole);
    const upperRole = sleeveRole(V), foreRole = sleeveRole(V);
    const cut = 0.55;
    // Upper arm: sleeve, or sleeve to the middle then skin for a short sleeve.
    if (sleeves) loft(m, [ringOf(sp, sub(elbow, sp), rA * 1.04, rA * 1.0), ringOf(elbow, sub(elbow, sp), rA * 0.86, rA * 0.84)], L, upperRole, 0.96);
    else {
      loft(m, [ringOf(sp, sub(elbow, sp), rA * 1.05, rA * 1.02), ringOf(lerp(sp, elbow, cut), sub(elbow, sp), rA * 1.02, rA * 1.0)], L, top, 0.96);
      loft(m, [ringOf(lerp(sp, elbow, cut - 0.02), sub(elbow, sp), rA * 0.99, rA * 0.97), ringOf(elbow, sub(elbow, sp), rA * 0.86, rA * 0.84)], L, skin, 0.97);
    }
    if (!lite) ellipsoid(m, elbow, rA * 0.88, rA * 0.88, rA * 0.88, 5, 2, sleeves ? foreRole : skin, 0.95);
    loft(m, [ringOf(elbow, sub(wrist, elbow), rA * 0.86, rA * 0.84), ringOf(wrist, sub(wrist, elbow), rA * 0.6, rA * 0.56)], L, sleeves ? foreRole : skin, sleeves ? 0.93 : 0.97);
    if (sleeves && !lite && jacket) loft(m, [ringOf(lerp(elbow, wrist, 0.9), sub(wrist, elbow), rA * 0.64, rA * 0.6), ringOf(wrist, sub(wrist, elbow), rA * 0.64, rA * 0.6)], L, trim, 1);
    hand(m, wrist, sub(wrist, elbow), a, sx, rA, lite, spec.build, far);
  };
  arm(-1, P.left); arm(1, P.right);

  // ---- props
  if (P.props === 'paper') abox(m, pt(0, 0.822, 0.4), 0.28, 0.012, 0.21, keep, K.paper);
  if (P.props === 'phone') abox(m, pt(0.108, spec.standing ? 1.605 : 1.205, 0.085), 0.03, 0.115, 0.012, keep, K.phone);

  // ---- head
  head(m, spec, V, B, H, P, S, ST, zAt);
  return m;
}

/** The sleeve role of an arm: jackets, shirts and dresses take the top colour, a knitted vest leaves the shirt sleeves in the trim colour. */
function sleeveRole(V: Variation): RoleId {
  return V.garment === 'dress' || V.garment === 'jacket' || V.garment === 'blouse' || V.garment === 'shirt' || V.garment === 'skirt' ? ROLE.top : V.garment === 'vest' ? ROLE.trim : ROLE.top;
}

/** A hand: a flat palm and fingers along the forearm, a thumb, and a closed fist or an open hand for the gestures. */
function hand(m: Mesh, wrist: V3, forearm: V3, a: Arm, sx: number, rA: number, lite: boolean, build: 'm' | 'f' | 'n', far = false): void {
  const dir = norm(add(norm(forearm), [0, a.handTilt ?? -0.2, 0]));
  const wide = rA * (build === 'm' ? 0.78 : 0.7), thin = rA * 0.42;
  const side: V3 = a.hand === 'phone' || a.hand === 'open' ? [0, 0, 1] : [1, 0, 0];
  const palmEnd = add(wrist, mul(dir, 0.075));
  if (lite) { if (far) return; ellipsoid(m, add(wrist, mul(dir, 0.05)), 0.03, 0.03, 0.03, 5, 2, ROLE.skin, 1); return; }
  if (a.hand === 'fist') {
    ellipsoid(m, add(wrist, mul(dir, 0.04)), 0.036, 0.034, 0.04, 6, 4, ROLE.skin, 1);
    return;
  }
  const fingerEnd = add(palmEnd, mul(norm(add(dir, [0, a.hand === 'flat' ? -0.25 : 0, 0])), a.hand === 'phone' ? 0.045 : 0.066));
  loft(m, [ringOf(wrist, dir, wide * 0.92, thin, side), ringOf(palmEnd, dir, wide * 1.1, thin * 0.9, side), ringOf(fingerEnd, dir, wide * 0.82, thin * 0.6, side)], 6, ROLE.skin, 1, { capEnd: true });
  // Thumb on the inner side.
  const inner = sx > 0 ? -1 : 1;
  const sideV = norm(side);
  abox(m, add(add(wrist, mul(dir, 0.04)), mul(sideV, inner * wide * 1.05)), 0.02, 0.02, 0.04, ROLE.skin, 0.98);
}

function head(m: Mesh, spec: CharacterSpec, V: Variation, B: Frame, H: number, P: Pose, S: number, ST: number, zAt: (h: number) => number): void {
  const lite = spec.lod >= 1;
  const far = spec.lod === 2;
  const hc: V3 = pt(0, H + 0.725, zAt(0.725) + (P.pitch > 0 ? 0.012 : 0));
  const [rx, ry, rz] = B.head;
  const skin = ROLE.skin, hair = ROLE.hair, keep = ROLE.keep;
  const pivot: V3 = pt(0, H + 0.6, zAt(0.6));
  m.with(rotation(pivot, P.pitch, P.yaw, P.roll), () => {
    // One skull-and-jaw surface: the widest part at the temples, a jaw that is squarer (masculine) or finer (feminine), a chin.
    const jw = B.jaw / rx;
    const prof: readonly (readonly [number, number, number, number])[] = far
      ? [[1.0, 0, 0, 0], [0.2, 1, 1, 0], [-0.6, jw, 0.85, 0.014], [-1.06, 0, 0, 0.03]]
      : lite
      ? [[1.0, 0, 0, 0], [0.55, 0.9, 0.92, 0], [0.0, 1, 1, 0.002], [-0.45, jw * 0.95, 0.95, 0.012], [-0.78, jw * 0.6, 0.72, 0.026], [-1, 0, 0, 0.03]]
      : [[1.0, 0, 0, 0], [0.88, 0.62, 0.66, -0.004], [0.55, 1, 1, 0], [0.15, 1, 1, 0.002], [-0.25, 0.97, 0.97, 0.006], [-0.55, jw * 1.0, 0.92, 0.012], [-0.82, jw * 0.64, 0.74, 0.022], [-1.0, jw * 0.3, 0.5, 0.03], [-1.06, 0, 0, 0.03]];
    loft(m, prof.map(([y, kx, kz, dz]) => ringOf(add(hc, pt(0, y * ry, dz)), [0, 1, 0], rx * kx, rz * kz)), far ? 5 : lite ? 6 : 9, skin, 1);
    if (!lite) {
      for (const sx of [-1, 1]) abox(m, add(hc, pt(sx * (rx + 0.003), -0.006, -0.006)), 0.014, 0.05, 0.034, skin, 0.93);
      loft(m, [ringOf(add(hc, pt(0, 0.004, rz * 0.93)), [0, -0.3, 1], 0.006, 0.008), ringOf(add(hc, pt(0, -0.026, rz * 0.97 + 0.016)), [0, -0.3, 1], 0.013, 0.012)], 4, skin, 0.97, { capEnd: true });
      // Eyes: white, a dark iris and a brow each side; a mouth.
      for (const sx of [-1, 1]) {
        const ex = sx * rx * 0.43, ey = 0.016, ez = rz * 0.9;
        abox(m, add(hc, pt(ex, ey, ez - 0.004)), 0.027, 0.015, 0.008, keep, K.sclera);
        abox(m, add(hc, pt(ex + sx * 0.001, ey, ez + 0.0005)), 0.013, 0.013, 0.006, keep, rgb(V.eye));
        const bx = sx * rx * 0.43;
        strip(m, add(hc, pt(bx - sx * 0.018, ey + 0.036 - (spec.build === 'f' ? 0.0 : 0.004), ez - 0.002)), add(hc, pt(bx + sx * 0.018, ey + 0.042 - (spec.build === 'f' ? 0.004 : 0), ez - 0.002)), B.brow * 1.0 + 0.002, 0.007, hair, 0.9, [0, 0, 1]);
      }
      if (spec.build === 'f') {
        abox(m, add(hc, pt(0, -0.052, rz * 0.98 + 0.012)), 0.034, 0.006, 0.008, keep, K.rose);
        abox(m, add(hc, pt(0, -0.06, rz * 0.98 + 0.012)), 0.036, 0.009, 0.008, keep, K.rose);
      } else abox(m, add(hc, pt(0, -0.056, rz * 0.98 + 0.012)), 0.036, 0.007, 0.008, skin, 0.7);
    } else if (!far) {
      for (const sx of [-1, 1]) abox(m, add(hc, pt(sx * rx * 0.43, 0.016, rz * 0.9)), 0.022, 0.013, 0.006, keep, rgb('#2a2220'));
    }
    if (V.freckles && !lite) for (const sx of [-1, 1]) abox(m, add(hc, pt(sx * 0.042, -0.012, rz * 0.86)), 0.022, 0.008, 0.004, skin, 0.84);
    hairAndWear(m, spec, V, B, hc, lite);
    if (V.glasses && !lite) {
      for (const sx of [-1, 1]) {
        const gx = sx * rx * 0.43;
        loft(m, [ringOf(add(hc, pt(gx, 0.016, rz * 0.9 + 0.008)), [0, 0, 1], 0.026, 0.02), ringOf(add(hc, pt(gx, 0.016, rz * 0.9 + 0.013)), [0, 0, 1], 0.026, 0.02)], 8, keep, K.glass);
        strip(m, add(hc, pt(gx + sx * 0.026, 0.02, rz * 0.9 + 0.01)), add(hc, pt(sx * (rx + 0.004), 0.02, 0.0)), 0.004, 0.004, keep, K.glass);
      }
      abox(m, add(hc, pt(0, 0.022, rz * 0.9 + 0.012)), 0.02, 0.005, 0.005, keep, K.glass);
    }
    if (spec.headset) {
      const hs = K.headset;
      for (const sx of [-1, 1]) abox(m, add(hc, pt(sx * (rx + 0.013), -0.004, 0.0)), 0.024, 0.056, 0.05, keep, hs);
      loft(m, [ringOf(add(hc, pt(0, 0.0, 0)), [1, 0, 0], ry * 1.02, rz * 1.02, [0, 1, 0]), ringOf(add(hc, pt(0.0, 0.0, 0)), [1, 0, 0], ry * 1.02, rz * 1.02, [0, 1, 0])], 3, keep, hs);
      strip(m, add(hc, pt(-(rx + 0.012), -0.02, 0.025)), add(hc, pt(-0.03, -0.06, rz * 0.93)), 0.008, 0.008, keep, hs);
      abox(m, add(hc, pt(-0.028, -0.062, rz * 0.96)), 0.022, 0.014, 0.014, keep, hs);
    }
  });
}

function hairAndWear(m: Mesh, spec: CharacterSpec, V: Variation, B: Frame, hc: V3, lite: boolean): void {
  const hair = ROLE.hair, accent = ROLE.accent;
  const [rx, ry, rz] = B.head;
  const S = spec.lod === 2 ? 5 : lite ? 6 : 9, ST = spec.lod === 2 ? 2 : lite ? 3 : 4;
  const scarf = V.headwear === 'scarf';
  const cap = V.headwear === 'cap';
  const PI = Math.PI;
  if (scarf) {
    // A head covering in the accent colour: crown, sides and back, a wrap under the chin and a drape to the shoulders. The face stays open.
    ellipsoid(m, add(hc, pt(0, 0.008, -0.004)), rx + 0.013, ry + 0.012, rz + 0.012, S, ST + 1, accent, 0.98, { from: 0.6, to: 1 });
    ellipsoid(m, add(hc, pt(0, -0.018, -0.008)), rx + 0.013, ry + 0.008, rz + 0.012, S, ST, accent, 0.94, { from: 0.18, to: 0.64, arc: [PI * 0.92, PI * 2.08] });
    loft(m, [ringOf(add(hc, pt(0, -0.1, -0.006)), [0, 1, 0], rx * 0.92, rz * 0.9), ringOf(add(hc, pt(0, -0.165, -0.01)), [0, 1, 0], rx * 1.25, rz * 1.05), ringOf(add(hc, pt(0, -0.215, -0.012)), [0, 1, 0], rx * 2.15, rz * 1.35)], S, accent, 0.9, { arc: [PI * 0.9, PI * 2.1] });
    loft(m, [ringOf(add(hc, pt(0, -0.095, 0.0)), [0, 1, 0], B.neck * 1.45, B.neck * 1.4), ringOf(add(hc, pt(0, -0.16, 0.005)), [0, 1, 0], B.neck * 1.8, B.neck * 1.65)], 8, accent, 0.92);
    if (!lite) strip(m, add(hc, pt(-rx * 0.9, 0.07, rz * 0.62)), add(hc, pt(rx * 0.9, 0.07, rz * 0.62)), 0.004, 0.008, accent, 0.86);
    return;
  }
  const cropTop = cap ? 0.5 : 0.6;
  switch (spec.hair) {
    case 'spiky': {
      ellipsoid(m, add(hc, pt(0, 0.012, -0.006)), rx * 1.05, ry * 1.04, rz * 1.04, S, ST, hair, 1, { from: 0.58, to: 1 });
      if (!cap && !lite) for (const [ax, ay, az, h] of [[0, 1, 0.1, 0.04], [0.55, 0.85, 0.2, 0.036], [-0.55, 0.85, 0.2, 0.036], [0.3, 0.8, -0.55, 0.034], [-0.3, 0.8, -0.55, 0.034]] as const) {
        const d = norm(pt(ax, ay, az));
        const base = add(hc, pt(d[0] * rx * 1.03, d[1] * ry * 1.02 + 0.012, d[2] * rz * 1.03));
        loft(m, [ringOf(base, d, 0.017, 0.017), ringOf(add(base, mul(d, h)), d, 0.002, 0.002)], 4, hair, 1);
      }
      ellipsoid(m, add(hc, pt(0, -0.012, -0.03)), rx * 1.02, 0.075, rz * 0.93, S, 3, hair, 0.92, { arc: [PI * 0.9, PI * 2.1], from: 0.3, to: 1 });
      break;
    }
    case 'parted': {
      ellipsoid(m, add(hc, pt(0, 0.012, -0.006)), rx * 1.05, ry * 1.04, rz * 1.04, S, ST, hair, 1, { from: cropTop, to: 1 });
      ellipsoid(m, add(hc, pt(0, -0.012, -0.03)), rx * 1.02, 0.075, rz * 0.93, S, 3, hair, 0.92, { arc: [PI * 0.9, PI * 2.1], from: 0.3, to: 1 });
      if (!cap) strip(m, add(hc, pt(-0.062, 0.066, rz * 0.7)), add(hc, pt(0.052, 0.088, rz * 0.62)), 0.03, 0.02, hair, 1.02, [0, 1, 0]);
      for (const sx of [-1, 1]) abox(m, add(hc, pt(sx * (rx * 1.02), 0.022, 0.0)), 0.012, 0.05, 0.05, hair, 0.95);
      break;
    }
    case 'bun': {
      ellipsoid(m, add(hc, pt(0, 0.012, -0.006)), rx * 1.04, ry * 1.03, rz * 1.03, S, ST, hair, 1, { from: cropTop + 0.02, to: 1 });
      ellipsoid(m, add(hc, pt(0, -0.01, -0.03)), rx * 1.02, 0.078, rz * 0.93, S, 3, hair, 0.93, { arc: [PI * 0.9, PI * 2.1], from: 0.3, to: 1 });
      if (!cap) ellipsoid(m, add(hc, pt(0, 0.07, -rz * 0.95)), 0.044, 0.04, 0.044, lite ? 5 : 7, lite ? 3 : 4, hair, 1);
      if (!lite && !cap) loft(m, [ringOf(add(hc, pt(0, 0.052, -rz * 0.8)), [0, 0.4, -1], 0.012, 0.012), ringOf(add(hc, pt(0, 0.07, -rz * 0.88)), [0, 0.4, -1], 0.012, 0.012)], 5, accent, 0.9);
      break;
    }
    case 'long': {
      ellipsoid(m, add(hc, pt(0, 0.012, -0.006)), rx * 1.05, ry * 1.04, rz * 1.04, S, ST, hair, 1, { from: cropTop, to: 1 });
      // Back curtain to the shoulder blades and a fall each side of the face.
      loft(m, [ringOf(add(hc, pt(0, 0.05, -0.04)), [0, 1, 0], rx * 1.04, rz * 0.85), ringOf(add(hc, pt(0, -0.12, -0.062)), [0, 1, 0], rx * 1.1, rz * 0.6), ringOf(add(hc, pt(0, -0.27, -0.07)), [0, 1, 0], rx * 1.15, rz * 0.45)], S, hair, 0.94, { arc: [PI * 0.9, PI * 2.1], fade: [1, 0.86] });
      for (const sx of [-1, 1]) loft(m, [ringOf(add(hc, pt(sx * (rx * 1.03), 0.045, 0.012)), [0, -1, 0.05], 0.02, 0.05), ringOf(add(hc, pt(sx * (rx * 1.08), -0.09, 0.0)), [0, -1, 0], 0.024, 0.052), ringOf(add(hc, pt(sx * (rx * 1.1), -0.2, 0.0)), [0, -1, 0], 0.02, 0.046)], 5, hair, 0.96);
      break;
    }
    default: {
      // Medium, swept: a bob to the jaw with a side swept fringe.
      ellipsoid(m, add(hc, pt(0, 0.012, -0.006)), rx * 1.05, ry * 1.04, rz * 1.04, S, ST, hair, 1, { from: cropTop, to: 1 });
      loft(m, [ringOf(add(hc, pt(0, 0.05, -0.035)), [0, 1, 0], rx * 1.05, rz * 0.85), ringOf(add(hc, pt(0, -0.07, -0.05)), [0, 1, 0], rx * 1.1, rz * 0.66), ringOf(add(hc, pt(0, -0.125, -0.045)), [0, 1, 0], rx * 1.12, rz * 0.5)], S, hair, 0.94, { arc: [PI * 0.9, PI * 2.1] });
      if (!cap) strip(m, add(hc, pt(-0.066, 0.07, rz * 0.68)), add(hc, pt(0.05, 0.09, rz * 0.66)), 0.034, 0.022, hair, 1.02, [0, 1, 0]);
      for (const sx of [-1, 1]) abox(m, add(hc, pt(sx * (rx * 1.04), 0.0, 0.0)), 0.016, 0.07, 0.06, hair, 0.95);
      break;
    }
  }
  if (cap) {
    ellipsoid(m, add(hc, pt(0, 0.012, -0.004)), rx * 1.1, ry * 1.09, rz * 1.1, S, ST, accent, 0.96, { from: 0.56, to: 1 });
    strip(m, add(hc, pt(0, 0.048, rz * 1.04)), add(hc, pt(0, 0.036, rz * 1.04 + 0.05)), 0.1, 0.01, accent, 0.9);
  }
  if (V.beard && !lite) {
    ellipsoid(m, add(hc, pt(0, -0.052, 0.012)), B.jaw + 0.006, 0.06, rz * 0.86 + 0.006, 8, 4, hair, 0.88, { from: 0.0, to: 0.62, arc: [PI * 0.02, PI * 0.98] });
    abox(m, add(hc, pt(0, -0.044, rz * 0.9)), 0.05, 0.011, 0.012, hair, 0.9);
  }
}

export function buildCharacter(spec: CharacterSpec): MeshData { return toData(buildCharacterMesh(spec)); }

/** The mesh for a shape code, or null for a code this kit does not know. */
export function buildFromCode(code: string): MeshData | null {
  const spec = parseShape(code);
  return spec ? buildCharacter(spec) : null;
}
