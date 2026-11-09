// Finance charts (docs/finance.md#reports): plain SVG, no chart library, loaded only with the Reports
// and Safe to spend pages. One y axis each; a legend, a hover and focus tooltip, and the same figures as
// a table for screen readers and print. Colours come from --fin-chart-* (financeShell.css), validated
// for colour vision deficiency in both themes.
import { useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { formatAmount, monthLabel } from './lib/finance';
import type { MonthFlow } from './lib/financeStatements';
import type { ForecastChartPoint } from './lib/financeForecast';
import { formatDateTime, t } from './lib/i18n';
import './financeCharts.css';

const H = 260, PAD = { top: 16, right: 16, bottom: 30, left: 64 };

/** The plot's width in CSS pixels, so text keeps its size on phones (a viewBox would shrink it). */
function useWidth(): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    // Measure before the first paint, so the chart never draws wider than its card.
    setWidth(Math.max(300, Math.round(el.getBoundingClientRect().width)));
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(300, Math.round(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Round axis ticks that cover [min, max], zero included. */
export function ticks(min: number, max: number, count = 5): number[] {
  const lo = Math.min(0, min), hi = Math.max(0, max);
  if (lo === hi) return [0, 1];
  const raw = (hi - lo) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 0.001; v += step) out.push(Math.round(v * 1e6) / 1e6);
  if (out[out.length - 1] < hi) out.push(out[out.length - 1] + step);
  return out;
}

const compact = (value: number, currency: string) => {
  const abs = Math.abs(value);
  const text = abs >= 1_000_000 ? `${Math.round(value / 100_000) / 10}M` : abs >= 1000 ? `${Math.round(value / 100) / 10}K` : String(Math.round(value));
  return `${currency} ${text}`;
};

function Legend({ items }: { items: { key: string; label: string; swatch: 'income' | 'expense' | 'net' | 'band' | 'pace' }[] }) {
  return <ul className="finLegend">{items.map((item) => <li key={item.key}><span className={`finSwatch ${item.swatch}`} aria-hidden="true" />{item.label}</li>)}</ul>;
}

function Tooltip({ at, width, children }: { at: { x: number; y: number } | null; width: number; children: ReactNode }) {
  if (!at) return null;
  return <div className="finTooltip" role="status" style={{ left: `${Math.min(Math.max(at.x, 80), width - 80)}px`, top: `${at.y}px` }}>{children}</div>;
}

/** Income and expenses as bars per month with the net result as a line, on one money axis. */
export function FlowChart({ rows, currency, title, highlight }: { rows: readonly MonthFlow[]; currency: string; title: string; highlight?: string }) {
  const [active, setActive] = useState<number | null>(null);
  const [plotRef, W] = useWidth();
  const tableId = useId();
  const values = rows.flatMap((r) => [Number(r.income), Number(r.expenses), Number(r.net)]);
  const axis = ticks(Math.min(...values, 0), Math.max(...values, 0));
  const lo = axis[0], hi = axis[axis.length - 1];
  const plotW = W - PAD.left - PAD.right, plotH = H - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH;
  const band = plotW / Math.max(1, rows.length);
  const barW = Math.min(36, band * 0.3);
  const cx = (i: number) => PAD.left + band * i + band / 2;
  const netPath = rows.map((r, i) => `${i ? 'L' : 'M'}${cx(i)},${y(Number(r.net))}`).join(' ');
  const bar = (v: number, x: number, cls: string) => {
    const top = Math.min(y(v), y(0)), height = Math.max(1, Math.abs(y(v) - y(0)));
    return <rect className={cls} x={x} y={top} width={barW} height={height} rx={Math.min(4, barW / 2)} />;
  };
  const r = active === null ? null : rows[active];
  return <figure className="finFigure">
    <Legend items={[{ key: 'i', label: t('frep.chart.income'), swatch: 'income' }, { key: 'e', label: t('frep.chart.expenses'), swatch: 'expense' }, { key: 'n', label: t('frep.chart.net'), swatch: 'net' }]} />
    <div className="finPlot" ref={plotRef}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} aria-describedby={tableId} onMouseLeave={() => setActive(null)}>
        {axis.map((v) => <g key={v}><line className={v === 0 ? 'finAxisZero' : 'finGridLine'} x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
          <text className="finTick" x={PAD.left - 8} y={y(v) + 4} textAnchor="end" direction="ltr">{compact(v, currency)}</text></g>)}
        {rows.map((row, i) => <g key={row.month} className={`finBarGroup ${active === i ? 'active' : ''} ${highlight === row.month ? 'current' : ''}`}>
          {highlight === row.month && <rect className="finBandHighlight" x={cx(i) - band / 2 + 4} y={PAD.top} width={band - 8} height={plotH} rx={6} />}
          {bar(Number(row.income), cx(i) - barW - 1, 'finBarIncome')}
          {bar(Number(row.expenses), cx(i) + 1, 'finBarExpense')}
          <text className="finTick finMonthTick" x={cx(i)} y={H - 10} textAnchor="middle">{formatDateTime(`${row.month}-15T12:00:00Z`, { month: W < 560 ? 'short' : 'long', timeZone: 'UTC' })}</text>
          <rect className="finHit" x={cx(i) - band / 2} y={PAD.top} width={band} height={plotH} onMouseEnter={() => setActive(i)} />
        </g>)}
        <path className="finNetLine" d={netPath} />
        {rows.map((row, i) => <circle key={row.month} className="finNetDot" cx={cx(i)} cy={y(Number(row.net))} r={4} />)}
      </svg>
      <Tooltip width={W} at={r && active !== null ? { x: cx(active), y: PAD.top } : null}>{r && <>
        <strong>{monthLabel(r.month)}</strong>
        <span>{t('frep.chart.income')}: <bdi>{formatAmount(r.income, currency)}</bdi></span>
        <span>{t('frep.chart.expenses')}: <bdi>{formatAmount(r.expenses, currency)}</bdi></span>
        <span>{t('frep.chart.net')}: <bdi>{formatAmount(r.net, currency)}</bdi></span>
      </>}</Tooltip>
    </div>
    <details className="finChartTable"><summary>{t('frep.chart.table')}</summary>
      <table id={tableId} className="finGrid"><caption className="visuallyHidden">{title}</caption>
        <thead><tr><th scope="col">{t('frep.col.month')}</th><th scope="col" className="num">{t('frep.chart.income')}</th><th scope="col" className="num">{t('frep.chart.expenses')}</th><th scope="col" className="num">{t('frep.chart.net')}</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.month}><th scope="row">{monthLabel(row.month)}</th><td className="num"><bdi>{formatAmount(row.income, currency)}</bdi></td>
          <td className="num"><bdi>{formatAmount(row.expenses, currency)}</bdi></td><td className="num"><bdi>{formatAmount(row.net, currency)}</bdi></td></tr>)}</tbody>
      </table>
    </details>
  </figure>;
}

