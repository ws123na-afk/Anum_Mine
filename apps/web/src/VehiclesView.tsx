// Vehicles, fuel and mileage (docs/vehicles.md#web). Overview (spend per currency, what is due, records waiting for a
// payment), Vehicles (search, archived, paging) and Settings (trip places, default vehicle, reminders, the iPhone
// Shortcut). A vehicle opens its own page: Fuel, Trips, Service, Odometer, Report and Details. Costs are links to the
// person's Finance payments, never new money: the page says so wherever a payment is shown.
import { useCallback, useEffect, useState } from 'react';
import { CarFront, ChevronLeft, ChevronRight, Lock, Plus, Smartphone } from 'lucide-react';
import type { Vehicle, VehicleOverview, VehiclePage, VehiclePreferences } from '@anum/contracts';
import * as api from './lib/vehiclesApi';
import { PAGE_SIZE, VEHICLE_TABS, dueLabel, dueText, hashForTab, km, moneyList, tabFromHash, vehicleName, type VehicleTab } from './lib/vehicles';
import { formatAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { PageHeader, PageSummary, type Crumb } from './PageParts';
import { VehicleDetail, VehicleForm } from './VehicleParts';
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
  const refresh = () => setTick((n) => n + 1);
  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('vehicles'), href: '#vehicles', onClick: () => { setOpenId(null); pick('overview'); } }];
  if (!openId && tab !== 'overview') crumbs.push({ label: t(`veh.tab.${tab}` as MessageKey) });
  const header = <PageHeader title={viewLabel('vehicles')} subtitle={t('veh.subtitle')} crumbs={openId || tab !== 'overview' ? crumbs : undefined}
    actions={denied ? undefined : <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('veh.add')}</button>} />;
  const added = (vehicle: Vehicle) => { setAdding(false); refresh(); setOpenId(vehicle.id); };
  if (denied) return <div className="cmLayout vehLayout">{header}<VehiclesDenied /></div>;
  return <div className="cmLayout vehLayout">
    {header}
    {adding && <VehicleForm onClose={() => setAdding(false)} onSaved={added} />}
    {openId ? <VehicleDetail key={`${openId}:${tick}`} vehicleId={openId} onBack={() => { setOpenId(null); refresh(); }} onDeleted={() => { setOpenId(null); refresh(); }} />
      : <>
        <nav className="settingsTabs cmTabs" aria-label={t('veh.tabs')}>
          {VEHICLE_TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`veh.tab.${id}` as MessageKey)}</button>)}
        </nav>
        {tab === 'overview' && <OverviewPanel key={tick} onOpen={setOpenId} onAdd={() => setAdding(true)} onDenied={() => setDenied(true)} onList={() => pick('list')} />}
        {tab === 'list' && <ListPanel key={tick} onOpen={setOpenId} onAdd={() => setAdding(true)} onDenied={() => setDenied(true)} />}
        {tab === 'settings' && <SettingsPanel onDenied={() => setDenied(true)} />}
      </>}
  </div>;
}

// ---- Overview ------------------------------------------------------------------------------------------

