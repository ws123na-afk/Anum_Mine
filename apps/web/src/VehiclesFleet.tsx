// Running a client fleet (docs/vehicles.md#fleet): Fleet costs (the month per vehicle and per km, the worst vehicles, twelve
// months, renewals, Tyre planning beside them, and the monthly client report as PDF or Excel in English or Arabic) and Import
// (a client's vehicles, fuel, service records or odometer readings from Excel or CSV: preview, columns, row problems,
// duplicates, all or nothing). A lazy chunk of the Vehicles page.
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { CalendarClock, CarFront, CircleGauge, Download, FileSpreadsheet, FileText, FileUp, Fuel, RefreshCw, Route, Search, TrendingUp, TriangleAlert, Upload, Wallet } from 'lucide-react';
import type { FleetCosts, FleetImportKind, FleetImportPreview, FleetImportResult, FleetRowAction, FleetRowStatus } from '@anum/contracts';
import * as api from './lib/fleet';
import * as fleetApi from './lib/fleetApi';
import { getCurrentMembership } from './lib/api';
import { bytesToBase64 } from './lib/contacts';
import { formatAmount, monthLabel } from './lib/finance';
import { describeError } from './lib/errors';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { ConfirmDialog } from './Dialog';
import './vehicles.css';

const money = (value: string | null | undefined, currency: string) => (value === null || value === undefined ? '-' : formatAmount(value, currency));
const kmText = (value: string | null | undefined) => (value === null || value === undefined ? '-' : formatNumber(Math.round(Number(value))));
const perKm = (value: string | null, currency: string) => (value === null ? '-' : `${currency} ${Number(value).toFixed(3)}`);

function useOwner(): boolean | null {
  const [owner, setOwner] = useState<boolean | null>(null);
  useEffect(() => { void getCurrentMembership().then((m) => setOwner(m.role === 'owner')).catch(() => setOwner(false)); }, []);
  return owner;
}

function Change({ now, before }: { now: string; before: string }) {
  const pct = api.percentChange(now, before);
  if (pct === null) return <span className="muted">-</span>;
  return <span className={pct > 0 ? 'vehUp' : pct < 0 ? 'vehDown' : ''}><bdi>{pct > 0 ? '+' : ''}{formatNumber(pct)}%</bdi></span>;
}

// ---- Fleet costs -----------------------------------------------------------------------------------------------------

