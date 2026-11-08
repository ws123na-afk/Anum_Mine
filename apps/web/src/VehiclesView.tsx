// Vehicles, fuel and mileage (docs/vehicles.md#web-page). Overview (the month's fleet figures, the fleet table with
// search and filters beside the selected vehicle's panel, what is due and spend by month), Vehicles (search, archived,
// paging) and Settings (trip places, default vehicle, reminders, the iPhone Shortcut). A vehicle opens its own page: Fuel, Trips, Service, Odometer, Report and Details. Costs are links to the
// person's Finance payments, never new money: the page says so wherever a payment is shown.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { CalendarClock, CarFront, ChevronLeft, ChevronRight, Download, FileUp, Fuel, Gauge, Lock, Plus, Route, Search, ShieldCheck, Smartphone, Wallet, Wrench } from 'lucide-react';
import type { Vehicle, VehicleFuelEntry, VehicleOverview, VehiclePage, VehiclePreferences, VehicleRecordKind, VehicleReport, VehicleServiceRecord, VehicleTrip } from '@anum/contracts';
import * as api from './lib/vehiclesApi';
import {
  PAGE_SIZE, VEHICLE_TABS, dueLabel, dueText, hashForTab, km, litres, moneyList, paymentText, purposeLabel, serviceLabel, tabFromHash, typeLabel, vehicleName, type VehicleTab,
} from './lib/vehicles';
import { amountIn, previousMonthName, kmIn, kmPerLitre, mainCurrency, newestFirst, nextDue, papers, usageRows, vehicleStatus, type RecentRecord } from './lib/ops';
import { formatAmount, monthLabel } from './lib/finance';
import { delta, monthRange, monthsEnding, type Delta } from './lib/financeNav';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { viewLabel } from './lib/shell';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { type Crumb } from './PageParts';
import { FinCard, FinanceTabs, FinancePageHead, KpiCard, KpiRow, PeriodPicker, StatusChip, tabPanel } from './FinanceShell';
import { UsageChart } from './OpsParts';
import { RecordForm, VehicleDetail, VehicleForm } from './VehicleParts';
import './commerce.css';
import './vehicles.css';

function useVehicleTab(): [VehicleTab, (next: VehicleTab) => void] {
  const [tab, setTab] = useState<VehicleTab>(() => tabFromHash(typeof location === 'undefined' ? '' : location.hash));
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#vehicles')) setTab(tabFromHash(location.hash)); };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);
  const pick = (next: VehicleTab) => {
    setTab(next);
    if (location.hash !== hashForTab(next)) history.pushState(null, '', hashForTab(next));
  };
  return [tab, pick];
}

export function VehiclesDenied() {
  return <section className="surface cmPanel cmDenied" aria-labelledby="veh-denied"><Lock aria-hidden="true" /><div><h2 id="veh-denied">{t('veh.denied.title')}</h2><p className="muted">{t('veh.denied.text')}</p></div></section>;
}

