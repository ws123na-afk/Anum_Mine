/**
 * Procedural textures for the headquarters scenes, drawn on 2D canvases at run time (no image
 * files). `signAtlasLayout` is pure so the scene builders can compute sign coordinates without a
 * DOM; the drawing functions need a browser and return null where a 2D canvas is missing.
 *
 * Material textures are multipliers around mid grey: the renderer doubles them, so a pixel of 128
 * leaves the surface colour as authored and lighter or darker pixels add grain.
 */
import { seeded } from './hqGeometry.ts';
import { hasArabicScript } from './arabicScript.ts';
import type { SignText } from './hqRenderTypes.ts';

export const SIGN_CELL_W = 512;
export const SIGN_CELL_H = 128;
const SIGN_COLS = 2;

const nextPow2 = (n: number) => { let p = 1; while (p < n) p *= 2; return p; };

export type SignCell = Readonly<{ u0: number; u1: number; v0: number; v1: number }>;

/** Where sign number `index` sits in an atlas of `count` signs. `v0` is the bottom edge, `v1` the top (texture space). */
export function signAtlasLayout(count: number): Readonly<{ width: number; height: number; cell: (index: number) => SignCell }> {
  const rows = Math.max(1, Math.ceil(Math.max(1, count) / SIGN_COLS));
  const width = SIGN_CELL_W * SIGN_COLS;
  const height = nextPow2(rows * SIGN_CELL_H);
  return {
    width, height,
    cell: (index) => {
      const col = index % SIGN_COLS, row = Math.floor(index / SIGN_COLS);
      return {
        u0: (col * SIGN_CELL_W) / width, u1: ((col + 1) * SIGN_CELL_W) / width,
        v1: 1 - (row * SIGN_CELL_H) / height, v0: 1 - ((row + 1) * SIGN_CELL_H) / height,
      };
    },
  };
}

function canvas(width: number, height: number): { el: HTMLCanvasElement; ctx: CanvasRenderingContext2D } | null {
  if (typeof document === 'undefined') return null;
  const el = document.createElement('canvas');
  el.width = width; el.height = height;
  const ctx = el.getContext('2d');
  return ctx ? { el, ctx } : null;
}

export type MaterialTextureKind = 'concrete' | 'tile' | 'asphalt' | 'grass' | 'wood' | 'metal' | 'roller' | 'court';
const SIZE = 256;

const grey = (v: number, a = 1) => `rgba(${v | 0},${v | 0},${v | 0},${a})`;

/** Draws `fn` at the four wrap offsets so features that cross an edge reappear on the other side. */
function wrapped(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, fn: (x: number, y: number) => void): void {
  for (const ox of [0, -SIZE, SIZE]) {
    for (const oy of [0, -SIZE, SIZE]) {
      const px = x + ox, py = y + oy;
      if (px + w < 0 || py + h < 0 || px > SIZE || py > SIZE) continue;
      fn(px, py);
    }
  }
  void ctx;
}

