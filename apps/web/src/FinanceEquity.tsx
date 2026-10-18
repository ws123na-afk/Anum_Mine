// SIMULATED P&L and drawdown over time for one paper or shadow portfolio
// (docs/finance-trading-intel.md#pl-and-drawdown-over-time). Read only: the curve comes from
// GET /intel/portfolios/{id}/equity, rebuilt by the API from stored orders and recorded prices.
// Drawn as plain SVG at the panel's real width, with the same figures as a paged table.
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, LineChart } from 'lucide-react';
import type { FinanceEquity, FinanceEquitySeries, FinancePortfolioMode } from '@anum/contracts';
import * as api from './lib/financeApi';
import { isolate } from './lib/finance';
import {
  DEFAULT_RANGE, RANGES, TABLE_PAGE, amount, benchmarkLine, benchmarkValues, chartSummary, drawdownLine, frame, latest, latestBenchmark, percent, pick, scale,
  scalePnl, tableRows, tick, type Measure, type Range,
} from './lib/financeEquity';
import { t } from './lib/i18n';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import './financeEquity.css';

function useWidth(): [(node: HTMLDivElement | null) => void, number] {
  const [node, setNode] = useState<HTMLDivElement | null>(null), [width, setWidth] = useState(0);
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

export function EquityCharts({ portfolioId, name, mode, onOpenTrading }: { portfolioId: string; name: string; mode: FinancePortfolioMode; onOpenTrading: () => void }) {
  const [range, setRange] = useState<Range>(DEFAULT_RANGE), [currency, setCurrency] = useState('');
  const [state, setState] = useState<Load<FinanceEquity>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await api.getPortfolioEquity(portfolioId, range) }); } catch (e) { setState(failed(e)); }
  }, [portfolioId, range]);
  useEffect(() => { void load(); }, [load]);
  const headingId = useId(), rangeId = useId(), currencyId = useId();
  const paper = mode === 'paper';
  const equity = state.kind === 'ready' ? state.value : null;
  const series = equity?.series.find((s) => s.currency === currency) ?? equity?.series[0] ?? null;
  return <section className="finEquity" aria-labelledby={headingId}>
    <div className="finEquityHead">
      <h4 id={headingId}><LineChart aria-hidden="true" className="cmInlineIcon" />{t('finance.equity.title')}</h4>
      <div className="finEquityControls">
        <label className="field finEquityField" htmlFor={rangeId}><span>{t('finance.equity.range')}</span>
          <select id={rangeId} value={range} onChange={(e) => setRange(Number(e.target.value) as Range)}>
            {RANGES.map((r) => <option key={r} value={r}>{t('finance.equity.days', { count: r })}</option>)}
          </select></label>
        {equity && equity.series.length > 1 && <label className="field finEquityField" htmlFor={currencyId}><span>{t('finance.equity.currency')}</span>
          <select id={currencyId} value={series?.currency ?? ''} onChange={(e) => setCurrency(e.target.value)}>
            {equity.series.map((s) => <option key={s.currency} value={s.currency}>{s.currency}</option>)}
          </select></label>}
      </div>
    </div>
    {state.kind === 'loading' && <Notice busy>{t('finance.equity.loading')}</Notice>}
    {state.kind === 'denied' && <Notice error>{t('finance.denied.text')}</Notice>}
    {state.kind === 'error' && <LoadError message={t('finance.equity.error', { message: state.message })} retry={() => void load()} />}
    {equity && (equity.orders === 0 || !series)
      ? <Empty icon={<LineChart aria-hidden="true" />} title={t('finance.equity.empty')} text={paper ? t('finance.equity.emptyPaper') : t('finance.equity.emptyShadow')}
        action={<button type="button" className="secondary" onClick={onOpenTrading}>{t('finance.intel.openPaper')}</button>} />
      : equity && series && <SeriesView key={`${series.currency}-${range}`} equity={equity} series={series} paper={paper} name={name} />}
  </section>;
}

function SeriesView({ equity, series, paper, name }: { equity: FinanceEquity; series: FinanceEquitySeries; paper: boolean; name: string }) {
  const now = latest(series);
  return <>
    <dl className="finFigures finEquityFigures">
      {paper && <div><dt>{t('finance.equity.value')}</dt><dd dir="ltr">{amount(now?.value ?? null, series.currency)}</dd></div>}
      <div><dt>{t('finance.equity.pnl')}</dt><dd dir="ltr">{amount(now?.pnl ?? null, series.currency)}</dd></div>
      {paper && <div><dt>{t('finance.equity.benchmark.title')}</dt><dd dir="ltr">{amount(latestBenchmark(series.benchmark), series.currency)}</dd></div>}
      <div><dt>{t('finance.equity.maxDrawdown')}</dt><dd>{isolate(drawdownLine(series, paper))}</dd></div>
      <div><dt>{t('finance.equity.period')}</dt><dd><span dir="ltr">{equity.start} / {equity.end}</span> <small className="muted">{t('finance.equity.filled', { count: equity.orders })}</small></dd></div>
    </dl>
    {series.gaps > 0 && <p className="notice" role="note">{t('finance.equity.gaps', { count: series.gaps, age: equity.max_price_age_days, list: isolate(series.unpriced.join(', ') || '-') })}</p>}
    {equity.prices_truncated && <p className="notice" role="note">{t('finance.equity.truncated')}</p>}
    {benchmarkLine(series) && <p className="muted finHelp finEquityBench">{isolate(benchmarkLine(series) ?? '')}</p>}
    <div className="finEquityCharts">
      <Chart series={series} measure="pnl" paper={paper} title={t('finance.equity.pnlChart', { currency: series.currency })} />
      <Chart series={series} measure="drawdown" paper={paper} title={paper ? t('finance.equity.drawdownChartPct') : t('finance.equity.drawdownChart', { currency: series.currency })} />
    </div>
    <Table series={series} paper={paper} name={name} />
    <p className="muted finHelp">{t('finance.equity.note')}</p>
  </>;
}

