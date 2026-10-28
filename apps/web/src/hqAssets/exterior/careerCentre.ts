/**
 * Career centre (owner decision O2: career is its own building kind). A two-storey learning block in two wings with
 * ribbon windows and deep sunshades, joined by a glazed barrel-vault atrium that is the entrance hall; a free-standing
 * sign pylon on the forecourt (the only building type whose sign stands on its own, so the career building reads from
 * the plaza); and a courtyard with benches and planting in front. Everything is decoration: a bench is furniture,
 * not someone waiting for an interview.
 *
 * Loaded with the lazy plan kit (`lib/hqPlanKit.ts` `careerDrawer`, fetched when a career building is on the campus), so it is not
 * one of the always-loaded `EXTERIOR_BUILDERS`: it belongs to the admin block family (same window rules and kind) and the
 * campus draws the admin block for a career building until this arrives. A building drawn from its floor plans keeps the
 * plan shell, like every other planned building.
 */
import { hex, scale } from '../../lib/hqGeometry.ts';
import { WARM, bench, bush, castShadow, hedge, planter, solarRow } from '../../lib/hqProps.ts';
import { atriumVault, beaconLight, louvres } from './facadeKit.ts';
import { STOREY, clamp, entrance, faceBox, faceQuad, fins, finish, ledge, makeCtx, paneRow, rectFaces, signBoard } from './parts.ts';
import { accentBand, slabBand } from './towerParts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

/** The career colour: a warm amber accent band, kept in step with `campusBuildings.ts` ACCENT.career. */
const AMBER = hex('#e8a24a');
/** Triangle cap at default size and full detail (measured, with headroom; `test/hqReception.test.ts` enforces it). */
export const CAREER_TRIANGLE_CAP = 2600;

