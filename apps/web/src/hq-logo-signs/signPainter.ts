/**
 * Draws one building sign into a 512 x 128 cell (the facade sign atlas cell) or into the builder's preview canvas, so the preview
 * and the 3D sign are the same drawing (docs/ANUM_MINE_HQ_LOGO_SIGNS.md). The words are the building's real name, shaped right to
 * left in Arabic. The logo, when there is one, is drawn as given on a white plate; nothing is recoloured except the sign's own accent.
 */
import { CELL_H, CELL_W, STANDARD_ACCENT, hasArabic, layoutSign, signLook, type SignLook, type SignStyle } from './signModel.ts';

export type SignInput = Readonly<{
  /** The building's name in the language of the page (Arabic name in Arabic mode when it has one). */
  name: string;
  rtl: boolean;
  style: SignStyle | null | undefined;
  color: string | null | undefined;
  /** A decoded copy of the owner's logo, or null for the lettered sign. */
  logo: CanvasImageSource | null;
  logoWidth?: number;
  logoHeight?: number;
  /** The platform wordmark of the standard sign ("ANUM MINE"), drawn small over the name when the sign has no logo; the other styles never draw it. */
  wordmark?: string;
}>;

const LATIN = 'system-ui, "Segoe UI", sans-serif';
const ARABIC = '"Noto Sans Arabic", Inter, "Segoe UI", Tahoma, system-ui, sans-serif';

type Ctx = Pick<CanvasRenderingContext2D, 'fillRect' | 'fillText' | 'measureText' | 'save' | 'restore' | 'beginPath' | 'moveTo' | 'lineTo' | 'closePath' | 'rect' | 'roundRect' | 'fill' | 'stroke' | 'drawImage' | 'createLinearGradient' | 'strokeRect'> & {
  fillStyle: unknown; strokeStyle: unknown; lineWidth: number; font: string; letterSpacing?: string; textAlign: CanvasTextAlign; textBaseline: CanvasTextBaseline; direction: CanvasDirection;
  shadowColor: string; shadowBlur: number; globalAlpha: number;
};

