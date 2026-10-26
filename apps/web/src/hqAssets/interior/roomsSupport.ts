/**
 * Support rooms: server and engineering area, pantry, restrooms, and the finance and sales
 * variants of the open office. Same frame as every kit: local x east, z south, door in the middle of
 * the front wall (z = depth), back wall at z 0. Nothing here places a person, and the only motion
 * is scenery: rack status lights that blink and a cooling fan that turns (see `ambient.ts`).
 */
import { hex, mix, scale } from '../../lib/hqGeometry.ts';
import { WARM } from '../../lib/hqProps.ts';
import { ambientTag } from './ambient.ts';
import { BLACK, bookcase, credenza, deskPlant, floorLamp, rug, tallPlant, wallDisplay, whiteboard, waterCooler, sideChair, roundTable, barStool } from './furniture.ts';
import { drawStations, openOffice } from './roomsWork.ts';
import { contact, type Local } from './local.ts';
import type { RoomCtx } from './types.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const LED_GREEN = hex('#5cf2a3');
const LED_TEAL = hex('#58f0cf');
const LED_AMBER = hex('#ffc65c');

/** One 42U rack facing local +z: frame, mesh door, switch faces and two groups of status lights (the groups blink in turn). */
function rack(R: Local, lx: number, lz: number, w: number, h: number, c: RoomCtx, index: number): void {
  const { S, b } = c;
  const depth = 0.9;
  R.box(lx, 0, lz, w, h, depth, hex('#1b222a'), { grad: 0.08 });
  R.box(lx + 0.03, 0.08, lz + depth, w - 0.06, h - 0.16, 0.012, hex('#26313b'));
  // Equipment faces: a few thick units in two greys, each with a vent slot and one status light.
  const units = Math.floor((h - 0.3) / 0.34);
  for (let u = 0; u < units; u++) {
    const y = 0.14 + u * 0.34;
    R.box(lx + 0.06, y, lz + depth + 0.008, w - 0.12, 0.28, 0.012, (u + index) % 3 === 0 ? hex('#3a4651') : hex('#2d3842'));
    R.box(lx + 0.1, y + 0.1, lz + depth + 0.016, (w - 0.2) * 0.45, 0.012, 0.004, hex('#10161c'), { skip: ['top', 'north', 'east', 'west'] });
  }
  // Status lights blink in turn (scenery, they say nothing about what a unit is doing).
  for (let u = 0; u < units; u += 1) {
    const y = 0.14 + u * 0.34 + 0.15;
    const phase = (u + index) % 2;
    const colour = (u + index) % 5 === 0 ? LED_AMBER : (u + index) % 2 === 0 ? LED_GREEN : LED_TEAL;
    b.tagged(ambientTag('blink', 2.6 + ((index + u) % 3) * 0.7, 0, 'y', phase), () => {
      R.box(lx + w - 0.16, y, lz + depth + 0.02, 0.04, 0.03, 0.006, colour, { emissive: 1, shade: false, skip: ['top', 'north', 'east', 'west'] });
    });
  }
  R.box(lx - 0.01, h, lz - 0.01, w + 0.02, 0.03, depth + 0.02, S.dark);
}

