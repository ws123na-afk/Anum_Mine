/**
 * Parked and moving vehicles: cars (sedan, hatch, SUV, coupe, pickup), vans, trucks and a bus.
 * Each has a lofted body, a glazed cabin with pillars, wheels with hubs, wheel arches, head and
 * tail lights (emissive, so they glow at night), grille, mirrors and plates. Nothing carries
 * lettering, a brand or a plate number.
 *
 * Local frame: the nose points to +z for `rot` 0; `rot` is a heading in radians from +z towards
 * +x. A vehicle sits on y = 0. Pass `detail: 'low'` for car parks (about 40 percent fewer triangles).
 */
import { MeshBuilder, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { TEAL, WARM } from '../../lib/hqProps.ts';
import { Frame, pick, scoped, type AssetResult, type GlowPoint, type Vec3t } from './common.ts';

export const CAR_PALETTE: readonly Rgb[] = [
  hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#3d6b52'), hex('#e8ebee'), hex('#1f2429'), hex('#7a8794'), hex('#a8b3be'), hex('#5b3a2c'),
];
const TYRE = hex('#15191e');
const HUB = hex('#aab3bc');
const CHROME = hex('#9aa4ae');
const RED = hex('#ff3b3b');
const GLASS = hex('#7fb2cc');
const DARK = hex('#14181d');

type Station = { z: number; hw: number; hwTop: number; y0: number; y1: number };
type LoftColours = { side: Rgb | readonly Rgb[]; top: Rgb | readonly Rgb[]; ends?: Rgb; emissive?: number };

const colourAt = (c: Rgb | readonly Rgb[], i: number): Rgb => (typeof c[0] === 'number' ? (c as Rgb) : (c as readonly Rgb[])[Math.min(i, (c as readonly Rgb[]).length - 1)]);

/** A body lofted through cross-sections along z. Stations run from front (high z) to back. */
export function loft(f: Frame, st: readonly Station[], c: LoftColours): void {
  const em = c.emissive ?? 0;
  for (let i = 0; i + 1 < st.length; i++) {
    const a = st[i], d = st[i + 1];
    const ref: Vec3t = [0, (Math.min(a.y0, d.y0) + Math.max(a.y1, d.y1)) / 2, (a.z + d.z) / 2];
    const top = colourAt(c.top, i), side = colourAt(c.side, i);
    const t4 = [top, top, top, top], s4 = [side, side, side, side];
    f.face([[-a.hwTop, a.y1, a.z], [a.hwTop, a.y1, a.z], [d.hwTop, d.y1, d.z], [-d.hwTop, d.y1, d.z]], ref, t4, em);
    f.face([[a.hw, a.y0, a.z], [a.hwTop, a.y1, a.z], [d.hwTop, d.y1, d.z], [d.hw, d.y0, d.z]], ref, s4, em);
    f.face([[-a.hw, a.y0, a.z], [-a.hwTop, a.y1, a.z], [-d.hwTop, d.y1, d.z], [-d.hw, d.y0, d.z]], ref, s4, em);
    f.face([[-a.hw, a.y0, a.z], [a.hw, a.y0, a.z], [d.hw, d.y0, d.z], [-d.hw, d.y0, d.z]], ref, [DARK, DARK, DARK, DARK], 0);
  }
  const ends = c.ends ?? colourAt(c.side, 0);
  for (const [s, k] of [[st[0], 0], [st[st.length - 1], 1]] as const) {
    const ref: Vec3t = [0, (s.y0 + s.y1) / 2, st[k === 0 ? 1 : st.length - 2].z];
    f.face([[-s.hw, s.y0, s.z], [s.hw, s.y0, s.z], [s.hwTop, s.y1, s.z], [-s.hwTop, s.y1, s.z]], ref, [ends, ends, ends, ends], em);
  }
}

type WheelOpts = { sides: number; hub: boolean; width: number };

function wheel(f: Frame, side: 1 | -1, x: number, z: number, r: number, o: WheelOpts): void {
  const x0 = side > 0 ? x - o.width : x;
  f.cyl(x0, r, z, 'x', r, r, o.width, o.sides, TYRE, { capStart: true, capEnd: true, capColor: scale(TYRE, 1.25) });
  if (o.hub) {
    const hx = side > 0 ? x : x - 0.03;
    f.cyl(hx, r, z, 'x', r * 0.58, r * 0.58, 0.03, 6, HUB, { capEnd: true, capStart: true });
  }
}

/** A flat dark patch on a body side so a wheel sits in an arch. */
function arch(f: Frame, side: 1 | -1, x: number, z: number, r: number, y0: number): void {
  const xx = side * (x + 0.006);
  f.quadToward([xx, y0, z - r - 0.1], [xx, y0, z + r + 0.1], [xx, y0 + r * 1.9, z + r * 0.9], [xx, y0 + r * 1.9, z - r * 0.9], DARK, [side, 0, 0]);
}

function headLights(f: Frame, z: number, w: number, y: number, wide = 0.46, tall = 0.16, glow?: GlowPoint[]): void {
  for (const sx of [-1, 1]) {
    f.box(sx > 0 ? w - 0.2 - wide : -w + 0.2, y, z - 0.02, wide, tall, 0.06, WARM, { emissive: 1, flat: true });
    if (glow) {
      const q = f.p(sx * (w - 0.43), y + tall / 2, z + 0.5);
      glow.push({ x: q[0], y: q[1], z: q[2], radius: 2.2, color: WARM, intensity: 0.35 });
    }
  }
}

function tailLights(f: Frame, z: number, w: number, y: number, wide = 0.4, tall = 0.15): void {
  for (const sx of [-1, 1]) f.box(sx > 0 ? w - 0.18 - wide : -w + 0.18, y, z - 0.04, wide, tall, 0.06, RED, { emissive: 0.9, flat: true });
}

function plate(f: Frame, z: number, y: number, front: boolean): void {
  f.box(-0.26, y, front ? z - 0.01 : z - 0.05, 0.52, 0.13, 0.06, hex('#e8e6dc'), { flat: true });
}

// ---------------------------------------------------------------- cars

export type CarKind = 'sedan' | 'hatch' | 'suv' | 'coupe' | 'pickup';
export type VehicleDetail = 'full' | 'low';
export type CarOptions = { kind?: CarKind; color?: Rgb; rot?: number; seed?: string; detail?: VehicleDetail };

type CarSpec = { L: number; W: number; belt: number; roof: number; cabinFront: number; roofFront: number; roofRear: number; cabinRear: number; trunk: number; clear: number; wheel: number };
const CAR_SPECS: Record<CarKind, CarSpec> = {
  sedan: { L: 4.6, W: 1.85, belt: 0.78, roof: 1.45, cabinFront: 1.0, roofFront: 0.38, roofRear: -0.72, cabinRear: -1.25, trunk: 0.84, clear: 0.2, wheel: 0.33 },
  hatch: { L: 3.95, W: 1.75, belt: 0.78, roof: 1.5, cabinFront: 0.82, roofFront: 0.3, roofRear: -1.2, cabinRear: -1.78, trunk: 0.78, clear: 0.2, wheel: 0.31 },
  suv: { L: 4.75, W: 1.95, belt: 1.0, roof: 1.78, cabinFront: 1.05, roofFront: 0.5, roofRear: -1.95, cabinRear: -2.3, trunk: 1.0, clear: 0.3, wheel: 0.39 },
  coupe: { L: 4.35, W: 1.86, belt: 0.72, roof: 1.3, cabinFront: 0.62, roofFront: 0.02, roofRear: -0.82, cabinRear: -1.42, trunk: 0.8, clear: 0.18, wheel: 0.33 },
  pickup: { L: 5.3, W: 2.0, belt: 1.02, roof: 1.84, cabinFront: 0.95, roofFront: 0.5, roofRear: -0.25, cabinRear: -0.55, trunk: 1.02, clear: 0.32, wheel: 0.4 },
};

export const CAR_TRIANGLES_CAP = { full: 680, low: 440 } as const;

/** A car. Returns triangle count, night glow points and bounds. */
export function car(b: MeshBuilder, x: number, z: number, o: CarOptions = {}): AssetResult {
  const rnd = seeded(o.seed ?? `car-${x.toFixed(1)}-${z.toFixed(1)}`);
  const kind = o.kind ?? pick(rnd, ['sedan', 'hatch', 'suv', 'sedan', 'hatch', 'coupe'] as const);
  const color = o.color ?? pick(rnd, CAR_PALETTE);
  const full = (o.detail ?? 'full') === 'full';
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const S = CAR_SPECS[kind];
    const h = S.L / 2, w = S.W / 2, c = S.clear, belt = S.belt;
    f.shadow(-w, -h, S.W, S.L, 1.5);
    const bodyEndY = kind === 'pickup' ? belt : S.trunk;
    const body: Station[] = [
      { z: h, hw: w * 0.86, hwTop: w * 0.82, y0: c + 0.12, y1: belt * 0.6 },
      { z: h - 0.12, hw: w * 0.97, hwTop: w * 0.93, y0: c, y1: belt * 0.7 },
      { z: h - 0.55, hw: w, hwTop: w * 0.95, y0: c, y1: belt * 0.9 },
      { z: S.cabinFront, hw: w, hwTop: w * 0.95, y0: c, y1: belt },
      { z: S.cabinRear, hw: w, hwTop: w * 0.95, y0: c, y1: kind === 'pickup' ? belt : bodyEndY },
      { z: -h + 0.12, hw: w * 0.97, hwTop: w * 0.9, y0: c, y1: kind === 'pickup' ? belt : bodyEndY },
      { z: -h, hw: w * 0.88, hwTop: w * 0.82, y0: c + 0.1, y1: kind === 'pickup' ? belt : bodyEndY * 0.92 },
    ];
    if (kind === 'pickup') body.length = 5;
    loft(f, body, { side: color, top: color, ends: scale(color, 0.85) });
    const rearTop = kind === 'hatch' || kind === 'suv' ? belt : S.trunk;
    const cabin: Station[] = [
      { z: S.cabinFront, hw: w * 0.94, hwTop: w * 0.94, y0: belt, y1: belt + 0.02 },
      { z: S.roofFront, hw: w * 0.93, hwTop: w * 0.8, y0: belt, y1: S.roof - 0.03 },
      { z: S.roofRear, hw: w * 0.93, hwTop: w * 0.8, y0: belt, y1: S.roof - 0.03 },
      { z: S.cabinRear, hw: w * 0.94, hwTop: w * 0.94, y0: belt, y1: rearTop + 0.02 },
    ];
    const roofCol = scale(color, 1.06);
    loft(f, cabin, { side: GLASS, top: [GLASS, roofCol, GLASS], ends: GLASS });
    const mid = (S.roofFront + S.roofRear) / 2;
    for (const sx of [-1, 1]) f.box(sx * w * 0.9 - 0.05, belt, mid - 0.05, 0.1, S.roof - belt - 0.03, 0.1, color, { flat: true });
    if (kind === 'pickup') {
      const bw = w * 2, z0 = -h, z1 = S.cabinRear;
      f.box(-w, c, z0, bw, 0.62, z1 - z0, color, { skip: ['top', 'front'] });
      f.box(-w + 0.08, 0.62, z0 + 0.05, bw - 0.16, 0.04, z1 - z0 - 0.1, hex('#262c33'), { flat: true });
      f.box(-w, belt, z0, 0.08, 0.1, z1 - z0, color); f.box(w - 0.08, belt, z0, 0.08, 0.1, z1 - z0, color); f.box(-w, belt, z0, bw, 0.1, 0.08, color);
      f.box(-w, c + 0.62, z0, 0.08, belt - c - 0.62, z1 - z0, color, { skip: ['bottom'] }); f.box(w - 0.08, c + 0.62, z0, 0.08, belt - c - 0.62, z1 - z0, color, { skip: ['bottom'] });
      f.box(-w, c + 0.62, z0, bw, belt - c - 0.62, 0.08, color, { skip: ['bottom'] });
    }
    // Front and rear fascia.
    f.box(-w * 0.6, c + 0.16, h - 0.04, w * 1.2, 0.2, 0.06, DARK, { flat: true });
    f.box(-w * 0.5, c + 0.4, h - 0.1, w, 0.1, 0.06, CHROME, { flat: true });
    headLights(f, h - 0.08, w, belt * 0.52, 0.5, 0.17, glow);
    tailLights(f, -h + 0.06, w, bodyEndY * 0.68, 0.42, 0.16);
    // Wheels and arches.
    const wr = S.wheel, wo: WheelOpts = { sides: full ? 10 : 8, hub: full, width: 0.23 };
    for (const az of [h - 0.95, -h + 0.95]) {
      for (const sd of [-1, 1] as const) {
        wheel(f, sd, sd * (w + 0.02), az, wr, wo);
        arch(f, sd, w, az, wr, c);
      }
    }
    f.box(-w * 0.7, 0.1, -h + 0.5, w * 1.4, 0.16, S.L - 1.0, DARK, { flat: true });
    if (full) {
      plate(f, h - 0.02, c + 0.1, true);
      plate(f, -h + 0.03, bodyEndY * 0.45, false);
      for (const sx of [-1, 1]) {
        f.box(sx * (w + 0.06) - 0.05, belt + 0.02, S.cabinFront - 0.1, 0.1, 0.12, 0.2, scale(color, 0.8), { flat: true });
        for (const dz of [S.cabinFront - 0.35, (S.roofFront + S.roofRear) / 2 - 0.6]) f.box(sx * (w + 0.004) - 0.01, c + 0.1, dz, 0.02, belt - c - 0.12, 0.025, DARK, { flat: true });
      }
    }
  });
}

