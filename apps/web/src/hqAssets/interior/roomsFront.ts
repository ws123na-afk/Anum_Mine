/**
 * Front-of-house rooms: reception, meeting room, lounge and executive suite. Local frame of every
 * builder: x east, z south, the door in the middle of the front wall (z = depth), back wall at z 0.
 * Nothing here places a person.
 */
import { hex, mix, scale } from '../../lib/hqGeometry.ts';
import { WARM, WINDOW_BASE, anumMark } from '../../lib/hqProps.ts';
import {
  BLACK, armchair, barStool, bookcase, coatRack, credenza, deskPlant, floorLamp, pouf, roundTable, rug, sideChair, sideTable,
  sofa, tableLamp, tallPlant, taskChair, wallDisplay, whiteboard,
} from './furniture.ts';
import { seatedFigure, standingFigure } from './figures.ts';
import { contact } from './local.ts';
import type { RoomCtx } from './types.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Slatted feature wall: a dark backing board with vertical timber slats and a warm cove light. */
function slatWall(c: RoomCtx, x: number, wd: number, h: number): void {
  const { R, S } = c;
  R.box(x, 0, 0.08, wd, h, 0.07, S.woodDark, { grad: 0.1 });
  const n = Math.max(6, Math.floor(wd / 0.16));
  const step = (wd - 0.08) / n;
  for (let i = 0; i < n; i++) {
    const tone = i % 4 === 0 ? scale(S.wood, 0.86) : i % 2 === 0 ? scale(S.woodDark, 1.18) : S.woodDark;
    R.box(x + 0.04 + i * step, 0.04, 0.15, step * 0.62, h - 0.04, 0.05, tone, { skip: ['bottom', 'north', 'top'] });
  }
  R.box(x, h, 0.08, wd, 0.05, 0.22, WARM, { emissive: 0.85, shade: false });
  R.box(x, 0, 0.08, wd, 0.06, 0.2, scale(S.woodDark, 0.6));
  R.box(x, 0.06, 0.2, wd, 0.025, 0.012, WARM, { emissive: 0.7, shade: false });
}

