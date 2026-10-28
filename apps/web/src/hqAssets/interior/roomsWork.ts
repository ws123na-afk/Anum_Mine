/**
 * Working rooms: open office, customer care and operations. These draw fixtures (storage, screens,
 * plants, shared equipment) and the workstations the caller passes in. They never invent a desk, and
 * a person is drawn only for a station that carries an `occupantSeed`.
 */
import { hex, mix, scale } from '../../lib/hqGeometry.ts';
import { WARM, anumMark } from '../../lib/hqProps.ts';
import {
  BLACK, bookcase, coatRack, credenza, floorLamp, printerStand, rug, sideChair, sofa, tallPlant, waterCooler, wallDisplay, whiteboard,
  workstation, roundTable, type StationLook,
} from './furniture.ts';
import { contact } from './local.ts';
import type { RoomCtx } from './types.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export function drawStations(c: RoomCtx, look: StationLook, extra: { monitors?: 1 | 2 | 3; headset?: boolean } = {}): void {
  for (const st of c.stations) {
    workstation(c.b, c.y, { monitors: extra.monitors, headset: extra.headset, ...st }, c.S, { floor: c.floor, plant: false, lite: c.lite, ...look });
  }
}

export function openOffice(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, b, y, floor } = c;
  // Storage wall at the back: two bookcases and a low credenza with a printer.
  bookcase(R, 1.0, 0.3, 1.5, 2.1, S, rnd);
  if (w >= 6) credenza(R, w * 0.5, 0.35, 1.8, S, scale(S.marble, 0.95));
  if (w >= 6) printerStand(R, w * 0.5 + 1.6, 0.4, S);
  if (w >= 6) {
    whiteboard(R, w * 0.3 - 0.4, 1.0, 0.1, w * 0.2, 0.95, S);
    wallDisplay(R, w * 0.74, 1.1, 0.1, clamp(w * 0.16, 1.0, 1.6), 0.8, false, S);
  }
  // Hot-desk lockers on the east wall (only decoration: no names).
  const lockers = clamp(Math.floor((d - 1.6) / 0.42), 0, 8);
  for (let i = 0; i < lockers; i++) {
    const tone = i % 3 === 0 ? scale(S.accent, 0.6) : i % 3 === 1 ? S.metal : scale(S.metal, 0.88);
    R.box(w - 0.5, 0, 1.0 + i * 0.42, 0.4, 1.7, 0.4, tone, { grad: 0.1, skip: ['bottom'] });
    R.box(w - 0.505, 1.0, 1.1 + i * 0.42, 0.012, 0.1, 0.18, S.dark);
  }
  waterCooler(R, w - 0.45, 0.6);
  coatRack(R, 0.4, d - 0.5, S);
  tallPlant(R, w - 0.5, d - 0.6, S, 1.1, 1);
  tallPlant(R, 0.5, d * 0.5, S, 1, 2);
  rug(b, c.frame.x + 0.8, y, c.frame.z + d * 0.18, w - 2.2, d * 0.7, mix(S.rug, accent, 0.08), scale(S.rug, 0.8));
  contact(b, c.frame.x + w - 0.6, c.frame.z + 0.9, 0.5, lockers * 0.42 + 0.2, y, floor);
  drawStations(c, { plant: false, chairTone: mix(S.fabric, hex('#14181d'), 0.6) });
  return 0;
}

export function customerCare(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, b, y } = c;
  // Acoustic wall: felt panels in two tones with the ANUM mark and a wide display.
  const pw = clamp((w - 0.6) / 6, 0.6, 1.2);
  for (let i = 0; i < 6; i++) {
    const px = 0.3 + i * pw;
    if (px + pw > w - 0.2) break;
    R.box(px, 0.9, 0.1, pw - 0.04, 1.6, 0.05, i % 2 ? mix(S.fabric, hex('#000000'), 0.15) : S.fabricAlt, { grad: 0.1 });
  }
  wallDisplay(R, w / 2, 1.2, 0.17, clamp(w * 0.34, 1.4, 3.0), clamp(w * 0.34, 1.4, 3.0) * 0.42, c.live, S);
  const [mx, mz] = R.pt(w * 0.12, 0.2);
  anumMark(b, mx, y + 1.9, mz, 0.5, accent, 0.9);
  // A quiet pod with two acoustic walls and a stool, and a two-seat sofa nook with a side table.
  if (w >= 7 && d >= 5) {
    // Quiet pod on the east side: two low acoustic panels and a stool; the sofa nook sits at the front left.
    R.box(w - 2.4, 0, 0.3, 0.08, 1.5, 1.5, S.fabric, { grad: 0.1 });
    R.box(w - 2.4, 0, 0.3, 1.9, 1.5, 0.08, S.fabric, { grad: 0.1 });
    R.box(w - 2.4, 1.5, 0.3, 0.08, 0.03, 1.5, WARM, { emissive: 0.8, shade: false });
    sideChair(R, w - 1.3, 1.1, 2, S.fabricAlt, S.dark);
    R.box(w - 1.9, 0.7, 0.6, 0.6, 0.04, 0.4, S.wood);
    R.box(w - 1.8, 0, 0.7, 0.04, 0.7, 0.04, S.dark);
    sofa(R, 1.4, d - 0.9, 0, 1.7, S.fabric, S.dark);
    roundTable(R, 2.9, d - 0.8, 0.28, 0.42, S.wood, S.dark);
  }
  waterCooler(R, 0.4, 0.55);
  printerStand(R, 1.6, 0.45, S);
  tallPlant(R, 0.5, d - 0.6, S, 1.05, 1);
  tallPlant(R, w - 0.5, d * 0.5, S, 1, 2);
  floorLamp(R, w - 0.5, d * 0.35, S, 1.6);
  rug(b, c.frame.x + 0.6, y, c.frame.z + d * 0.2, w - 1.2, d * 0.6, mix(S.rug, accent, 0.1), scale(S.rug, 0.75));
  void rnd;
  drawStations(c, { plant: false, chairTone: mix(S.fabric, hex('#14181d'), 0.3) }, { monitors: 2, headset: true });
  return 0;
}

