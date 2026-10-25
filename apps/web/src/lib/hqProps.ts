/**
 * Reusable props for the headquarters scenes (campus and building): trees, vehicles, lamps,
 * rooftop plant, water, a tennis court, canal and bridges, and the ANUM mark. Everything here is
 * decoration generated from fixed seeds: no data, names or counts are encoded in geometry (sign
 * lettering is drawn from real names at render time, see `hqTextures.ts`).
 *
 * Window convention: an emissive weight of 2 or more marks a window. The renderer shows it as
 * reflective glass by day and as a warm lit pane at night (weight minus two is the glow).
 * Shadows are real (a shadow map in the renderer), so nothing here is baked into the ground.
 */
import { MAT, MeshBuilder, hex, mix, scale, type MatId, type Rgb } from './hqGeometry.ts';

export const WINDOW_BASE = 2;
export const GROUND = hex('#44704a');
export const STONE = hex('#b9b4a6');
export const ASPHALT = hex('#3a3f46');
export const WATER = hex('#2c6f99');
export const WARM = hex('#ffcf7a');
export const TEAL = hex('#45d0b5');
export const GLASS_DARK = hex('#1c2c3b');

/** A flat polygon on a horizontal plane, wound so it faces up whatever order the points come in. */
export function flatPoly(b: MeshBuilder, pts: ReadonlyArray<readonly [number, number]>, y: number, color: Rgb, emissive = 0): void {
  let area = 0;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i], q = pts[(i + 1) % pts.length];
    area += p[0] * q[1] - q[0] * p[1];
  }
  // With x east and z south the shoelace sum is positive for the order that faces down, so reverse it.
  const ordered = area > 0 ? [...pts].reverse() : pts;
  for (let i = 1; i < ordered.length - 1; i++) {
    b.tri([ordered[0][0], y, ordered[0][1]], [ordered[i][0], y, ordered[i][1]], [ordered[i + 1][0], y, ordered[i + 1][1]], color, emissive);
  }
}

/** A box turned to any heading: centre (cx, cz), `len` along (dx, dz), `wid` across, from y to y + h. */
export function orientedBox(b: MeshBuilder, cx: number, y: number, cz: number, dx: number, dz: number, len: number, wid: number, h: number, color: Rgb, emissive = 0): void {
  const l = Math.hypot(dx, dz) || 1;
  const ux = dx / l, uz = dz / l, vx = -uz, vz = ux;
  const c = (a: number, w: number): [number, number] => [cx + ux * a + vx * w, cz + uz * a + vz * w];
  const p0 = c(-len / 2, -wid / 2), p1 = c(len / 2, -wid / 2), p2 = c(len / 2, wid / 2), p3 = c(-len / 2, wid / 2);
  const top = y + h;
  flatPoly(b, [p0, p1, p2, p3], top, color, emissive);
  flatPoly(b, [p0, p1, p2, p3], y, scale(color, 0.6), emissive);
  const side = (a: [number, number], d: [number, number], f: number) => {
    b.quad([a[0], y, a[1]], [d[0], y, d[1]], [d[0], top, d[1]], [a[0], top, a[1]], scale(color, f), emissive);
  };
  side(p0, p1, 0.82); side(p1, p2, 0.9); side(p2, p3, 0.78); side(p3, p0, 0.7);
}

/** A cylinder lying along x (wheels). */
export function cylX(b: MeshBuilder, cx: number, cy: number, cz: number, radius: number, len: number, sides: number, color: Rgb, rim?: Rgb): void {
  const ring = (i: number): [number, number] => [cy + Math.sin((i / sides) * Math.PI * 2) * radius, cz + Math.cos((i / sides) * Math.PI * 2) * radius];
  for (let i = 0; i < sides; i++) {
    const a = ring(i), c = ring(i + 1);
    const shade = 0.75 + 0.25 * Math.sin((i / sides) * Math.PI * 2 + 1);
    b.quad([cx - len / 2, a[0], a[1]], [cx + len / 2, a[0], a[1]], [cx + len / 2, c[0], c[1]], [cx - len / 2, c[0], c[1]], scale(color, shade));
    if (rim) {
      for (const sx of [-1, 1]) {
        const inner = (j: number): [number, number] => [cy + Math.sin((j / sides) * Math.PI * 2) * radius * 0.6, cz + Math.cos((j / sides) * Math.PI * 2) * radius * 0.6];
        const ia = inner(i), ic = inner(i + 1);
        b.tri([cx + sx * (len / 2 + 0.005), cy, cz], [cx + sx * (len / 2 + 0.005), ia[0], ia[1]], [cx + sx * (len / 2 + 0.005), ic[0], ic[1]], rim);
      }
    }
  }
}