// ---------------------------------------------------------------- vans

export type VanKind = 'cargo' | 'passenger';
export type VanOptions = { kind?: VanKind; color?: Rgb; rot?: number; seed?: string; detail?: VehicleDetail };
export const VAN_TRIANGLES_CAP = 560;

export function van(b: MeshBuilder, x: number, z: number, o: VanOptions = {}): AssetResult {
  const kind = o.kind ?? 'cargo';
  const color = o.color ?? hex('#e8ebee');
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const h = 2.6, w = 1.0, c = 0.28;
    f.shadow(-w, -h, 2 * w, 2 * h, 2.2);
    const st: Station[] = [
      { z: h, hw: w * 0.88, hwTop: w * 0.84, y0: c + 0.12, y1: 0.8 },
      { z: h - 0.12, hw: w * 0.97, hwTop: w * 0.93, y0: c, y1: 0.9 },
      { z: h - 0.7, hw: w, hwTop: w * 0.96, y0: c, y1: 1.18 },
      { z: 1.55, hw: w, hwTop: w * 0.96, y0: c, y1: 1.26 },
      { z: 0.85, hw: w, hwTop: w * 0.96, y0: c, y1: 2.05 },
      { z: -h + 0.08, hw: w, hwTop: w * 0.96, y0: c, y1: 2.1 },
      { z: -h, hw: w * 0.97, hwTop: w * 0.94, y0: c + 0.05, y1: 2.04 },
    ];
    loft(f, st, { side: color, top: [color, GLASS, color, color, color, color], ends: scale(color, 0.85) });
    // Cab and passenger glazing as flat panes slightly proud of the sides.
    for (const sd of [-1, 1] as const) {
      const xx = sd * (w + 0.006);
      f.quadToward([xx, 1.28, 1.5], [xx, 1.28, 0.95], [xx, 1.92, 0.95], [xx, 1.92, 1.3], GLASS, [sd, 0, 0]);
      if (kind === 'passenger') {
        for (const [z0, z1] of [[0.5, -0.5], [-0.65, -1.65]] as const) f.quadToward([xx, 1.35, z0], [xx, 1.35, z1], [xx, 1.95, z1], [xx, 1.95, z0], GLASS, [sd, 0, 0]);
      } else {
        f.box(sd * (w + 0.004) - 0.01, c + 0.1, -0.2, 0.02, 1.75, 0.03, DARK, { flat: true });
        f.box(sd * (w + 0.004) - 0.01, c + 0.1, -1.9, 0.02, 1.75, 0.03, DARK, { flat: true });
      }
      f.box(sd * (w + 0.06) - 0.05, 1.2, 1.55, 0.1, 0.16, 0.2, scale(color, 0.8), { flat: true });
    }
    f.box(-w * 0.7, c + 0.16, h - 0.04, w * 1.4, 0.2, 0.06, DARK, { flat: true });
    headLights(f, h - 0.08, w, 0.62, 0.5, 0.18, glow);
    tailLights(f, -h + 0.04, w, 0.7, 0.2, 0.7);
    f.box(-0.25, 0.5, -h - 0.01, 0.5, 0.13, 0.05, hex('#e8e6dc'), { flat: true });
    f.box(-w * 0.9, c + 0.55, -h - 0.02, w * 1.8, 0.05, 0.04, DARK, { flat: true });
    const wo: WheelOpts = { sides: 10, hub: (o.detail ?? 'full') === 'full', width: 0.26 };
    for (const az of [1.7, -1.55]) for (const sd of [-1, 1] as const) { wheel(f, sd, sd * (w + 0.02), az, 0.38, wo); arch(f, sd, w, az, 0.38, c); }
    f.box(-w * 0.2, 2.1, -1.8, w * 0.4, 0.1, 3.0, hex('#aeb6bf'), { flat: true });
    f.box(-w * 0.7, 0.12, -h + 0.5, w * 1.4, 0.16, 2 * h - 1.0, DARK, { flat: true });
  });
}

