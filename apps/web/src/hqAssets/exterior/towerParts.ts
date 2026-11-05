/** Parts shared by the glass tower and the executive HQ: crown, accent band, roof terrace and flag pole. */
import { hex, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { acUnit, antenna, planter } from '../../lib/hqProps.ts';
import { faceQuad, paneRow, rectFaces, type Ctx } from './parts.ts';
import { beaconLight, pennant } from './facadeKit.ts';
import { bevelBand } from './bevel.ts';

/** A lit accent ring around a block at height y (four thin boxes in one, skip top and bottom). */
export function accentBand(ctx: Ctx, x: number, z: number, w: number, d: number, y: number, h = 0.28, emissive = 0.8): void {
  ctx.b.box(x - 0.22, y, z - 0.22, w + 0.44, h, d + 0.44, ctx.scheme.accent, { emissive, shade: false, skip: ['top', 'bottom'] });
}

/** Stone band (a floor slab edge, a roof line) around a block, its top edge and corners bevelled (`bevel.ts`). */
export function slabBand(ctx: Ctx, x: number, z: number, w: number, d: number, y: number, h = 0.5): void {
  bevelBand(ctx.b, x - 0.3, z - 0.3, w + 0.6, d + 0.6, y, h, ctx.scheme.wall);
}

/**
 * Stepped crown: a louvred plant room, a glass lantern that glows at night, an accent cap, a
 * spire and a red aviation light. `w` and `d` are the shaft top; returns the top of the lantern cap.
 */
export function crown(ctx: Ctx, cx: number, cz: number, w: number, d: number, y: number, grand = false): number {
  const { b, scheme } = ctx;
  slabBand(ctx, cx - w / 2, cz - d / 2, w, d, y, 0.5);
  accentBand(ctx, cx - w / 2, cz - d / 2, w, d, y + 0.5, 0.3, 0.9);
  const y1 = y + 0.8;
  // Plant room with louvres.
  const pw = w * 0.74, pd = d * 0.74, ph = grand ? 4.0 : 3.2;
  const px = cx - pw / 2, pz = cz - pd / 2;
  b.box(px, y1, pz, pw, ph, pd, scale(scheme.wall, 0.92), { skip: ['bottom'], grad: 0.12 });
  const F = rectFaces(px, pz, pw, pd);
  for (const f of Object.values(F)) {
    for (let i = 0; i < 5; i++) faceQuad(b, f, 0.8, f.len - 0.8, y1 + 0.5 + i * ((ph - 0.9) / 5), y1 + 0.5 + i * ((ph - 0.9) / 5) + 0.2, hex('#37424e'), 0, 0.03);
  }
  // Glass lantern.
  const y2 = y1 + ph;
  const lw = pw * 0.56, ld = pd * 0.56, lh = grand ? 3.4 : 2.6;
  const lx = cx - lw / 2, lz = cz - ld / 2;
  b.box(lx, y2, lz, lw, lh, ld, scheme.core, { skip: ['bottom', 'top'], grad: 0.3 });
  const LF = rectFaces(lx, lz, lw, ld);
  for (const f of Object.values(LF)) paneRow(ctx, f, 0.4, f.len - 0.4, y2 + 0.3, y2 + lh - 0.3, Math.max(2, Math.floor(f.len / 1.6)), 0.12, 1);
  b.box(lx - 0.35, y2 + lh, lz - 0.35, lw + 0.7, 0.35, ld + 0.7, scheme.accent, { emissive: 0.7, shade: false });
  const top = y2 + lh + 0.35;
  if (grand) {
    // The crown of the headquarters: two raked blades that meet at the apex like the ANUM mark, joined by a lit crossbar, and a beacon at the tip.
    const mh = 9.5, half = Math.min(2.6, lw * 0.5);
    for (const sgn of [-1, 1]) {
      const bx = cx + sgn * half;
      b.quad([bx - 0.14, top, cz - 0.07], [bx + 0.14, top, cz - 0.07], [cx + sgn * 0.12 + 0.1, top + mh, cz - 0.07], [cx + sgn * 0.12 - 0.1, top + mh, cz - 0.07], scale(scheme.accent, 0.95), 0.85);
      b.quad([bx + 0.14, top, cz + 0.07], [bx - 0.14, top, cz + 0.07], [cx + sgn * 0.12 - 0.1, top + mh, cz + 0.07], [cx + sgn * 0.12 + 0.1, top + mh, cz + 0.07], scale(scheme.accent, 0.8), 0.85);
    }
    b.quad([cx - half * 0.55, top + mh * 0.34, cz - 0.07], [cx + half * 0.55, top + mh * 0.34, cz - 0.07], [cx + half * 0.55, top + mh * 0.34 + 0.2, cz - 0.07], [cx - half * 0.55, top + mh * 0.34 + 0.2, cz - 0.07], hex('#bff7ea'), 0.95);
    beaconLight(ctx, cx, top + mh - 0.2, cz);
  } else {
    b.prism(cx, top, cz, 0.2, 0.05, 6, 5, hex('#9aa5b1'), { capTop: false });
    beaconLight(ctx, cx, top + 5.6, cz);
  }
  return top;
}

/** A terrace on a setback: paving, a parapet, planters and a few plant units. */
export function terrace(ctx: Ctx, x: number, z: number, w: number, d: number, y: number, keepClear: { x: number; z: number; w: number; d: number }): void {
  const { b, rnd, scheme } = ctx;
  b.box(x, y, z, w, 0.25, d, scheme.roof);
  b.plane(x + 0.3, y + 0.26, z + 0.3, w - 0.6, d - 0.6, scale(scheme.stone, 0.85));
  const ring = (rx: number, rz: number, rw: number, rd: number) => b.box(rx, y + 0.25, rz, rw, 0.7, rd, scheme.trim);
  ring(x, z, w, 0.25); ring(x, z + d - 0.25, w, 0.25); ring(x, z, 0.25, d); ring(x + w - 0.25, z, 0.25, d);
  if (!ctx.lean) {
    for (let i = 0; i < Math.max(2, Math.floor(w / 5)); i++) {
      const px = x + 1.6 + i * ((w - 3.2) / Math.max(1, Math.floor(w / 5) - 1));
      const pz = z + d - 1.4;
      if (px > keepClear.x - 1 && px < keepClear.x + keepClear.w + 1 && pz > keepClear.z - 1 && pz < keepClear.z + keepClear.d + 1) continue;
      planter(b, px, pz, 1.1, rnd);
    }
    acUnit(b, x + 1, y + 0.25, z + 1, 2.4, 1.6);
    antenna(b, x + w - 1.2, y + 0.25, z + 1.2, 3 + rnd() * 1.5);
  }
}

/** A flag pole with a plain accent pennant (no flag artwork) that ripples in the wind. */
export function flagPole(ctx: Ctx, x: number, z: number, h: number, color?: Rgb): void {
  pennant(ctx, x, z, h, color);
}