/**
 * Baked ground shadows are gone: the renderer draws a real shadow map. The function stays, drawing nothing,
 * so builders written for the baked version (the exterior assets) keep working and never double the shadow.
 */
export function castShadow(_b: MeshBuilder, _x: number, _z: number, _w: number, _d: number, _h: number, _y?: number, _color?: Rgb): void { /* real shadows */ }

// ---------------------------------------------------------------- planting

export function palm(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  const lean = (rnd() - 0.5) * 0.7;
  const bark = hex('#7a6044');
  b.prism(x, 0, z, 0.24, 0.16, h * 0.5, 6, bark, { capTop: false });
  b.prism(x + lean * 0.5, h * 0.5, z, 0.16, 0.12, h * 0.3, 6, scale(bark, 0.95), { capTop: false });
  b.prism(x + lean, h * 0.8, z, 0.12, 0.1, h * 0.2, 6, scale(bark, 0.9), { capTop: false });
  const topY = h;
  const cx = x + lean, cz = z;
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2 + rnd() * 0.3;
    const len = 2.2 + rnd() * 0.7;
    const tipX = cx + Math.cos(a) * len, tipZ = cz + Math.sin(a) * len;
    const midX = cx + Math.cos(a) * len * 0.5, midZ = cz + Math.sin(a) * len * 0.5;
    const sx = Math.sin(a) * 0.4, sz = -Math.cos(a) * 0.4;
    const leaf = mix(hex('#2d7a3f'), hex('#58a050'), rnd());
    b.tri([cx - sx * 0.3, topY, cz - sz * 0.3], [cx + sx * 0.3, topY, cz + sz * 0.3], [midX + sx, topY + 0.35, midZ + sz], leaf);
    b.tri([cx - sx * 0.3, topY, cz - sz * 0.3], [midX + sx, topY + 0.35, midZ + sz], [midX - sx, topY + 0.35, midZ - sz], leaf);
    b.tri([midX + sx, topY + 0.35, midZ + sz], [midX - sx, topY + 0.35, midZ - sz], [tipX, topY - 1.1, tipZ], scale(leaf, 0.85));
  }
  b.blob(cx, topY - 0.15, cz, 0.3, 0.26, 0.3, hex('#5f9a46'), 5);
}

export function roundTree(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  b.prism(x, 0, z, 0.26, 0.2, h * 0.5, 6, hex('#5d4630'), { capTop: false });
  const leaf = mix(hex('#2f6a43'), hex('#5a9a54'), rnd());
  b.blob(x, h * 0.64, z, h * 0.36, h * 0.3, h * 0.36, leaf, 8);
  b.blob(x + h * 0.14, h * 0.8, z - h * 0.06, h * 0.24, h * 0.2, h * 0.24, scale(leaf, 1.12), 7);
  b.blob(x - h * 0.16, h * 0.72, z + h * 0.1, h * 0.22, h * 0.19, h * 0.22, scale(leaf, 0.92), 7);
  b.blob(x + h * 0.04, h * 0.56, z + h * 0.18, h * 0.2, h * 0.16, h * 0.2, scale(leaf, 0.84), 6);
}

export function cypress(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  b.prism(x, 0, z, 0.16, 0.12, h * 0.15, 5, hex('#5d4630'), { capTop: false });
  b.prism(x, h * 0.1, z, h * 0.17, h * 0.1, h * 0.55, 8, mix(hex('#245437'), hex('#3a7a48'), rnd()));
  b.prism(x, h * 0.55, z, h * 0.11, 0, h * 0.45, 8, mix(hex('#2a6240'), hex('#46894f'), rnd()));
}

