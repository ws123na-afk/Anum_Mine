/**
 * Shared pieces for the exterior builders: colour schemes, a facade frame (so a window row, a fin
 * or an awning is written once for all four sides), window panes with the night rule, entrance and
 * sign parts, and the final placement transform.
 */
import { MeshBuilder, VERTEX_FLOATS, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { TEAL, WARM, WINDOW_BASE, anumMark } from '../../lib/hqProps.ts';
import type { Aabb } from '../../lib/hqMath.ts';
import type { ExteriorAsset, ExteriorKind, ExteriorParams, ExteriorSchemeName, Point3, SignAnchor } from './types.ts';

// ---------------------------------------------------------------- schemes

export type Scheme = Readonly<{ name: ExteriorSchemeName; wall: Rgb; trim: Rgb; roof: Rgb; core: Rgb; accent: Rgb; stone: Rgb }>;

const SCHEMES: Record<ExteriorSchemeName, Scheme> = {
  white: { name: 'white', wall: hex('#d9dde0'), trim: hex('#b9c0c6'), roof: hex('#8f9ba5'), core: hex('#27435f'), accent: hex('#45d0b5'), stone: hex('#b9b4a6') },
  sandstone: { name: 'sandstone', wall: hex('#d8c9a8'), trim: hex('#bfae88'), roof: hex('#9a8f7a'), core: hex('#2a4560'), accent: hex('#d9b25f'), stone: hex('#c4b79a') },
  slate: { name: 'slate', wall: hex('#8a93a0'), trim: hex('#6f7884'), roof: hex('#59626d'), core: hex('#1f3a55'), accent: hex('#6aa7ff'), stone: hex('#9c9a92') },
  graphite: { name: 'graphite', wall: hex('#4b5560'), trim: hex('#39424c'), roof: hex('#2f3740'), core: hex('#1c2f45'), accent: hex('#45d0b5'), stone: hex('#8d8a82') },
};
const AUTO: readonly ExteriorSchemeName[] = ['white', 'sandstone', 'slate'];

export function resolveScheme(p: ExteriorParams, rnd: () => number): Scheme {
  const pick = p.scheme && p.scheme !== 'auto' ? p.scheme : AUTO[Math.floor(rnd() * AUTO.length)];
  const base = SCHEMES[pick];
  return p.accent ? { ...base, accent: p.accent } : base;
}

export const clamp = (v: number | undefined, lo: number, hi: number, fallback: number): number =>
  Math.min(hi, Math.max(lo, Number.isFinite(v as number) ? (v as number) : fallback));

// ---------------------------------------------------------------- context

export type Ctx = {
  b: MeshBuilder;
  rnd: () => number;
  scheme: Scheme;
  /** Share of windows lit at night. */
  lit: number;
  lean: boolean;
  windows: { total: number; lit: number };
  seed: string;
};

export function makeCtx(kind: ExteriorKind, p: ExteriorParams, defaultLit: number): Ctx {
  const seed = p.seed ?? kind;
  const rnd = seeded(`ext:${kind}:${seed}`);
  const scheme = resolveScheme(p, rnd);
  return { b: new MeshBuilder(), rnd, scheme, lit: clamp(p.litWindows, 0, 1, defaultLit), lean: p.detail === 'lean', windows: { total: 0, lit: 0 }, seed };
}

// ---------------------------------------------------------------- faces

/** A facade: start point, unit tangent (u runs along it) and outward normal. */
export type Face = Readonly<{ ox: number; oz: number; tx: number; tz: number; nx: number; nz: number; len: number }>;

/** The four faces of a rectangle: south (front, +z), east (+x), north (-z), west (-x). */
export function rectFaces(x: number, z: number, w: number, d: number): { south: Face; east: Face; north: Face; west: Face } {
  const f = (ox: number, oz: number, tx: number, tz: number, len: number): Face => ({ ox, oz, tx, tz, nx: tz === 0 ? 0 : -tz, nz: tx, len });
  return { south: f(x, z + d, 1, 0, w), east: f(x + w, z + d, 0, -1, d), north: f(x + w, z, -1, 0, w), west: f(x, z, 0, 1, d) };
}

export const pt = (f: Face, u: number, y: number, off = 0): [number, number, number] => [f.ox + f.tx * u + f.nx * off, y, f.oz + f.tz * u + f.nz * off];

/** A flat quad on a facade, facing outward. */
export function faceQuad(b: MeshBuilder, f: Face, u0: number, u1: number, y0: number, y1: number, color: Rgb, emissive = 0, off = 0.04): void {
  b.quad(pt(f, u0, y0, off), pt(f, u1, y0, off), pt(f, u1, y1, off), pt(f, u0, y1, off), color, emissive);
}

/** A box standing on a facade: `o0..o1` is the distance from the wall plane (negative = into the wall). */
export function faceBox(
  b: MeshBuilder, f: Face, u0: number, u1: number, y0: number, y1: number, o0: number, o1: number, color: Rgb,
  options: { emissive?: number; shade?: boolean; skip?: readonly ('top' | 'bottom' | 'north' | 'south' | 'west' | 'east')[]; grad?: number } = {},
): void {
  const a = pt(f, u0, y0, o0), c = pt(f, u1, y1, o1);
  b.box(Math.min(a[0], c[0]), y0, Math.min(a[2], c[2]), Math.abs(c[0] - a[0]), y1 - y0, Math.abs(c[2] - a[2]), color, options);
}

/** A flat disc (a wheel, a clock) standing on a facade. */
export function faceDisc(b: MeshBuilder, f: Face, u: number, y: number, r: number, sides: number, color: Rgb, emissive = 0, off = 0.06): void {
  const ring = (i: number): [number, number, number] => {
    const a = (i / sides) * Math.PI * 2;
    return pt(f, u + Math.cos(a) * r, y + Math.sin(a) * r, off);
  };
  for (let i = 0; i < sides; i++) b.tri(pt(f, u, y, off), ring(i), ring(i + 1), color, emissive);
}

// ---------------------------------------------------------------- facade relief

/**
 * Vertical fins: blades of stone standing proud of a facade at equal spacing. They give a curtain wall depth and
 * throw real shadows across the glass (the shadow map sees them), which is most of what makes a facade read as built.
 */
export function fins(ctx: Ctx, f: Face, uFrom: number, uTo: number, y0: number, y1: number, spacing: number, depth = 0.5, width = 0.24, color?: Rgb): void {
  const count = Math.max(1, Math.floor((uTo - uFrom) / spacing));
  const step = (uTo - uFrom) / count;
  for (let i = 0; i <= count; i++) {
    const u = uFrom + i * step;
    faceBox(ctx.b, f, u - width / 2, u + width / 2, y0, y1, 0, depth, color ?? ctx.scheme.wall, { grad: 0.18 });
  }
}

/** A projecting ledge (a sunshade, or a slab edge) along a facade at height y. */
export function ledge(ctx: Ctx, f: Face, uFrom: number, uTo: number, y: number, depth = 0.55, h = 0.12, color?: Rgb): void {
  faceBox(ctx.b, f, uFrom, uTo, y, y + h, 0, depth, color ?? ctx.scheme.trim, { skip: ['bottom'] });
}

// ---------------------------------------------------------------- windows

const COOL = hex('#cfe6ff');
const SPANDREL = hex('#2a333d');

/**
 * One window pane. Night rule: a pane is lit with probability `ctx.lit`; a lit pane carries
 * WINDOW_BASE plus a glow of 0.45 to 0.95 (the shader shows it warm at night), a dark pane carries
 * exactly WINDOW_BASE (reflective glass by day, dark glass at night). `lift` (0..1) makes upper
 * floors a little brighter, like the campus buildings.
 */
export function pane(ctx: Ctx, f: Face, u0: number, u1: number, y0: number, y1: number, lift = 1, off = 0.04): void {
  const { b, rnd } = ctx;
  const lit = rnd() < ctx.lit;
  const roll = rnd();
  ctx.windows.total += 1;
  if (!lit) {
    const c = hex('#9fb4c8');
    b.quadV(pt(f, u0, y0, off), pt(f, u1, y0, off), pt(f, u1, y1, off), pt(f, u0, y1, off), scale(c, 0.8), scale(c, 0.8), c, c, WINDOW_BASE);
    return;
  }
  ctx.windows.lit += 1;
  const tone = roll > 0.86 ? mix(COOL, hex('#8fd0ff'), rnd()) : mix(WARM, hex('#ffe9b8'), rnd() * 0.7);
  const glow = WINDOW_BASE + (roll < 0.3 ? 0.45 : 0.8 + 0.15 * lift);
  const bot = scale(tone, 0.72 * lift + 0.2);
  b.quadV(pt(f, u0, y0, off), pt(f, u1, y0, off), pt(f, u1, y1, off), pt(f, u0, y1, off), bot, bot, tone, tone, glow);
}

/** A row of equal panes along a facade, from `uFrom` to `uTo`, between heights y0 and y1. */
export function paneRow(ctx: Ctx, f: Face, uFrom: number, uTo: number, y0: number, y1: number, cols: number, gapRatio = 0.14, lift = 1): void {
  const step = (uTo - uFrom) / cols;
  for (let i = 0; i < cols; i++) pane(ctx, f, uFrom + i * step + step * gapRatio * 0.5, uFrom + (i + 1) * step - step * gapRatio * 0.5, y0, y1, lift);
}

/**
 * Curtain wall: a dark core box and, per face and storey, a row of panes. The gaps between panes
 * are the mullions and spandrels (the dark core shows through), so a whole tower costs two
 * triangles per pane. Returns the roof height.
 */
export function curtainWall(
  ctx: Ctx, x: number, z: number, w: number, d: number, y: number, levels: number, levelH: number,
  options: { colW?: number; inset?: number; skipFaces?: readonly ('south' | 'east' | 'north' | 'west')[] } = {},
): number {
  const { b, scheme } = ctx;
  const h = levels * levelH;
  b.box(x, y, z, w, h, d, scheme.core, { skip: ['top', 'bottom'], grad: 0.5 });
  const faces = rectFaces(x, z, w, d);
  const colW = options.colW ?? 1.9;
  const inset = options.inset ?? 0.6;
  const skip = new Set(options.skipFaces ?? []);
  for (const [name, f] of Object.entries(faces) as Array<['south' | 'east' | 'north' | 'west', Face]>) {
    if (skip.has(name)) continue;
    const cols = Math.max(2, Math.floor((f.len - inset * 2) / colW));
    for (let l = 0; l < levels; l++) {
      const ly = y + l * levelH;
      // Near floor-to-ceiling glass with slim mullions, and a pale slab edge between storeys: the grid reads as glass, not as dark boxes.
      paneRow(ctx, f, inset, f.len - inset, ly + levelH * 0.12, ly + levelH * 0.9, cols, 0.06, 0.55 + 0.45 * ((l + 1) / levels));
      if (l > 0 && !ctx.lean) faceQuad(ctx.b, f, inset - 0.2, f.len - inset + 0.2, ly - 0.14, ly + 0.1, scheme.wall, 0, 0.07);
      // The opaque spandrel under every window row: the dark band between the slab edge and the glass.
      if (!ctx.lean) faceQuad(ctx.b, f, inset, f.len - inset, ly + 0.1, ly + levelH * 0.12, SPANDREL, 0, 0.065);
    }
  }
  return y + h;
}

// ---------------------------------------------------------------- entrance and sign

/** Lit glass entrance on a facade: door glass, frame piers, a canopy slab and two columns. Returns the canopy top. */
export function entrance(ctx: Ctx, f: Face, uc: number, y: number, width: number, height: number, depth: number, canopyColor: Rgb, withCanopy = true): number {
  const { b, scheme } = ctx;
  faceQuad(b, f, uc - width / 2, uc + width / 2, y, y + height, WARM, WINDOW_BASE + 0.95, 0.06);
  for (const u of [uc - width / 2, uc, uc + width / 2]) faceBox(b, f, u - 0.06, u + 0.06, y, y + height, 0, 0.16, scheme.trim);
  if (!withCanopy) return y + height;
  faceBox(b, f, uc - width / 2 - 1.2, uc + width / 2 + 1.2, y + height + 0.1, y + height + 0.42, 0, depth, canopyColor, { emissive: 0.3 });
  faceBox(b, f, uc - width / 2 - 1.2, uc + width / 2 + 1.2, y + height + 0.42, y + height + 0.54, 0, depth, scale(scheme.wall, 0.9));
  for (const u of [uc - width / 2 - 0.9, uc + width / 2 + 0.9]) faceBox(b, f, u - 0.11, u + 0.11, y, y + height + 0.1, depth - 0.4, depth - 0.18, scheme.wall);
  return y + height + 0.54;
}

/**
 * Sign board on the +z facade: a dark panel, an accent rule and the ANUM mark at the left. The
 * name itself is an HTML overlay pinned to the returned anchor (it needs real text).
 */
export function signBoard(ctx: Ctx, f: Face, uc: number, y: number, width: number, height: number, off: number): SignAnchor {
  const { b, scheme } = ctx;
  faceBox(b, f, uc - width / 2, uc + width / 2, y, y + height, off, off + 0.22, hex('#0e1b26'));
  faceBox(b, f, uc - width / 2, uc + width / 2, y, y + 0.07, off + 0.22, off + 0.26, scheme.accent, { emissive: 0.9, shade: false });
  faceBox(b, f, uc - width / 2, uc + width / 2, y + height - 0.07, y + height, off + 0.22, off + 0.26, scheme.accent, { emissive: 0.9, shade: false });
  const markSize = height * 0.62;
  const p = pt(f, uc - width / 2 + height * 0.5, y + height / 2, off + 0.27);
  anumMark(b, p[0], p[1], p[2], markSize, TEAL);
  const c = pt(f, uc, y + height / 2, off + 0.3);
  return { x: c[0], y: c[1], z: c[2], nx: f.nx, nz: f.nz, width, height, markWidth: height };
}

// ---------------------------------------------------------------- placement

const rotateVertices = (data: Float32Array, rot: number, ox: number, oz: number): void => {
  const r = ((rot % 4) + 4) % 4;
  for (let i = 0; i < data.length; i += VERTEX_FLOATS) {
    let x = data[i], z = data[i + 2], nx = data[i + 3], nz = data[i + 5];
    for (let k = 0; k < r; k++) { [x, z] = [-z + 0, x]; [nx, nz] = [-nz + 0, nx + 0]; }
    data[i] = x + ox; data[i + 2] = z + oz; data[i + 3] = nx; data[i + 5] = nz;
  }
};

/** Turn the halo list of a builder by quarter turns and move it to the building's origin. */
function rotateHalos(list: readonly number[], rot: number, ox: number, oz: number): Float32Array {
  const out = new Float32Array(list);
  const r = ((rot % 4) + 4) % 4;
  for (let i = 0; i < out.length; i += 8) {
    let x = out[i], z = out[i + 2];
    for (let k = 0; k < r; k++) [x, z] = [-z, x];
    out[i] = x + ox; out[i + 2] = z + oz;
  }
  return out;
}

const rotPoint = (p: Point3, rot: number, ox: number, oz: number): Point3 => {
  let x = p.x, z = p.z;
  for (let k = 0; k < rot; k++) [x, z] = [-z, x];
  return { x: x + ox, y: p.y, z: z + oz };
};

function rotBox(box: Aabb, rot: number, ox: number, oz: number): Aabb {
  const a = rotPoint(box.min, rot, ox, oz), c = rotPoint(box.max, rot, ox, oz);
  return { min: { x: Math.min(a.x, c.x), y: box.min.y, z: Math.min(a.z, c.z) }, max: { x: Math.max(a.x, c.x), y: box.max.y, z: Math.max(a.z, c.z) } };
}

export type Draft = Readonly<{
  kind: ExteriorKind; footprint: { width: number; depth: number }; height: number; anchor: Point3; door: Point3; sign: SignAnchor;
  /** Ground rectangle of everything drawn: x0, z0, x1, z1 (local frame). */
  ground: readonly [number, number, number, number];
}>;

/** Freeze the builder, apply rotation and origin, and measure. */
export function finish(ctx: Ctx, d: Draft, p: ExteriorParams): ExteriorAsset {
  const rot = p.rotation ?? 0;
  const ox = p.origin?.x ?? 0, oz = p.origin?.z ?? 0;
  const vertices = ctx.b.toFloat32();
  rotateVertices(vertices, rot, ox, oz);
  const lb = ctx.b.bounds();
  const bounds = rotBox(lb, rot, ox, oz);
  const triangles = ctx.b.triangleCount;
  const sp = rotPoint({ x: d.sign.x, y: d.sign.y, z: d.sign.z }, rot, ox, oz);
  let nx = d.sign.nx, nz = d.sign.nz;
  for (let k = 0; k < rot; k++) [nx, nz] = [-nz + 0, nx + 0];
  const [gx0, gz0, gx1, gz1] = d.ground;
  return {
    kind: d.kind,
    mesh: { vertices, bounds, triangles },
    triangles,
    footprint: d.footprint,
    height: d.height,
    anchor: rotPoint(d.anchor, rot, ox, oz),
    door: rotPoint(d.door, rot, ox, oz),
    sign: { ...d.sign, x: sp.x, y: sp.y, z: sp.z, nx, nz },
    windows: { total: ctx.windows.total, lit: ctx.windows.lit },
    box: rotBox({ min: { x: Math.min(gx0, lb.min.x), y: 0, z: Math.min(gz0, lb.min.z) }, max: { x: Math.max(gx1, lb.max.x), y: lb.max.y, z: Math.max(gz1, lb.max.z) } }, rot, ox, oz),
    scheme: ctx.scheme.name,
    seed: ctx.seed,
    halos: rotateHalos(ctx.b.halos, rot, ox, oz),
  };
}
