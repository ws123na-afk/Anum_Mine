/**
 * Low administration block: three storeys of ribbon windows with sunshades, end wings, a glass
 * atrium bay in the middle with a columned portico and the sign board, a parapet roof with solar
 * panels and plant, and flag poles on the forecourt.
 */
import { hex, scale } from '../../lib/hqGeometry.ts';
import { WARM, acUnit, bench, castShadow, hedge, planter, solarRow } from '../../lib/hqProps.ts';
import { clamp, curtainWall, entrance, faceBox, faceQuad, fins, finish, ledge, makeCtx, paneRow, rectFaces, signBoard } from './parts.ts';
import { accentBand, flagPole, slabBand } from './towerParts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

export function buildAdminBlock(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('adminBlock', params, 0.5);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 24, 64, 38), D = clamp(params.depth, 12, 30, 17);
  const levels = Math.round(clamp(params.levels, 2, 5, 3));
  const LH = 3.7, y0 = 0.45;
  const H = levels * LH;
  const x0 = -W / 2, z0 = -D / 2, x1 = W / 2, z1 = D / 2;
  const F = rectFaces(x0, z0, W, D);
  const aw = Math.min(12, W * 0.3), ad = 3.6;
  const ax0 = -aw / 2;

  b.plane(x0 - 3, 0.01, z0 - 3, W + 6, D + ad + 16, scale(scheme.stone, 0.62));
  castShadow(b, x0, z0, W, D, H + 1.5, 0.022);
  b.box(x0 - 0.4, 0, z0 - 0.4, W + 0.8, y0, D + 0.8 + ad * 0.5, scheme.stone);
  b.box(x0, y0, z0, W, H, D, scale(scheme.wall, 1.0), { skip: ['top', 'bottom'], grad: 0.12 });
  // Ground-floor stone base on all faces.
  for (const f of Object.values(F)) faceQuad(b, f, 0, f.len, y0, y0 + 1.0, scale(scheme.stone, 0.72), 0, 0.05);

  // Ribbon windows and sunshades per storey; the front leaves the atrium gap clear.
  const gapFrom = W / 2 + ax0 - 0.5, gapTo = W / 2 + ax0 + aw + 0.5;
  for (let l = 0; l < levels; l++) {
    const ly = y0 + l * LH;
    const wy0 = ly + 0.95, wy1 = ly + LH - 0.75;
    const lift = 0.55 + 0.45 * ((l + 1) / levels);
    paneRow(ctx, F.south, 1.0, gapFrom, wy0, wy1, Math.floor((gapFrom - 1) / 2.2), 0.12, lift);
    paneRow(ctx, F.south, gapTo, W - 1.0, wy0, wy1, Math.floor((W - 1 - gapTo) / 2.2), 0.12, lift);
    paneRow(ctx, F.north, 1.0, W - 1.0, wy0, wy1, Math.floor((W - 2) / 2.2), 0.12, lift);
    paneRow(ctx, F.east, 1.0, D - 1.0, wy0, wy1, Math.max(2, Math.floor((D - 2) / 2.2)), 0.12, lift);
    paneRow(ctx, F.west, 1.0, D - 1.0, wy0, wy1, Math.max(2, Math.floor((D - 2) / 2.2)), 0.12, lift);
    // Sunshade over each front window group, and a floor line on the other faces.
    faceBox(b, F.south, 0.6, gapFrom + 0.2, wy1 + 0.1, wy1 + 0.26, 0, 1.0, scheme.trim);
    faceBox(b, F.south, gapTo - 0.2, W - 0.6, wy1 + 0.1, wy1 + 0.26, 0, 1.0, scheme.trim);
    for (const f of [F.north, F.east, F.west]) {
      faceQuad(b, f, 0, f.len, ly + LH - 0.12, ly + LH, scheme.trim, 0, 0.05);
      ledge(ctx, f, 0.4, f.len - 0.4, wy1 + 0.08, 0.4, 0.08);
    }
  }
  // Stone fins between the window bays: relief and shadow lines on every face.
  fins(ctx, F.south, 1.0, gapFrom, y0 + 1.0, y0 + H, 4.4, 0.4, 0.2);
  fins(ctx, F.south, gapTo, W - 1.0, y0 + 1.0, y0 + H, 4.4, 0.4, 0.2);
  for (const f of [F.east, F.west]) fins(ctx, f, 1.0, D - 1.0, y0 + 1.0, y0 + H, 4.4, 0.35, 0.2);
  // Wing end piers (the front corners read as heavy stone).
  for (const x of [x0 - 0.3, x1 - 0.9]) b.box(x, y0, z1 - 0.3, 1.2, H, 0.5, scheme.trim, { grad: 0.2 });

  // Atrium bay: a glass box proud of the front, one storey taller.
  const aH = H + 1.6;
  curtainWall(ctx, ax0, z1, aw, ad, y0, 1, aH, { colW: 1.7, inset: 0.4, skipFaces: ['north'] });
  b.box(ax0 - 0.3, y0 + aH, z1 - 0.3, aw + 0.6, 0.5, ad + 0.6, scheme.wall);
  accentBand(ctx, ax0, z1, aw, ad, y0 + aH + 0.5, 0.26, 0.9);
  const AF = rectFaces(ax0, z1, aw, ad);
  // Entrance door at the bottom of the atrium front, with a portico of columns and a lit soffit.
  entrance(ctx, AF.south, aw / 2, y0, 3.4, 3.2, 3.6, scheme.accent, false);
  const cols = 6;
  const pw = aw + 3;
  for (let i = 0; i < cols; i++) {
    const cx = -pw / 2 + 0.4 + (i * (pw - 0.8)) / (cols - 1);
    b.prism(cx, y0, z1 + ad + 3.6, 0.28, 0.28, 3.9, 8, scale(scheme.wall, 1.05));
  }
  b.box(-pw / 2 - 0.3, y0 + 3.9, z1 + ad - 0.2, pw + 0.6, 0.5, 4.2, scheme.wall);
  b.box(-pw / 2 + 0.2, y0 + 3.88, z1 + ad, pw - 0.4, 0.04, 3.7, WARM, { emissive: 0.5, shade: false });
  b.box(-pw / 2 - 0.3, y0 + 4.4, z1 + ad + 3.9, pw + 0.6, 0.1, 0.1, scheme.accent, { emissive: 0.9, shade: false });
  // Steps up to the portico.
  for (let i = 0; i < 3; i++) b.box(-pw / 2 - 0.6 + i * 0.3, 0, z1 + ad + 3.9 + i * 0.4, pw + 1.2 - i * 0.6, 0.15 * (3 - i) + 0.1, 0.45, hex('#d7d2c4'));
  // Warm uplight under the portico and a glow on the atrium for the night.
  for (const cx of [-pw / 2 + 0.4, pw / 2 - 0.4]) b.halo(cx, y0 + 1.2, z1 + ad + 3.6, 3, WARM, 0.9);
  b.halo(0, y0 + 2.6, z1 + ad + 1.5, 6, WARM, 0.8);
  // Sign board on the atrium front, above the portico.
  const sign = signBoard(ctx, AF.south, aw / 2, y0 + 5.2, Math.min(aw - 1, 9), 1.6, 0.05);

  // Roof: parapet ring, roof deck, solar, plant and a stair head.
  slabBand(ctx, x0, z0, W, D, y0 + H, 0.55);
  accentBand(ctx, x0, z0, W, D, y0 + H + 0.55, 0.22, 0.6);
  b.plane(x0 + 0.3, y0 + H + 0.57, z0 + 0.3, W - 0.6, D - 0.6, scale(scheme.roof, 0.9));
  if (!ctx.lean) {
    solarRow(b, x0 + 3, y0 + H + 0.55, z0 + 3, Math.max(3, Math.min(8, Math.floor((W - 18) / 2.4))), 2.0, 1.7);
    acUnit(b, x1 - 7, y0 + H + 0.55, z0 + 2.2, 3.2, 2.2);
    acUnit(b, x1 - 12, y0 + H + 0.55, z0 + 2.2, 2.6, 2.0);
  }
  b.box(x0 + W * 0.7, y0 + H + 0.55, z0 + D * 0.5, 4, 2.4, 4, scale(scheme.wall, 0.95), { grad: 0.12 });

  if (!ctx.lean) {
    // Forecourt: three flag poles with plain pennants, hedges, planters and benches.
    for (const dx of [-aw / 2 - 7, -aw / 2 - 9.5, -aw / 2 - 12]) if (dx > x0 + 1) flagPole(ctx, dx, z1 + ad + 6, 9);
    hedge(b, x0 + 1, z1 + 1.0, ax0 - 1 - (x0 + 1), 0.8, 0.7);
    hedge(b, ax0 + aw + 1, z1 + 1.0, x1 - 1 - (ax0 + aw + 1), 0.8, 0.7);
    for (const sx of [-pw / 2 - 2.4, pw / 2 + 2.4]) planter(b, sx, z1 + ad + 3.2, 1.4, rnd);
    bench(b, ax0 - 3.5, z1 + ad + 3.5, 'x');
    bench(b, ax0 + aw + 3.5, z1 + ad + 3.5, 'x');
  }
  return finish(ctx, {
    kind: 'adminBlock', footprint: { width: W, depth: D }, height: y0 + H + 0.8,
    anchor: { x: 0, y: y0 + aH + 3.6, z: 0 },
    door: { x: 0, y: 0, z: z1 + ad + 9 },
    sign, ground: [x0 - 3, z0 - 3, x1 + 3, z1 + ad + 11],
  }, params);
}
