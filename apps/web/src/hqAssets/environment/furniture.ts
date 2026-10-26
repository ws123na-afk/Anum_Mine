/**
 * Street furniture: benches, lamps (with night glow points), fences, a gate house with a barrier,
 * a bus shelter and bins. `rot` is a heading in radians from +z towards +x.
 */
import { MeshBuilder, hex, mix, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { GROUND, TEAL, WARM, WINDOW_BASE, anumMark } from '../../lib/hqProps.ts';
import { Frame, scoped, type AssetResult, type GlowPoint } from './common.ts';
import { lengths, directionAt, ribbon, subPath, type P2 } from './paths.ts';

const STEEL = hex('#2a313b');
const WOOD = hex('#9a7a52');
const CONCRETE = hex('#b9b4a6');
const GLASS = hex('#7fb2cc');

export const BENCH_TRIANGLES_CAP = 140;

/** A park bench: seven slats, steel legs and arms. `style: 'stone'` gives a concrete block bench. */
export function bench(b: MeshBuilder, x: number, z: number, o: { rot?: number; style?: 'slat' | 'stone' } = {}): AssetResult {
  return scoped(b, [], (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    if ((o.style ?? 'slat') === 'stone') {
      f.box(-0.9, 0, -0.25, 1.8, 0.4, 0.5, CONCRETE, { grad: 0.1 });
      f.box(-0.95, 0.4, -0.3, 1.9, 0.07, 0.6, scale(CONCRETE, 1.1));
      return;
    }
    for (let i = 0; i < 4; i++) f.box(-0.9, 0.42, -0.24 + i * 0.13, 1.8, 0.05, 0.1, WOOD, { skip: ['bottom'] });
    for (let i = 0; i < 3; i++) f.box(-0.9, 0.62 + i * 0.13, -0.3 + i * 0.01, 1.8, 0.09, 0.04, WOOD, { skip: ['bottom'] });
    for (const sx of [-0.8, 0.8]) {
      f.box(sx - 0.03, 0, -0.26, 0.06, 0.42, 0.52, STEEL, { skip: ['bottom'] });
      f.box(sx - 0.03, 0.42, -0.3, 0.06, 0.04, 0.58, STEEL, { skip: ['bottom'] });
      f.box(sx - 0.03, 0.42, -0.32, 0.06, 0.55, 0.05, STEEL, { skip: ['bottom'] });
    }
  });
}

export type LampKind = 'street' | 'garden' | 'bollard' | 'double';
export const LAMP_TRIANGLES_CAP = 140;

/**
 * A lamp. The lit head is emissive (it glows with the night factor) and a `GlowPoint` is returned
 * for a halo or light pool. Pass `pool: true` to also draw a flat warm pool on the ground (emissive 0.22);
 * it is off by default because it reads as a pale patch on asphalt by day.
 */
export function lamp(b: MeshBuilder, x: number, z: number, o: { kind?: LampKind; rot?: number; height?: number; pool?: boolean } = {}): AssetResult {
  const kind = o.kind ?? 'street';
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const H = o.height ?? (kind === 'street' || kind === 'double' ? 6.5 : kind === 'garden' ? 3.2 : 0.9);
    const radius = kind === 'street' || kind === 'double' ? 9 : kind === 'garden' ? 4.5 : 1.8;
    if (o.pool === true) m.disc(x, 0.046, z, radius * 0.45, 8, mix(GROUND, WARM, 0.1), 0.22);
    if (kind === 'bollard') {
      f.cyl(0, 0, 0, 'y', 0.13, 0.12, H, 8, hex('#59636e'), { capEnd: true });
      f.cyl(0, H, 0, 'y', 0.13, 0.13, 0.12, 8, WARM, { emissive: 1, capEnd: true });
      glow.push({ x, y: H, z, radius, color: WARM, intensity: 0.35 });
      return;
    }
    f.cyl(0, 0, 0, 'y', 0.18, 0.1, 0.35, 8, STEEL, { capEnd: true });
    f.cyl(0, 0.35, 0, 'y', 0.1, 0.07, H - 0.35, 8, STEEL);
    if (kind === 'garden') {
      f.box(-0.2, H, -0.2, 0.4, 0.34, 0.4, WARM, { emissive: 1, flat: true });
      f.box(-0.28, H + 0.34, -0.28, 0.56, 0.07, 0.56, STEEL);
      const q = f.p(0, H + 0.1, 0);
      glow.push({ x: q[0], y: q[1], z: q[2], radius, color: WARM, intensity: 0.5 });
      return;
    }
    const arms = kind === 'double' ? [-1, 1] : [1];
    for (const a of arms) {
      f.box(a > 0 ? 0 : -1.25, H - 0.06, -0.04, 1.25, 0.08, 0.08, STEEL);
      f.box(a * 1.25 - 0.3, H - 0.12, -0.14, 0.6, 0.1, 0.28, WARM, { emissive: 1, flat: true });
      f.box(a * 1.25 - 0.34, H - 0.02, -0.18, 0.68, 0.06, 0.36, STEEL);
      const q = f.p(a * 1.25, H - 0.2, 0);
      glow.push({ x: q[0], y: q[1], z: q[2], radius, color: WARM, intensity: 0.7 });
    }
  });
}

