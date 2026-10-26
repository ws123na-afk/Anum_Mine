/**
 * Glass office tower: a two-storey glazed podium with a canopy entrance, a lit lobby and the sign board; a roof deck
 * on the podium with a plunge pool and a pergola; a curtain-wall shaft (panes between slim mullions, dark spandrels,
 * stone slab edges and vertical fins) that steps in on one side, so the silhouette is a stepped composition and not a
 * wedding cake; balconies with glass balustrades on the exposed floors; and a roof plant enclosure with louvres, solar
 * panels and a blinking aviation light. Window panes follow the night rule in `parts.ts`.
 */
import { scale, hex } from '../../lib/hqGeometry.ts';
import { WARM, acUnit, castShadow, solarRow } from '../../lib/hqProps.ts';
import { balconyRun, beaconLight, canopy, leaf, louvres, pennant, roofDeck } from './facadeKit.ts';
import { clamp, curtainWall, entrance, faceBox, fins, finish, ledge, makeCtx, rectFaces, signBoard } from './parts.ts';
import { accentBand, slabBand, terrace } from './towerParts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

export function buildOfficeTower(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('officeTower', params, 0.55);
  const { b, scheme } = ctx;
  const W = clamp(params.width, 14, 40, 22), D = clamp(params.depth, 14, 40, 22);
  const levels = Math.round(clamp(params.levels, 5, 32, 18));
  const LH = 3.8, PH = 4.4;
  const PW = W + 10, PD = D + 8;
  const px = -PW / 2, pz = -PD / 2;
  const front = PD / 2;

  b.plane(px - 3, 0.01, pz - 3, PW + 6, PD + 14, scale(scheme.stone, 0.6));
  castShadow(b, px, pz, PW, PD, 2 * PH + 3, 0.022);
  b.box(px - 0.4, 0, pz - 0.4, PW + 0.8, 0.45, PD + 0.8, scheme.stone);
  // Podium: two glass storeys, stone end walls, a slab on top that is also the deck floor.
  curtainWall(ctx, px, pz, PW, PD, 0.45, 2, PH, { colW: 2.4, inset: 0.8 });
  for (const [x, w] of [[px, 0.9], [px + PW - 0.9, 0.9]] as const) b.box(x, 0.45, pz, w, 2 * PH, PD, scheme.wall, { grad: 0.2 });
  const podTop = 0.45 + 2 * PH;
  slabBand(ctx, px, pz, PW, PD, podTop - 0.1, 0.6);

  // Shaft volume A: nearly the full footprint, at the back of the podium. Volume B stands on its east part and is set back from the front.
  const sx = -W / 2, sz = pz + 1.5;
  const LA = Math.max(3, Math.ceil(levels * 0.55));
  const LB = Math.max(2, levels - LA);
  const y0 = podTop + 0.5;
  curtainWall(ctx, sx, sz, W, D, y0, LA, LH);
  for (const [cx, cz] of [[sx, sz], [sx + W, sz], [sx, sz + D], [sx + W, sz + D]] as const) b.box(cx - 0.5, y0, cz - 0.5, 1.0, LA * LH, 1.0, scheme.wall, { grad: 0.25 });
  const AF = rectFaces(sx, sz, W, D);
  for (const f of [AF.south, AF.east, AF.west]) {
    fins(ctx, f, 1.9, f.len - 1.9, y0, y0 + LA * LH, 3.9, 0.45, 0.24);
    for (let l = 2; l <= LA; l += 2) ledge(ctx, f, 0.3, f.len - 0.3, y0 + l * LH - 0.55, 0.45, 0.09);
  }
  // Balconies on the front of volume A (the floor above the podium and the one above it), set between two fins.
  if (!ctx.lean) for (const l of [1, 3]) if (l < LA) balconyRun(ctx, AF.south, 2.2, Math.min(W - 2.2, W * 0.55), y0 + l * LH + 0.1, { depth: 1.5 });
  const midY = y0 + LA * LH;
  slabBand(ctx, sx, sz, W, D, midY - 0.1, 0.55);
  const bw = Math.round(W * 0.62), bd = D - 3.6;
  const bx = sx + W - bw, bz = sz + 3.6 * 0.5;
  // Roof deck on the exposed part of A (the west part and the front strip) with a pool, pergola and planting.
  const exposedW = W - bw;
  terrace(ctx, sx - 0.3, sz - 0.3, W + 0.6, D + 0.6, midY + 0.45, { x: bx - 0.4, z: bz - 0.4, w: bw + 0.8, d: bd + 0.8 });
  if (!ctx.lean) {
    roofDeck(ctx, sx + 0.7, sz + D * 0.42, exposedW - 1.0, D * 0.5, midY + 0.7, { pool: exposedW > 8, pergola: false, loungers: 2, shrubs: 4 });
    roofDeck(ctx, bx, sz + 0.6, bw, 2.6, midY + 0.7, { pool: false, pergola: false, loungers: 0, shrubs: 3 });
  }
  const uy = midY + 0.7;
  curtainWall(ctx, bx, bz, bw, bd, uy, LB, LH);
  for (const [cx, cz] of [[bx, bz], [bx + bw, bz], [bx, bz + bd], [bx + bw, bz + bd]] as const) b.box(cx - 0.4, uy, cz - 0.4, 0.8, LB * LH, 0.8, scheme.wall, { grad: 0.25 });
  const BF = rectFaces(bx, bz, bw, bd);
  for (const f of [BF.south, BF.east, BF.west]) fins(ctx, f, 1.5, f.len - 1.5, uy, uy + LB * LH, 3.9, 0.4, 0.22);
  if (!ctx.lean) balconyRun(ctx, BF.west, 1.8, BF.west.len - 1.8, uy + LH + 0.1, { depth: 1.4 });
  const roofY = uy + LB * LH;
  // Roof: stone edge, accent line, plant enclosure with louvres, solar rows, a unit and the blinking light.
  slabBand(ctx, bx, bz, bw, bd, roofY, 0.5);
  accentBand(ctx, bx, bz, bw, bd, roofY + 0.5, 0.26, 0.9);
  const ry = roofY + 0.8;
  b.plane(bx + 0.3, ry - 0.02, bz + 0.3, bw - 0.6, bd - 0.6, scale(scheme.roof, 0.9));
  const ew = bw * 0.5, ed = bd * 0.4, ex = bx + bw * 0.1, ez = bz + bd * 0.5;
  b.box(ex, ry, ez, ew, 3.2, ed, scale(scheme.wall, 0.9), { skip: ['bottom'], grad: 0.12 });
  const EF = rectFaces(ex, ez, ew, ed);
  louvres(ctx, EF.south, 0.5, ew - 0.5, ry + 0.4, ry + 2.8, 6, 0.12, hex('#39424c'));
  louvres(ctx, EF.west, 0.5, ed - 0.5, ry + 0.4, ry + 2.8, 6, 0.12, hex('#39424c'));
  if (!ctx.lean) {
    solarRow(b, bx + bw * 0.1, ry, bz + 1.2, Math.max(2, Math.min(5, Math.floor((bw * 0.8) / 2.4))), 2.0, 1.7);
    acUnit(b, bx + bw * 0.72, ry, ez + 0.5, 2.4, 1.8);
  }
  beaconLight(ctx, bx + bw - 0.9, ry, bz + 0.9);
  const top = ry + 3.2;

  // Front: lobby entrance with a canopy, the sign board on the podium parapet, and a pennant row.
  const f = { ox: px, oz: pz + PD, tx: 1, tz: 0, nx: 0, nz: 1, len: PW } as const;
  entrance(ctx, f, PW / 2, 0.45, 3.8, 3.5, 2.8, scheme.accent, false);
  canopy(ctx, f, PW / 2, 8.2, 4.4, 3.4);
  b.halo(0, 2.2, front + 1.5, 6, WARM, 0.85);
  b.halo(-PW * 0.28, 2.6, front + 0.6, 4, WARM, 0.5);
  b.halo(PW * 0.28, 2.6, front + 0.6, 4, WARM, 0.5);
  const sign = signBoard(ctx, f, PW / 2, PH + 0.9, Math.min(10, PW - 6), 1.6, 0.03);
  if (!ctx.lean) {
    for (const sgn of [-1, 1]) { pennant(ctx, sgn * (PW / 2 - 2.4), front + 2.2, 8.5); leaf(ctx, sgn * 4.8, 0.9, front + 1.6, 0.8, 0.6, 0.8); leaf(ctx, sgn * 6.4, 0.9, front + 1.7, 0.7, 0.55, 0.7); }
    // Podium roof: a planted edge along the front so the deck reads as a garden from the street.
    faceBox(b, f, 1.2, PW - 1.2, podTop + 0.1, podTop + 0.7, 0, 0.5, hex('#8d8777'));
    for (let i = 0; i < 7; i++) leaf(ctx, px + 2.5 + i * ((PW - 5) / 6), podTop + 1.1, pz + PD - 0.3, 0.6, 0.5, 0.45);
  }

  return finish(ctx, {
    kind: 'officeTower', footprint: { width: PW, depth: PD }, height: top + 0.8,
    anchor: { x: 0, y: top + 3, z: bz + bd / 2 },
    door: { x: 0, y: 0, z: front + 8 },
    sign, ground: [px - 3, pz - 3, px + PW + 3, front + 9],
  }, params);
}