export function FleetCostsPanel({ month, onOpen, onImport, onAdd, onDenied }: { month: string; onOpen: (id: string) => void; onImport: () => void; onAdd: () => void; onDenied: () => void }) {
  const [data, setData] = useState<Load<FleetCosts>>({ kind: 'loading' });
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<api.FleetSort>('total');
  const [shown, setShown] = useState(api.ROWS_PAGE);
  const load = useCallback(async () => {
    setData({ kind: 'loading' });
    try { setData({ kind: 'ready', value: await fleetApi.fleetCosts(month) }); } catch (e) { setData(failed(e)); }
  }, [month]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (data.kind === 'denied') onDenied(); }, [data.kind, onDenied]);
  const value = data.kind === 'ready' ? data.value : null;
  const rows = useMemo(() => (value ? api.fleetRows(value.rows, query, sort) : []), [value, query, sort]);
  if (data.kind === 'error') return <LoadError message={t('veh.fleet.error.load', { message: data.message })} retry={() => void load()} />;
  if (data.kind === 'denied') return null;
  if (!value) return <Notice busy>{t('veh.fleet.loading')}</Notice>;
  if (value.vehicles === 0) return <section className="surface cmPanel"><Empty icon={<CarFront aria-hidden="true" />} title={t('veh.fleet.empty.title')} text={t('veh.fleet.empty.text')}
    action={<div className="actions"><button type="button" onClick={onImport}><FileUp aria-hidden="true" />{t('veh.fleet.empty.import')}</button>
      <button type="button" className="secondary" onClick={onAdd}>{t('veh.add')}</button></div>} /></section>;
  const main = api.mainTotal(value.totals);
  const others = value.totals.filter((total) => total !== main);
  return <div className="vehFleet">
    <KpiRow label={t('veh.fleet.kpis')}>
      <KpiCard icon={<CarFront />} label={t('veh.fleet.kpi.vehicles')} value={formatNumber(value.vehicles)} hint={t('veh.fleet.kpi.vehiclesHint')} />
      <KpiCard icon={<Route />} label={t('veh.fleet.kpi.distance')} value={<bdi>{kmText(value.distance_km)}</bdi>} hint={t('veh.fleet.kpi.litres', { litres: formatNumber(Math.round(Number(value.fuel_litres))) })} />
      <KpiCard icon={<Wallet />} label={t('veh.fleet.kpi.total')} value={<bdi>{main ? money(main.total, main.currency) : '-'}</bdi>}
        hint={main ? <>{t('veh.fleet.kpi.before', { amount: money(main.previous_total, main.currency) })}{others.length ? ` · ${others.map((o) => money(o.total, o.currency)).join(' · ')}` : ''}</> : t('veh.fleet.kpi.noCost')} />
      <KpiCard icon={<CircleGauge />} label={t('veh.fleet.kpi.perKm')} value={<bdi>{main ? perKm(main.cost_per_km, main.currency) : '-'}</bdi>} hint={t('veh.fleet.kpi.perKmHint')} />
    </KpiRow>
    {main && Number(main.recorded) > 0 && <p className="muted vehNote">{t('veh.fleet.recorded', { share: api.recordedShare(main) })}</p>}
    <p className="muted vehNote">{t('veh.fleet.basis')}</p>
    <FinCard title={t('veh.fleet.vehicles.title')} icon={<Fuel />} subtitle={t('veh.fleet.vehicles.subtitle', { month: monthLabel(value.month) })}>
      <form className="opsFilters" role="search" onSubmit={(e) => e.preventDefault()}>
        <label className="opsSearch"><span className="visuallyHidden">{t('veh.fleet.search')}</span><Search aria-hidden="true" />
          <input type="search" dir="auto" value={query} maxLength={80} placeholder={t('veh.fleet.search')} onChange={(e) => { setQuery(e.target.value); setShown(api.ROWS_PAGE); }} /></label>
        <label className="opsSelect"><span className="visuallyHidden">{t('veh.fleet.sort')}</span>
          <select value={sort} onChange={(e) => setSort(e.target.value as api.FleetSort)}>
            <option value="total">{t('veh.fleet.sort.total')}</option><option value="per_km">{t('veh.fleet.sort.perKm')}</option><option value="registration">{t('veh.fleet.sort.registration')}</option>
          </select></label>
      </form>
      {rows.length === 0 ? <p className="muted">{t('veh.fleet.noMatch', { q: query })}</p>
        : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('veh.fleet.vehicles.title')}><table className="finGrid vehFleetTable">
          <caption className="visuallyHidden">{t('veh.fleet.vehicles.title')}</caption>
          <thead><tr><th scope="col">{t('veh.fleet.col.vehicle')}</th><th scope="col" className="num">{t('veh.fleet.col.km')}</th><th scope="col" className="num">{t('veh.fleet.col.l100')}</th>
            <th scope="col" className="num">{t('veh.fleet.cat.fuel')}</th><th scope="col" className="num">{t('veh.fleet.cat.service')}</th><th scope="col" className="num">{t('veh.fleet.cat.tyres')}</th>
            <th scope="col" className="num">{t('veh.fleet.cat.other')}</th><th scope="col" className="num">{t('veh.fleet.col.total')}</th><th scope="col" className="num">{t('veh.fleet.col.perKm')}</th>
            <th scope="col" className="num">{t('veh.fleet.col.change')}</th></tr></thead>
          <tbody>{rows.slice(0, shown).flatMap((row) => (row.costs.length ? row.costs : [null]).map((cost, index) => <tr key={`${row.vehicle_id}:${cost?.currency ?? '-'}`}>
            {index === 0 ? <th scope="row" rowSpan={Math.max(row.costs.length, 1)}><button type="button" className="cmLinkButton" onClick={() => onOpen(row.vehicle_id)}
              aria-label={t('veh.openLabel', { name: row.registration })}><bdi>{row.registration}</bdi></button>
              <small dir="auto">{[row.nickname, row.make, row.model].filter(Boolean).join(' · ') || ' '}</small>{row.archived && <small>{t('veh.archived')}</small>}</th> : null}
            {index === 0 ? <><td className="num" rowSpan={Math.max(row.costs.length, 1)}><bdi>{kmText(row.distance_km)}</bdi></td>
              <td className="num" rowSpan={Math.max(row.costs.length, 1)}><bdi>{row.litres_per_100km ?? '-'}</bdi></td></> : null}
            {cost ? <><td className="num"><bdi>{money(cost.fuel, cost.currency)}</bdi></td><td className="num"><bdi>{money(cost.service, cost.currency)}</bdi></td>
              <td className="num"><bdi>{money(cost.tyres, cost.currency)}</bdi></td><td className="num"><bdi>{money(cost.other, cost.currency)}</bdi></td>
              <td className="num"><strong><bdi>{money(cost.total, cost.currency)}</bdi></strong></td><td className="num"><bdi>{perKm(cost.cost_per_km, cost.currency)}</bdi></td>
              <td className="num"><Change now={cost.total} before={cost.previous_total} /></td></>
              : <td colSpan={7} className="muted">{t('veh.fleet.noCosts')}</td>}
          </tr>))}</tbody>
        </table></div>}
      {rows.length > shown && <div className="vehPager"><span>{t('veh.fleet.showing', { shown: formatNumber(shown), total: formatNumber(rows.length) })}</span>
        <button type="button" className="secondary" onClick={() => setShown((n) => n + api.ROWS_PAGE)}>{t('veh.fleet.more')}</button></div>}
    </FinCard>
    <div className="vehGrid">
      <FinCard title={t('veh.fleet.worst.title')} icon={<TriangleAlert />} subtitle={t('veh.fleet.worst.subtitle')}>
        {value.worst.length === 0 ? <p className="muted">{t('veh.fleet.worst.none')}</p>
          : <ol className="cmList vehWorst">{value.worst.map((w) => <li key={`${w.vehicle_id}:${w.currency}`}>
            <div><strong><bdi>{w.registration}</bdi>{w.nickname ? <> · <bdi>{w.nickname}</bdi></> : null}</strong>
              <small><bdi>{perKm(w.cost_per_km, w.currency)}</bdi> · <bdi>{money(w.total, w.currency)}</bdi> · <bdi>{kmText(w.distance_km)}</bdi> {t('veh.fleet.km')}</small></div>
            <button type="button" className="secondary" aria-label={t('veh.openLabel', { name: w.registration })} onClick={() => onOpen(w.vehicle_id)}>{t('veh.open')}</button></li>)}</ol>}
      </FinCard>
      <FinCard title={t('veh.fleet.renewals.title')} icon={<CalendarClock />} subtitle={t('veh.fleet.renewals.subtitle')}>
        {value.renewals.length === 0 ? <p className="muted">{t('veh.fleet.renewals.none')}</p>
          : <ul className="cmList">{value.renewals.map((r) => <li key={`${r.vehicle_id}:${r.kind}`}>
            <div><strong>{t(`veh.fleet.renewal.${r.kind}` as MessageKey)} · <bdi>{r.registration}</bdi></strong><small><bdi>{r.due_on}</bdi></small></div>
            <StatusChip tone={r.days_left < 0 ? 'bad' : r.days_left <= 30 ? 'warn' : 'info'}>{r.days_left < 0 ? t('veh.fleet.overdue') : t('veh.fleet.daysLeft', { days: r.days_left })}</StatusChip></li>)}</ul>}
      </FinCard>
    </div>
    <Trend value={value} />
    <TyreCard value={value} />
    <ReportCard month={value.month} />
  </div>;
}