/** Lamps along a path every `spacing` metres, `offset` metres to its right; the arm points at the path. */
export function lampLine(b: MeshBuilder, pts: readonly P2[], o: { spacing?: number; offset?: number; kind?: LampKind; closed?: boolean } = {}): AssetResult {
  const closed = o.closed ?? false;
  const { total } = lengths(pts, closed);
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const off = o.offset ?? 0;
    for (let s = (o.spacing ?? 24) * 0.5; s < total; s += o.spacing ?? 24) {
      const d = directionAt(pts, closed, s);
      const x = d.p[0] - d.dz * off, z = d.p[1] + d.dx * off;
      const rot = off >= 0 ? Math.atan2(d.dx, d.dz) : Math.atan2(-d.dx, -d.dz);
      glow.push(...lamp(m, x, z, { kind: o.kind ?? 'street', rot }).glow);
    }
  });
}

// ---------------------------------------------------------------- fences

export type FenceKind = 'bar' | 'rail' | 'mesh';
export const FENCE_TRIANGLES_PER_METRE_CAP = 14;

/**
 * A fence along a path. `bar`: steel posts, top and bottom rails and vertical bars; `rail`: timber
 * posts and two rails; `mesh`: tall posts with thin wire rails (tennis courts). `gaps` removes
 * sections as [startMetre, endMetre] pairs (gates).
 */