export function drawMaterialTexture(kind: MaterialTextureKind): HTMLCanvasElement | null {
  const made = canvas(SIZE, SIZE);
  if (!made) return null;
  const { el, ctx } = made;
  const rnd = seeded('anum-tex-' + kind);
  ctx.fillStyle = grey(128);
  ctx.fillRect(0, 0, SIZE, SIZE);
  const speckle = (count: number, spread: number, minS: number, maxS: number, alpha: number) => {
    for (let i = 0; i < count; i++) {
      const x = rnd() * SIZE, y = rnd() * SIZE, s = minS + rnd() * (maxS - minS);
      const v = 128 + (rnd() - 0.5) * 2 * spread;
      wrapped(ctx, x, y, s, s, (px, py) => { ctx.fillStyle = grey(v, alpha); ctx.fillRect(px, py, s, s); });
    }
  };
  switch (kind) {
    case 'concrete': {
      speckle(2600, 38, 1, 4, 0.18);
      speckle(500, 22, 8, 22, 0.05);
      // Panel joints and a few tie-rod holes.
      ctx.fillStyle = grey(88, 0.9);
      ctx.fillRect(0, 0, SIZE, 2); ctx.fillRect(0, SIZE / 2, SIZE, 2); ctx.fillRect(0, 0, 2, SIZE); ctx.fillRect(SIZE / 2, 0, 2, SIZE);
      for (const [x, y] of [[32, 32], [96, 32], [160, 32], [224, 32], [32, 160], [96, 160], [160, 160], [224, 160]]) { ctx.fillStyle = grey(96, 0.8); ctx.fillRect(x, y, 3, 3); }
      break;
    }
    case 'tile': {
      const n = 4, t = SIZE / n;
      for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
        ctx.fillStyle = grey(122 + rnd() * 16);
        ctx.fillRect(c * t, r * t, t, t);
      }
      speckle(1200, 14, 1, 3, 0.15);
      ctx.fillStyle = grey(86, 0.95);
      for (let i = 0; i <= n; i++) { ctx.fillRect(i * t - 1, 0, 3, SIZE); ctx.fillRect(0, i * t - 1, SIZE, 3); }
      break;
    }
    case 'asphalt': {
      speckle(7000, 46, 1, 2.4, 0.5);
      speckle(260, 70, 1.5, 3, 0.7);
      break;
    }
    case 'grass': {
      speckle(900, 18, 6, 16, 0.08);
      for (let i = 0; i < 5200; i++) {
        const x = rnd() * SIZE, y = rnd() * SIZE, len = 3 + rnd() * 6, lean = (rnd() - 0.5) * 3;
        const v = 128 + (rnd() - 0.5) * 80;
        wrapped(ctx, x - 4, y - 8, 10, 14, (px, py) => {
          ctx.strokeStyle = `rgba(${(v - 8) | 0},${(v + 6) | 0},${(v - 14) | 0},0.55)`;
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(px + 4, py + 8); ctx.lineTo(px + 4 + lean, py + 8 - len); ctx.stroke();
        });
      }
      break;
    }
    case 'wood': {
      for (let y = 0; y < SIZE; y += 2) {
        const v = 128 + Math.sin(y * 0.4 + rnd()) * 14 + (rnd() - 0.5) * 20;
        ctx.fillStyle = grey(v, 0.5); ctx.fillRect(0, y, SIZE, 2);
      }
      for (let i = 0; i < 90; i++) {
        const y = rnd() * SIZE, x = rnd() * SIZE, w = 20 + rnd() * 60;
        ctx.fillStyle = grey(96 + rnd() * 20, 0.35); ctx.fillRect(x, y, w, 1);
      }
      ctx.fillStyle = grey(78, 0.9);
      for (let y = 0; y < SIZE; y += 64) ctx.fillRect(0, y, SIZE, 2);
      for (let y = 0; y < SIZE; y += 64) ctx.fillRect(((y / 64) * 91) % SIZE, y, 2, 64);
      break;
    }
    case 'metal': {
      for (let x = 0; x < SIZE; x++) {
        const v = 128 + Math.sin((x / 16) * Math.PI * 2) * 44;
        ctx.fillStyle = grey(v); ctx.fillRect(x, 0, 1, SIZE);
      }
      speckle(500, 30, 1, 3, 0.2);
      break;
    }
    case 'roller': {
      for (let y = 0; y < SIZE; y += 16) {
        const g = ctx.createLinearGradient(0, y, 0, y + 16);
        g.addColorStop(0, grey(170)); g.addColorStop(0.7, grey(120)); g.addColorStop(1, grey(64));
        ctx.fillStyle = g; ctx.fillRect(0, y, SIZE, 16);
      }
      speckle(300, 20, 1, 2, 0.2);
      break;
    }
    case 'court': {
      speckle(3000, 16, 1, 3, 0.2);
      break;
    }
  }
  return el;
}

