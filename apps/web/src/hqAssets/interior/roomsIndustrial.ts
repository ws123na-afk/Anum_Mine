/**
 * Industrial rooms: warehouse (racking, pallets, crates, forklift, dock door) and tyre workshop
 * (tyre stacks and racks, two-post lift with a raised car, changer, balancer, benches, tool chest).
 * Local frame as the other rooms. No people.
 */
import { hex, mix, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { WARM, anumMark } from '../../lib/hqProps.ts';
import { BLACK, tallPlant } from './furniture.ts';
import { Local } from './local.ts';
import type { RoomCtx } from './types.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const RACK_BLUE = hex('#2f6fb0');
const RACK_ORANGE = hex('#e8902b');
const WOOD_PALLET = hex('#c79b62');
const YELLOW = hex('#e8d36a');

/** A tyre standing on its edge in the local x-y plane (axle along z). */
export function upTyre(L: Local, cx: number, cy: number, cz: number, r: number, t: number, n = 10): void {
  const ri = r * 0.6;
  const tread = hex('#15181d'), wall = hex('#262b31'), hub = hex('#aab4be');
  const p = (rr: number, a: number, dz: number): [number, number, number] => [cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, cz + dz];
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2;
    L.quad(p(r, a0, -t / 2), p(r, a1, -t / 2), p(r, a1, t / 2), p(r, a0, t / 2), scale(tread, 0.85 + 0.3 * Math.max(0, Math.sin(a0 + 0.8))));
    L.quad(p(ri, a0, t / 2), p(r, a0, t / 2), p(r, a1, t / 2), p(ri, a1, t / 2), wall);
    L.quad(p(ri, a1, -t / 2), p(r, a1, -t / 2), p(r, a0, -t / 2), p(ri, a0, -t / 2), wall);
    L.tri([cx, cy, cz + t / 2 - 0.015], p(ri, a0, t / 2 - 0.015), p(ri, a1, t / 2 - 0.015), hub);
  }
}

function tyreStack(L: Local, lx: number, lz: number, count: number, r = 0.42, sides = 10): void {
  for (let i = 0; i < count; i++) {
    L.prism(lx, i * 0.24, lz, r, r, 0.23, sides, i % 2 ? hex('#1b1f24') : hex('#22272d'));
    L.prism(lx, i * 0.24 + 0.231, lz, r * 0.62, r * 0.62, 0.004, sides, hex('#2b3138'));
  }
  L.prism(lx, count * 0.24 - 0.002, lz, r * 0.34, r * 0.34, 0.006, sides, hex('#aab4be'));
}

function pallet(L: Local, lx: number, y: number, lz: number, w = 1.2, d = 0.8): void {
  L.box(lx, y + 0.09, lz, w, 0.035, d, WOOD_PALLET, { skip: ['bottom'] });
  for (const f of [0, 0.5, 1]) L.box(lx + f * (w - 0.1), y, lz + 0.02, 0.1, 0.09, d - 0.04, scale(WOOD_PALLET, 0.82), { skip: ['bottom'] });
}

function cartons(L: Local, lx: number, y: number, lz: number, rnd: () => number, lite: boolean): void {
  const tones = [hex('#c79b62'), hex('#b5834d'), hex('#d4ad78'), hex('#a98458')];
  const rows = lite ? 1 : 1 + Math.floor(rnd() * 2);
  for (let r = 0; r < rows; r++) {
    for (let i = 0; i < 2; i++) {
      for (let j = 0; j < 2; j++) {
        if (rnd() < 0.12) continue;
        const bx = lx + 0.04 + i * 0.57, bz = lz + 0.03 + j * 0.37;
        L.box(bx, y + 0.125 + r * 0.33, bz, 0.55, 0.32, 0.35, tones[Math.floor(rnd() * tones.length)], { skip: ['bottom'], grad: 0.1 });
        L.box(bx + 0.22, y + 0.125 + r * 0.33 + 0.318, bz, 0.11, 0.004, 0.35, hex('#e8e4d8'), { shade: false });
      }
    }
  }
}

