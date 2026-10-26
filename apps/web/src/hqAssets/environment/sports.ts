/**
 * Sports and landing areas: a tennis court (lines, net, wire fence with a gate, floodlights), a
 * football pitch (mown stripes, markings, goals with nets, corner flags, floodlights) and a
 * helipad (lit rim, H mark, windsock). All flat markings sit on the ground at y 0.025 to 0.04.
 */
import { MeshBuilder, hex, mix, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { GROUND, WARM } from '../../lib/hqProps.ts';
import { Frame, scoped, type AssetResult, type GlowPoint } from './common.ts';
import { circlePath, ribbon, type P2 } from './paths.ts';
import { fence } from './furniture.ts';

const LINE = hex('#f1f0e8');
const POLE = hex('#59636e');
const MARK_Y = 0.036;

export type CourtSurface = 'blue' | 'green' | 'clay';
const SURFACES: Record<CourtSurface, readonly [Rgb, Rgb]> = {
  blue: [hex('#2c5f93'), hex('#3b78b0')],
  green: [hex('#2f6a4a'), hex('#3f8760')],
  clay: [hex('#a8553a'), hex('#c06a48')],
};

export const TENNIS_TRIANGLES_CAP = 1000;
export const PITCH_TRIANGLES_CAP = 640;
export const HELIPAD_TRIANGLES_CAP = 520;

function floodlight(f: Frame, x: number, z: number, h: number, glow: GlowPoint[], aim: number): void {
  f.cyl(x, 0, z, 'y', 0.2, 0.12, h, 8, POLE, { capEnd: true });
  f.box(x - 0.9, h, z - 0.15, 1.8, 0.7, 0.3, hex('#2a313b'));
  for (let i = 0; i < 4; i++) f.box(x - 0.8 + i * 0.42, h + 0.08, z + 0.14 * aim, 0.34, 0.54, 0.05, WARM, { emissive: 1, flat: true });
  const q = f.p(x, h - 0.3, z);
  glow.push({ x: q[0], y: q[1], z: q[2], radius: 14, color: WARM, intensity: 0.8 });
}

/** A tennis court with its enclosure, centred on (x, z); the long axis runs along local z. */
export function tennisCourt(b: MeshBuilder, x: number, z: number, o: { rot?: number; surface?: CourtSurface; fenced?: boolean; floodlights?: boolean } = {}): AssetResult {
  const glow: GlowPoint[] = [];
  const [outer, inner] = SURFACES[o.surface ?? 'blue'];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const L = 23.77, W = 10.97, SW = 8.23, ow = 18.3, ol = 36.6;
    f.quadToward([-ow / 2, 0.025, -ol / 2], [ow / 2, 0.025, -ol / 2], [ow / 2, 0.025, ol / 2], [-ow / 2, 0.025, ol / 2], outer, [0, 1, 0]);
    f.quadToward([-W / 2, 0.03, -L / 2], [W / 2, 0.03, -L / 2], [W / 2, 0.03, L / 2], [-W / 2, 0.03, L / 2], inner, [0, 1, 0]);
    const strip = (x0: number, z0: number, x1: number, z1: number) => f.quadToward([x0, MARK_Y, z0], [x1, MARK_Y, z0], [x1, MARK_Y, z1], [x0, MARK_Y, z1], LINE, [0, 1, 0]);
    const t = 0.05;
    strip(-W / 2, -L / 2, W / 2, -L / 2 + 2 * t); strip(-W / 2, L / 2 - 2 * t, W / 2, L / 2);
    strip(-W / 2, -L / 2, -W / 2 + 2 * t, L / 2); strip(W / 2 - 2 * t, -L / 2, W / 2, L / 2);
    strip(-SW / 2 - t, -L / 2, -SW / 2 + t, L / 2); strip(SW / 2 - t, -L / 2, SW / 2 + t, L / 2);
    strip(-SW / 2, -6.4 - t, SW / 2, -6.4 + t); strip(-SW / 2, 6.4 - t, SW / 2, 6.4 + t);
    strip(-t, -6.4, t, 6.4); strip(-t, -L / 2, t, -L / 2 + 0.2); strip(-t, L / 2 - 0.2, t, L / 2);
    // Net: two posts, a dark net band and a white tape.
    for (const sx of [-W / 2 - 0.9, W / 2 + 0.9]) f.cyl(sx, 0, 0, 'y', 0.07, 0.07, 1.07, 6, hex('#2a313b'), { capEnd: true });
    f.quadToward([-W / 2 - 0.9, 0.12, 0], [W / 2 + 0.9, 0.12, 0], [W / 2 + 0.9, 1.0, 0], [-W / 2 - 0.9, 1.0, 0], hex('#20262e'), [0, 0, 1]);
    f.quadToward([-W / 2 - 0.9, 0.12, 0], [-W / 2 - 0.9, 1.0, 0], [W / 2 + 0.9, 1.0, 0], [W / 2 + 0.9, 0.12, 0], scale(hex('#20262e'), 1.3), [0, 0, -1]);
    f.box(-W / 2 - 0.9, 0.96, -0.03, W + 1.8, 0.1, 0.06, LINE, { flat: true });
    // Umpire chair.
    f.box(W / 2 + 1.3, 0, 0.3, 0.9, 0.12, 0.9, hex('#6b737c'));
    f.box(W / 2 + 1.5, 0.12, 0.5, 0.06, 1.9, 0.06, hex('#6b737c')); f.box(W / 2 + 1.98, 0.12, 0.5, 0.06, 1.9, 0.06, hex('#6b737c'));
    f.box(W / 2 + 1.35, 2.0, 0.4, 0.8, 0.08, 0.7, hex('#3d4650'));
    if (o.fenced !== false) {
      const hw = ow / 2 + 0.2, hl = ol / 2 + 0.2;
      const ring = [[-hw, -hl], [hw, -hl], [hw, hl], [-hw, hl]].map((p) => {
        const q = f.p(p[0], 0, p[1]);
        return [q[0], q[2]] as P2;
      });
      // A gate gap in the middle of the left long side (the fourth edge of the ring).
      const gate = 2 * (ow + 0.4) + (ol + 0.4) + (ol + 0.4) / 2;
      fence(m, ring, { kind: 'mesh', height: 3.6, closed: true, gaps: [[gate - 1.2, gate + 1.2]] });
    }
    if (o.floodlights !== false) {
      for (const [fx, fz] of [[-ow / 2 - 0.8, -L / 2 - 2], [ow / 2 + 0.8, -L / 2 - 2], [-ow / 2 - 0.8, L / 2 + 2], [ow / 2 + 0.8, L / 2 + 2]]) floodlight(f, fx, fz, 8, glow, 1);
    }
  });
}