export function VehiclesView() {
  const [tab, pick] = useVehicleTab();
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [tick, setTick] = useState(0);
  const [denied, setDenied] = useState(false);
  const thisMonth = currentMonth();
  const [month, setMonth] = useState(thisMonth);
  const refresh = () => setTick((n) => n + 1);
  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('vehicles'), href: '#vehicles', onClick: () => { setOpenId(null); pick('overview'); } }];
  if (!openId && tab !== 'overview') crumbs.push({ label: t(`veh.tab.${tab}` as MessageKey) });
  const tabs = VEHICLE_TABS.map((id) => ({ id, label: t(`veh.tab.${id}` as MessageKey) }));
  const head = <FinancePageHead title={viewLabel('vehicles')} subtitle={t('veh.subtitle')} crumbs={openId || tab !== 'overview' ? crumbs : undefined}
    actions={denied ? undefined : <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('veh.add')}</button>}
    controls={!denied && !openId && tab === 'overview' ? <PeriodPicker month={month} onChange={setMonth} max={thisMonth} /> : undefined}
    tabs={denied || openId ? undefined : <FinanceTabs label={t('veh.tabs')} items={tabs} value={tab} onChange={pick} idPrefix="veh" />} />;
  const added = (vehicle: Vehicle) => { setAdding(false); refresh(); setOpenId(vehicle.id); };
  if (denied) return <div className="cmLayout vehLayout opsPage">{head}<VehiclesDenied /></div>;
  return <div className="cmLayout vehLayout opsPage">
    {head}
    {adding && <VehicleForm onClose={() => setAdding(false)} onSaved={added} />}
    {openId ? <VehicleDetail key={`${openId}:${tick}`} vehicleId={openId} onBack={() => { setOpenId(null); refresh(); }} onDeleted={() => { setOpenId(null); refresh(); }} />
      : <div {...tabPanel('veh', tab)}>
        {tab === 'overview' && <OverviewPanel key={`${tick}:${month}`} month={month} onOpen={setOpenId} onAdd={() => setAdding(true)} onDenied={() => setDenied(true)} onList={() => pick('list')} />}
        {tab === 'list' && <ListPanel key={tick} onOpen={setOpenId} onAdd={() => setAdding(true)} onDenied={() => setDenied(true)} />}
        {tab === 'settings' && <SettingsPanel onDenied={() => setDenied(true)} />}
      </div>}
  </div>;
}

/** "YYYY-MM" for today, in local time (the month picker never goes past it). */
const currentMonth = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

// ---- Overview ------------------------------------------------------------------------------------------

interface OverviewData { months: VehicleOverview; month: VehicleOverview; vehicles: VehiclePage<Vehicle> }
type StatusFilter = '' | 'due' | 'ok';
const FLEET_LIMIT = 100;