/** Server and engineering area: rows of racks, a cable tray overhead, a cooling unit with a turning fan and a bench. */
export function serverRoom(c: RoomCtx): number {
  const { R, w, d, S, accent, b, y, lite } = c;
  const rw = 0.62;
  const perRow = clamp(Math.floor((w - 2.2) / (rw + 0.04)), 2, 9);
  const rows = d >= 6.4 ? 2 : 1;
  const x0 = 1.5 + Math.max(0, (w - 2.2 - perRow * (rw + 0.04)) / 2);
  let n = 0;
  for (let r = 0; r < rows; r++) {
    const rz = 0.45 + r * 2.7;
    for (let i = 0; i < perRow; i++) rack(R, x0 + i * (rw + 0.04), rz, rw, 2.0, c, n++);
    // Cable tray over the row and a cold-aisle light strip.
    R.box(x0 - 0.05, 2.3, rz + 0.1, perRow * (rw + 0.04) + 0.1, 0.06, 0.7, S.metal);
    R.box(x0, 2.36, rz + 0.1, perRow * (rw + 0.04), 0.012, 0.04, hex('#2a343e'));
    R.box(x0, 2.28, rz + 1.2, perRow * (rw + 0.04), 0.04, 0.12, hex('#d8f2ff'), { emissive: 0.9, shade: false });
    contact(b, c.frame.x + x0 - 0.1, c.frame.z + rz - 0.1, perRow * (rw + 0.04) + 0.2, 1.2, y, c.floor);
  }
  // Cooling unit on the west wall; its fan turns slowly.
  const cu = R.sub(0.5, clamp(d * 0.5, 1.8, d - 1.6), 1);
  cu.box(-0.5, 0, -0.45, 1.0, 2.0, 0.9, hex('#d9dde0'), { grad: 0.1 });
  cu.box(-0.46, 0.2, 0.446, 0.92, 1.6, 0.014, hex('#bcc4ca'));
  if (!lite) {
    const [fx, fz] = cu.pt(0, 0);
    // A two-blade fan laid on top of the unit: it turns about its own middle (kept to one part by the tag).
    b.tagged(ambientTag('spin', 2.2, 0, 'y', 0), () => {
      b.box(fx - 0.36, y + 2.04, fz - 0.06, 0.72, 0.012, 0.12, hex('#8d98a3'));
      b.box(fx - 0.06, y + 2.04, fz - 0.36, 0.12, 0.012, 0.72, hex('#8d98a3'));
      b.disc(fx, y + 2.06, fz, 0.07, 8, hex('#3a4651'));
    });
  }
  cu.box(-0.5, 2.0, -0.45, 1.0, 0.04, 0.9, hex('#aeb8c2'));
  // A service bench with a toolbox at the front, and a status wall display that is lit only for a live room.
  const bx = clamp(w - 2.6, x0, w - 2.2), bz = d - 1.0;
  R.box(bx, 0.74, bz, 1.8, 0.05, 0.7, S.wood, { grad: 0.08 });
  R.box(bx + 0.04, 0, bz + 0.04, 0.05, 0.74, 0.6, S.dark);
  R.box(bx + 1.71, 0, bz + 0.04, 0.05, 0.74, 0.6, S.dark);
  R.box(bx + 0.2, 0.79, bz + 0.2, 0.34, 0.14, 0.26, hex('#d94a3b'));
  sideChair(R, bx + 0.9, bz + 1.0 > d - 0.3 ? bz - 0.5 : bz + 0.9, 2, S.fabric, S.dark);
  wallDisplay(R, w * 0.55, 1.2, 0.1, clamp(w * 0.22, 1.2, 2.0), 0.7, c.live, S);
  R.box(0.2, 0.01, 0.2, w - 0.4, 0.006, 0.05, accent, { emissive: 0.6, shade: false });
  drawStations(c, { plant: false, chairTone: mix(S.fabric, hex('#14181d'), 0.4) }, { monitors: 2 });
  return 0;
}