function wrapped(L: Local, lx: number, y: number, lz: number, rnd: () => number): void {
  const h = 0.55 + rnd() * 0.25;
  L.box(lx + 0.05, y + 0.125, lz + 0.04, 1.1, h, 0.72, hex('#cfe0e8'), { skip: ['bottom'], grad: 0.1, emissive: 0.1 });
  L.box(lx + 0.05, y + 0.125 + h * 0.3, lz + 0.035, 1.1, 0.03, 0.73, hex('#9db6c4'));
  L.box(lx + 0.05, y + 0.125 + h * 0.65, lz + 0.035, 1.1, 0.03, 0.73, hex('#9db6c4'));
}

/** One storage bay, 1.5 m wide, 0.9 m deep, local x along the run, three beam levels. */
function rackBay(L: Local, lx: number, lz: number, rnd: () => number, lite: boolean): void {
  const W = 1.5, D = 0.9;
  for (const ux of [0, W - 0.07]) for (const uz of [0, D - 0.07]) L.box(lx + ux, 0, lz + uz, 0.07, 2.6, 0.07, RACK_BLUE, { skip: ['bottom'] });
  for (let lvl = 0; lvl < 3; lvl++) {
    const by = 0.16 + lvl * 0.8;
    L.box(lx, by, lz, W, 0.09, 0.05, RACK_ORANGE, { skip: ['bottom'] });
    L.box(lx, by, lz + D - 0.05, W, 0.09, 0.05, RACK_ORANGE, { skip: ['bottom'] });
    L.box(lx + 0.07, by, lz + 0.05, W - 0.14, 0.012, D - 0.1, scale(RACK_ORANGE, 0.55), { skip: ['bottom'] });
    const roll = rnd();
    if (roll < 0.1) continue;
    pallet(L, lx + 0.15, by + 0.09 - 0.0, lz + 0.05);
    const gy = by + 0.09;
    if (roll < 0.55) cartons(L, lx + 0.15, gy, lz + 0.07, rnd, lite);
    else if (roll < 0.8) wrapped(L, lx + 0.15, gy, lz + 0.07, rnd);
    else {
      tyreStack(L.sub(0, 0, 0, gy), lx + 0.5, lz + 0.45, 2, 0.32, 8);
      tyreStack(L.sub(0, 0, 0, gy), lx + 1.0, lz + 0.45, 2, 0.32, 8);
    }
  }
  L.box(lx + 0.02, 0, lz + 0.02, W - 0.04, 0.14, 0.04, YELLOW, { skip: ['bottom'] });
}

function forklift(L: Local, lx: number, lz: number, rot: number): void {
  const f = L.sub(lx, lz, rot);
  const body = hex('#e8902b'), dark = hex('#252c34');
  f.box(-0.45, 0.22, -0.95, 0.9, 0.5, 1.3, body, { grad: 0.12 });
  f.box(-0.47, 0.24, -1.0, 0.94, 0.6, 0.35, scale(body, 0.88), { grad: 0.1 });
  f.box(-0.4, 0.72, -0.55, 0.8, 0.12, 0.9, dark);
  f.box(-0.28, 0.84, -0.5, 0.56, 0.08, 0.5, hex('#31465a'));
  f.box(-0.28, 0.9, -0.55, 0.56, 0.4, 0.08, hex('#31465a'));
  for (const sx of [-0.43, 0.37]) for (const sz of [-0.55, 0.28]) f.box(sx, 0.72, sz, 0.06, 0.95, 0.06, dark);
  f.box(-0.45, 1.67, -0.6, 0.9, 0.05, 0.95, dark);
  f.limb([0, 0.74, 0.12], [0, 1.0, 0.0], 0.04, 0.04, 0.04, 0.04, dark);
  f.prism(0, 1.0, 0.0, 0.14, 0.14, 0.025, 8, dark);
  // Mast, carriage and forks.
  for (const sx of [-0.28, 0.2]) f.box(sx, 0.1, 0.38, 0.08, 1.85, 0.08, dark);
  f.box(-0.3, 1.9, 0.38, 0.6, 0.06, 0.08, dark);
  f.box(-0.3, 0.3, 0.46, 0.6, 0.5, 0.05, hex('#3a424b'));
  for (const sx of [-0.25, 0.17]) f.box(sx, 0.06, 0.5, 0.08, 0.05, 0.95, hex('#9aa5b1'));
  f.box(-0.45, 0.78, 0.42, 0.9, 0.04, 0.04, WARM, { emissive: 0.5, shade: false });
  for (const sx of [-0.53, 0.33]) for (const sz of [-0.55, 0.28]) {
    const w = f.sub(sx + 0.1, sz, 1);
    upTyre(w, 0, 0.19, 0, 0.19, 0.2, 8);
  }
}

