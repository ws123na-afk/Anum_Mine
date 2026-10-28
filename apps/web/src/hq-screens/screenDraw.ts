/**
 * Draws the faces into the shared atlas (docs/ANUM_MINE_HQ_LIVE_SCREENS.md). `ScreenAtlas.update` draws only the cells whose face changed,
 * at most once a second each, and says when the next one is due; the renderer uploads the same canvas again (`HqRenderer.setScreens`), nothing
 * is rebuilt. Text is large and high contrast, shaped right to left in Arabic. Under reduced motion the content is the same and still updates;
 * only the scan line and the cursor stand still.
 */
import type { ScreenLayout } from './screenSites.ts';
import { MON_H, MON_W, WALL_H, WALL_W } from './screenSites.ts';
import { monitorKey, wallKey, type MonitorFace, type ScreenTone, type WallFace } from './screenModel.ts';

/** A cell is drawn again at most this often. */
export const SCREEN_MIN_MS = 1000;

const LATIN = 'system-ui, "Segoe UI", sans-serif';
const ARABIC = '"Noto Sans Arabic", Inter, "Segoe UI", Tahoma, system-ui, sans-serif';
const hasArabic = (s: string): boolean => /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/.test(s);

type Ctx = Pick<CanvasRenderingContext2D, 'fillRect' | 'fillText' | 'measureText' | 'beginPath' | 'arc' | 'fill' | 'save' | 'restore' | 'translate' | 'scale' | 'clearRect'> & {
  fillStyle: unknown; font: string; textAlign: CanvasTextAlign; textBaseline: CanvasTextBaseline; direction: CanvasDirection; globalAlpha: number;
};

const BG: Record<ScreenTone, string> = { work: '#0d4f46', wait: '#6b5212', meet: '#3d2f6e', block: '#6e1f1f', idle: '#1c3552', off: '#11161c', dim: '#262d36' };
const FG: Record<ScreenTone, string> = { work: '#d9fff6', wait: '#fff1c9', meet: '#ece4ff', block: '#ffe0e0', idle: '#dbe8ff', off: '#8a96a3', dim: '#b3bcc7' };

const font = (text: string, size: number, weight: number, rtl: boolean): string => `${weight} ${Math.round(size)}px ${hasArabic(text) || rtl ? ARABIC : LATIN}`;

/** Draws `text` at `size` (shrunk to fit `maxW`) with its start edge at `x`. */
function line(ctx: Ctx, text: string, x: number, y: number, size: number, min: number, maxW: number, weight: number, rtl: boolean): void {
  let s = size;
  ctx.font = font(text, s, weight, rtl);
  while (s > min && ctx.measureText(text).width > maxW) { s -= 2; ctx.font = font(text, s, weight, rtl); }
  ctx.fillText(text, x, y, maxW);
}

/**
 * One monitor into the cell at (x, y). The glass has `aspect` (width over height); the cell is 2:1, so the face is drawn in a logical box
 * of the glass's own proportions and squeezed to the cell, which the quad stretches back: nothing looks stretched on the screen.
 */