// ---------------------------------------------------------------- trucks

export type TruckKind = 'box' | 'flatbed' | 'tanker' | 'semi';
export type TruckOptions = { kind?: TruckKind; cab?: Rgb; body?: Rgb; rot?: number; seed?: string; detail?: VehicleDetail };
export const TRUCK_TRIANGLES_CAP = 960;

/** The tractor/cab block with its windscreen, side glass, grille and lamps. Returns the cab's rear z. */
function cab(f: Frame, zFront: number, w: number, color: Rgb, glow: GlowPoint[]): number {
  const roof = scale(color, 1.05);
  const st: Station[] = [
    { z: zFront, hw: w * 0.94, hwTop: w * 0.9, y0: 0.55, y1: 1.5 },
    { z: zFront - 0.1, hw: w, hwTop: w * 0.96, y0: 0.5, y1: 1.6 },
    { z: zFront - 0.62, hw: w, hwTop: w * 0.94, y0: 0.5, y1: 2.65 },
    { z: zFront - 2.1, hw: w, hwTop: w * 0.94, y0: 0.5, y1: 2.7 },
  ];
  loft(f, st, { side: color, top: [color, GLASS, roof], ends: scale(color, 0.85) });
  for (const sd of [-1, 1] as const) {
    const xx = sd * (w + 0.006);
    f.quadToward([xx, 1.75, zFront - 0.7], [xx, 1.75, zFront - 1.55], [xx, 2.5, zFront - 1.55], [xx, 2.5, zFront - 0.8], GLASS, [sd, 0, 0]);
    f.box(sd * (w + 0.1) - 0.06, 1.7, zFront - 0.55, 0.12, 0.7, 0.14, DARK, { flat: true });
  }
  f.box(-w * 0.7, 0.75, zFront - 0.02, w * 1.4, 0.55, 0.06, DARK, { flat: true });
  f.box(-w * 0.6, 1.0, zFront - 0.04, w * 1.2, 0.05, 0.06, CHROME, { flat: true });
  headLights(f, zFront - 0.04, w, 0.62, 0.42, 0.22, glow);
  f.box(-w, 0.38, zFront, w * 2, 0.22, 0.14, scale(color, 0.7));
  return zFront - 2.1;
}