export function reception(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, floor, b, y } = c;
  const bw = Math.min(w * 0.86, 7.4), bx = (w - bw) / 2;
  slatWall(c, bx, bw, Math.min(2.7, 2.9));
  const [mx, mz] = R.pt(w / 2, 0.24);
  anumMark(b, mx, y + 1.55, mz, Math.min(1.1, bw * 0.16), accent, 0.95);
  R.box(w / 2 - Math.min(1.3, bw * 0.2), 0.62, 0.2, Math.min(2.6, bw * 0.4), 0.02, 0.03, S.brass, { emissive: 0.35 });

  // Marble reception desk with a fluted front, brass ledge and a low return.
  const cw = clamp(w * 0.5, 2.4, 4.4), dx = (w - cw) / 2, dz = clamp(d * 0.36, 1.6, 2.8);
  contact(b, c.frame.x + dx, c.frame.z + dz - 0.9, cw, 1.6, y, floor);
  R.box(dx, 0, dz, cw, 1.02, 0.6, S.marble, { grad: 0.1 });
  R.box(dx - 0.04, 1.02, dz - 0.05, cw + 0.08, 0.05, 0.72, scale(S.marble, 1.04));
  R.box(dx, 0.99, dz + 0.6, cw, 0.03, 0.02, S.brass, { emissive: 0.3 });
  const flutes = Math.floor(cw / 0.2);
  for (let i = 0; i < flutes; i++) R.box(dx + 0.1 + (i * (cw - 0.2)) / flutes, 0.08, dz + 0.6, 0.09, 0.88, 0.022, scale(S.marble, 0.88), { skip: ['bottom', 'north', 'top'] });
  R.box(dx, 0, dz + 0.6, cw, 0.05, 0.03, accent, { emissive: 0.75, shade: false });
  R.box(dx, 0, dz - 0.9, 0.55, 0.76, 1.0, S.marble, { grad: 0.1 });
  R.box(dx - 0.03, 0.76, dz - 0.93, 0.61, 0.04, 1.06, S.woodDark);
  // Working side: monitors seen from behind, keyboard tray, phone, bell, orchid.
  for (const mxp of [0.28, 0.62]) {
    R.box(dx + cw * mxp - 0.02, 1.07, dz + 0.22, 0.04, 0.2, 0.04, BLACK);
    R.box(dx + cw * mxp - 0.27, 1.25, dz + 0.2, 0.54, 0.33, 0.03, BLACK);
  }
  R.box(dx + cw * 0.82, 1.07, dz + 0.3, 0.2, 0.05, 0.14, S.dark);
  R.prism(dx + cw * 0.9, 1.07, dz + 0.42, 0.045, 0.04, 0.05, 8, S.brass);
  deskPlant(R, dx + cw * 0.08, 1.07, dz + 0.3, S);
  // The reception posts: each person the registry assigns to a desk of this room (and who is on shift) stands behind the
  // counter facing the door, spread along it. Nobody is drawn without such a record; the chair stays when nobody stands there.
  const staff = c.stations.filter((st) => st.occupantSeed).slice(0, Math.max(1, Math.floor(cw / 0.8)));
  staff.forEach((st, i) => {
    const [fx, fz] = R.pt(dx + (cw * (i + 1)) / (staff.length + 1), dz - 0.42);
    standingFigure(b, fx, y, fz, { seed: st.occupantSeed!, rot: 0, pose: 'neutral', headset: st.headset, look: st.look });
  });
  taskChair(R, staff.length > 0 ? dx + cw * 0.92 : dx + cw * 0.45, dz - 0.5, mix(S.fabric, hex('#14181d'), 0.5));
  // Waiting group on a rug: sofa, two armchairs, marble coffee table, lamp.
  const gx = clamp(w * 0.24, 2.4, 3.2), gz = Math.min(d - 1.55, clamp(d * 0.7, dz + 2.2, d));
  rug(b, c.frame.x + gx - 1.9, y, c.frame.z + gz - 1.45, 3.8, 2.9, mix(S.rug, accent, 0.18), scale(S.rug, 0.7));
  roundTable(R, gx, gz, 0.5, 0.42, S.marble, S.brass);
  R.box(gx - 0.2, 0.42, gz - 0.12, 0.24, 0.025, 0.18, hex('#c0504d'));
  R.box(gx - 0.17, 0.445, gz - 0.1, 0.22, 0.025, 0.17, hex('#e8e4d8'));
  R.prism(gx + 0.22, 0.42, gz + 0.1, 0.05, 0.035, 0.16, 6, hex('#8aa89a'));
  sofa(R, gx, gz - 1.05, 0, 2.0, S.fabric, S.dark);
  sofa(R, gx, gz + 1.0, 2, 1.8, mix(S.fabric, S.fabricAlt, 0.5), S.dark);
  armchair(R, gx - 1.5, gz, 3, S.fabricAlt, S.dark);
  armchair(R, gx + 1.5, gz, 1, S.fabricAlt, S.dark);
  sideTable(R, gx - 1.95, gz - 1.1, S);
  // The right side: a second quiet corner, plants, a floor lamp, coat stand.
  const qx = w * 0.8;
  armchair(R, qx, d * 0.74, 2, mix(S.fabric, S.fabricAlt, 0.3), S.dark);
  sideTable(R, qx + 0.85, d * 0.74 - 0.1, S);
  tallPlant(R, w - 0.5, 0.75, S, 1.1, 1);
  tallPlant(R, 0.5, 0.75, S, 1.1, 2);
  tallPlant(R, w - 0.5, d - 0.6, S, 1, 0);
  floorLamp(R, w - 0.5, dz + 0.4, S, 1.65);
  coatRack(R, 0.45, dz + 0.3, S);
  // Entrance mat in front of the door.
  const door = clamp(w * 0.3, 0.9, 1.2);
  R.box(w / 2 - door / 2, 0.01, d - 0.62, door, 0.012, 0.5, scale(S.dark, 1.3), { skip: ['bottom'], emissive: 0.2 });
  void rnd;
  return 0;
}

