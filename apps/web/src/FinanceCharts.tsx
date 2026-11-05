// Charts for Finance Holdings and the paper trading lab (docs/finance.md#holdings). Plain SVG at the card's real
// width: a value line with a hover crosshair and a table view, an allocation donut with its legend, and a small
// sparkline. Lazy with the pages that use them; no chart library.
import { useEffect, useId, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { labelIndices, nearest, plot, sparkline as sparkPath, donut as donutPaths, type Box } from './lib/financeCharts';
import type { Slice } from './lib/financeHoldings';
import { t } from './lib/i18n';

export function useWidth(): [(node: HTMLDivElement | null) => void, number] {
  const [node, setNode] = useState<HTMLDivElement | null>(null), [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return undefined;
    const update = () => setWidth(Math.floor(node.getBoundingClientRect().width));
    update();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null;
    observer?.observe(node);
    return () => observer?.disconnect();
  }, [node]);
  return [setNode, width];
}

export interface LinePoint { label: string; value: number | null; short?: string }

/** One series over time (a single series: the title names it, so no legend box). Hover or arrow keys move the
 * crosshair; "Show as table" lists the same figures. Gaps stay gaps. */
export function LineChart({ points, format, axis, label, height = 220, tableCaption, testId }: {
  points: LinePoint[]; format: (value: number) => string; axis: (value: number) => string; label: string; height?: number; tableCaption: string; testId?: string;
}) {
  const [ref, width] = useWidth();
  const [active, setActive] = useState<number | null>(null), [table, setTable] = useState(false);
  const descId = useId();
  const axisWidth = Math.max(44, ...points.filter((p) => p.value !== null).map((p) => axis(p.value as number).length * 7 + 8));
  const box: Box = { width: Math.max(width, 200), height, left: axisWidth, right: 12, top: 12, bottom: 28 };
  const plotted = plot(points.map((p) => p.value), box);
  const hover = active === null ? null : plotted.points.find((p) => p.index === active) ?? null;
  const last = plotted.points[plotted.points.length - 1] ?? null;
  const move = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * box.width;
    setActive(nearest(plotted.points, x)?.index ?? null);
  };
  const keys = (event: KeyboardEvent<SVGSVGElement>) => {
    if (!plotted.points.length) return;
    const at = plotted.points.findIndex((p) => p.index === active);
    const forward = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!forward) return;
    event.preventDefault();
    const step = forward;
    const next = at < 0 ? (step > 0 ? 0 : plotted.points.length - 1) : Math.min(plotted.points.length - 1, Math.max(0, at + step));
    setActive(plotted.points[next].index);
  };
  const xLabels = labelIndices(points.length, Math.max(2, Math.min(6, Math.floor((box.width - box.left - box.right) / 96))));
  const known = points.filter((p) => p.value !== null);
  const summary = known.length ? t('fholdings.chart.summary', { first: known[0].label, last: known[known.length - 1].label, from: format(known[0].value as number), to: format(known[known.length - 1].value as number) }) : '';
  return <div className="finLine" data-testid={testId}>
    <div ref={ref} className="finLinePlot" dir="ltr">
      {width > 0 && <svg width={box.width} height={box.height} viewBox={`0 0 ${box.width} ${box.height}`} role="img" aria-label={label} aria-describedby={descId} tabIndex={0}
        onPointerMove={move} onPointerLeave={() => setActive(null)} onKeyDown={keys} onBlur={() => setActive(null)} className="finLineSvg">
        <desc id={descId}>{summary}</desc>
        <g className="finLineGrid" aria-hidden="true">
          {plotted.ticks.map((tick) => <g key={tick.value}><line x1={box.left} x2={box.width - box.right} y1={tick.y} y2={tick.y} />
            <text x={box.left - 8} y={tick.y} dy="0.32em" textAnchor="end">{axis(tick.value)}</text></g>)}
          {xLabels.map((i) => {
            const x = points.length <= 1 ? (box.left + box.width - box.right) / 2 : box.left + (i * (box.width - box.left - box.right)) / (points.length - 1);
            const anchor = i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle';
            return <text key={i} x={x} y={box.height - 8} textAnchor={anchor}>{points[i].short ?? points[i].label}</text>;
          })}
        </g>
        <path d={plotted.area} className="finLineArea" aria-hidden="true" />
        <path d={plotted.line} className="finLineLine" aria-hidden="true" />
        {last && !hover && <circle cx={last.x} cy={last.y} r={4} className="finLineDot" aria-hidden="true" />}
        {hover && <g aria-hidden="true"><line x1={hover.x} x2={hover.x} y1={box.top} y2={plotted.baseY} className="finLineCross" /><circle cx={hover.x} cy={hover.y} r={5} className="finLineDot" /></g>}
      </svg>}
      {hover && width > 0 && <div className="finLineTip" role="status" style={{ insetInlineStart: `${Math.min(Math.max(hover.x - 70, 0), box.width - 150)}px` }}>
        <span>{points[hover.index].label}</span><strong dir="ltr">{format(hover.value)}</strong></div>}
    </div>
    <button type="button" className="cmLinkButton finLineToggle" aria-expanded={table} onClick={() => setTable((v) => !v)}>{t(table ? 'fholdings.chart.hideTable' : 'fholdings.chart.showTable')}</button>
    {table && <div className="cmTableWrap finLineTable" tabIndex={0}><table className="cmTable finTable">
      <caption className="visuallyHidden">{tableCaption}</caption>
      <thead><tr><th scope="col">{t('fholdings.chart.date')}</th><th scope="col" className="num">{t('fholdings.chart.value')}</th></tr></thead>
      <tbody>{points.map((p) => <tr key={p.label}><th scope="row">{p.label}</th><td className="num" dir="ltr">{p.value === null ? '-' : format(p.value)}</td></tr>)}</tbody>
    </table></div>}
  </div>;
}