function dockDoor(c: RoomCtx, z: number, wd: number): void {
  const { R, w } = c;
  const x = w - 0.14;
  R.box(x - 0.04, 0, z - 0.14, 0.18, 2.55, wd + 0.28, YELLOW, { skip: ['bottom'] });
  R.box(x - 0.01, 0.0, z, 0.1, 2.35, wd, hex('#aab4be'), { skip: ['bottom'] });
  for (let i = 0; i < 9; i++) R.box(x - 0.025, 0.05 + i * 0.26, z, 0.015, 0.03, wd, hex('#7f8a95'));
  R.box(x - 0.1, 2.58, z + wd / 2 - 0.1, 0.12, 0.12, 0.2, WARM, { emissive: 0.8, shade: false });
  for (const bz of [z - 0.05, z + wd - 0.15]) R.box(x - 0.1, 0.5, bz, 0.12, 0.3, 0.2, BLACK);
}

function bollard(L: Local, lx: number, lz: number): void {
  L.prism(lx, 0, lz, 0.07, 0.07, 0.9, 8, YELLOW);
  L.prism(lx, 0.55, lz, 0.072, 0.072, 0.1, 8, BLACK);
}

export function warehouse(c: RoomCtx): number {
  const { R, w, d, rnd, b, y, floor, S } = c;
  const lite = c.lite;
  // Back run of racking.
  const bays = clamp(Math.floor((w - 0.9) / 1.55), 2, 7);
  const bx0 = 0.4 + Math.max(0, (w - 0.9 - bays * 1.55) / 2);
  for (let i = 0; i < bays; i++) rackBay(R, bx0 + i * 1.55, 0.3, rnd, lite);
  // West run, rotated: rack depth runs towards -x from x = 1.35.
  const side = lite ? 0 : clamp(Math.floor((d - 4.0) / 1.55), 0, 3);
  for (let i = 0; i < side; i++) rackBay(R.sub(1.45, 1.75 + i * 1.55, 1), 0, 0, rnd, lite);
  // Floor markings: aisle edges in yellow, a green walkway line.
  const aisleZ = 1.45;
  b.plane(c.frame.x + 0.3, y + 0.012, c.frame.z + aisleZ, w - 0.6, 0.1, YELLOW, 0.35);
  if (side > 0) b.plane(c.frame.x + 1.5, y + 0.013, c.frame.z + 1.7, 0.1, side * 1.55 + 0.3, YELLOW, 0.35);
  b.plane(c.frame.x + w * 0.5, y + 0.013, c.frame.z + aisleZ + 0.2, 0.1, d - aisleZ - 0.5, hex('#4fb477'), 0.35);
  // Dock door on the east wall, a pallet at the door, a pallet jack, wrapped goods.
  const dz = clamp(d * 0.45, 1.8, d - 2.6);
  if (d >= 5.2 && w >= 6) {
    dockDoor(c, dz, 2.0);
    pallet(R, w - 2.0, 0, dz + 0.5);
    wrapped(R, w - 2.0, 0, dz + 0.5, rnd);
    bollard(R, w - 0.6, dz - 0.45);
    bollard(R, w - 0.6, dz + 2.45);
  }
  // Forklift in the main aisle and a pallet on the floor with a pallet jack.
  forklift(R, clamp(w * 0.5, 2.5, w - 2.6), clamp(d - 1.4, aisleZ + 1.9, d - 0.9), 2);
  pallet(R, clamp(w * 0.28, 1.2, w - 4.0), 0, d - 1.5);
  cartons(R, clamp(w * 0.28, 1.2, w - 4.0), 0, d - 1.43, rnd, lite);
  const pj = R.sub(clamp(w * 0.28, 1.2, w - 4.0) + 1.5, d - 1.1, 1);
  pj.box(-0.1, 0.0, -0.3, 0.2, 0.08, 1.2, hex('#d94a3b'));
  pj.box(-0.04, 0.0, 0.9, 0.08, 0.9, 0.08, hex('#d94a3b'));
  pj.box(-0.12, 0.9, 0.82, 0.24, 0.05, 0.05, BLACK);
  // Cage trolley and a fire extinguisher by the door, a wall sign with the ANUM mark.
  const cx = w - 1.4;
  if (!lite && d >= 6) {
    const k = R.sub(cx, d - 1.0);
    for (const [px, pz] of [[0, 0], [0.8, 0], [0, 0.7], [0.8, 0.7]] as const) k.box(px, 0.1, pz, 0.04, 1.4, 0.04, S.metal);
    for (let i = 0; i < 4; i++) { k.box(0, 0.5 + i * 0.3, 0, 0.84, 0.015, 0.015, S.metal); k.box(0, 0.5 + i * 0.3, 0.7, 0.84, 0.015, 0.015, S.metal); k.box(0, 0.5 + i * 0.3, 0, 0.015, 0.015, 0.74, S.metal); k.box(0.82, 0.5 + i * 0.3, 0, 0.015, 0.015, 0.74, S.metal); }
    k.box(0.04, 0.12, 0.04, 0.76, 0.05, 0.66, hex('#59656f'));
    k.box(0.1, 0.17, 0.1, 0.3, 0.3, 0.3, hex('#c79b62'));
  }
  R.prism(0.45, 0, d - 0.4, 0.06, 0.06, 0.4, 8, hex('#c0392b'));
  const [mx, mz] = R.pt(w * 0.5, 0.26);
  anumMark(b, mx, y + 2.7, mz, 0.3, c.accent, 0.8);
  void floor; void tallPlant;
  return 0;
}