/** Pantry: counter run with a sink and a hob, upper cabinets, a fridge, a coffee machine and a small table with stools. */
export function pantry(c: RoomCtx): number {
  const { R, w, d, S, accent, b, y, floor } = c;
  const run = clamp(w - 1.4, 2.2, 5.2);
  R.box(0.3, 0, 0.12, run, 0.88, 0.62, S.woodDark, { grad: 0.1 });
  R.box(0.26, 0.88, 0.1, run + 0.08, 0.05, 0.68, S.marble);
  // Sink with a tap, and a hob.
  R.box(0.7, 0.9, 0.2, 0.7, 0.012, 0.42, hex('#c9d1d9'), { shade: false });
  R.box(1.02, 0.93, 0.14, 0.04, 0.3, 0.04, hex('#aeb8c2'));
  R.box(1.02, 1.2, 0.14, 0.04, 0.04, 0.24, hex('#aeb8c2'));
  R.box(2.0, 0.93, 0.24, 0.55, 0.02, 0.38, BLACK);
  for (const [px, pz] of [[2.12, 0.34], [2.36, 0.34], [2.12, 0.5], [2.36, 0.5]] as const) R.prism(px, 0.95, pz, 0.06, 0.06, 0.008, 8, hex('#3a4651'));
  // Upper cabinets with glass-tone doors and a warm under-cabinet strip.
  for (let i = 0; i < Math.floor(run / 0.9); i++) {
    R.box(0.3 + i * 0.9, 1.5, 0.12, 0.88, 0.7, 0.32, i % 2 ? S.wood : scale(S.wood, 0.9), { grad: 0.1 });
    R.box(0.3 + i * 0.9 + 0.38, 1.7, 0.44, 0.12, 0.02, 0.012, S.brass);
  }
  R.box(0.3, 1.46, 0.16, run, 0.03, 0.28, WARM, { emissive: 0.9, shade: false });
  const [hx, hz] = R.pt(run * 0.4, 0.5);
  b.halo(hx, y + 1.3, hz, 1.8, WARM, 0.6);
  // Fridge at the end of the run, coffee machine on the counter.
  R.box(0.3 + run + 0.08, 0, 0.1, 0.72, 1.85, 0.68, hex('#dfe6ea'), { grad: 0.1 });
  R.box(0.3 + run + 0.12, 0.9, 0.78, 0.04, 0.5, 0.02, hex('#9aa5b1'));
  R.box(3.0, 0.93, 0.2, 0.34, 0.42, 0.34, hex('#2d3842'), { grad: 0.1 });
  R.box(3.08, 0.98, 0.54, 0.18, 0.1, 0.02, hex('#58f0cf'), { emissive: 0.8, shade: false });
  waterCooler(R, w - 0.45, 0.55);
  // A small round table with three stools on a rug, a bin and a plant.
  const tx = clamp(w * 0.5, 1.6, w - 1.4), tz = clamp(d * 0.62, 2.2, d - 1.1);
  rug(b, c.frame.x + tx - 1.1, y, c.frame.z + tz - 1.0, 2.2, 2.0, mix(S.rug, accent, 0.1), scale(S.rug, 0.8));
  roundTable(R, tx, tz, 0.55, 1.0, S.wood, S.dark);
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + 0.4;
    barStool(R, tx + Math.cos(a) * 0.8, tz + Math.sin(a) * 0.8, S.fabricAlt);
  }
  R.prism(w - 0.5, 0, d - 0.5, 0.17, 0.15, 0.5, 8, hex('#59656f'));
  tallPlant(R, 0.5, d - 0.6, S, 1, 2);
  contact(b, c.frame.x + 0.3, c.frame.z + 0.1, run + 1, 0.8, y, floor);
  return 0;
}

/** Restroom: a row of stalls with doors, a vanity with basins and mirrors, hand dryers and a bin. Pictograms are drawn by the shell sign, never here. */
export function restroom(c: RoomCtx): number {
  const { R, w, d, S, b, y, floor } = c;
  const stalls = clamp(Math.floor((w - 1.0) / 1.0), 2, 5);
  const sw = (w - 0.8) / stalls;
  const panel = hex('#8fa4b1');
  for (let i = 0; i <= stalls; i++) R.box(0.4 + i * sw - 0.02, 0.15, 0.1, 0.04, 1.8, 1.7, panel, { grad: 0.1 });
  for (let i = 0; i < stalls; i++) {
    const x = 0.4 + i * sw;
    // Toilet pan and cistern at the back of the stall, a door with a latch.
    R.box(x + sw / 2 - 0.2, 0, 0.14, 0.4, 0.3, 0.2, hex('#f1f4f6'), { grad: 0.1 });
    R.prism(x + sw / 2, 0, 0.62, 0.2, 0.17, 0.4, 8, hex('#f1f4f6'));
    R.box(x + 0.02, 0.15, 1.78, sw - 0.04, 1.75, 0.03, scale(panel, 1.08), { grad: 0.1 });
    R.box(x + sw - 0.2, 1.0, 1.81, 0.1, 0.04, 0.02, hex('#c9d1d9'));
  }
  // Vanity on the front-left with basins, taps, mirrors and a dryer.
  const vz = clamp(d - 0.9, 2.6, d - 0.6);
  const vw = clamp(w - 2.2, 1.6, 4.0);
  R.box(0.5, 0, vz - 0.3, vw, 0.84, 0.55, S.woodDark, { grad: 0.1 });
  R.box(0.46, 0.84, vz - 0.32, vw + 0.08, 0.04, 0.6, S.marble);
  const basins = clamp(Math.floor(vw / 0.8), 1, 4);
  for (let i = 0; i < basins; i++) {
    const bx = 0.5 + (i + 0.5) * (vw / basins);
    R.prism(bx, 0.88, vz - 0.05, 0.19, 0.16, 0.02, 10, hex('#f1f4f6'));
    R.box(bx - 0.02, 0.9, vz - 0.25, 0.04, 0.2, 0.04, hex('#aeb8c2'));
    R.box(bx - 0.28, 1.1, vz - 0.32, 0.56, 0.8, 0.02, hex('#c9e3ee'), { emissive: 0.35, shade: false });
    R.box(bx - 0.3, 1.08, vz - 0.33, 0.6, 0.84, 0.012, S.metal);
  }
  R.box(0.5 + vw + 0.2, 1.1, vz - 0.32, 0.22, 0.34, 0.12, hex('#dfe6ea'));
  R.prism(w - 0.4, 0, vz + 0.1, 0.14, 0.12, 0.36, 8, hex('#59656f'));
  R.box(0.3, 0.01, 1.85, w - 0.6, 0.006, 0.05, hex('#c9d1d9'), { shade: false });
  void deskPlant; void floorLamp; void credenza; void bookcase; void whiteboard;
  contact(b, c.frame.x + 0.4, c.frame.z + vz - 0.4, vw + 0.2, 0.9, y, floor);
  return 0;
}

