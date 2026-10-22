/**
 * Reusable props for the headquarters scenes (campus and building): trees, vehicles, lamps,
 * rooftop plant, water, baked contact shadows and the ANUM mark. Everything here is decoration
 * generated from fixed seeds: no data, names or counts are encoded in geometry.
 *
 * Window convention: an emissive weight of 2 or more marks a window. The renderer shows it as
 * blue reflective glass by day and as a warm lit pane at night (weight minus two is the glow).
 */
import { MeshBuilder, hex, mix, scale, type Rgb } from './hqGeometry.ts';

export const WINDOW_BASE = 2;
export const GROUND = hex('#4a7a48');
export const STONE = hex('#b9b4a6');
export const ASPHALT = hex('#3a3f46');
export const WATER = hex('#2c6f99');
export const WARM = hex('#ffcf7a');
export const TEAL = hex('#45d0b5');
export const SHADOW = hex('#1f3524');
export const GLASS_DARK = hex('#1c2c3b');

/** Light travels towards -x and -z (see `lightingFor`): shadows fall that way. */
export const SHADOW_DX = -0.56;
export const SHADOW_DZ = -0.48;

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

/** Ground shadow of an upright box of height `h`: the footprint swept along the shadow direction. */
export function castShadow(b: MeshBuilder, x: number, z: number, w: number, d: number, h: number, y = 0.02, color: Rgb = SHADOW): void {
  const vx = SHADOW_DX * h, vz = SHADOW_DZ * h;
  flatPoly(b, [[x + vx, z + vz], [x + w + vx, z + vz], [x + w + vx, z + d + vz], [x + vx, z + d + vz]], y, color);
  flatPoly(b, [[x, z], [x + w, z], [x + w + vx, z + vz], [x + vx, z + vz]], y, color);
  flatPoly(b, [[x, z], [x, z + d], [x + vx, z + d + vz], [x + vx, z + vz]], y, color);
}

export function blobShadow(b: MeshBuilder, cx: number, cz: number, radius: number, height: number, y = 0.02, color: Rgb = SHADOW): void {
  b.disc(cx + SHADOW_DX * height * 0.6, y, cz + SHADOW_DZ * height * 0.6, radius, 10, color);
}

// ---------------------------------------------------------------- planting

export function palm(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  blobShadow(b, x, z, h * 0.3, h * 0.9, 0.024);
  const lean = (rnd() - 0.5) * 0.6;
  b.prism(x, 0, z, 0.22, 0.14, h * 0.55, 5, hex('#6b5238'), { capTop: false });
  b.prism(x + lean, h * 0.55, z, 0.14, 0.1, h * 0.45, 5, hex('#6b5238'), { capTop: false });
  const topY = h;
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + rnd() * 0.3;
    const len = 1.9 + rnd() * 0.5;
    const cx = x + lean, cz = z;
    const tipX = cx + Math.cos(a) * len, tipZ = cz + Math.sin(a) * len;
    const sx = Math.sin(a) * 0.45, sz = -Math.cos(a) * 0.45;
    const leaf = scale(hex('#2f7d46'), 0.8 + rnd() * 0.3);
    b.tri([cx - sx, topY, cz - sz], [cx + sx, topY, cz + sz], [tipX, topY - 0.9, tipZ], leaf);
    b.tri([cx + sx, topY, cz + sz], [cx - sx, topY, cz - sz], [tipX, topY - 0.9, tipZ], scale(leaf, 0.72));
  }
  b.blob(x + lean, topY - 0.1, z, 0.34, 0.3, 0.34, hex('#4f8f46'), 5);
}

export function roundTree(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  blobShadow(b, x, z, h * 0.42, h, 0.024);
  b.prism(x, 0, z, 0.25, 0.2, h * 0.45, 5, hex('#5d4630'), { capTop: false });
  const leaf = mix(hex('#2f6a43'), hex('#4f8f52'), rnd());
  b.blob(x, h * 0.62, z, h * 0.34, h * 0.3, h * 0.34, leaf, 7);
  b.blob(x + h * 0.12, h * 0.78, z - h * 0.06, h * 0.22, h * 0.2, h * 0.22, scale(leaf, 1.12), 6);
  b.blob(x - h * 0.14, h * 0.7, z + h * 0.08, h * 0.2, h * 0.18, h * 0.2, scale(leaf, 0.92), 6);
}