function Trend({ value }: { value: FleetCosts }) {
  const currencies = [...new Set(value.trend.map((row) => row.currency))];
  const top = Math.max(1, ...value.trend.map((row) => Number(row.total)));
  return <FinCard title={t('veh.fleet.trend.title')} icon={<TrendingUp />} subtitle={t('veh.fleet.trend.subtitle')}>
    {currencies.length === 0 ? <p className="muted">{t('veh.fleet.trend.none')}</p> : currencies.map((currency) => <div key={currency} className="finTableWrap" tabIndex={0} role="region"
      aria-label={t('veh.fleet.trend.in', { currency })}><table className="finGrid vehTrend">
      <caption className="visuallyHidden">{t('veh.fleet.trend.in', { currency })}</caption>
      <thead><tr><th scope="col">{t('veh.col.month')}</th><th scope="col" className="num">{t('veh.fleet.col.total')}</th><th scope="col" className="vehTrendBarHead"><span className="visuallyHidden">{t('veh.fleet.trend.bar')}</span></th>
        <th scope="col" className="num">{t('veh.fleet.col.km')}</th><th scope="col" className="num">{t('veh.fleet.col.perKm')}</th></tr></thead>
      <tbody>{[...value.trend].filter((row) => row.currency === currency).reverse().map((row) => <tr key={row.month} className={row.month === value.month ? 'selected' : ''}>
        <th scope="row">{monthLabel(row.month)}</th><td className="num"><bdi>{money(row.total, currency)}</bdi></td>
        <td className="vehTrendBar" aria-hidden="true"><span style={{ inlineSize: `${Math.round((Number(row.total) / top) * 100)}%` }} /></td>
        <td className="num"><bdi>{kmText(row.distance_km)}</bdi></td><td className="num"><bdi>{perKm(row.cost_per_km, currency)}</bdi></td></tr>)}</tbody>
    </table></div>)}
  </FinCard>;
}

