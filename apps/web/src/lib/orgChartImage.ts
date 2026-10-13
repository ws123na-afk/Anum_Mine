// The org chart as a shareable picture (docs/org-chart.md#export-image): a self-contained SVG
// drawn from the same data as the page, which the browser turns into a PNG. No fonts, images or
// links from elsewhere go into it, so the canvas it is drawn on is never tainted. Pure: the unit
// tests check layout, escaping and right-to-left text without a browser.
import type { MemberStatus } from './orgChart.ts';

/** One member as drawn: every line is already translated; empty lines are left out. */
export interface ImageMember { name: string; role: string; status: MemberStatus | 'person'; statusLabel: string; counts: string; skills: string; tools: string }
export interface ImageDepartment { name: string; stats: string; members: ImageMember[] }
export interface ImageModel {
  title: string;
  subtitle: string;
  owner: { name: string; role: string; initials: string };
  anum: { name: string; role: string; stats: string };
  departments: ImageDepartment[];
  strip: string;
  footer: string;
  rtl: boolean;
}

export interface ImagePalette {
  bg: string; surface: string; surface2: string; line: string; text: string; muted: string;
  accent: string; onAccent: string; sky: string; ok: string; warn: string; stop: string;
}

/** Dark poster colours, used when the page's tokens cannot be read. */
export const DEFAULT_PALETTE: ImagePalette = {
  bg: '#070b1a', surface: '#0e1530', surface2: '#141d3b', line: '#2b3566', text: '#eef1f8', muted: '#a3abc2',
  accent: '#4f46e5', onAccent: '#ffffff', sky: '#7dd3fc', ok: '#86efac', warn: '#fcd38d', stop: '#fda4af',
};

const PAD = 48, COL_W = 340, GAP = 24, DEPT_HEAD = 64, LINE_H = 17, FONT = 'Inter, "Noto Sans Arabic", "Segoe UI", system-ui, sans-serif';

export const escapeXml = (text: string) => text.replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[c] ?? c);

/** Cut to roughly what fits in `width` pixels at `size`. */
export function fit(text: string, width: number, size: number): string {
  const max = Math.max(4, Math.floor(width / (size * 0.56)));
  const chars = Array.from(text.replace(/\s+/g, ' ').trim());
  return chars.length <= max ? chars.join('') : `${chars.slice(0, max - 1).join('').trimEnd()}…`;
}

export function statusColor(status: MemberStatus | 'person', palette: ImagePalette): string {
  switch (status) {
    case 'approval': return palette.warn;
    case 'error': return palette.stop;
    case 'running': return palette.sky;
    case 'idle': return palette.ok;
    default: return palette.muted;
  }
}

/** Columns: one row up to four departments; the trunk then runs in the gap between columns 2 and 3. */
export function columnsFor(count: number): number {
  if (count <= 1) return 1;
  if (count <= 4) return count === 3 ? 3 : count;
  return 4;
}

