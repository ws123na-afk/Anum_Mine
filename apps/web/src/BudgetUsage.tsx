// Where the model spend goes (docs/model-gateway.md#spend-breakdown): `GET /api/v1/model-budgets/usage`
// grouped by day, month, person, agent or feature for this workspace or the whole organization, over a
// chosen period. One chart (columns over time, ranked bars otherwise; one hue, the figures carry the
// meaning), the same rows as a paged table, and a CSV export of the whole period. A lazy chunk of the
// Budgets page.
import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, Download, RefreshCw, Search } from 'lucide-react';
import { apiRequest, authHeaders, currentUserId } from './lib/api';
import {
  RANGE_PRESETS, USAGE_GROUPS, USAGE_PAGE, barLengths, csvFileName, formatMeasure, isTimeSeries, measureOf, rangeFor, rowLabel, shareOf, tickLabel, ticks,
  usagePath, validRange, type RangePreset, type UsageBreakdown, type UsageGroup, type UsageMeasure, type UsageRange, type UsageRow, type UsageScope,
} from './lib/budgetUsage';
import { formatTokens, formatUsd } from './lib/admin';
import { apiErrorFromResponse, describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import './budgetUsage.css';

const base = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

type Load = { kind: 'loading' } | { kind: 'ready'; value: UsageBreakdown; rows: UsageRow[] } | { kind: 'error'; detail: string };

function useWidth(): [(node: HTMLDivElement | null) => void, number] {
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

const dayText = (iso: string) => formatDateTime(`${iso}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export function BudgetUsage() {
  const id = useId();
  const [group, setGroup] = useState<UsageGroup>('day');
  const [scope, setScope] = useState<UsageScope>('workspace');
  const [measure, setMeasure] = useState<UsageMeasure>('cost');
  const [preset, setPreset] = useState<RangePreset>('month');
  const [custom, setCustom] = useState<UsageRange>(() => rangeFor('month', new Date()));
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [more, setMore] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const range = preset === 'custom' ? custom : rangeFor(preset, new Date());
  const rangeOk = validRange(range);
  const me = currentUserId();

  const fetchPage = useCallback((offset: number) => apiRequest<UsageBreakdown>(usagePath({ group, scope, range, offset }), { method: 'GET' }), [group, scope, range.from, range.to]); // eslint-disable-line react-hooks/exhaustive-deps

  const reload = useCallback(async () => {
    if (!rangeOk) return;
    setLoad({ kind: 'loading' });
    try {
      const value = await fetchPage(0);
      setLoad({ kind: 'ready', value, rows: value.rows });
    } catch (error) { setLoad({ kind: 'error', detail: describeError(error) }); }
  }, [fetchPage, rangeOk]);
  useEffect(() => { void reload(); }, [reload]);

  const loadMore = async () => {
    if (load.kind !== 'ready') return;
    setMore(true);
    try {
      const next = await fetchPage(load.rows.length);
      setLoad({ kind: 'ready', value: next, rows: [...load.rows, ...next.rows] });
    } catch (error) { setNote({ text: t('budgets.usage.error', { message: describeError(error) }), error: true }); }
    finally { setMore(false); }
  };

  const exportCsv = async () => {
    setExporting(true); setNote({ text: t('budgets.usage.exporting') });
    try {
      const response = await fetch(`${base}${usagePath({ group, scope, range, format: 'csv' })}`, { headers: await authHeaders() });
      if (!response.ok) throw await apiErrorFromResponse(response, t('budgets.usage.exportFailed', { message: '' }).trim());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = csvFileName(scope, group, range); link.click();
      URL.revokeObjectURL(url);
      setNote({ text: t('budgets.usage.exported') });
    } catch (error) { setNote({ text: t('budgets.usage.exportFailed', { message: describeError(error) }), error: true }); }
    finally { setExporting(false); }
  };

  const groupName = t(`budgets.usage.group.${group}` as MessageKey);
  const measureName = t(`budgets.usage.measure.${measure}` as MessageKey);
  // Rows answer the group and scope they were asked for: until the new answer arrives the old rows are not
  // drawn under the new group (a person's id is not a date), and the loading line shows instead.
  const ready = load.kind === 'ready' && load.value.group_by === group && load.value.scope === scope ? load : null;
  const tableId = `${id}-table`;

  return <section className="surface budgetUsage" aria-label={t('budgets.usage.region')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('budgets.usage.eyebrow')}</p><h2>{t('budgets.usage.title')}</h2><p className="muted">{t('budgets.usage.intro')}</p></div>
      <div className="actions budgetUsageHeaderActions">
        <button type="button" className="secondary" disabled={exporting || !rangeOk} onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('budgets.usage.export')}</button>
        <button type="button" className="secondary" onClick={() => void reload()}><RefreshCw aria-hidden="true" />{t('admin.refresh')}</button>
      </div>
    </div>
    <div className="budgetUsageControls">
      <fieldset className="budgetUsageGroup"><legend>{t('budgets.usage.groupBy')}</legend>
        <div className="segmentedRadios" role="radiogroup" aria-label={t('budgets.usage.groupBy')}>
          {USAGE_GROUPS.map((value) => <label key={value}><input type="radio" name={`${id}-group`} checked={group === value} onChange={() => setGroup(value)} />{t(`budgets.usage.group.${value}` as MessageKey)}</label>)}
        </div>
      </fieldset>
      <label className="field"><span>{t('budgets.usage.scope')}</span>
        <select value={scope} onChange={(event) => setScope(event.target.value as UsageScope)}>
          <option value="workspace">{t('budgets.usage.scope.workspace')}</option>
          <option value="tenant">{t('budgets.usage.scope.tenant')}</option>
        </select></label>
      <label className="field"><span>{t('budgets.usage.range')}</span>
        <select value={preset} onChange={(event) => { const next = event.target.value as RangePreset; if (next === 'custom') setCustom(range); setPreset(next); }}>
          {RANGE_PRESETS.map((value) => <option key={value} value={value}>{t(`budgets.usage.range.${value}` as MessageKey)}</option>)}
        </select></label>
      {preset === 'custom' && <>
        <label className="field"><span>{t('budgets.usage.from')}</span><input type="date" dir="ltr" value={custom.from} max={custom.to || undefined} onChange={(event) => setCustom({ ...custom, from: event.target.value })} /></label>
        <label className="field"><span>{t('budgets.usage.to')}</span><input type="date" dir="ltr" value={custom.to} min={custom.from || undefined} onChange={(event) => setCustom({ ...custom, to: event.target.value })} /></label>
      </>}
      <label className="field"><span>{t('budgets.usage.measure')}</span>
        <select value={measure} onChange={(event) => setMeasure(event.target.value as UsageMeasure)}>
          <option value="cost">{t('budgets.usage.measure.cost')}</option>
          <option value="tokens">{t('budgets.usage.measure.tokens')}</option>
        </select></label>
    </div>
    {!rangeOk && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('budgets.usage.badRange')}</p>}
    {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{note.text}</p>}
    {rangeOk && (load.kind === 'loading' || (load.kind === 'ready' && !ready)) && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('budgets.usage.loading')}</p>}
    {load.kind === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('budgets.usage.error', { message: load.detail })}</span><button type="button" className="secondary" onClick={() => void reload()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>}
    {ready && ready.value.recorded_since && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('budgets.usage.recordedSince', { date: dayText(ready.value.recorded_since) })}</p>}
    {ready && ready.rows.length === 0 && <div className="emptyState budgetUsageEmpty"><Search aria-hidden="true" /><h3>{t('budgets.usage.empty')}</h3><p>{t('budgets.usage.emptyText', { from: dayText(ready.value.from_day), to: dayText(ready.value.to_day) })}</p></div>}
    {ready && ready.rows.length > 0 && <>
      <dl className="budgetStats budgetUsageTotals">
        <div><dt>{t('budgets.usage.col.calls')}</dt><dd>{formatTokens(ready.value.total.calls)}</dd></div>
        <div><dt>{t('budgets.usage.col.tokens')}</dt><dd>{formatTokens(ready.value.total.input_tokens + ready.value.total.output_tokens)}</dd></div>
        <div><dt>{t('budgets.usage.col.cost')}</dt><dd>{formatUsd(ready.value.total.estimated_cost_usd)}</dd></div>
        <div><dt>{t('budgets.usage.col.unpriced')}</dt><dd>{formatTokens(ready.value.total.unpriced_calls)}</dd></div>
      </dl>
      {isTimeSeries(group)
        ? <UsageColumns rows={ready.rows} group={group} measure={measure} label={t('budgets.usage.chart', { measure: measureName, group: groupName })} tableId={tableId} />
        : <UsageBars rows={ready.rows} group={group} measure={measure} total={ready.value.total} label={t('budgets.usage.chart', { measure: measureName, group: groupName })} tableId={tableId} me={me} />}
      {ready.value.count > ready.rows.length && <p className="muted budgetUsageChartNote">{t('budgets.usage.chartOf', { count: ready.rows.length })}</p>}
      <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('budgets.usage.chart', { measure: measureName, group: groupName })}><table className="dataTable budgetUsageTable" id={tableId}>
        <caption className="visuallyHidden">{t('budgets.usage.chart', { measure: measureName, group: groupName })}</caption>
        <thead><tr>
          <th scope="col">{t(`budgets.usage.col.${group}` as MessageKey)}</th>
          <th scope="col" className="num">{t('budgets.usage.col.calls')}</th>
          <th scope="col" className="num optional">{t('budgets.usage.col.input')}</th>
          <th scope="col" className="num optional">{t('budgets.usage.col.output')}</th>
          <th scope="col" className="num">{t('budgets.usage.col.tokens')}</th>
          <th scope="col" className="num">{t('budgets.usage.col.cost')}</th>
          <th scope="col" className="num optional">{t('budgets.usage.col.unpriced')}</th>
        </tr></thead>
        <tbody>{ready.rows.map((row) => <tr key={row.key}>
          <th scope="row"><span dir="auto">{rowLabel(group, row, me)}</span>{group === 'agent' && row.label && <small className="muted budgetUsageId" dir="ltr">{row.key}</small>}</th>
          <td className="num">{formatTokens(row.calls)}</td>
          <td className="num optional">{formatTokens(row.input_tokens)}</td>
          <td className="num optional">{formatTokens(row.output_tokens)}</td>
          <td className="num">{formatTokens(row.total_tokens)}</td>
          <td className="num">{formatUsd(row.estimated_cost_usd)}</td>
          <td className="num optional">{formatTokens(row.unpriced_calls)}</td>
        </tr>)}</tbody>
        <tfoot><tr>
          <th scope="row">{t('budgets.usage.total')}</th>
          <td className="num">{formatTokens(ready.value.total.calls)}</td>
          <td className="num optional">{formatTokens(ready.value.total.input_tokens)}</td>
          <td className="num optional">{formatTokens(ready.value.total.output_tokens)}</td>
          <td className="num">{formatTokens(ready.value.total.input_tokens + ready.value.total.output_tokens)}</td>
          <td className="num">{formatUsd(ready.value.total.estimated_cost_usd)}</td>
          <td className="num optional">{formatTokens(ready.value.total.unpriced_calls)}</td>
        </tr></tfoot>
      </table></div>
      <div className="budgetUsagePaging">
        <p className="muted" role="status">{t('budgets.usage.showing', { shown: formatNumber(ready.rows.length), count: ready.value.count })}</p>
        {ready.value.count > ready.rows.length && <button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('budgets.usage.loadMore')}</button>}
      </div>
    </>}
  </section>;
}

/** Columns over time: one hue, the axis in round steps, the table below as the accessible form. */
function UsageColumns({ rows, group, measure, label, tableId }: { rows: UsageRow[]; group: UsageGroup; measure: UsageMeasure; label: string; tableId: string }) {
  const [ref, width] = useWidth();
  const [active, setActive] = useState<number | null>(null);
  const pad = { top: 10, right: 12, bottom: 26, left: 56 };
  const height = 200;
  const values = rows.map((row) => measureOf(row, measure));
  const axis = ticks(Math.max(0, ...values));
  const top = axis[axis.length - 1] || 1;
  const w = Math.max(width, 240), plotW = w - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const band = plotW / Math.max(1, rows.length);
  const barW = Math.max(4, Math.min(28, band * 0.6));
  const y = (value: number) => pad.top + plotH - (value / top) * plotH;
  const every = Math.max(1, Math.ceil(rows.length / Math.max(2, Math.floor(plotW / 48))));
  const hover = active === null ? null : rows[active];
  return <figure className="budgetUsageFigure">
    <div className="budgetUsagePlot" ref={ref} dir="ltr">
      {width > 0 && <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} role="img" aria-label={label} aria-describedby={tableId} onPointerLeave={() => setActive(null)}>
        {axis.map((value) => <g key={value}><line className={value === 0 ? 'budgetAxisZero' : 'budgetGridLine'} x1={pad.left} x2={w - pad.right} y1={y(value)} y2={y(value)} />
          <text className="budgetTick" x={pad.left - 6} y={y(value) + 4} textAnchor="end">{measure === 'cost' ? formatUsd(value) : formatTokens(value)}</text></g>)}
        {rows.map((row, index) => {
          const x = pad.left + band * index + band / 2;
          return <g key={row.key} onPointerEnter={() => setActive(index)}>
            <rect className="budgetHit" x={pad.left + band * index} y={pad.top} width={band} height={plotH} />
            <rect className={`budgetBar ${active === index ? 'active' : ''}`} x={x - barW / 2} y={y(values[index])} width={barW} height={Math.max(0, y(0) - y(values[index]))} rx={3} />
            {index % every === 0 && <text className="budgetTick" x={x} y={height - 8} textAnchor="middle">{tickLabel(group, row.key)}</text>}
          </g>;
        })}
      </svg>}
    </div>
    <figcaption className="budgetUsageCaption" aria-live="polite">{hover ? `${rowLabel(group, hover)}: ${formatMeasure(measureOf(hover, measure), measure)}` : label}</figcaption>
  </figure>;
}

/** Ranked horizontal bars, largest first, one hue; the label and figures carry the meaning. */
function UsageBars({ rows, group, measure, total, label, tableId, me }: { rows: UsageRow[]; group: UsageGroup; measure: UsageMeasure; total: UsageBreakdown['total']; label: string; tableId: string; me: string | null }) {
  const lengths = barLengths(rows, measure);
  return <figure className="budgetUsageFigure">
    <ul className="budgetUsageBars" aria-label={label} aria-describedby={tableId}>
      {rows.map((row, index) => <li key={row.key}>
        <span className="budgetUsageBarLabel" dir="auto">{rowLabel(group, row, me)}</span>
        <span className="budgetUsageTrack" aria-hidden="true"><span style={{ width: `${lengths[index]}%` }} /></span>
        <span className="budgetUsageValue"><bdi>{formatMeasure(measureOf(row, measure), measure)}</bdi> <small>{t('budgets.usage.share', { percent: shareOf(row, total, measure) })}</small></span>
      </li>)}
    </ul>
    <figcaption className="visuallyHidden">{label}</figcaption>
  </figure>;
}
