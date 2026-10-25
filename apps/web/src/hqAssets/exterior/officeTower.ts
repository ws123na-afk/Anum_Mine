/**
 * Glass office tower: a two-storey glass podium with a canopy entrance and the sign board, a
 * curtain-wall shaft (facade grid of panes between corner piers and stone slab bands), a setback
 * with a planted terrace, a narrower upper shaft, and a crown (louvred plant room, glass lantern,
 * spire). Window panes follow the night rule in `parts.ts`.
 */
import { scale } from '../../lib/hqGeometry.ts';
import { WARM, castShadow } from '../../lib/hqProps.ts';
import { clamp, curtainWall, entrance, fins, finish, ledge, makeCtx, rectFaces, signBoard } from './parts.ts';
import { crown, slabBand, terrace } from './towerParts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

export function buildOfficeTower(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('officeTower', params, 0.55);
  const { b, scheme } = ctx;
  const W = clamp(params.width, 14, 40, 22), D = clamp(params.depth, 14, 40, 22);
  const levels = Math.round(clamp(params.levels, 5, 32, 18));
  const LH = 3.8, PH = 4.6;
  const PW = W + 10, PD = D + 8;
  const px = -PW / 2, pz = -PD / 2;
  const front = PD / 2;

  b.plane(px - 3, 0.01, pz - 3, PW + 6, PD + 12, scale(scheme.stone, 0.6));
  castShadow(b, px, pz, PW, PD, 2 * PH + 3, 0.022);
  b.box(px - 0.4, 0, pz - 0.4, PW + 0.8, 0.45, PD + 0.8, scheme.stone);
  // Podium: two glass storeys, a slab on top that is also the terrace floor.
  curtainWall(ctx, px, pz, PW, PD, 0.45, 2, PH, { colW: 2.4, inset: 0.8 });
  for (const [x, w] of [[px, 0.9], [px + PW - 0.9, 0.9]] as const) b.box(x, 0.45, pz, w, 2 * PH, PD, scheme.wall, { grad: 0.2 });
  const podTop = 0.45 + 2 * PH;
  slabBand(ctx, px, pz, PW, PD, podTop - 0.1, 0.6);

  // Shaft at the back of the podium: lower part full width, upper part stepped in.
  const sx = -W / 2, sz = pz + 1.5;
  const lower = Math.max(4, Math.ceil(levels * 0.62));
  const upper = Math.max(2, levels - lower);
  const y0 = podTop + 0.5;
  curtainWall(ctx, sx, sz, W, D, y0, lower, LH);
  for (const [cx, cz] of [[sx, sz], [sx + W, sz], [sx, sz + D], [sx + W, sz + D]] as const) b.box(cx - 0.5, y0, cz - 0.5, 1.0, lower * LH, 1.0, scheme.wall, { grad: 0.25 });
  for (let l = 6; l < lower; l += 6) slabBand(ctx, sx, sz, W, D, y0 + l * LH - 0.1, 0.45);
  const SF = rectFaces(sx, sz, W, D);
  for (const f of [SF.south, SF.east, SF.west]) {
    fins(ctx, f, 1.9, f.len - 1.9, y0, y0 + lower * LH, 3.9, 0.45, 0.24);
    for (let l = 2; l <= lower; l += 2) ledge(ctx, f, 0.3, f.len - 0.3, y0 + l * LH - 0.55, 0.45, 0.09);
  }
  const midY = y0 + lower * LH;
  slabBand(ctx, sx, sz, W, D, midY - 0.1, 0.55);
  const inset = Math.min(3, W * 0.12);
  const ux = sx + inset, uz = sz + inset, uw = W - inset * 2, ud = D - inset * 2;
  terrace(ctx, sx - 0.3, sz - 0.3, W + 0.6, D + 0.6, midY + 0.45, { x: ux - 0.4, z: uz - 0.4, w: uw + 0.8, d: ud + 0.8 });
  const uy = midY + 0.7;
  curtainWall(ctx, ux, uz, uw, ud, uy, upper, LH);
  for (const [cx, cz] of [[ux, uz], [ux + uw, uz], [ux, uz + ud], [ux + uw, uz + ud]] as const) b.box(cx - 0.4, uy, cz - 0.4, 0.8, upper * LH, 0.8, scheme.wall, { grad: 0.25 });
  const UF = rectFaces(ux, uz, uw, ud);
  for (const f of [UF.south, UF.east, UF.west]) fins(ctx, f, 1.5, f.len - 1.5, uy, uy + upper * LH, 3.9, 0.4, 0.22);
  const roofY = uy + upper * LH;
  const top = crown(ctx, 0, uz + ud / 2, uw, ud, roofY);

  // Front: entrance and the sign board on the podium (over the first slab line).
  const f = { ox: px, oz: pz + PD, tx: 1, tz: 0, nx: 0, nz: 1, len: PW } as const;
  entrance(ctx, f, PW / 2, 0.45, 3.6, 3.4, 2.8, scheme.accent);
  b.halo(0, 2.2, front + 1.5, 6, WARM, 0.85);
  const sign = signBoard(ctx, f, PW / 2, PH + 0.45 - 0.4, Math.min(10, PW - 6), 1.6, 0.03);

  return finish(ctx, {
    kind: 'officeTower', footprint: { width: PW, depth: PD }, height: roofY + 0.8,
    anchor: { x: 0, y: top + 3, z: uz + ud / 2 },
    door: { x: 0, y: 0, z: front + 8 },
    sign, ground: [px - 3, pz - 3, px + PW + 3, front + 9],
  }, params);
}
