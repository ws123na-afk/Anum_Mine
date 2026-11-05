// The two line charts of the planning pages (docs/finance.md#debts-and-goals, #bills-and-budgets): the
// debt and savings outlook, and the month's spend against the budget. Plain SVG drawn at the card's real
// width (12px labels on a phone), with a table of the same figures behind "Show as table". The figures
// come from the pure functions in lib/financeDebtPlan.ts and the API's budget report; nothing is fixed.
import { useEffect, useId, useState } from 'react';
import { formatAmount } from './lib/finance';
import { fromMinor } from './lib/financeDebtPlan';
import { formatDateTime, t } from './lib/i18n';
import type { OutlookPoint } from './lib/financeDebtPlan';

function useWidth(): [(node: HTMLDivElement | null) => void, number] {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return undefined;
    const update = () => setWidth(node.getBoundingClientRect().width);
    update();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null;
    observer?.observe(node);
    return () => observer?.disconnect();
  }, [node]);
  return [setNode, width];
}

/** A round step for the y axis: 1, 2 or 5 times a power of ten, giving about four gridlines. */
export function niceStep(max: number): number {
  if (max <= 0) return 1;
  const raw = max / 4, power = 10 ** Math.floor(Math.log10(raw)), unit = raw / power;
  return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
}

interface Series { key: string; label: string; className: string; values: number[]; dashed?: boolean; area?: boolean }

/** A compact amount for axis labels ("12K", "1.2M"); the table carries the exact figures. */
function axisLabel(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`;
  return String(Math.round(value));
}

function LineChart({ title, labels, series, currency, digits, summary }: {
  title: string; labels: string[]; series: Series[]; currency: string; digits: number; summary: string;
}) {
  const [measure, measured] = useWidth();
  const rtl = typeof document !== 'undefined' && document.documentElement.dir === 'rtl';
  const width = Math.max(280, Math.round(measured) || 640), height = 220;
  const pad = { top: 10, bottom: 26, start: 46, end: 12 };
  const scale = 10 ** digits;
  const max = Math.max(1, ...series.flatMap((s) => s.values)) / scale;
  const step = niceStep(max), top = Math.ceil(max / step) * step || step;
  const plotW = width - pad.start - pad.end, plotH = height - pad.top - pad.bottom;
  const x = (i: number) => {
    const at = labels.length <= 1 ? 0 : (i / (labels.length - 1)) * plotW;
    return rtl ? width - pad.start - at : pad.start + at;
  };
  const y = (v: number) => pad.top + plotH - (v / scale / top) * plotH;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(plotW / 70))));
  return <>
    <div className="fplChart" ref={measure}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={summary} direction="ltr">
        {ticks.map((tick) => <g key={tick}>
          <line className="fplGrid" x1={pad.start} x2={width - pad.end} y1={y(tick * scale)} y2={y(tick * scale)} />
          <text className="fplAxis" x={rtl ? width - 4 : 4} y={y(tick * scale) + 4} textAnchor={rtl ? 'end' : 'start'}>{axisLabel(tick)}</text>
        </g>)}
        {labels.map((label, i) => ((i % every === 0 && labels.length - 1 - i >= every / 2) || i === labels.length - 1) && <text key={label + i} className="fplAxis" x={x(i)} y={height - 8}
          textAnchor={i === 0 ? (rtl ? 'end' : 'start') : i === labels.length - 1 ? (rtl ? 'start' : 'end') : 'middle'}>{label}</text>)}
        {series.map((s) => {
          const path = s.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
          return <g key={s.key} className={s.className}>
            {s.area && s.values.length > 1 && <path className="fplArea" d={`${path} L${x(s.values.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z`} />}
            <path className={`fplLine ${s.dashed ? 'dashed' : ''}`} d={path} />
            {s.values.length <= 16 && s.values.map((v, i) => <circle key={i} className="fplDot" cx={x(i)} cy={y(v)} r={3} />)}
          </g>;
        })}
      </svg>
    </div>
    <ul className="fplLegend">{series.map((s) => <li key={s.key} className={s.className}><span className={`fplSwatch ${s.dashed ? 'dashed' : ''}`} aria-hidden="true" />{s.label}</li>)}</ul>
    <details className="cmChartTable"><summary>{t('finance.chart.table')}</summary><div className="cmTableWrap" tabIndex={0}><table className="cmTable">
      <caption className="visuallyHidden">{title}</caption>
      <thead><tr><th scope="col">{t('fplan.chart.when')}</th>{series.map((s) => <th key={s.key} scope="col" className="num">{s.label}</th>)}</tr></thead>
      <tbody>{labels.map((label, i) => <tr key={label + i}><td>{label}</td>{series.map((s) => <td key={s.key} className="num" dir="ltr">{s.values[i] === undefined ? '-' : formatAmount(fromMinor(s.values[i], currency), currency)}</td>)}</tr>)}</tbody>
    </table></div></details>
  </>;
}

/** Total debt (falling) and goal savings (rising) over the coming months, in one currency. */
export function OutlookChart({ points, currency, digits }: { points: OutlookPoint[]; currency: string; digits: number }) {
  const id = useId();
  const labels = points.map((p) => formatDateTime(`${p.month}-01T12:00:00Z`, { month: 'short', year: '2-digit', timeZone: 'UTC' }));
  const last = points[points.length - 1];
  const summary = t('fplan.outlook.aria', {
    months: points.length - 1, debt: formatAmount(fromMinor(points[0]?.debt ?? 0, currency), currency), debtEnd: formatAmount(fromMinor(last?.debt ?? 0, currency), currency),
    saved: formatAmount(fromMinor(last?.savings ?? 0, currency), currency),
  });
  return <div aria-describedby={id}><p id={id} className="visuallyHidden">{summary}</p>
    <LineChart title={t('fplan.outlook.title')} labels={labels} currency={currency} digits={digits} summary={summary} series={[
      { key: 'debt', label: t('fplan.outlook.debt'), className: 'fplSeriesDebt', values: points.map((p) => p.debt), area: true },
      { key: 'savings', label: t('fplan.outlook.savings'), className: 'fplSeriesSave', values: points.map((p) => p.savings) },
    ]} />
  </div>;
}

/** The month's spend in the budgeted categories, day by day, against the budgets together (dashed). */
export function BudgetSpendChart({ points, budget, currency, digits }: { points: { day: string; spent: number }[]; budget: number; currency: string; digits: number }) {
  const labels = points.map((p) => formatDateTime(`${p.day}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' }));
  const spent = points[points.length - 1]?.spent ?? 0;
  const summary = t('fplan.budgetChart.aria', { spent: formatAmount(fromMinor(spent, currency), currency), budget: formatAmount(fromMinor(budget, currency), currency), days: points.length });
  return <LineChart title={t('fplan.budgetChart.title')} labels={labels} currency={currency} digits={digits} summary={summary} series={[
    { key: 'spent', label: t('fplan.budgetChart.actual', { amount: formatAmount(fromMinor(spent, currency), currency) }), className: 'fplSeriesSave', values: points.map((p) => p.spent), area: true },
    { key: 'budget', label: t('fplan.budgetChart.budget', { amount: formatAmount(fromMinor(budget, currency), currency) }), className: 'fplSeriesBudget', values: points.map(() => budget), dashed: true },
  ]} />;
}