export function bush(b: MeshBuilder, x: number, z: number, r: number, rnd: () => number): void {
  const leaf = mix(hex('#2b6a40'), hex('#5a9a52'), rnd());
  b.blob(x, r * 0.55, z, r, r * 0.65, r, leaf, 7);
  b.blob(x + r * 0.5, r * 0.4, z + r * 0.2, r * 0.6, r * 0.45, r * 0.6, scale(leaf, 1.1), 6);
}

export function hedge(b: MeshBuilder, x: number, z: number, w: number, d: number, h = 0.8): void {
  b.box(x, 0, z, w, h, d, hex('#2c6a3e'), { grad: 0.15 });
}

export function flowerBed(b: MeshBuilder, x: number, z: number, w: number, d: number, rnd: () => number): void {
  b.box(x, 0, z, w, 0.22, d, hex('#7e776a'));
  b.plane(x + 0.12, 0.23, z + 0.12, w - 0.24, d - 0.24, hex('#3f6a3b'));
  const tones = [hex('#f2c14e'), hex('#e0646e'), hex('#f4f0e6'), hex('#b48cff')];
  const n = Math.max(3, Math.floor((w * d) / 1.2));
  for (let i = 0; i < n; i++) {
    b.blob(x + 0.3 + rnd() * (w - 0.6), 0.4, z + 0.3 + rnd() * (d - 0.6), 0.16, 0.14, 0.16, tones[Math.floor(rnd() * tones.length)], 5);
  }
}

export function planter(b: MeshBuilder, x: number, z: number, size: number, rnd: () => number): void {
  b.box(x - size / 2, 0, z - size / 2, size, 0.55, size, hex('#8d8777'), { grad: 0.1 });
  bush(b, x, z, size * 0.42, rnd);
}

export function bench(b: MeshBuilder, x: number, z: number, along: 'x' | 'z'): void {
  const w = along === 'x' ? 1.8 : 0.5, d = along === 'x' ? 0.5 : 1.8;
  b.withMat(MAT.wood, () => b.box(x - w / 2, 0.38, z - d / 2, w, 0.07, d, hex('#9a7a52')));
  b.box(x - w / 2 + 0.1, 0, z - d / 2 + 0.05, 0.08, 0.38, d - 0.1, hex('#2a313b'));
  b.box(x + w / 2 - 0.18, 0, z - d / 2 + 0.05, 0.08, 0.38, d - 0.1, hex('#2a313b'));
}

export function lamp(b: MeshBuilder, x: number, z: number): void {
  b.prism(x, 0, z, 0.1, 0.08, 2.8, 6, hex('#2a313b'), { capTop: false });
  b.box(x - 0.24, 2.8, z - 0.24, 0.48, 0.12, 0.48, hex('#2a313b'));
  b.box(x - 0.2, 2.68, z - 0.2, 0.4, 0.12, 0.4, WARM, { emissive: 1, shade: false });
}

// ---------------------------------------------------------------- vehicles

type Xf = (lx: number, ly: number, lz: number) => [number, number, number];

/** Turns the local car frame (nose towards +z) by quarter turns about (ox, oz) and scales it. */
function frame(rot: number, ox: number, oz: number, s: number): Xf {
  const r = ((rot % 4) + 4) % 4;
  return (lx, ly, lz) => {
    const a = lx * s, y = ly * s, c = lz * s;
    if (r === 0) return [ox + a, y, oz + c];
    if (r === 1) return [ox - c, y, oz + a];
    if (r === 2) return [ox - a, y, oz - c];
    return [ox + c, y, oz - a];
  };
}

/** A box in the local car frame, turned to the heading. */
export function lbox(
  b: MeshBuilder, ox: number, oz: number, rot: number, lx: number, ly: number, lz: number, w: number, h: number, d: number,
  color: Rgb, options: { emissive?: number; shade?: boolean; grad?: number; skip?: readonly ('top' | 'bottom' | 'north' | 'south' | 'west' | 'east')[] } = {},
): void {
  const r = ((rot % 4) + 4) % 4;
  if (r === 0) b.box(ox + lx, ly, oz + lz, w, h, d, color, options);
  else if (r === 1) b.box(ox - lz - d, ly, oz + lx, d, h, w, color, options);
  else if (r === 2) b.box(ox - lx - w, ly, oz - lz - d, w, h, d, color, options);
  else b.box(ox + lz, ly, oz - lx - w, d, h, w, color, options);
}