function OverviewPanel({ month, onOpen, onAdd, onDenied, onList }: { month: string; onOpen: (id: string) => void; onAdd: () => void; onDenied: () => void; onList: () => void }) {
  const [data, setData] = useState<Load<OverviewData>>({ kind: 'loading' });
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilter>('');
  const [ownership, setOwnership] = useState<'' | 'personal' | 'business'>('');
  const [country, setCountry] = useState<'' | 'SA' | 'AE'>('');
  const [selected, setSelected] = useState<string | null>(null);
  const months = useMemo(() => monthsEnding(month, 6), [month]);
  const load = useCallback(async () => {
    setData((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try {
      const range = monthRange(month);
      const [six, one, vehicles] = await Promise.all([api.overview(monthRange(months[0]).from, range.to), api.overview(range.from, range.to),
        api.listVehicles(search, false, 0, FLEET_LIMIT)]);
      setData({ kind: 'ready', value: { months: six, month: one, vehicles } });
    } catch (e) { setData(failed(e)); }
  }, [month, months, search]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (data.kind === 'denied') onDenied(); }, [data.kind, onDenied]);
  const value = data.kind === 'ready' ? data.value : null;
  const rows = useMemo(() => (value ? value.vehicles.items.filter((v) => {
    const s = vehicleStatus(v.due);
    if (status === 'due' && s.tone === 'ok') return false;
    if (status === 'ok' && s.tone !== 'ok') return false;
    return (!ownership || v.ownership === ownership) && (!country || v.country === country);
  }) : []), [value, status, ownership, country]);
  const current = rows.find((v) => v.id === selected) ?? rows[0] ?? null;
  if (data.kind === 'error') return <LoadError message={t('veh.error.load', { message: data.message })} retry={() => void load()} />;
  if (data.kind === 'denied') return null;
  if (!value) return <Notice busy>{t('veh.loading')}</Notice>;
  if (value.months.vehicles === 0 && !search) return <section className="surface cmPanel"><Empty icon={<CarFront aria-hidden="true" />} title={t('veh.empty.title')} text={t('veh.empty.text')}
    action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('veh.add')}</button>} /></section>;
  const previous = months[months.length - 2];
  const spendCurrency = mainCurrency([...value.months.monthly, ...value.month.spend], 'SAR');
  const kmNow = kmIn(value.months.usage, month), kmBefore = kmIn(value.months.usage, previous);
  const spendNow = amountIn(value.months.monthly, month, spendCurrency), spendBefore = amountIn(value.months.monthly, previous, spendCurrency);
  const otherSpend = value.month.spend.filter((s) => s.currency !== spendCurrency);
  const vsBefore = t('ops.vsMonth', { month: previousMonthName(month) });
  const filtered = Boolean(status || ownership || country);
  return <>
    <KpiRow label={t('veh.summary.label')}>
      <KpiCard icon={<CarFront />} label={t('ops.veh.kpi.active')} value={formatNumber(value.months.vehicles)} hint={t('veh.fig.vehiclesHint')} />
      <KpiCard icon={<Route />} label={t('ops.veh.kpi.distance')} value={km(kmNow)} delta={delta(kmNow, kmBefore)} deltaText={deltaWords(delta(kmNow, kmBefore), vsBefore)} />
      <KpiCard icon={<Fuel />} label={t('ops.veh.kpi.spend')} value={<bdi>{formatAmount(spendNow, spendCurrency)}</bdi>} delta={delta(spendNow, spendBefore, false)}
        deltaText={deltaWords(delta(spendNow, spendBefore, false), vsBefore)} hint={otherSpend.length ? moneyList(otherSpend) : t('veh.fig.spendHint')} />
      <KpiCard icon={<Wrench />} label={t('ops.veh.kpi.due')} value={formatNumber(value.months.due.length)} tone={value.months.due.some((d) => (d.days_left ?? 1) < 0) ? 'bad' : value.months.due.length ? 'warn' : ''}
        hint={value.months.unlinked_records ? t('veh.fig.waiting', { count: value.months.unlinked_records }) : t('veh.fig.dueHint')} />
    </KpiRow>
    <p className="muted vehNote">{t('veh.noPosting')}</p>
    <div className="opsVehGrid">
      <div className="opsVehMain">
        <form className="opsFilters" role="search" onSubmit={(e) => { e.preventDefault(); setSearch(q.trim()); }}>
          <label className="opsSearch"><span className="visuallyHidden">{t('ops.veh.search')}</span><Search aria-hidden="true" />
            <input type="search" dir="auto" value={q} maxLength={80} placeholder={t('ops.veh.search')} onChange={(e) => setQ(e.target.value)} /></label>
          <button type="submit" className="secondary" disabled={q.trim() === search}>{t('veh.list.find')}</button>
          <label className="opsSelect"><span className="visuallyHidden">{t('ops.veh.filter.status')}</span>
            <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
              <option value="">{t('ops.veh.filter.anyStatus')}</option><option value="due">{t('ops.veh.filter.due')}</option><option value="ok">{t('ops.veh.status.ok')}</option></select></label>
          <label className="opsSelect"><span className="visuallyHidden">{t('ops.veh.filter.ownership')}</span>
            <select value={ownership} onChange={(e) => setOwnership(e.target.value as '' | 'personal' | 'business')}>
              <option value="">{t('ops.veh.filter.anyOwnership')}</option><option value="business">{t('veh.own.business')}</option><option value="personal">{t('veh.own.personal')}</option></select></label>
          <label className="opsSelect"><span className="visuallyHidden">{t('ops.veh.filter.country')}</span>
            <select value={country} onChange={(e) => setCountry(e.target.value as '' | 'SA' | 'AE')}>
              <option value="">{t('ops.veh.filter.anyCountry')}</option><option value="SA">{t('veh.country.SA')}</option><option value="AE">{t('veh.country.AE')}</option></select></label>
        </form>
        <FleetTable rows={rows} month={value.month} current={current?.id ?? null} onSelect={setSelected} empty={search || filtered
          ? <p className="muted opsEmptyLine">{search ? t('veh.list.noMatch', { q: search }) : t('ops.veh.noFilterMatch')}</p> : null} />
        {value.vehicles.total > value.vehicles.items.length && <p className="muted vehNote">{t('ops.veh.more', { shown: value.vehicles.items.length, total: value.vehicles.total })}{' '}
          <button type="button" className="cmLinkButton" onClick={onList}>{t('veh.allVehicles')}</button></p>}
        <div className="vehGrid">
          <FinCard title={t('veh.due.title')} icon={<CalendarClock />}>
            {value.months.due.length === 0 ? <p className="muted">{t('veh.due.none')}</p>
              : <ul className="cmList">{value.months.due.map((item) => <li key={`${item.vehicle_id}:${item.kind}:${item.source_id}`}>
                <div><strong dir="auto">{dueLabel(item)} · <bdi>{item.registration}</bdi></strong><small>{dueText(item)}{item.due_on ? ` · ${item.due_on}` : ''}</small></div>
                <button type="button" className="secondary" aria-label={t('veh.openLabel', { name: item.registration })} onClick={() => onOpen(item.vehicle_id)}>{t('veh.open')}</button>
              </li>)}</ul>}
          </FinCard>
          <FinCard title={t('veh.monthly.title')} icon={<Wallet />}>
            {value.months.monthly.length === 0 ? <p className="muted">{t('veh.monthly.none')}</p>
              : <div className="finTableWrap" tabIndex={0}><table className="finGrid"><thead><tr><th scope="col">{t('veh.col.month')}</th><th scope="col" className="num">{t('veh.col.amount')}</th></tr></thead>
                <tbody>{[...value.months.monthly].reverse().map((row) => <tr key={`${row.month}:${row.currency}`}><td>{monthLabel(row.month)}</td><td className="num"><bdi>{formatAmount(row.amount, row.currency)}</bdi></td></tr>)}</tbody></table></div>}
          </FinCard>
        </div>
      </div>
      {current && <VehiclePanel key={`${current.id}:${month}`} vehicle={current} month={month} months={months} usage={value.month.by_vehicle_usage.find((u) => u.vehicle_id === current.id) ?? null}
        spend={value.month.by_vehicle.filter((b) => b.vehicle_id === current.id)} onOpen={() => onOpen(current.id)} onChanged={() => void load()} />}
    </div>
  </>;
}

