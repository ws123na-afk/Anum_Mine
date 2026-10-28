/**
 * The live board atlas (docs/ANUM_MINE_HQ_BILLBOARDS.md, "The board"): one small canvas, a cell per board, drawn in place. `BoardAtlas.update`
 * draws only the boards whose face changed, at most once a second each, and says when the next one is due; the renderer uploads the same
 * canvas again (`HqRenderer.setBoards`), nothing is rebuilt. Text is drawn the way the facade signs are: large, high contrast, shaped right to
 * left in Arabic mode. The one movement is real: when a board's text changes, the new face fades in over the old one in `BOARD_FADE_MS`
 * (a few timer steps, no frame loop; none under a reduced motion setting, where the new text simply replaces the old). A Live tick is a still dot.
 */
import { boardAtlasLayout, type BoardAtlasLayout } from './billboardSites.ts';
import { faceKey, type BoardFace, type BoardTone } from './boardFace.ts';
import { iconPath } from '../lib/hqIcons.ts';

/** A board is drawn again at most this often (per board). */
export const BOARD_MIN_MS = 1000;
/** How long a changed board takes to fade from its old face to its new one. */
export const BOARD_FADE_MS = 400;
/** The fade is drawn in steps this far apart (8 steps over 400 ms): a timer, not a frame loop, so an idle campus draws nothing. */
export const BOARD_FADE_STEP_MS = 50;
/** Boards that fade at the same time, at most; any more change at once (a language switch) and the rest change without a fade. */
export const BOARD_FADE_MAX = 4;

const LATIN = 'system-ui, "Segoe UI", sans-serif';
const ARABIC = '"Noto Sans Arabic", Inter, "Segoe UI", Tahoma, system-ui, sans-serif';
const hasArabic = (text: string): boolean => /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/.test(text);

const TONE: Record<BoardTone, string> = { ok: '#ffffff', stale: '#ffd27a', off: '#d3dde6' };
const STATUS_DOT: Record<string, string> = { working: '#45d0b5', meeting: '#ffc857', blocked: '#ff6b6b', waiting_approval: '#ffc857', idle: '#8aa4ff', queued: '#8aa4ff', paused: '#9fb0bf', empty: '#9fb0bf', unavailable: '#9fb0bf' };
/** The standard header band (no brand colour, or one that fails the contrast check) and the standard accent. */
const STANDARD_HEADER = '#183a4f';
const TEAL = '#45d0b5';

type Ctx = Pick<CanvasRenderingContext2D, 'fillRect' | 'fillText' | 'measureText' | 'beginPath' | 'arc' | 'fill' | 'createLinearGradient' | 'save' | 'restore' | 'clearRect'> & {
  fillStyle: unknown; font: string; textAlign: CanvasTextAlign; textBaseline: CanvasTextBaseline; direction: CanvasDirection; globalAlpha: number;
  /** Used for the logo plate and the icons; a context without them draws neither (the plain header). */
  strokeStyle?: unknown; lineWidth?: number; lineCap?: CanvasLineCap; lineJoin?: CanvasLineJoin;
  translate?: (x: number, y: number) => void; scale?: (x: number, y: number) => void; stroke?: (path: Path2D) => void;
  rect?: (x: number, y: number, w: number, h: number) => void; roundRect?: (x: number, y: number, w: number, h: number, r: number) => void;
  drawImage?: (image: CanvasImageSource, ...xywh: number[]) => void;
};

/** A decoded logo the campus sign loader holds (`hq-logo-signs/campusSigns.ts`), looked up by `buildingId:sha`. */
export type BoardLogo = Readonly<{ image: CanvasImageSource; width: number; height: number }>;
export type LogoLookup = (key: string) => BoardLogo | null;

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

/** One icon of the HQ set (`lib/hqIcons.ts`), `size` design units square, top left at (x, y). */
function drawIcon(ctx: Ctx, key: string, x: number, y: number, size: number, color: string): void {
  if (typeof Path2D === 'undefined' || !ctx.stroke || !ctx.translate || !ctx.scale) return;
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(size / 24, size / 24);
  ctx.strokeStyle = color; ctx.lineWidth = 2.1; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.stroke(new Path2D(iconPath(key)));
  ctx.restore();
}

