/**
 * Warehouse shed: clad walls with ribs and a plinth, a low gable roof with roof lights and vents,
 * roller doors on the front, a glazed office annex with the sign board, clerestory windows, and a
 * raised loading bay on the east side with dock doors, bumpers, a canopy and a backed-in truck.
 */
import { hex, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { WARM, acUnit, castShadow, truck } from '../../lib/hqProps.ts';
import { clamp, entrance, faceBox, faceQuad, finish, makeCtx, paneRow, rectFaces, signBoard, type Ctx, type Face } from './parts.ts';
import type { ExteriorAsset, ExteriorParams } from './types.ts';

const CLAD = hex('#c3c9ce');
const YELLOW = hex('#e8c34a');

function rollerDoor(ctx: Ctx, f: Face, u0: number, u1: number, h: number, color: Rgb): void {
  const { b } = ctx;
  faceBox(b, f, u0 - 0.2, u1 + 0.2, 0, h + 0.2, 0, 0.12, YELLOW);
  faceQuad(b, f, u0, u1, 0, h, color, 0, 0.14);
  for (let y = 0.5; y < h; y += 0.5) faceQuad(b, f, u0, u1, y, y + 0.06, scale(color, 0.55), 0, 0.15);
  faceBox(b, f, (u0 + u1) / 2 - 0.25, (u0 + u1) / 2 + 0.25, h + 0.35, h + 0.6, 0, 0.3, hex('#2a313b'));
  faceQuad(b, f, (u0 + u1) / 2 - 0.2, (u0 + u1) / 2 + 0.2, h + 0.2, h + 0.34, WARM, 1.0, 0.32);
}

export function buildWarehouse(params: ExteriorParams = {}): ExteriorAsset {
  const ctx = makeCtx('warehouse', params, 0.3);
  const { b, rnd, scheme } = ctx;
  const W = clamp(params.width, 24, 72, 42), D = clamp(params.depth, 16, 44, 26);
  const wallH = clamp(params.levels ? params.levels * 3.2 : undefined, 7, 14, 8.6);
  const x0 = -W / 2, z0 = -D / 2, x1 = W / 2, z1 = D / 2;
  const F = rectFaces(x0, z0, W, D);
  const clad = scale(CLAD, 0.95);
  const rib = scale(CLAD, 0.78);

  // Apron, plinth, shadow.
  b.plane(x0 - 3, 0.01, z0 - 3, W + 6, D + 14, scale(scheme.stone, 0.62));
  castShadow(b, x0, z0, W, D, wallH + 2.2, 0.022);
  b.box(x0 - 0.2, 0, z0 - 0.2, W + 0.4, 1.0, D + 0.4, scheme.trim, { skip: ['top', 'bottom'] });
  b.box(x0, 0, z0, W, wallH, D, clad, { skip: ['top', 'bottom'], grad: 0.18 });
  // Wall bands (colour block along the base and a trim line under the eaves).
  for (const f of [F.south, F.north]) {
    faceQuad(b, f, 0, f.len, 1.0, 1.9, scheme.accent, 0.06, 0.05);
    faceQuad(b, f, 0, f.len, wallH - 0.5, wallH, scale(clad, 0.86), 0, 0.05);
  }
  for (const f of [F.east, F.west]) faceQuad(b, f, 0, f.len, 1.0, 1.9, scheme.accent, 0.06, 0.05);
  // Vertical cladding ribs.
  for (const f of [F.south, F.north]) for (let u = 1.2; u < f.len - 0.6; u += 2.4) faceQuad(b, f, u, u + 0.16, 1.9, wallH - 0.5, rib, 0, 0.05);
  for (const f of [F.east, F.west]) for (let u = 1.2; u < f.len - 0.6; u += 2.6) faceQuad(b, f, u, u + 0.16, 1.9, wallH - 0.5, rib, 0, 0.05);
  // Clerestory strip high on the rear wall: dark glass by day, lit at night (the front carries the sign).
  paneRow(ctx, F.north, 1.5, F.north.len - 1.5, wallH - 2.5, wallH - 1.0, Math.floor((F.north.len - 3) / 3), 0.2, 1);

  // Gable roof along x with a deep eave.
  const ey = wallH, ry = wallH + Math.min(4.2, D * 0.16);
  const ov = 0.9;
  const rc = hex('#7f8a94');
  b.quad([x0 - ov, ey, z1 + ov], [x1 + ov, ey, z1 + ov], [x1 + ov, ry, 0], [x0 - ov, ry, 0], scale(rc, 1.0));
  b.quad([x1 + ov, ey, z0 - ov], [x0 - ov, ey, z0 - ov], [x0 - ov, ry, 0], [x1 + ov, ry, 0], scale(rc, 0.82));
  b.tri([x1, ey, z1], [x1, ey, z0], [x1, ry, 0], clad);
  b.tri([x0, ey, z0], [x0, ey, z1], [x0, ry, 0], scale(clad, 0.85));
  // Standing seams down both slopes.
  for (let x = x0 - ov + 1.5; x < x1 + ov - 1; x += 3) {
    b.quad([x, ey + 0.03, z1 + ov], [x + 0.14, ey + 0.03, z1 + ov], [x + 0.14, ry + 0.03, 0], [x, ry + 0.03, 0], scale(rc, 0.7));
    b.quad([x + 0.14, ey + 0.03, z0 - ov], [x, ey + 0.03, z0 - ov], [x, ry + 0.03, 0], [x + 0.14, ry + 0.03, 0], scale(rc, 0.6));
  }
  // Fascia along the eaves, ridge cap, translucent roof lights on both slopes.
  b.box(x0 - ov, ey - 0.05, z1 + ov - 0.1, W + ov * 2, 0.5, 0.2, scheme.trim);
  b.box(x0 - ov, ry - 0.05, -0.25, W + ov * 2, 0.35, 0.5, scheme.trim);
  const slope = (ry - ey) / (z1 + ov);
  const lights = Math.max(2, Math.floor(W / 11));
  for (let i = 0; i < lights; i++) {
    const lx = x0 + 3 + i * ((W - 6) / lights) + 0.5, lw = Math.min(5, (W - 6) / lights - 1.5);
    for (const sgn of [1, -1]) {
      const za = sgn * (z1 * 0.35), zb = sgn * (z1 * 0.62);
      const ya = ey + slope * (z1 + ov - Math.abs(za)) + 0.04, yb = ey + slope * (z1 + ov - Math.abs(zb)) + 0.04;
      const glass = hex('#8fc4d4'), glow = 0.12 + (rnd() < ctx.lit ? 0.35 : 0);
      // Wound so the pane faces up on either slope.
      if (sgn > 0) b.quad([lx, ya, za], [lx, yb, zb], [lx + lw, yb, zb], [lx + lw, ya, za], glass, glow);
      else b.quad([lx, ya, za], [lx + lw, ya, za], [lx + lw, yb, zb], [lx, yb, zb], glass, glow);
    }
  }
  // Ridge vents and a few roof units.
  const nv = Math.max(2, Math.floor(W / 14));
  for (let i = 0; i < nv; i++) {
    const vx = x0 + 5 + i * ((W - 10) / (nv - 1));
    b.box(vx, ry + 0.3, -0.6, 1.8, 0.7, 1.2, hex('#9aa4ae'));
    b.box(vx - 0.1, ry + 1.0, -0.8, 2.0, 0.12, 1.6, hex('#6e7782'));
  }
  if (!ctx.lean) acUnit(b, x1 - 7, ey + slope * (z1 * 0.2) + 0.1, z1 * 0.4, 2.6, 1.8);

  // Roller doors on the front, spread over the part of the front that is not the annex.
  const annexW = Math.min(10, W * 0.24);
  const doorsFrom = annexW + 3;
  const doorW = 4.4, doorH = 4.6;
  const nDoors = Math.max(2, Math.min(6, Math.floor((W - doorsFrom - 2) / (doorW + 3))));
  const span = (W - doorsFrom - 2) / nDoors;
  for (let i = 0; i < nDoors; i++) {
    const c = doorsFrom + span * (i + 0.5);
    rollerDoor(ctx, F.south, c - doorW / 2, c + doorW / 2, doorH, hex(i % 2 ? '#d5dadf' : '#c9d0d6'));
    if (!ctx.lean) b.box(x0 + c - 0.3, 0, z1 + 0.1, 0.18, 1.1, 0.18, YELLOW);
  }
  // Fascia sign band above the doors (the board for the real name).
  const sign = signBoard(ctx, F.south, doorsFrom + (W - doorsFrom - 2) / 2, wallH - 3.1, Math.min(14, W - doorsFrom - 6), 1.7, 0.02);

  // Glazed office annex, two storeys, standing proud of the front.
  const aD = 5.0, aH = 6.4;
  b.box(x0, 0, z1, annexW, aH, aD, hex('#27435f'), { grad: 0.45 });
  const AF = rectFaces(x0, z1, annexW, aD);
  for (const [lvl, y0] of [[0, 1.0], [1, 3.6]] as const) {
    paneRow(ctx, AF.south, 0.6, annexW - 0.6, y0 + 0.2, y0 + 2.1, Math.max(2, Math.floor(annexW / 2.1)), 0.14, 0.6 + 0.4 * lvl);
    paneRow(ctx, AF.east, 0.6, aD - 0.6, y0 + 0.2, y0 + 2.1, 2, 0.14, 0.7);
  }
  entrance(ctx, AF.south, annexW / 2, 0, 2.6, 2.7, 2.2, scheme.accent);
  b.box(x0 - 0.3, aH, z1 - 0.3, annexW + 0.6, 0.35, aD + 0.6, scheme.trim);

  // Loading bay on the east side: raised dock, doors, bumpers, canopy and a truck.
  const bayDoors = Math.max(2, Math.min(4, Math.floor(D / 7)));
  const bayLen = bayDoors * 5.2 + 1;
  const bz = -D / 2 + 3 + 0;
  const dockH = 1.25, dockD = 3.0;
  b.box(x1, 0, bz, dockD, dockH, bayLen, scale(scheme.stone, 0.92), { grad: 0.1 });
  b.box(x1 + dockD, 0, bz, 0.25, dockH, bayLen, YELLOW);
  const bayFace = { ...F.east, ox: x1, oz: bz + bayLen, len: bayLen };
  for (let i = 0; i < bayDoors; i++) {
    const c = 0.5 + 5.2 * (i + 0.5);
    faceBox(b, bayFace, c - 1.4, c + 1.4, dockH, dockH + 3.5, 0, 0.12, YELLOW);
    faceQuad(b, bayFace, c - 1.25, c + 1.25, dockH + 0.05, dockH + 3.4, hex('#cfd5da'), 0, 0.14);
    for (let y = dockH + 0.6; y < dockH + 3.4; y += 0.55) faceQuad(b, bayFace, c - 1.25, c + 1.25, y, y + 0.06, hex('#8a949c'), 0, 0.15);
    for (const s of [-1.55, 1.55]) faceBox(b, bayFace, c + s - 0.12, c + s + 0.12, dockH + 0.3, dockH + 1.3, 0, 0.35, hex('#1d2227'));
    faceQuad(b, bayFace, c - 0.2, c + 0.2, dockH + 3.6, dockH + 3.75, WARM, 1.0, 0.16);
  }
  // Canopy over the bay on posts.
  const cy = dockH + 5.4;
  b.box(x1, cy, bz - 0.4, dockD + 4.4, 0.35, bayLen + 0.8, scheme.trim);
  b.box(x1, cy + 0.35, bz - 0.4, dockD + 4.4, 0.1, 0.25, scheme.accent, { emissive: 0.5, shade: false });
  for (const pz of [bz - 0.2, bz + bayLen]) b.box(x1 + dockD + 4.0, 0, pz, 0.3, cy, 0.3, scheme.trim);
  if (!ctx.lean) {
    const tz = bz + 2.6 + 4.7;
    truck(b, x1 + dockD + 4.8, tz, hex('#2f6a8f'), 3);
    // Pallet stacks on the dock.
    for (let i = 0; i < 3; i++) {
      const pz = bz + 1.2 + i * 4.4;
      b.box(x1 + 0.5, dockH, pz, 1.2, 0.14, 1.2, hex('#a07d55'));
      b.box(x1 + 0.55, dockH + 0.14, pz + 0.05, 1.1, 0.9 + (i % 2) * 0.4, 1.1, hex('#c8a46a'), { grad: 0.12 });
    }
    // Yellow bollards along the front.
    for (let u = doorsFrom - 1; u < W - 1; u += 7) b.prism(x0 + u, 0, z1 + 1.8, 0.16, 0.16, 0.9, 6, YELLOW);
  }

  return finish(ctx, {
    kind: 'warehouse', footprint: { width: W, depth: D }, height: ry,
    anchor: { x: 0, y: ry + 2.4, z: 0 },
    door: { x: x0 + annexW / 2, y: 0, z: z1 + aD + 6 },
    sign, ground: [x0 - 3, z0 - 3, x1 + dockD + 12, z1 + 11],
  }, params);
}
