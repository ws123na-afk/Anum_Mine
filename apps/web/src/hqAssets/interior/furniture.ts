/**
 * Shared furniture for the interior rooms. Every piece draws in a `Local` frame (floor level at
 * local y 0, front towards local +z) and takes colours from the scheme, so the room builders only
 * place and compose.
 */
import { MeshBuilder, hex, mix, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { WARM } from '../../lib/hqProps.ts';
import { Local } from './local.ts';
import type { InteriorScheme } from './scheme.ts';
import { seatedFigure, type SeatedPose } from './figures.ts';
import type { FigureLook } from '../../lib/hqAvatar.ts';

export type StationState = 'working' | 'waiting' | 'idle' | 'unknown' | 'vacant' | 'assigned';

export const SCREEN_LOOK: Readonly<Record<StationState, { color: Rgb; emissive: number }>> = {
  working: { color: hex('#58f0cf'), emissive: 1 },
  waiting: { color: hex('#ffc861'), emissive: 0.95 },
  idle: { color: hex('#6aa7ff'), emissive: 0.55 },
  unknown: { color: hex('#8b95a5'), emissive: 0.3 },
  assigned: { color: hex('#8b95a5'), emissive: 0.25 },
  vacant: { color: hex('#232c36'), emissive: 0 },
};

export const BLACK = hex('#14181d');

// ------------------------------------------------------------------ seating

/** An ergonomic task chair facing local +z: mesh back, arms, five star base on castors. */
export function taskChair(L: Local, lx: number, lz: number, color: Rgb, swivel = 0): void {
  const c = L.sub(lx, lz, swivel);
  c.prism(0, 0.1, 0, 0.035, 0.03, 0.34, 5, BLACK);
  c.box(-0.3, 0.06, -0.025, 0.6, 0.035, 0.05, BLACK);
  c.box(-0.025, 0.06, -0.3, 0.05, 0.035, 0.6, BLACK);
  for (const [wx, wz] of [[-0.3, 0], [0.3, 0], [0, -0.3], [0, 0.3]] as const) c.box(wx - 0.03, 0, wz - 0.03, 0.06, 0.06, 0.06, BLACK);
  c.box(-0.23, 0.43, -0.22, 0.46, 0.07, 0.46, color, { grad: 0.1 });
  c.box(-0.22, 0.52, -0.27, 0.44, 0.46, 0.05, scale(color, 0.9), { grad: 0.15 });
  c.box(-0.05, 0.48, -0.24, 0.1, 0.06, 0.04, BLACK);
  for (const sx of [-1, 1]) {
    c.box(sx * 0.25 - 0.015, 0.5, -0.1, 0.03, 0.2, 0.03, BLACK);
    c.box(sx * 0.25 - 0.03, 0.7, -0.14, 0.06, 0.03, 0.3, BLACK);
  }
}

/** A guest or meeting chair: four legs, slim seat and a curved back. */
export function sideChair(L: Local, lx: number, lz: number, rot: number, color: Rgb, frame: Rgb): void {
  const c = L.sub(lx, lz, rot);
  for (const [px, pz] of [[-0.2, -0.2], [0.17, -0.2], [-0.2, 0.17], [0.17, 0.17]] as const) c.box(px, 0, pz, 0.03, 0.44, 0.03, frame);
  c.box(-0.22, 0.44, -0.22, 0.44, 0.06, 0.44, color, { grad: 0.1 });
  c.box(-0.21, 0.5, -0.24, 0.42, 0.42, 0.05, scale(color, 0.92), { grad: 0.12 });
  c.box(-0.19, 0.86, -0.25, 0.38, 0.05, 0.07, frame);
}

export function sofa(L: Local, lx: number, lz: number, rot: number, w: number, color: Rgb, legs: Rgb, model: 'sofa' | 'armchair' = 'sofa'): void {
  const c = L.sub(lx, lz, rot);
  // A loadable model may replace this piece: the slot says where, and the tagged triangles are its fallback.
  L.b.slots.push({ model, x: c.ox, y: c.y0, z: c.oz, rot: c.rot, width: w });
  L.b.tagged('sofa', () => {
    const seat = Math.max(1, Math.round(w / 0.75));
    c.box(-w / 2, 0.1, -0.42, w, 0.28, 0.84, scale(color, 0.78));
    for (const sx of [-w / 2 + 0.06, w / 2 - 0.1]) for (const sz of [-0.36, 0.3]) c.box(sx, 0, sz, 0.04, 0.1, 0.04, legs);
    const cw = (w - 0.36) / seat;
    for (let i = 0; i < seat; i++) {
      c.box(-w / 2 + 0.18 + i * cw + 0.01, 0.38, -0.2, cw - 0.02, 0.14, 0.6, color, { grad: 0.1 });
      c.box(-w / 2 + 0.18 + i * cw + 0.02, 0.5, -0.4, cw - 0.04, 0.42, 0.2, scale(color, 0.92), { grad: 0.12 });
    }
    for (const sx of [-w / 2, w / 2 - 0.18]) c.box(sx, 0.38, -0.42, 0.18, 0.28, 0.84, scale(color, 0.84), { grad: 0.1 });
  });
}

export function armchair(L: Local, lx: number, lz: number, rot: number, color: Rgb, legs: Rgb): void {
  sofa(L, lx, lz, rot, 0.95, color, legs, 'armchair');
}

export function pouf(L: Local, lx: number, lz: number, color: Rgb, r = 0.28): void {
  L.prism(lx, 0, lz, r, r * 0.92, 0.36, 8, color);
  L.prism(lx, 0.36, lz, r * 0.92, r * 0.7, 0.04, 8, scale(color, 1.1));
}

export function barStool(L: Local, lx: number, lz: number, color: Rgb): void {
  L.prism(lx, 0, lz, 0.2, 0.2, 0.025, 8, BLACK);
  L.prism(lx, 0.02, lz, 0.03, 0.03, 0.62, 5, BLACK);
  L.prism(lx, 0.64, lz, 0.19, 0.17, 0.08, 8, color);
}

// ------------------------------------------------------------------ tables

export function roundTable(L: Local, lx: number, lz: number, r: number, h: number, top: Rgb, base: Rgb): void {
  L.prism(lx, 0, lz, r * 0.45, r * 0.45, 0.025, 10, base);
  L.prism(lx, 0.02, lz, 0.05, 0.05, h - 0.06, 6, base);
  L.prism(lx, h - 0.05, lz, r, r, 0.05, 12, top);
}

export function sideTable(L: Local, lx: number, lz: number, S: InteriorScheme, lamp = true): void {
  L.box(lx - 0.25, 0.48, lz - 0.25, 0.5, 0.04, 0.5, S.wood);
  for (const [px, pz] of [[-0.22, -0.22], [0.2, -0.22], [-0.22, 0.2], [0.2, 0.2]] as const) L.box(lx + px, 0, lz + pz, 0.03, 0.48, 0.03, S.dark);
  if (lamp) tableLamp(L, lx, 0.52, lz, S);
}

export function tableLamp(L: Local, lx: number, ly: number, lz: number, S: InteriorScheme): void {
  L.prism(lx, ly, lz, 0.07, 0.07, 0.02, 8, S.brass);
  L.prism(lx, ly + 0.02, lz, 0.015, 0.015, 0.22, 4, S.brass);
  L.prism(lx, ly + 0.2, lz, 0.1, 0.07, 0.14, 8, WARM, { emissive: 0.7 });
}

// ------------------------------------------------------------------ plants and lights

export function tallPlant(L: Local, lx: number, lz: number, S: InteriorScheme, s = 1, seed = 0): void {
  L.prism(lx, 0, lz, 0.2 * s, 0.16 * s, 0.4 * s, 8, S.pot);
  L.prism(lx, 0.4 * s, lz, 0.17 * s, 0.17 * s, 0.02, 8, hex('#3b2a20'));
  L.limb([lx, 0.4 * s, lz], [lx + 0.05 * s, 1.2 * s, lz], 0.05 * s, 0.05 * s, 0.03 * s, 0.03 * s, hex('#6b5238'));
  const g = [hex('#2f7d46'), hex('#3f8a55'), hex('#5fa862'), hex('#2b6a40')];
  const offs = [[0, 1.55, 0, 0.34], [0.2, 1.3, 0.1, 0.26], [-0.2, 1.2, -0.08, 0.26], [0.05, 1.05, -0.2, 0.22], [-0.05, 1.85, 0.05, 0.22]] as const;
  offs.forEach(([ox, oy, oz, r], i) => L.blob(lx + ox * s, oy * s, lz + oz * s, r * s, r * 1.25 * s, r * s, g[(i + seed) % g.length], 6));
}

export function deskPlant(L: Local, lx: number, ly: number, lz: number, S: InteriorScheme): void {
  L.prism(lx, ly, lz, 0.06, 0.05, 0.1, 6, S.pot);
  L.blob(lx, ly + 0.2, lz, 0.1, 0.12, 0.1, hex('#3f8a55'), 6);
  L.blob(lx + 0.04, ly + 0.28, lz, 0.06, 0.08, 0.06, hex('#5fa862'), 5);
}

export function floorLamp(L: Local, lx: number, lz: number, S: InteriorScheme, h = 1.6): void {
  L.prism(lx, 0, lz, 0.14, 0.14, 0.03, 8, S.dark);
  L.prism(lx, 0.03, lz, 0.015, 0.015, h - 0.2, 4, S.brass);
  L.prism(lx, h - 0.25, lz, 0.2, 0.14, 0.26, 8, WARM, { emissive: 0.75 });
  const [wx, wz] = L.pt(lx, lz);
  L.b.halo(wx, L.y0 + h - 0.12, wz, 1.5, WARM, 0.8);
}

// ------------------------------------------------------------------ storage and walls

export function bookcase(L: Local, lx: number, lz: number, w: number, h: number, S: InteriorScheme, rnd: () => number): void {
  const c = L.sub(lx, lz);
  c.box(-w / 2, 0, -0.16, w, h, 0.03, S.woodDark);
  c.box(-w / 2, 0, -0.16, 0.04, h, 0.32, S.woodDark);
  c.box(w / 2 - 0.04, 0, -0.16, 0.04, h, 0.32, S.woodDark);
  const books = [hex('#c0504d'), hex('#4f81bd'), hex('#9bbb59'), hex('#f2c14e'), hex('#e8e4d8'), hex('#8064a2'), hex('#2b8aa0')];
  const levels = Math.max(2, Math.floor(h / 0.45));
  for (let l = 0; l <= levels; l++) {
    const sy = (l * (h - 0.04)) / levels;
    c.box(-w / 2, sy, -0.16, w, 0.035, 0.32, S.woodDark);
    if (l === levels) break;
    let at = -w / 2 + 0.07;
    while (at < w / 2 - 0.16) {
      if (rnd() < 0.15) { at += 0.2; continue; }
      const bw = 0.07 + rnd() * 0.08, bh = 0.2 + rnd() * 0.1;
      c.box(at, sy + 0.035, -0.12, bw, Math.min(bh, (h - 0.04) / levels - 0.06), 0.2, books[Math.floor(rnd() * books.length)], { skip: ['north'] });
      at += bw + 0.006;
    }
  }
}

export function credenza(L: Local, lx: number, lz: number, w: number, S: InteriorScheme, top = S.marble): void {
  const c = L.sub(lx, lz);
  c.box(-w / 2, 0.1, -0.24, w, 0.62, 0.48, S.woodDark, { grad: 0.08 });
  c.box(-w / 2, 0.72, -0.26, w, 0.04, 0.52, top);
  c.box(-w / 2 + 0.04, 0, -0.2, 0.05, 0.1, 0.4, S.dark);
  c.box(w / 2 - 0.09, 0, -0.2, 0.05, 0.1, 0.4, S.dark);
  const doors = Math.max(2, Math.round(w / 0.6));
  for (let i = 1; i < doors; i++) c.box(-w / 2 + (i * w) / doors - 0.006, 0.12, 0.236, 0.012, 0.58, 0.006, scale(S.woodDark, 0.6));
  for (let i = 0; i < doors; i++) c.box(-w / 2 + ((i + 0.5) * w) / doors - 0.09, 0.5, 0.242, 0.18, 0.016, 0.014, S.brass);
}

export function rug(b: MeshBuilder, x: number, y: number, z: number, w: number, d: number, color: Rgb, border: Rgb): void {
  b.box(x, y + 0.012, z, w, 0.014, d, border, { skip: ['bottom'], shade: false, emissive: 0.45 });
  b.box(x + 0.12, y + 0.026, z + 0.12, Math.max(0.1, w - 0.24), 0.006, Math.max(0.1, d - 0.24), color, { skip: ['bottom'], shade: false, emissive: 0.45 });
}

/** A wall display on a bracket: bezel, glowing panel and a slim soundbar below. `w` is the panel width. */
export function wallDisplay(L: Local, lx: number, ly: number, lz: number, w: number, h: number, live: boolean, S: InteriorScheme): void {
  const c = L.sub(lx, lz);
  c.box(-w / 2 - 0.04, ly - 0.04, -0.04, w + 0.08, h + 0.08, 0.07, BLACK);
  c.box(-w / 2, ly, 0.03, w, h, 0.012, live ? hex('#58f0cf') : hex('#1b3a52'), { emissive: live ? 0.95 : 0.4, shade: false });
  // Abstract blocks on the panel (not data): two bands and a mark.
  c.box(-w / 2 + 0.08, ly + h * 0.62, 0.044, w * 0.4, h * 0.08, 0.004, live ? hex('#e8fff9') : hex('#3a6a8c'), { emissive: live ? 1 : 0.6, shade: false });
  c.box(-w / 2 + 0.08, ly + h * 0.42, 0.044, w * 0.26, h * 0.06, 0.004, live ? hex('#e8fff9') : hex('#3a6a8c'), { emissive: live ? 0.9 : 0.5, shade: false });
  c.box(-w * 0.24, ly - 0.14, 0.0, w * 0.48, 0.07, 0.08, S.dark);
}

export function whiteboard(L: Local, lx: number, ly: number, lz: number, w: number, h: number, S: InteriorScheme): void {
  const c = L.sub(lx, lz);
  c.box(-w / 2 - 0.03, ly - 0.03, 0, w + 0.06, h + 0.06, 0.05, S.metal);
  c.box(-w / 2, ly, 0.045, w, h, 0.01, hex('#f4f6f8'), { emissive: 0.35, shade: false });
  c.box(-w / 2 + 0.1, ly + h * 0.62, 0.056, w * 0.35, 0.025, 0.004, hex('#3b6ea8'), { shade: false });
  c.box(-w / 2 + 0.1, ly + h * 0.42, 0.056, w * 0.5, 0.025, 0.004, hex('#a8453b'), { shade: false });
  c.box(-w / 2 + 0.1, ly + h * 0.22, 0.056, w * 0.28, 0.025, 0.004, hex('#2f7d46'), { shade: false });
  c.box(-w / 2, ly - 0.06, 0.04, w, 0.025, 0.07, S.metal);
}

export function waterCooler(L: Local, lx: number, lz: number): void {
  L.box(lx - 0.17, 0, lz - 0.17, 0.34, 0.95, 0.34, hex('#dfe6ea'), { grad: 0.1 });
  L.prism(lx, 0.95, lz, 0.13, 0.13, 0.34, 8, hex('#9fd4e6'), { emissive: 0.2 });
  L.box(lx - 0.06, 0.62, lz + 0.17, 0.12, 0.04, 0.03, hex('#3a8fd0'));
}

export function printerStand(L: Local, lx: number, lz: number, S: InteriorScheme): void {
  L.box(lx - 0.35, 0, lz - 0.25, 0.7, 0.72, 0.5, S.paper, { grad: 0.1 });
  L.box(lx - 0.32, 0.72, lz - 0.22, 0.64, 0.3, 0.44, hex('#d4dadf'), { grad: 0.1 });
  L.box(lx - 0.3, 0.9, lz + 0.22, 0.2, 0.05, 0.02, hex('#58f0cf'), { emissive: 0.8, shade: false });
  L.box(lx - 0.2, 0.74, lz + 0.22, 0.4, 0.015, 0.2, S.paper);
}

export function coatRack(L: Local, lx: number, lz: number, S: InteriorScheme): void {
  L.prism(lx, 0, lz, 0.2, 0.2, 0.03, 8, S.dark);
  L.prism(lx, 0.03, lz, 0.025, 0.025, 1.6, 4, S.dark);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + 0.6;
    L.limb([lx, 1.55, lz], [lx + Math.cos(a) * 0.2, 1.66, lz + Math.sin(a) * 0.2], 0.025, 0.025, 0.02, 0.02, S.brass);
  }
  L.box(lx - 0.1, 1.0, lz + 0.03, 0.2, 0.55, 0.1, mix(S.fabric, hex('#000000'), 0.2));
}