/** Extrudes a side profile (z, y points, anticlockwise from the rear bottom) across the width, with a colour per top edge. */
function extrude(b: MeshBuilder, T: Xf, profile: ReadonlyArray<readonly [number, number]>, hw: number, colors: ReadonlyArray<{ c: Rgb; e?: number } | null>): void {
  const n = profile.length;
  for (let i = 0; i < n; i++) {
    const spec = colors[i];
    if (!spec) continue;
    const p = profile[i], q = profile[(i + 1) % n];
    b.quad(T(-hw, p[1], p[0]), T(hw, p[1], p[0]), T(hw, q[1], q[0]), T(-hw, q[1], q[0]), spec.c, spec.e ?? 0);
  }
  const cz = profile.reduce((a, p) => a + p[0], 0) / n, cy = profile.reduce((a, p) => a + p[1], 0) / n;
  for (const sx of [-1, 1]) {
    const body = colors.find((c) => c && !c.e)?.c ?? [0.5, 0.5, 0.5];
    for (let i = 0; i < n; i++) {
      const p = profile[i], q = profile[(i + 1) % n];
      b.tri(T(sx * hw, cy, cz), T(sx * hw, p[1], p[0]), T(sx * hw, q[1], q[0]), scale(body, 0.92));
    }
  }
}

/** A parked car, nose along +z for rot 0 (0..3 quarter turns), `s` scales it. */
export function car(b: MeshBuilder, x: number, z: number, color: Rgb, rot = 0, s = 1): void {
  const T = frame(rot, x, z, s);
  const glass = { c: hex('#13202c'), e: WINDOW_BASE };
  const body = { c: color };
  const roof = { c: scale(color, 1.08) };
  const profile: Array<readonly [number, number]> = [
    [-2.1, 0.3], [2.1, 0.3], [2.12, 0.62], [1.92, 0.84], [1.12, 0.95], [0.62, 1.42], [-0.78, 1.42], [-1.42, 0.98], [-2.0, 0.92], [-2.12, 0.62],
  ];
  b.withMat(MAT.paint, () => {
    extrude(b, T, profile, 0.9, [null, body, body, body, glass, roof, glass, body, body, body]);
    // Lower sills, bumpers and a grille.
    const bump = scale(color, 0.55);
    b.quad(T(-0.92, 0.3, 2.1), T(0.92, 0.3, 2.1), T(0.92, 0.5, 2.14), T(-0.92, 0.5, 2.14), bump);
    b.quad(T(-0.92, 0.3, -2.1), T(0.92, 0.3, -2.1), T(0.92, 0.5, -2.14), T(-0.92, 0.5, -2.14), bump);
  });
  // Side windows: dark glass patches on the cabin.
  for (const sx of [-1, 1]) {
    const gx = sx * 0.905;
    b.withMat(MAT.glass, () => {
      b.quad(T(gx, 0.98, -1.2), T(gx, 1.38, -0.7), T(gx, 1.38, 0.55), T(gx, 0.98, 1.0), hex('#13202c'), WINDOW_BASE);
    });
    // Mirrors.
    b.box(...T(sx * 1.0 - 0.06, 1.0, 0.95), 0.12 * s, 0.1 * s, 0.14 * s, scale(color, 0.8));
  }
  // Lamps and plate.
  b.box(...T(-0.72, 0.58, 2.1), 0.42 * s, 0.14 * s, 0.05 * s, WARM, { emissive: 1, shade: false });
  b.box(...T(0.3, 0.58, 2.1), 0.42 * s, 0.14 * s, 0.05 * s, WARM, { emissive: 1, shade: false });
  b.box(...T(-0.78, 0.6, -2.16), 0.4 * s, 0.12 * s, 0.05 * s, hex('#ff3b3b'), { emissive: 0.95, shade: false });
  b.box(...T(0.38, 0.6, -2.16), 0.4 * s, 0.12 * s, 0.05 * s, hex('#ff3b3b'), { emissive: 0.95, shade: false });
  // Wheels with rims, turned with the car.
  for (const [wx, wz] of [[-0.86, 1.3], [0.86, 1.3], [-0.86, -1.3], [0.86, -1.3]] as const) {
    const [px, py, pz] = T(wx, 0.34, wz);
    if (rot % 2 === 0) cylX(b, px, py, pz, 0.34 * s, 0.24 * s, 10, hex('#14181d'), hex('#b9c2cb'));
    else wheelAlongZ(b, px, py, pz, 0.34 * s, 0.24 * s, hex('#14181d'), hex('#b9c2cb'));
  }
}