const LATIN_SIGN_FONT = 'system-ui, "Segoe UI", sans-serif';
/** The self-hosted Arabic face (public/fonts/fonts.css) first, then Latin faces for the Latin words of a mixed name. */
const ARABIC_SIGN_FONT = '"Noto Sans Arabic", Inter, "Segoe UI", Tahoma, system-ui, sans-serif';
const ARABIC_SAMPLE = 'المستوى الطابق';

/**
 * The sign atlas: one dark board per cell with the ANUM mark, the wordmark and the building name. A sign with Arabic text
 * is drawn right to left: the mark moves to the right edge, the text is shaped by the canvas (direction "rtl") and
 * right aligned beside it, with no letter spacing (spacing breaks the joined Arabic letters). Latin signs are unchanged.
 */
export function drawSignAtlas(signs: readonly SignText[]): HTMLCanvasElement | null {
  const layout = signAtlasLayout(signs.length);
  const made = canvas(layout.width, layout.height);
  if (!made) return null;
  const { el, ctx } = made;
  ctx.fillStyle = '#0e1b26';
  ctx.fillRect(0, 0, layout.width, layout.height);
  signs.forEach((sign, i) => {
    const col = i % SIGN_COLS, row = Math.floor(i / SIGN_COLS);
    const x = col * SIGN_CELL_W, y = row * SIGN_CELL_H;
    const rtl = hasArabicScript(sign.title) || hasArabicScript(sign.subtitle);
    const bg = ctx.createLinearGradient(x, y, x, y + SIGN_CELL_H);
    bg.addColorStop(0, '#142c3d'); bg.addColorStop(1, '#0b1722');
    ctx.fillStyle = bg;
    ctx.fillRect(x, y, SIGN_CELL_W, SIGN_CELL_H);
    // Accent rule and the triangular A mark (mirrored to the right edge on a right to left sign).
    ctx.fillStyle = '#45d0b5';
    ctx.fillRect(x, y + SIGN_CELL_H - 6, SIGN_CELL_W, 6);
    const mx = (offset: number) => (rtl ? x + SIGN_CELL_W - offset : x + offset);
    ctx.beginPath();
    ctx.moveTo(mx(24), y + 104); ctx.lineTo(mx(58), y + 22); ctx.lineTo(mx(92), y + 104);
    ctx.lineTo(mx(76), y + 104); ctx.lineTo(mx(58), y + 58); ctx.lineTo(mx(40), y + 104);
    ctx.closePath(); ctx.fill();
    ctx.fillRect(rtl ? mx(70) : mx(46), y + 80, 24, 8);
    const maxW = SIGN_CELL_W - 116 - 20;
    const textX = rtl ? x + SIGN_CELL_W - 116 : x + 116;
    ctx.textBaseline = 'alphabetic';
    ctx.textAlign = rtl ? 'right' : 'left';
    const spaced = ctx as CanvasRenderingContext2D & { letterSpacing?: string };
    ctx.direction = rtl ? 'rtl' : 'ltr';
    const face = (text: string, size: number) => `700 ${size}px ${hasArabicScript(text) ? ARABIC_SIGN_FONT : LATIN_SIGN_FONT}`;
    ctx.fillStyle = '#8fe3d1';
    ctx.font = face(sign.title, 30);
    // Letter spacing is for the Latin wordmark only.
    spaced.letterSpacing = rtl ? '0px' : '6px';
    ctx.fillText(sign.title, textX, y + 52, maxW);
    spaced.letterSpacing = '0px';
    if (sign.subtitle) {
      ctx.fillStyle = '#f2f6fa';
      let size = 38;
      ctx.font = face(sign.subtitle, size);
      while (size > 18 && ctx.measureText(sign.subtitle).width > maxW) { size -= 2; ctx.font = face(sign.subtitle, size); }
      ctx.fillText(sign.subtitle, textX, y + 98, maxW);
    }
  });
  return el;
}