function TyreCard({ value }: { value: FleetCosts }) {
  const tyres = value.tyres;
  return <FinCard title={t('veh.fleet.tyres.title')} icon={<CircleGauge />} subtitle={t('veh.fleet.tyres.subtitle')}>
    {tyres.status !== 'ok' ? <p className="muted">{t(`veh.fleet.tyres.${tyres.status}` as MessageKey)}</p>
      : tyres.vehicles.length === 0 ? <p className="muted">{t('veh.fleet.tyres.noMatch')}</p>
        : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('veh.fleet.tyres.title')}><table className="finGrid">
          <caption className="visuallyHidden">{t('veh.fleet.tyres.title')}</caption>
          <thead><tr><th scope="col">{t('veh.fleet.col.vehicle')}</th><th scope="col">{t('veh.fleet.tyres.source')}</th><th scope="col" className="num">{t('veh.fleet.tyres.cost')}</th>
            <th scope="col" className="num">{t('veh.fleet.tyres.due')}</th><th scope="col">{t('veh.fleet.tyres.next')}</th></tr></thead>
          <tbody>{tyres.vehicles.map((row) => <tr key={row.vehicle_id}><th scope="row"><bdi>{row.registration}</bdi></th><td dir="auto">{row.tyre_vehicle}</td>
            <td className="num"><bdi>{money(row.tyre_cost, tyres.currency ?? 'SAR')}</bdi></td><td className="num"><bdi>{formatNumber(row.tyres_due)}</bdi></td>
            <td><bdi>{row.next_removal ?? '-'}</bdi></td></tr>)}</tbody>
        </table></div>}
  </FinCard>;
}