/** The owner's logo on a white plate (as given, never recoloured), the logo fitted inside; false when it could not be drawn. */
function drawLogo(ctx: Ctx, logo: BoardLogo, x: number, y: number, size: number): boolean {
  if (!ctx.drawImage || !(logo.width > 0) || !(logo.height > 0)) return false;
  ctx.fillStyle = '#ffffff';
  ctx.beginPath();
  if (typeof ctx.roundRect === 'function') ctx.roundRect(x, y, size, size, size * 0.14); else ctx.rect?.(x, y, size, size);
  ctx.fill();
  const inner = size * 0.84, fit = Math.min(inner / logo.width, inner / logo.height);
  const w = logo.width * fit, h = logo.height * fit;
  try { ctx.drawImage(logo.image, x + (size - w) / 2, y + (size - h) / 2, w, h); } catch { return false; }
  return true;
}

/**
 * Draws one board into the cell whose top left corner is (x, y); `k` scales the 512 x 256 design to the cell.
 * Far away (`detail` false) the board is a header with the name and one key line, both large; the close view adds the detail lines.
 */
export function drawBoardCell(ctx: Ctx, x: number, y: number, k: number, face: BoardFace, logoOf?: LogoLookup): void {
  const W = 512, H = 256;
  const detail = face.detail;
  const rtl = face.rtl;
  const edge = 20;
  const bg = ctx.createLinearGradient(x, y, x, y + H * k);
  bg.addColorStop(0, '#11283a'); bg.addColorStop(1, '#08131c');
  ctx.fillStyle = bg;
  ctx.fillRect(x, y, W * k, H * k);
  // The header band: the owner's brand colour when it passes the contrast check, else the standard slate.
  const headH = detail ? 78 : 104;
  ctx.fillStyle = face.header?.fill ?? STANDARD_HEADER;
  ctx.fillRect(x, y, W * k, headH * k);
  ctx.fillStyle = face.accent ?? TEAL;
  ctx.fillRect(x, y + (H - 8) * k, W * k, 8 * k);
  if (!face.header) ctx.fillRect(x, y + headH * k, W * k, 4 * k);
  const px = (offset: number): number => (rtl ? x + (W - offset) * k : x + offset * k);
  ctx.textBaseline = 'alphabetic';
  ctx.direction = rtl ? 'rtl' : 'ltr';
  const align = (start: boolean): void => { ctx.textAlign = (start !== rtl) ? 'left' : 'right'; };
  const font = (text: string, size: number, weight = 700) => `${weight} ${Math.max(6, Math.round(size * k))}px ${hasArabic(text) || (rtl && face.fonts) ? ARABIC : LATIN}`;
  const fit = (text: string, size: number, min: number, maxW: number, weight = 700): number => {
    let s = size;
    ctx.font = font(text, s, weight);
    while (s > min && ctx.measureText(text).width > maxW * k) { s -= 2; ctx.font = font(text, s, weight); }
    return s;
  };
  // The logo, then the name.
  const plate = detail ? 60 : 80;
  const logo = face.logo && logoOf ? logoOf(face.logo) : null;
  const plateAt = (headH - plate) / 2;
  const drewLogo = logo ? drawLogo(ctx, logo, rtl ? x + (W - plateAt - plate) * k : x + plateAt * k, y + plateAt * k, plate * k) : false;
  const nameStart = drewLogo ? plateAt + plate + 16 : edge;
  const nameW = W - nameStart - edge;
  ctx.fillStyle = face.header?.text ?? '#ffffff';
  align(true);
  const nameSize = fit(face.name, detail ? 44 : 60, detail ? 24 : 30, nameW);
  ctx.fillText(face.name, px(nameStart), y + (headH / 2 + nameSize * 0.36) * k, nameW * k);
  const dot = (cy: number, r: number): void => {
    ctx.fillStyle = STATUS_DOT[face.status] ?? '#9fb0bf';
    ctx.beginPath(); ctx.arc(px(edge + r), y + cy * k, r * k, 0, Math.PI * 2); ctx.fill();
  };
  const bodyStart = edge + 40;
  const bodyW = W - bodyStart - edge;
  if (!detail) {
    // The key line, large: one line, or two when it does not fit at the smaller size.
    ctx.fillStyle = TONE[face.tone];
    align(true);
    const size = fit(face.headline, 56, 42, bodyW);
    if (ctx.measureText(face.headline).width <= bodyW * k) {
      dot(158, 13);
      ctx.fillStyle = TONE[face.tone];
      ctx.fillText(face.headline, px(bodyStart), y + 176 * k, bodyW * k);
    } else {
      const lines = wrapText((t) => ctx.measureText(t).width, face.headline, bodyW * k, 2);
      dot(136, 12);
      ctx.fillStyle = TONE[face.tone];
      lines.forEach((line, i) => ctx.fillText(line, px(bodyStart), y + (152 + i * 46) * k, bodyW * k));
    }
    void size;
    face.icons.slice(0, 6).forEach((key, i) => drawIcon(ctx, key, rtl ? x + (W - edge - 30 - i * 40) * k : x + (edge + i * 40) * k, y + 210 * k, 30 * k, '#8fe3d1'));
  } else {
    let cursor = headH;
    const line = (text: string | null, size: number, gap: number, color: string, weight = 500, start = edge): void => {
      if (!text || cursor + gap > 222) return;
      cursor += gap;
      ctx.fillStyle = color; align(true);
      fit(text, size, Math.max(12, size - 8), W - start - edge, weight);
      ctx.fillText(text, px(start), y + cursor * k, (W - start - edge) * k);
    };
    line(face.purpose, 20, 28, '#a9e4d8');
    cursor += 44;
    dot(cursor - 14, 11);
    ctx.fillStyle = TONE[face.tone]; align(true);
    fit(face.headline, 40, 26, W - edge - 30 - edge);
    ctx.fillText(face.headline, px(edge + 30), y + cursor * k, (W - edge - 30 - edge) * k);
    line(face.states, 22, 28, '#dbe4ec');
    line(face.work, 22, 27, face.workConnected ? '#dbe4ec' : '#b4c3d0');
    if (face.departments && cursor + 26 <= 222) {
      const icons = face.icons.filter((key) => key.startsWith('dept:')).slice(0, 4);
      icons.forEach((key, i) => drawIcon(ctx, key, rtl ? x + (W - edge - 20 - i * 26) * k : x + (edge + i * 26) * k, y + (cursor + 8) * k, 20 * k, '#8fe3d1'));
      line(face.departments, 20, 26, '#dbe4ec', 500, edge + icons.length * 26 + (icons.length ? 6 : 0));
    }
    if (face.foot) {
      ctx.fillStyle = face.tone === 'stale' ? '#ffd27a' : '#a9e4d8';
      align(true);
      const room = face.live ? W - 2 * edge - 96 : W - 2 * edge;
      fit(face.foot, 20, 12, room, 600);
      ctx.fillText(face.foot, px(edge), y + 242 * k, room * k);
    }
  }
  // The Live tick (a still dot and the word), only while the presence stream is connected.
  if (face.live) {
    const baseline = detail ? 242 : 238;
    ctx.fillStyle = TEAL;
    align(false);
    ctx.font = font(face.liveLabel, detail ? 20 : 24, 700);
    const w = ctx.measureText(face.liveLabel).width;
    ctx.fillText(face.liveLabel, px(W - edge), y + baseline * k, 90 * k);
    const end = rtl ? x + edge * k + w + 11 * k : x + (W - edge) * k - w - 11 * k;
    ctx.beginPath(); ctx.arc(end, y + (baseline - 8) * k, 6 * k, 0, Math.PI * 2); ctx.fill();
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

/** A changed board on its way from the old face to the new one: the old pixels wait in a cell-sized canvas of a small reused pool. */
type Fade = { index: number; face: BoardFace; from: number; snap: { canvas: HTMLCanvasElement; ctx: Ctx } };

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

  /** Where the decoded logos are found (the campus sign loader's bitmaps); without it no board draws a logo. */
  logoOf: LogoLookup | undefined;
  /** How long a changed board fades from its old face to the new one; 0 (the default, and under reduced motion) changes it at once. */
  fadeMs = 0;
  private readonly make: CanvasMaker;
  private readonly fades: Fade[] = [];
  private readonly pool: Array<{ canvas: HTMLCanvasElement; ctx: Ctx }> = [];
  /** True while a board is fading: the feed draws a step every `BOARD_FADE_STEP_MS` until it is done. */
  get fading(): boolean { return this.fades.length > 0; }

  constructor(count: number, make: CanvasMaker = makeCanvas, opts: Readonly<{ cell?: 512 | 256; logoOf?: LogoLookup }> = {}) {
    this.make = make;
    this.layout = boardAtlasLayout(count, opts.cell ?? 512);
    this.logoOf = opts.logoOf;
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
      // A board that already shows a face fades to the new one (its old pixels are kept first); a first draw is at once.
      if (!(this.keys[i] !== undefined && this.startFade(i, face, now))) drawBoardCell(this.ctx!, x, y, this.layout.cellW / 512, face, this.logoOf);
      this.keys[i] = key;
      this.drawn[i] = face;
      this.drawnAt[i] = now;
      drawn += 1;
    });
    if (drawn > 0) { this.version += 1; this.draws += drawn; }
    return { drawn, nextDueMs: nextDue };
  }

  /** Keeps the old pixels of board `index` and lets `fadeStep` blend the new face in; false when it changes at once instead. */
  private startFade(index: number, face: BoardFace, now: number): boolean {
    if (!(this.fadeMs > 0) || !this.canvas || !this.ctx?.drawImage) return false;
    const { cellW, cellH } = this.layout;
    const { x, y } = this.layout.origin(index);
    // A board that changes again while it fades keeps what it shows now (a blend) as its old face and starts again.
    let fade = this.fades.find((f) => f.index === index);
    if (!fade) {
      if (this.fades.length >= BOARD_FADE_MAX) return false;
      const snap = this.pool.pop() ?? this.make(cellW, cellH);
      if (!snap?.ctx.drawImage) return false;
      fade = { index, face, from: now, snap };
      this.fades.push(fade);
    }
    fade.snap.ctx.clearRect(0, 0, cellW, cellH);
    fade.snap.ctx.drawImage!(this.canvas, x, y, cellW, cellH, 0, 0, cellW, cellH);
    fade.face = face;
    fade.from = now;
    return true;
  }

  /** Draws one step of every fade at time `now`: the new face, then the old one over it at the alpha left. True when it drew. */
  fadeStep(now: number): boolean {
    if (!this.ctx || !this.fades.length) return false;
    const { cellW, cellH } = this.layout;
    for (let i = this.fades.length - 1; i >= 0; i--) {
      const fade = this.fades[i];
      const { x, y } = this.layout.origin(fade.index);
      const left = 1 - Math.min(1, Math.max(0, (now - fade.from) / this.fadeMs));
      drawBoardCell(this.ctx, x, y, cellW / 512, fade.face, this.logoOf);
      if (left > 0) {
        this.ctx.globalAlpha = left;
        this.ctx.drawImage!(fade.snap.canvas, x, y, cellW, cellH);
        this.ctx.globalAlpha = 1;
      } else {
        this.pool.push(fade.snap);
        this.fades.splice(i, 1);
      }
    }
    this.version += 1;
    return true;
  }
}