/** A wheel whose axle runs along z (cars turned a quarter). */
function wheelAlongZ(b: MeshBuilder, cx: number, cy: number, cz: number, radius: number, len: number, color: Rgb, rim: Rgb): void {
  const sides = 10;
  const ring = (i: number, r: number): [number, number] => [cx + Math.cos((i / sides) * Math.PI * 2) * r, cy + Math.sin((i / sides) * Math.PI * 2) * r];
  for (let i = 0; i < sides; i++) {
    const a = ring(i, radius), c = ring(i + 1, radius);
    b.quad([a[0], a[1], cz - len / 2], [a[0], a[1], cz + len / 2], [c[0], c[1], cz + len / 2], [c[0], c[1], cz - len / 2], scale(color, 0.75 + 0.25 * Math.sin((i / sides) * Math.PI * 2 + 1)));
    const ia = ring(i, radius * 0.6), ic = ring(i + 1, radius * 0.6);
    for (const sz of [-1, 1]) b.tri([cx, cy, cz + sz * (len / 2 + 0.005)], [ia[0], ia[1], cz + sz * (len / 2 + 0.005)], [ic[0], ic[1], cz + sz * (len / 2 + 0.005)], rim);
  }
}

/** A box truck (cab and container), nose along +z for rot 0. */
export function truck(b: MeshBuilder, x: number, z: number, cab: Rgb, rot = 0): void {
  lbox(b, x, z, rot, -1.2, 0.6, -4.7, 2.4, 3.0, 6.6, hex('#e3e7ec'), { grad: 0.16 });
  lbox(b, x, z, rot, -1.22, 1.3, -4.7, 2.44, 0.3, 6.6, TEAL, { emissive: 0.3 });
  lbox(b, x, z, rot, -1.15, 0.6, 2.0, 2.3, 2.2, 2.4, cab, { grad: 0.2 });
  b.withMat(MAT.glass, () => lbox(b, x, z, rot, -0.95, 1.9, 4.3, 1.9, 0.8, 0.08, hex('#13202c'), { emissive: WINDOW_BASE, shade: false }));
  lbox(b, x, z, rot, -0.9, 0.8, 4.4, 0.5, 0.25, 0.08, WARM, { emissive: 1, shade: false });
  lbox(b, x, z, rot, 0.4, 0.8, 4.4, 0.5, 0.25, 0.08, WARM, { emissive: 1, shade: false });
  lbox(b, x, z, rot, -1.2, 0.2, -4.6, 2.4, 0.4, 9.0, hex('#2a313b'));
  for (const wz of [-3.4, -1.6, 3.2]) for (const sx of [-1.28, 1.08]) lbox(b, x, z, rot, sx, 0, wz, 0.22, 0.95, 0.95, hex('#161b21'));
}

const CAR_COLORS = [hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#3d6b52'), hex('#e8ebee'), hex('#1f2429'), hex('#7a8794')];

export function parkingLot(b: MeshBuilder, x: number, z: number, cols: number, rows: number, rnd: () => number, fill = 0.7, carScale = 1): void {
  const w = cols * 2.9 + 1, d = rows * 5.6 + 1.6;
  b.withMat(MAT.asphalt, () => b.plane(x - 0.5, 0.012, z - 0.8, w, d, scale(ASPHALT, 1.05)));
  for (let c = 0; c <= cols; c++) b.plane(x - 0.5 + c * 2.9 + 0.4, 0.018, z - 0.4, 0.1, d - 0.8, hex('#d8d8d0'));
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (rnd() > fill) continue;
      car(b, x + c * 2.9 + 1.0, z + r * 5.6 + 2.2, CAR_COLORS[Math.floor(rnd() * CAR_COLORS.length)], rnd() < 0.5 ? 0 : 2, carScale);
    }
  }
}

// ---------------------------------------------------------------- roofs