export function workshop(c: RoomCtx): number {
  const { R, w, d, S, rnd, b, y, accent } = c;
  // Back wall: pegboard with outlined tools, a wall display, and a long steel bench with vises.
  const pw = clamp(w - 3.0, 2.0, 5.0), px = clamp(w * 0.5 - pw / 2 + 0.6, 1.4, w - pw - 0.4);
  R.box(px, 1.1, 0.1, pw, 1.4, 0.05, hex('#3a424b'));
  const toolTone = [hex('#e8902b'), hex('#9aa5b1'), hex('#c0504d'), hex('#6aa7ff'), hex('#e8e4d8')];
  for (let i = 0; i < Math.floor(pw / 0.32); i++) {
    const th = 0.25 + (i % 4) * 0.12;
    R.box(px + 0.15 + i * 0.32, 2.3 - th, 0.15, 0.06, th, 0.03, toolTone[i % toolTone.length]);
    if (i % 3 === 0) R.box(px + 0.1 + i * 0.32, 2.3 - th, 0.15, 0.16, 0.05, 0.03, toolTone[(i + 2) % toolTone.length]);
  }
  R.box(px, 1.05, 0.1, pw, 0.04, 0.2, S.metal);
  const bw = pw, bx = px;
  R.box(bx, 0.82, 0.3, bw, 0.06, 0.75, hex('#9aa5b1'), { grad: 0.08 });
  R.box(bx, 0.0, 0.34, 0.08, 0.82, 0.65, S.dark);
  R.box(bx + bw - 0.08, 0.0, 0.34, 0.08, 0.82, 0.65, S.dark);
  R.box(bx + 0.08, 0.28, 0.4, bw - 0.16, 0.04, 0.55, S.dark);
  R.box(bx + 0.4, 0.88, 0.45, 0.2, 0.16, 0.2, hex('#d94a3b'));
  R.box(bx + 0.34, 0.88, 0.62, 0.32, 0.05, 0.08, S.dark);
  for (let i = 0; i < 3; i++) R.box(bx + 1.2 + i * 0.5, 0.88, 0.5, 0.26, 0.12, 0.3, [hex('#e8902b'), hex('#6aa7ff'), hex('#9aa5b1')][i]);
  // Red tool chest on the left with drawers and handles.
  R.box(0.4, 0, 0.35, 0.8, 1.05, 0.55, hex('#b3372c'), { grad: 0.1 });
  R.box(0.38, 1.05, 0.33, 0.84, 0.05, 0.59, hex('#2a313b'));
  for (let i = 0; i < 5; i++) { R.box(0.46, 0.1 + i * 0.19, 0.9, 0.68, 0.015, 0.012, scale(hex('#b3372c'), 0.55)); R.box(0.6, 0.15 + i * 0.19, 0.905, 0.4, 0.02, 0.015, hex('#c9d1d9')); }
  // Two-post lift at the middle with a car raised on it; yellow-black hazard border on the floor.
  const lx = clamp(w * 0.5, 3.0, w - 2.4), lz = clamp(d * 0.55, 3.0, d - 2.0);
  const k = clamp((d - 2.2) / 4.8, 0.55, 1);
  const hz = 2.6 * k + 0.4;
  for (let i = 0; i < 14; i++) {
    const t = lx - 1.6 + i * (3.2 / 14);
    R.box(t, 0.012, lz - hz, 3.2 / 14 - 0.01, 0.006, 0.12, i % 2 ? BLACK : YELLOW, { skip: ['bottom'], shade: false });
    R.box(t, 0.012, lz + hz, 3.2 / 14 - 0.01, 0.006, 0.12, i % 2 ? BLACK : YELLOW, { skip: ['bottom'], shade: false });
  }
  const lift = R.sub(lx, lz);
  for (const sx of [-1.15, 0.85]) {
    lift.box(sx, 0, -0.1, 0.3, 2.85, 0.3, hex('#e8902b'), { grad: 0.1 });
    lift.box(sx - 0.1, 0, -0.2, 0.5, 0.05, 0.5, S.dark);
    lift.box(sx + 0.05, 1.4, -0.2, 0.2, 0.3, 0.5, scale(hex('#e8902b'), 0.7));
    lift.box(sx - 0.0, 2.85, -0.1, 0.3, 0.05, 0.3, S.dark);
  }
  lift.box(-1.0, 2.9, -0.04, 2.0, 0.08, 0.18, hex('#e8902b'));
  // The car, nose towards +z, raised 1.3 m, one front wheel off and standing on the floor.
  const car = lift.sub(0, 0, 0, 0);
  const cl = 4.0 * k, cwid = 1.7;
  const body = mix(hex('#8a2f3a'), accent, 0.12);
  car.box(-cwid / 2, 1.3, -cl * 0.5, cwid, 0.5, cl, body, { grad: 0.18 });
  car.box(-cwid / 2 + 0.1, 1.8, -cl * 0.25, cwid - 0.2, 0.46, cl * 0.45, mix(body, hex('#9fd4e6'), 0.5));
  car.box(-cwid / 2 + 0.08, 2.26, -cl * 0.22, cwid - 0.16, 0.05, cl * 0.4, scale(body, 0.9));
  car.box(-cwid / 2 + 0.2, 1.4, cl * 0.5, 0.4, 0.14, 0.05, WARM, { emissive: 0.9, shade: false });
  car.box(cwid / 2 - 0.6, 1.4, cl * 0.5, 0.4, 0.14, 0.05, WARM, { emissive: 0.9, shade: false });
  for (const [sx, sz] of [[-1, -0.3], [1, -0.3], [1, 0.3]] as const) {
    const wh = car.sub(sx * (cwid / 2 + 0.02), sz * cl * 0.5, sx < 0 ? 1 : 3, 0);
    upTyre(wh, 0, 1.55, 0, 0.32, 0.22, 8);
  }
  // Tyre changer, balancer, and a standing tyre on a stand beside the lift.
  const tcx = clamp(w - 1.4, lx + 2.0, w - 0.9), tcz = clamp(d * 0.4, 1.8, d - 3.0);
  R.box(tcx - 0.4, 0, tcz - 0.4, 0.8, 0.14, 0.8, S.dark);
  R.prism(tcx, 0.14, tcz, 0.28, 0.28, 0.36, 8, hex('#9aa5b1'));
  R.prism(tcx, 0.5, tcz, 0.3, 0.3, 0.03, 12, hex('#59656f'));
  R.box(tcx + 0.3, 0.14, tcz - 0.1, 0.16, 1.25, 0.16, hex('#3a8fd0'));
  R.box(tcx - 0.1, 1.28, tcz - 0.1, 0.5, 0.1, 0.16, hex('#3a8fd0'));
  R.prism(tcx, 0.53, tcz, 0.3, 0.3, 0.18, 10, hex('#1b1f24'));
  const bcx = clamp(tcx, lx + 2.0, w - 0.9), bcz = clamp(tcz + 1.4, tcz + 1.2, d - 1.2);
  R.box(bcx - 0.35, 0, bcz - 0.3, 0.7, 0.85, 0.6, hex('#d9dde0'), { grad: 0.1 });
  R.box(bcx - 0.3, 0.85, bcz - 0.25, 0.6, 0.28, 0.5, S.dark);
  R.box(bcx - 0.2, 0.9, bcz + 0.26, 0.4, 0.1, 0.02, accent, { emissive: 0.7, shade: false });
  // Tyre rack on the west wall: three shelves of standing tyres seen edge-on.
  const rk = R.sub(0.5, clamp(d * 0.62, 2.8, d - 1.2), 1);
  for (let lvl = 0; lvl < 2; lvl++) {
    rk.box(-0.55, 0.4 + lvl * 0.85, -0.5, 1.1, 0.05, 1.0, S.metal);
    for (let i = 0; i < 4; i++) upTyre(rk, 0, 0.4 + lvl * 0.85 + 0.05 + 0.33, -0.36 + i * 0.24, 0.33, 0.2, 8);
  }
  rk.box(-0.55, 0, -0.5, 0.06, 1.7, 0.06, S.metal);
  rk.box(-0.55, 0, 0.44, 0.06, 1.7, 0.06, S.metal);
  // Stacks near the front corner, oil drums and a compressor.
  tyreStack(R, w - 0.8, d - 0.8, 5);
  tyreStack(R, w - 1.75, d - 0.7, 3);
  tyreStack(R, 0.85, d - 0.75, 4);
  for (let i = 0; i < 2; i++) { R.prism(1.85 + i * 0.6, 0, d - 0.55, 0.27, 0.27, 0.78, 10, hex('#2f6fb0')); R.prism(1.85 + i * 0.6, 0.78, d - 0.55, 0.27, 0.25, 0.03, 10, hex('#1f4f80')); }
  R.box(w - 1.2, 0, 0.35, 0.8, 0.5, 0.45, hex('#d94a3b'), { grad: 0.1 });
  R.prism(w - 0.8, 0.5, 0.58, 0.2, 0.2, 0.3, 10, hex('#b3372c'));
  void rnd; void b; void y;
  const [mx, mz] = R.pt(px - 0.8 > 0.6 ? px - 0.6 : w - 0.8, 0.2);
  anumMark(b, mx, y + 2.4, mz, 0.3, accent, 0.8);
  return 0;
}
