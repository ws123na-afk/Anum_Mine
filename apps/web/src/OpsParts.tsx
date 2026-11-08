// Shared parts of the operations pages (docs/home.md#operations-overview, docs/vehicles.md#web-page,
// docs/business-books.md#overview-dashboard, docs/finance.md#finance-overview): a two-line trend chart
// (income against expenses), a bar and line usage chart (litres and kilometres), horizontal share bars
// and a progress ring. Plain SVG and CSS, no chart library. Presentation only: pages pass real figures
// from the API and every chart has the same figures as a table for screen readers and print.
import { useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { ticks } from './FinanceCharts';
import { formatAmount } from './lib/finance';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import './commerce.css';
import './financePages.css';
import './ops.css';

const H = 240, PAD = { top: 24, right: 20, bottom: 30, left: 64 };

function useWidth(min = 280): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setWidth(Math.max(min, Math.round(el.getBoundingClientRect().width)));
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(min, Math.round(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, [min]);
  return [ref, width];
}

const compact = (value: number, unit: string) => {
  const abs = Math.abs(value);
  const text = abs >= 1_000_000 ? `${Math.round(value / 100_000) / 10}M` : abs >= 1000 ? `${Math.round(value / 100) / 10}K` : String(Math.round(value));
  return unit ? `${unit} ${text}` : text;
};

/** "Jun", "Jul" for a "YYYY-MM" month (the year once it changes on wide charts). */
export const monthTick = (month: string, long = false) => formatDateTime(`${month}-15T12:00:00Z`, { month: long ? 'long' : 'short', timeZone: 'UTC' });
const monthYear = (month: string) => formatDateTime(`${month}-15T12:00:00Z`, { month: 'long', year: 'numeric', timeZone: 'UTC' });

export interface TrendRow { month: string; income: string; expenses: string }

/**
 * Income and expenses per month as two lines with a soft area under each, the last month's figures
 * labelled at the line ends. One money axis; hover or focus a month for its figures.
 */
export function TrendLines({ rows, currency, title, incomeLabel, expenseLabel }: {
  rows: readonly TrendRow[]; currency: string; title: string; incomeLabel: string; expenseLabel: string;
}) {
  const [active, setActive] = useState<number | null>(null);
  const [plotRef, W] = useWidth();
  const tableId = useId();
  const values = rows.flatMap((r) => [Number(r.income), Number(r.expenses)]);
  const axis = ticks(Math.min(0, ...values), Math.max(0, ...values));
  const lo = axis[0], hi = axis[axis.length - 1];
  const plotW = W - PAD.left - PAD.right, plotH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (rows.length > 1 ? (plotW * i) / (rows.length - 1) : plotW / 2);
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const line = (key: 'income' | 'expenses') => rows.map((r, i) => `${i ? 'L' : 'M'}${x(i)},${y(Number(r[key]))}`).join(' ');
  const area = (key: 'income' | 'expenses') => rows.length ? `${line(key)} L${x(rows.length - 1)},${y(lo)} L${x(0)},${y(lo)} Z` : '';
  const last = rows[rows.length - 1];
  const r = active === null ? null : rows[active];
  const endLabel = (value: string, cls: string, dy: number) => {
    const text = formatAmount(value, currency);
    const width = Math.min(150, text.length * 7 + 14);
    const lx = Math.max(PAD.left, Math.min(W - width - 2, x(rows.length - 1) - width / 2));
    const ly = Math.max(2, Math.min(H - PAD.bottom - 20, y(Number(value)) + dy));
    return <g className={`opsEndTag ${cls}`} aria-hidden="true"><rect x={lx} y={ly} width={width} height={20} rx={5} /><text x={lx + width / 2} y={ly + 14} textAnchor="middle" direction="ltr">{text}</text></g>;
  };
  const incomeHigher = last ? Number(last.income) >= Number(last.expenses) : true;
  return <figure className="opsFigure">
    <ul className="opsLegend">
      <li><span className="opsSwatch income" aria-hidden="true" />{incomeLabel}</li>
      <li><span className="opsSwatch expense" aria-hidden="true" />{expenseLabel}</li>
    </ul>
    <div className="opsPlot" ref={plotRef}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} aria-describedby={tableId} onMouseLeave={() => setActive(null)} direction="ltr">
        {axis.map((v) => <g key={v}><line className={v === 0 ? 'opsAxisZero' : 'opsGridLine'} x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
          <text className="opsTick" x={PAD.left - 8} y={y(v) + 4} textAnchor="end">{compact(v, currency)}</text></g>)}
        <path className="opsArea income" d={area('income')} />
        <path className="opsArea expense" d={area('expenses')} />
        <path className="opsLine income" d={line('income')} />
        <path className="opsLine expense" d={line('expenses')} />
        {rows.map((row, i) => <g key={row.month}>
          <circle className="opsDot income" cx={x(i)} cy={y(Number(row.income))} r={active === i ? 5 : 3.5} />
          <circle className="opsDot expense" cx={x(i)} cy={y(Number(row.expenses))} r={active === i ? 5 : 3.5} />
          <text className="opsTick" x={x(i)} y={H - 8} textAnchor="middle">{monthTick(row.month)}</text>
          <rect className="opsHit" x={x(i) - plotW / Math.max(2, rows.length) / 2} y={PAD.top} width={plotW / Math.max(1, rows.length)} height={plotH} onMouseEnter={() => setActive(i)} />
        </g>)}
        {last && active === null && <>{endLabel(last.income, 'income', incomeHigher ? -28 : 8)}{endLabel(last.expenses, 'expense', incomeHigher ? 8 : -28)}</>}
      </svg>
      {r && active !== null && <div className="opsTooltip" role="status" style={{ left: `${Math.min(Math.max(x(active), 80), W - 80)}px` }}>
        <strong>{monthYear(r.month)}</strong>
        <span>{incomeLabel}: <bdi>{formatAmount(r.income, currency)}</bdi></span>
        <span>{expenseLabel}: <bdi>{formatAmount(r.expenses, currency)}</bdi></span>
      </div>}
    </div>
    <details className="opsChartTable"><summary>{t('ops.chart.table')}</summary>
      <table id={tableId} className="finGrid"><caption className="visuallyHidden">{title}</caption>
        <thead><tr><th scope="col">{t('ops.chart.month')}</th><th scope="col" className="num">{incomeLabel}</th><th scope="col" className="num">{expenseLabel}</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.month}><th scope="row">{monthYear(row.month)}</th><td className="num"><bdi>{formatAmount(row.income, currency)}</bdi></td>
          <td className="num"><bdi>{formatAmount(row.expenses, currency)}</bdi></td></tr>)}</tbody>
      </table>
    </details>
  </figure>;
}