export function operationsRoom(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, b, y, live } = c;
  // Video wall: a 3 x 2 grid of displays on the back wall; the glow shows only for a live room.
  const cols = clamp(Math.floor((w - 1.2) / 1.5), 2, 4), rows = 2;
  const cw = clamp((w - 1.2) / cols, 1.1, 1.7), ch = cw * 0.56;
  const x0 = (w - cols * cw) / 2, y0 = 0.7;
  R.box(x0 - 0.1, y0 - 0.1, 0.08, cols * cw + 0.2, rows * ch + 0.2, 0.08, BLACK);
  for (let r = 0; r < rows; r++) {
    for (let k = 0; k < cols; k++) {
      const px = x0 + k * cw, py = y0 + r * ch;
      R.box(px + 0.02, py + 0.02, 0.16, cw - 0.04, ch - 0.04, 0.012, live ? mix(hex('#0c3a46'), accent, 0.35 + 0.1 * ((r + k) % 2)) : hex('#12202c'), { emissive: live ? 0.85 : 0.2, shade: false });
      if (live) {
        // Abstract map lines (a grid and one route), not data.
        R.box(px + 0.08, py + ch * 0.5, 0.176, cw - 0.16, 0.01, 0.004, hex('#9be9d8'), { emissive: 0.9, shade: false });
        R.box(px + cw * 0.5, py + 0.08, 0.176, 0.01, ch - 0.16, 0.004, hex('#9be9d8'), { emissive: 0.9, shade: false });
        R.box(px + cw * 0.2, py + ch * 0.25, 0.176, cw * 0.35, 0.012, 0.004, hex('#e8fff9'), { emissive: 1, shade: false });
      }
    }
  }
  // The whole wall takes one live face, laid over the tiles (the lazy screens chunk draws it).
  const wx0 = x0, wx1 = x0 + cols * cw, wy1 = y0 + rows * ch;
  b.screens.push({ kind: 'wall', id: '', p: [...R.w([wx0, y0, 0.185]), ...R.w([wx1, y0, 0.185]), ...R.w([wx1, wy1, 0.185]), ...R.w([wx0, wy1, 0.185])] });
  R.box(x0 - 0.1, y0 - 0.14, 0.08, cols * cw + 0.2, 0.04, 0.2, accent, { emissive: live ? 0.9 : 0.35, shade: false });
  // Side wall rack and server cabinet with steady status lights, and a briefing corner.
  const rx = w - 0.9;
  R.box(rx, 0, 0.35, 0.7, 2.1, 0.8, hex('#1b222a'), { grad: 0.08 });
  R.box(rx + 0.05, 0.1, 1.17, 0.6, 1.9, 0.012, hex('#2a343e'));
  for (let i = 0; i < 9; i++) {
    R.box(rx + 0.1, 0.2 + i * 0.2, 1.185, 0.5, 0.06, 0.008, hex('#323f4b'));
    R.box(rx + 0.55, 0.2 + i * 0.2 + 0.02, 1.19, 0.03, 0.02, 0.006, hex('#58f0cf'), { emissive: 0.8, shade: false });
  }
  const bx = clamp(w * 0.2, 1.6, 2.6), bz = clamp(d * 0.8, 3.4, d - 1.2);
  roundTable(R, bx, bz, 0.6, 0.74, S.wood, S.dark);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    sideChair(R, bx + Math.cos(a) * 0.95, bz + Math.sin(a) * 0.95, ((Math.round((a + Math.PI / 2) / (Math.PI / 2)) % 4) + 4) % 4, S.fabric, S.dark);
  }
  // A cable trunk along the floor in the accent colour and a coat stand.
  R.box(0.3, 0.012, d * 0.55, w - 0.6, 0.02, 0.1, S.dark);
  R.box(0.3, 0.032, d * 0.55 + 0.04, w - 0.6, 0.006, 0.02, accent, { emissive: 0.6, shade: false });
  rug(b, c.frame.x + 0.5, y, c.frame.z + d * 0.32, w - 1.0, d * 0.5, mix(S.rug, accent, 0.05), scale(S.rug, 0.7));
  tallPlant(R, 0.5, 0.7, S, 1, 3);
  tallPlant(R, w - 0.5, d - 0.6, S, 1, 1);
  floorLamp(R, w - 0.5, d * 0.4, S, 1.5);
  void rnd;
  drawStations(c, { plant: false, chairTone: mix(S.fabric, hex('#14181d'), 0.45) }, { monitors: 3 });
  return 0;
}