function ReportCard({ month }: { month: string }) {
  const id = useId();
  const [company, setCompany] = useState('');
  const [language, setLanguage] = useState<'en' | 'ar'>('en');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState<'pdf' | 'xlsx' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const download = async (format: 'pdf' | 'xlsx') => {
    if (!company.trim()) { setError(t('veh.fleet.report.companyRequired')); return; }
    setBusy(format); setError(null); setSaved(null);
    try { setSaved(await fleetApi.downloadReport({ month, language, format, company_name: company.trim(), notes })); }
    catch (e) { setError(t('veh.fleet.report.failed', { message: describeError(e) })); } finally { setBusy(null); }
  };
  return <FinCard title={t('veh.fleet.report.title')} icon={<FileText />} subtitle={t('veh.fleet.report.subtitle', { month: monthLabel(month) })}>
    <form className="vehForm vehReportForm" onSubmit={(e) => { e.preventDefault(); void download('pdf'); }}>
      <label className="field" htmlFor={`${id}-company`}><span>{t('veh.fleet.report.company')}</span>
        <input id={`${id}-company`} dir="auto" value={company} maxLength={120} aria-required="true" aria-invalid={error === t('veh.fleet.report.companyRequired') || undefined}
          aria-describedby={`${id}-company-hint`} onChange={(e) => setCompany(e.target.value)} />
        <small id={`${id}-company-hint`} className="muted">{t('veh.fleet.report.companyHint')}</small></label>
      <label className="field" htmlFor={`${id}-language`}><span>{t('veh.fleet.report.language')}</span>
        <select id={`${id}-language`} value={language} onChange={(e) => setLanguage(e.target.value as 'en' | 'ar')}>
          <option value="en">{t('veh.fleet.report.english')}</option><option value="ar">{t('veh.fleet.report.arabic')}</option></select></label>
      <label className="field vehReportNotes" htmlFor={`${id}-notes`}><span>{t('veh.fleet.report.notes')}</span>
        <textarea id={`${id}-notes`} dir="auto" rows={4} value={notes} maxLength={api.NOTES_MAX} placeholder={t('veh.fleet.report.notesPlaceholder')} onChange={(e) => setNotes(e.target.value)} />
        <small className="muted">{t('veh.fleet.report.notesHint', { left: formatNumber(api.NOTES_MAX - notes.length) })}</small></label>
      <div className="actions">
        <button type="submit" disabled={busy !== null}><Download aria-hidden="true" />{busy === 'pdf' ? t('veh.fleet.report.making') : t('veh.fleet.report.pdf')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void download('xlsx')}><FileSpreadsheet aria-hidden="true" />{busy === 'xlsx' ? t('veh.fleet.report.making') : t('veh.fleet.report.xlsx')}</button>
      </div>
    </form>
    {error && <Notice error>{error}</Notice>}
    {saved && <Notice>{t('veh.fleet.report.saved', { name: saved })}</Notice>}
    <p className="muted vehNote">{t('veh.fleet.report.never')}</p>
  </FinCard>;
}

// ---- Import --------------------------------------------------------------------------------------------------------------

const STATUS_TONE: Record<FleetRowStatus, 'ok' | 'info' | 'warn' | 'bad' | 'muted'> = { new: 'ok', exists: 'info', duplicate_in_file: 'muted', imported: 'muted', invalid: 'bad' };
interface Source { name: string; format: api.ImportFormat; content: string }

