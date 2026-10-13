// Tyre planning (docs/tyre-planning.md): dashboard, forecasts, purchase plan, alerts, reports and
// the connection wizard, over /api/v1/tyre. The server enforces every role; this view hides what
// the caller's role cannot do and says who can.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { AlertTriangle, Bell, BellRing, CalendarClock, CheckCircle2, CircleDot, Download, FileText, Gauge, Info, Lock, Plug, RefreshCw, Save, Search, Send, Trash2, TrendingDown } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import {
  ALERT_KINDS, REPORT_KINDS, ago, alertKindLabel, confidenceTone, day, forecastTone, formatPrices, isRecipient, money, monthBars, monthLabel, newAlerts, num, pageRange, parsePrices, reportKindLabel, severityTone,
  type AlertPage, type Assumptions, type Dashboard, type ForecastPage, type PurchasePlan, type ReportSchedule, type ReportSummary, type ScheduleDraft, type TyreAlert, type TyreForecast, type TyreSetup as Setup, type Page,
} from './lib/tyre';
import * as tyreApi from './lib/tyreApi';
import { TyreSetup } from './TyreSetup';
import './tyre.css';

type Tab = 'dashboard' | 'forecasts' | 'plan' | 'alerts' | 'reports' | 'connection';
const TABS: Tab[] = ['dashboard', 'forecasts', 'plan', 'alerts', 'reports', 'connection'];
const PAGE = 25;
const SEEN_KEY = 'anum.tyre.seenAlerts';
const today = () => new Date().toISOString().slice(0, 10);