/** A football pitch (default 64 x 40 m, small-sided) centred on (x, z); the long axis runs along local z. */
export function footballPitch(b: MeshBuilder, x: number, z: number, o: { rot?: number; length?: number; width?: number; floodlights?: boolean } = {}): AssetResult {
  const glow: GlowPoint[] = [];
  const L = o.length ?? 64, W = o.width ?? 40;
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const g0 = mix(GROUND, hex('#2f7a3a'), 0.7), g1 = scale(g0, 1.12);
    const stripes = 10, sh = L / stripes;
    f.quadToward([-W / 2 - 3, 0.022, -L / 2 - 3], [W / 2 + 3, 0.022, -L / 2 - 3], [W / 2 + 3, 0.022, L / 2 + 3], [-W / 2 - 3, 0.022, L / 2 + 3], scale(g0, 0.85), [0, 1, 0]);
    for (let i = 0; i < stripes; i++) f.quadToward([-W / 2, 0.026, -L / 2 + i * sh], [W / 2, 0.026, -L / 2 + i * sh], [W / 2, 0.026, -L / 2 + (i + 1) * sh], [-W / 2, 0.026, -L / 2 + (i + 1) * sh], i % 2 ? g1 : g0, [0, 1, 0]);
    const strip = (x0: number, z0: number, x1: number, z1: number) => f.quadToward([x0, MARK_Y, z0], [x1, MARK_Y, z0], [x1, MARK_Y, z1], [x0, MARK_Y, z1], LINE, [0, 1, 0]);
    const t = 0.07;
    strip(-W / 2, -L / 2, W / 2, -L / 2 + t * 2); strip(-W / 2, L / 2 - t * 2, W / 2, L / 2);
    strip(-W / 2, -L / 2, -W / 2 + t * 2, L / 2); strip(W / 2 - t * 2, -L / 2, W / 2, L / 2);
    strip(-W / 2, -t, W / 2, t);
    const boxW = W * 0.55, boxD = L * 0.16, goalW = W * 0.27, goalD = L * 0.055;
    for (const s of [-1, 1]) {
      const zb = s * L / 2;
      strip(-boxW / 2, zb - s * boxD - t, boxW / 2, zb - s * boxD + t); strip(-boxW / 2 - t, Math.min(zb, zb - s * boxD), -boxW / 2 + t, Math.max(zb, zb - s * boxD)); strip(boxW / 2 - t, Math.min(zb, zb - s * boxD), boxW / 2 + t, Math.max(zb, zb - s * boxD));
      strip(-goalW / 2, zb - s * goalD - t, goalW / 2, zb - s * goalD + t); strip(-goalW / 2 - t, Math.min(zb, zb - s * goalD), -goalW / 2 + t, Math.max(zb, zb - s * goalD)); strip(goalW / 2 - t, Math.min(zb, zb - s * goalD), goalW / 2 + t, Math.max(zb, zb - s * goalD));
      m.disc(...(() => { const q = f.p(0, 0, zb - s * boxD * 0.7); return [q[0], MARK_Y, q[2]] as const; })(), 0.18, 6, LINE);
      // Goal: posts, bar and a back net of two quads.
      const gw = 7.3, gh = 2.44, gd = 2.0;
      f.box(-gw / 2 - 0.06, 0, zb - 0.06, 0.12, gh, 0.12, LINE, { skip: ['bottom'], flat: true });
      f.box(gw / 2 - 0.06, 0, zb - 0.06, 0.12, gh, 0.12, LINE, { skip: ['bottom'], flat: true });
      f.box(-gw / 2, gh - 0.06, zb - 0.06, gw, 0.12, 0.12, LINE, { flat: true });
      const net = hex('#d8dde2');
      const zz = zb + s * gd;
      f.quadToward([-gw / 2, 0, zz], [gw / 2, 0, zz], [gw / 2, gh * 0.8, zz], [-gw / 2, gh * 0.8, zz], net, [0, 0, s]);
      f.quadToward([-gw / 2, gh * 0.8, zz], [gw / 2, gh * 0.8, zz], [gw / 2, gh, zb], [-gw / 2, gh, zb], scale(net, 0.9), [0, 1, 0]);
      for (const sx of [-gw / 2, gw / 2]) f.quadToward([sx, 0, zb], [sx, 0, zz], [sx, gh * 0.8, zz], [sx, gh, zb], scale(net, 0.8), [sx, 0, 0]);
    }
    ribbon(m, circlePath(...((): [number, number, number] => { const q = f.p(0, 0, 0); return [q[0], q[2], 5.5]; })(), 24), true, -t, MARK_Y, t, MARK_Y, LINE);
    m.disc(...(() => { const q = f.p(0, 0, 0); return [q[0], MARK_Y, q[2]] as const; })(), 0.2, 6, LINE);
    for (const [cx, cz] of [[-W / 2, -L / 2], [W / 2, -L / 2], [-W / 2, L / 2], [W / 2, L / 2]]) {
      f.cyl(cx, 0, cz, 'y', 0.03, 0.03, 1.5, 4, hex('#e9d44a'));
      f.box(cx, 1.15, cz - 0.02, 0.3, 0.25, 0.02, hex('#d9482f'), { flat: true });
    }
    if (o.floodlights !== false) for (const [fx, fz] of [[-W / 2 - 4, -L / 4], [W / 2 + 4, -L / 4], [-W / 2 - 4, L / 4], [W / 2 + 4, L / 4]]) floodlight(f, fx, fz, 12, glow, 1);
  });
}