export function acUnit(b: MeshBuilder, x: number, y: number, z: number, w: number, d: number): void {
  b.withMat(MAT.metal, () => b.box(x, y, z, w, 0.9, d, hex('#aeb6bf'), { grad: 0.1 }));
  b.box(x + w * 0.12, y + 0.9, z + d * 0.12, w * 0.76, 0.12, d * 0.76, hex('#6e7782'));
  b.prism(x + w * 0.5, y + 1.02, z + d * 0.5, Math.min(w, d) * 0.3, Math.min(w, d) * 0.3, 0.06, 10, hex('#2a313b'));
}

export function waterTank(b: MeshBuilder, x: number, y: number, z: number): void {
  b.prism(x, y, z, 0.9, 0.9, 1.7, 10, hex('#9aa4ae'));
  b.prism(x, y + 1.7, z, 0.9, 0.25, 0.4, 10, hex('#7f8993'));
  for (const [dx, dz] of [[-0.6, -0.6], [0.6, -0.6], [-0.6, 0.6], [0.6, 0.6]] as const) b.box(x + dx - 0.05, y - 0.35, z + dz - 0.05, 0.1, 0.4, 0.1, hex('#3a424b'));
}

/** A row of tilted solar panels: each is a thin sloped slab on a low frame. */
export function solarRow(b: MeshBuilder, x: number, y: number, z: number, count: number, w: number, d: number): void {
  const panel = hex('#1b3b66');
  for (let i = 0; i < count; i++) {
    const px = x + i * (w + 0.35);
    const lo = y + 0.25, hi = y + 0.95;
    b.withMat(MAT.glass, () => b.quad([px, lo, z + d], [px + w, lo, z + d], [px + w, hi, z], [px, hi, z], panel, WINDOW_BASE));
    b.quad([px, hi - 0.04, z], [px + w, hi - 0.04, z], [px + w, hi, z], [px, hi, z], hex('#c9d3dc'));
    b.box(px + 0.1, y, z + d * 0.45, 0.08, 0.5, 0.08, hex('#59636e'));
    b.box(px + w - 0.18, y, z + d * 0.45, 0.08, 0.5, 0.08, hex('#59636e'));
  }
}

export function antenna(b: MeshBuilder, x: number, y: number, z: number, h: number): void {
  b.prism(x, y, z, 0.12, 0.04, h, 4, hex('#8a949f'), { capTop: false });
  b.box(x - 0.1, y + h, z - 0.1, 0.2, 0.2, 0.2, hex('#ff5a5a'), { emissive: 1, shade: false });
}

export function roofGarden(b: MeshBuilder, x: number, y: number, z: number, w: number, d: number, rnd: () => number): void {
  b.box(x, y, z, w, 0.18, d, hex('#7e776a'));
  b.withMat(MAT.grass, () => b.plane(x + 0.15, y + 0.19, z + 0.15, w - 0.3, d - 0.3, hex('#417244')));
  const n = Math.max(2, Math.floor((w * d) / 14));
  for (let i = 0; i < n; i++) bush(b, x + 0.8 + rnd() * (w - 1.6), z + 0.8 + rnd() * (d - 1.6), 0.45 + rnd() * 0.3, rnd);
  for (let i = 0; i < n; i++) b.blob(x + 0.6 + rnd() * (w - 1.2), y + 0.4, z + 0.6 + rnd() * (d - 1.2), 0.14, 0.12, 0.14, rnd() < 0.5 ? hex('#f2c14e') : hex('#f4f0e6'), 5);
}

// ---------------------------------------------------------------- water, court, canal, bridges

export function pool(b: MeshBuilder, cx: number, cz: number, r: number, rnd: () => number): void {
  b.withMat(MAT.tile, () => {
    b.disc(cx, 0.015, cz, r + 1.1, 22, scale(STONE, 0.92));
    b.disc(cx, 0.1, cz, r + 0.55, 22, scale(STONE, 1.05));
  });
  b.withMat(MAT.water, () => {
    b.disc(cx, 0.16, cz, r, 22, WATER, 0.05);
    b.disc(cx, 0.17, cz, r * 0.55, 16, mix(WATER, hex('#7fd8ff'), 0.35), 0.1);
  });
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2 + rnd();
    b.prism(cx + Math.cos(a) * r * 0.45, 0.16, cz + Math.sin(a) * r * 0.45, 0.1, 0.02, 1.2 + rnd() * 0.9, 5, hex('#cfeaff'), { emissive: 0.92, capTop: false });
  }
  b.prism(cx, 0.16, cz, 0.16, 0.03, 2.6, 6, hex('#e0f2ff'), { emissive: 0.95, capTop: false });
}