export function buildCareerCentre(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('adminBlock', params, 0.55);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 22, 48, 30), D = clamp(params.depth, 12, 24, 15);
  const levels = Math.round(clamp(params.levels, 1, 3, 2));
  const LH = STOREY, y0 = 0.45, H = Math.max(2, levels) * LH;
  const x0 = -W / 2, z0 = -D / 2, x1 = W / 2, z1 = D / 2;
  const accent = params.accent ?? AMBER;
  // Atrium in the middle third; the two wings either side.
  const aw = Math.min(10, W * 0.32), ax0 = -aw / 2, ax1 = aw / 2;
  const wingW = (W - aw) / 2;

  b.plane(x0 - 3, 0.01, z0 - 3, W + 6, D + 22, scale(scheme.stone, 0.62));
  castShadow(b, x0, z0, W, D, H + 1.5, 0.022);
  b.box(x0 - 0.4, 0, z0 - 0.4, W + 0.8, y0, D + 0.8, scheme.stone);
  for (const wx of [x0, ax1]) {
    b.box(wx, y0, z0, wingW, H, D, scheme.wall, { skip: ['top', 'bottom'], grad: 0.12 });
    const F = rectFaces(wx, z0, wingW, D);
    faceQuad(b, F.south, 0, F.south.len, y0, y0 + 0.9, scale(scheme.stone, 0.72), 0, 0.05);
    for (let l = 0; l < Math.max(2, levels); l++) {
      const ly = y0 + l * LH, wy0 = ly + 0.95, wy1 = ly + LH - 0.75;
      const lift = 0.55 + 0.45 * ((l + 1) / Math.max(2, levels));
      paneRow(ctx, F.south, 0.9, wingW - 0.9, wy0, wy1, Math.max(2, Math.floor((wingW - 2) / 2.2)), 0.12, lift);
      paneRow(ctx, F.north, 0.9, wingW - 0.9, wy0, wy1, Math.max(2, Math.floor((wingW - 2) / 2.2)), 0.12, lift);
      // Deep sunshades over the front windows, an amber reveal line under them.
      faceBox(b, F.south, 0.5, wingW - 0.5, wy1 + 0.1, wy1 + 0.28, 0, 1.1, scheme.trim);
      faceQuad(b, F.south, 0.5, wingW - 0.5, wy1 + 0.02, wy1 + 0.1, accent, 0.5, 0.06);
      ledge(ctx, F.north, 0.4, wingW - 0.4, wy1 + 0.08, 0.4, 0.08);
    }
    const end = wx === x0 ? F.west : F.east;
    paneRow(ctx, end, 1.0, D - 1.0, y0 + 0.95, y0 + LH - 0.75, Math.max(2, Math.floor((D - 2) / 2.4)), 0.12, 0.8);
    fins(ctx, F.south, 0.9, wingW - 0.9, y0 + 0.9, y0 + H, 3.6, 0.35, 0.2);
    slabBand(ctx, wx, z0, wingW, D, y0 + H, 0.5);
    accentBand(ctx, wx, z0, wingW, D, y0 + H + 0.5, 0.2, 0.7);
    b.plane(wx + 0.3, y0 + H + 0.52, z0 + 0.3, wingW - 0.6, D - 0.6, scale(scheme.roof, 0.9));
    if (!ctx.lean) solarRow(b, wx + 1.4, y0 + H + 0.5, z0 + 2, Math.max(2, Math.min(5, Math.floor((wingW - 3) / 2.4))), 2.0, 1.7);
  }
  if (!ctx.lean) {
    louvres(ctx, rectFaces(x0, z0, wingW, D).south, 0.5, wingW - 0.5, y0 + H - LH + 0.9, y0 + H - 0.5, 4, 0.8);
    louvres(ctx, rectFaces(ax1, z0, wingW, D).south, 0.5, wingW - 0.5, y0 + H - LH + 0.9, y0 + H - 0.5, 4, 0.8);
  }

  // Atrium: glass front and back walls up to the wings' roof line, then a glass barrel vault along the depth.
  const AF = rectFaces(ax0, z0, aw, D);
  b.box(ax0, y0, z0, aw, 0.3, D, scheme.trim);
  paneRow(ctx, AF.south, 0.3, aw - 0.3, y0 + 0.2, y0 + H - 0.2, Math.max(3, Math.floor(aw / 1.8)), 0.06, 1);
  paneRow(ctx, AF.north, 0.3, aw - 0.3, y0 + 0.2, y0 + H - 0.2, Math.max(3, Math.floor(aw / 1.8)), 0.06, 1);
  atriumVault(ctx, 0, z0, aw, D, y0 + H, Math.min(3.2, aw * 0.3), ctx.lean ? 4 : 6, ctx.lean ? 2 : 4);
  entrance(ctx, AF.south, aw / 2, y0, 3.2, 3.0, 3.0, accent, true);
  b.halo(0, y0 + 2.4, z1 + 1.4, 6, WARM, 0.85);
  beaconLight(ctx, x1 - 1.2, y0 + H + 0.5, z1 - 1.2);

  // Forecourt: a free-standing sign pylon left of the path, with an amber edge light.
  const py = 0, pz = z1 + 6.5, px = -aw / 2 - 4.5;
  b.box(px - 0.5, py, pz - 0.4, 1.0, 5.6, 0.8, scale(scheme.stone, 1.05), { grad: 0.15 });
  b.box(px - 3.1, py + 3.0, pz - 0.35, 6.2, 2.2, 0.7, hex('#1b2733'), { grad: 0.1 });
  b.box(px - 3.1, py + 5.2, pz - 0.38, 6.2, 0.1, 0.76, accent, { emissive: 0.9, shade: false });
  const PF = { ox: px - 3.1, oz: pz + 0.35, tx: 1, tz: 0, nx: 0, nz: 1, len: 6.2 } as const;
  const sign = signBoard(ctx, PF, 3.1, py + 3.25, 5.6, 1.5, 0.04);
  b.halo(px, 1.2, pz + 1.2, 3.5, WARM, 0.7);

  // Path and a shaded courtyard: paving, benches under trees, hedges and planters along the wings.
  b.plane(-1.6, 0.02, z1, 3.2, 14, scale(scheme.stone, 1.12));
  if (!ctx.lean) {
    hedge(b, x0 + 1, z1 + 1.0, wingW - 2, 0.8, 0.7);
    hedge(b, ax1 + 1, z1 + 1.0, wingW - 2, 0.8, 0.7);
    for (const sx of [ax0 - 1.6, ax1 + 1.6]) planter(b, sx, z1 + 3.0, 1.2, rnd);
    bench(b, ax1 + 4.5, z1 + 6.2, 'x');
    bench(b, ax1 + 8.5, z1 + 6.2, 'x');
    for (let i = 0; i < 3; i++) bush(b, ax1 + 5 + i * 1.6, z1 + 8.6, 0.7, rnd);
    for (let i = 0; i < 3; i++) bush(b, x0 + 2 + i * 1.6, z1 + 8.2, 0.7, rnd);
  }
  return finish(ctx, {
    kind: 'adminBlock', footprint: { width: W, depth: D }, height: y0 + H + 0.7,
    anchor: { x: 0, y: y0 + H + 5.2, z: 0 },
    door: { x: 0, y: 0, z: z1 + 12 },
    sign, ground: [x0 - 3, z0 - 3, x1 + 3, z1 + 14],
  }, params);
}