function deltaWords(d: Delta | null, vs: string): string | undefined {
  if (!d) return undefined;
  return d.percent === null ? `${t(`fshell.delta.${d.direction}` as MessageKey)} ${vs}` : `${t(`fshell.delta.pct.${d.direction}` as MessageKey, { percent: Math.abs(d.percent) })} ${vs}`;
}

/** The fleet table: the vehicle (a button that shows it in the side panel), registration, odometer, this month, next due and status. */
function FleetTable({ rows, month, current, onSelect, empty }: { rows: Vehicle[]; month: VehicleOverview; current: string | null; onSelect: (id: string) => void; empty: ReactNode }) {
  if (rows.length === 0) return <>{empty}</>;
  return <div className="finTableWrap opsTableWrap" tabIndex={0}>
    <table className="finGrid opsVehTable">
      <caption className="visuallyHidden">{t('ops.veh.table')}</caption>
      <thead><tr><th scope="col">{t('ops.veh.col.vehicle')}</th><th scope="col">{t('ops.veh.col.registration')}</th><th scope="col" className="num">{t('ops.veh.col.odometer')}</th>
        <th scope="col" className="num">{t('ops.veh.col.month')}</th><th scope="col">{t('ops.veh.col.next')}</th><th scope="col">{t('ops.veh.col.status')}</th></tr></thead>
      <tbody>{rows.map((v) => {
        const status = vehicleStatus(v.due);
        const next = nextDue(v.due);
        const spend = month.by_vehicle.filter((b) => b.vehicle_id === v.id);
        return <tr key={v.id} className={current === v.id ? 'selected' : ''}>
          <th scope="row"><button type="button" className="opsRowButton" aria-pressed={current === v.id} onClick={() => onSelect(v.id)}>
            <span className="opsVehIcon" aria-hidden="true"><CarFront /></span>
            <span><strong dir="auto">{vehicleName(v)}</strong><small>{[v.year, typeLabel(v.vehicle_type)].filter(Boolean).join(' · ')}</small></span></button></th>
          <td><span className="opsPlate"><bdi>{v.registration}</bdi></span></td>
          <td className="num"><bdi>{km(v.odometer_km)}</bdi></td>
          <td className="num"><bdi>{spend.length ? moneyList(spend) : '-'}</bdi></td>
          <td>{next ? <span className="opsNext"><strong>{dueLabel(next)}</strong><small>{next.due_on ? formatDay(next.due_on) : dueText(next)}</small></span> : '-'}</td>
          <td><StatusChip tone={status.tone === 'ok' ? 'ok' : status.tone === 'bad' ? 'bad' : 'warn'}>{status.label}</StatusChip></td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

const formatDay = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

type PanelRecords = { fuel: VehicleFuelEntry[]; service: VehicleServiceRecord[]; trips: VehicleTrip[] };

/** The selected vehicle: its figures for the month, litres and kilometres over six months, what is due, its papers, its newest records and quick actions. */
function VehiclePanel({ vehicle: v, month, months, usage, spend, onOpen, onChanged }: {
  vehicle: Vehicle; month: string; months: string[]; usage: VehicleOverview['by_vehicle_usage'][number] | null; spend: VehicleOverview['by_vehicle']; onOpen: () => void; onChanged: () => void;
}) {
  const [report, setReport] = useState<Load<VehicleReport>>({ kind: 'loading' });
  const [records, setRecords] = useState<Load<PanelRecords>>({ kind: 'loading' });
  const [form, setForm] = useState<VehicleRecordKind | null>(null);
  const [exporting, setExporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const load = useCallback(async () => {
    const [r, fuel, service, trips] = await Promise.allSettled([api.report(v.id, monthRange(months[0]).from, monthRange(month).to),
      api.listRecords('fuel', v.id, '', 0, 3), api.listRecords('service', v.id, '', 0, 3), api.listRecords('trips', v.id, '', 0, 3)]);
    setReport(r.status === 'fulfilled' ? { kind: 'ready', value: r.value } : failed(r.reason));
    setRecords(fuel.status === 'fulfilled' && service.status === 'fulfilled' && trips.status === 'fulfilled'
      ? { kind: 'ready', value: { fuel: fuel.value.items, service: service.value.items, trips: trips.value.items } }
      : failed([fuel, service, trips].find((x) => x.status === 'rejected')?.reason));
  }, [v.id, month, months]);
  useEffect(() => { void load(); }, [load]);
  const next = nextDue(v.due);
  const perKm = kmPerLitre(usage?.average_per_100);
  const today = isoDay(new Date());
  const docs = papers(v, today);
  const lastService = records.kind === 'ready' ? records.value.service[0] ?? null : null;
  const recent: RecentRecord[] = records.kind === 'ready' ? newestFirst([
    ...records.value.fuel.map((f) => ({ kind: 'fuel' as const, id: f.id, at: f.filled_at, title: t('ops.veh.rec.fuel'), detail: [f.station, litres(f.litres)].filter(Boolean).join(' · '), figure: paymentText(f.payment) })),
    ...records.value.service.map((s) => ({ kind: 'service' as const, id: s.id, at: `${s.service_on}T12:00:00Z`, title: serviceLabel(s.kind), detail: s.workshop ?? '', figure: paymentText(s.payment) })),
    ...records.value.trips.map((tr) => ({ kind: 'trips' as const, id: tr.id, at: tr.started_at, title: t('ops.veh.rec.trip'), detail: [tr.origin, tr.destination].filter(Boolean).join(' · ') || purposeLabel(tr.purpose), figure: km(tr.distance_km) })),
  ], 4) : [];
  const exportCsv = async () => {
    setExporting(true); setMessage(null);
    try { await api.exportCsv(v.id); } catch (e) { setMessage(describeError(e)); } finally { setExporting(false); }
  };
  const ICON = { fuel: <Fuel />, service: <Wrench />, trips: <Route /> };
  return <aside className="opsVehPanel" aria-labelledby="veh-panel-title">
    <div className="opsVehPanelHead">
      <h2 id="veh-panel-title"><span dir="auto">{vehicleName(v)}</span> · <bdi>{v.registration}</bdi></h2>
      <div className="opsVehPanelActions">
        <button type="button" className="secondary" onClick={onOpen}>{t('ops.veh.openPage')}</button>
        <button type="button" className="secondary" disabled={exporting} onClick={() => void exportCsv()}><Download aria-hidden="true" />{exporting ? t('ops.veh.exporting') : t('ops.veh.export')}</button>
      </div>
    </div>
    {message && <Notice error>{message}</Notice>}
    <dl className="opsFacts">
      <div><dt><Gauge aria-hidden="true" />{t('ops.veh.fact.odometer')}</dt><dd><bdi>{km(v.odometer_km)}</bdi></dd></div>
      <div><dt><Wrench aria-hidden="true" />{t('ops.veh.fact.next')}</dt><dd>{next ? <>{dueLabel(next)} <small>{dueText(next)}</small></> : t('veh.due.none')}</dd></div>
      <div><dt><Fuel aria-hidden="true" />{t('ops.veh.fact.economy')}</dt><dd><bdi>{perKm ? t('ops.veh.kmPerL', { value: perKm }) : '-'}</bdi></dd></div>
      <div><dt><Wallet aria-hidden="true" />{t('ops.veh.fact.cost')}</dt><dd><bdi>{spend.length ? moneyList(spend) : '-'}</bdi></dd></div>
    </dl>
    <section className="opsPanelSection" aria-labelledby="veh-usage-title">
      <h3 id="veh-usage-title">{t('ops.veh.usage.title')}</h3>
      {report.kind === 'loading' ? <Notice busy>{t('veh.loading')}</Notice>
        : report.kind !== 'ready' ? <LoadError message={t('veh.error.report', { message: report.kind === 'error' || report.kind === 'denied' ? report.message : '' })} retry={() => void load()} />
        : report.value.usage.length === 0 ? <p className="muted">{t('ops.veh.usage.none')}</p>
        : <UsageChart rows={usageRows(report.value.usage, months)} title={t('ops.veh.usage.title')} litresLabel={t('ops.veh.usage.litres')} kmLabel={t('ops.veh.usage.km')} />}
    </section>
    <section className="opsPanelSection" aria-labelledby="veh-timeline-title">
      <h3 id="veh-timeline-title">{t('ops.veh.timeline.title')}</h3>
      <ol className="opsTimeline">
        {lastService && <li className="done"><span><strong>{t('ops.veh.timeline.last')}</strong><small>{serviceLabel(lastService.kind)}{lastService.workshop ? ` · ${lastService.workshop}` : ''}</small></span><bdi>{formatDay(lastService.service_on)}</bdi></li>}
        {v.due.map((d) => <li key={`${d.kind}:${d.source_id}`} className={d.days_left !== null && d.days_left < 0 ? 'late' : 'next'}>
          <span><strong>{dueLabel(d)}</strong><small>{dueText(d)}</small></span><bdi>{d.due_on ? formatDay(d.due_on) : km(d.due_km)}</bdi></li>)}
        {!lastService && v.due.length === 0 && <li className="muted">{t('ops.veh.timeline.none')}</li>}
      </ol>
    </section>
    <section className="opsPanelSection" aria-labelledby="veh-papers-title">
      <h3 id="veh-papers-title">{t('ops.veh.papers.title')}</h3>
      {docs.length === 0 ? <p className="muted">{t('ops.veh.papers.none')}</p> : <ul className="opsPapers">{docs.map((d) => <li key={d.kind}>
        <ShieldCheck aria-hidden="true" /><span><strong>{t(`veh.due.${d.kind}` as MessageKey)}</strong>
          <StatusChip tone={d.days < 0 ? 'bad' : d.days <= 30 ? 'warn' : 'ok'}>{d.days < 0 ? t('ops.veh.papers.expired', { date: formatDay(d.on) }) : t('ops.veh.papers.expires', { date: formatDay(d.on) })}</StatusChip></span>
      </li>)}</ul>}
    </section>
    <section className="opsPanelSection" aria-labelledby="veh-recent-title">
      <div className="opsPanelSectionHead"><h3 id="veh-recent-title">{t('ops.veh.recent.title')}</h3><button type="button" className="cmLinkButton" onClick={onOpen}>{t('ops.viewAll')}</button></div>
      {records.kind === 'loading' ? <Notice busy>{t('veh.loading')}</Notice>
        : records.kind !== 'ready' ? <LoadError message={t('veh.error.load', { message: records.kind === 'error' || records.kind === 'denied' ? records.message : '' })} retry={() => void load()} />
        : recent.length === 0 ? <p className="muted">{t('ops.veh.recent.none')}</p>
        : <ul className="opsRecent">{recent.map((r) => <li key={`${r.kind}:${r.id}`}><span className="opsRecentIcon" aria-hidden="true">{ICON[r.kind]}</span>
          <span><strong>{r.title}</strong><small dir="auto">{r.detail || '-'}</small></span><small><bdi>{formatDay(r.at.slice(0, 10))}</bdi></small><strong className="opsRecentFigure"><bdi>{r.figure}</bdi></strong></li>)}</ul>}
    </section>
    {v.archived_at ? null : <div className="opsQuick">
      <button type="button" onClick={() => setForm('fuel')}><Fuel aria-hidden="true" />{t('ops.veh.act.fuel')}</button>
      <button type="button" className="secondary" onClick={() => setForm('trips')}><Route aria-hidden="true" />{t('ops.veh.act.trip')}</button>
      <button type="button" className="secondary" onClick={() => setForm('service')}><Wrench aria-hidden="true" />{t('ops.veh.act.service')}</button>
      <a className="finLinkBtn secondary" href="#vault"><FileUp aria-hidden="true" />{t('ops.veh.act.document')}</a>
    </div>}
    {form && <RecordForm kind={form} vehicle={v} record={null} onClose={() => setForm(null)} onSaved={() => { setForm(null); void load(); onChanged(); }} />}
  </aside>;
}

// ---- Vehicles list -------------------------------------------------------------------------------------

function ListPanel({ onOpen, onAdd, onDenied }: { onOpen: (id: string) => void; onAdd: () => void; onDenied: () => void }) {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [run, setRun] = useState(0);
  const [page, setPage] = useState<Load<VehiclePage<Vehicle>>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setPage({ kind: 'loading' });
    try { setPage({ kind: 'ready', value: await api.listVehicles(search, archived, offset, PAGE_SIZE) }); } catch (e) { setPage(failed(e)); }
  }, [search, archived, offset, run]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (page.kind === 'denied') onDenied(); }, [page.kind, onDenied]);
  return <section className="surface cmPanel" aria-labelledby="veh-list-title">
    <h2 id="veh-list-title">{archived ? t('veh.list.archivedTitle') : t('veh.list.title')}</h2>
    <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(q.trim()); setRun((n) => n + 1); }}>
      <label className="cmInline cmSearch"><span>{t('veh.list.search')}</span><input type="search" dir="auto" value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} /></label>
      <button type="submit" className="secondary">{t('veh.list.find')}</button>
      <label className="vehCheck"><input type="checkbox" checked={archived} onChange={(e) => { setOffset(0); setArchived(e.target.checked); }} />{t('veh.list.showArchived')}</label>
    </form>
    {page.kind === 'loading' && <Notice busy>{t('veh.loading')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('veh.error.load', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && (search || archived
      ? <p className="muted">{search ? t('veh.list.noMatch', { q: search }) : t('veh.list.noArchived')}</p>
      : <Empty icon={<CarFront aria-hidden="true" />} title={t('veh.empty.title')} text={t('veh.empty.text')} action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('veh.add')}</button>} />)}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <ul className="cmList vehList">{page.value.items.map((v) => <li key={v.id}>
        <div><strong><bdi>{v.registration}</bdi> · <span dir="auto">{vehicleName(v)}</span></strong>
          <small>{[t(`veh.own.${v.ownership}` as MessageKey), v.country === 'SA' ? t('veh.country.SA') : t('veh.country.AE'), km(v.odometer_km),
            v.due[0] ? `${dueLabel(v.due[0])}: ${dueText(v.due[0])}` : ''].filter(Boolean).join(' · ')}</small></div>
        <button type="button" className="secondary" aria-label={t('veh.openLabel', { name: v.registration })} onClick={() => onOpen(v.id)}>{t('veh.open')}</button>
      </li>)}</ul>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
  </section>;
}

