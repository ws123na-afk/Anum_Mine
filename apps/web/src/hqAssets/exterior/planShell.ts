/**
 * A building shell generated from the real floor plans of a registry building. Storey n stands on the footprint of
 * floor n (its rooms, a margin and the shared service core, as the interior pipeline draws that slab), at the one
 * storey pitch of the interior stack, so an upper floor that is smaller than the one below sets back and a floor that is
 * larger overhangs. The service core rises above the roof as a lift tower. The silhouette of the building kind (the
 * executive crown and portico, the tower crown, the shop canopy, workshop bays, warehouse docks, the villa terrace) is
 * decoration on top of that footprint.
 *
 * Every window pane is tagged with the building's slot in the plan atlas (`tagPaneUv`), and the glass shader looks
 * through the pane into the atlas: the room behind a pane is the room that sits there on that floor.
 * Nothing here encodes a name, a count or a status.
 */
import { MAT, VERTEX_FLOATS, hex, scale } from '../../lib/hqGeometry.ts';
import { WARM } from '../../lib/hqProps.ts';
import { CORE_DEPTH, CORE_WIDTH } from '../interior/core.ts';
import { PLINTH, STOREY_PITCH, type PlanFrame, type PlanStorey } from '../../lib/hqPlanFrame.ts';
import { beaconLight, canopy, louvres, roofDeck } from './facadeKit.ts';
import { curtainWall, entrance, faceBox, faceQuad, finish, makeCtx, rectFaces, signBoard, type Ctx } from './parts.ts';
import { accentBand, crown, slabBand, terrace } from './towerParts.ts';
import type { ExteriorAsset, ExteriorKind, ExteriorParams } from './types.ts';

/** Each pane keeps its local 0..1 coordinates; the integer part of u says which plan atlas row it belongs to (2 * (row + 1)). */
export const PANE_TAG_STEP = 2;

/** Writes the atlas row of a building into the texture coordinates of every window pane of a mesh (in place). Panes above `maxY` (a crown lantern) keep the generic room. */
export function tagPaneUv(vertices: Float32Array, row: number, maxY = Infinity): void {
  for (let i = 0; i < vertices.length; i += VERTEX_FLOATS) {
    if (vertices[i + 1] <= maxY && vertices[i + 9] >= 2 && (vertices[i + 11] > 0 || vertices[i + 12] > 0) && vertices[i + 11] < PANE_TAG_STEP) vertices[i + 11] += PANE_TAG_STEP * (row + 1);
  }
}

/** The atlas row a pane's texture coordinate says (-1 for an untagged pane). Same arithmetic as the shader. */
export const paneRowOf = (u: number): number => Math.floor((u + 0.05) / PANE_TAG_STEP) - 1;

const largestStrip = (outer: PlanStorey['rect'], inner: PlanStorey['rect'] | null): { x: number; z: number; w: number; d: number } | null => {
  if (!inner) return { x: outer.x0, z: outer.z0, w: outer.x1 - outer.x0, d: outer.z1 - outer.z0 };
  const strips = [
    { x: outer.x0, z: inner.z1, w: outer.x1 - outer.x0, d: outer.z1 - inner.z1 },
    { x: outer.x0, z: outer.z0, w: outer.x1 - outer.x0, d: inner.z0 - outer.z0 },
    { x: outer.x0, z: outer.z0, w: inner.x0 - outer.x0, d: outer.z1 - outer.z0 },
    { x: inner.x1, z: outer.z0, w: outer.x1 - inner.x1, d: outer.z1 - outer.z0 },
  ].filter((s) => s.w >= 5 && s.d >= 5);
  strips.sort((a, b) => b.w * b.d - a.w * a.d);
  return strips[0] ?? null;
};

export type PlanShellParams = ExteriorParams & Readonly<{ row: number }>;