export function FleetImportPanel({ onDone }: { onDone: () => void }) {
  const owner = useOwner();
  const fileId = useId();
  const [kind, setKind] = useState<FleetImportKind>('vehicles');
  const [country, setCountry] = useState<'AE' | 'SA'>('AE');
  const [dateOrder, setDateOrder] = useState<'dmy' | 'mdy'>('dmy');
  const [file, setFile] = useState<Source | null>(null);
  const [preview, setPreview] = useState<FleetImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [choices, setChoices] = useState<Record<number, FleetRowAction>>({});
  const [status, setStatus] = useState<FleetRowStatus | ''>('');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(api.IMPORT_PAGE);
  const [busy, setBusy] = useState<'reading' | 'checking' | 'importing' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<FleetImportResult | null>(null);
  const input = useRef<HTMLInputElement>(null);
  if (owner === null) return <Notice busy>{t('veh.loading')}</Notice>;
  if (!owner) return <section className="surface cmPanel cmDenied" aria-labelledby={`${fileId}-denied`}><div><h2 id={`${fileId}-denied`}>{t('veh.fleet.import.ownersTitle')}</h2>
    <p className="muted">{t('veh.fleet.import.ownersOnly')}</p></div></section>;

  const body = (source: Source, chosen: Record<string, string> | null) => ({
    kind, format: source.format, ...(source.format === 'xlsx' ? { file_base64: source.content } : { csv: source.content }), file_name: source.name,
    mapping: chosen, country, date_order: dateOrder,
  });
  const check = async (source: Source, chosen: Record<string, string> | null) => {
    setBusy(chosen ? 'checking' : 'reading'); setError(null); setResult(null);
    try {
      const next = await fleetApi.previewImport(body(source, chosen));
      setPreview(next); setMapping(next.mapping); setChoices({}); setShown(api.IMPORT_PAGE);
    } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const choose = async (picked: File | undefined) => {
    setError(null); setResult(null); setPreview(null);
    if (!picked) return;
    const problem = api.importFileProblem(picked);
    if (problem) { setError(problem); return; }
    const format = api.importFormatOf(picked) ?? 'csv';
    setBusy('reading');
    let content: string;
    try { content = format === 'xlsx' ? bytesToBase64(new Uint8Array(await picked.arrayBuffer())) : await picked.text(); }
    catch (e) { setBusy(null); setError(t('veh.fleet.err.read', { error: describeError(e) })); return; }
    const source = { name: picked.name, format, content };
    setFile(source);
    await check(source, null);
  };
  const reset = () => { setFile(null); setPreview(null); setError(null); setChoices({}); setMapping({}); if (input.current) input.current.value = ''; };
  const decisions = preview ? api.importDecisions(preview.rows, choices) : [];
  const totals = api.importTotals(decisions);
  const rows = preview ? api.importRowsShown(preview.rows, status, query) : [];
  const run = async () => {
    if (!preview || !file) return;
    setBusy('importing'); setError(null);
    try {
      const done = await fleetApi.applyImport({ ...body(file, mapping), mapping, fingerprint: preview.fingerprint,
        decisions: decisions.filter((d) => d.action !== preview.rows.find((r) => r.row === d.row)?.action) });
      setConfirming(false); setResult(done); setPreview(null); setFile(null); onDone();
    } catch (e) { setConfirming(false); setError(describeError(e)); } finally { setBusy(null); }
  };
  const settingsLocked = busy !== null || file !== null;
  return <section className="surface cmPanel vehImport" aria-labelledby={`${fileId}-title`}>
    <h2 id={`${fileId}-title`}>{t('veh.fleet.import.title')}</h2>
    <p className="muted">{t('veh.fleet.import.intro')}</p>
    <p className="muted">{t('veh.fleet.import.limits', { rows: formatNumber(api.IMPORT_MAX_ROWS) })}</p>
    <fieldset className="vehImportSettings" disabled={settingsLocked}>
      <legend>{t('veh.fleet.import.settings')}</legend>
      <label className="field"><span>{t('veh.fleet.import.kind')}</span>
        <select value={kind} onChange={(e) => setKind(e.target.value as FleetImportKind)}>{api.FLEET_KINDS.map((k) => <option key={k} value={k}>{api.kindLabel(k)}</option>)}</select></label>
      <label className="field"><span>{t('veh.fleet.import.country')}</span>
        <select value={country} onChange={(e) => setCountry(e.target.value as 'AE' | 'SA')}><option value="AE">{t('veh.country.AE')}</option><option value="SA">{t('veh.country.SA')}</option></select></label>
      <label className="field"><span>{t('veh.fleet.import.dates')}</span>
        <select value={dateOrder} onChange={(e) => setDateOrder(e.target.value as 'dmy' | 'mdy')}><option value="dmy">{t('veh.fleet.import.dmy')}</option><option value="mdy">{t('veh.fleet.import.mdy')}</option></select></label>
    </fieldset>
    <p className="muted vehNote">{t(`veh.fleet.import.hint.${kind}` as MessageKey)}{' '}
      <button type="button" className="cmLinkButton" onClick={() => fleetApi.saveTemplate(kind)}>{t('veh.fleet.import.template', { kind: api.kindLabel(kind) })}</button></p>
    {!file && <label className="field vehImportFile" htmlFor={fileId}><span>{t('veh.fleet.import.choose')}</span>
      <input id={fileId} ref={input} type="file" accept=".csv,.txt,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" disabled={busy !== null}
        onChange={(e) => void choose(e.target.files?.[0])} /></label>}
    {file && <div className="actions vehImportFileName"><span dir="auto"><FileUp aria-hidden="true" />{t('veh.fleet.import.file', { name: file.name })}</span>
      <button type="button" className="secondary" disabled={busy !== null} onClick={reset}>{t('veh.fleet.import.another')}</button></div>}
    {busy === 'reading' && <Notice busy>{t('veh.fleet.import.reading')}</Notice>}
    {error && <Notice error>{error}</Notice>}
    {result && <div className="vehImportDone">
      <Notice>{t('veh.fleet.import.done', { created: result.created, updated: result.updated, skipped: result.skipped, readings: result.readings })}</Notice>
      <div className="actions"><button type="button" className="secondary" onClick={reset}>{t('veh.fleet.import.another')}</button></div>
    </div>}
    {preview && file && <>
      <fieldset className="vehImportMapping" disabled={busy !== null}>
        <legend>{t('veh.fleet.import.mapping')}</legend>
        <p className="muted">{preview.rows.length ? t('veh.fleet.import.mappingIntro') : t('veh.fleet.import.mappingNone')}</p>
        <div className="vehImportSlots">
          {preview.fields.map(({ field, required }) => <label key={field} className="field"><span>{api.fieldLabel(field)}{required ? ` (${t('veh.fleet.import.required')})` : ''}</span>
            <select value={mapping[field] ?? ''} onChange={(e) => setMapping((current) => { const next = { ...current }; if (e.target.value) next[field] = e.target.value; else delete next[field]; return next; })}>
              <option value="">{t('veh.fleet.import.none')}</option>
              {preview.columns.filter(Boolean).map((column) => <option key={column} value={column}>{column}</option>)}
            </select></label>)}
        </div>
        <div className="actions"><button type="button" className="secondary" onClick={() => void check(file, mapping)}>
          <RefreshCw aria-hidden="true" />{busy === 'checking' ? t('veh.fleet.import.checking') : t('veh.fleet.import.check')}</button></div>
      </fieldset>
      {preview.rows.length > 0 && <>
        <h3>{t('veh.fleet.import.summary', { rows: formatNumber(preview.counts.rows) })}</h3>
        <ul className="vehImportCounts">{api.FLEET_STATUSES.map((s) => <li key={s}><StatusChip tone={STATUS_TONE[s]}>{api.statusLabel(s)}: <bdi>{formatNumber(preview.counts[s])}</bdi></StatusChip></li>)}</ul>
        <form className="opsFilters" role="search" onSubmit={(e) => e.preventDefault()}>
          <label className="opsSearch"><span className="visuallyHidden">{t('veh.fleet.import.search')}</span><Search aria-hidden="true" />
            <input type="search" dir="auto" value={query} placeholder={t('veh.fleet.import.search')} onChange={(e) => { setQuery(e.target.value); setShown(api.IMPORT_PAGE); }} /></label>
          <label className="opsSelect"><span className="visuallyHidden">{t('veh.fleet.import.filter')}</span>
            <select value={status} onChange={(e) => { setStatus(e.target.value as FleetRowStatus | ''); setShown(api.IMPORT_PAGE); }}>
              <option value="">{t('veh.fleet.import.allRows')}</option>{api.FLEET_STATUSES.map((s) => <option key={s} value={s}>{api.statusLabel(s)}</option>)}</select></label>
        </form>
        {rows.length === 0 ? <p className="muted">{t('veh.fleet.import.noMatch')}</p>
          : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('veh.fleet.import.rowsLabel')}><table className="finGrid vehImportTable">
            <caption className="visuallyHidden">{t('veh.fleet.import.rowsLabel')}</caption>
            <thead><tr><th scope="col">{t('veh.fleet.import.col.row')}</th><th scope="col">{t('veh.fleet.import.col.what')}</th><th scope="col">{t('veh.fleet.import.col.check')}</th>
              <th scope="col">{t('veh.fleet.import.col.action')}</th></tr></thead>
            <tbody>{rows.slice(0, shown).map((row) => {
              const action = choices[row.row] && row.actions.includes(choices[row.row]) ? choices[row.row] : row.action;
              return <tr key={row.row}>
                <td><bdi>{formatNumber(row.row)}</bdi></td>
                <td><strong><bdi>{row.registration || '-'}</bdi></strong>
                  <small dir="auto">{Object.entries(row.summary).filter(([, v]) => v).map(([k, v]) => `${api.fieldLabel(k)}: ${v}`).join(' · ') || ' '}</small></td>
                <td><StatusChip tone={STATUS_TONE[row.status]}>{api.statusLabel(row.status)}</StatusChip>
                  {api.rowIssues(row).map((problem) => <small key={problem} className="vehImportProblem" dir="auto">{problem}</small>)}
                  {row.duplicate_of !== null && <small>{t('veh.fleet.import.repeats', { row: row.duplicate_of })}</small>}</td>
                <td><label className="field vehImportChoice"><span className="visuallyHidden">{t('veh.fleet.import.actionFor', { row: row.row })}</span>
                  <select value={action} disabled={busy !== null || row.actions.length < 2} onChange={(e) => setChoices((c) => ({ ...c, [row.row]: e.target.value as FleetRowAction }))}>
                    {row.actions.map((option) => <option key={option} value={option}>{api.actionLabel(option)}</option>)}</select></label></td>
              </tr>;
            })}</tbody>
          </table></div>}
        {rows.length > shown && <div className="vehPager"><span>{t('veh.fleet.showing', { shown: formatNumber(shown), total: formatNumber(rows.length) })}</span>
          <button type="button" className="secondary" onClick={() => setShown((n) => n + api.IMPORT_PAGE)}>{t('veh.fleet.more')}</button></div>}
        <div className="actions vehImportApply">
          <span>{t('veh.fleet.import.totals', { create: totals.create, update: totals.update, skip: totals.skip })}</span>
          <button type="button" disabled={busy !== null || totals.create + totals.update === 0} onClick={() => setConfirming(true)}><Upload aria-hidden="true" />{t('veh.fleet.import.apply')}</button>
        </div>
        {totals.create + totals.update === 0 && <small className="muted">{t('veh.fleet.import.nothing')}</small>}
      </>}
    </>}
    {confirming && <ConfirmDialog title={t('veh.fleet.import.confirmTitle')} confirmLabel={busy === 'importing' ? t('veh.fleet.import.applying') : t('veh.fleet.import.apply')} busy={busy === 'importing'}
      onConfirm={() => void run()} onCancel={() => setConfirming(false)}>
      <p>{t('veh.fleet.import.confirmText', { create: totals.create, update: totals.update, skip: totals.skip, kind: api.kindLabel(kind) })}</p>
    </ConfirmDialog>}
  </section>;
}