export function truck(b: MeshBuilder, x: number, z: number, o: TruckOptions = {}): AssetResult {
  const kind = o.kind ?? 'box';
  const cabCol = o.cab ?? hex('#2f4b66');
  const bodyCol = o.body ?? hex('#e3e7ec');
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const w = 1.2;
    const semi = kind === 'semi';
    const zF = semi ? 3.2 : 4.6;
    const zBack = semi ? -9.4 : -4.6;
    f.shadow(-w, zBack, 2 * w, zF - zBack, 3.4);
    const cabBack = cab(f, zF, w, cabCol, glow);
    const wo: WheelOpts = { sides: 10, hub: (o.detail ?? 'full') === 'full', width: 0.3 };
    const wr = 0.52;
    const axle = (az: number, dual: boolean) => {
      for (const sd of [-1, 1] as const) {
        wheel(f, sd, sd * (w + 0.04), az, wr, wo);
        if (dual) wheel(f, sd, sd * (w + 0.04) - sd * 0.34, az, wr, wo);
        arch(f, sd, w, az, wr, 0.45);
      }
    };
    if (semi) {
      f.box(-w * 0.8, 0.55, zBack, w * 1.6, 0.3, zF - zBack - 2.2, hex('#2a313b'));
      axle(zF - 0.95, false); axle(cabBack + 0.2, true); axle(cabBack - 1.3, true);
      const tz1 = cabBack + 0.6, tz0 = -9.6;
      f.box(-w * 0.95, 0.85, tz0, w * 1.9, 0.18, tz1 - tz0, hex('#2a313b'));
      f.box(-w, 1.03, tz0, w * 2, 2.75, tz1 - tz0 - 0.4, bodyCol, { grad: 0.12 });
      f.box(-w - 0.01, 2.0, tz0, w * 2 + 0.02, 0.28, tz1 - tz0 - 0.4, TEAL, { emissive: 0.3, flat: true });
      for (let k = 0; k < 9; k++) f.box(-w - 0.012, 1.08, tz0 + 0.5 + k * 1.15, w * 2 + 0.024, 2.65, 0.05, scale(bodyCol, 0.8), { flat: true });
      axle(tz0 + 1.2, true); axle(tz0 + 2.6, true);
      tailLights(f, tz0 + 0.02, w, 1.2, 0.35, 0.2);
    } else {
      f.box(-w * 0.8, 0.55, zBack, w * 1.6, 0.32, zF - zBack - 0.2, hex('#2a313b'));
      axle(zF - 1.1, false); axle(-2.6, true); axle(-3.9, true);
      if (kind === 'box') {
        f.box(-w, 0.9, zBack + 0.1, w * 2, 3.1, cabBack - zBack - 0.5, bodyCol, { grad: 0.14 });
        f.box(-w - 0.01, 1.7, zBack + 0.1, w * 2 + 0.02, 0.28, cabBack - zBack - 0.5, TEAL, { emissive: 0.3, flat: true });
        for (const sd of [-1, 1]) f.box(sd * (w + 0.004) - 0.01, 1.0, -2.4, 0.02, 2.9, 0.04, scale(bodyCol, 0.75), { flat: true });
      } else if (kind === 'flatbed') {
        f.box(-w, 0.9, zBack + 0.1, w * 2, 0.2, cabBack - zBack - 0.5, hex('#59636e'));
        const rnd = seeded(o.seed ?? 'flatbed');
        for (let i = 0; i < 4; i++) {
          const zz = zBack + 0.5 + i * 1.5;
          f.box(-w + 0.15, 1.1, zz, w * 2 - 0.3, 0.9, 1.3, mix(hex('#b08a58'), hex('#8e6c42'), rnd()), { grad: 0.1 });
          f.box(-w + 0.15, 2.0, zz + 0.1, w * 2 - 0.3, 0.7, 1.1, mix(hex('#b08a58'), hex('#caa66c'), rnd()), { grad: 0.1 });
        }
      } else {
        const len = cabBack - zBack - 0.8;
        f.cyl(0, 1.1 + 0.95, zBack + 0.4, 'z', 0.95, 0.95, len, 12, hex('#c9d3dc'), { capStart: true, capEnd: true });
        f.box(-w * 0.6, 0.9, zBack + 0.5, w * 1.2, 0.35, len - 0.2, hex('#2a313b'));
        f.box(-0.3, 3.0, zBack + len * 0.5, 0.6, 0.25, 0.6, hex('#8a949f'));
      }
      tailLights(f, zBack + 0.1, w, 0.85, 0.3, 0.2);
    }
  });
}