export function meetingRoom(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, floor, b, y, live } = c;
  const tw = clamp(w - 2.4, 1.8, 5.2), td = clamp(d * 0.34, 0.95, 1.5);
  const tx = (w - tw) / 2, tz = clamp(d * 0.52 - td / 2, 1.3, d - td - 1.0);
  contact(b, c.frame.x + tx - 0.3, c.frame.z + tz - 0.8, tw + 0.6, td + 1.6, y, floor);
  rug(b, c.frame.x + tx - 0.5, y, c.frame.z + tz - 1.0, tw + 1.0, td + 2.0, mix(S.rug, accent, live ? 0.3 : 0.1), scale(S.rug, 0.65));
  // Table: a long top with rounded ends, an inset leather edge and two slab bases.
  const topCol = live ? mix(S.wood, accent, 0.12) : S.wood;
  R.box(tx + td / 2, 0.7, tz, tw - td, 0.05, td, topCol, { grad: 0.06 });
  // The prism cap is drawn 5 percent lighter, so the ends are darkened to match the box top.
  const endCol = scale(topCol, 0.952);
  R.prism(tx + td / 2, 0.7, tz + td / 2, td / 2, td / 2, 0.05, 14, endCol);
  R.prism(tx + tw - td / 2, 0.7, tz + td / 2, td / 2, td / 2, 0.05, 14, endCol);
  R.box(tx + td / 2, 0.69, tz + 0.04, tw - td, 0.012, td - 0.08, scale(S.woodDark, 0.8));
  for (const f of [0.22, 0.78]) R.box(tx + tw * f - 0.07, 0, tz + td * 0.2, 0.14, 0.7, td * 0.6, S.woodDark);
  // Per seat: chair on each long side, notepad, glass; head chairs at both ends.
  const seats = clamp(Math.floor((tw - 0.4) / 0.72), 2, 8);
  const chairTone = mix(S.fabric, hex('#1b2733'), 0.35);
  for (let i = 0; i < seats; i++) {
    const sx = tx + 0.45 + (i * (tw - 0.9)) / Math.max(1, seats - 1);
    sideChair(R, sx, tz - 0.4, 0, chairTone, S.dark);
    sideChair(R, sx, tz + td + 0.4, 2, chairTone, S.dark);
    R.box(sx - 0.12, 0.75, tz + 0.08, 0.22, 0.012, 0.3, S.paper);
    R.box(sx - 0.12, 0.75, tz + td - 0.38, 0.22, 0.012, 0.3, S.paper);
    R.prism(sx + 0.17, 0.75, tz + 0.2, 0.03, 0.026, 0.09, 6, hex('#bfe0ee'));
    R.prism(sx + 0.17, 0.75, tz + td - 0.22, 0.03, 0.026, 0.09, 6, hex('#bfe0ee'));
  }
  sideChair(R, tx - 0.35, tz + td / 2, 3, chairTone, S.dark);
  sideChair(R, tx + tw + 0.35, tz + td / 2, 1, chairTone, S.dark);
  // The stored participants of the live meeting, seated in order: the near and far sides from the screen end, then the heads.
  // The chairs are furniture; a person is drawn only for a participant the meeting record lists.
  const chairs: Array<[number, number, number]> = [];
  for (let i = 0; i < seats; i++) {
    const sx = tx + 0.45 + (i * (tw - 0.9)) / Math.max(1, seats - 1);
    chairs.push([sx, tz - 0.4, 0], [sx, tz + td + 0.4, 2]);
  }
  chairs.push([tx - 0.35, tz + td / 2, 3], [tx + tw + 0.35, tz + td / 2, 1]);
  c.attendees.slice(0, chairs.length).forEach((a, i) => {
    const [lx, lz, rot] = chairs[i];
    const [fx, fz] = R.pt(lx, lz);
    seatedFigure(b, fx, y, fz, { seed: a.seed, rot, pose: i % 2 === 0 ? 'talking' : 'reading', look: a.look });
  });
  // Table centre: cable cubby, speaker puck with a ring that lights when live, a carafe.
  R.box(tx + tw / 2 - 0.2, 0.75, tz + td / 2 - 0.08, 0.4, 0.02, 0.16, BLACK);
  R.prism(tx + tw / 2 - 0.5, 0.75, tz + td / 2, 0.1, 0.1, 0.03, 10, S.dark);
  R.prism(tx + tw / 2 - 0.5, 0.78, tz + td / 2, 0.07, 0.07, 0.006, 10, live ? accent : hex('#59656f'), { emissive: live ? 1 : 0.1 });
  R.prism(tx + tw / 2 + 0.55, 0.75, tz + td / 2, 0.045, 0.04, 0.2, 8, hex('#bfe0ee'));
  // Wall display with a credenza and soundbar; a whiteboard on the side wall.
  const sw = clamp(w * 0.5, 1.4, 2.6);
  wallDisplay(R, w / 2, 0.95, 0.1, sw, sw * 0.5, live, S);
  credenza(R, w / 2, 0.45, Math.min(w - 1.0, sw + 0.8), S);
  whiteboard(R.sub(0.1, clamp(d * 0.5, 1.5, 3.2), 3), 0, 1.0, 0, 1.5, 0.9, S);
  // Glass wall on the east side: slim steel posts, a frosted band and clear panes, with a door pocket.
  const gx = w - 0.1;
  for (let i = 0; i <= 4; i++) R.box(gx - 0.02, 0, 0.2 + (i * (d - 0.5)) / 4, 0.05, 2.7, 0.05, S.dark);
  R.box(gx - 0.02, 2.7, 0.2, 0.05, 0.05, d - 0.45, S.dark);
  R.box(gx - 0.02, 0, 0.2, 0.05, 0.06, d - 0.45, S.dark);
  R.box(gx, 0.06, 0.25, 0.015, 2.64, d - 0.55, hex('#9fd4e6'), { emissive: WINDOW_BASE + 0.02, shade: false });
  R.box(gx - 0.01, 1.05, 0.25, 0.02, 0.4, d - 0.55, hex('#eef3f6'), { emissive: 0.5, shade: false });
  tallPlant(R, 0.5, d - 0.55, S, 1, 3);
  tallPlant(R, w - 0.6, d - 0.55, S, 0.95, 1);
  void rnd; void accent;
  return 0;
}

