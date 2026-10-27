/**
 * The live board atlas (docs/ANUM_MINE_HQ_BILLBOARDS.md, "The board"): one small canvas, a cell per board, drawn in place. `BoardAtlas.update`
 * draws only the boards whose face changed, at most once a second each, and says when the next one is due; the renderer uploads the same
 * canvas again (`HqRenderer.setBoards`), nothing is rebuilt. Text is drawn the way the facade signs are: large, high contrast, shaped right to
 * left in Arabic mode, nothing animated (a reduced motion setting changes nothing here: the text still updates, a Live tick is a still dot).
 */
import { boardAtlasLayout, type BoardAtlasLayout } from './billboardSites.ts';
import { faceKey, type BoardFace, type BoardTone } from './boardFace.ts';

/** A board is drawn again at most this often (per board). */
export const BOARD_MIN_MS = 1000;

const LATIN = 'system-ui, "Segoe UI", sans-serif';
const ARABIC = '"Noto Sans Arabic", Inter, "Segoe UI", Tahoma, system-ui, sans-serif';
const hasArabic = (text: string): boolean => /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/.test(text);

const TONE: Record<BoardTone, string> = { ok: '#f2f6fa', stale: '#ffc857', off: '#9fb0bf' };
const STATUS_DOT: Record<string, string> = { working: '#45d0b5', meeting: '#ffc857', blocked: '#ff6b6b', waiting_approval: '#ffc857', idle: '#8aa4ff', queued: '#8aa4ff', paused: '#9fb0bf', empty: '#9fb0bf', unavailable: '#9fb0bf' };

type Ctx = Pick<CanvasRenderingContext2D, 'fillRect' | 'fillText' | 'measureText' | 'beginPath' | 'arc' | 'fill' | 'createLinearGradient' | 'save' | 'restore' | 'clearRect'> & {
  fillStyle: unknown; font: string; textAlign: CanvasTextAlign; textBaseline: CanvasTextBaseline; direction: CanvasDirection; globalAlpha: number;
};

/** Words onto at most `lines` lines no wider than `maxW`; a last line that still does not fit ends in an ellipsis. */
export function wrapText(measure: (text: string) => number, text: string, maxW: number, lines: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const out: string[] = [];
  let line = '';
  for (let i = 0; i < words.length; i++) {
    const next = line ? `${line} ${words[i]}` : words[i];
    if (measure(next) <= maxW || !line) { line = next; continue; }
    out.push(line);
    line = words[i];
    if (out.length === lines - 1) { line = words.slice(i).join(' '); break; }
  }
  if (line) out.push(line);
  const last = out.length - 1;
  if (out.length && measure(out[last]) > maxW) {
    let cut = out[last];
    while (cut.length > 1 && measure(`${cut}…`) > maxW) cut = cut.slice(0, -1);
    out[last] = `${cut}…`;
  }
  return out.slice(0, lines);
}

/** Draws one board into the cell whose top left corner is (x, y); `k` scales the 512 x 256 design to the cell. */
export function drawBoardCell(ctx: Ctx, x: number, y: number, k: number, face: BoardFace): void {
  const W = 512, H = 256;
  const bg = ctx.createLinearGradient(x, y, x, y + H * k);
  bg.addColorStop(0, '#142c3d'); bg.addColorStop(1, '#0b1722');
  ctx.fillStyle = bg;
  ctx.fillRect(x, y, W * k, H * k);
  ctx.fillStyle = face.accent ?? '#45d0b5';
  ctx.fillRect(x, y + (H - 8) * k, W * k, 8 * k);
  const rtl = face.rtl;
  const edge = 22, maxW = W - 2 * edge;
  const left = x + edge * k, right = x + (W - edge) * k;
  const at = rtl ? right : left;
  ctx.textAlign = rtl ? 'right' : 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.direction = rtl ? 'rtl' : 'ltr';
  const font = (text: string, size: number, weight = 700) => `${weight} ${Math.max(6, Math.round(size * k))}px ${hasArabic(text) || (rtl && face.fonts) ? ARABIC : LATIN}`;
  const fit = (text: string, size: number, min: number, weight = 700): number => {
    let s = size;
    ctx.font = font(text, s, weight);
    while (s > min && ctx.measureText(text).width > maxW * k) { s -= 2; ctx.font = font(text, s, weight); }
    return s;
  };
  // The status dot and the name.
  const dot = STATUS_DOT[face.status] ?? '#9fb0bf';
  const dotX = rtl ? right - 9 * k : left + 9 * k;
  ctx.fillStyle = dot;
  ctx.beginPath(); ctx.arc(dotX, y + 40 * k, 9 * k, 0, Math.PI * 2); ctx.fill();
  const nameEdge = rtl ? right - 28 * k : left + 28 * k;
  ctx.fillStyle = '#f2f6fa';
  const nameSize = fit(face.name, 44, 24);
  ctx.fillText(face.name, nameEdge, y + 54 * k, (maxW - 28) * k);
  void nameSize;
  let line = 84;
  // What the building does: the fixed sentence, at most two lines.
  if (face.purpose) {
    ctx.fillStyle = '#9fd8cc';
    ctx.font = font(face.purpose, 20, 500);
    for (const text of wrapText((s) => ctx.measureText(s).width, face.purpose, maxW * k, 2)) { ctx.fillText(text, at, y + (line + 18) * k, maxW * k); line += 24; }
  }
  // The live headline, large.
  ctx.fillStyle = TONE[face.tone];
  fit(face.headline, 38, 22);
  ctx.fillText(face.headline, at, y + 166 * k, maxW * k);
  if (face.states) { ctx.fillStyle = '#c7d3de'; fit(face.states, 22, 14, 500); ctx.fillText(face.states, at, y + 192 * k, maxW * k); }
  if (face.work) { ctx.fillStyle = face.workConnected ? '#c7d3de' : '#8497a8'; fit(face.work, 21, 13, 500); ctx.fillText(face.work, at, y + 214 * k, maxW * k); }
  // The foot: when it was verified (or that it is stale), and the Live tick only while the stream is connected.
  if (face.foot) {
    ctx.fillStyle = face.tone === 'stale' ? '#ffc857' : '#8fe3d1';
    const room = face.live ? maxW - 96 : maxW;
    let s = 20; ctx.font = font(face.foot, s, 600);
    while (s > 12 && ctx.measureText(face.foot).width > room * k) { s -= 2; ctx.font = font(face.foot, s, 600); }
    ctx.fillText(face.foot, at, y + 240 * k, room * k);
  }
  if (face.live) {
    const lx = rtl ? left : right;
    ctx.textAlign = rtl ? 'left' : 'right';
    ctx.fillStyle = '#45d0b5';
    ctx.font = font(face.liveLabel, 20, 700);
    const w = ctx.measureText(face.liveLabel).width;
    ctx.fillText(face.liveLabel, lx, y + 240 * k, 80 * k);
    ctx.beginPath(); ctx.arc(rtl ? lx + w + 10 * k : lx - w - 10 * k, y + 233 * k, 6 * k, 0, Math.PI * 2); ctx.fill();
  }
}

