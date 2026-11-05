/**
 * Tyre workshop: a single-storey service building with three glass-fronted service bays (lit work
 * interiors), a showroom and counter at one end, a deep accent fascia carrying the sign board and
 * a generic wheel emblem, outdoor tyre stacks and a rack, an air-hose post and a pylon.
 */
import { MAT, hex, scale } from '../../lib/hqGeometry.ts';
import { WARM, WINDOW_BASE, acUnit, castShadow } from '../../lib/hqProps.ts';
import { clamp, entrance, faceBox, faceDisc, faceQuad, finish, makeCtx, paneRow, rectFaces, signBoard } from './parts.ts';
import { beaconLight } from './facadeKit.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';
import { cornerBevels } from './bevel.ts';

const RUBBER = hex('#1b1f24');

export function buildTyreWorkshop(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('tyreWorkshop', params, 0.6);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 18, 40, 28), D = clamp(params.depth, 12, 26, 17);
  const H = 6.2;
  const x0 = -W / 2, z0 = -D / 2, x1 = W / 2, z1 = D / 2;
  const F = rectFaces(x0, z0, W, D);

  b.plane(x0 - 3, 0.01, z0 - 3, W + 6, D + 16, scale(scheme.stone, 0.6));
  castShadow(b, x0, z0, W, D, H + 1, 0.022);
  // Walls: a stone base and light upper cladding.
  b.box(x0, 0, z0, W, H, D, scale(scheme.wall, 0.96), { skip: ['top', 'bottom'], grad: 0.15 });
  cornerBevels(b, x0, z0, W, D, 0, H, scheme.trim);
  for (const f of Object.values(F)) {
    faceQuad(b, f, 0, f.len, 0, 1.2, scale(scheme.stone, 0.7), 0, 0.05);
    faceQuad(b, f, 0, f.len, 1.2, 1.32, scheme.trim, 0, 0.05);
  }
  // Roof slab with a parapet and a few units.
  b.box(x0 - 0.5, H, z0 - 0.5, W + 1, 0.4, D + 1, scheme.roof);
  b.box(x0 - 0.5, H + 0.4, z0 - 0.5, W + 1, 0.5, 0.25, scheme.trim);
  b.box(x0 - 0.5, H + 0.4, z1 + 0.25, W + 1, 0.5, 0.25, scheme.trim);
  b.box(x0 - 0.5, H + 0.4, z0 - 0.25, 0.25, 0.5, D + 0.5, scheme.trim);
  b.box(x1 + 0.25, H + 0.4, z0 - 0.25, 0.25, 0.5, D + 0.5, scheme.trim);
  acUnit(b, x1 - 7, H + 0.4, z0 + 3, 3, 2);
  beaconLight(ctx, x0 + 2, H + 0.4, z0 + 2);
  if (!ctx.lean) acUnit(b, x1 - 11, H + 0.4, z0 + 3.5, 2.4, 1.8);
  // Roof-edge sign tower stub is left to the sign board; a flue stack at the back.
  b.prism(x0 + 4, H + 0.4, z0 + 3, 0.35, 0.3, 2.2, 6, hex('#8a949f'));

  // Showroom at the west end (glass, entrance), service bays after it.
  const showW = Math.min(8.5, W * 0.3);
  paneRow(ctx, F.south, 0.5, showW - 0.5, 0.5, 3.2, 3, 0.12, 0.8);
  b.box(x0 + 0.5, 0.5, z1 - 0.9, showW - 1, 0.9, 0.4, hex('#7a5a3c'));
  entrance(ctx, F.south, showW / 2, 0, 2.4, 2.7, 1.8, scheme.accent);
  // Service bays: dark frame, a glass roller door whose lit interior shows at night.
  const bays = Math.max(2, Math.min(4, Math.floor((W - showW - 1) / 6)));
  const bayW = 4.3, span = (W - showW - 1) / bays;
  for (let i = 0; i < bays; i++) {
    const c = showW + 0.5 + span * (i + 0.5);
    faceBox(b, F.south, c - bayW / 2 - 0.25, c + bayW / 2 + 0.25, 0, 3.9, 0, 0.14, hex('#2a313b'));
    faceQuad(b, F.south, c - bayW / 2, c + bayW / 2, 0.15, 3.65, hex('#2b3541'), WINDOW_BASE + 0, 0.15);
    // The roller door is rolled up two thirds: slats above, a lit bay with a hoist frame below.
    b.withMat(MAT.roller, () => faceQuad(b, F.south, c - bayW / 2, c + bayW / 2, 2.45, 3.65, hex('#cfd5da'), 0, 0.2));
    faceBox(b, F.south, c - 1.7, c - 1.5, 0.15, 2.3, -0.9, -0.7, hex('#59636e'));
    faceBox(b, F.south, c + 1.5, c + 1.7, 0.15, 2.3, -0.9, -0.7, hex('#59636e'));
    // Lit interior glow: a warm panel behind two slat lines, brighter in some bays.
    ctx.windows.total += 1;
    const on = rnd() < Math.max(ctx.lit, 0.34);
    if (on) ctx.windows.lit += 1;
    faceQuad(b, F.south, c - bayW / 2 + 0.2, c + bayW / 2 - 0.2, 0.3, 2.0, on ? WARM : hex('#46515e'), WINDOW_BASE + (on ? 0.75 : 0), 0.17);
    faceQuad(b, F.south, c - bayW / 2, c + bayW / 2, 2.1, 2.2, hex('#171c22'), 0, 0.18);
    faceQuad(b, F.south, c - bayW / 2, c + bayW / 2, 2.9, 3.0, hex('#171c22'), 0, 0.18);
    faceQuad(b, F.south, c - 0.2, c + 0.2, 3.95, 4.1, WARM, 1.0, 0.16);
  }
  // Side and back windows.
  paneRow(ctx, F.east, 2, F.east.len - 2, 1.8, 3.6, Math.max(3, Math.floor(F.east.len / 3.4)), 0.2, 0.7);
  paneRow(ctx, F.west, 2, F.west.len - 2, 1.8, 3.6, Math.max(3, Math.floor(F.west.len / 3.4)), 0.2, 0.7);
  paneRow(ctx, F.north, 2, F.north.len - 2, 2.2, 3.8, Math.max(4, Math.floor(F.north.len / 3.6)), 0.2, 0.7);

  // Deep accent fascia over the whole front, carrying the sign and the wheel emblem.
  const fy = 4.4;
  faceBox(b, F.south, -0.4, W + 0.4, fy, H - 0.1, 0, 0.6, scheme.accent, { emissive: 0.2 });
  faceBox(b, F.south, -0.4, W + 0.4, fy - 0.12, fy, 0, 0.7, scale(scheme.accent, 0.6));
  const signW = Math.min(14, W - showW - 5);
  const sign = signBoard(ctx, F.south, showW + 3 + signW / 2, fy + 0.1, signW, 1.45, 0.62);
  const ex = showW + 1.5;
  faceDisc(b, F.south, ex, fy + 0.85, 0.72, 14, RUBBER, 0, 0.66);
  faceDisc(b, F.south, ex, fy + 0.85, 0.42, 14, hex('#c9d0d6'), 0.15, 0.68);
  faceDisc(b, F.south, ex, fy + 0.85, 0.12, 8, RUBBER, 0, 0.7);

  if (!ctx.lean) {
    // Tyre stacks beside the bays, a tyre rack on the east wall, an air-hose post and a pylon.
    for (const [sx, n] of [[x1 + 1.6, 6], [x1 + 1.6, 4]] as const) {
      const sz = n === 6 ? z1 - 6 : z1 - 9.2;
      for (let i = 0; i < n; i++) b.prism(sx, i * 0.26, sz, 0.62, 0.62, 0.26, 10, i % 2 ? RUBBER : scale(RUBBER, 1.5));
    }
    b.box(x1 + 0.4, 0, z0 + 2, 0.12, 2.6, 6, hex('#59636e'));
    b.box(x1 + 0.4, 1.3, z0 + 2, 1.1, 0.1, 6, hex('#59636e'));
    for (let i = 0; i < 6; i++) { b.prism(x1 + 1.0, 0.35 + (i % 2) * 1.4, z0 + 2.6 + i * 0.95, 0.55, 0.55, 0.26, 10, RUBBER); }
    b.prism(x0 + showW + 2, 0, z1 + 2.2, 0.1, 0.08, 1.4, 5, hex('#2a313b'), { capTop: false });
    b.box(x0 + showW + 1.8, 1.4, z1 + 2.1, 0.4, 0.4, 0.3, scheme.accent, { emissive: 0.4 });
    // Pylon.
    b.box(x1 + 3.4, 0, z1 + 3, 0.7, 5.2, 0.7, scheme.trim, { grad: 0.2 });
    b.box(x1 + 2.7, 5.2, z1 + 2.8, 2.1, 1.7, 0.5, hex('#0e1b26'));
    b.box(x1 + 2.7, 5.2, z1 + 3.3, 2.1, 0.07, 0.04, scheme.accent, { emissive: 0.9, shade: false });
    b.prism(x1 + 3.75, 6.9, z1 + 3.34, 0.55, 0.55, 0.05, 12, RUBBER);
    b.prism(x1 + 3.75, 6.95, z1 + 3.34, 0.28, 0.28, 0.04, 12, hex('#c9d0d6'), { emissive: 0.15 });
  }
  return finish(ctx, {
    kind: 'tyreWorkshop', footprint: { width: W, depth: D }, height: H + 0.9,
    anchor: { x: 0, y: H + 3.4, z: 0 },
    door: { x: x0 + showW / 2, y: 0, z: z1 + 8 },
    sign, ground: [x0 - 3, z0 - 3, x1 + 6, z1 + 13],
  }, params);
}