function OverviewPanel({ onOpen, onAdd, onDenied, onList }: { onOpen: (id: string) => void; onAdd: () => void; onDenied: () => void; onList: () => void }) {
  const [data, setData] = useState<Load<VehicleOverview>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setData({ kind: 'loading' });
    try { setData({ kind: 'ready', value: await api.overview() }); } catch (e) { setData(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (data.kind === 'denied') onDenied(); }, [data.kind, onDenied]);
  const state = data.kind === 'ready' ? 'ready' : data.kind === 'loading' ? 'loading' : 'error';
  const value = data.kind === 'ready' ? data.value : null;
  const figures: Figure[] = [
    figure('vehicles', t('veh.fig.vehicles'), { state }, () => ({ value: String(value!.vehicles), hint: t('veh.fig.vehiclesHint') })),
    figure('spend', t('veh.fig.spend'), { state }, () => ({ value: moneyList(value!.spend), hint: t('veh.fig.spendHint') })),
    figure('due', t('veh.fig.due'), { state }, () => ({ value: String(value!.due.length), hint: t('veh.fig.dueHint') })),
    figure('unlinked', t('veh.fig.unlinked'), { state }, () => ({ value: String(value!.unlinked_records), hint: t('veh.fig.unlinkedHint') })),
  ];
  if (data.kind === 'error') return <LoadError message={t('veh.error.load', { message: data.message })} retry={() => void load()} />;
  if (data.kind === 'denied') return null;
  if (value && value.vehicles === 0) return <section className="surface cmPanel"><Empty icon={<CarFront aria-hidden="true" />} title={t('veh.empty.title')} text={t('veh.empty.text')}
    action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('veh.add')}</button>} /></section>;
  return <>
    <PageSummary label={t('veh.summary.label')} figures={figures} onRetry={() => void load()} footer={<p className="muted vehNote">{t('veh.noPosting')}</p>} />
    {data.kind === 'loading' && <Notice busy>{t('veh.loading')}</Notice>}
    {value && <div className="vehGrid">
      <section className="surface cmPanel" aria-labelledby="veh-due-title">
        <h2 id="veh-due-title">{t('veh.due.title')}</h2>
        {value.due.length === 0 ? <p className="muted">{t('veh.due.none')}</p>
          : <ul className="cmList">{value.due.map((item) => <li key={`${item.vehicle_id}:${item.kind}:${item.source_id}`}>
            <div><strong dir="auto">{dueLabel(item)} · <bdi>{item.registration}</bdi></strong><small>{dueText(item)}{item.due_on ? ` · ${item.due_on}` : ''}</small></div>
            <button type="button" className="secondary" aria-label={t('veh.openLabel', { name: item.registration })} onClick={() => onOpen(item.vehicle_id)}>{t('veh.open')}</button>
          </li>)}</ul>}
      </section>
      <section className="surface cmPanel" aria-labelledby="veh-month-title">
        <h2 id="veh-month-title">{t('veh.monthly.title')}</h2>
        {value.monthly.length === 0 ? <p className="muted">{t('veh.monthly.none')}</p>
          : <div className="vehTableWrap"><table className="cmTable vehTable"><thead><tr><th scope="col">{t('veh.col.month')}</th><th scope="col" className="num">{t('veh.col.amount')}</th></tr></thead>
            <tbody>{value.monthly.map((row) => <tr key={`${row.month}:${row.currency}`}><td>{row.month}</td><td className="num" dir="ltr">{formatAmount(row.amount, row.currency)}</td></tr>)}</tbody></table></div>}
      </section>
      <section className="surface cmPanel" aria-labelledby="veh-byveh-title">
        <h2 id="veh-byveh-title">{t('veh.byVehicle.title')}</h2>
        {value.by_vehicle.length === 0 ? <p className="muted">{t('veh.monthly.none')}</p>
          : <ul className="cmList">{value.by_vehicle.map((row) => <li key={`${row.vehicle_id}:${row.currency}`}>
            <div><strong><bdi>{row.registration}</bdi></strong><small dir="ltr">{formatAmount(row.amount, row.currency)}</small></div>
            <button type="button" className="secondary" aria-label={t('veh.openLabel', { name: row.registration })} onClick={() => onOpen(row.vehicle_id)}>{t('veh.open')}</button>
          </li>)}</ul>}
        <button type="button" className="secondary" onClick={onList}>{t('veh.allVehicles')}</button>
      </section>
    </div>}
  </>;
}

// ---- Vehicles list -------------------------------------------------------------------------------------

function ListPanel({ onOpen, onAdd, onDenied }: { onOpen: (id: string) => void; onAdd: () => void; onDenied: () => void }) {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<VehiclePage<Vehicle>>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setPage({ kind: 'loading' });
    try { setPage({ kind: 'ready', value: await api.listVehicles(search, archived, offset, PAGE_SIZE) }); } catch (e) { setPage(failed(e)); }
  }, [search, archived, offset]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (page.kind === 'denied') onDenied(); }, [page.kind, onDenied]);
  return <section className="surface cmPanel" aria-labelledby="veh-list-title">
    <h2 id="veh-list-title">{archived ? t('veh.list.archivedTitle') : t('veh.list.title')}</h2>
    <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(q.trim()); }}>
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