export type CanvasMaker = (width: number, height: number) => { canvas: HTMLCanvasElement; ctx: Ctx } | null;
const makeCanvas: CanvasMaker = (width, height) => {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d');
  return ctx ? { canvas, ctx: ctx as unknown as Ctx } : null;
};

export type BoardUpdate = Readonly<{ drawn: number; nextDueMs: number | null }>;

export class BoardAtlas {
  readonly layout: BoardAtlasLayout;
  readonly canvas: HTMLCanvasElement | null;
  /** Moves every time at least one board was drawn: the renderer uploads the canvas again when it moved. */
  version = 0;
  private readonly ctx: Ctx | null;
  private readonly keys: string[] = [];
  private readonly drawnAt: number[] = [];
  /** How many cells were ever drawn (for tests and the stage's data attribute). */
  draws = 0;
  /** The faces as drawn on the canvas now, by board (what the stage reports in its data attribute; a held back board still shows its old face here). */
  readonly drawn: BoardFace[] = [];

  constructor(count: number, make: CanvasMaker = makeCanvas) {
    this.layout = boardAtlasLayout(count);
    const made = make(this.layout.width, this.layout.height);
    this.canvas = made?.canvas ?? null;
    this.ctx = made?.ctx ?? null;
  }

  /** Draws the boards whose face changed and are due (a board is drawn at most once per `BOARD_MIN_MS`); reports when the next changed one is due. */
  update(faces: readonly BoardFace[], now: number): BoardUpdate {
    if (!this.ctx) return { drawn: 0, nextDueMs: null };
    let drawn = 0;
    let nextDue: number | null = null;
    faces.forEach((face, i) => {
      const key = faceKey(face);
      if (this.keys[i] === key) return;
      const last = this.drawnAt[i];
      if (last !== undefined && now - last < BOARD_MIN_MS) {
        const wait = BOARD_MIN_MS - (now - last);
        nextDue = nextDue === null ? wait : Math.min(nextDue, wait);
        return;
      }
      const { x, y } = this.layout.origin(i);
      drawBoardCell(this.ctx!, x, y, this.layout.cellW / 512, face);
      this.keys[i] = key;
      this.drawn[i] = face;
      this.drawnAt[i] = now;
      drawn += 1;
    });
    if (drawn > 0) { this.version += 1; this.draws += drawn; }
    return { drawn, nextDueMs: nextDue };
  }
}

export type BoardFeed = Readonly<{ atlas: BoardAtlas; touch: () => void; stop: () => void }>;

/**
 * Keeps an atlas current: `touch` reads the faces now and draws what changed; a board held back by the one second rule is drawn when it is due;
 * and a slow tick reads again so a record that stops being confirmed turns "Stale" without any new data. The tick stops while the tab is hidden.
 */
export function startBoardFeed(args: Readonly<{ count: number; read: () => readonly BoardFace[]; onDraw: (atlas: BoardAtlas) => void; tickMs?: number; make?: CanvasMaker; now?: () => number }>): BoardFeed {
  const atlas = new BoardAtlas(args.count, args.make);
  const clock = args.now ?? Date.now;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const touch = (): void => {
    if (stopped) return;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    const result = atlas.update(args.read(), clock());
    if (result.drawn > 0) args.onDraw(atlas);
    if (result.nextDueMs !== null) timer = setTimeout(touch, result.nextDueMs + 5);
  };
  const tick = setInterval(() => { if (typeof document === 'undefined' || document.visibilityState === 'visible') touch(); }, args.tickMs ?? 5000);
  touch();
  return { atlas, touch, stop: () => { stopped = true; clearInterval(tick); if (timer !== null) clearTimeout(timer); } };
}
