/**
 * Retail / commerce block: a two-storey building with a row of shop units (lit display windows,
 * coloured awnings, blank fascia boards), a double-height glass entrance portal in the middle with
 * the sign board, an upper storey with window bands and a louvred screen, a lit parapet and roof
 * plant, a pylon, trolley bay and planting. Awnings carry no text.
 */
import { hex, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { WARM, WINDOW_BASE, acUnit, bench, castShadow, planter } from '../../lib/hqProps.ts';
import { clamp, curtainWall, entrance, faceBox, faceQuad, finish, makeCtx, paneRow, rectFaces, signBoard } from './parts.ts';
import { accentBand, slabBand } from './towerParts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

const AWNINGS: readonly Rgb[] = [hex('#c0453a'), hex('#2f6a8f'), hex('#d9b25f'), hex('#3d6b52'), hex('#7a4a8a'), hex('#e0803a')];

export function buildRetailBlock(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('retailBlock', params, 0.62);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 22, 60, 36), D = clamp(params.depth, 14, 34, 22);
  const GH = 4.6, UH = 3.8, y0 = 0.4;
  const H = GH + UH;
  const x0 = -W / 2, z0 = -D / 2, x1 = W / 2, z1 = D / 2;
  const F = rectFaces(x0, z0, W, D);
  const pw = Math.min(9, W * 0.26), pd = 3.0;
  const px0 = -pw / 2;

  b.plane(x0 - 3, 0.01, z0 - 3, W + 6, D + 18, scale(scheme.stone, 0.62));
  castShadow(b, x0, z0, W, D, H + 1.5, 0.022);
  b.box(x0 - 0.4, 0, z0 - 0.4, W + 0.8, y0, D + 0.8, scheme.stone);
  b.box(x0, y0, z0, W, H, D, scale(scheme.wall, 1.0), { skip: ['top', 'bottom'], grad: 0.12 });
  for (const f of Object.values(F)) faceQuad(b, f, 0, f.len, y0, y0 + 0.9, scale(scheme.stone, 0.7), 0, 0.05);

  // Shop units either side of the portal.
  const gapFrom = W / 2 + px0 - 0.8, gapTo = W / 2 + px0 + pw + 0.8;
  const side = (from: number, to: number, count: number) => {
    const span = (to - from) / count;
    for (let i = 0; i < count; i++) {
      const a = from + i * span, c = a + span;
      const awning = AWNINGS[Math.floor(rnd() * AWNINGS.length)];
      // Display window: lit warm at night (display lighting stays on in most units), dark glass otherwise.
      ctx.windows.total += 1;
      const lit = rnd() < Math.max(ctx.lit, 0.4);
      if (lit) ctx.windows.lit += 1;
      faceQuad(b, F.south, a + 0.5, c - 0.5, y0 + 0.9, y0 + 3.2, lit ? scale(WARM, 0.95) : hex('#9fb4c8'), WINDOW_BASE + (lit ? 0.6 + rnd() * 0.3 : 0), 0.06);
      faceQuad(b, F.south, a + 0.5, c - 0.5, y0 + 2.4, y0 + 2.46, hex('#2a313b'), 0, 0.08);
      // Door on one side of the window.
      faceQuad(b, F.south, c - 1.6, c - 0.6, y0 + 0.1, y0 + 2.5, hex('#2a313b'), 0, 0.09);
      // Awning: a sloped quad and a valance, then a blank lit fascia board above.
      b.quad([x0 + a + 0.2, y0 + 3.6, z1 + 0.02], [x0 + a + 0.2, y0 + 3.1, z1 + 1.6], [x0 + c - 0.2, y0 + 3.1, z1 + 1.6], [x0 + c - 0.2, y0 + 3.6, z1 + 0.02], awning);
      b.box(x0 + a + 0.2, y0 + 2.85, z1 + 1.6, span - 0.4, 0.3, 0.06, scale(awning, 0.8));
      faceBox(b, F.south, a + 0.6, c - 0.6, y0 + 3.75, y0 + 4.35, 0, 0.14, hex('#0e1b26'));
      faceBox(b, F.south, a + 0.6, c - 0.6, y0 + 3.75, y0 + 3.82, 0.14, 0.18, awning, { emissive: 0.7, shade: false });
      // Pilaster.
      faceBox(b, F.south, a - 0.18, a + 0.18, y0, y0 + GH, 0, 0.3, scheme.trim);
    }
  };
  const leftN = Math.max(1, Math.floor((gapFrom - 0.5) / 6.2));
  const rightN = Math.max(1, Math.floor((W - gapTo - 0.5) / 6.2));
  side(0.5, gapFrom, leftN);
  side(gapTo, W - 0.5, rightN);
  // Floor slab and upper storey window bands (front, sides, back).
  slabBand(ctx, x0, z0, W, D, y0 + GH - 0.25, 0.5);
  const uy = y0 + GH + 0.5;
  paneRow(ctx, F.south, 0.8, gapFrom, uy + 0.5, uy + UH - 0.8, Math.max(2, Math.floor((gapFrom - 0.8) / 2.4)), 0.14, 1);
  paneRow(ctx, F.south, gapTo, W - 0.8, uy + 0.5, uy + UH - 0.8, Math.max(2, Math.floor((W - gapTo - 0.8) / 2.4)), 0.14, 1);
  paneRow(ctx, F.north, 0.8, W - 0.8, uy + 0.5, uy + UH - 0.8, Math.floor((W - 1.6) / 2.4), 0.14, 1);
  for (const f of [F.east, F.west]) paneRow(ctx, f, 1, f.len - 1, uy + 0.5, uy + UH - 0.8, Math.floor((f.len - 2) / 2.4), 0.14, 1);
  // Ground floor on the sides and back: a few shop windows and a service door at the back.
  for (const f of [F.east, F.west]) paneRow(ctx, f, 2, f.len - 2, y0 + 1.1, y0 + 3.0, Math.max(2, Math.floor((f.len - 4) / 4)), 0.3, 0.8);
  paneRow(ctx, F.north, 2, 12, y0 + 1.1, y0 + 3.0, 3, 0.3, 0.8);
  faceQuad(b, F.north, W - 8, W - 4, y0 + 0.1, y0 + 3.6, hex('#8a949c'), 0, 0.08);
  faceBox(b, F.north, W - 8.3, W - 3.7, y0, y0 + 3.8, 0, 0.1, hex('#e8c34a'));
  // Entrance portal: a glass box two storeys high, proud of the front, with the sign board over the door.
  const PH = H + 0.9;
  curtainWall(ctx, px0, z1, pw, pd, y0, 1, PH, { colW: 1.6, inset: 0.4, skipFaces: ['north'] });
  b.box(px0 - 0.3, y0 + PH, z1 - 0.3, pw + 0.6, 0.5, pd + 0.6, scheme.wall);
  accentBand(ctx, px0, z1, pw, pd, y0 + PH + 0.5, 0.26, 0.9);
  const PF = rectFaces(px0, z1, pw, pd);
  entrance(ctx, PF.south, pw / 2, y0, 4.2, 3.4, 2.4, scheme.accent);
  const sign = signBoard(ctx, PF.south, pw / 2, y0 + 5.0, Math.min(pw - 1, 7.5), 1.6, 0.05);

  // Roof.
  slabBand(ctx, x0, z0, W, D, y0 + H, 0.55);
  accentBand(ctx, x0, z0, W, D, y0 + H + 0.55, 0.24, 0.65);
  b.plane(x0 + 0.3, y0 + H + 0.57, z0 + 0.3, W - 0.6, D - 0.6, scale(scheme.roof, 0.9));
  acUnit(b, x1 - 8, y0 + H + 0.55, z0 + 2.5, 3.4, 2.4);
  if (!ctx.lean) {
    acUnit(b, x1 - 13, y0 + H + 0.55, z0 + 2.5, 2.8, 2.2);
    acUnit(b, x0 + 4, y0 + H + 0.55, z0 + 3, 3, 2.2);
    // Pylon at the front-left corner, trolley bay, benches and planters.
    b.box(x0 - 1.2, 0, z1 + 5, 0.7, 6.4, 0.7, scheme.trim, { grad: 0.2 });
    b.box(x0 - 2.0, 6.4, z1 + 4.8, 2.3, 2.0, 0.5, hex('#0e1b26'));
    b.box(x0 - 2.0, 6.4, z1 + 5.3, 2.3, 0.07, 0.04, scheme.accent, { emissive: 0.9, shade: false });
    b.box(x0 - 1.9, 6.5, z1 + 5.3, 2.1, 0.07, 0.04, scheme.accent, { emissive: 0.9, shade: false });
    b.box(x1 - 7, 0, z1 + 3.4, 5, 0.12, 2.4, scale(scheme.stone, 1.1));
    b.box(x1 - 7, 2.1, z1 + 3.4, 5, 0.14, 2.4, scheme.trim);
    for (const sx of [x1 - 7.1, x1 - 2.2]) b.box(sx, 0, z1 + 3.4, 0.14, 2.2, 0.14, scheme.trim);
    for (let i = 0; i < 4; i++) bench(b, x0 + 4 + i * 6.2, z1 + 4.2, 'x');
    for (const sx of [px0 - 2.2, px0 + pw + 2.2]) planter(b, sx, z1 + pd + 1.6, 1.3, rnd);
  }
  return finish(ctx, {
    kind: 'retailBlock', footprint: { width: W, depth: D }, height: y0 + H + 0.8,
    anchor: { x: 0, y: y0 + PH + 3.4, z: 0 },
    door: { x: 0, y: 0, z: z1 + pd + 8 },
    sign, ground: [x0 - 3, z0 - 3, x1 + 3, z1 + pd + 9],
  }, params);
}