export function TyreView() {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('dashboard');
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    setState('loading');
    try {
      const next = await tyreApi.getSetup();
      setSetup(next);
      setState('ready');
      setTab((current) => (!next.sync && current !== 'connection' && current !== 'reports' ? 'connection' : current));
    } catch (e) {
      if (isPermissionDenied(e)) setState('denied');
      else { setError(describeError(e)); setState('error'); }
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function syncNow() {
    setSyncing(true); setNotice(null);
    try {
      const result = await tyreApi.sync();
      setSetup(result.setup);
      setVersion((value) => value + 1);
      setNotice({ text: t('tyre.sync.done', { count: result.new_alerts }), error: false });
    } catch (e) { setNotice({ text: t('tyre.sync.failed', { reason: describeError(e) }), error: true }); } finally { setSyncing(false); }
  }

  if (state === 'loading' && !setup) return <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('tyre.loading')}</p>;
  if (state === 'denied') return <section className="surface tyreCard tyreEmpty"><Lock aria-hidden="true" /><h2>{t('tyre.denied.title')}</h2><p className="muted">{t('tyre.denied.body')}</p></section>;
  if (state === 'error' || !setup) return <div className="notice errorNotice" role="alert"><AlertTriangle aria-hidden="true" /><span>{t('tyre.loadFailed', { reason: error })}</span><button type="button" className="secondary" onClick={() => void load()}>{t('common.retry')}</button></div>;

  const synced = Boolean(setup.sync);
  return <div className="tyreLayout">
    <section className="moduleIntro">
      <div><p>{t('tyre.subtitle')}</p></div>
      <div className="tyreSyncState">
        {setup.sync ? <span className="muted">{t('tyre.sync.dataFrom', { when: formatDateTime(setup.sync.synced_at), ago: ago(setup.sync.synced_at) })}</span> : <span className="muted">{setup.connection ? t('tyre.sync.notYet') : t('tyre.connect.notConnected')}</span>}
        {setup.can_operate && setup.connection && setup.mapping && <div className="actions"><button type="button" disabled={syncing || setup.mapping_problems.length > 0} onClick={() => void syncNow()}><RefreshCw aria-hidden="true" />{syncing ? t('tyre.sync.syncing') : t('tyre.sync.now')}</button></div>}
      </div>
    </section>
    {notice && <p className={`notice ${notice.error ? 'errorNotice' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
    <Tabs value={tab} onChange={setTab} />
    <div role="tabpanel" id={`tyre-panel-${tab}`} aria-labelledby={`tyre-tab-${tab}`} className="tyrePanel">
      {tab === 'connection' && <TyreSetup setup={setup} onChange={(next) => { setSetup(next); setVersion((v) => v + 1); }} onSync={syncNow} syncing={syncing} />}
      {tab !== 'connection' && tab !== 'reports' && !synced && <NotReady setup={setup} openSetup={() => setTab('connection')} />}
      {synced && tab === 'dashboard' && <DashboardPanel key={version} onOpen={setTab} />}
      {synced && tab === 'forecasts' && <ForecastsPanel key={version} />}
      {synced && tab === 'plan' && <PlanPanel key={version} setup={setup} onSaved={(next) => { setSetup(next); setVersion((v) => v + 1); }} />}
      {synced && tab === 'alerts' && <AlertsPanel key={version} canOperate={setup.can_operate} />}
      {tab === 'reports' && <ReportsPanel setup={setup} synced={synced} />}
    </div>
  </div>;
}

function Tabs({ value, onChange }: { value: Tab; onChange: (tab: Tab) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const rtl = document.documentElement.dir === 'rtl';
    const step = event.key === 'ArrowRight' ? (rtl ? -1 : 1) : event.key === 'ArrowLeft' ? (rtl ? 1 : -1) : 0;
    let next = index;
    if (step) next = (index + step + TABS.length) % TABS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = TABS.length - 1;
    else return;
    event.preventDefault();
    onChange(TABS[next]);
    refs.current[next]?.focus();
  }
  return <div className="tyreTabs" role="tablist" aria-label={t('tyre.tabsLabel')}>
    {TABS.map((tab, index) => <button key={tab} ref={(node) => { refs.current[index] = node; }} type="button" role="tab" id={`tyre-tab-${tab}`} aria-controls={`tyre-panel-${tab}`} aria-selected={value === tab} tabIndex={value === tab ? 0 : -1} onClick={() => onChange(tab)} onKeyDown={(e) => onKey(e, index)}>{t(`tyre.tab.${tab}` as MessageKey)}</button>)}
  </div>;
}

function NotReady({ setup, openSetup }: { setup: Setup; openSetup: () => void }) {
  const key: MessageKey = !setup.connection ? (setup.can_manage ? 'tyre.empty.connectOwner' : 'tyre.empty.connectOther') : !setup.mapping ? (setup.can_manage ? 'tyre.empty.mapOwner' : 'tyre.empty.mapOther') : (setup.can_operate ? 'tyre.empty.syncOperator' : 'tyre.empty.syncViewer');
  return <section className="surface tyreCard tyreEmpty">
    <Plug aria-hidden="true" />
    <h2>{t('tyre.empty.title')}</h2>
    <p className="muted">{t(key)}</p>
    <div className="actions"><button type="button" onClick={openSetup}>{t('tyre.empty.open')}</button></div>
  </section>;
}

function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[]): { data: T | null; error: string; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fetcher().then((value) => { if (live) { setData(value); setError(''); } }).catch((e) => { if (live) setError(describeError(e)); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((value) => value + 1) };
}

function LoadState({ loading, error, reload, children }: { loading: boolean; error: string; reload: () => void; children: ReactNode }) {
  if (error) return <div className="notice errorNotice" role="alert"><AlertTriangle aria-hidden="true" /><span>{t('tyre.loadFailed', { reason: error })}</span><button type="button" className="secondary" onClick={reload}>{t('common.retry')}</button></div>;
  if (loading) return <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('common.loading')}</p>;
  return <>{children}</>;
}

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: string }) {
  return <div className={`metric tyreMetric ${tone ?? ''}`}><span className="metricTop">{label}</span><strong dir="auto">{value}</strong>{hint && <small>{hint}</small>}</div>;
}

// ---- dashboard ----

function DashboardPanel({ onOpen }: { onOpen: (tab: Tab) => void }) {
  const { data, error, loading, reload } = useLoad<Dashboard>(() => tyreApi.getDashboard(), []);
  return <LoadState loading={loading} error={error} reload={reload}>{data && <div className="tyreStack">
    <div className="tyreMetrics">
      <Metric label={t('tyre.kpi.vehicles')} value={num(data.summary.vehicles, 0)} hint={t('tyre.kpi.fitted', { count: data.summary.tyres_fitted })} />
      <Metric label={t('tyre.kpi.avgTread')} value={`${num(data.summary.average_tread_mm)} mm`} hint={t('tyre.kpi.measured', { count: data.summary.tyres_with_readings })} />
      <Metric label={t('tyre.kpi.belowMin', { mm: data.summary.min_tread_mm })} value={num(data.summary.below_minimum, 0)} hint={t('tyre.kpi.warningBand', { count: data.summary.in_warning_band })} tone={data.summary.below_minimum ? 'danger' : ''} />
      <Metric label={t('tyre.kpi.pressure')} value={data.summary.pressure_compliant_pct === null ? '-' : `${num(data.summary.pressure_compliant_pct)}%`} hint={t('tyre.kpi.pressureHint', { under: data.summary.under_inflated, over: data.summary.over_inflated })} />
      <Metric label={t('tyre.kpi.overdue')} value={num(data.summary.overdue_inspections, 0)} tone={data.summary.overdue_inspections ? 'warn' : ''} />
      <Metric label={t('tyre.kpi.due30')} value={num(data.summary.due_next_30_days, 0)} hint={t('tyre.kpi.due90', { count: data.summary.due_next_90_days })} />
      <Metric label={t('tyre.kpi.cpk', { currency: data.summary.currency })} value={num(data.summary.cost_per_1000_km, 2)} hint={data.summary.cost_per_hour !== null ? t('tyre.kpi.cph', { value: num(data.summary.cost_per_hour, 2) }) : undefined} />
      <Metric label={t('tyre.kpi.alerts')} value={num((data.alert_counts.critical ?? 0) + (data.alert_counts.warning ?? 0) + (data.alert_counts.info ?? 0), 0)} hint={t('tyre.kpi.critical', { count: data.alert_counts.critical ?? 0 })} tone={data.alert_counts.critical ? 'danger' : ''} />
    </div>
    <div className="tyreColumns">
      <section className="surface tyreCard" aria-labelledby="tyre-due-title">
        <div className="sectionHeader"><h2 id="tyre-due-title"><TrendingDown aria-hidden="true" />{t('tyre.dash.dueSoon')}</h2><div className="actions"><button type="button" className="secondary" onClick={() => onOpen('forecasts')}>{t('tyre.dash.allForecasts')}</button></div></div>
        {data.due_soon.length ? <ul className="tyreList">{data.due_soon.map((item) => <li key={item.tyre_id}>
          <div><strong dir="auto">{item.serial ?? item.tyre_id}</strong><small dir="auto">{[item.vehicle_label ?? item.vehicle_id, item.position, item.size].filter(Boolean).join(' · ')}</small></div>
          <span className={`statusBadge ${forecastTone(item, today())}`}>{item.state === 'due' ? t('tyre.forecast.dueNow') : day(item.projected_removal)}</span>
        </li>)}</ul> : <p className="muted">{t('tyre.dash.noneDue')}</p>}
      </section>
      <section className="surface tyreCard" aria-labelledby="tyre-alerts-title">
        <div className="sectionHeader"><h2 id="tyre-alerts-title"><Bell aria-hidden="true" />{t('tyre.dash.topAlerts')}</h2><div className="actions"><button type="button" className="secondary" onClick={() => onOpen('alerts')}>{t('tyre.dash.allAlerts')}</button></div></div>
        {data.top_alerts.length ? <ul className="tyreList">{data.top_alerts.map((alert) => <li key={alert.id}>
          <div><strong dir="auto">{alert.subject_label}</strong><small dir="auto">{alert.message}</small></div>
          <span className={`statusBadge ${severityTone(alert.severity)}`}>{t(`tyre.severity.${alert.severity}` as MessageKey)}</span>
        </li>)}</ul> : <p className="muted">{t('tyre.dash.noAlerts')}</p>}
      </section>
    </div>
    <section className="surface tyreCard" aria-labelledby="tyre-months-title">
      <h2 id="tyre-months-title"><CalendarClock aria-hidden="true" />{t('tyre.dash.byMonth')}</h2>
      {Object.keys(data.due_by_month).length ? <ul className="tyreBars">{monthBars(data.due_by_month).map((bar) => <li key={bar.month}>
        <span>{monthLabel(bar.month)}</span><span className="tyreBar" aria-hidden="true"><span style={{ inlineSize: `${bar.share}%` }} /></span><strong>{t('tyre.dash.tyresCount', { count: bar.count })}</strong>
      </li>)}</ul> : <p className="muted">{t('tyre.dash.noneYear')}</p>}
    </section>
  </div>}</LoadState>;
}

// ---- forecasts ----

function ForecastsPanel() {
  const [filters, setFilters] = useState({ q: '', month: '', confidence: '', state: '' });
  const [applied, setApplied] = useState(filters);
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad<ForecastPage>(() => tyreApi.getForecasts({ ...applied, limit: PAGE, offset }), [applied, offset]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const ids = { q: useId(), month: useId(), confidence: useId(), state: useId() };
  function submit(event: FormEvent) { event.preventDefault(); setOffset(0); setApplied(filters); }
  async function csv() {
    setBusy(true); setProblem('');
    const params = new URLSearchParams();
    if (applied.month) params.set('month', applied.month);
    if (applied.q) params.set('q', applied.q);
    try { await tyreApi.download(`/forecasts.csv?${params.toString()}`, 'tyre-forecast.csv'); } catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  }
  return <div className="tyreStack">
    <form className="surface tyreCard tyreFilters" onSubmit={submit} role="search" aria-label={t('tyre.forecast.filters')}>
      <label className="field" htmlFor={ids.q}><span>{t('common.search')}</span><input id={ids.q} type="search" placeholder={t('tyre.forecast.searchHint')} value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} /></label>
      <label className="field" htmlFor={ids.month}><span>{t('tyre.forecast.month')}</span><input id={ids.month} type="month" value={filters.month} onChange={(e) => setFilters({ ...filters, month: e.target.value })} /></label>
      <label className="field" htmlFor={ids.confidence}><span>{t('tyre.forecast.confidence')}</span><select id={ids.confidence} value={filters.confidence} onChange={(e) => setFilters({ ...filters, confidence: e.target.value })}><option value="">{t('tyre.common.any')}</option>{(['high', 'medium', 'low'] as const).map((value) => <option key={value} value={value}>{t(`tyre.confidence.${value}` as MessageKey)}</option>)}</select></label>
      <label className="field" htmlFor={ids.state}><span>{t('tyre.forecast.state')}</span><select id={ids.state} value={filters.state} onChange={(e) => setFilters({ ...filters, state: e.target.value })}><option value="">{t('tyre.common.any')}</option>{(['due', 'projected', 'no_wear', 'no_data'] as const).map((value) => <option key={value} value={value}>{t(`tyre.state.${value}` as MessageKey)}</option>)}</select></label>
      <div className="actions"><button type="submit"><Search aria-hidden="true" />{t('common.search')}</button><button type="button" className="secondary" disabled={busy} onClick={() => void csv()}><Download aria-hidden="true" />{t('tyre.common.csv')}</button></div>
    </form>
    {problem && <p className="notice errorNotice" role="alert">{problem}</p>}
    <LoadState loading={loading} error={error} reload={reload}>{data && (data.total ? <>
      <div className="tyreTableWrap" tabIndex={0} role="region" aria-label={t('tyre.forecast.tableTitle')}>
        <table className="tyreTable">
          <caption>{t('tyre.forecast.caption', { mm: data.min_tread_mm })}</caption>
          <thead><tr>
            <th scope="col">{t('tyre.col.tyre')}</th><th scope="col">{t('tyre.col.vehicle')}</th><th scope="col">{t('tyre.col.size')}</th>
            <th scope="col">{t('tyre.col.tread')}</th><th scope="col">{t('tyre.col.wear')}</th><th scope="col">{t('tyre.col.removal')}</th>
            <th scope="col">{t('tyre.col.range')}</th><th scope="col">{t('tyre.col.confidence')}</th>
          </tr></thead>
          <tbody>{data.items.map((item) => <ForecastRow key={item.tyre_id} item={item} />)}</tbody>
        </table>
      </div>
      <Pager page={data} onOffset={setOffset} />
    </> : <p className="notice" role="status"><Info aria-hidden="true" />{t('tyre.forecast.none')}</p>)}</LoadState>
    <Method />
  </div>;
}

function ForecastRow({ item }: { item: TyreForecast }) {
  return <tr>
    <th scope="row"><span dir="auto">{item.serial ?? item.tyre_id}</span><small dir="auto">{item.brand}</small></th>
    <td dir="auto">{[item.vehicle_label ?? item.vehicle_id, item.position].filter(Boolean).join(' · ') || '-'}</td>
    <td dir="ltr">{item.size ?? '-'}</td>
    <td dir="auto">{item.current_tread_mm === null ? '-' : `${num(item.current_tread_mm)} mm`}</td>
    <td>{item.wear_mm_per_30_days === null ? '-' : t('tyre.forecast.wear', { value: num(item.wear_mm_per_30_days, 2) })}{item.wear_mm_per_1000km !== null && <small>{t('tyre.forecast.wearKm', { value: num(item.wear_mm_per_1000km, 2) })}</small>}</td>
    <td><span className={`statusBadge ${forecastTone(item, today())}`}>{item.state === 'due' ? t('tyre.forecast.dueNow') : item.projected_removal ? day(item.projected_removal) : t(`tyre.state.${item.state}` as MessageKey)}</span></td>
    <td>{item.earliest_removal ? t('tyre.forecast.window', { from: day(item.earliest_removal), to: item.latest_removal ? day(item.latest_removal) : t('tyre.forecast.later') }) : '-'}{item.note && <small>{item.note}</small>}</td>
    <td><span className={`statusBadge ${confidenceTone(item.confidence)}`}>{t(`tyre.confidence.${item.confidence}` as MessageKey)}</span><small>{t(`tyre.basis.${item.basis}` as MessageKey)}</small></td>
  </tr>;
}

function Method() {
  return <details className="surface tyreCard tyreMethod">
    <summary><Info aria-hidden="true" />{t('tyre.method.title')}</summary>
    <p>{t('tyre.method.wear')}</p>
    <p>{t('tyre.method.removal')}</p>
    <p>{t('tyre.method.confidence')}</p>
    <p>{t('tyre.method.fallback')}</p>
    <p>{t('tyre.method.plan')}</p>
  </details>;
}

function Pager({ page, onOffset }: { page: Page<unknown>; onOffset: (offset: number) => void }) {
  return <nav className="tyrePager" aria-label={t('tyre.common.pages')}>
    <span role="status">{pageRange(page)}</span>
    <div className="actions">
      <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>{t('tyre.common.previous')}</button>
      <button type="button" className="secondary" disabled={page.offset + page.items.length >= page.total} onClick={() => onOffset(page.offset + page.limit)}>{t('tyre.common.next')}</button>
    </div>
  </nav>;
}

// ---- purchase plan and assumptions ----

function PlanPanel({ setup, onSaved }: { setup: Setup; onSaved: (setup: Setup) => void }) {
  const [months, setMonths] = useState(setup.assumptions.horizon_months);
  const { data, error, loading, reload } = useLoad<PurchasePlan>(() => tyreApi.getPlan(months), [months]);
  const [problem, setProblem] = useState('');
  const id = useId();
  async function csv() {
    setProblem('');
    try { await tyreApi.download(`/purchase-plan.csv?months=${months}`, 'tyre-purchase-plan.csv'); } catch (e) { setProblem(describeError(e)); }
  }
  return <div className="tyreStack">
    <div className="surface tyreCard tyreFilters">
      <label className="field" htmlFor={id}><span>{t('tyre.plan.horizon')}</span><select id={id} value={months} onChange={(e) => setMonths(Number(e.target.value))}>{[3, 6, 12, 18, 24, 36].map((value) => <option key={value} value={value}>{t('tyre.plan.months', { count: value })}</option>)}</select></label>
      <div className="actions"><button type="button" className="secondary" onClick={() => void csv()}><Download aria-hidden="true" />{t('tyre.common.csv')}</button></div>
    </div>
    {problem && <p className="notice errorNotice" role="alert">{problem}</p>}
    <LoadState loading={loading} error={error} reload={reload}>{data && <>
      <div className="tyreMetrics three">
        <Metric label={t('tyre.plan.toBuy')} value={num(data.total_tyres, 0)} />
        <Metric label={t('tyre.plan.cost', { currency: data.currency })} value={data.total_cost === null ? '-' : num(data.total_cost, 0)} hint={data.unpriced_rows ? t('tyre.plan.unpriced', { count: data.unpriced_rows }) : undefined} tone={data.unpriced_rows ? 'warn' : ''} />
        <Metric label={t('tyre.plan.skipped')} value={num(data.skipped_no_size, 0)} hint={t('tyre.plan.skippedHint')} />
      </div>
      {data.rows.length ? <div className="tyreTableWrap" tabIndex={0} role="region" aria-label={t('tyre.plan.tableTitle')}>
        <table className="tyreTable">
          <caption>{t('tyre.plan.caption', { safety: data.assumptions.safety_stock_pct })}</caption>
          <thead><tr>
            <th scope="col">{t('tyre.col.month')}</th><th scope="col">{t('tyre.col.size')}</th><th scope="col">{t('tyre.col.brand')}</th><th scope="col">{t('tyre.col.due')}</th>
            <th scope="col">{t('tyre.col.safety')}</th><th scope="col">{t('tyre.col.fromStock')}</th><th scope="col">{t('tyre.col.toBuy')}</th><th scope="col">{t('tyre.col.unitPrice')}</th><th scope="col">{t('tyre.col.cost')}</th>
          </tr></thead>
          <tbody>{data.rows.map((row) => <tr key={`${row.month}|${row.size}|${row.brand}`}>
            <th scope="row">{monthLabel(row.month)}</th><td dir="ltr">{row.size}</td><td dir="auto">{row.brand}</td>
            <td>{row.due}{row.low_confidence > 0 && <small>{t('tyre.plan.lowConfidence', { count: row.low_confidence })}</small>}</td>
            <td>{row.safety}</td><td>{row.from_stock}</td><td><strong>{row.to_buy}</strong></td>
            <td>{row.unit_price === null ? t('tyre.common.noPrice') : num(row.unit_price, 0)}<small>{t(`tyre.priceSource.${row.price_source}` as MessageKey)}</small></td>
            <td>{money(row.estimated_cost, data.currency)}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="notice" role="status"><Info aria-hidden="true" />{t('tyre.plan.none')}</p>}
    </>}</LoadState>
    <AssumptionsCard setup={setup} onSaved={onSaved} />
  </div>;
}

function AssumptionsCard({ setup, onSaved }: { setup: Setup; onSaved: (setup: Setup) => void }) {
  const [value, setValue] = useState<Assumptions>(setup.assumptions);
  const [prices, setPrices] = useState(formatPrices(setup.assumptions.unit_prices));
  const [brands, setBrands] = useState(Object.entries(setup.assumptions.preferred_brands).map(([size, brand]) => `${size} = ${brand}`).join('\n'));
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const disabled = !setup.can_manage || busy;
  const field = (key: keyof Assumptions, label: MessageKey, step = 'any') => <label className="field" key={key}><span>{t(label)}</span>
    <input type="number" step={step} disabled={disabled} value={value[key] === null ? '' : String(value[key])} onChange={(e) => setValue({ ...value, [key]: e.target.value === '' ? null : Number(e.target.value) })} /></label>;
  async function save(event: FormEvent) {
    event.preventDefault();
    const parsed = parsePrices(prices);
    if (parsed.bad.length) { setMessage({ text: t('tyre.assume.badPrices', { lines: parsed.bad.join('; ') }), error: true }); return; }
    const preferred: Record<string, string> = {};
    for (const line of brands.split('\n')) { const [size, brand] = line.split('=').map((part) => part.trim()); if (size && brand) preferred[size] = brand; }
    setBusy(true); setMessage(null);
    try {
      const next = await tyreApi.saveAssumptions({ ...value, unit_prices: parsed.prices, preferred_brands: preferred });
      onSaved(next);
      setMessage({ text: t('tyre.assume.saved'), error: false });
    } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  }
  return <form className="surface tyreCard" onSubmit={(e) => void save(e)} aria-labelledby="tyre-assume-title">
    <h2 id="tyre-assume-title"><Gauge aria-hidden="true" />{t('tyre.assume.title')}</h2>
    <p className="muted">{setup.can_manage ? t('tyre.assume.intro') : t('tyre.assume.ownerOnly')}</p>
    <div className="tyreFormGrid">
      {field('min_tread_mm', 'tyre.assume.minTread', '0.1')}
      {field('warning_band_mm', 'tyre.assume.warningBand', '0.1')}
      {field('pressure_tolerance_pct', 'tyre.assume.tolerance', '1')}
      {field('default_target_pressure_psi', 'tyre.assume.target', '1')}
      {field('inspection_interval_days', 'tyre.assume.interval', '1')}
      {field('abnormal_wear_factor', 'tyre.assume.wearFactor', '0.1')}
      {field('cost_anomaly_factor', 'tyre.assume.costFactor', '0.1')}
      {field('default_new_tread_mm', 'tyre.assume.newTread', '0.1')}
      {field('horizon_months', 'tyre.assume.horizon', '1')}
      {field('safety_stock_pct', 'tyre.assume.safety', '1')}
    </div>
    <label className="tyreCheck"><input type="checkbox" disabled={disabled} checked={value.include_low_confidence} onChange={(e) => setValue({ ...value, include_low_confidence: e.target.checked })} />{t('tyre.assume.includeLow')}</label>
    <label className="tyreCheck"><input type="checkbox" disabled={disabled} checked={value.subtract_stock} onChange={(e) => setValue({ ...value, subtract_stock: e.target.checked })} />{t('tyre.assume.subtractStock')}</label>
    <div className="tyreFormGrid">
      <label className="field"><span>{t('tyre.assume.prices')}</span><textarea dir="ltr" disabled={disabled} placeholder="315/80R22.5|brand = 1200" value={prices} onChange={(e) => setPrices(e.target.value)} /></label>
      <label className="field"><span>{t('tyre.assume.brands')}</span><textarea dir="ltr" disabled={disabled} placeholder="315/80R22.5 = brand" value={brands} onChange={(e) => setBrands(e.target.value)} /></label>
    </div>
    {setup.can_manage && <div className="actions"><button type="submit" disabled={disabled}><Save aria-hidden="true" />{busy ? t('tyre.common.saving') : t('tyre.assume.save')}</button></div>}
    {message && <p className={`notice ${message.error ? 'errorNotice' : ''}`} role={message.error ? 'alert' : 'status'}>{message.text}</p>}
  </form>;
}

// ---- alerts ----

function readSeen(): string[] {
  try { return JSON.parse(localStorage.getItem(SEEN_KEY) ?? '[]') as string[]; } catch { return []; }
}

function AlertsPanel({ canOperate }: { canOperate: boolean }) {
  const [status, setStatus] = useState('active');
  const [kind, setKind] = useState('');
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad<AlertPage>(() => tyreApi.getAlerts({ status, kind, q: applied, limit: PAGE, offset }), [status, kind, applied, offset]);
  const [items, setItems] = useState<TyreAlert[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [permission, setPermission] = useState(() => (typeof Notification === 'undefined' ? 'unsupported' : Notification.permission));
  const ids = { status: useId(), kind: useId(), q: useId() };
  useEffect(() => { setItems(data?.items ?? []); }, [data]);

  // Desktop notifications for new critical and warning alerts, when the person asked for them
  // (Settings > Notifications: tyre alerts and desktop delivery) and the browser allows it.
  useEffect(() => {
    if (!data || permission !== 'granted') return;
    void api.getNotificationPreferences().then((prefs) => {
      const { fresh, seen } = newAlerts(data.items, readSeen());
      try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch { /* private mode */ }
      if (!prefs.desktop_enabled || prefs.tyre_alerts === false) return;
      for (const alert of fresh.slice(0, 3)) new Notification(t('tyre.alerts.notifyTitle', { kind: alertKindLabel(alert.kind) }), { body: `${alert.subject_label}: ${alert.message}`, tag: alert.id });
    }).catch(() => undefined);
  }, [data, permission]);

  async function act(alert: TyreAlert, action: 'ack' | 'reopen') {
    setBusy(alert.id); setMessage('');
    try {
      const next = action === 'ack' ? await tyreApi.acknowledgeAlert(alert.id) : await tyreApi.reopenAlert(alert.id);
      setItems((current) => current.map((item) => item.id === next.id ? next : item));
    } catch (e) { setMessage(describeError(e)); } finally { setBusy(null); }
  }
  return <div className="tyreStack">
    <form className="surface tyreCard tyreFilters" role="search" aria-label={t('tyre.alerts.filters')} onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied(query); }}>
      <label className="field" htmlFor={ids.status}><span>{t('tyre.alerts.status')}</span><select id={ids.status} value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value); }}>{(['active', 'open', 'acknowledged', 'resolved'] as const).map((value) => <option key={value} value={value}>{t(`tyre.alertStatus.${value}` as MessageKey)}</option>)}</select></label>
      <label className="field" htmlFor={ids.kind}><span>{t('tyre.alerts.kind')}</span><select id={ids.kind} value={kind} onChange={(e) => { setOffset(0); setKind(e.target.value); }}><option value="">{t('tyre.common.any')}</option>{ALERT_KINDS.map((value) => <option key={value} value={value}>{alertKindLabel(value)}</option>)}</select></label>
      <label className="field" htmlFor={ids.q}><span>{t('common.search')}</span><input id={ids.q} type="search" placeholder={t('tyre.alerts.searchHint')} value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      <div className="actions">
        <button type="submit"><Search aria-hidden="true" />{t('common.search')}</button>
        {permission === 'default' && <button type="button" className="secondary" onClick={() => void Notification.requestPermission().then(setPermission)}><BellRing aria-hidden="true" />{t('tyre.alerts.enableDesktop')}</button>}
      </div>
    </form>
    {!canOperate && <p className="muted">{t('tyre.alerts.viewerNote')}</p>}
    {message && <p className="notice errorNotice" role="alert">{message}</p>}
    <LoadState loading={loading} error={error} reload={reload}>{data && (items.length ? <>
      <ul className="tyreAlertList">{items.map((alert) => <li key={alert.id} className={`surface tyreAlert ${alert.severity}`}>
        <div className="tyreAlertHead">
          <span className={`statusBadge ${severityTone(alert.severity)}`}>{t(`tyre.severity.${alert.severity}` as MessageKey)}</span>
          <strong>{alertKindLabel(alert.kind)}</strong>
          <span className="statusBadge neutral">{t(`tyre.alertStatus.${alert.status}` as MessageKey)}</span>
        </div>
        <p dir="auto"><strong>{alert.subject_label}</strong>: {alert.message}</p>
        <small className="muted">{t('tyre.alerts.seen', { first: formatDateTime(alert.first_seen_at), last: formatDateTime(alert.last_seen_at) })}{alert.acknowledged_by ? ` · ${t('tyre.alerts.ackBy', { who: alert.acknowledged_by })}` : ''}</small>
        {canOperate && alert.status !== 'resolved' && <div className="actions">
          {alert.status === 'open'
            ? <button type="button" disabled={busy === alert.id} onClick={() => void act(alert, 'ack')}><CheckCircle2 aria-hidden="true" />{t('tyre.alerts.acknowledge')}</button>
            : <button type="button" className="secondary" disabled={busy === alert.id} onClick={() => void act(alert, 'reopen')}>{t('tyre.alerts.reopen')}</button>}
        </div>}
      </li>)}</ul>
      <Pager page={{ ...data, items }} onOffset={setOffset} />
    </> : <p className="notice" role="status"><CheckCircle2 aria-hidden="true" />{status === 'active' && !applied && !kind ? t('tyre.alerts.noneActive') : t('tyre.alerts.noneMatch')}</p>)}</LoadState>
  </div>;
}

// ---- reports ----

const REPORT_PAGE = 10;

function ReportsPanel({ setup, synced }: { setup: Setup; synced: boolean }) {
  const [offset, setOffset] = useState(0);
  const [tick, setTick] = useState(0);
  const { data, error, loading, reload } = useLoad<Page<ReportSummary>>(() => tyreApi.listReports(REPORT_PAGE, offset), [offset, tick]);
  const [kind, setKind] = useState<ReportSummary['kind']>('fleet_health');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const ids = { kind: useId(), from: useId(), to: useId() };
  async function act(name: string, work: () => Promise<void>) {
    setBusy(name); setMessage(null);
    try { await work(); } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }
  const generate = (event: FormEvent) => { event.preventDefault(); void act('generate', async () => {
    if (from && to && from > to) { setMessage({ text: t('tyre.reports.badPeriod'), error: true }); return; }
    await tyreApi.createReport(kind, from, to); setOffset(0); setTick((v) => v + 1);
    setMessage({ text: t('tyre.reports.generated'), error: false });
  }); };
  return <div className="tyreStack">
    {setup.can_operate ? (synced ? <form className="surface tyreCard tyreFilters" onSubmit={generate} aria-labelledby="tyre-generate-title">
      <h2 id="tyre-generate-title" className="tyreFull"><FileText aria-hidden="true" />{t('tyre.reports.generate')}</h2>
      <label className="field" htmlFor={ids.kind}><span>{t('tyre.reports.kind')}</span><select id={ids.kind} value={kind} onChange={(e) => setKind(e.target.value as ReportSummary['kind'])}>{REPORT_KINDS.map((value) => <option key={value} value={value}>{reportKindLabel(value)}</option>)}</select></label>
      <label className="field" htmlFor={ids.from}><span>{t('tyre.reports.from')}</span><input id={ids.from} type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
      <label className="field" htmlFor={ids.to}><span>{t('tyre.reports.to')}</span><input id={ids.to} type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={busy !== null}><FileText aria-hidden="true" />{busy === 'generate' ? t('tyre.reports.generating') : t('tyre.reports.generateButton')}</button></div>
      <p className="muted tyreFull">{t('tyre.reports.periodHelp')}</p>
    </form> : <p className="notice" role="status"><Info aria-hidden="true" />{t('tyre.reports.syncFirst')}</p>) : <p className="muted">{t('tyre.reports.viewerNote')}</p>}
    {message && <p className={`notice ${message.error ? 'errorNotice' : ''}`} role={message.error ? 'alert' : 'status'}>{message.text}</p>}
    <section aria-labelledby="tyre-report-list"><h2 id="tyre-report-list" className="tyreSubhead">{t('tyre.reports.list')}</h2>
      <LoadState loading={loading} error={error} reload={reload}>{data && (data.items.length ? <>
        <ul className="tyreReportList">{data.items.map((report) => <ReportCard key={report.id} report={report} setup={setup} onChanged={() => setTick((v) => v + 1)} />)}</ul>
        <Pager page={data} onOffset={setOffset} />
      </> : <p className="notice" role="status"><Info aria-hidden="true" />{setup.can_operate ? t('tyre.reports.noneOperator') : t('tyre.reports.noneViewer')}</p>)}</LoadState>
    </section>
    <SchedulesCard setup={setup} />
  </div>;
}

function ReportCard({ report, setup, onChanged }: { report: ReportSummary; setup: Setup; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [sending, setSending] = useState(false);
  const [channel, setChannel] = useState(setup.delivery_channels[0] ?? '');
  const [to, setTo] = useState('');
  const [format, setFormat] = useState<'pdf' | 'csv'>('pdf');
  const ids = { channel: useId(), to: useId(), format: useId() };
  async function act(name: string, work: () => Promise<void>) {
    setBusy(name); setMessage(null);
    try { await work(); } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }
  const exportAs = (fmt: 'pdf' | 'csv') => act(fmt, () => tyreApi.download(`/reports/${encodeURIComponent(report.id)}/export?format=${fmt}`, `tyre-report.${fmt}`));
  const remove = () => act('delete', async () => { if (!confirm(t('tyre.reports.confirmDelete', { title: report.title }))) return; await tyreApi.deleteReport(report.id); onChanged(); });
  const send = (event: FormEvent) => { event.preventDefault(); void act('send', async () => {
    if (!isRecipient(to)) { setMessage({ text: t('tyre.reports.badRecipient'), error: true }); return; }
    const result = await tyreApi.deliverReport(report.id, channel, to.trim(), format);
    setMessage({ text: result.status === 'waiting_approval' ? t('tyre.reports.waitingApproval') : result.message, error: result.status === 'blocked' || result.status === 'failed' });
    setSending(false); onChanged();
  }); };
  return <li className="surface tyreCard tyreReport">
    <div className="sectionHeader">
      <div><h3>{reportKindLabel(report.kind)}</h3><small className="muted">{t('tyre.reports.meta', { from: day(report.period_from), to: day(report.period_to), when: formatDateTime(report.created_at), who: report.created_by })}{report.source === 'schedule' ? ` · ${t('tyre.reports.scheduled')}` : ''}</small></div>
      <div className="actions tyreInlineActions">
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportAs('pdf')}><Download aria-hidden="true" />PDF</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportAs('csv')}><Download aria-hidden="true" />CSV</button>
        {setup.can_operate && <button type="button" className="secondary" disabled={busy !== null} aria-expanded={sending} onClick={() => setSending(!sending)}><Send aria-hidden="true" />{t('tyre.reports.send')}</button>}
        {setup.can_operate && <button type="button" className="danger iconOnly" disabled={busy !== null} aria-label={t('tyre.reports.delete', { title: report.title })} onClick={() => void remove()}><Trash2 aria-hidden="true" /></button>}
      </div>
    </div>
    {report.highlights.length > 0 && <dl className="tyreHighlights">{report.highlights.slice(0, 6).map((item) => <div key={item.label}><dt dir="auto">{item.label}</dt><dd dir="auto">{item.value === null ? '-' : String(item.value)}</dd></div>)}</dl>}
    {report.deliveries.length > 0 && <ul className="tyreDeliveries">{report.deliveries.slice(-3).map((delivery, index) => <li key={index}><span className="statusBadge neutral">{t(`tyre.deliveryStatus.${['waiting_approval', 'sent', 'failed', 'blocked', 'unavailable'].includes(delivery.status) ? delivery.status : 'failed'}` as MessageKey)}</span><span dir="auto">{delivery.channel} → {delivery.to}</span>{delivery.message && <small>{delivery.message}</small>}</li>)}</ul>}
    {sending && (setup.delivery_channels.length ? <form className="tyreSend" onSubmit={send}>
      <label className="field" htmlFor={ids.channel}><span>{t('tyre.reports.channel')}</span><select id={ids.channel} value={channel} onChange={(e) => setChannel(e.target.value)}>{setup.delivery_channels.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
      <label className="field" htmlFor={ids.to}><span>{t('tyre.reports.to')}</span><input id={ids.to} dir="ltr" value={to} placeholder="fleet@example.com / +9665..." onChange={(e) => setTo(e.target.value)} /></label>
      <label className="field" htmlFor={ids.format}><span>{t('tyre.reports.format')}</span><select id={ids.format} value={format} onChange={(e) => setFormat(e.target.value as 'pdf')}><option value="pdf">PDF</option><option value="csv">CSV</option></select></label>
      <div className="actions"><button type="submit" disabled={busy !== null}><Send aria-hidden="true" />{t('tyre.reports.requestSend')}</button></div>
      <p className="muted tyreFull">{t('tyre.reports.approvalNote')}</p>
    </form> : <p className="notice" role="status"><Info aria-hidden="true" />{t('tyre.reports.noChannels')}</p>)}
    {message && <p className={`notice ${message.error ? 'errorNotice' : ''}`} role={message.error ? 'alert' : 'status'}>{message.text}</p>}
  </li>;
}

function SchedulesCard({ setup }: { setup: Setup }) {
  const [tick, setTick] = useState(0);
  const { data, error, loading, reload } = useLoad<ReportSchedule[]>(() => tyreApi.listSchedules(), [tick]);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Riyadh';
  const [draft, setDraft] = useState<ScheduleDraft>({ kind: 'fleet_health', cadence: 'weekly', weekday: 0, time: '07:00', timezone: zone, channel: '', to: '', format: 'pdf' });
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const ids = { kind: useId(), cadence: useId(), weekday: useId(), time: useId(), zone: useId(), channel: useId(), to: useId() };
  async function create(event: FormEvent) {
    event.preventDefault();
    if (draft.channel && !isRecipient(draft.to)) { setMessage({ text: t('tyre.reports.badRecipient'), error: true }); return; }
    setBusy(true); setMessage(null);
    try { await tyreApi.createSchedule(draft); setTick((v) => v + 1); setMessage({ text: t('tyre.schedule.created'), error: false }); }
    catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  }
  async function remove(schedule: ReportSchedule) {
    if (!confirm(t('tyre.schedule.confirmDelete', { what: schedule.description }))) return;
    try { await tyreApi.deleteSchedule(schedule.id); setTick((v) => v + 1); } catch (e) { setMessage({ text: describeError(e), error: true }); }
  }
  return <section className="surface tyreCard" aria-labelledby="tyre-schedules-title">
    <h2 id="tyre-schedules-title"><CalendarClock aria-hidden="true" />{t('tyre.schedule.title')}</h2>
    <p className="muted">{t('tyre.schedule.intro')}</p>
    <LoadState loading={loading} error={error} reload={reload}>{data && (data.length ? <ul className="tyreList">{data.map((schedule) => <li key={schedule.id}>
      <div><strong>{reportKindLabel(schedule.kind)}</strong><small>{schedule.description} ({schedule.timezone}){schedule.next_run_at ? ` · ${t('tyre.schedule.next', { when: formatDateTime(schedule.next_run_at) })}` : ''}{schedule.delivery ? ` · ${schedule.delivery.channel} → ${schedule.delivery.to}` : ''}</small></div>
      {setup.can_operate && <button type="button" className="danger iconOnly" aria-label={t('tyre.schedule.delete', { what: schedule.description })} onClick={() => void remove(schedule)}><Trash2 aria-hidden="true" /></button>}
    </li>)}</ul> : <p className="muted">{t('tyre.schedule.none')}</p>)}</LoadState>
    {setup.can_operate && <form className="tyreFilters tyreScheduleForm" onSubmit={(e) => void create(e)}>
      <label className="field" htmlFor={ids.kind}><span>{t('tyre.reports.kind')}</span><select id={ids.kind} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as ScheduleDraft['kind'] })}>{REPORT_KINDS.map((value) => <option key={value} value={value}>{reportKindLabel(value)}</option>)}</select></label>
      <label className="field" htmlFor={ids.cadence}><span>{t('tyre.schedule.cadence')}</span><select id={ids.cadence} value={draft.cadence} onChange={(e) => setDraft({ ...draft, cadence: e.target.value as ScheduleDraft['cadence'] })}>{(['daily', 'weekly', 'monthly'] as const).map((value) => <option key={value} value={value}>{t(`tyre.cadence.${value}` as MessageKey)}</option>)}</select></label>
      {draft.cadence === 'weekly' && <label className="field" htmlFor={ids.weekday}><span>{t('tyre.schedule.weekday')}</span><select id={ids.weekday} value={draft.weekday} onChange={(e) => setDraft({ ...draft, weekday: Number(e.target.value) })}>{[0, 1, 2, 3, 4, 5, 6].map((value) => <option key={value} value={value}>{formatDateTime(new Date(Date.UTC(2026, 0, 4 + value, 12)), { weekday: 'long' })}</option>)}</select></label>}
      <label className="field" htmlFor={ids.time}><span>{t('tyre.schedule.time')}</span><input id={ids.time} type="time" value={draft.time} onChange={(e) => setDraft({ ...draft, time: e.target.value })} /></label>
      <label className="field" htmlFor={ids.zone}><span>{t('tyre.schedule.timezone')}</span><input id={ids.zone} dir="ltr" value={draft.timezone} onChange={(e) => setDraft({ ...draft, timezone: e.target.value })} /></label>
      {setup.delivery_channels.length > 0 && <>
        <label className="field" htmlFor={ids.channel}><span>{t('tyre.schedule.sendBy')}</span><select id={ids.channel} value={draft.channel} onChange={(e) => setDraft({ ...draft, channel: e.target.value })}><option value="">{t('tyre.schedule.dontSend')}</option>{setup.delivery_channels.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        {draft.channel && <label className="field" htmlFor={ids.to}><span>{t('tyre.reports.to')}</span><input id={ids.to} dir="ltr" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} /></label>}
      </>}
      <div className="actions"><button type="submit" disabled={busy}><CalendarClock aria-hidden="true" />{t('tyre.schedule.create')}</button></div>
      {!setup.delivery_channels.length && <p className="muted tyreFull">{t('tyre.schedule.noChannels')}</p>}
    </form>}
    {message && <p className={`notice ${message.error ? 'errorNotice' : ''}`} role={message.error ? 'alert' : 'status'}>{message.text}</p>}
    {!setup.can_operate && <p className="muted">{t('tyre.reports.viewerNote')}</p>}
    <p className="muted"><Info aria-hidden="true" className="tyreInlineIcon" />{t('tyre.schedule.automationNote')}</p>
  </section>;
}