export interface UsageRow { month: string; litres: string; km: string }

/** Litres per month as bars (left axis) and kilometres as a line (right axis). */
export function UsageChart({ rows, title, litresLabel, kmLabel }: { rows: readonly UsageRow[]; title: string; litresLabel: string; kmLabel: string }) {
  const [plotRef, W] = useWidth(260);
  const tableId = useId();
  const pad = { ...PAD, left: 44, right: 52 };
  const h = 200;
  const litres = ticks(0, Math.max(1, ...rows.map((r) => Number(r.litres))), 4);
  const kms = ticks(0, Math.max(1, ...rows.map((r) => Number(r.km))), 4);
  const plotW = W - pad.left - pad.right, plotH = h - pad.top - pad.bottom;
  const band = plotW / Math.max(1, rows.length);
  const cx = (i: number) => pad.left + band * i + band / 2;
  const yL = (v: number) => pad.top + plotH - (v / litres[litres.length - 1]) * plotH;
  const yK = (v: number) => pad.top + plotH - (v / kms[kms.length - 1]) * plotH;
  const barW = Math.min(30, band * 0.5);
  const path = rows.map((r, i) => `${i ? 'L' : 'M'}${cx(i)},${yK(Number(r.km))}`).join(' ');
  return <figure className="opsFigure">
    <ul className="opsLegend">
      <li><span className="opsSwatch income" aria-hidden="true" />{litresLabel}</li>
      <li><span className="opsSwatch line" aria-hidden="true" />{kmLabel}</li>
    </ul>
    <div className="opsPlot" ref={plotRef}>
      <svg width={W} height={h} viewBox={`0 0 ${W} ${h}`} role="img" aria-label={title} aria-describedby={tableId} direction="ltr">
        {litres.map((v) => <g key={`l${v}`}><line className={v === 0 ? 'opsAxisZero' : 'opsGridLine'} x1={pad.left} x2={W - pad.right} y1={yL(v)} y2={yL(v)} />
          <text className="opsTick" x={pad.left - 6} y={yL(v) + 4} textAnchor="end">{compact(v, '')}</text></g>)}
        {kms.map((v) => <text key={`k${v}`} className="opsTick" x={W - pad.right + 6} y={yK(v) + 4} textAnchor="start">{compact(v, '')}</text>)}
        {rows.map((row, i) => <g key={row.month}>
          <rect className="opsBar" x={cx(i) - barW / 2} y={yL(Number(row.litres))} width={barW} height={Math.max(0, yL(0) - yL(Number(row.litres)))} rx={3} />
          <text className="opsTick" x={cx(i)} y={h - 8} textAnchor="middle">{monthTick(row.month)}</text>
        </g>)}
        <path className="opsLine km" d={path} />
        {rows.map((row, i) => <circle key={row.month} className="opsDot km" cx={cx(i)} cy={yK(Number(row.km))} r={3.5} />)}
      </svg>
    </div>
    <details className="opsChartTable"><summary>{t('ops.chart.table')}</summary>
      <table id={tableId} className="finGrid"><caption className="visuallyHidden">{title}</caption>
        <thead><tr><th scope="col">{t('ops.chart.month')}</th><th scope="col" className="num">{litresLabel}</th><th scope="col" className="num">{kmLabel}</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.month}><th scope="row">{monthYear(row.month)}</th><td className="num"><bdi>{formatNumber(Number(row.litres))}</bdi></td>
          <td className="num"><bdi>{formatNumber(Number(row.km))}</bdi></td></tr>)}</tbody>
      </table>
    </details>
  </figure>;
}