export function Pager({ total, offset, onChange }: { total: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  return <div className="vehPager">
    <span className="muted">{t('veh.page.showing', { from: offset + 1, to: Math.min(offset + PAGE_SIZE, total), total })}</span>
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft aria-hidden="true" className="vehFlip" />{t('veh.page.previous')}</button>
    <button type="button" className="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>{t('veh.page.next')}<ChevronRight aria-hidden="true" className="vehFlip" /></button>
  </div>;
}

// ---- Settings ------------------------------------------------------------------------------------------

function SettingsPanel({ onDenied }: { onDenied: () => void }) {
  const [prefs, setPrefs] = useState<Load<VehiclePreferences>>({ kind: 'loading' });
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [draft, setDraft] = useState<VehiclePreferences | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setPrefs({ kind: 'loading' });
    try {
      const [p, v] = await Promise.all([api.getPreferences(), api.listVehicles('', false, 0, 200)]);
      setPrefs({ kind: 'ready', value: p }); setDraft(p); setVehicles(v.items);
    } catch (e) { setPrefs(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (prefs.kind === 'denied') onDenied(); }, [prefs.kind, onDenied]);
  if (prefs.kind === 'loading') return <Notice busy>{t('veh.loading')}</Notice>;
  if (prefs.kind === 'error') return <LoadError message={t('veh.error.load', { message: prefs.message })} retry={() => void load()} />;
  if (prefs.kind !== 'ready' || !draft) return null;
  const save = async () => {
    setBusy(true); setMessage(null);
    try {
      const saved = await api.savePreferences({ version: draft.version, trip_places: draft.trip_places, default_vehicle_id: draft.default_vehicle_id,
        reminder_days: draft.reminder_days, reminders: draft.reminders });
      setDraft(saved); setMessage({ text: t('veh.settings.saved') });
    } catch (e) { setMessage({ text: describeError(e), error: true }); }
    finally { setBusy(false); }
  };
  return <div className="vehGrid">
    <form className="surface cmPanel vehForm" aria-labelledby="veh-set-title" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <h2 id="veh-set-title">{t('veh.settings.title')}</h2>
      <label className="vehCheck"><input type="checkbox" checked={draft.trip_places} onChange={(e) => setDraft({ ...draft, trip_places: e.target.checked })} />{t('veh.settings.places')}</label>
      <p className="muted vehNote">{t('veh.settings.placesHint')}</p>
      <label className="field"><span>{t('veh.settings.default')}</span>
        <select value={draft.default_vehicle_id ?? ''} onChange={(e) => setDraft({ ...draft, default_vehicle_id: e.target.value || null })}>
          <option value="">{t('veh.settings.noDefault')}</option>
          {vehicles.map((v) => <option key={v.id} value={v.id}>{v.registration} · {vehicleName(v)}</option>)}
        </select></label>
      <label className="vehCheck"><input type="checkbox" checked={draft.reminders} onChange={(e) => setDraft({ ...draft, reminders: e.target.checked })} />{t('veh.settings.reminders')}</label>
      <label className="field"><span>{t('veh.settings.days')}</span>
        <input type="number" inputMode="numeric" min={1} max={120} value={draft.reminder_days} onChange={(e) => setDraft({ ...draft, reminder_days: Math.max(1, Math.min(120, Number(e.target.value) || 30)) })} /></label>
      {message && <Notice error={message.error}>{message.text}</Notice>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('veh.saving') : t('veh.save')}</button></div>
    </form>
    <section className="surface cmPanel" aria-labelledby="veh-iphone-title">
      <h2 id="veh-iphone-title"><Smartphone aria-hidden="true" className="vehInlineIcon" />{t('veh.iphone.title')}</h2>
      <p>{t('veh.iphone.text')}</p>
      <p className="muted vehNote">{t('veh.iphone.odometer')}</p>
      <a className="vehLinkButton" href="#iphone">{t('veh.iphone.open')}</a>
    </section>
  </div>;
}
