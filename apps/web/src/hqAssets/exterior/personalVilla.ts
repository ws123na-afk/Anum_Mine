/**
 * Personal villa: a two-storey modern house made of offset white volumes. A long ground floor with a glazed
 * living front, a garage door and a roof terrace with a plunge pool; an upper volume that cantilevers over the
 * entrance with a balcony and a glass balustrade; timber cladding on the entrance wall, a slim canopy with a
 * lit soffit and a lit lobby; a garden with a swimming pool, loungers and palms; and a low stone wall at the
 * gate that carries the sign board. Everything is decoration: a lounger is furniture, not someone relaxing.
 */
import { MAT, hex, mix, scale } from '../../lib/hqGeometry.ts';
import { WARM, castShadow } from '../../lib/hqProps.ts';
import { stamp } from '../environment/common.ts';
import { treeTemplate } from '../environment/planting.ts';
import { balconyRun, canopy, leaf, pennant, roofDeck } from './facadeKit.ts';
import { STOREY, clamp, entrance, faceBox, faceQuad, finish, ledge, makeCtx, paneRow, rectFaces, signBoard } from './parts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';
import { cornerBevels } from './bevel.ts';

const TIMBER = hex('#9b7550');
const WATER = hex('#2f86b4');

export function buildPersonalVilla(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('personalVilla', params, 0.5);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 14, 26, 18), D = clamp(params.depth, 12, 22, 15);
  const GH = STOREY, UH = STOREY, y0 = 0.35;
  const x0 = -W / 2, z0 = -D / 2;
  // Ground floor: a long volume across the back; the front strip is the entrance court.
  const gw = W * 0.94, gd = D * 0.66;
  const gx = -gw / 2, gz = z0;
  const GF = rectFaces(gx, gz, gw, gd);
  const white = scale(scheme.wall, 1.06);

  b.plane(x0 - 8, 0.01, z0 - 3, W + 24, D + 22, scale(scheme.stone, 0.62));
  castShadow(b, x0, z0, W, D, GH + UH + 1, 0.022);
  b.box(gx - 0.3, 0, gz - 0.3, gw + 0.6, y0, gd + 0.6, scheme.stone);
  b.box(gx, y0, gz, gw, GH, gd, white, { skip: ['top', 'bottom'], grad: 0.12 });
  cornerBevels(b, gx, gz, gw, gd, y0, y0 + GH, scale(white, 0.94));
  // Living front: floor-to-ceiling panes on the west 62 percent; the east end is the garage and the entrance returns.
  const glazeTo = gw * 0.62;
  paneRow(ctx, GF.south, 0.6, glazeTo, y0 + 0.2, y0 + GH - 0.35, Math.max(3, Math.floor(glazeTo / 2.6)), 0.05, 0.9);
  faceBox(b, GF.south, 0.4, glazeTo + 0.2, y0 + GH - 0.35, y0 + GH - 0.1, 0, 0.12, GRAPHITE_FRAME);
  // Garage: a roller door with the slat texture, set in a dark frame.
  const gDoorW = Math.min(4.6, gw * 0.24), gDoorU = gw - gDoorW - 1.2;
  faceBox(b, GF.south, gDoorU - 0.15, gDoorU + gDoorW + 0.15, y0, y0 + 2.7, 0, 0.14, GRAPHITE_FRAME);
  b.withMat(MAT.roller, () => faceQuad(b, GF.south, gDoorU, gDoorU + gDoorW, y0, y0 + 2.55, hex('#cfd5da'), 0, 0.16));
  // Sides and back: ribbon glazing so every face has life.
  for (const f of [GF.east, GF.west]) paneRow(ctx, f, 1.2, f.len - 1.2, y0 + 0.9, y0 + GH - 0.6, 2, 0.12, 0.8);
  paneRow(ctx, GF.north, 0.8, GF.north.len - 0.8, y0 + 0.9, y0 + GH - 0.6, Math.floor(GF.north.len / 2.6), 0.1, 0.8);
  ledge(ctx, GF.south, -0.3, gw + 0.3, y0 + GH, 0.9, 0.22, white);
  const roof1 = y0 + GH + 0.22;

  // Upper volume: cantilevers 2.6 m over the entrance court on the west side.
  const uw = gw * 0.56, ud = gd * 0.78, ux = gx, uz = gz + 0.8, over = 2.6;
  const uy = y0 + GH + 0.25;
  b.box(ux, uy, uz, uw, UH, ud + over, scale(white, 0.98), { skip: ['top', 'bottom'], grad: 0.12 });
  cornerBevels(b, ux, uz, uw, ud + over, uy, uy + UH, scale(white, 0.94));
  const UF = rectFaces(ux, uz, uw, ud + over);
  paneRow(ctx, UF.south, 0.5, uw - 0.5, uy + 0.35, uy + UH - 0.4, Math.max(3, Math.floor(uw / 2.4)), 0.05, 1);
  paneRow(ctx, UF.west, 1.0, UF.west.len - 1.0, uy + 0.5, uy + UH - 0.5, 3, 0.14, 0.9);
  // Timber cladding on the upper volume's east flank and a slim fin wall.
  b.withMat(MAT.wood, () => faceQuad(b, UF.east, 0.6, UF.east.len - 0.6, uy + 0.3, uy + UH - 0.3, TIMBER, 0, 0.06));
  b.box(ux - 0.3, uy + UH, uz - 0.3, uw + 0.6, 0.3, ud + over + 0.6, scale(scheme.trim, 1.1));
  b.box(ux - 0.3, uy + UH + 0.3, uz - 0.3, uw + 0.6, 0.05, ud + over + 0.6, scheme.accent, { emissive: 0.6, shade: false, skip: ['top', 'bottom'] });
  // Columns that carry the cantilever, slim and set back from the corner.
  for (const u of [0.5, uw - 0.6]) b.box(ux + u, y0, uz + ud + over - 0.5, 0.22, GH + 0.25, 0.22, scheme.trim);
  // Balcony on the cantilever's flank, with a glass balustrade and planting.
  if (!ctx.lean) balconyRun(ctx, UF.west, 1.2, UF.west.len - 1.2, uy + 0.05, { depth: 1.2, planters: true });

  // Roof terrace over the garage end of the ground floor: decking, a small plunge pool, loungers, a pergola.
  const tx = gx + uw + 1.0, tz = gz + 0.8, tw = gw - uw - 2.0, td = gd - 1.6;
  if (tw > 4 && !ctx.lean) {
    b.box(tx - 0.2, roof1 - 0.02, tz - 0.2, tw + 0.4, 0.55, 0.15, scheme.trim);
    roofDeck(ctx, tx, tz, tw, td, roof1, { pool: tw > 6, pergola: tw > 7, loungers: 2, shrubs: 3 });
  }

  // Entrance court: stone paving, a timber-clad entrance wall with a lit lobby and a canopy.
  const entU = gw * 0.72 - 0.4;
  entrance(ctx, GF.south, entU - gw * 0.0, y0, 2.6, 2.8, 2.0, scheme.accent, false);
  b.withMat(MAT.wood, () => faceQuad(b, GF.south, glazeTo + 0.9, entU - 1.7, y0 + 0.1, y0 + GH - 0.2, TIMBER, 0, 0.1));
  canopy(ctx, GF.south, entU, 5.6, y0 + 3.15, 2.8, { columns: false });
  b.halo(gx + entU, y0 + 2, gz + gd + 1.2, 5, WARM, 0.85);
  b.halo(gx + gw * 0.25, y0 + 2, gz + gd + 1.0, 6, WARM, 0.55);
  // Path from the gate and steps.
  b.plane(gx + entU - 1.1, 0.02, gz + gd, 2.2, D * 0.34 + 4, scale(scheme.stone, 1.12));

  // Garden: swimming pool with coping, a deck and loungers on the east side; palms and shrubs round the house.
  const pw = 7.5, pd = 3.4, ppx = gx + gw + 1.8, ppz = gz + 2.0;
  b.box(ppx - 0.9, 0.02, ppz - 0.9, pw + 1.8, 0.14, pd + 1.8, hex('#e1dccf'));
  b.box(ppx - 0.25, 0.08, ppz - 0.25, pw + 0.5, 0.2, pd + 0.5, hex('#f0ece0'));
  b.withMat(MAT.water, () => b.plane(ppx, 0.26, ppz, pw, pd, WATER, 0.12));
  if (!ctx.lean) {
    for (let i = 0; i < 3; i++) {
      b.box(ppx + 0.4 + i * 1.3, 0.16, ppz + pd + 1.0, 0.7, 0.3, 1.9, hex('#f2efe7'), { grad: 0.1 });
      b.box(ppx + 0.4 + i * 1.3, 0.46, ppz + pd + 1.0, 0.7, 0.3, 0.6, hex('#e6e2d8'), { grad: 0.1 });
    }
    stamp(b, treeTemplate('datePalm', 0), { x: gx + gw + 4, z: gz + 7.2, y: 0.06, rot: 3, scale: 0.9 });
    stamp(b, treeTemplate('datePalm', 0), { x: gx - 3.2, z: gz + gd * 0.6, y: 0.06, rot: 1, scale: 0.9 });
    stamp(b, treeTemplate('olive', 1), { x: ppx + pw + 1.6, z: ppz + 1.5, y: 0.06, rot: 3, scale: 0.9 });
    for (let i = 0; i < 6; i++) leaf(ctx, gx + 1 + i * (gw / 6), 0.6, gz + gd + 1.1, 0.6, 0.5, 0.55, mix(hex('#2f6b43'), hex('#5a9a54'), rnd()));
    pennant(ctx, gx - 1.8, gz + gd + 3.2, 7.2);
  }

  // Gate wall with the sign board (a low stone wall across the plot front).
  const wallZ = gz + gd + D * 0.34 + 4.2;
  const FW = { ox: x0, oz: wallZ, tx: 1, tz: 0, nx: 0, nz: 1, len: W } as const;
  b.box(x0, 0, wallZ - 0.4, W, 0.9, 0.4, hex('#d8d2c4'), { grad: 0.1 });
  b.box(x0 + W * 0.5 - 3.2, 0, wallZ - 0.6, 6.4, 3.3, 0.6, scale(scheme.stone, 1.05), { grad: 0.15 });
  const sign = signBoard(ctx, FW, W / 2, 1.7, 5.4, 1.3, 0.04);

  return finish(ctx, {
    kind: 'personalVilla', footprint: { width: W, depth: D }, height: uy + UH + 0.3,
    anchor: { x: 0, y: uy + UH + 4.2, z: gz + gd * 0.4 },
    door: { x: 0, y: 0, z: wallZ + 8 },
    sign, ground: [x0 - 8, z0 - 3, x0 + W + 16, wallZ + 9],
  }, params);
}

const GRAPHITE_FRAME = hex('#222a33');
