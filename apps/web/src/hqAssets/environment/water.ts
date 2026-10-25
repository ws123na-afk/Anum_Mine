/**
 * Water features: a canal with stone banks and a lit water surface, bridges (arched road bridge,
 * flat bridge, footbridge), organic ponds with reeds, rocks and lily pads, and a tiered fountain
 * with jets and under-water lights.
 *
 * Water surfaces are flat at y = 0.08 with a small emissive weight so the renderer keeps them
 * bright at night. Banks are raised, so the canal sits on a flat ground plane without a cut-out.
 */
import { MeshBuilder, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { STONE, WATER, TEAL } from '../../lib/hqProps.ts';
import { Frame, scoped, type AssetResult, type GlowPoint, type Vec3t } from './common.ts';
import { circlePath, dashedRibbon, ribbon, type P2 } from './paths.ts';
import { lamp } from './furniture.ts';

export const WATER_Y = 0.08;
const COPING = hex('#cfc9ba');
const WALL = hex('#a39d8e');
const LIGHT_WATER = mix(WATER, hex('#9fe0ff'), 0.4);

export const CANAL_TRIANGLES_PER_METRE_CAP = 3;

/** A canal along a path. The path is the centre line; `width` is the water width. */
export function canal(b: MeshBuilder, pts: readonly P2[], o: { width?: number; closed?: boolean; seed?: string } = {}): AssetResult {
  const w = o.width ?? 8, hw = w / 2, closed = o.closed ?? false, top = 0.42;
  return scoped(b, [], (m) => {
    ribbon(m, pts, closed, -hw, WATER_Y, hw, WATER_Y, WATER, 'up', 0.12);
    ribbon(m, pts, closed, -hw * 0.55, WATER_Y + 0.004, hw * 0.55, WATER_Y + 0.004, mix(WATER, LIGHT_WATER, 0.5), 'up', 0.2);
    for (const side of [-0.32, 0.32]) dashedRibbon(m, pts, closed, side * hw, 0.05, WATER_Y + 0.008, 4.5, 7, LIGHT_WATER, side > 0 ? 3 : 0);
    for (const side of [-1, 1]) {
      // Wall facing the water, coping on top, and a sloped outer bank down to the ground.
      ribbon(m, pts, closed, side * hw, WATER_Y, side * hw, top, WALL, side < 0 ? 'right' : 'left');
      ribbon(m, pts, closed, Math.min(side * hw, side * (hw + 0.55)), top, Math.max(side * hw, side * (hw + 0.55)), top, COPING);
      ribbon(m, pts, closed, side * (hw + 0.55), top, side * (hw + 1.5), 0.03, scale(STONE, 0.9));
    }
  });
}

export const BRIDGE_TRIANGLES_CAP = 470;
export type BridgeStyle = 'arch' | 'flat' | 'foot';

/**
 * A bridge across a canal. Local z runs across the canal (length), local x is the deck width. The
 * deck meets the bank coping (y 0.42) at both ends; the arched style rises in the middle. Lamps
 * stand at the corners of the vehicle styles and give glow points.
 */
export function bridge(b: MeshBuilder, x: number, z: number, o: { rot?: number; length?: number; width?: number; style?: BridgeStyle; lamps?: boolean } = {}): AssetResult {
  const style = o.style ?? 'arch';
  const L = o.length ?? 12, W = o.width ?? (style === 'foot' ? 3 : 7);
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const hw = W / 2, base = 0.42;
    const rise = style === 'arch' ? 1.0 : style === 'foot' ? 0.7 : 0;
    const thick = style === 'foot' ? 0.3 : 0.55;
    const N = 8;
    const yAt = (t: number) => base + rise * Math.sin(Math.PI * t);
    const deck = style === 'foot' ? hex('#b8aa92') : hex('#43484f');
    for (let i = 0; i < N; i++) {
      const t0 = i / N, t1 = (i + 1) / N;
      const z0 = -L / 2 + t0 * L, z1 = -L / 2 + t1 * L;
      const a = yAt(t0), c = yAt(t1);
      const ref: Vec3t = [0, (a + c) / 2 - thick / 2, (z0 + z1) / 2];
      f.face([[-hw, a, z0], [hw, a, z0], [hw, c, z1], [-hw, c, z1]], ref, [deck, deck, deck, deck]);
      f.face([[-hw, a - thick, z0], [hw, a - thick, z0], [hw, c - thick, z1], [-hw, c - thick, z1]], ref, [WALL, WALL, WALL, WALL]);
      for (const sd of [-1, 1]) {
        f.face([[sd * hw, a - thick, z0], [sd * hw, a, z0], [sd * hw, c, z1], [sd * hw, c - thick, z1]], ref, [STONE, STONE, STONE, STONE]);
        // Parapet: inner and outer face and the coping.
        const ph = style === 'foot' ? 0.95 : 0.85, pw = style === 'foot' ? 0.08 : 0.28;
        const xi = sd * (hw - pw), xo = sd * hw;
        const pref: Vec3t = [sd * (hw - pw / 2), (a + c) / 2 + ph / 2, (z0 + z1) / 2];
        if (style === 'foot') {
          f.face([[xi, a + ph, z0], [xo, a + ph, z0], [xo, c + ph, z1], [xi, c + ph, z1]], pref, [COPING, COPING, COPING, COPING]);
          if (i % 2 === 0) f.box(sd * (hw - 0.06) - 0.02, c, z1 - 0.02, 0.04, ph, 0.04, hex('#2f3a45'), { skip: ['bottom'] });
        } else {
          f.face([[xi, a, z0], [xi, a + ph, z0], [xi, c + ph, z1], [xi, c, z1]], pref, [STONE, STONE, STONE, STONE]);
          f.face([[xo, a, z0], [xo, a + ph, z0], [xo, c + ph, z1], [xo, c, z1]], pref, [STONE, STONE, STONE, STONE]);
          f.face([[xi, a + ph, z0], [xo, a + ph, z0], [xo, c + ph, z1], [xi, c + ph, z1]], pref, [COPING, COPING, COPING, COPING]);
        }
      }
    }
    // Deck centre line on vehicle bridges.
    if (style !== 'foot') {
      for (let i = 0; i < N; i += 2) {
        const t0 = i / N, t1 = (i + 1) / N;
        const z0 = -L / 2 + t0 * L, z1 = -L / 2 + t1 * L, a = yAt(t0) + 0.01, c = yAt(t1) + 0.01;
        f.quadToward([-0.07, a, z0], [0.07, a, z0], [0.07, c, z1], [-0.07, c, z1], hex('#e3c35a'), [0, 1, 0]);
      }
    }
    // Abutments down to the ground at both ends.
    for (const ez of [-L / 2, L / 2 - 0.5]) f.box(-hw, 0, ez, W, base - thick + 0.05, 0.5, WALL, { skip: ['bottom'] });
    if (style === 'foot') {
      for (const sd of [-1, 1]) f.box(sd * (hw - 0.04) - 0.04, yAt(0.5) + 0.9, -L / 2, 0.08, 0.06, L, hex('#2f3a45'));
    }
    if (style !== 'foot' && o.lamps !== false) {
      for (const sx of [-hw + 0.14, hw - 0.14]) for (const sz of [-L / 2 + 0.4, L / 2 - 0.4]) {
        const q = f.p(sx, 0, sz);
        glow.push(...lamp(m, q[0], q[2], { kind: 'garden', rot: o.rot ?? 0, height: 3.2, pool: false }).glow);
      }
    }
  });
}