export interface ShareRow { key: string; label: string; amount: string; icon?: ReactNode }

/**
 * Amounts as horizontal bars, each with its share of the total ("SAR 6,820 (37%)"). The bar is
 * decoration: the label and the figures carry the meaning. Shares are of `total` when given (the
 * whole, when the list shows only the largest rows), else of the rows' sum.
 */
export function ShareBars({ rows, currency, label, total }: { rows: readonly ShareRow[]; currency: string; label: string; total?: number }) {
  const sum = total ?? rows.reduce((s, r) => s + Math.max(0, Number(r.amount)), 0);
  const top = Math.max(...rows.map((r) => Math.max(0, Number(r.amount))), 0);
  return <ul className="opsShares" aria-label={label}>
    {rows.map((row) => {
      const value = Math.max(0, Number(row.amount));
      const share = sum > 0 ? Math.round((value / sum) * 100) : 0;
      return <li key={row.key}>
        <span className="opsShareLabel">{row.icon && <span className="opsShareIcon" aria-hidden="true">{row.icon}</span>}<span dir="auto">{row.label}</span></span>
        <span className="opsShareTrack" aria-hidden="true"><span style={{ width: `${top > 0 ? Math.max(2, (value / top) * 100) : 0}%` }} /></span>
        <span className="opsShareValue"><bdi>{formatAmount(row.amount, currency)}</bdi> <small>({t('ops.share', { percent: share })})</small></span>
      </li>;
    })}
  </ul>;
}

/** A progress ring with the figure in the middle ("4/5", "94%"); the label is read out. */
export function Ring({ value, max, text, label, tone = 'ok' }: { value: number; max: number; text: string; label: string; tone?: 'ok' | 'warn' }) {
  const r = 34, c = 2 * Math.PI * r;
  const share = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  return <span className={`opsRing ${tone}`} role="img" aria-label={label}>
    <svg width={84} height={84} viewBox="0 0 84 84" aria-hidden="true">
      <circle className="opsRingTrack" cx={42} cy={42} r={r} />
      <circle className="opsRingFill" cx={42} cy={42} r={r} strokeDasharray={`${c * share} ${c}`} transform="rotate(-90 42 42)" />
    </svg>
    <strong aria-hidden="true" dir="ltr">{text}</strong>
  </span>;
}

/** A plain progress bar with its words beside it (budgets). */
export function Meter({ percent, label, tone = 'ok' }: { percent: number; label: string; tone?: 'ok' | 'warn' | 'bad' }) {
  const shown = Math.max(0, Math.min(100, percent));
  return <span className={`opsMeter ${tone}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(shown)} aria-label={label}>
    <span style={{ width: `${shown}%` }} />
  </span>;
}