export function lounge(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, floor, b, y } = c;
  // Rug and a sectional that wraps a low table, plus a coffee bar along the back wall.
  const gx = clamp(w * 0.42, 2.2, w - 2.4), gz = clamp(d * 0.62, 2.0, d - 1.5);
  contact(b, c.frame.x + gx - 2.0, c.frame.z + gz - 1.4, 4.0, 3.0, y, floor);
  rug(b, c.frame.x + gx - 2.1, y, c.frame.z + gz - 1.5, 4.2, 3.0, mix(S.rug, accent, 0.28), scale(S.rug, 0.7));
  sofa(R, gx, gz - 1.15, 0, Math.min(2.8, w * 0.42), S.fabric, S.dark);
  sofa(R, gx - 1.7, gz, 3, 1.9, mix(S.fabric, S.fabricAlt, 0.4), S.dark);
  armchair(R, gx + 1.7, gz + 0.1, 1, S.fabricAlt, S.dark);
  roundTable(R, gx, gz, 0.52, 0.38, S.wood, S.dark);
  R.prism(gx, 0.38, gz, 0.05, 0.05, 0.14, 6, hex('#8aa89a'));
  pouf(R, gx + 0.7, gz + 0.9, mix(S.fabricAlt, S.fabric, 0.4));
  pouf(R, gx - 0.4, gz + 1.0, S.fabric, 0.25);
  sideTable(R, gx + 1.9, gz - 1.0, S);
  // Coffee bar: a counter with espresso machine, cups and stools.
  const bw = clamp(w * 0.46, 1.8, 3.4), bx = w - bw - 0.35;
  R.box(bx, 0, 0.35, bw, 0.95, 0.55, S.woodDark, { grad: 0.08 });
  R.box(bx - 0.04, 0.95, 0.32, bw + 0.08, 0.05, 0.64, S.marble);
  R.box(bx + 0.2, 0.2, 0.9, bw - 0.4, 0.02, 0.01, accent, { emissive: 0.5, shade: false });
  R.box(bx + 0.25, 1.0, 0.5, 0.42, 0.34, 0.3, S.metal, { grad: 0.1 });
  R.box(bx + 0.3, 1.0, 0.78, 0.32, 0.02, 0.1, BLACK);
  R.box(bx + 0.27, 1.34, 0.48, 0.38, 0.05, 0.28, S.dark);
  R.prism(bx + 0.9, 1.0, 0.7, 0.03, 0.026, 0.07, 6, hex('#f2f0ea'));
  R.prism(bx + 1.05, 1.0, 0.7, 0.03, 0.026, 0.07, 6, hex('#f2f0ea'));
  R.box(bx + bw - 0.5, 1.0, 0.5, 0.3, 0.02, 0.22, S.wood);
  R.box(bx - 0.04, 1.4, 0.15, bw + 0.08, 0.04, 0.22, S.woodDark);
  for (let i = 0; i < 3; i++) R.prism(bx + 0.4 + i * (bw - 0.8) / 2, 1.44, 0.26, 0.05, 0.045, 0.1, 6, hex(['#e8e4d8', '#c9a24a', '#8aa89a'][i]));
  const stools = clamp(Math.floor(bw / 0.8), 2, 4);
  for (let i = 0; i < stools; i++) barStool(R, bx + 0.4 + (i * (bw - 0.8)) / Math.max(1, stools - 1), 1.35, S.fabricAlt);
  // Shelf wall, plants and lamps.
  bookcase(R, 1.1, 0.3, 1.6, 2.0, S, rnd);
  tallPlant(R, 0.5, d - 0.6, S, 1.15, 1);
  tallPlant(R, w - 0.5, d - 0.6, S, 1, 2);
  floorLamp(R, Math.max(0.45, gx - 2.3), gz - 1.4, S, 1.65);
  return 0;
}