// ---------------------------------------------------------------- pond

export const POND_TRIANGLES_CAP = 420;

function fanUp(b: MeshBuilder, cx: number, y: number, cz: number, ring: readonly P2[], color: Rgb, emissive: number): void {
  for (let i = 0; i < ring.length; i++) {
    const p = ring[i], q = ring[(i + 1) % ring.length];
    const cross = (p[0] - cx) * (q[1] - cz) - (p[1] - cz) * (q[0] - cx);
    // (p - c) x (q - c) with x east and z south: positive means q is clockwise from p on screen.
    if (cross > 0) b.tri([cx, y, cz], [q[0], y, q[1]], [p[0], y, p[1]], color, emissive);
    else b.tri([cx, y, cz], [p[0], y, p[1]], [q[0], y, q[1]], color, emissive);
  }
}

/** An organic pond: stone rim, bright water, lily pads, reeds and a few boulders. */
export function pond(b: MeshBuilder, cx: number, cz: number, r: number, o: { seed?: string; reeds?: number; lilies?: number; rocks?: number } = {}): AssetResult {
  const rnd = seeded(o.seed ?? `pond-${cx.toFixed(1)}-${cz.toFixed(1)}`);
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const n = 16;
    const ring: P2[] = [];
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2, rr = r * (0.86 + rnd() * 0.28);
      ring.push([cx + Math.cos(a) * rr * 1.15, cz + Math.sin(a) * rr * 0.9]);
    }
    fanUp(m, cx, WATER_Y, cz, ring, WATER, 0.12);
    fanUp(m, cx, WATER_Y + 0.004, cz, ring.map((p) => [cx + (p[0] - cx) * 0.55, cz + (p[1] - cz) * 0.55] as P2), LIGHT_WATER, 0.2);
    ribbon(m, ring, true, -0.75, 0.16, 0, 0.16, COPING);
    ribbon(m, ring, true, 0, WATER_Y, 0, 0.16, WALL, 'right');
    ribbon(m, ring, true, -1.3, 0.03, -0.75, 0.16, scale(STONE, 0.9));
    for (let i = 0; i < (o.lilies ?? 5); i++) {
      const a = rnd() * Math.PI * 2, rr = rnd() * r * 0.6;
      const px = cx + Math.cos(a) * rr, pz = cz + Math.sin(a) * rr * 0.8;
      m.disc(px, WATER_Y + 0.012, pz, 0.38 + rnd() * 0.18, 7, hex('#3f8a4a'));
      if (rnd() < 0.5) m.prism(px, WATER_Y + 0.012, pz, 0.12, 0, 0.14, 5, hex('#f3c6d8'));
    }
    for (let i = 0; i < (o.reeds ?? 9); i++) {
      const p = ring[Math.floor(rnd() * n)];
      const px = p[0] + (cx - p[0]) * 0.06, pz = p[1] + (cz - p[1]) * 0.06;
      for (let k = 0; k < 3; k++) m.prism(px + (rnd() - 0.5) * 0.4, 0.1, pz + (rnd() - 0.5) * 0.4, 0.05, 0, 1.0 + rnd() * 0.9, 3, mix(hex('#5d8a3a'), hex('#8aa84c'), rnd()));
    }
    for (let i = 0; i < (o.rocks ?? 4); i++) {
      const p = ring[Math.floor(rnd() * n)];
      m.blob(p[0] + (cx - p[0]) * 0.02, 0.22, p[1] + (cz - p[1]) * 0.02, 0.55 + rnd() * 0.4, 0.34, 0.5 + rnd() * 0.3, mix(hex('#8a8578'), hex('#aaa595'), rnd()), 6);
    }
    glow.push({ x: cx, y: 0.3, z: cz, radius: r * 1.4, color: TEAL, intensity: 0.2 });
  });
}