/** Paints the sign into the cell whose top left corner is (x, y); `k` scales the 512 x 128 design to the cell (1 for 512 x 128). */
export function paintSign(ctx: Ctx, x: number, y: number, k: number, input: SignInput): SignLook {
  const look = signLook(input.style, input.color);
  const logo = input.logo && input.logoWidth && input.logoHeight ? { w: input.logoWidth, h: input.logoHeight } : null;
  const layout = layoutSign(logo, input.rtl);
  const W = CELL_W * k, H = CELL_H * k;
  // The board.
  ctx.fillStyle = look.face;
  ctx.fillRect(x, y, W, H);
  if (look.style === 'standard') {
    const dark = ctx.createLinearGradient(x, y, x, y + H);
    dark.addColorStop(0, '#142c3d'); dark.addColorStop(1, '#0b1722');
    ctx.fillStyle = dark; ctx.fillRect(x, y, W, H);
  }
  if (look.style === 'banner') {
    const shade = ctx.createLinearGradient(x, y, x, y + H);
    shade.addColorStop(0, 'rgba(255,255,255,0.10)'); shade.addColorStop(1, 'rgba(0,0,0,0.12)');
    ctx.fillStyle = shade; ctx.fillRect(x, y, W, H);
  }
  const accent = look.accent;
  if (look.style === 'standard') {
    ctx.fillStyle = accent ?? STANDARD_ACCENT;
    ctx.fillRect(x, y + H - 6 * k, W, 6 * k);
  } else if (look.style === 'plain') {
    ctx.fillStyle = accent ?? look.text;
    ctx.fillRect(x, y + H - 8 * k, W, 8 * k);
  } else if (look.style === 'plaque') {
    ctx.strokeStyle = accent ?? '#8a949b';
    ctx.lineWidth = 7 * k;
    ctx.strokeRect(x + 3.5 * k, y + 3.5 * k, W - 7 * k, H - 7 * k);
  } else if (look.style === 'illuminated') {
    ctx.fillStyle = accent ?? '#8a949b';
    ctx.fillRect(x, y + H - 5 * k, W, 5 * k);
  } else {
    ctx.strokeStyle = 'rgba(255,255,255,0.45)';
    ctx.lineWidth = 2 * k;
    ctx.strokeRect(x + 8 * k, y + 8 * k, W - 16 * k, H - 16 * k);
  }
  // The logo on its white plate, as given.
  if (layout.plate && layout.logo && input.logo) {
    const p = layout.plate, l = layout.logo;
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    // roundRect is missing in older browsers: a square plate is the same sign.
    if (typeof ctx.roundRect === 'function') ctx.roundRect(x + p.x * k, y + p.y * k, p.w * k, p.h * k, 10 * k);
    else ctx.rect(x + p.x * k, y + p.y * k, p.w * k, p.h * k);
    ctx.fill();
    ctx.drawImage(input.logo, x + l.x * k, y + l.y * k, l.w * k, l.h * k);
  }
  // The name.
  const rtl = input.rtl;
  ctx.textAlign = rtl ? 'right' : 'left';
  ctx.textBaseline = 'alphabetic';
  ctx.direction = rtl ? 'rtl' : 'ltr';
  const family = (text: string) => (hasArabic(text) ? ARABIC : LATIN);
  if (look.style === 'standard' && !layout.plate) {
    // The standard sign: the campus mark at the start edge, the wordmark small, the real name large (the same drawing as the facade sign atlas).
    const mx = (offset: number) => (rtl ? x + W - offset * k : x + offset * k);
    ctx.fillStyle = accent ?? STANDARD_ACCENT;
    ctx.beginPath();
    ctx.moveTo(mx(24), y + 104 * k); ctx.lineTo(mx(58), y + 22 * k); ctx.lineTo(mx(92), y + 104 * k);
    ctx.lineTo(mx(76), y + 104 * k); ctx.lineTo(mx(58), y + 58 * k); ctx.lineTo(mx(40), y + 104 * k);
    ctx.closePath(); ctx.fill();
    ctx.fillRect(rtl ? mx(70) : mx(46), y + 80 * k, 24 * k, 8 * k);
    const maxW = (CELL_W - 116 - 20) * k;
    const textX = rtl ? x + (CELL_W - 116) * k : x + 116 * k;
    if (input.wordmark) {
      ctx.fillStyle = '#8fe3d1';
      ctx.font = `700 ${Math.round(30 * k)}px ${family(input.wordmark)}`;
      ctx.letterSpacing = rtl ? '0px' : `${6 * k}px`;
      ctx.fillText(input.wordmark, textX, y + 52 * k, maxW);
      ctx.letterSpacing = '0px';
    }
    let size = 38;
    ctx.font = `700 ${Math.round(size * k)}px ${family(input.name)}`;
    while (size > 18 && ctx.measureText(input.name).width > maxW) { size -= 2; ctx.font = `700 ${Math.round(size * k)}px ${family(input.name)}`; }
    ctx.fillStyle = look.text;
    ctx.fillText(input.name, textX, y + 98 * k, maxW);
    return look;
  }
  const t = layout.text;
  ctx.textBaseline = 'middle';
  let size = layout.plate ? 46 : 54;
  ctx.font = `700 ${Math.round(size * k)}px ${family(input.name)}`;
  while (size > 20 && ctx.measureText(input.name).width > t.w * k) { size -= 2; ctx.font = `700 ${Math.round(size * k)}px ${family(input.name)}`; }
  ctx.fillStyle = look.text;
  if (look.style === 'illuminated' && accent) { ctx.shadowColor = accent; ctx.shadowBlur = 14 * k; }
  ctx.fillText(input.name, rtl ? x + (t.x + t.w) * k : x + t.x * k, y + (t.y + t.h / 2 - 4) * k, t.w * k);
  ctx.shadowBlur = 0; ctx.shadowColor = 'transparent';
  return look;
}