/** Allocation: a donut (the centre shows the total) and a legend with each share, so colour is never alone. */
export function Donut({ slices, total, totalLabel, format, label }: { slices: Slice[]; total: string; totalLabel: string; format: (slice: Slice) => string; label: string }) {
  const size = 168, paths = donutPaths(slices.map((s) => s.value), size / 2, size / 2, size / 2 - 2, size / 2 - 30);
  return <div className="finDonut">
    <div className="finDonutRing">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={label}>
        {paths.map((d, i) => <path key={slices[i].key} d={d} className={`finSeries finSeries-${slices[i].key === 'other' ? 'other' : i + 1}`} />)}
      </svg>
      <div className="finDonutCenter"><strong dir="ltr">{total}</strong><small>{totalLabel}</small></div>
    </div>
    <ul className="finLegend" aria-label={t('fholdings.allocation.legend')}>
      {slices.map((s, i) => <li key={s.key}><span className={`finSwatch finSeries-${s.key === 'other' ? 'other' : i + 1}`} aria-hidden="true" />
        <span className="finLegendName"><bdi>{s.label}</bdi></span><span className="finLegendValue" dir="ltr">{s.share.toFixed(1)}%</span>
        <span className="visuallyHidden">{format(s)}</span></li>)}
    </ul>
  </div>;
}

export function Sparkline({ values, tone, label }: { values: number[]; tone: 'up' | 'down' | 'flat'; label: string }) {
  const d = sparkPath(values, 72, 24);
  if (!d) return <span className="muted finSparkNone">-</span>;
  return <svg width={72} height={24} viewBox="0 0 72 24" className={`finSpark finSpark-${tone}`} role="img" aria-label={label}><path d={d} /></svg>;
}

/** A KPI tile: label, figure and an optional tone chip. */
export function Kpi({ label, value, chip, tone, hint, icon }: { label: string; value: ReactNode; chip?: string | null; tone?: 'up' | 'down' | ''; hint?: ReactNode; icon?: ReactNode }) {
  return <div className="finKpi">
    {icon && <span className="finKpiIcon" aria-hidden="true">{icon}</span>}
    <div><span className="finKpiLabel">{label}</span>
      <strong className={`finKpiValue ${tone ?? ''}`}>{value}</strong>
      {chip && <span className={`finChip ${tone ?? ''}`} dir="ltr">{chip}</span>}
      {hint && <small className="finKpiHint">{hint}</small>}</div>
  </div>;
}
