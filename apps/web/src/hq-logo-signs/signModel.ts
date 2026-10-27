/**
 * The look of a building sign (docs/ANUM_MINE_HQ_LOGO_SIGNS.md). Pure: no DOM, no network.
 *
 * An owner uploads a logo and picks a brand colour and a style. The sign never invents a mark: with no logo it is the
 * building's real name in letters. The brand colour is the owner's own; the only changes made to it are readability
 * ones (the lettering falls back to a readable colour when the owner's colour is too faint on the board, and the
 * colour stays on the sign's accent). The logo is shown as given, on a white plate, so a dark logo never disappears.
 */
export const SIGN_STYLES = ['plain', 'plaque', 'illuminated', 'banner'] as const;
export type SignStyle = (typeof SIGN_STYLES)[number];

/** A sign without a chosen style is the plain lettered sign. */
export const DEFAULT_STYLE: SignStyle = 'plain';
/** Used for a banner board when the owner has not chosen a colour (a neutral slate, not a brand). */
export const NEUTRAL_BOARD = '#34454f';
const PAPER = '#f6f3ea';
const INK = '#1b2420';
const WHITE = '#ffffff';
const DARK_PLAQUE = '#1f262b';
const DARK_NIGHT = '#0d1216';

export const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** `#RRGGBB` in lower case, or null when the text is not a six digit colour. */
export function normalizeColor(value: string | null | undefined): string | null {
  const text = (value ?? '').trim();
  return HEX_COLOR.test(text) ? text.toLowerCase() : null;
}

export function rgbOf(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** WCAG relative luminance. */
export function luminance(hex: string): number {
  const channel = (v: number): number => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  const [r, g, b] = rgbOf(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio between two colours, 1 to 21. */
export function contrast(a: string, b: string): number {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

export type SignPart = 'bar' | 'frame' | 'glow' | 'board';

export type SignLook = Readonly<{
  style: SignStyle;
  face: string;
  /** The lettering colour actually used. */
  text: string;
  /** The owner's colour, or null when none was chosen. */
  accent: string | null;
  /** Where the owner's colour appears on this style. */
  part: SignPart;
  /** Contrast of the owner's colour against what it is used on (the board for lettering, the board for a frame), or null with no colour. */
  ratio: number | null;
  /** The ratio the owner's colour needs on this style (4.5 for lettering, 3 for a frame). */
  needs: number;
  /** True when the owner's colour is readable as chosen (always true with no colour). */
  readable: boolean;
  /** True when the lettering had to fall back to ink or paper because the colour was too faint. */
  letteringFallback: boolean;
}>;

export function signLook(style: SignStyle | null | undefined, color: string | null | undefined): SignLook {
  const s: SignStyle = style && (SIGN_STYLES as readonly string[]).includes(style) ? style : DEFAULT_STYLE;
  const brand = normalizeColor(color);
  if (s === 'banner') {
    const face = brand ?? NEUTRAL_BOARD;
    const text = contrast(face, WHITE) >= contrast(face, INK) ? WHITE : INK;
    return { style: s, face, text, accent: brand, part: 'board', ratio: brand ? contrast(face, text) : null, needs: 4.5, readable: true, letteringFallback: false };
  }
  if (s === 'plaque') {
    const ratio = brand ? contrast(brand, DARK_PLAQUE) : null;
    return { style: s, face: DARK_PLAQUE, text: PAPER, accent: brand, part: 'frame', ratio, needs: 3, readable: ratio === null || ratio >= 3, letteringFallback: false };
  }
  if (s === 'illuminated') {
    const ratio = brand ? contrast(brand, DARK_NIGHT) : null;
    const readable = ratio === null || ratio >= 4.5;
    return { style: s, face: DARK_NIGHT, text: brand && readable ? brand : PAPER, accent: brand, part: 'glow', ratio, needs: 4.5, readable, letteringFallback: brand !== null && !readable };
  }
  const ratio = brand ? contrast(brand, PAPER) : null;
  const readable = ratio === null || ratio >= 4.5;
  return { style: s, face: PAPER, text: brand && readable ? brand : INK, accent: brand, part: 'bar', ratio, needs: 4.5, readable, letteringFallback: brand !== null && !readable };
}

export type Box = Readonly<{ x: number; y: number; w: number; h: number }>;
export type SignLayout = Readonly<{ plate: Box | null; logo: Box | null; text: Box }>;

/** Design size of a sign cell (the facade sign atlas cell). */
export const CELL_W = 512;
export const CELL_H = 128;

/**
 * Where the logo plate and the lettering go in a cell of `CELL_W` x `CELL_H` design units. The plate sits at the start edge (the right in
 * Arabic), the logo is fitted inside it keeping its proportions, and the name takes the rest of the width.
 */
export function layoutSign(logo: Readonly<{ w: number; h: number }> | null, rtl: boolean): SignLayout {
  const pad = 14, gap = 18, inner = 8;
  const plateH = CELL_H - 2 * pad - 8;
  if (!logo || !(logo.w > 0) || !(logo.h > 0)) return { plate: null, logo: null, text: { x: pad + 8, y: pad, w: CELL_W - 2 * (pad + 8), h: plateH } };
  const fit = Math.min((plateH - 2 * inner) / logo.h, (CELL_W * 0.4 - 2 * inner) / logo.w);
  const lw = Math.max(1, Math.round(logo.w * fit)), lh = Math.max(1, Math.round(logo.h * fit));
  const plateW = Math.max(plateH, lw + 2 * inner);
  const px = rtl ? CELL_W - pad - plateW : pad;
  const plate: Box = { x: px, y: pad, w: plateW, h: plateH };
  const box: Box = { x: px + Math.round((plateW - lw) / 2), y: pad + Math.round((plateH - lh) / 2), w: lw, h: lh };
  const textW = CELL_W - 2 * pad - plateW - gap;
  const text: Box = { x: rtl ? pad : pad + plateW + gap, y: pad, w: textW, h: plateH };
  return { plate, logo: box, text };
}

export const hasArabic = (text: string): boolean => /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/.test(text);