function Chart({ series, measure, paper, title }: { series: FinanceEquitySeries; measure: Measure; paper: boolean; title: string }) {
  const [measureRef, measured] = useWidth();
  const width = Math.max(240, Math.round(measured) || 480), height = 176;
  const f = useMemo(() => frame(width, height), [width]);
  const asPercent = measure === 'drawdown' && paper;
  const pair = useMemo(() => (measure === 'pnl' ? scalePnl(series, f) : { portfolio: scale(series.points.map((p) => pick(p, measure, paper)), f), benchmark: null }),
    [series, measure, paper, f]);
  const scaled = pair.portfolio, bench = pair.benchmark;
  const titleId = useId();
  const first = series.points[0]?.day ?? '', last = series.points[series.points.length - 1]?.day ?? '';
  return <figure className="finEquityChart" aria-labelledby={titleId}>
    <figcaption id={titleId}>{title}</figcaption>
    <div className="cmChart finChart" ref={measureRef}>
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} direction="ltr" role="img"
        aria-label={measure === 'pnl' ? chartSummary(series, paper) : `${title}. ${drawdownLine(series, paper)}`}>
        <line className="finEquityZero" x1={f.left} x2={width - f.right} y1={scaled.zeroY} y2={scaled.zeroY} />
        {measure === 'drawdown' && <path className="finEquityArea" d={scaled.area} />}
        {bench && <path className="finEquityBenchLine" d={bench.line} data-testid="equity-benchmark-line" />}
        <path className={measure === 'pnl' ? 'finEquityLine' : 'finEquityLine finEquityDown'} d={scaled.line} />
        {scaled.last && <circle className={measure === 'pnl' ? 'finEquityDot' : 'finEquityDot finEquityDown'} cx={scaled.last.x} cy={scaled.last.y} r={3} />}
        <text className="finLabel" x={f.left} y={f.top - 5}>{tick(scaled.max, asPercent)}</text>
        <text className="finLabel" x={f.left} y={height - f.bottom + 14}>{tick(scaled.min, asPercent)}</text>
        <text className="finLabel" x={f.left} y={height - 2}>{first}</text>
        <text className="finLabel" x={width - f.right} y={height - 2} textAnchor="end">{last}</text>
      </svg>
    </div>
    {bench && <ul className="finEquityLegend" aria-hidden="true">
      <li><span className="finEquityKey" />{t('finance.equity.benchmark.legendPortfolio')}</li>
      <li><span className="finEquityKey finEquityKeyBench" />{t('finance.equity.benchmark.legendBenchmark')}</li>
    </ul>}
  </figure>;
}

function Table({ series, paper, name }: { series: FinanceEquitySeries; paper: boolean; name: string }) {
  const [offset, setOffset] = useState(0);
  const rows = tableRows(series, offset);
  const total = series.points.length;
  const values = benchmarkValues(series), withBench = paper && values.some((v) => v !== null);
  const benchByDay = new Map(series.points.map((p, i) => [p.day, values[i]]));
  return <details className="cmChartTable"><summary>{t('finance.equity.table')}</summary>
    <div className="cmTableWrap"><table className="cmTable finTable finIntelCards" aria-label={t('finance.equity.tableOf', { name, currency: series.currency })}>
      <thead><tr><th scope="col">{t('finance.equity.day')}</th>{paper && <th scope="col">{t('finance.equity.value')}</th>}<th scope="col">{t('finance.equity.pnl')}</th><th scope="col">{t('finance.equity.drawdown')}</th>{withBench && <th scope="col">{t('finance.equity.benchmark.title')}</th>}</tr></thead>
      <tbody>{rows.map((p) => <tr key={p.day}>
        <th scope="row" dir="ltr">{p.day}</th>
        {paper && <td data-label={t('finance.equity.value')}><span dir="ltr">{p.complete ? amount(p.value, series.currency) : t('finance.equity.gap')}</span></td>}
        <td data-label={t('finance.equity.pnl')}><span dir="ltr">{p.complete ? amount(p.pnl, series.currency) : t('finance.equity.gap')}</span></td>
        <td data-label={t('finance.equity.drawdown')}><span dir="ltr">{!p.complete ? t('finance.equity.gap') : paper ? `${amount(p.drawdown, series.currency)} (${percent(p.drawdown_pct)})` : amount(p.drawdown, series.currency)}</span></td>
        {withBench && <td data-label={t('finance.equity.benchmark.title')}><span dir="ltr">{amount(benchByDay.get(p.day)?.toFixed(2) ?? null, series.currency)}</span></td>}
      </tr>)}</tbody>
    </table></div>
    {total > TABLE_PAGE && <nav className="cmPager" aria-label={t('finance.equity.tablePages')}>
      <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - TABLE_PAGE))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('finance.equity.newer')}</button>
      <span className="muted" aria-live="polite">{t('finance.equity.rows', { from: offset + 1, to: offset + rows.length, total })}</span>
      <button type="button" className="secondary" disabled={offset + rows.length >= total} onClick={() => setOffset(offset + TABLE_PAGE)}>{t('finance.equity.older')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
    </nav>}
  </details>;
}