export function orgChartSvg(model: ImageModel, palette: ImagePalette = DEFAULT_PALETTE): { svg: string; width: number; height: number } {
  const cols = columnsFor(model.departments.length);
  const width = Math.max(960, PAD * 2 + cols * COL_W + (cols - 1) * GAP);
  const center = width / 2;
  const out: string[] = [];
  const text = (x: number, y: number, value: string, size: number, fill: string, opts: { weight?: number; anchor?: 'start' | 'middle' } = {}) => {
    const anchor = opts.anchor ?? 'start';
    const dir = model.rtl ? ' direction="rtl"' : '';
    out.push(`<text x="${x}" y="${y}" font-size="${size}" font-weight="${opts.weight ?? 400}" fill="${fill}" text-anchor="${anchor}"${dir}>${escapeXml(value)}</text>`);
  };
  /** x for start-aligned text inside a box: the left edge, or the right edge in Arabic. */
  const startX = (left: number, w: number, inset: number) => model.rtl ? left + w - inset : left + inset;
  const box = (x: number, y: number, w: number, h: number, fill: string, stroke = palette.line, r = 16) =>
    out.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`);
  const line = (x1: number, y1: number, x2: number, y2: number) =>
    out.push(`<path d="M${x1} ${y1}L${x2} ${y2}" stroke="${palette.line}" stroke-width="2" fill="none"/>`);

  let y = PAD;
  text(center, y + 26, fit(model.title, width - PAD * 2, 30), 30, palette.text, { weight: 700, anchor: 'middle' });
  text(center, y + 56, fit(model.subtitle, width - PAD * 2, 15), 15, palette.muted, { anchor: 'middle' });
  y += 88;

  // Owner
  const ownerW = 340, ownerH = 76;
  box(center - ownerW / 2, y, ownerW, ownerH, palette.surface2);
  const avatarX = model.rtl ? center + ownerW / 2 - 46 : center - ownerW / 2 + 46;
  out.push(`<circle cx="${avatarX}" cy="${y + ownerH / 2}" r="24" fill="${palette.accent}"/>`);
  text(avatarX, y + ownerH / 2 + 6, model.owner.initials, 16, palette.onAccent, { weight: 700, anchor: 'middle' });
  const ownerTextX = model.rtl ? avatarX - 38 : avatarX + 38;
  text(ownerTextX, y + 33, fit(model.owner.name, ownerW - 100, 18), 18, palette.text, { weight: 700 });
  text(ownerTextX, y + 56, fit(model.owner.role, ownerW - 100, 14), 14, palette.muted);
  y += ownerH;
  line(center, y, center, y + 32);
  y += 32;

  // ANUM
  const anumW = 420, anumH = 92;
  box(center - anumW / 2, y, anumW, anumH, palette.surface2, palette.accent);
  text(center, y + 32, model.anum.name, 22, palette.text, { weight: 800, anchor: 'middle' });
  text(center, y + 55, fit(model.anum.role, anumW - 40, 14), 14, palette.muted, { anchor: 'middle' });
  text(center, y + 77, fit(model.anum.stats, anumW - 40, 13), 13, palette.sky, { weight: 600, anchor: 'middle' });
  y += anumH;

  // Departments, row by row, each row hanging from its own bar.
  const rows: ImageDepartment[][] = [];
  for (let i = 0; i < model.departments.length; i += cols) rows.push(model.departments.slice(i, i + cols));
  const rowWidth = cols * COL_W + (cols - 1) * GAP;
  const left0 = (width - rowWidth) / 2;
  let trunkTop = y;
  for (const row of rows) {
    const barY = y + 32;
    line(center, trunkTop, center, barY);
    const xs = row.map((_, i) => {
      const slot = model.rtl ? cols - 1 - i : i;
      // A short last row is centred under the bar.
      const offset = (cols - row.length) * (COL_W + GAP) / 2;
      return left0 + offset + slot * (COL_W + GAP) - (model.rtl ? 2 * offset : 0);
    });
    const mids = xs.map((x) => x + COL_W / 2);
    line(Math.min(center, ...mids), barY, Math.max(center, ...mids), barY);
    let rowBottom = barY;
    row.forEach((dept, i) => {
      const x = xs[i];
      const top = barY + 24;
      line(mids[i], barY, mids[i], top);
      // Every member is drawn (the picture is the whole team); each card is as tall as its lines.
      const heights = dept.members.map(memberHeight);
      const h = DEPT_HEAD + heights.reduce((a, b) => a + b, 0) + 12;
      box(x, top, COL_W, h, palette.surface);
      text(startX(x, COL_W, 18), top + 30, fit(dept.name, COL_W - 36, 17), 17, palette.text, { weight: 700 });
      text(startX(x, COL_W, 18), top + 50, fit(dept.stats, COL_W - 36, 12), 12, palette.muted);
      let my = top + DEPT_HEAD;
      dept.members.forEach((member, j) => {
        out.push(`<rect x="${x + 10}" y="${my}" width="${COL_W - 20}" height="${heights[j] - 8}" rx="12" fill="${palette.surface2}"/>`);
        const dotX = model.rtl ? x + COL_W - 26 : x + 26;
        out.push(`<circle cx="${dotX}" cy="${my + 19}" r="5" fill="${statusColor(member.status, palette)}"/>`);
        const tx = model.rtl ? dotX - 14 : dotX + 14;
        text(tx, my + 24, fit(member.name, COL_W - 64, 14), 14, palette.text, { weight: 600 });
        let ly = my + 24;
        for (const [value, fill] of memberLines(member, palette)) { ly += LINE_H; text(tx, ly, value, 12, fill); }
        my += heights[j];
      });
      rowBottom = Math.max(rowBottom, top + h);
    });
    trunkTop = barY;
    y = rowBottom;
  }
  if (rows.length === 0) y += 8;

  // The approvals strip and the footer.
  y += 32;
  const stripW = Math.min(width - PAD * 2, 640);
  box(center - stripW / 2, y, stripW, 48, palette.surface2, palette.warn, 24);
  text(center, y + 30, fit(model.strip, stripW - 40, 15), 15, palette.text, { weight: 600, anchor: 'middle' });
  y += 48 + 36;
  text(center, y, fit(model.footer, width - PAD * 2, 12), 12, palette.muted, { anchor: 'middle' });
  const height = y + PAD - 12;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family='${FONT}'>`
    + `<rect width="100%" height="100%" fill="${palette.bg}"/>${out.join('')}</svg>`;
  return { svg, width, height };
}

/** Words wrapped to lines that fit `width` at `size`; the last kept line ends with "…" when cut. */
export function wrap(text: string, width: number, size: number, maxLines = 4): string[] {
  const max = Math.max(4, Math.floor(width / (size * 0.56)));
  const lines: string[] = [];
  let line = '';
  for (const word of text.replace(/\s+/g, ' ').trim().split(' ').filter(Boolean)) {
    const next = line ? `${line} ${word}` : word;
    if (Array.from(next).length <= max) { line = next; continue; }
    if (line) lines.push(line);
    line = Array.from(word).length > max ? fit(word, width, size) : word;
  }
  if (line) lines.push(line);
  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  const last = kept[maxLines - 1];
  kept[maxLines - 1] = Array.from(last).length < max ? `${last}…` : fit(`${last} …`, width, size);
  return kept;
}

const TEXT_W = COL_W - 64;

/** Every detail line of a member card: status and work, then role, skills and tools, wrapped. */
function memberLines(member: ImageMember, palette: ImagePalette): [string, string][] {
  const parts: [string, string, number][] = [
    [[member.statusLabel, member.counts].filter(Boolean).join(' · '), statusColor(member.status, palette), 2],
    [member.role, palette.muted, 3], [member.skills, palette.text, 4], [member.tools, palette.sky, 5],
  ];
  return parts.filter(([value]) => Boolean(value)).flatMap(([value, fill, lines]) => wrap(value, TEXT_W, 12, lines).map((l) => [l, fill] as [string, string]));
}

/** A member card's height: the name line plus one line per detail it has. */
export function memberHeight(member: ImageMember): number {
  return 44 + memberLines(member, DEFAULT_PALETTE).length * LINE_H;
}

/** The download's file name: `anum-ai-team-YYYY-MM-DD.png`. */
export function imageFileName(date: Date = new Date()): string {
  return `anum-ai-team-${date.toISOString().slice(0, 10)}.png`;
}