/** A helipad on a raised slab: lit rim, white ring and H, a windsock. `y` lifts it onto a roof. */
export function helipad(b: MeshBuilder, x: number, z: number, o: { radius?: number; y?: number; rot?: number } = {}): AssetResult {
  const r = o.radius ?? 9, y0 = o.y ?? 0;
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, y0, z, o.rot ?? 0);
    f.cyl(0, 0, 0, 'y', r + 0.6, r + 0.6, 0.45, 28, hex('#8d8777'), { capEnd: true });
    f.cyl(0, 0.45, 0, 'y', r, r, 0.04, 28, hex('#3a4048'), { capEnd: true });
    const yy = 0.5;
    const ring = circlePath(0, 0, r * 0.82, 28).map((p) => { const q = f.p(p[0], 0, p[1]); return [q[0], q[2]] as P2; });
    ribbon(m, ring, true, -0.28, y0 + yy, 0.28, y0 + yy, hex('#f0eee4'));
    const bar = (x0: number, z0: number, x1: number, z1: number) => f.quadToward([x0, yy, z0], [x1, yy, z0], [x1, yy, z1], [x0, yy, z1], hex('#f0eee4'), [0, 1, 0]);
    const s = r * 0.34, tt = r * 0.07;
    bar(-s, -s * 1.3, -s + tt * 2, s * 1.3); bar(s - tt * 2, -s * 1.3, s, s * 1.3); bar(-s, -tt, s, tt);
    // Rim lights and their glow points (every other one).
    const n = 16;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const px = Math.cos(a) * (r + 0.3), pz = Math.sin(a) * (r + 0.3);
      f.box(px - 0.12, 0.45, pz - 0.12, 0.24, 0.2, 0.24, hex('#f2b24a'), { emissive: 1, flat: true });
      if (i % 2 === 0) { const q = f.p(px, 0.7, pz); glow.push({ x: q[0], y: q[1], z: q[2], radius: 2.6, color: hex('#f2b24a'), intensity: 0.5 }); }
    }
    // Windsock: pole and four alternating cone bands.
    f.cyl(r + 1.8, 0, 0, 'y', 0.1, 0.07, 4.2, 6, POLE, { capEnd: true });
    for (let i = 0; i < 4; i++) f.cyl(r + 1.8 + i * 0.5, 4.0 - i * 0.07, 0, 'x', 0.38 - i * 0.07, 0.38 - (i + 1) * 0.07, 0.5, 6, i % 2 ? hex('#f0eee4') : hex('#e0562b'));
  });
}
