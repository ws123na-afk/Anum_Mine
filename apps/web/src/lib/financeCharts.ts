// Chart geometry for the Finance Holdings and paper trading pages (docs/finance.md#holdings). Pure, so the
// unit tests cover it: it only scales the API's figures into SVG paths. It never fills a gap, smooths a line
// or forecasts.

export interface Box { width: number; height: number; left: number; right: number; top: number; bottom: number }

export interface Plotted {
  line: string;
  area: string;
  points: { x: number; y: number; value: number; index: number }[];
  ticks: { value: number; y: number }[];
  min: number;
  max: number;
  baseY: number;
}

const round = (n: number) => Math.round(n * 10) / 10;

/** Round tick steps (1, 2, 2.5, 5 times a power of ten) covering min to max with about `count` ticks. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) { const pad = Math.abs(min) * 0.05 || 1; min -= pad; max += pad; }
  const raw = (max - min) / Math.max(1, count - 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const start = Math.floor(min / step) * step, end = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/** Scale values (null = gap) into the box. The y range is the ticks' range, so lines sit inside the grid. */
export function plot(values: readonly (number | null)[], box: Box, alongside: readonly (number | null)[] = []): Plotted {
  const known = [...values, ...alongside].filter((v): v is number => v !== null && Number.isFinite(v));
  const ticksRaw = known.length ? niceTicks(Math.min(...known), Math.max(...known)) : [0, 1];
  const min = ticksRaw[0], max = ticksRaw[ticksRaw.length - 1];
  const innerW = box.width - box.left - box.right, innerH = box.height - box.top - box.bottom;
  const x = (i: number) => (values.length <= 1 ? box.left + innerW / 2 : box.left + (i * innerW) / (values.length - 1));
  const y = (v: number) => box.top + (max === min ? innerH / 2 : ((max - v) / (max - min)) * innerH);
  const baseY = round(box.top + innerH);
  const runs: { x: number; y: number; value: number; index: number }[][] = [];
  let run: { x: number; y: number; value: number; index: number }[] = [];
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) { if (run.length) runs.push(run); run = []; return; }
    run.push({ x: round(x(i)), y: round(y(v)), value: v, index: i });
  });
  if (run.length) runs.push(run);
  const line = runs.map((r) => r.length === 1 ? `M${r[0].x - 2},${r[0].y}L${r[0].x + 2},${r[0].y}` : r.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join('')).join('');
  const area = runs.filter((r) => r.length > 1).map((r) => `M${r[0].x},${baseY}${r.map((p) => `L${p.x},${p.y}`).join('')}L${r[r.length - 1].x},${baseY}Z`).join('');
  return { line, area, points: runs.flat(), ticks: ticksRaw.map((v) => ({ value: v, y: round(y(v)) })), min, max, baseY };
}

/** The point nearest to an x position, for the hover crosshair. */
export function nearest(points: Plotted['points'], x: number): Plotted['points'][number] | null {
  let best: Plotted['points'][number] | null = null;
  for (const p of points) if (!best || Math.abs(p.x - x) < Math.abs(best.x - x)) best = p;
  return best;
}

/** A small trend line in a width by height box; flat when every value is the same. */
export function sparkline(values: readonly number[], width: number, height: number): string {
  if (values.length < 2) return '';
  const min = Math.min(...values), max = Math.max(...values);
  const pad = 2, h = height - pad * 2, w = width - pad * 2;
  return values.map((v, i) => `${i ? 'L' : 'M'}${round(pad + (i * w) / (values.length - 1))},${round(pad + (max === min ? h / 2 : ((max - v) / (max - min)) * h))}`).join('');
}

/** Donut segments as SVG arc paths, clockwise from 12 o'clock, with a small gap between segments. */
export function donut(shares: readonly number[], cx: number, cy: number, outer: number, inner: number, gapDegrees = 1.2): string[] {
  const total = shares.reduce((a, b) => a + Math.max(0, b), 0);
  if (!total) return [];
  const point = (r: number, deg: number) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return `${round(cx + r * Math.cos(rad))},${round(cy + r * Math.sin(rad))}`;
  };
  let start = 0;
  return shares.map((s) => {
    const sweep = (Math.max(0, s) / total) * 360;
    const gap = shares.filter((x) => x > 0).length > 1 ? gapDegrees : 0;
    const a = start + gap / 2, b = start + Math.max(sweep - gap / 2, gap / 2 + 0.01);
    start += sweep;
    if (sweep >= 359.99) {
      return `M${point(outer, 0)}A${outer},${outer} 0 1 1 ${point(outer, 180)}A${outer},${outer} 0 1 1 ${point(outer, 359.99)}L${point(inner, 359.99)}A${inner},${inner} 0 1 0 ${point(inner, 180)}A${inner},${inner} 0 1 0 ${point(inner, 0)}Z`;
    }
    const large = b - a > 180 ? 1 : 0;
    return `M${point(outer, a)}A${outer},${outer} 0 ${large} 1 ${point(outer, b)}L${point(inner, b)}A${inner},${inner} 0 ${large} 0 ${point(inner, a)}Z`;
  });
}

/** Indices of up to `count` evenly spread labels for an x axis of `length` points (first and last kept). */
export function labelIndices(length: number, count: number): number[] {
  if (length <= 0) return [];
  if (length <= count) return Array.from({ length }, (_, i) => i);
  const out = new Set<number>();
  for (let k = 0; k < count; k++) out.add(Math.round((k * (length - 1)) / (count - 1)));
  return [...out];
}
