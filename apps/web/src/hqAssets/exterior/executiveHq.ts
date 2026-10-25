/**
 * Executive HQ: a three-storey stone-and-glass podium with a grand columned portico, ceremonial
 * steps, a drive with a fountain, and a slim curtain-wall tower rising behind it with gold bands,
 * a large lit ANUM mark and a crown with a lantern and spire. A helipad sits on the podium roof.
 */
import { hex, scale } from '../../lib/hqGeometry.ts';
import { TEAL, anumMark, castShadow, hedge, lamp, pool } from '../../lib/hqProps.ts';
import { clamp, curtainWall, faceBox, faceQuad, finish, makeCtx, rectFaces, signBoard, type Face } from './parts.ts';
import { accentBand, crown, flagPole, slabBand, terrace } from './towerParts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

export function buildExecutiveHq(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('executiveHq', params, 0.58);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 30, 64, 42), D = clamp(params.depth, 26, 50, 34);
  const levels = Math.round(clamp(params.levels, 6, 26, 13));
  const PL = 3, PLH = 4.4, TLH = 3.8, y0 = 0.9;
  const x0 = -W / 2, z0 = -D / 2, z1 = D / 2;
  const F = rectFaces(x0, z0, W, D);
  const podH = PL * PLH;
  const podTop = y0 + podH;

  b.plane(x0 - 5, 0.01, z0 - 5, W + 10, D + 40, scale(scheme.stone, 0.64));
  castShadow(b, x0, z0, W, D, podH + levels * 1.2, 0.022);
  // Plinth and podium.
  b.box(x0 - 0.8, 0, z0 - 0.8, W + 1.6, y0, D + 1.6, scheme.stone);
  curtainWall(ctx, x0, z0, W, D, y0, PL, PLH, { colW: 2.5, inset: 1.2 });
  // Stone piers on the front bays and stone end blocks all round.
  const bays = Math.floor((W - 2.4) / 4.6);
  for (let i = 0; i <= bays; i++) {
    const u = 1.2 + (i * (W - 2.4)) / bays;
    faceBox(b, F.south, u - 0.32, u + 0.32, y0, podTop, 0, 0.45, scheme.wall, { grad: 0.2 });
  }
  for (const [cx, cz] of [[x0, z0], [x0 + W, z0], [x0, z0 + D], [x0 + W, z0 + D]] as const) b.box(cx - 0.6, y0, cz - 0.6, 1.2, podH, 1.2, scheme.wall, { grad: 0.25 });
  for (let l = 1; l < PL; l++) slabBand(ctx, x0, z0, W, D, y0 + l * PLH - 0.12, 0.4);
  slabBand(ctx, x0, z0, W, D, podTop - 0.1, 0.6);
  accentBand(ctx, x0, z0, W, D, podTop + 0.5, 0.22, 0.8);

  // Portico: six columns two storeys high, a deep entablature, a lit soffit and the sign board.
  const pD = 6.0, colH = 8.4, pW = Math.min(20, W * 0.5);
  const pu0 = W / 2 - pW / 2;
  const frontPlane: Face = F.south;
  faceQuad(b, frontPlane, W / 2 - 2.4, W / 2 + 2.4, y0, y0 + 4.2, hex('#ffcf7a'), 2.95, 0.7);
  for (const u of [W / 2 - 2.4, W / 2, W / 2 + 2.4]) faceBox(b, frontPlane, u - 0.07, u + 0.07, y0, y0 + 4.2, 0.6, 0.8, scheme.trim);
  for (let i = 0; i < 6; i++) {
    const u = pu0 + 0.8 + (i * (pW - 1.6)) / 5;
    b.prism(x0 + u, y0, z1 + pD - 0.6, 0.55, 0.5, colH, 10, scale(scheme.wall, 1.08));
    b.box(x0 + u - 0.7, y0, z1 + pD - 1.3, 1.4, 0.35, 1.4, scheme.wall);
    b.box(x0 + u - 0.65, y0 + colH, z1 + pD - 1.25, 1.3, 0.3, 1.3, scheme.wall);
  }
  faceBox(b, frontPlane, pu0 - 0.4, pu0 + pW + 0.4, y0 + colH + 0.3, y0 + colH + 1.7, 0, pD + 0.2, scale(scheme.wall, 1.02), { grad: 0.1 });
  faceBox(b, frontPlane, pu0 + 0.4, pu0 + pW - 0.4, y0 + colH + 0.28, y0 + colH + 0.32, 0.2, pD - 0.2, hex('#ffcf7a'), { emissive: 0.6, shade: false });
  faceBox(b, frontPlane, pu0 - 0.4, pu0 + pW + 0.4, y0 + colH + 1.7, y0 + colH + 1.95, 0, pD + 0.3, scale(scheme.wall, 0.95));
  faceQuad(b, frontPlane, pu0 - 0.4, pu0 + pW + 0.4, y0 + colH + 1.72, y0 + colH + 1.8, scheme.accent, 0.8, pD + 0.32);
  const sign = signBoard(ctx, frontPlane, W / 2, y0 + colH + 0.45, Math.min(pW - 2, 15), 1.3, pD + 0.22);
  // Steps (five, wide) and lit risers.
  for (let i = 0; i < 5; i++) {
    const w = pW + 3 - i * 0.5;
    b.box(x0 + W / 2 - w / 2, 0, z1 + pD + 0.2 + i * 0.5, w, y0 - i * 0.18, 0.5, hex('#e1dccf'));
  }
  for (const sgn of [-1, 1]) b.box(x0 + W / 2 + sgn * (pW / 2 + 2.2) - 0.5, 0, z1 + pD, 1.0, 1.3, 3.0, scheme.wall, { grad: 0.12 });

  // Podium roof: terrace round the tower, helipad.
  const tw = W * 0.5, td = D * 0.5;
  const tx = -tw / 2, tz = z0 + 2.4;
  terrace(ctx, x0 + 0.4, z0 + 0.4, W - 0.8, D - 0.8, podTop + 0.7, { x: tx - 0.6, z: tz - 0.6, w: tw + 1.2, d: td + 1.2 });
  const hx = x0 + W * 0.84, hz = tz + td + 4.2;
  b.prism(hx, podTop + 0.95, hz, 3.4, 3.4, 0.08, 18, hex('#3b4452'));
  b.prism(hx, podTop + 1.03, hz, 2.7, 2.7, 0.02, 18, hex('#e8d36a'), { emissive: 0.25 });
  b.box(hx - 0.7, podTop + 1.06, hz - 1.2, 0.3, 0.02, 2.4, hex('#e8d36a'), { emissive: 0.3, shade: false });
  b.box(hx + 0.4, podTop + 1.06, hz - 1.2, 0.3, 0.02, 2.4, hex('#e8d36a'), { emissive: 0.3, shade: false });
  b.box(hx - 0.5, podTop + 1.06, hz - 0.15, 1.0, 0.02, 0.3, hex('#e8d36a'), { emissive: 0.3, shade: false });

  // Tower.
  const tl = Math.max(3, levels - PL);
  const ty = podTop + 1.0;
  curtainWall(ctx, tx, tz, tw, td, ty, tl, TLH, { colW: 1.9, inset: 0.7 });
  for (const [cx, cz] of [[tx, tz], [tx + tw, tz], [tx, tz + td], [tx + tw, tz + td]] as const) b.box(cx - 0.55, ty, cz - 0.55, 1.1, tl * TLH, 1.1, scheme.wall, { grad: 0.25 });
  for (let l = 3; l < tl; l += 3) { slabBand(ctx, tx, tz, tw, td, ty + l * TLH - 0.12, 0.4); accentBand(ctx, tx, tz, tw, td, ty + l * TLH + 0.28, 0.14, 0.7); }
  const tTop = ty + tl * TLH;
  const markSize = Math.min(9, tl * TLH * 0.34);
  anumMark(b, 0, ty + tl * TLH * 0.6, tz + td + 0.36, markSize, TEAL, 1.0);
  const top = crown(ctx, 0, tz + td / 2, tw, td, tTop, true);

  if (!ctx.lean) {
    // Drive, fountain, flags, lamps and hedges.
    b.plane(x0 + W / 2 - 7, 0.02, z1 + pD + 2.6, 14, 22, scale(hex('#3a3f46'), 1.05));
    pool(b, 0, z1 + pD + 15, 4.4, rnd);
    for (const sgn of [-1, 1]) {
      for (let i = 0; i < 3; i++) flagPole(ctx, sgn * (pW / 2 + 5.4 + i * 2.2), z1 + pD + 3.4, 10.5);
      lamp(b, sgn * (pW / 2 + 3.4), z1 + pD + 8);
      lamp(b, sgn * 9, z1 + pD + 17);
      hedge(b, sgn > 0 ? pW / 2 + 3 : -pW / 2 - 3 - 14, z1 + pD + 11, 14, 0.9, 0.9);
    }
  }
  return finish(ctx, {
    kind: 'executiveHq', footprint: { width: W, depth: D }, height: tTop + 0.8,
    anchor: { x: 0, y: top + 3.5, z: tz + td / 2 },
    door: { x: 0, y: 0, z: z1 + pD + 11 },
    sign, ground: [x0 - 5, z0 - 5, x0 + W + 5, z1 + pD + 28],
  }, params);
}
