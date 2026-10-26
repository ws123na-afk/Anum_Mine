/**
 * The facade kit: the pieces that make a block read as built rather than boxed. Spandrels and mullion lines
 * on glass, balcony runs with glass balustrades and planters, horizontal louvres, a barrel-vault atrium roof,
 * roof decks with a pool, loungers, a pergola and planting, cantilevered canopies with lit soffits, a
 * blinking-beacon mount and a flag. Everything is decoration drawn in the shared primitive format; nothing
 * encodes a name, a count, a person or an activity (a lounger is furniture, not somebody relaxing).
 *
 * Surface classes carry the motion that scenery is allowed: leaves are `MAT.foliage` (they sway), a flag is
 * `MAT.cloth` (it ripples, `uv.x` is the distance from the pole) and a rooftop aviation light is `MAT.beacon`
 * (it blinks). The renderer freezes all three under reduced motion.
 */
import { MAT, hex, mix, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { WARM, WINDOW_BASE } from '../../lib/hqProps.ts';
import { faceBox, faceQuad, pt, type Ctx, type Face } from './parts.ts';

export const GRAPHITE = hex('#2a333d');
export const GLASS = hex('#8fb2c8');
const LEAF_A = hex('#2f6b43'), LEAF_B = hex('#5a9a54');
const WATER = hex('#2f86b4');

/** A leafy blob that sways (scenery foliage). */
export function leaf(ctx: Ctx, x: number, y: number, z: number, rx: number, ry: number, rz: number, color?: Rgb): void {
  const c = color ?? mix(LEAF_A, LEAF_B, ctx.rnd());
  ctx.b.withMat(MAT.foliage, () => ctx.b.smoothBlob(x, y, z, rx, ry, rz, c, { lat: 3, lon: 6, lump: 0.1, tone: 0.18, seed: Math.floor(ctx.rnd() * 997) }));
}

/** A red aviation light on a short mast that blinks while ambient motion runs. */
export function beaconLight(ctx: Ctx, x: number, y: number, z: number): void {
  const { b } = ctx;
  b.prism(x, y, z, 0.07, 0.04, 0.9, 4, hex('#9aa5b1'), { capTop: false });
  b.withMat(MAT.beacon, () => b.box(x - 0.2, y + 0.9, z - 0.2, 0.4, 0.4, 0.4, hex('#ff4040'), { emissive: 1, shade: false }));
  b.halo(x, y + 1.1, z, 3.2, hex('#ff5050'), 0.35);
}

/** A flag pole with a plain pennant in `color` (no artwork); the pennant ripples. */
export function pennant(ctx: Ctx, x: number, z: number, h: number, color?: Rgb): void {
  const { b, scheme } = ctx;
  b.prism(x, 0, z, 0.13, 0.07, h, 5, hex('#c9d0d6'), { capTop: false });
  const c = color ?? scheme.accent;
  const y0 = h - 1.5, y1 = h - 0.2, L = 1.9;
  const n = 3;
  b.withMat(MAT.cloth, () => {
    for (let i = 0; i < n; i++) {
      const u0 = i / n, u1 = (i + 1) / n;
      const shade = scale(c, 0.92 + 0.1 * (i % 2));
      b.quadUV([x + u0 * L, y0, z], [x + u1 * L, y0, z], [x + u1 * L, y1, z], [x + u0 * L, y1, z], [u0, 0], [u1, 0], [u1, 1], [u0, 1], shade, 0.12);
    }
  });
  b.box(x - 0.08, h, z - 0.08, 0.16, 0.16, 0.16, WARM, { emissive: 0.8, shade: false });
}

/** A dark spandrel panel (the opaque band under a window row). */
export function spandrel(ctx: Ctx, f: Face, uFrom: number, uTo: number, y: number, h = 0.5, off = 0.065): void {
  faceQuad(ctx.b, f, uFrom, uTo, y, y + h, GRAPHITE, 0, off);
}

export type BalconyOptions = { depth?: number; planters?: boolean; railHeight?: number };

/** A run of balconies: a projecting slab, a glass balustrade with a slim top rail, and a planter strip on the outer edge. */
export function balconyRun(ctx: Ctx, f: Face, uFrom: number, uTo: number, y: number, o: BalconyOptions = {}): void {
  const { b, scheme } = ctx;
  const depth = o.depth ?? 1.6, rail = o.railHeight ?? 1.05;
  faceBox(b, f, uFrom, uTo, y - 0.28, y, 0, depth, scale(scheme.wall, 1.02), { grad: 0.1 });
  faceQuad(b, f, uFrom, uTo, y + 0.02, y + rail, GLASS, WINDOW_BASE, depth - 0.04);
  faceBox(b, f, uFrom, uTo, y + rail, y + rail + 0.06, depth - 0.08, depth, scheme.trim);
  // End returns of the balustrade so the run reads as a box in plan.
  for (const u of [uFrom, uTo]) faceBox(b, f, u - 0.03, u + 0.03, y, y + rail, 0, depth, scheme.trim, { skip: ['bottom'] });
  if (o.planters !== false) {
    faceBox(b, f, uFrom + 0.2, uTo - 0.2, y, y + 0.34, depth - 0.52, depth - 0.2, hex('#8d8777'));
    const n = Math.max(1, Math.floor((uTo - uFrom) / 2.4));
    for (let i = 0; i < n; i++) {
      const u = uFrom + ((i + 0.5) * (uTo - uFrom)) / n;
      const p = pt(f, u, y + 0.5, depth - 0.36);
      leaf(ctx, p[0], p[1], p[2], 0.42, 0.3, 0.32);
    }
  }
}

/** Horizontal louvre blades on a face (a brise-soleil): `rows` thin blades between y0 and y1. */
export function louvres(ctx: Ctx, f: Face, uFrom: number, uTo: number, y0: number, y1: number, rows: number, depth = 0.8, color?: Rgb): void {
  const step = (y1 - y0) / rows;
  for (let i = 0; i < rows; i++) faceBox(ctx.b, f, uFrom, uTo, y0 + i * step, y0 + i * step + 0.07, 0.04, depth, color ?? ctx.scheme.trim, { skip: ['bottom'] });
}

/**
 * A barrel-vault glass roof over a rectangle (axis along z): arched glass panels with slim steel ribs. At night the
 * glass glows warm from the lit space below. `rise` is the height of the crown above the springing line.
 */
export function atriumVault(ctx: Ctx, cx: number, z: number, w: number, d: number, y: number, rise: number, segments = 6, ribs = 4): void {
  const { b, scheme } = ctx;
  const rx = w / 2;
  const pts: Array<[number, number]> = [];
  for (let i = 0; i <= segments; i++) {
    const a = Math.PI - (i / segments) * Math.PI;
    pts.push([cx + Math.cos(a) * rx, y + Math.sin(a) * rise]);
  }
  // Side walls (glass) under the springing line, and the end walls as glass fans.
  b.box(cx - rx - 0.15, y - 1.1, z - 0.15, 0.3, 1.1, d + 0.3, scheme.trim);
  b.box(cx + rx - 0.15, y - 1.1, z - 0.15, 0.3, 1.1, d + 0.3, scheme.trim);
  for (let i = 0; i < segments; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const tone = mix(WARM, hex('#ffe9b8'), 0.4 + 0.1 * (i % 2));
    b.quadV([x0, y0, z], [x0, y0, z + d], [x1, y1, z + d], [x1, y1, z], scale(tone, 0.7), scale(tone, 0.7), tone, tone, WINDOW_BASE + 0.75);
    for (let r = 0; r <= ribs; r++) {
      const rz = z + (r * d) / ribs;
      b.quad([x0, y0 + 0.04, rz - 0.07], [x0, y0 + 0.04, rz + 0.07], [x1, y1 + 0.04, rz + 0.07], [x1, y1 + 0.04, rz - 0.07], scheme.trim);
    }
  }
  // Ridge and eaves rails along the length.
  for (const [px, py] of pts) b.box(px - 0.05, py - 0.02, z - 0.05, 0.1, 0.1, d + 0.1, scheme.trim);
  // End gables: the arch outline as thin steel.
  for (const zz of [z, z + d]) for (let i = 0; i < segments; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    b.quad([x0, y0 - 0.1, zz - 0.04], [x1, y1 - 0.1, zz - 0.04], [x1, y1 + 0.05, zz - 0.04], [x0, y0 + 0.05, zz - 0.04], scheme.trim);
  }
}

export type DeckOptions = { pool?: boolean; pergola?: boolean; loungers?: number; shrubs?: number; keepClear?: Readonly<{ x: number; z: number; w: number; d: number }> };

/** A roof deck: timber decking, a glass-free parapet line, an optional plunge pool with coping, loungers, a pergola and planting. */
export function roofDeck(ctx: Ctx, x: number, z: number, w: number, d: number, y: number, o: DeckOptions = {}): void {
  const { b, scheme } = ctx;
  b.withMat(MAT.wood, () => b.plane(x, y + 0.02, z, w, d, hex('#a98962')));
  let px = x + w * 0.1, pz = z + d * 0.18, pw = 0, pd = 0;
  if (o.pool && w > 5 && d > 5) {
    pw = Math.min(w * 0.46, 7), pd = Math.min(d * 0.34, 2.8);
    b.box(px - 0.25, y + 0.02, pz - 0.25, pw + 0.5, 0.22, pd + 0.5, hex('#e1dccf'));
    b.withMat(MAT.water, () => b.plane(px, y + 0.25, pz, pw, pd, WATER, 0.1));
  }
  const lounger = (lx: number, lz: number) => {
    b.box(lx, y + 0.02, lz, 0.7, 0.34, 1.9, hex('#f2efe7'), { grad: 0.1 });
    b.box(lx, y + 0.34, lz, 0.7, 0.34, 0.6, hex('#e6e2d8'), { grad: 0.1 });
  };
  const n = o.loungers ?? 3;
  for (let i = 0; i < n; i++) lounger(x + w * 0.1 + i * 1.2, z + d * 0.62);
  if (o.pergola && w > 6 && d > 6) {
    const gx = x + w * 0.62, gz = z + d * 0.5, gw = Math.min(w * 0.3, 5), gd = Math.min(d * 0.3, 3.4);
    for (const [ax, az] of [[0, 0], [gw - 0.14, 0], [0, gd - 0.14], [gw - 0.14, gd - 0.14]] as const) b.box(gx + ax, y + 0.02, gz + az, 0.14, 2.5, 0.14, scheme.trim);
    for (let i = 0; i < 6; i++) b.box(gx - 0.2, y + 2.5, gz + (i * (gd - 0.1)) / 5, gw + 0.4, 0.08, 0.1, hex('#8d7556'));
    b.box(gx + 0.3, y + 0.02, gz + gd * 0.35, gw - 0.6, 0.45, 1.0, hex('#d9d3c4'), { grad: 0.1 });
  }
  const shrubs = o.shrubs ?? Math.max(3, Math.floor((w + d) / 5));
  for (let i = 0; i < shrubs; i++) {
    const sx = x + 0.6 + ctx.rnd() * (w - 1.2), sz = z + 0.6 + ctx.rnd() * (d - 1.2);
    if (sx > px - 0.8 && sx < px + pw + 0.8 && sz > pz - 0.8 && sz < pz + pd + 0.8) continue;
    leaf(ctx, sx, y + 0.5, sz, 0.55 + ctx.rnd() * 0.3, 0.42, 0.55 + ctx.rnd() * 0.3);
  }
}

/**
 * A cantilevered entrance canopy on a face: a slim slab with a lit soffit, a slab edge in the accent colour, and
 * two slender raked columns. Returns the height of the canopy top.
 */
export function canopy(ctx: Ctx, f: Face, uc: number, width: number, y: number, depth: number, o: { columns?: boolean } = {}): number {
  const { b, scheme } = ctx;
  faceBox(b, f, uc - width / 2, uc + width / 2, y, y + 0.3, 0, depth, scale(scheme.wall, 1.04), { grad: 0.1 });
  faceBox(b, f, uc - width / 2, uc + width / 2, y - 0.02, y, 0.2, depth - 0.1, WARM, { emissive: 0.7, shade: false });
  faceBox(b, f, uc - width / 2, uc + width / 2, y + 0.3, y + 0.38, depth - 0.1, depth, scheme.accent, { emissive: 0.8, shade: false });
  if (o.columns !== false) for (const s of [-1, 1]) {
    const u = uc + (s * (width / 2 - 0.5));
    faceBox(b, f, u - 0.11, u + 0.11, 0, y, depth - 0.55, depth - 0.3, scale(scheme.wall, 1.08));
  }
  return y + 0.38;
}

/** A free-standing signage frame: two posts and a dark blank panel with an accent LED edge. It carries no text or brand. */
export function signFrame(ctx: Ctx, x: number, z: number, w: number, h: number, y = 0): void {
  const { b, scheme } = ctx;
  for (const sx of [x - w / 2, x + w / 2 - 0.22]) b.box(sx, y, z, 0.22, h + 0.6, 0.22, scheme.trim, { grad: 0.2 });
  b.box(x - w / 2, y + h * 0.35, z - 0.02, w, h * 0.65, 0.18, hex('#0e1b26'));
  b.box(x - w / 2, y + h * 0.35, z + 0.16, w, 0.08, 0.04, scheme.accent, { emissive: 0.9, shade: false });
  b.box(x - w / 2, y + h - 0.08 + h * 0.0, z + 0.16, w, 0.08, 0.04, scheme.accent, { emissive: 0.9, shade: false });
}