/** Finance: the open office plus a filing wall, a safe and a ledger shelf. Static pieces only. */
export function financeOffice(c: RoomCtx): number {
  const { R, w, d, S, rnd } = c;
  openOffice(c);
  if (d >= 6) {
    // Two filing cabinets and a safe against the west wall, clear of the plant and the coat rack.
    for (let i = 0; i < 2; i++) {
      R.box(0.15, 0, d * 0.5 + 0.7 + i * 0.5, 0.5, 1.3, 0.46, hex('#9aa5b1'), { grad: 0.1 });
      for (let k = 0; k < 3; k++) R.box(0.655, 0.12 + k * 0.4, d * 0.5 + 0.78 + i * 0.5, 0.012, 0.03, 0.3, hex('#59656f'));
    }
    R.box(0.15, 0, d * 0.5 - 0.9, 0.6, 0.85, 0.6, hex('#2d3842'), { grad: 0.1 });
    R.prism(0.77, 0.4, d * 0.5 - 0.6, 0.1, 0.1, 0.02, 10, S.brass);
  }
  if (w >= 8) {
    // A statement chart on the back wall: abstract bars, not data.
    R.box(w * 0.46, 1.05, 0.11, 1.5, 0.9, 0.04, hex('#10161d'));
    for (let i = 0; i < 6; i++) R.box(w * 0.46 + 0.14 + i * 0.22, 1.12, 0.15, 0.12, 0.18 + ((i * 7) % 5) * 0.1, 0.012, i % 2 ? hex('#58f0cf') : hex('#f2c14e'), { emissive: 0.6, shade: false });
  }
  void rnd;
  return 0;
}

/** Sales: the open office plus a pipeline board of cards, a target board and a team table. Static pieces only. */
export function salesOffice(c: RoomCtx): number {
  const { R, w, d, S, accent } = c;
  openOffice(c);
  if (w >= 8) {
    // Pipeline board: four columns of paper cards on a dark board.
    R.box(w * 0.46, 1.0, 0.11, 2.0, 1.0, 0.04, hex('#1b2733'));
    const tones = [hex('#f2c14e'), hex('#58f0cf'), hex('#6aa7ff'), hex('#e8e4d8')];
    for (let col = 0; col < 4; col++) for (let k = 0; k < 3 - (col % 2); k++) R.box(w * 0.46 + 0.1 + col * 0.47, 1.62 - k * 0.28, 0.15, 0.4, 0.2, 0.01, tones[col], { emissive: 0.4, shade: false });
  }
  if (d >= 6) {
    // A square target board on the west wall: concentric squares, white, accent, white, red.
    const tz = d * 0.45;
    R.box(0.1, 1.0, tz - 0.4, 0.03, 0.8, 0.8, hex('#f4f6f8'));
    R.box(0.13, 1.1, tz - 0.3, 0.012, 0.6, 0.6, accent);
    R.box(0.142, 1.2, tz - 0.2, 0.012, 0.4, 0.4, hex('#f4f6f8'));
    R.box(0.154, 1.3, tz - 0.1, 0.012, 0.2, 0.2, hex('#c0392b'));
  }
  R.box(0.15, 0, d - 1.6, 0.5, 0.5, 0.5, S.fabricAlt);
  return 0;
}
