// SIMULATED P&L and drawdown over time (docs/finance-trading-intel.md#pl-and-drawdown-over-time), pure so
// the unit tests cover it. The API rebuilds every day from stored orders and recorded prices; this file
// only scales those figures into SVG paths and words. It never fills a gap, smooths a line or forecasts.
import type { FinanceEquityBenchmark, FinanceEquityPoint, FinanceEquitySeries } from '@anum/contracts';
import { formatDecimal } from './financeTrading.ts';
import { getLocale, intlLocale, t } from './i18n.ts';

/** The ranges offered, in days. The API allows 1 to 730. */
export const RANGES = [30, 90, 180, 365] as const;
export type Range = (typeof RANGES)[number];
export const DEFAULT_RANGE: Range = 90;
export const TABLE_PAGE = 31;

export interface Frame { width: number; height: number; left: number; right: number; top: number; bottom: number }

export function frame(width: number, height: number): Frame {
  return { width, height, left: 8, right: 8, top: 18, bottom: 34 };
}

const number = (value: string | null): number | null => {
  if (value === null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

/** What a chart plots for one point: P&L, or the drawdown (a fraction on paper, an amount on shadow). */
export type Measure = 'pnl' | 'drawdown';

export function pick(point: FinanceEquityPoint, measure: Measure, paper: boolean): number | null {
  if (!point.complete) return null;
  if (measure === 'pnl') return number(point.pnl);
  return paper ? point.drawdown_pct : number(point.drawdown);
}

export interface Scaled {
  /** One `M`...`L` run per stretch of days without a gap. */
  line: string;
  /** Closed areas between the zero line and the values, per stretch (used for drawdown). */
  area: string;
  min: number;
  max: number;
  zeroY: number;
  /** Points with a value, as drawn (for the end marker). */
  last: { x: number; y: number; value: number } | null;
  plotted: number;
}

/** Scale values into the frame. Zero is always inside the range so gains and losses read against it.
 * `alongside` widens the range only, so a second line drawn with the same range lines up with this one. */
export function scale(values: (number | null)[], f: Frame, alongside: (number | null)[] = []): Scaled {
  const known = values.filter((v): v is number => v !== null);
  const both = [...known, ...alongside.filter((v): v is number => v !== null)];
  let min = Math.min(0, ...both), max = Math.max(0, ...both);
  if (min === max) { min -= 1; max += 1; }
  const innerW = f.width - f.left - f.right, innerH = f.height - f.top - f.bottom;
  const x = (i: number) => (values.length <= 1 ? f.left + innerW / 2 : f.left + (i * innerW) / (values.length - 1));
  const y = (v: number) => f.top + ((max - v) / (max - min)) * innerH;
  const zeroY = y(0);
  const runs: { x: number; y: number; value: number }[][] = [];
  let current: { x: number; y: number; value: number }[] = [];
  values.forEach((v, i) => {
    if (v === null) { if (current.length) runs.push(current); current = []; return; }
    current.push({ x: round(x(i)), y: round(y(v)), value: v });
  });
  if (current.length) runs.push(current);
  const line = runs.map((run) => run.length === 1
    ? `M${run[0].x - 2},${run[0].y}L${run[0].x + 2},${run[0].y}`
    : run.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join('')).join('');
  const area = runs.map((run) => `M${run[0].x},${round(zeroY)}${run.map((p) => `L${p.x},${p.y}`).join('')}L${run[run.length - 1].x},${round(zeroY)}Z`).join('');
  const lastRun = runs[runs.length - 1];
  return { line, area, min, max, zeroY: round(zeroY), last: lastRun ? lastRun[lastRun.length - 1] : null, plotted: known.length };
}

const round = (n: number) => Math.round(n * 10) / 10;

/** An amount the API sent, with its currency, for reading ("1,049.5 USD"). Missing is "-". */
export function amount(value: string | null, currency: string): string {
  if (value === null) return '-';
  const text = formatDecimal(value, 2);
  return text === '-' ? text : `${text} ${currency}`;
}

/** A fraction as a signed percentage ("-18.20%"). Missing is "-". */
export function percent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '-';
  return new Intl.NumberFormat(intlLocale(getLocale()), { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2, numberingSystem: 'latn', signDisplay: 'exceptZero' }).format(value);
}

/** A number on an axis: amounts to 2 decimals, fractions as percentages. */
export function tick(value: number, asPercent: boolean): string {
  if (asPercent) return percent(value);
  return new Intl.NumberFormat(intlLocale(getLocale()), { maximumFractionDigits: 2, numberingSystem: 'latn' }).format(value);
}

/** The newest day with a value, if any. */
export function latest(series: FinanceEquitySeries): FinanceEquityPoint | null {
  for (let i = series.points.length - 1; i >= 0; i -= 1) if (series.points[i].complete) return series.points[i];
  return null;
}

/** The deepest drawdown in words: amount, percentage on paper, and from which peak to which low. */
export function drawdownLine(series: FinanceEquitySeries, paper: boolean): string {
  const deep = series.max_drawdown !== null && Number(series.max_drawdown) < 0;
  if (!deep) return t('finance.equity.noDrawdown');
  const size = paper && series.max_drawdown_pct !== null ? `${amount(series.max_drawdown, series.currency)} (${percent(series.max_drawdown_pct)})` : amount(series.max_drawdown, series.currency);
  return t('finance.equity.drawdownLine', { size, peak: series.peak_day ?? '-', trough: series.trough_day ?? '-' });
}

/** The chart's text alternative: range, newest P&L, lowest and highest P&L, deepest drawdown, gaps. */
export function chartSummary(series: FinanceEquitySeries, paper: boolean): string {
  const values = series.points.map((p) => (p.complete ? number(p.pnl) : null)).filter((v): v is number => v !== null);
  const first = series.points[0]?.day ?? '-', last = series.points[series.points.length - 1]?.day ?? '-';
  const now = latest(series);
  if (!values.length || !now) return t('finance.equity.ariaEmpty', { currency: series.currency, start: first, end: last });
  return t('finance.equity.aria', {
    currency: series.currency, start: first, end: last, latest: amount(now.pnl, series.currency),
    low: amount(Math.min(...values).toFixed(2), series.currency), high: amount(Math.max(...values).toFixed(2), series.currency),
    drawdown: drawdownLine(series, paper), gaps: series.gaps,
  }) + (latestBenchmark(series.benchmark) !== null ? ` ${t('finance.equity.benchmark.aria', { latest: amount(latestBenchmark(series.benchmark), series.currency) })}` : '');
}

/** One page of the table fallback, newest day first. */
export function tableRows(series: FinanceEquitySeries, offset: number, size = TABLE_PAGE): FinanceEquityPoint[] {
  return [...series.points].reverse().slice(offset, offset + size);
}

/** The buy and hold P&L for each point of the series, by day (null where there is none). */
export function benchmarkValues(series: FinanceEquitySeries): (number | null)[] {
  const bench = series.benchmark;
  if (!bench || bench.reason) return series.points.map(() => null);
  const byDay = new Map(bench.points.map((b) => [b.day, b.pnl]));
  return series.points.map((p) => number(byDay.get(p.day) ?? null));
}

/** The newest buy and hold P&L, as the API sent it. */
export function latestBenchmark(bench: FinanceEquityBenchmark | null | undefined): string | null {
  if (!bench || bench.reason) return null;
  for (let i = bench.points.length - 1; i >= 0; i -= 1) if (bench.points[i].pnl !== null) return bench.points[i].pnl;
  return null;
}

/** What the benchmark is, or why there is none, in words. */
export function benchmarkLine(series: FinanceEquitySeries): string | null {
  const bench = series.benchmark;
  if (!bench) return null;
  if (bench.reason) return t(`finance.equity.benchmark.${bench.reason}`);
  return t('finance.equity.benchmark.what', { symbols: bench.symbols.join(', '), start: bench.start ?? '-' });
}

/** The P&L chart's two lines, scaled to one range: the portfolio and buy and hold. */
export function scalePnl(series: FinanceEquitySeries, f: Frame): { portfolio: Scaled; benchmark: Scaled | null } {
  const own = series.points.map((p) => pick(p, 'pnl', true));
  const bench = benchmarkValues(series);
  const hasBench = bench.some((v) => v !== null);
  return { portfolio: scale(own, f, bench), benchmark: hasBench ? scale(bench, f, own) : null };
}
