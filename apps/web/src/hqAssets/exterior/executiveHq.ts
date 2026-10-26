/**
 * Executive HQ: a three-storey stone-and-glass podium with a grand columned portico, ceremonial
 * steps, a drive with a fountain, and a slim curtain-wall tower rising behind it with gold bands,
 * a large lit ANUM mark and a crown with a lantern and spire. A helipad sits on the podium roof.
 */
import { hex, scale } from '../../lib/hqGeometry.ts';
import { TEAL, anumMark, castShadow, hedge, lamp, pool } from '../../lib/hqProps.ts';
import { clamp, curtainWall, faceBox, faceQuad, fins, finish, ledge, makeCtx, rectFaces, signBoard, type Face } from './parts.ts';
import { accentBand, crown, flagPole, slabBand, terrace } from './towerParts.ts';
import { atriumVault, balconyRun, roofDeck } from './facadeKit.ts';
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
  // Lobby turnstiles under the portico: four slim gates with a lit top strip (furniture, not a count of anyone).
  for (let i = 0; i < 4; i++) {
    const tx0 = x0 + W / 2 - 3.3 + i * 2.2;
    b.box(tx0, y0, z1 + pD - 3.2, 0.34, 1.05, 1.3, hex('#2a333d'), { grad: 0.15 });
    b.box(tx0 + 0.04, y0 + 1.05, z1 + pD - 3.1, 0.26, 0.04, 1.1, hex('#7fe8d4'), { emissive: 0.9, shade: false });
  }
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
  // The atrium: a glazed barrel vault on the podium roof in front of the tower, lit warm from below at night, and a roof garden with a pool beside it.
  if (!ctx.lean) {
    atriumVault(ctx, 0, tz + td + 1.0, Math.min(tw * 0.74, 11), 5.2, podTop + 2.05, 2.3);
    roofDeck(ctx, x0 + 1.2, tz + td + 0.8, Math.max(5.6, W * 0.2), Math.min(7, z1 - (tz + td) - 2), podTop + 0.96, { pool: true, pergola: true, loungers: 3, shrubs: 5 });
  }
  const hx = x0 + W * 0.84, hz = tz + td + 4.2;
  b.prism(hx, podTop + 0.95, hz, 3.4, 3.4, 0.08, 18, hex('#3b4452'));
  b.prism(hx, podTop + 1.03, hz, 2.7, 2.7, 0.02, 18, hex('#e8d36a'), { emissive: 0.25 });
  b.box(hx - 0.7, podTop + 1.06, hz - 1.2, 0.3, 0.02, 2.4, hex('#e8d36a'), { emissive: 0.3, shade: false });
  b.box(hx + 0.4, podTop + 1.06, hz - 1.2, 0.3, 0.02, 2.4, hex('#e8d36a'), { emissive: 0.3, shade: false });
  b.box(hx - 0.5, podTop + 1.06, hz - 0.15, 1.0, 0.02, 0.3, hex('#e8d36a'), { emissive: 0.3, shade: false });

  // Tower: a full-width lower shaft, a terrace setback, then a narrower upper shaft under the crown.
  const tl = Math.max(4, levels - PL);
  const ty = podTop + 1.0;
  const lower = Math.max(2, Math.ceil(tl * 0.62)), upper = Math.max(2, tl - lower);
  curtainWall(ctx, tx, tz, tw, td, ty, lower, TLH, { colW: 1.9, inset: 0.7 });
  for (const [cx, cz] of [[tx, tz], [tx + tw, tz], [tx, tz + td], [tx + tw, tz + td]] as const) b.box(cx - 0.55, ty, cz - 0.55, 1.1, lower * TLH, 1.1, scheme.wall, { grad: 0.25 });
  const TF = rectFaces(tx, tz, tw, td);
  // Stone fins every second bay and a sunshade ledge on every floor of the front and the two sides.
  for (const f of [TF.south, TF.east, TF.west]) {
    fins(ctx, f, 1.9, f.len - 1.9, ty, ty + lower * TLH, 3.8, 0.5, 0.26);
    for (let l = 1; l <= lower; l++) ledge(ctx, f, 0.3, f.len - 0.3, ty + l * TLH - 0.55, 0.5, 0.1);
  }
  for (let l = 3; l < lower; l += 3) { slabBand(ctx, tx, tz, tw, td, ty + l * TLH - 0.12, 0.4); accentBand(ctx, tx, tz, tw, td, ty + l * TLH + 0.28, 0.14, 0.7); }
  // Balconies with glass balustrades on both flanks of the lower shaft.
  if (!ctx.lean) for (const l of [1, 2]) for (const f of [TF.east, TF.west]) balconyRun(ctx, f, 2.2, f.len - 2.2, ty + l * TLH + 0.1, { depth: 1.5 });
  const midY = ty + lower * TLH;
  slabBand(ctx, tx, tz, tw, td, midY - 0.1, 0.5);
  const inset = Math.min(3.2, tw * 0.1);
  const ux = tx + inset, uz = tz + inset, uw = tw - inset * 2, ud = td - inset * 2;
  terrace(ctx, tx - 0.3, tz - 0.3, tw + 0.6, td + 0.6, midY + 0.4, { x: ux - 0.4, z: uz - 0.4, w: uw + 0.8, d: ud + 0.8 });
  const uy = midY + 0.65;
  curtainWall(ctx, ux, uz, uw, ud, uy, upper, TLH, { colW: 1.9, inset: 0.7 });
  for (const [cx, cz] of [[ux, uz], [ux + uw, uz], [ux, uz + ud], [ux + uw, uz + ud]] as const) b.box(cx - 0.5, uy, cz - 0.5, 1.0, upper * TLH, 1.0, scheme.wall, { grad: 0.25 });
  const UF = rectFaces(ux, uz, uw, ud);
  for (const f of [UF.south, UF.east, UF.west]) fins(ctx, f, 1.9, f.len - 1.9, uy, uy + upper * TLH, 3.8, 0.45, 0.24);
  const tTop = uy + upper * TLH;
  const markSize = Math.min(9, tl * TLH * 0.3);
  anumMark(b, 0, ty + lower * TLH * 0.55, tz + td + 0.62, markSize, TEAL, 1.0);
  const top = crown(ctx, 0, uz + ud / 2, uw, ud, tTop, true);
  // Warm uplighting at the foot of the portico and a halo on the crown for the night.
  for (let i = 0; i < 6; i++) b.halo(x0 + pu0 + 0.8 + (i * (pW - 1.6)) / 5, y0 + 1.0, z1 + pD + 0.6, 3.2, hex('#ffcf7a'), 0.9);
  b.halo(0, y0 + 3.0, z1 + pD + 1.5, 7, hex('#ffcf7a'), 0.8);
  b.halo(0, top - 1.2, uz + ud / 2, 9, hex('#7fe8d4'), 0.6);

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