export function buildPlanShell(kind: ExteriorKind, frame: PlanFrame, params: PlanShellParams): ExteriorAsset {
  const ctx: Ctx = makeCtx(kind, params, 1);
  const { b, scheme } = ctx;
  const { cx, cz } = frame;
  const W = frame.w, D = frame.d;
  const lx = (x: number) => x - cx, lz = (z: number) => z - cz;
  const x0 = -W / 2, z0 = -D / 2, z1 = D / 2;
  const last = frame.storeys[frame.storeys.length - 1];

  // Plinth, forecourt paving.
  b.box(x0 - 0.8, 0, z0 - 0.8, W + 1.6, PLINTH, D + 1.6, scheme.stone);
  b.plane(x0 - 3, 0.012, z1 + 0.8, W + 6, 9, scale(scheme.stone, 0.64));

  // One glass storey per floor plan, on its own footprint.
  for (const s of frame.storeys) {
    const r = s.rect;
    const x = lx(r.x0), z = lz(r.z0), w = r.x1 - r.x0, d = r.z1 - r.z0;
    const y = PLINTH + s.n * STOREY_PITCH;
    curtainWall(ctx, x, z, w, d, y, 1, STOREY_PITCH, { colW: 2.2, inset: 0.7 });
    // The slab edge doubles as the roof of this storey where nothing stands on it.
    slabBand(ctx, x, z, w, d, y + STOREY_PITCH - 0.2, 0.45);
  }
  const roofY = PLINTH + frame.storeys.length * STOREY_PITCH + 0.25;

  // The service core rises above the roof as a lift tower.
  if (last.core) {
    const kx = lx(last.core.x), kz = lz(last.core.z);
    b.box(kx, roofY - 0.2, kz, CORE_WIDTH, 3.4, CORE_DEPTH * 0.5, scale(scheme.wall, 0.96), { grad: 0.12 });
    b.box(kx - 0.1, roofY + 3.2, kz - 0.1, CORE_WIDTH + 0.2, 0.3, CORE_DEPTH * 0.5 + 0.2, scheme.accent, { emissive: 0.6, shade: false });
  }

  // Setbacks: a roof deck on the biggest free strip of a storey that has a smaller one above it.
  frame.storeys.forEach((s, i) => {
    const next = frame.storeys[i + 1] ?? null;
    if (!next || ctx.lean) return;
    const strip = largestStrip(s.rect, next.rect);
    if (!strip) return;
    const y = PLINTH + (s.n + 1) * STOREY_PITCH + 0.25;
    roofDeck(ctx, lx(strip.x) + 0.4, lz(strip.z) + 0.4, strip.w - 0.8, strip.d - 0.8, y, { pool: kind === 'executiveHq' || kind === 'officeTower', pergola: kind === 'adminBlock', loungers: 2, shrubs: 3 });
  });

  // Ground storey: front face, lit entrance, canopy, sign board.
  const g = frame.storeys[0].rect;
  const gx = lx(g.x0), gz = lz(g.z0), gw = g.x1 - g.x0;
  const front = rectFaces(gx, gz, gw, g.z1 - g.z0).south;
  const uc = gw / 2;
  const grand = kind === 'executiveHq';
  entrance(ctx, front, uc, PLINTH, grand ? 4.4 : 3.4, 3.4, 2.8, scheme.accent, false);
  const canopyW = kind === 'retailBlock' ? Math.min(gw * 0.8, 20) : grand ? Math.min(gw * 0.5, 16) : 8;
  canopy(ctx, front, uc, canopyW, PLINTH + 3.7, 3.4);
  const signY = PLINTH + STOREY_PITCH + 0.55;
  const sign = signBoard(ctx, front, uc, signY, Math.min(grand ? 14 : 9, gw - 3), grand ? 1.5 : 1.3, 0.1);
  const doorZ = lz(g.z1);
  b.halo(gx + uc, PLINTH + 2.2, doorZ + 1.5, 6, WARM, 0.85);

  // Kind silhouette on top of the real footprint.
  const topRect = last.rect;
  const tx = lx(topRect.x0), tz = lz(topRect.z0), tw = topRect.x1 - topRect.x0, td = topRect.z1 - topRect.z0;
  let top = roofY + (last.core ? 3.5 : 0.6);
  if (kind === 'executiveHq' || kind === 'officeTower') {
    accentBand(ctx, tx, tz, tw, td, roofY - 0.1, 0.22, 0.8);
    const cw = tw * 0.5, cd = td * 0.5;
    top = Math.max(top, crown(ctx, tx + tw * 0.4, tz + td * 0.5, cw, cd, roofY, grand));
  } else if (kind === 'adminBlock') {
    const f = rectFaces(tx, tz, tw, td).south;
    louvres(ctx, f, 0.8, f.len - 0.8, roofY - 1.2, roofY - 0.2, 3, 0.7);
    terrace(ctx, tx - 0.3, tz - 0.3, tw + 0.6, td + 0.6, roofY, { x: tx, z: tz, w: 0, d: 0 });
    top = roofY + 1.6;
    beaconLight(ctx, tx + tw - 1, roofY + 0.9, tz + 1);
  } else if (kind === 'retailBlock') {
    accentBand(ctx, tx, tz, tw, td, roofY - 0.1, 0.3, 0.9);
    top = roofY + 0.9;
  } else if (kind === 'tyreWorkshop' || kind === 'warehouse') {
    // Roller bays on the ground storey front, yellow bumpers and a lit lamp over each.
    const bays = Math.max(2, Math.min(4, Math.floor(gw / 7)));
    for (let i = 0; i < bays; i++) {
      const u = ((i + 0.5) * gw) / bays + (i === Math.floor(bays / 2) ? 0 : 0);
      if (Math.abs(u - uc) < 3.4) continue;
      faceBox(b, front, u - 1.8, u + 1.8, PLINTH, PLINTH + 3.6, 0.05, 0.2, hex('#e8c34a'));
      b.withMat(MAT.roller, () => faceQuad(b, front, u - 1.6, u + 1.6, PLINTH, PLINTH + 3.4, hex('#d5dbe0'), 0, 0.22));
      faceQuad(b, front, u - 0.2, u + 0.2, PLINTH + 3.7, PLINTH + 3.85, WARM, 1, 0.24);
    }
    accentBand(ctx, tx, tz, tw, td, roofY - 0.1, 0.26, 0.7);
    top = roofY + 1.0;
  } else {
    // Personal villa: a roof terrace with a plunge pool and planting.
    roofDeck(ctx, tx + 0.6, tz + 0.6, tw - 1.2, td - 1.2, roofY, { pool: true, pergola: true, loungers: 2, shrubs: 4 });
    top = roofY + 2.8;
  }

  return finish(ctx, {
    kind, footprint: { width: W, depth: D }, height: top,
    anchor: { x: 0, y: top + 3, z: 0 },
    door: { x: gx + uc, y: 0, z: doorZ + 9 },
    sign, ground: [x0 - 3, z0 - 3, x0 + W + 3, z1 + 10],
  }, params);
}