export function executive(c: RoomCtx): number {
  const { R, w, d, S, rnd, accent, floor, b, y } = c;
  const bw = Math.min(w * 0.9, 7.2), bx = (w - bw) / 2;
  // Back wall: bookcases either side of an ANUM panel in dark timber.
  const side = clamp(bw * 0.3, 1.2, 2.4);
  bookcase(R, bx + side / 2, 0.3, side, 2.5, S, rnd);
  bookcase(R, bx + bw - side / 2, 0.3, side, 2.5, S, rnd);
  R.box(bx + side + 0.1, 0, 0.1, bw - 2 * side - 0.2, 2.6, 0.08, S.woodDark, { grad: 0.1 });
  const [mx, mz] = R.pt(w / 2, 0.24);
  anumMark(b, mx, y + 1.55, mz, 0.9, S.brass, 0.7);
  R.box(bx + side + 0.1, 2.6, 0.1, bw - 2 * side - 0.2, 0.04, 0.2, WARM, { emissive: 0.8, shade: false });
  // Desk on a rug: heavy walnut top with brass trim, two pedestals, leather blotter, lamp.
  const st = c.stations[0];
  const dw = clamp(w * 0.34, 1.9, 2.5), dd = 1.0;
  const dx = st ? st.x - c.frame.x : w / 2, dz = st ? st.z - c.frame.z : clamp(d * 0.34, 1.6, 3.0);
  rug(b, c.frame.x + dx - dw * 0.75, y, c.frame.z + dz - 1.2, dw * 1.5, 3.1, mix(S.rug, accent, 0.14), scale(S.rug, 0.65));
  contact(b, c.frame.x + dx - dw / 2, c.frame.z + dz - dd / 2 - 0.2, dw, dd + 1.4, y, floor);
  R.box(dx - dw / 2, 0.69, dz - dd / 2, dw, 0.07, dd, S.woodDark, { grad: 0.06 });
  R.box(dx - dw / 2, 0.69, dz + dd / 2, dw, 0.07, 0.014, S.brass, { emissive: 0.15 });
  R.box(dx - dw / 2, 0.69, dz - dd / 2 - 0.014, dw, 0.07, 0.014, S.brass, { emissive: 0.1 });
  for (const sx of [dx - dw / 2 + 0.05, dx + dw / 2 - 0.55]) {
    R.box(sx, 0, dz - dd / 2 + 0.05, 0.5, 0.69, dd - 0.1, scale(S.woodDark, 1.05), { grad: 0.08 });
    for (let i = 0; i < 3; i++) R.box(sx + 0.02, 0.08 + i * 0.2, dz + dd / 2 - 0.052, 0.46, 0.015, 0.01, S.brass);
  }
  R.box(dx - 0.5, 0.775, dz - 0.18, 1.0, 0.012, 0.45, S.leather);
  R.box(dx - 0.45, 0.787, dz - 0.15, 0.8, 0.02, 0.025, BLACK);
  R.box(dx - 0.26, 0.77, dz - dd / 2 + 0.08, 0.52, 0.34, 0.03, BLACK);
  const state = st?.state ?? 'vacant';
  const sc = state === 'working' ? hex('#58f0cf') : state === 'waiting' ? hex('#ffc861') : state === 'idle' ? hex('#6aa7ff') : state === 'vacant' ? hex('#232c36') : hex('#8b95a5');
  R.box(dx - 0.24, 0.79, dz - dd / 2 + 0.115, 0.48, 0.3, 0.008, sc, { emissive: state === 'vacant' ? 0 : state === 'working' ? 1 : 0.6, shade: false });
  tableLamp(R, dx + dw / 2 - 0.7, 0.77, dz - dd / 2 + 0.2, S);
  // High-back leather chair behind the desk (facing +z, towards the visitor chairs).
  const cz = dz - dd / 2 - 0.55;
  R.box(dx - 0.3, 0.46, cz - 0.28, 0.6, 0.1, 0.56, S.leather, { grad: 0.1 });
  R.box(dx - 0.29, 0.56, cz - 0.34, 0.58, 0.82, 0.12, scale(S.leather, 0.92), { grad: 0.15 });
  R.box(dx - 0.28, 1.1, cz - 0.36, 0.56, 0.3, 0.14, scale(S.leather, 1.08));
  for (const sx of [-0.34, 0.26]) R.box(dx + sx, 0.58, cz - 0.15, 0.08, 0.2, 0.4, scale(S.leather, 0.8));
  R.prism(dx, 0.08, cz, 0.04, 0.035, 0.4, 5, BLACK);
  R.box(dx - 0.35, 0.04, cz - 0.03, 0.7, 0.04, 0.06, BLACK);
  R.box(dx - 0.03, 0.04, cz - 0.38, 0.06, 0.04, 0.7, BLACK);
  void st;
  // Two visitor chairs facing the desk.
  sideChair(R, dx - 0.55, dz + dd / 2 + 0.7, 2, S.leather, S.brass);
  sideChair(R, dx + 0.55, dz + dd / 2 + 0.7, 2, S.leather, S.brass);
  // Sitting group at the front corner: sofa, two armchairs, marble table.
  const gx = clamp(w * 0.22, 1.7, 3.0), gz = clamp(d * 0.74, dz + 2.4, d - 1.2);
  rug(b, c.frame.x + gx - 1.7, y, c.frame.z + gz - 1.3, 3.4, 2.6, mix(S.rug, accent, 0.2), scale(S.rug, 0.7));
  sofa(R, gx, gz - 0.95, 0, 2.1, S.leather, S.brass);
  armchair(R, gx - 1.3, gz + 0.1, 3, S.fabric, S.brass);
  armchair(R, gx + 1.3, gz + 0.1, 1, S.fabric, S.brass);
  roundTable(R, gx, gz + 0.1, 0.45, 0.4, S.marble, S.brass);
  // Round meeting table with four chairs on the right.
  const rx = clamp(w * 0.78, w / 2 + 1.4, w - 1.5), rz = clamp(d * 0.7, dz + 2.2, d - 1.4);
  roundTable(R, rx, rz, 0.65, 0.74, S.wood, S.dark);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 4;
    sideChair(R, rx + Math.cos(a) * 1.0, rz + Math.sin(a) * 1.0, ((Math.round((a + Math.PI / 2) / (Math.PI / 2)) % 4) + 4) % 4, S.fabric, S.dark);
  }
  // Credenza with a globe, a minibar side, plants and lamps.
  credenza(R, w - 1.5, 0.45, 1.8, S);
  R.prism(w - 1.8, 0.78, 0.45, 0.2, 0.2, 0.03, 8, S.brass);
  R.blob(w - 1.8, 1.08, 0.45, 0.2, 0.2, 0.2, hex('#2c6f99'), 8);
  R.prism(w - 1.25, 0.78, 0.45, 0.05, 0.045, 0.22, 8, hex('#8a3f2b'));
  tallPlant(R, 0.5, d - 0.6, S, 1.2, 1);
  tallPlant(R, w - 0.5, d - 0.6, S, 1.1, 2);
  floorLamp(R, bx + side + 0.5, 0.7, S, 1.7);
  deskPlant(R, dx - dw / 2 + 0.3, 0.76, dz + 0.1, S);
  // The occupant, only when the registry says so, sits in the leather chair.
  if (st?.occupantSeed) {
    const [fx, fz] = R.pt(dx, cz - 0.02);
    seatedFigure(b, fx, y + 0.1, fz, { seed: st.occupantSeed, rot: 0, pose: st.pose ?? 'leaning', look: st.look });
  }
  return 0;
}