// ---------------------------------------------------------------- fountain

export const FOUNTAIN_TRIANGLES_CAP = 700;

/** A round fountain: raised basin, lit water, a two tier pedestal, a centre jet and arching side jets. */
export function fountain(b: MeshBuilder, cx: number, cz: number, o: { radius?: number; seed?: string } = {}): AssetResult {
  const r = o.radius ?? 4;
  const rnd = seeded(o.seed ?? `fountain-${cx.toFixed(1)}-${cz.toFixed(1)}`);
  const glow: GlowPoint[] = [];
  const jet = hex('#d5f1ff');
  return scoped(b, glow, (m) => {
    const f = new Frame(m, cx, 0, cz, 0);
    m.disc(cx, 0.016, cz, r + 1.6, 24, scale(STONE, 0.95));
    f.cyl(0, 0, 0, 'y', r + 0.5, r + 0.5, 0.7, 24, COPING);
    const rim = circlePath(cx, cz, r, 24);
    ribbon(m, rim, true, -0.5, 0.7, 0, 0.7, COPING);
    ribbon(m, rim, true, 0, 0.62, 0, 0.7, WALL, 'right');
    m.disc(cx, 0.62, cz, r, 24, WATER, 0.14);
    m.disc(cx, 0.625, cz, r * 0.6, 20, LIGHT_WATER, 0.25);
    for (const rr of [r * 0.82, r * 0.42]) ribbon(m, circlePath(cx, cz, rr, 20), true, -0.04, 0.63, 0.04, 0.63, hex('#e4f6ff'), 'up', 0.35);
    // Pedestal and bowls.
    f.cyl(0, 0.6, 0, 'y', 0.5, 0.35, 1.4, 12, COPING);
    f.cyl(0, 2.0, 0, 'y', 1.2, 0.6, 0.35, 14, COPING, { capEnd: true });
    f.cyl(0, 2.35, 0, 'y', 0.25, 0.2, 0.8, 8, COPING);
    f.cyl(0, 3.1, 0, 'y', 0.7, 0.3, 0.25, 12, COPING, { capEnd: true });
    // Jets: a tall cone and eight arching pairs of segments.
    f.cyl(0, 3.3, 0, 'y', 0.14, 0, 2.6, 5, jet, { emissive: 0.6 });
    const n = 8;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + rnd() * 0.1;
      const r0 = r * 0.35, r1 = r * 0.62, r2 = r * 0.82;
      const pt = (rr: number, y: number): [number, number, number] => [cx + Math.cos(a) * rr, y, cz + Math.sin(a) * rr];
      const seg = (p: [number, number, number], q: [number, number, number], w0: number, w1: number) => {
        const dx = q[0] - p[0], dz = q[2] - p[2], l = Math.hypot(dx, dz) || 1;
        const sx = (-dz / l) * w0, sz = (dx / l) * w0, tx = (-dz / l) * w1, tz = (dx / l) * w1;
        m.quad([p[0] - sx, p[1], p[2] - sz], [p[0] + sx, p[1], p[2] + sz], [q[0] + tx, q[1], q[2] + tz], [q[0] - tx, q[1], q[2] - tz], jet, 0.55);
        m.quad([p[0] - sx, p[1], p[2] - sz], [q[0] - tx, q[1], q[2] - tz], [q[0] + tx, q[1], q[2] + tz], [p[0] + sx, p[1], p[2] + sz], scale(jet, 0.85), 0.55);
      };
      seg(pt(r0, 0.7), pt(r1, 2.1), 0.1, 0.08);
      seg(pt(r1, 2.1), pt(r2, 0.68), 0.08, 0.05);
    }
    for (let i = 0; i < 4; i++) {
      const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
      glow.push({ x: cx + Math.cos(a) * r * 0.7, y: 0.7, z: cz + Math.sin(a) * r * 0.7, radius: 3.2, color: TEAL, intensity: 0.55 });
    }
  });
}