export function cypress(b: MeshBuilder, x: number, z: number, h: number, rnd: () => number): void {
  blobShadow(b, x, z, h * 0.16, h, 0.024);
  b.prism(x, 0, z, 0.16, 0.12, h * 0.15, 5, hex('#5d4630'), { capTop: false });
  b.prism(x, h * 0.1, z, h * 0.17, h * 0.1, h * 0.55, 7, mix(hex('#245437'), hex('#3a7a48'), rnd()));
  b.prism(x, h * 0.55, z, h * 0.11, 0, h * 0.45, 7, mix(hex('#2a6240'), hex('#46894f'), rnd()));
}

export function bush(b: MeshBuilder, x: number, z: number, r: number, rnd: () => number): void {
  const leaf = mix(hex('#2b6a40'), hex('#5a9a52'), rnd());
  b.blob(x, r * 0.55, z, r, r * 0.65, r, leaf, 6);
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
  b.box(x - w / 2, 0.38, z - d / 2, w, 0.07, d, hex('#9a7a52'));
  b.box(x - w / 2 + 0.1, 0, z - d / 2 + 0.05, 0.08, 0.38, d - 0.1, hex('#2a313b'));
  b.box(x + w / 2 - 0.18, 0, z - d / 2 + 0.05, 0.08, 0.38, d - 0.1, hex('#2a313b'));
}

export function lamp(b: MeshBuilder, x: number, z: number, glow = 2.6): void {
  b.disc(x, 0.045, z, glow, 12, mix(GROUND, WARM, 0.1), 0.22);
  b.prism(x, 0, z, 0.1, 0.08, 2.6, 5, hex('#2a313b'), { capTop: false });
  b.box(x - 0.22, 2.6, z - 0.22, 0.44, 0.3, 0.44, WARM, { emissive: 1, shade: false });
}

// ---------------------------------------------------------------- vehicles

/** Rotates a local box (a, b, c, d in a 0..3 quarter-turn frame) onto the ground plane. */
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

/** A parked car. Local frame: the nose points to +z for rot 0 (0..3 quarter turns). */
export function car(b: MeshBuilder, x: number, z: number, color: Rgb, rot = 0): void {
  const g = GLASS_DARK;
  const [bw, bl] = rot % 2 === 0 ? [1.9, 4.2] : [4.2, 1.9];
  castShadow(b, x - bw / 2, z - bl / 2, bw, bl, 1.5, 0.026);
  lbox(b, x, z, rot, -0.9, 0.28, -2.1, 1.8, 0.62, 4.2, color, { grad: 0.2 });
  lbox(b, x, z, rot, -0.78, 0.9, -1.0, 1.56, 0.5, 2.2, mix(color, hex('#9fd4e6'), 0.5));
  lbox(b, x, z, rot, -0.8, 0.9, -1.04, 1.6, 0.46, 0.08, g, { shade: false });
  lbox(b, x, z, rot, -0.72, 1.4, -0.9, 1.44, 0.06, 2.0, scale(color, 0.9));
  lbox(b, x, z, rot, -0.74, 0.5, 2.08, 0.46, 0.2, 0.06, WARM, { emissive: 1, shade: false });
  lbox(b, x, z, rot, 0.28, 0.5, 2.08, 0.46, 0.2, 0.06, WARM, { emissive: 1, shade: false });
  lbox(b, x, z, rot, -0.74, 0.55, -2.14, 0.4, 0.16, 0.06, hex('#ff3b3b'), { emissive: 0.9, shade: false });
  lbox(b, x, z, rot, 0.34, 0.55, -2.14, 0.4, 0.16, 0.06, hex('#ff3b3b'), { emissive: 0.9, shade: false });
  for (const wz of [-1.35, 1.2]) for (const sx of [-0.98, 0.78]) lbox(b, x, z, rot, sx, 0, wz, 0.2, 0.6, 0.62, hex('#161b21'));
}

/** A box truck. Local frame as `car`. */
export function truck(b: MeshBuilder, x: number, z: number, cab: Rgb, rot = 0): void {
  const [bw, bl] = rot % 2 === 0 ? [2.5, 9.4] : [9.4, 2.5];
  castShadow(b, x - bw / 2, z - bl / 2, bw, bl, 3.4, 0.026);
  lbox(b, x, z, rot, -1.2, 0.6, -4.7, 2.4, 3.0, 6.6, hex('#e3e7ec'), { grad: 0.16 });
  lbox(b, x, z, rot, -1.22, 1.3, -4.7, 2.44, 0.3, 6.6, TEAL, { emissive: 0.3 });
  lbox(b, x, z, rot, -1.15, 0.6, 2.0, 2.3, 2.2, 2.4, cab, { grad: 0.2 });
  lbox(b, x, z, rot, -0.95, 1.9, 4.3, 1.9, 0.8, 0.08, hex('#7fb9d6'), { shade: false });
  lbox(b, x, z, rot, -0.9, 0.8, 4.4, 0.5, 0.25, 0.08, WARM, { emissive: 1, shade: false });
  lbox(b, x, z, rot, 0.4, 0.8, 4.4, 0.5, 0.25, 0.08, WARM, { emissive: 1, shade: false });
  lbox(b, x, z, rot, -1.2, 0.2, -4.6, 2.4, 0.4, 9.0, hex('#2a313b'));
  for (const wz of [-3.4, -1.6, 3.2]) for (const sx of [-1.28, 1.08]) lbox(b, x, z, rot, sx, 0, wz, 0.22, 0.95, 0.95, hex('#161b21'));
}