export function fence(
  b: MeshBuilder, pts: readonly P2[], o: { kind?: FenceKind; height?: number; closed?: boolean; gaps?: ReadonlyArray<readonly [number, number]>; color?: Rgb; postSpacing?: number; barSpacing?: number } = {},
): AssetResult {
  const kind = o.kind ?? 'bar';
  const closed = o.closed ?? false;
  const H = o.height ?? (kind === 'mesh' ? 3.2 : kind === 'bar' ? 1.5 : 0.9);
  const col = o.color ?? (kind === 'rail' ? hex('#8a6b46') : hex('#2f3a45'));
  const { total } = lengths(pts, closed);
  const gaps = o.gaps ?? [];
  const inGap = (s: number) => gaps.some((g) => s > g[0] - 0.01 && s < g[1] + 0.01);
  const step = o.postSpacing ?? (kind === 'mesh' ? 3 : 2.4);
  return scoped(b, [], (m) => {
    const sections: Array<[number, number]> = [];
    let cur = 0;
    for (const g of [...gaps].sort((a, c) => a[0] - c[0])) { if (g[0] > cur) sections.push([cur, g[0]]); cur = Math.max(cur, g[1]); }
    if (cur < total) sections.push([cur, total]);
    for (const [s0, s1] of sections) {
      const seg = subPath(pts, closed, s0, s1);
      if (seg.length < 2) continue;
      const rails = kind === 'bar' ? [0.12, H - 0.06] : kind === 'rail' ? [H * 0.4, H * 0.85] : [0.15, H * 0.5, H - 0.08];
      for (const y of rails) {
        const t = kind === 'mesh' ? 0.03 : 0.06;
        ribbon(m, seg, false, -t, y, t, y, col);
        ribbon(m, seg, false, -t, y, t, y + t * 2, col, 'right');
        ribbon(m, seg, false, -t, y, t, y + t * 2, col, 'left');
      }
      if (kind === 'bar') {
        for (let s = s0 + 0.2; s < s1; s += o.barSpacing ?? 0.36) {
          const d = directionAt(pts, closed, s);
          const x = d.p[0], z = d.p[1];
          const nx = -d.dz * 0.018, nz = d.dx * 0.018;
          const A = [x - nx, 0.12, z - nz], B2 = [x + nx, 0.12, z + nz], C = [x + nx, H - 0.06, z + nz], D = [x - nx, H - 0.06, z - nz];
          m.quad(A, B2, C, D, col);
          m.quad(A, D, C, B2, scale(col, 0.85));
        }
      }
    }
    // Posts last so they cover the rail ends.
    for (let s = 0; s <= total + 1e-6; s += step) {
      if (inGap(s)) continue;
      const d = directionAt(pts, closed, Math.min(s, total));
      const pw = kind === 'mesh' ? 0.07 : kind === 'rail' ? 0.1 : 0.08;
      m.box(d.p[0] - pw, 0, d.p[1] - pw, pw * 2, H + (kind === 'bar' ? 0.08 : 0), pw * 2, scale(col, 1.1), { skip: ['bottom'] });
    }
    for (const g of gaps) for (const s of [g[0], g[1]]) {
      const d = directionAt(pts, closed, s);
      m.box(d.p[0] - 0.14, 0, d.p[1] - 0.14, 0.28, H + 0.3, 0.28, scale(CONCRETE, 0.95), { skip: ['bottom'] });
    }
  });
}

// ---------------------------------------------------------------- gate house

export const GATE_HOUSE_TRIANGLES_CAP = 760;

/**
 * A gate house with a canopy over one lane, a glazed booth (lit windows at night), an ANUM mark on
 * the canopy front, a striped barrier arm (raised or lowered) and bollards. The booth faces the
 * lane; the lane runs along local z.
 */