// ------------------------------------------------------------------ workstation

export type StationSpec = Readonly<{
  /** Optional key the caller chooses (a desk id). Never printed. */
  id?: string;
  x: number;
  z: number;
  /** Quarter turns. 0: the person sits on the +z side, facing -z, with the screen towards +z. */
  rot?: number;
  state?: StationState;
  /** A seed (agent id) when, and only when, the registry says someone is assigned to this desk. */
  occupantSeed?: string | null;
  /** The avatar style of the occupant (see `lib/hqAvatar.ts`); only used together with `occupantSeed`. */
  look?: FigureLook;
  pose?: SeatedPose;
  monitors?: 1 | 2 | 3;
  headset?: boolean;
  /** Draw the desk without a chair (a vacant station still has its chair; this removes both). */
  noChair?: boolean;
}>;

export type StationLook = Readonly<{ width?: number; depth?: number; deskTone?: Rgb; chairTone?: Rgb; lamp?: boolean; plant?: boolean; pedestal?: boolean; floor?: Rgb; lite?: boolean }>;

/**
 * One workstation: desk, pedestal, one to three monitors, keyboard, mouse, lamp, mug, chair and, only
 * when `occupantSeed` is set, the seated figure.
 */
export function workstation(b: MeshBuilder, y: number, spec: StationSpec, S: InteriorScheme, look: StationLook = {}): void {
  const W = look.width ?? 1.5, D = look.depth ?? 0.78;
  const L = new Local(b, spec.x, spec.z, (spec.rot ?? 0) + 2, y);
  const state = spec.state ?? (spec.occupantSeed ? 'assigned' : 'vacant');
  const screen = SCREEN_LOOK[state];
  const top = look.deskTone ?? S.wood;
  const H = 0.74;
  // Desk top with a pale edge, two panel legs, a modesty panel and a cable tray.
  L.box(-W / 2, H - 0.04, -D / 2, W, 0.04, D, top, { grad: 0.08 });
  L.box(-W / 2, H - 0.045, -D / 2, W, 0.012, D, scale(top, 0.7));
  L.box(-W / 2 + 0.03, 0, -D / 2 + 0.05, 0.035, H - 0.05, D - 0.1, S.dark);
  L.box(W / 2 - 0.065, 0, -D / 2 + 0.05, 0.035, H - 0.05, D - 0.1, S.dark);
  L.box(-W / 2 + 0.07, 0.3, D / 2 - 0.1, W - 0.14, 0.4, 0.02, scale(top, 0.8));
  const lite = look.lite === true;
  if (look.pedestal !== false && !lite) {
    L.box(W / 2 - 0.5, 0.05, -D / 2 + 0.06, 0.42, 0.6, D - 0.14, scale(S.paper, 0.92), { grad: 0.1 });
    for (let i = 0; i < 3; i++) L.box(W / 2 - 0.48, 0.1 + i * 0.19, -D / 2 + 0.047, 0.38, 0.015, 0.012, scale(S.paper, 0.6));
  }
  // Monitors face -z (the person).
  const n = spec.monitors ?? 1;
  const mw = n === 1 ? 0.58 : n === 2 ? 0.46 : 0.4;
  for (let i = 0; i < n; i++) {
    const cx = (i - (n - 1) / 2) * (mw + 0.03);
    L.box(cx - 0.13, H, 0.1, 0.26, 0.015, 0.16, BLACK);
    L.box(cx - 0.02, H, 0.16, 0.04, 0.2, 0.03, BLACK);
    L.box(cx - mw / 2, H + 0.2, 0.15, mw, 0.34, 0.035, BLACK);
    L.box(cx - mw / 2 + 0.02, H + 0.22, 0.128, mw - 0.04, 0.3, 0.008, screen.color, { emissive: screen.emissive, shade: false });
    if (state !== 'vacant') {
      for (let k = 0; k < 3; k++) L.box(cx - mw / 2 + 0.05, H + 0.27 + k * 0.08, 0.12, (mw - 0.1) * (0.5 + 0.15 * ((k + i) % 3)), 0.02, 0.004, hex('#f4fffb'), { emissive: Math.min(1, screen.emissive + 0.1), shade: false });
    }
  }
  L.box(-0.2, H, -0.2, 0.4, 0.015, 0.14, hex('#c9d1d9'));
  if (!lite) {
    L.box(-0.17, H + 0.015, -0.19, 0.34, 0.01, 0.1, BLACK);
    L.box(0.26, H, -0.16, 0.06, 0.02, 0.1, hex('#c9d1d9'));
    L.prism(-W / 2 + 0.22, H, 0.08, 0.04, 0.036, 0.09, 6, hex('#e8e4d8'));
    L.box(-W / 2 + 0.1, H, -0.12, 0.2, 0.02, 0.27, S.paper);
  }
  if (spec.headset) {
    L.box(0.34, H, 0.12, 0.1, 0.02, 0.08, BLACK);
    L.box(0.36, H + 0.02, 0.14, 0.06, 0.14, 0.04, BLACK);
  }
  if (look.lamp !== false && !lite) {
    L.box(W / 2 - 0.22, H, 0.2, 0.1, 0.015, 0.1, S.dark);
    L.limb([W / 2 - 0.17, H + 0.01, 0.25], [W / 2 - 0.2, H + 0.36, 0.2], 0.018, 0.018, 0.014, 0.014, S.dark);
    L.box(W / 2 - 0.32, H + 0.35, 0.12, 0.2, 0.025, 0.1, hex('#ffe9b8'), { emissive: 0.7, shade: false });
    const [lampX, lampZ] = L.pt(W / 2 - 0.22, 0.17);
    b.halo(lampX, y + H + 0.3, lampZ, 1.3, hex('#ffe0a0'), 0.7);
  }
  if (look.plant) deskPlant(L, W / 2 - 0.1, H, 0.27, S);
  if (look.floor) L.box(-W / 2 - 0.1, 0.016, -1.25, W + 0.2, 0.008, D + 1.55, scale(look.floor, 0.64), { skip: ['bottom'], shade: false, emissive: 0.3 });
  if (!spec.noChair) {
    taskChair(L, 0, -0.8, look.chairTone ?? S.fabric);
    if (spec.occupantSeed) {
      const [fx, fz] = L.pt(0, -0.82);
      seatedFigure(b, fx, y, fz, { seed: spec.occupantSeed, rot: (spec.rot ?? 0) + 2, pose: spec.pose ?? 'typing', headset: spec.headset, look: spec.look });
    }
  }
}