// ---------------------------------------------------------------- bus

export type BusOptions = { color?: Rgb; accent?: Rgb; rot?: number; detail?: VehicleDetail };
export const BUS_TRIANGLES_CAP = 700;

/** A 12 m city bus: glazed side bands with pillars, doors, blank destination board, roof units. */
export function bus(b: MeshBuilder, x: number, z: number, o: BusOptions = {}): AssetResult {
  const color = o.color ?? hex('#e8ebee');
  const accent = o.accent ?? TEAL;
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const w = 1.25, h = 6.0;
    f.shadow(-w, -h, 2 * w, 2 * h, 3.1);
    const st: Station[] = [
      { z: h, hw: w * 0.86, hwTop: w * 0.82, y0: 0.5, y1: 2.65 },
      { z: h - 0.15, hw: w, hwTop: w * 0.96, y0: 0.4, y1: 3.0 },
      { z: h - 0.5, hw: w, hwTop: w * 0.96, y0: 0.35, y1: 3.12 },
      { z: -h + 0.1, hw: w, hwTop: w * 0.96, y0: 0.35, y1: 3.12 },
      { z: -h, hw: w * 0.94, hwTop: w * 0.9, y0: 0.4, y1: 3.0 },
    ];
    loft(f, st, { side: color, top: color, ends: scale(color, 0.9) });
    for (const sd of [-1, 1] as const) {
      const xx = sd * (w + 0.006);
      const panes = [4.3, 3.0, 1.7, 0.4, -0.9, -2.2, -3.5, -4.8];
      for (let i = 0; i < panes.length - 1; i++) {
        if (i === 0 || (sd === 1 && i === 3)) continue; // the doors
        f.quadToward([xx, 1.3, panes[i]], [xx, 1.3, panes[i + 1] + 0.1], [xx, 2.6, panes[i + 1] + 0.1], [xx, 2.6, panes[i]], GLASS, [sd, 0, 0], 0);
      }
      const doorZ = sd === 1 ? [[5.0, 4.2], [0.35, -0.5]] : [[5.0, 4.2]];
      for (const [d0, d1] of doorZ) f.quadToward([xx, 0.55, d0], [xx, 0.55, d1], [xx, 2.6, d1], [xx, 2.6, d0], hex('#5f8aa3'), [sd, 0, 0]);
      f.box(sd * (w + 0.01) - 0.01, 0.75, -h + 0.3, 0.02, 0.28, 2 * h - 0.6, accent, { flat: true, emissive: 0.15 });
    }
    // Windscreen and blank amber destination board.
    f.quadToward([-w * 0.8, 1.15, h + 0.012], [w * 0.8, 1.15, h + 0.012], [w * 0.8, 2.55, h - 0.1], [-w * 0.8, 2.55, h - 0.1], GLASS, [0, 0, 1]);
    f.box(-w * 0.6, 2.62, h - 0.12, w * 1.2, 0.3, 0.06, hex('#f2b24a'), { emissive: 0.9, flat: true });
    headLights(f, h - 0.04, w, 0.7, 0.4, 0.2, glow);
    tailLights(f, -h + 0.04, w, 1.0, 0.3, 0.35);
    f.box(-w * 0.9, 3.12, -2.6, w * 1.8, 0.35, 3.0, hex('#aeb6bf'), { grad: 0.1 });
    f.box(-w * 0.5, 3.12, 2.4, w, 0.14, 1.2, hex('#8a949f'));
    const wo: WheelOpts = { sides: 10, hub: (o.detail ?? 'full') === 'full', width: 0.3 };
    for (const az of [3.9, -3.7]) for (const sd of [-1, 1] as const) { wheel(f, sd, sd * (w + 0.03), az, 0.52, wo); arch(f, sd, w, az, 0.52, 0.35); }
    f.box(-w * 0.8, 0.14, -h + 0.5, w * 1.6, 0.22, 2 * h - 1.0, DARK, { flat: true });
  });
}