export function gateHouse(b: MeshBuilder, x: number, z: number, o: { rot?: number; open?: boolean; laneWidth?: number; mark?: boolean } = {}): AssetResult {
  const glow: GlowPoint[] = [];
  const lane = o.laneWidth ?? 4.2;
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const bz = 6.5;
    f.shadow(-lane / 2 - 3, -bz / 2, lane + 6, bz, 4.3);
    // Island and booth.
    f.box(lane / 2 + 0.2, 0, -bz / 2, 2.8, 0.2, bz, CONCRETE, { grad: 0.1 });
    f.box(lane / 2 + 0.5, 0.2, -1.5, 2.2, 3.0, 3.0, hex('#e6e1d6'), { grad: 0.1 });
    for (const [wx, wz, ww, wd, face] of [[lane / 2 + 0.5 - 0.012, -1.2, 0.02, 2.4, 'left'], [lane / 2 + 0.5 + 0.7, 1.5 + 0.012, 0.8, 0.02, 'front'], [lane / 2 + 0.5 + 0.7, -1.5 - 0.012 - 0.02, 0.8, 0.02, 'back']] as const) {
      const hh = 1.3;
      f.box(wx, 1.2, wz, ww, hh, wd, GLASS, { emissive: WINDOW_BASE + 0.6, flat: true, skip: face === 'left' ? ['right'] : [] });
    }
    glow.push({ ...(() => { const q = f.p(lane / 2 + 1.5, 2.0, 0); return { x: q[0], y: q[1], z: q[2] }; })(), radius: 5, color: WARM, intensity: 0.6 });
    // Canopy: two columns and a slab.
    const cw = lane + 3.6;
    for (const sx of [-lane / 2 - 0.3, lane / 2 + 0.2]) {
      f.box(sx - 0.2, 0.2, -2.4, 0.4, 3.9, 0.4, hex('#d6d1c4'), { grad: 0.08 });
    }
    f.box(-lane / 2 - 0.9, 4.1, -bz / 2, cw + 0.4, 0.35, bz, hex('#eae6dc'));
    f.box(-lane / 2 - 0.9, 4.1, -bz / 2, cw + 0.4, 0.08, bz, TEAL, { emissive: 0.5, flat: true, skip: ['top'] });
    f.box(-lane / 2 - 0.6, 4.02, -2.0, cw - 0.2, 0.06, 4.0, WARM, { emissive: 0.9, flat: true, skip: ['top'] });
    if (o.mark !== false) {
      const q = f.p(0, 4.7, bz / 2 + 0.03);
      // The mark is drawn on a wall facing world +z, so only add it when the frame is unrotated.
      if ((o.rot ?? 0) === 0) anumMark(m, q[0], q[1], q[2], 0.9);
    }
    // Barrier: base box, arm of alternating segments.
    const bx = lane / 2 - 0.2;
    f.box(bx, 0.2, 2.2, 0.5, 1.1, 0.5, hex('#c9362e'), { grad: 0.1 });
    const open = o.open ?? false;
    for (let i = 0; i < 8; i++) {
      const t0 = i / 8, t1 = (i + 1) / 8;
      const c = i % 2 === 0 ? hex('#f1efe8') : hex('#c9362e');
      if (open) f.box(bx + 0.15, 1.3 + t0 * 3.4, 2.4, 0.2, (t1 - t0) * 3.4, 0.1, c, { flat: true });
      else f.box(bx - t1 * lane, 1.3, 2.4, (t1 - t0) * lane, 0.12, 0.1, c, { flat: true });
    }
    if (!open) f.box(bx - lane - 0.1, 0.2, 2.3, 0.14, 1.2, 0.3, STEEL);
    for (const bz2 of [-bz / 2 + 0.3, bz / 2 - 0.3]) for (const bxx of [lane / 2 - 0.15, -lane / 2 + 0.1]) {
      f.cyl(bxx, 0, bz2, 'y', 0.14, 0.14, 0.9, 6, hex('#59636e'), { capEnd: true });
    }
  });
}

// ---------------------------------------------------------------- shelter and bin

export const SHELTER_TRIANGLES_CAP = 200;

/** A bus shelter: roof, three glass panels, bench and a blank light box. */
export function busShelter(b: MeshBuilder, x: number, z: number, o: { rot?: number } = {}): AssetResult {
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    f.shadow(-1.8, -0.7, 3.6, 1.4, 2.7);
    for (const sx of [-1.7, 1.7]) f.box(sx - 0.04, 0, -0.62, 0.08, 2.5, 0.08, STEEL);
    f.box(-1.9, 2.5, -0.8, 3.8, 0.12, 1.6, hex('#d6dbe0'));
    f.box(-1.7, 0.1, -0.64, 3.4, 2.3, 0.03, GLASS, { flat: true });
    for (const sx of [-1.7, 1.7]) f.box(sx - 0.015, 0.1, -0.6, 0.03, 2.3, 1.2, GLASS, { flat: true });
    f.box(-1.2, 0.45, -0.5, 2.4, 0.06, 0.4, WOOD);
    f.box(-1.2, 0, -0.5, 0.06, 0.45, 0.4, STEEL);
    f.box(1.14, 0, -0.5, 0.06, 0.45, 0.4, STEEL);
    f.box(1.0, 0.2, 0.3, 0.8, 2.0, 0.12, hex('#dfe6ee'), { emissive: 0.5, flat: true });
    const q = f.p(0, 2.4, 0);
    glow.push({ x: q[0], y: q[1], z: q[2], radius: 4, color: WARM, intensity: 0.4 });
  });
}

/** A litter bin. */
export function bin(b: MeshBuilder, x: number, z: number): AssetResult {
  return scoped(b, [], (m) => {
    const f = new Frame(m, x, 0, z, 0);
    f.cyl(0, 0, 0, 'y', 0.28, 0.26, 0.85, 8, hex('#3d4650'), { capEnd: true });
    f.cyl(0, 0.85, 0, 'y', 0.3, 0.3, 0.06, 8, hex('#59636e'), { capEnd: true });
  });
}