/** A tennis court centred at (cx, cz), long side along x (rot 0) or z (rot 1). */
export function tennisCourt(b: MeshBuilder, cx: number, cz: number, rot: 0 | 1): void {
  const L = 23.8, W = 11, margin = 3.2;
  const dx = rot === 0 ? 1 : 0, dz = rot === 0 ? 0 : 1;
  const at = (a: number, w: number): [number, number] => (rot === 0 ? [cx + a, cz + w] : [cx + w, cz + a]);
  b.withMat(MAT.court, () => {
    orientedBox(b, cx, 0, cz, dx, dz, L + margin * 2, W + margin * 2, 0.04, hex('#3b6ea8'));
    orientedBox(b, cx, 0.04, cz, dx, dz, L, W, 0.02, hex('#2f8a62'));
  });
  const line = hex('#f4f4ee');
  const strip = (a0: number, w0: number, a1: number, w1: number) => {
    const p = [at(a0, w0), at(a1, w0), at(a1, w1), at(a0, w1)] as const;
    flatPoly(b, p, 0.07, line);
  };
  const t = 0.07;
  strip(-L / 2, -W / 2, L / 2, -W / 2 + t); strip(-L / 2, W / 2 - t, L / 2, W / 2);
  strip(-L / 2, -W / 2, -L / 2 + t, W / 2); strip(L / 2 - t, -W / 2, L / 2, W / 2);
  strip(-6.4, -W / 2 + 1.4, 6.4, -W / 2 + 1.4 + t); strip(-6.4, W / 2 - 1.4 - t, 6.4, W / 2 - 1.4);
  strip(-6.4, -W / 2 + 1.4, -6.4 + t, W / 2 - 1.4); strip(6.4 - t, -W / 2 + 1.4, 6.4, W / 2 - 1.4);
  strip(-6.4, -t / 2, 6.4, t / 2);
  // Net: two posts, a tape and a dark net.
  const [nx, nz] = at(0, 0);
  for (const w of [-W / 2 - 0.4, W / 2 + 0.4]) { const [px, pz] = at(0, w); b.box(px - 0.05, 0, pz - 0.05, 0.1, 1.1, 0.1, hex('#2a313b')); }
  orientedBox(b, nx, 0.55, nz, dz, dx, W + 0.8, 0.03, 0.55, hex('#20262d'));
  orientedBox(b, nx, 1.05, nz, dz, dx, W + 0.8, 0.05, 0.06, line);
  // Fence: posts every 3 m and two rails.
  const fx = L / 2 + margin - 0.6, fw = W / 2 + margin - 0.6;
  const rail = hex('#2d4a3a');
  for (let a = -fx; a <= fx + 0.01; a += 3) for (const w of [-fw, fw]) { const [px, pz] = at(a, w); b.box(px - 0.04, 0, pz - 0.04, 0.08, 3.0, 0.08, rail); }
  for (let w = -fw + 3; w < fw - 0.01; w += 3) for (const a of [-fx, fx]) { const [px, pz] = at(a, w); b.box(px - 0.04, 0, pz - 0.04, 0.08, 3.0, 0.08, rail); }
  const run = (a: number, w: number, h: number, alongLong: boolean, len: number) => {
    const [px, pz] = at(a, w);
    if (alongLong) orientedBox(b, px, h, pz, dx, dz, len, 0.05, 0.05, rail);
    else orientedBox(b, px, h, pz, dz, dx, len, 0.05, 0.05, rail);
  };
  for (const h of [1.2, 2.9]) {
    run(0, -fw, h, true, fx * 2); run(0, fw, h, true, fx * 2);
    run(-fx, 0, h, false, fw * 2); run(fx, 0, h, false, fw * 2);
  }
}