/**
 * The forecast (docs/finance.md#forecast): the expected balance day by day (a line), the API's low and high
 * band around it, each salary, bill or payment marked on its day with its name, and the last day's figure
 * labelled. A dashed line marks the emergency reserve target when there is one. One money axis.
 */
export function RunwayChart({ points, currency, title, reserve = null }: { points: readonly ForecastChartPoint[]; currency: string; title: string; reserve?: string | null }) {
  const [active, setActive] = useState<number | null>(null);
  const [plotRef, W] = useWidth();
  const tableId = useId();
  const values = points.flatMap((p) => [Number(p.low), Number(p.high), Number(p.balance)]).concat(reserve ? [Number(reserve)] : []);
  const axis = ticks(Math.min(...values), Math.max(...values));
  const lo = axis[0], hi = axis[axis.length - 1];
  const plotW = W - PAD.left - PAD.right, plotH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (points.length > 1 ? (plotW * i) / (points.length - 1) : plotW / 2);
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH;
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(Number(p.balance))}`).join(' ');
  const bandPath = points.length ? `${points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(Number(p.high))}`).join(' ')} ${[...points].reverse().map((p, k) => {
    const i = points.length - 1 - k; return `L${x(i)},${y(Number(p.low))}`;
  }).join(' ')} Z` : '';
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));
  const markEvery = points.length > 40 ? Math.ceil(points.length / 30) : 1;
  const last = points[points.length - 1];
  const p = active === null ? null : points[active];
  return <figure className="finFigure">
    <Legend items={[{ key: 'b', label: t('fsafe.chart.balance'), swatch: 'income' }, { key: 'u', label: t('ffc.chart.band'), swatch: 'band' }]} />
    <div className="finPlot" ref={plotRef}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} aria-describedby={tableId} onMouseLeave={() => setActive(null)}>
        {axis.map((v) => <g key={v}><line className={v === 0 ? 'finAxisZero' : 'finGridLine'} x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
          <text className="finTick" x={PAD.left - 8} y={y(v) + 4} textAnchor="end" direction="ltr">{compact(v, currency)}</text></g>)}
        <path className="finRunwayBand" d={bandPath} />
        {reserve && Number(reserve) >= lo && Number(reserve) <= hi && <g><line className="finReserveLine" x1={PAD.left} x2={W - PAD.right} y1={y(Number(reserve))} y2={y(Number(reserve))} />
          <text className="finTick" x={W - PAD.right} y={y(Number(reserve)) - 4} textAnchor="end">{t('ffc.chart.reserve')}</text></g>}
        <path className="finRunwayLine" d={line} />
        {points.map((pt, i) => <g key={pt.day}>
          {i % labelEvery === 0 && <text className="finTick" x={x(i)} y={H - 10} textAnchor="middle">{formatDateTime(`${pt.day}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' })}</text>}
          {pt.bills.length > 0 && i % markEvery === 0 && <><circle className="finBillDot" cx={x(i)} cy={y(Number(pt.balance))} r={5} />
            {points.length <= 40 && <text className="finBillLabel" x={x(i)} y={Math.max(12, y(Number(pt.balance)) - 10)} textAnchor="middle">{pt.bills.map((b) => b.name).join(', ').slice(0, 24)}</text>}</>}
          <rect className="finHit" x={x(i) - plotW / Math.max(2, points.length) / 2} y={PAD.top} width={plotW / Math.max(1, points.length)} height={plotH} onMouseEnter={() => setActive(i)} />
        </g>)}
        {last && <><circle className="finNetDot" cx={x(points.length - 1)} cy={y(Number(last.balance))} r={4} />
          <text className="finEndLabel" x={x(points.length - 1) - 6} y={y(Number(last.balance)) + 18} textAnchor="end" direction="ltr">{formatAmount(last.balance, currency)}</text></>}
      </svg>
      <Tooltip width={W} at={p && active !== null ? { x: x(active), y: PAD.top } : null}>{p && <>
        <strong>{formatDateTime(`${p.day}T12:00:00Z`, { day: 'numeric', month: 'long', timeZone: 'UTC' })}</strong>
        <span>{t('fsafe.chart.balance')}: <bdi>{formatAmount(p.balance, currency)}</bdi></span>
        <span>{t('ffc.chart.range')}: <bdi>{formatAmount(p.low, currency)}</bdi> {t('ffc.to')} <bdi>{formatAmount(p.high, currency)}</bdi></span>
        {p.bills.map((b) => <span key={b.name}>{b.name}: <bdi>{formatAmount(b.amount, currency)}</bdi></span>)}
      </>}</Tooltip>
    </div>
    <details className="finChartTable"><summary>{t('frep.chart.table')}</summary>
      <table id={tableId} className="finGrid"><caption className="visuallyHidden">{title}</caption>
        <thead><tr><th scope="col">{t('fsafe.col.day')}</th><th scope="col" className="num">{t('fsafe.chart.balance')}</th><th scope="col" className="num">{t('ffc.chart.low')}</th><th scope="col" className="num">{t('ffc.chart.high')}</th><th scope="col">{t('fsafe.col.bills')}</th></tr></thead>
        <tbody>{points.map((pt) => <tr key={pt.day}><th scope="row"><bdi>{pt.day}</bdi></th><td className="num"><bdi>{formatAmount(pt.balance, currency)}</bdi></td>
          <td className="num"><bdi>{formatAmount(pt.low, currency)}</bdi></td><td className="num"><bdi>{formatAmount(pt.high, currency)}</bdi></td>
          <td>{pt.bills.map((b) => `${b.name} ${formatAmount(b.amount, currency)}`).join(', ') || '-'}</td></tr>)}</tbody>
      </table>
    </details>
  </figure>;
}