export function drawMonitor(ctx: Ctx, x: number, y: number, k: number, face: MonitorFace, aspect: number, near: boolean, phase: number, animate: boolean): void {
  const H = MON_H, W = Math.round(H * Math.max(0.8, Math.min(3, aspect)));
  ctx.save();
  ctx.translate(x, y);
  ctx.scale((MON_W / W) * k, k);
  ctx.fillStyle = BG[face.tone];
  ctx.fillRect(0, 0, W, H);
  if (near) {
    const rtl = face.rtl, pad = 8, maxW = W - 2 * pad;
    const at = rtl ? W - pad : pad;
    ctx.textAlign = rtl ? 'right' : 'left';
    ctx.textBaseline = 'alphabetic';
    ctx.direction = rtl ? 'rtl' : 'ltr';
    ctx.fillStyle = FG[face.tone];
    line(ctx, face.headline, at, 30, 26, 14, maxW, 700, rtl);
    ctx.globalAlpha = 0.9;
    if (face.record) line(ctx, face.record, at, 58, 18, 11, maxW, 500, rtl);
    if (face.elapsed) line(ctx, face.elapsed, at, 84, 22, 12, maxW, 700, rtl);
    if (face.foot) line(ctx, face.foot, at, H - 10, 14, 9, maxW, 500, rtl);
    ctx.globalAlpha = 1;
    if (face.scan) {
      // Activity only while the presence is fresh and the agent is working or in a meeting: a sweeping bar and a cursor that blinks once a second.
      const step = animate ? phase % 4 : 1;
      ctx.globalAlpha = 0.25;
      ctx.fillRect(0, 36 + step * 18, W, 4);
      ctx.globalAlpha = 1;
      if (!animate || phase % 2 === 0) ctx.fillRect(rtl ? pad : W - pad - 8, H - 26, 8, 12);
    }
  } else {
    // Far away: a plain face of the state's colour with a bar (a lit screen reads as lit), no text nobody could read.
    ctx.fillStyle = FG[face.tone];
    ctx.globalAlpha = face.tone === 'off' ? 0.12 : 0.5;
    ctx.fillRect(W * 0.12, H * 0.3, W * 0.76, H * 0.12);
    ctx.fillRect(W * 0.12, H * 0.55, W * 0.5, H * 0.12);
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

const BRASS = '#d9b25f', TEAL = '#45d0b5', AMBER = '#ffc857';

/** The wall into the cell at (x, y): title, the four numbers, agents, the newest flow items, the tyre figures, and the time and link. */
export function drawWall(ctx: Ctx, x: number, y: number, k: number, face: WallFace, aspect: number, near: boolean): void {
  const H = WALL_H, W = Math.round(H * Math.max(1.4, Math.min(3.4, aspect)));
  ctx.save();
  ctx.translate(x, y);
  ctx.scale((WALL_W / W) * k, k);
  ctx.fillStyle = '#0a1620';
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = TEAL;
  ctx.fillRect(0, H - 5, W, 5);
  const rtl = face.rtl, pad = 12, inner = W - 2 * pad;
  const start = rtl ? W - pad : pad, end = rtl ? pad : W - pad;
  ctx.textBaseline = 'alphabetic';
  ctx.direction = rtl ? 'rtl' : 'ltr';
  const align = (r: boolean) => { ctx.textAlign = r ? 'right' : 'left'; };
  // Head: title and the link state.
  align(rtl);
  ctx.fillStyle = '#f2f6fa';
  line(ctx, face.title, start, 24, 20, 12, inner * 0.6, 700, rtl);
  if (face.statusText) {
    align(!rtl);
    ctx.fillStyle = face.status === 'stale' ? AMBER : face.status === 'off' ? '#9fb0bf' : TEAL;
    line(ctx, face.statusText, end, 24, 18, 11, inner * 0.3, 700, rtl);
  }
  // The four numbers.
  const tiles = face.tiles, cols = Math.max(1, tiles.length), gap = 8;
  const tw = (inner - gap * (cols - 1)) / cols;
  tiles.forEach((tile, i) => {
    const tx = pad + i * (tw + gap);
    ctx.fillStyle = '#12303f';
    ctx.fillRect(tx, 34, tw, near ? 62 : 84);
    ctx.fillStyle = tile.ready ? '#f2f6fa' : '#8497a8';
    ctx.textAlign = 'center';
    ctx.direction = rtl ? 'rtl' : 'ltr';
    line(ctx, tile.text, tx + tw / 2, near ? 68 : 82, tile.ready ? (near ? 34 : 44) : 15, 10, tw - 8, 800, rtl);
    ctx.fillStyle = '#9fd8cc';
    if (near) line(ctx, tile.label, tx + tw / 2, 88, 14, 9, tw - 8, 500, rtl);
  });
  if (!near) {
    // Far: the numbers are the screen; the labels come with the close view.
    ctx.restore();
    return;
  }
  // Agents by state.
  align(rtl);
  ctx.fillStyle = BRASS;
  line(ctx, `${face.agents.label}: ${face.agents.text}`, start, 114, 16, 10, inner, 700, rtl);
  ctx.fillStyle = '#c7d3de';
  if (face.agents.parts.length) line(ctx, face.agents.parts.map((p) => p.text).join('  ·  '), start, 133, 14, 9, inner, 500, rtl);
  // The latest flow items, and for a tyre building the tyre figures beside them.
  const split = face.tyre ? inner * 0.5 : inner;
  ctx.fillStyle = BRASS;
  line(ctx, face.flowTitle, start, 156, 15, 10, split - 10, 700, rtl);
  ctx.fillStyle = '#c7d3de';
  const rows = face.flowRows.length ? face.flowRows : face.flowNote ? [face.flowNote] : [];
  rows.slice(0, 4).forEach((row, i) => line(ctx, row, start, 174 + i * 17, 13, 9, split - 10, 500, rtl));
  if (face.tyre) {
    const tx = rtl ? W - pad - split : pad + split;
    const tstart = rtl ? tx + split - 8 : tx + 8;
    ctx.fillStyle = BRASS;
    line(ctx, face.tyre.title, tstart, 156, 15, 10, split - 16, 700, rtl);
    if (face.tyre.note) { ctx.fillStyle = '#c7d3de'; line(ctx, face.tyre.note, tstart, 176, 14, 9, split - 16, 500, rtl); }
    face.tyre.items.slice(0, 6).forEach((item, i) => {
      const col = i % 3, row = Math.floor(i / 3), cw = (split - 16) / 3;
      const cx = rtl ? tx + split - 8 - col * cw : tx + 8 + col * cw;
      ctx.fillStyle = '#f2f6fa';
      line(ctx, item.text, cx, 184 + row * 36, 20, 11, cw - 6, 800, rtl);
      ctx.fillStyle = '#9fd8cc';
      line(ctx, item.label, cx, 198 + row * 36, 10, 7, cw - 6, 500, rtl);
    });
  }
  // The foot: when it was read.
  if (face.updated) { align(rtl); ctx.fillStyle = face.status === 'stale' ? AMBER : '#8fe3d1'; line(ctx, face.updated, start, H - 12, 13, 9, inner, 600, rtl); }
  ctx.restore();
}

export type CanvasMaker = (width: number, height: number) => { canvas: HTMLCanvasElement; ctx: Ctx } | null;
const makeCanvas: CanvasMaker = (width, height) => {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d');
  return ctx ? { canvas, ctx: ctx as unknown as Ctx } : null;
};

export type ScreenFace =
  | Readonly<{ kind: 'monitor'; face: MonitorFace; near: boolean; aspect: number; animate: boolean; phase: number }>
  | Readonly<{ kind: 'wall'; face: WallFace; near: boolean; aspect: number }>;

const keyOf = (f: ScreenFace): string => (f.kind === 'monitor' ? `m|${f.near ? 1 : 0}|${monitorKey(f.face)}|${f.face.scan && f.animate ? f.phase % 4 : 0}` : `w|${f.near ? 1 : 0}|${wallKey(f.face)}`);

export type ScreenUpdate = Readonly<{ drawn: number; nextDueMs: number | null }>;

export class ScreenAtlas {
  readonly canvas: HTMLCanvasElement | null;
  /** Moves every time at least one cell was drawn: the renderer uploads the canvas again when it moved. */
  version = 0;
  draws = 0;
  private readonly ctx: Ctx | null;
  private readonly keys = new Map<string, string>();
  private readonly drawnAt = new Map<string, number>();
  /** What is on the canvas now, by cell (the stage reports it in a data attribute for QA). */
  readonly drawn = new Map<string, ScreenFace>();

  readonly layout: ScreenLayout;

  constructor(layout: ScreenLayout, make: CanvasMaker = makeCanvas) {
    this.layout = layout;
    const made = make(layout.width, layout.height);
    this.canvas = made?.canvas ?? null;
    this.ctx = made?.ctx ?? null;
    if (this.ctx) { this.ctx.fillStyle = '#11161c'; this.ctx.fillRect(0, 0, layout.width, layout.height); }
  }

  /** Draws the cells whose face changed and are due. `faces` maps `monitor:<cell>` or `wall:<cell>` to its face. */
  update(faces: ReadonlyMap<string, ScreenFace>, now: number): ScreenUpdate {
    if (!this.ctx) return { drawn: 0, nextDueMs: null };
    let drawn = 0;
    let nextDue: number | null = null;
    for (const [id, face] of faces) {
      const key = keyOf(face);
      if (this.keys.get(id) === key) continue;
      const last = this.drawnAt.get(id);
      if (last !== undefined && now - last < SCREEN_MIN_MS) {
        const wait = SCREEN_MIN_MS - (now - last);
        nextDue = nextDue === null ? wait : Math.min(nextDue, wait);
        continue;
      }
      const index = Number(id.split(':')[1]);
      const c = this.layout.cell(face.kind, index);
      if (face.kind === 'monitor') drawMonitor(this.ctx, c.x, c.y, 1, face.face, face.aspect, face.near, face.phase, face.animate);
      else drawWall(this.ctx, c.x, c.y, 1, face.face, face.aspect, face.near);
      this.keys.set(id, key);
      this.drawnAt.set(id, now);
      this.drawn.set(id, face);
      drawn += 1;
    }
    if (drawn > 0) { this.version += 1; this.draws += drawn; }
    return { drawn, nextDueMs: nextDue };
  }
}