export type BoardFeed = Readonly<{ atlas: BoardAtlas; touch: () => void; stop: () => void }>;

/**
 * Keeps an atlas current: `touch` reads the faces now and draws what changed; a board held back by the one second rule is drawn when it is due;
 * and a slow tick reads again so a record that stops being confirmed turns "Stale" without any new data. The tick stops while the tab is hidden.
 */
export function startBoardFeed(args: Readonly<{
  count: number; read: () => readonly BoardFace[]; onDraw: (atlas: BoardAtlas) => void; tickMs?: number; make?: CanvasMaker; now?: () => number; cell?: 512 | 256; logoOf?: LogoLookup;
  /** The fade of a changed board in ms, read at every change (so a reduced motion setting applies at once); 0 or none: no fade. */
  fadeMs?: () => number;
}>): BoardFeed {
  const atlas = new BoardAtlas(args.count, args.make, { cell: args.cell, logoOf: args.logoOf });
  const clock = args.now ?? Date.now;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let fadeTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const fadeStep = (): void => {
    fadeTimer = null;
    if (stopped) return;
    if (atlas.fadeStep(clock())) args.onDraw(atlas);
    if (atlas.fading) fadeTimer = setTimeout(fadeStep, BOARD_FADE_STEP_MS);
  };
  const touch = (): void => {
    if (stopped) return;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    atlas.fadeMs = args.fadeMs?.() ?? 0;
    const result = atlas.update(args.read(), clock());
    // A fade that just began draws its first step now (it reports the draw); one under way keeps its own timer.
    if (atlas.fading && fadeTimer === null) fadeStep();
    else if (result.drawn > 0) args.onDraw(atlas);
    if (result.nextDueMs !== null) timer = setTimeout(touch, result.nextDueMs + 5);
  };
  const tick = setInterval(() => { if (typeof document === 'undefined' || document.visibilityState === 'visible') touch(); }, args.tickMs ?? 5000);
  touch();
  return { atlas, touch, stop: () => { stopped = true; clearInterval(tick); if (timer !== null) clearTimeout(timer); if (fadeTimer !== null) clearTimeout(fadeTimer); } };
}