/** One straight piece of canal between two points, with stone edges. */
export function canalPiece(b: MeshBuilder, x0: number, z0: number, x1: number, z1: number, width: number): void {
  const len = Math.hypot(x1 - x0, z1 - z0);
  if (len < 0.01) return;
  const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2, dx = x1 - x0, dz = z1 - z0;
  b.withMat(MAT.tile, () => orientedBox(b, cx, 0, cz, dx, dz, len + 0.3, width + 1.8, 0.06, scale(STONE, 0.95)));
  b.withMat(MAT.water, () => orientedBox(b, cx, 0.06, cz, dx, dz, len + 0.3, width, 0.04, WATER, 0.05));
}

/** A footbridge over water: a stone deck, side walls and two lamps, along (dx, dz). */
export function bridge(b: MeshBuilder, cx: number, cz: number, dx: number, dz: number, span: number, width: number): void {
  b.withMat(MAT.tile, () => orientedBox(b, cx, 0.1, cz, dx, dz, span, width, 0.35, scale(STONE, 1.05)));
  const l = Math.hypot(dx, dz) || 1, vx = -dz / l, vz = dx / l;
  for (const side of [-1, 1]) {
    const px = cx + vx * side * (width / 2 - 0.12), pz = cz + vz * side * (width / 2 - 0.12);
    b.withMat(MAT.concrete, () => orientedBox(b, px, 0.45, pz, dx, dz, span, 0.24, 0.9, hex('#cfc9ba')));
    for (const end of [-1, 1]) lamp(b, px + (dx / l) * end * (span / 2 - 0.4), pz + (dz / l) * end * (span / 2 - 0.4));
  }
}

// ---------------------------------------------------------------- marks

/** The ANUM mark: a bright triangular A drawn on a wall that faces +z, centred at (cx, cy), `size` metres tall. */
export function anumMark(b: MeshBuilder, cx: number, cy: number, z: number, size: number, color: Rgb = TEAL, emissive = 0.95): void {
  const w = size * 0.95, h = size;
  const x0 = cx - w / 2, x1 = cx + w / 2, y0 = cy - h / 2, y1 = cy + h / 2;
  const leg = w * 0.2;
  // Left and right legs, a crossbar, and the apex.
  b.tri([x0, y0, z], [x0 + leg, y0, z], [cx - leg * 0.1, y1, z], color, emissive);
  b.tri([x0 + leg, y0, z], [cx + leg * 0.1, y1, z], [cx - leg * 0.1, y1, z], color, emissive);
  b.tri([x1 - leg, y0, z], [x1, y0, z], [cx + leg * 0.1, y1, z], color, emissive);
  b.tri([x1 - leg, y0, z], [cx + leg * 0.1, y1, z], [cx - leg * 0.1, y1, z], color, emissive);
  b.quad([cx - w * 0.2, y0 + h * 0.3, z], [cx + w * 0.2, y0 + h * 0.3, z], [cx + w * 0.2, y0 + h * 0.4, z], [cx - w * 0.2, y0 + h * 0.4, z], color, emissive);
}

/** A far skyline of boxes with lit window bands: a backdrop, not data. */
export function skylineTower(b: MeshBuilder, x: number, z: number, w: number, d: number, h: number, rnd: () => number): void {
  const wall = mix(hex('#5c6b7c'), hex('#8794a3'), rnd());
  b.box(x - w / 2, -0.05, z - d / 2, w, h, d, wall, { grad: 0.3 });
  const bands = Math.floor(h / 3.2);
  for (let i = 1; i < bands; i++) {
    if (rnd() < 0.25) continue;
    const y = i * 3.2;
    const tone = mix(WARM, hex('#cfe6ff'), rnd() * 0.6);
    const em = WINDOW_BASE + (rnd() < 0.3 ? 0.3 : 0.6);
    b.withMat(MAT.glass, () => {
      b.quad([x - w / 2 + 0.4, y, z + d / 2 + 0.03], [x + w / 2 - 0.4, y, z + d / 2 + 0.03], [x + w / 2 - 0.4, y + 1.4, z + d / 2 + 0.03], [x - w / 2 + 0.4, y + 1.4, z + d / 2 + 0.03], tone, em);
      b.quad([x + w / 2 + 0.03, y, z - d / 2 + 0.4], [x + w / 2 + 0.03, y, z + d / 2 - 0.4], [x + w / 2 + 0.03, y + 1.4, z + d / 2 - 0.4], [x + w / 2 + 0.03, y + 1.4, z - d / 2 + 0.4], tone, em);
    });
  }
}

export type { MatId };