export function parkingLot(b: MeshBuilder, x: number, z: number, cols: number, rows: number, rnd: () => number, fill = 0.7): void {
  const colors = [hex('#c9d3dc'), hex('#2f4b66'), hex('#8a2f3a'), hex('#d9b25f'), hex('#3d6b52'), hex('#e8ebee'), hex('#1f2429'), hex('#7a8794')];
  const w = cols * 2.9 + 1, d = rows * 5.6 + 1.6;
  b.plane(x - 0.5, 0.012, z - 0.8, w, d, scale(ASPHALT, 1.05));
  for (let c = 0; c <= cols; c++) b.plane(x - 0.5 + c * 2.9 + 0.4, 0.018, z - 0.4, 0.1, d - 0.8, hex('#d8d8d0'));
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (rnd() > fill) continue;
      car(b, x + c * 2.9 + 1.0, z + r * 5.6 + 2.2, colors[Math.floor(rnd() * colors.length)], rnd() < 0.5 ? 0 : 2);
    }
  }
}

// ---------------------------------------------------------------- roofs

export function acUnit(b: MeshBuilder, x: number, y: number, z: number, w: number, d: number): void {
  b.box(x, y, z, w, 0.9, d, hex('#aeb6bf'), { grad: 0.1 });
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
    b.quad([px, lo, z + d], [px + w, lo, z + d], [px + w, hi, z], [px, hi, z], panel, 0.06);
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
  b.plane(x + 0.15, y + 0.19, z + 0.15, w - 0.3, d - 0.3, hex('#417244'));
  const n = Math.max(2, Math.floor((w * d) / 14));
  for (let i = 0; i < n; i++) bush(b, x + 0.8 + rnd() * (w - 1.6), z + 0.8 + rnd() * (d - 1.6), 0.45 + rnd() * 0.3, rnd);
  for (let i = 0; i < n; i++) b.blob(x + 0.6 + rnd() * (w - 1.2), y + 0.4, z + 0.6 + rnd() * (d - 1.2), 0.14, 0.12, 0.14, rnd() < 0.5 ? hex('#f2c14e') : hex('#f4f0e6'), 5);
}

// ---------------------------------------------------------------- water

export function pool(b: MeshBuilder, cx: number, cz: number, r: number, rnd: () => number): void {
  b.disc(cx, 0.015, cz, r + 1.1, 20, scale(STONE, 0.92));
  b.disc(cx, 0.1, cz, r + 0.55, 20, scale(STONE, 1.05));
  b.disc(cx, 0.16, cz, r, 20, WATER, 0.1);
  b.disc(cx, 0.17, cz, r * 0.55, 16, mix(WATER, hex('#7fd8ff'), 0.35), 0.18);
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2 + rnd();
    b.prism(cx + Math.cos(a) * r * 0.45, 0.16, cz + Math.sin(a) * r * 0.45, 0.1, 0.02, 1.2 + rnd() * 0.9, 4, hex('#bfe6ff'), { emissive: 0.5, capTop: false });
  }
  b.prism(cx, 0.16, cz, 0.16, 0.03, 2.6, 5, hex('#cdeeff'), { emissive: 0.6, capTop: false });
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
    b.quad([x - w / 2 + 0.4, y, z + d / 2 + 0.03], [x + w / 2 - 0.4, y, z + d / 2 + 0.03], [x + w / 2 - 0.4, y + 1.4, z + d / 2 + 0.03], [x - w / 2 + 0.4, y + 1.4, z + d / 2 + 0.03], tone, em);
    b.quad([x + w / 2 + 0.03, y, z - d / 2 + 0.4], [x + w / 2 + 0.03, y, z + d / 2 - 0.4], [x + w / 2 + 0.03, y + 1.4, z + d / 2 - 0.4], [x + w / 2 + 0.03, y + 1.4, z - d / 2 + 0.4], tone, em);
  }
}
