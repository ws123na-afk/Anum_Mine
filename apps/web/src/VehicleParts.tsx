// One vehicle's page and the forms of Vehicles (docs/vehicles.md#web): the vehicle form, the record lists (fuel, trips,
// service) with their forms, the odometer with corrections, the report and the payment picker. A payment is always one of
// the person's own Finance transactions (or a cash payment recorded once in Finance): linking never adds money.
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Archive, ArchiveRestore, CarFront, Download, Fuel, Gauge, Link2, Pencil, Plus, Route, Trash2, Unlink, Wrench } from 'lucide-react';
import type {
  FinanceAccount, Vehicle, VehicleCostKind, VehicleFuelEntry, VehicleFuelType, VehicleOdometerReading, VehiclePage, VehiclePayment,
  VehiclePaymentCandidate, VehicleRecordKind, VehicleReport, VehicleServiceKind, VehicleServiceRecord, VehicleTrip, VehicleTripPurpose,
  VehicleType,
} from '@anum/contracts';
import { VEHICLE_FUEL_TYPES, VEHICLE_PAYMENT_METHODS, VEHICLE_SERVICE_KINDS, VEHICLE_TYPES } from '@anum/contracts';
import * as api from './lib/vehiclesApi';
import {
  PAGE_SIZE, VEHICLE_DETAIL_TABS, anomalyLabel, decimal, dueLabel, dueText, fromLocalInput, fuelLabel, km, litres, localInput, methodLabel,
  moneyList, newKey, paymentState, paymentText, purposeLabel, serviceLabel, today, typeLabel, vehicleName, type VehicleDetailTab,
} from './lib/vehicles';
import { formatAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { ConfirmDialog, Modal } from './Dialog';
import { Empty, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { PageSummary } from './PageParts';
import { Pager } from './VehiclesView';

type Message = { text: string; error?: boolean } | null;

// ---- The vehicle form ----------------------------------------------------------------------------------

export function VehicleForm({ vehicle, onClose, onSaved }: { vehicle?: Vehicle; onClose: () => void; onSaved: (v: Vehicle) => void }) {
  const [f, setF] = useState(() => ({
    ownership: vehicle?.ownership ?? 'personal', registration: vehicle?.registration ?? '', vin: vehicle && !vehicle.vin_masked ? vehicle.vin ?? '' : '',
    nickname: vehicle?.nickname ?? '', make: vehicle?.make ?? '', model: vehicle?.model ?? '', year: vehicle?.year ? String(vehicle.year) : '',
    vehicle_type: vehicle?.vehicle_type ?? 'car', country: vehicle?.country ?? 'SA', fuel_type: vehicle?.fuel_type ?? 'petrol_91',
    tank_litres: vehicle?.tank_litres ?? '', odometer_km: '', acquisition_date: vehicle?.acquisition_date ?? '', purchase_price: vehicle?.purchase_price ?? '',
    residual_value: vehicle?.residual_value ?? '', useful_life_months: vehicle?.useful_life_months ? String(vehicle.useful_life_months) : '',
    insurance_expires_on: vehicle?.insurance_expires_on ?? '', registration_expires_on: vehicle?.registration_expires_on ?? '',
    inspection_expires_on: vehicle?.inspection_expires_on ?? '', notes: vehicle?.notes ?? '',
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [key]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!f.registration.trim()) return setError(t('veh.form.needRegistration'));
    const numbers = { tank_litres: f.tank_litres ? decimal(f.tank_litres) : null, odometer_km: f.odometer_km ? decimal(f.odometer_km, 1) : null,
      purchase_price: f.purchase_price ? decimal(f.purchase_price) : null, residual_value: f.residual_value ? decimal(f.residual_value) : null };
    if (Object.entries(numbers).some(([key, value]) => (f as Record<string, string>)[key] && value === null)) return setError(t('veh.form.badNumber'));
    const body = {
      registration: f.registration.trim(), vin: f.vin.trim() || null, nickname: f.nickname.trim() || null, make: f.make.trim() || null, model: f.model.trim() || null,
      year: f.year ? Number(f.year) : null, vehicle_type: f.vehicle_type as VehicleType, country: f.country as 'SA' | 'AE', fuel_type: f.fuel_type as VehicleFuelType,
      tank_litres: numbers.tank_litres, acquisition_date: f.acquisition_date || null, purchase_price: numbers.purchase_price, residual_value: numbers.residual_value,
      useful_life_months: f.useful_life_months ? Number(f.useful_life_months) : null, insurance_expires_on: f.insurance_expires_on || null,
      registration_expires_on: f.registration_expires_on || null, inspection_expires_on: f.inspection_expires_on || null, notes: f.notes.trim() || null,
    };
    setBusy(true); setError(null);
    try {
      const saved = vehicle
        ? await api.updateVehicle(vehicle.id, { ...body, vin: f.vin.trim() ? f.vin.trim() : vehicle.vin_masked ? undefined : null, version: vehicle.version })
        : await api.createVehicle({ ...body, ownership: f.ownership as 'personal' | 'business', odometer_km: numbers.odometer_km });
      onSaved(saved);
    } catch (err) { setError(describeError(err)); } finally { setBusy(false); }
  };
  return <Modal title={vehicle ? t('veh.form.editTitle') : t('veh.form.addTitle')} className="vehDialog" onClose={onClose}>
    <form className="vehForm" noValidate onSubmit={(e) => void submit(e)} aria-describedby="veh-form-error">
      {!vehicle && <fieldset className="vehFieldset"><legend>{t('veh.form.ownership')}</legend>
        {(['personal', 'business'] as const).map((o) => <label key={o} className="vehCheck"><input type="radio" name="veh-own" checked={f.ownership === o} onChange={() => setF({ ...f, ownership: o })} />{t(`veh.own.${o}.long` as MessageKey)}</label>)}
      </fieldset>}
      <div className="vehTwo">
        <label className="field"><span>{t('veh.form.registration')}</span><input dir="auto" required maxLength={20} value={f.registration} onChange={set('registration')} /></label>
        <label className="field"><span>{t('veh.form.country')}</span><select value={f.country} onChange={set('country')}><option value="SA">{t('veh.country.SA')}</option><option value="AE">{t('veh.country.AE')}</option></select></label>
        <label className="field"><span>{t('veh.form.make')}</span><input dir="auto" maxLength={60} value={f.make} onChange={set('make')} /></label>
        <label className="field"><span>{t('veh.form.model')}</span><input dir="auto" maxLength={60} value={f.model} onChange={set('model')} /></label>
        <label className="field"><span>{t('veh.form.year')}</span><input inputMode="numeric" maxLength={4} value={f.year} onChange={set('year')} /></label>
        <label className="field"><span>{t('veh.form.type')}</span><select value={f.vehicle_type} onChange={set('vehicle_type')}>{VEHICLE_TYPES.map((v) => <option key={v} value={v}>{typeLabel(v)}</option>)}</select></label>
        <label className="field"><span>{t('veh.form.fuel')}</span><select value={f.fuel_type} onChange={set('fuel_type')}>{VEHICLE_FUEL_TYPES.map((v) => <option key={v} value={v}>{fuelLabel(v)}</option>)}</select></label>
        <label className="field"><span>{t('veh.form.tank')}</span><input inputMode="decimal" value={f.tank_litres} onChange={set('tank_litres')} /></label>
        <label className="field"><span>{t('veh.form.nickname')}</span><input dir="auto" maxLength={60} value={f.nickname} onChange={set('nickname')} /></label>
        <label className="field"><span>{t('veh.form.vin')}</span><input dir="ltr" maxLength={17} autoComplete="off" placeholder={vehicle?.vin_masked ? vehicle.vin ?? '' : ''} value={f.vin} onChange={set('vin')} /></label>
        {!vehicle && <label className="field"><span>{t('veh.form.odometer')}</span><input inputMode="decimal" value={f.odometer_km} onChange={set('odometer_km')} /></label>}
      </div>
      <fieldset className="vehFieldset"><legend>{t('veh.form.dates')}</legend><div className="vehTwo">
        <label className="field"><span>{t('veh.form.insurance')}</span><input type="date" value={f.insurance_expires_on} onChange={set('insurance_expires_on')} /></label>
        <label className="field"><span>{t('veh.form.registrationExpiry')}</span><input type="date" value={f.registration_expires_on} onChange={set('registration_expires_on')} /></label>
        <label className="field"><span>{t('veh.form.inspection')}</span><input type="date" value={f.inspection_expires_on} onChange={set('inspection_expires_on')} /></label>
      </div></fieldset>
      <fieldset className="vehFieldset"><legend>{t('veh.form.purchase')}</legend><p className="muted vehNote">{t('veh.form.purchaseHint')}</p><div className="vehTwo">
        <label className="field"><span>{t('veh.form.acquired')}</span><input type="date" max={today()} value={f.acquisition_date} onChange={set('acquisition_date')} /></label>
        <label className="field"><span>{t('veh.form.price')}</span><input inputMode="decimal" value={f.purchase_price} onChange={set('purchase_price')} /></label>
        <label className="field"><span>{t('veh.form.residual')}</span><input inputMode="decimal" value={f.residual_value} onChange={set('residual_value')} /></label>
        <label className="field"><span>{t('veh.form.life')}</span><input inputMode="numeric" value={f.useful_life_months} onChange={set('useful_life_months')} /></label>
      </div></fieldset>
      <label className="field"><span>{t('veh.form.notes')}</span><textarea dir="auto" maxLength={1000} rows={2} value={f.notes} onChange={set('notes')} /></label>
      <FormError id="veh-form-error" text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('veh.saving') : t('veh.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- One vehicle ---------------------------------------------------------------------------------------

const TAB_ICONS: Record<VehicleDetailTab, ReactNode> = {
  fuel: <Fuel aria-hidden="true" />, trips: <Route aria-hidden="true" />, service: <Wrench aria-hidden="true" />, odometer: <Gauge aria-hidden="true" />,
  report: <CarFront aria-hidden="true" />, details: <Pencil aria-hidden="true" />,
};

export function VehicleDetail({ vehicleId, onBack, onDeleted }: { vehicleId: string; onBack: () => void; onDeleted: () => void }) {
  const [vehicle, setVehicle] = useState<Load<Vehicle>>({ kind: 'loading' });
  const [report, setReport] = useState<Load<VehicleReport>>({ kind: 'loading' });
  const [tab, setTab] = useState<VehicleDetailTab>('fuel');
  const [tick, setTick] = useState(0);
  const load = useCallback(async () => {
    const [v, r] = await Promise.allSettled([api.getVehicle(vehicleId), api.report(vehicleId)]);
    setVehicle(v.status === 'fulfilled' ? { kind: 'ready', value: v.value } : failed(v.reason));
    setReport(r.status === 'fulfilled' ? { kind: 'ready', value: r.value } : failed(r.reason));
  }, [vehicleId]);
  useEffect(() => { void load(); }, [load, tick]);
  const changed = () => setTick((n) => n + 1);
  const back = <button type="button" className="secondary vehBack" onClick={onBack}>{t('veh.back')}</button>;
  if (vehicle.kind === 'loading') return <Notice busy>{t('veh.loading')}</Notice>;
  if (vehicle.kind === 'denied' || vehicle.kind === 'error') return <><LoadError message={t('veh.error.vehicle', { message: vehicle.message })} retry={changed} />{back}</>;
  if (vehicle.kind !== 'ready') return null;
  const v = vehicle.value;
  const r = report.kind === 'ready' ? report.value : null;
  const state = report.kind === 'ready' ? 'ready' : report.kind === 'loading' ? 'loading' : 'error';
  const figures: Figure[] = [
    figure('odometer', t('veh.fig.odometer'), { state: 'ready' }, () => ({ value: km(v.odometer_km), hint: t('veh.fig.odometerHint') })),
    figure('distance', t('veh.fig.distance'), { state }, () => ({ value: km(r!.distance_km), hint: t('veh.fig.distanceHint') })),
    figure('spend', t('veh.fig.spend'), { state }, () => ({ value: moneyList(r!.spend), hint: r!.unlinked_records ? t('veh.fig.waiting', { count: r!.unlinked_records }) : t('veh.fig.spendHint') })),
    figure('consumption', t('veh.fig.consumption'), { state }, () => ({ value: r!.consumption.average_per_100 ? t('veh.per100', { value: r!.consumption.average_per_100 }) : '-', hint: t('veh.fig.consumptionHint') })),
  ];
  return <div className="vehDetail">
    <div className="vehDetailHead">
      {back}
      <div><h2><bdi>{v.registration}</bdi> · <span dir="auto">{vehicleName(v)}</span></h2>
        <p className="muted">{[t(`veh.own.${v.ownership}` as MessageKey), t(`veh.country.${v.country}` as MessageKey), fuelLabel(v.fuel_type), v.archived_at ? t('veh.archived') : ''].filter(Boolean).join(' · ')}</p></div>
    </div>
    {v.archived_at && <Notice>{t('veh.archivedNotice')}</Notice>}
    {v.due.length > 0 && <ul className="vehDue" aria-label={t('veh.due.title')}>{v.due.map((d) => <li key={`${d.kind}:${d.source_id}`} className={d.days_left !== null && d.days_left < 8 ? 'soon' : ''}>{dueLabel(d)}: {dueText(d)}</li>)}</ul>}
    <PageSummary label={t('veh.detail.summary')} figures={figures} onRetry={changed} footer={<p className="muted vehNote">{t('veh.noPosting')}</p>} />
    <nav className="settingsTabs cmTabs" aria-label={t('veh.detail.tabs')}>
      {VEHICLE_DETAIL_TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>{TAB_ICONS[id]}{t(`veh.dtab.${id}` as MessageKey)}</button>)}
    </nav>
    {(tab === 'fuel' || tab === 'trips' || tab === 'service') && <RecordsPanel key={tab} kind={tab} vehicle={v} onChanged={changed} />}
    {tab === 'odometer' && <OdometerPanel vehicle={v} onChanged={changed} />}
    {tab === 'report' && (report.kind === 'error' || report.kind === 'denied' ? <LoadError message={t('veh.error.report', { message: report.message })} retry={changed} />
      : r ? <ReportPanel report={r} /> : <Notice busy>{t('veh.loading')}</Notice>)}
    {tab === 'details' && <DetailsPanel vehicle={v} onChanged={changed} onDeleted={onDeleted} />}
  </div>;
}

// ---- Records: fuel, trips and service ----------------------------------------------------------------

type AnyRecord = VehicleFuelEntry | VehicleTrip | VehicleServiceRecord;
const COST_KIND: Record<VehicleRecordKind, VehicleCostKind | null> = { fuel: 'fuel_entry', service: 'service_record', trips: null };

function RecordsPanel({ kind, vehicle, onChanged }: { kind: VehicleRecordKind; vehicle: Vehicle; onChanged: () => void }) {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [run, setRun] = useState(0);
  const [page, setPage] = useState<Load<VehiclePage<AnyRecord>>>({ kind: 'loading' });
  const [editing, setEditing] = useState<AnyRecord | 'new' | null>(null);
  const [paying, setPaying] = useState<AnyRecord | null>(null);
  const [removing, setRemoving] = useState<AnyRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const load = useCallback(async () => {
    setPage((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setPage({ kind: 'ready', value: await api.listRecords(kind, vehicle.id, search, offset, PAGE_SIZE) as VehiclePage<AnyRecord> }); } catch (e) { setPage(failed(e)); }
  }, [kind, vehicle.id, search, offset, run]);
  useEffect(() => { void load(); }, [load]);
  const done = (text: string) => { setMessage({ text }); setEditing(null); setPaying(null); void load(); onChanged(); };
  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteRecord(kind, removing.id); setRemoving(null); done(t('veh.rec.deleted')); }
    catch (e) { setRemoving(null); setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const title = t(`veh.rec.${kind}.title` as MessageKey);
  const canAdd = !vehicle.archived_at;
  return <section className="surface cmPanel" aria-labelledby={`veh-${kind}-title`}>
    <div className="vehBar"><h2 id={`veh-${kind}-title`}>{title}</h2>
      {canAdd && <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t(`veh.rec.${kind}.add` as MessageKey)}</button>}</div>
    {kind !== 'trips' && <p className="muted vehNote">{t('veh.rec.moneyNote')}</p>}
    <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(q.trim()); setRun((n) => n + 1); }}>
      <label className="cmInline cmSearch"><span>{t('veh.rec.search')}</span><input type="search" dir="auto" value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} /></label>
      <button type="submit" className="secondary">{t('veh.list.find')}</button>
    </form>
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {page.kind === 'loading' && <Notice busy>{t('veh.loading')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('veh.error.records', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && (search ? <p className="muted">{t('veh.list.noMatch', { q: search })}</p>
      : <Empty icon={kind === 'fuel' ? <Fuel aria-hidden="true" /> : kind === 'trips' ? <Route aria-hidden="true" /> : <Wrench aria-hidden="true" />}
        title={t(`veh.rec.${kind}.empty` as MessageKey)} text={t(`veh.rec.${kind}.emptyText` as MessageKey)}
        action={canAdd ? <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t(`veh.rec.${kind}.add` as MessageKey)}</button> : undefined} />)}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <ul className="cmList vehRecords">{page.value.items.map((record) => <li key={record.id}>
        <RecordRow kind={kind} record={record} />
        <div className="vehRowButtons">
          {COST_KIND[kind] && record.mine && <button type="button" className="secondary" onClick={() => setPaying(record)}
            aria-label={t('veh.pay.manageLabel', { what: rowTitle(kind, record) })}><Link2 aria-hidden="true" />{t('veh.pay.manage')}</button>}
          {record.can_edit && <button type="button" className="secondary" aria-label={t('veh.rec.editLabel', { what: rowTitle(kind, record) })} onClick={() => setEditing(record)}><Pencil aria-hidden="true" />{t('veh.rec.edit')}</button>}
          {record.can_edit && <button type="button" className="secondary danger" aria-label={t('veh.rec.deleteLabel', { what: rowTitle(kind, record) })} onClick={() => setRemoving(record)}><Trash2 aria-hidden="true" />{t('veh.rec.delete')}</button>}
        </div>
      </li>)}</ul>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
    {editing && <RecordForm kind={kind} vehicle={vehicle} record={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => done(t('veh.rec.saved'))} />}
    {paying && COST_KIND[kind] && <PaymentDialog kind={COST_KIND[kind]!} recordId={paying.id} payment={'payment' in paying ? paying.payment : null}
      hint={{ day: kind === 'fuel' ? (paying as VehicleFuelEntry).filled_at.slice(0, 10) : (paying as VehicleServiceRecord).service_on,
        amount: kind === 'fuel' ? (paying as VehicleFuelEntry).total : (paying as VehicleServiceRecord).receipt_total, currency: (paying as VehicleFuelEntry).currency }}
      onClose={() => setPaying(null)} onDone={(text) => done(text)} />}
    {removing && <ConfirmDialog title={t('veh.rec.deleteTitle')} confirmLabel={t('veh.rec.delete')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('veh.rec.deleteText', { what: rowTitle(kind, removing) })}</p>
      {'payment' in removing && removing.payment && removing.payment.status !== 'void' && <p className="muted">{t('veh.rec.deletePaid')}</p>}
    </ConfirmDialog>}
  </section>;
}

function rowTitle(kind: VehicleRecordKind, record: AnyRecord): string {
  if (kind === 'fuel') { const r = record as VehicleFuelEntry; return `${formatDateTime(r.filled_at)} ${r.station ?? ''}`.trim(); }
  if (kind === 'trips') { const r = record as VehicleTrip; return formatDateTime(r.started_at); }
  const r = record as VehicleServiceRecord; return `${serviceLabel(r.kind)} ${r.service_on}`;
}

function PaymentChip({ payment }: { payment: VehiclePayment | null }) {
  const state = paymentState(payment);
  return <span className={`vehChip ${state}`}>{paymentText(payment)}</span>;
}

function RecordRow({ kind, record }: { kind: VehicleRecordKind; record: AnyRecord }) {
  if (kind === 'fuel') {
    const r = record as VehicleFuelEntry;
    return <div><strong dir="auto">{r.station || t('veh.rec.fuel.noStation')} · {litres(r.litres)}{r.full_tank ? '' : ` · ${t('veh.rec.partial')}`}</strong>
      <small>{[formatDateTime(r.filled_at), km(r.odometer_km), r.total ? t('veh.rec.pump', { amount: formatAmount(r.total, r.currency) }) : '', methodLabel(r.payment_method)].filter((x) => x && x !== '-').join(' · ')}</small>
      <PaymentChip payment={r.payment} /></div>;
  }
  if (kind === 'trips') {
    const r = record as VehicleTrip;
    return <div><strong>{purposeLabel(r.purpose)} · {r.status === 'open' ? t('veh.rec.trips.open') : km(r.distance_km)}</strong>
      <small>{[formatDateTime(r.started_at), r.ended_at ? formatDateTime(r.ended_at) : '', r.distance_basis === 'previous_reading' ? t('veh.rec.trips.fromLast') : '',
        r.source === 'device' ? t('veh.rec.trips.iphone') : '', r.purpose_note ?? '', r.origin && r.destination ? `${r.origin} ${t('veh.rec.trips.to')} ${r.destination}` : ''].filter(Boolean).join(' · ')}</small></div>;
  }
  const r = record as VehicleServiceRecord;
  return <div><strong dir="auto">{serviceLabel(r.kind)}{r.workshop ? ` · ${r.workshop}` : ''}</strong>
    <small>{[r.service_on, km(r.odometer_km), r.receipt_total ? t('veh.rec.receipt', { amount: formatAmount(r.receipt_total, r.currency) }) : '',
      r.next_due_on || r.next_due_km ? t('veh.rec.next', { when: [r.next_due_on, r.next_due_km ? km(r.next_due_km) : ''].filter(Boolean).join(' / ') }) : '',
      r.reference ?? ''].filter((x) => x && x !== '-').join(' · ')}</small>
    <PaymentChip payment={r.payment} /></div>;
}

function RecordForm({ kind, vehicle, record, onClose, onSaved }: { kind: VehicleRecordKind; vehicle: Vehicle; record: AnyRecord | null; onClose: () => void; onSaved: () => void }) {
  const fuel = record as VehicleFuelEntry | null, trip = record as VehicleTrip | null, svc = record as VehicleServiceRecord | null;
  const [f, setF] = useState(() => ({
    filled_at: fuel?.filled_at ? localInput(new Date(fuel.filled_at)) : localInput(), station: fuel?.station ?? '', fuel_type: fuel?.fuel_type ?? vehicle.fuel_type,
    litres: fuel?.litres ?? '', total: fuel?.total ?? '', price_per_litre: '', odometer_km: (fuel ?? svc)?.odometer_km ?? '', full_tank: fuel ? fuel.full_tank : true,
    payment_method: (fuel ?? svc)?.payment_method ?? '', notes: (fuel ?? trip)?.notes ?? '',
    started_at: trip?.started_at ? localInput(new Date(trip.started_at)) : localInput(), ended_at: trip?.ended_at ? localInput(new Date(trip.ended_at)) : '',
    start_odometer_km: trip?.start_odometer_km ?? '', end_odometer_km: trip?.end_odometer_km ?? '', purpose: trip?.purpose ?? 'business', purpose_note: trip?.purpose_note ?? '',
    project_id: trip?.project_id ?? '', origin: trip?.origin ?? '', destination: trip?.destination ?? '',
    kind: svc?.kind ?? 'maintenance', service_on: svc?.service_on ?? today(), workshop: svc?.workshop ?? '', description: svc?.description ?? '', reference: svc?.reference ?? '',
    next_due_on: svc?.next_due_on ?? '', next_due_km: svc?.next_due_km ?? '', warranty_until: svc?.warranty_until ?? '', receipt_total: svc?.receipt_total ?? '',
  }));
  const [key] = useState(() => newKey(kind));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (name: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [name]: e.target.value });
  const num = (value: string, places = 2) => (value.trim() ? decimal(value, places) : null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    let body: Record<string, unknown>;
    if (kind === 'fuel') {
      const l = num(f.litres, 3);
      if (!l) return setError(t('veh.form.needLitres'));
      const at = fromLocalInput(f.filled_at);
      if (!at) return setError(t('veh.form.needTime'));
      body = { filled_at: at, station: f.station.trim() || null, fuel_type: f.fuel_type, litres: l, total: num(f.total), odometer_km: num(f.odometer_km, 1),
        full_tank: f.full_tank, payment_method: f.payment_method || null, notes: f.notes.trim() || null };
    } else if (kind === 'trips') {
      const start = fromLocalInput(f.started_at);
      if (!start) return setError(t('veh.form.needTime'));
      body = { started_at: start, ended_at: fromLocalInput(f.ended_at), start_odometer_km: num(f.start_odometer_km, 1), end_odometer_km: num(f.end_odometer_km, 1),
        purpose: f.purpose, purpose_note: f.purpose_note.trim() || null, project_id: f.project_id.trim() || null, origin: f.origin.trim() || null,
        destination: f.destination.trim() || null, notes: f.notes.trim() || null };
    } else {
      body = { kind: f.kind, service_on: f.service_on, odometer_km: num(f.odometer_km, 1), workshop: f.workshop.trim() || null, description: f.description.trim() || null,
        reference: f.reference.trim() || null, next_due_on: f.next_due_on || null, next_due_km: num(f.next_due_km, 1), warranty_until: f.warranty_until || null,
        receipt_total: num(f.receipt_total), payment_method: f.payment_method || null };
    }
    setBusy(true);
    try {
      if (record) await api.updateRecord(kind, record.id, { ...body, version: record.version } as never);
      else await api.createRecord(kind, vehicle.id, body as never, key);
      onSaved();
    } catch (err) { setError(describeError(err)); } finally { setBusy(false); }
  };
  const methods = <label className="field"><span>{t('veh.form.method')}</span><select value={f.payment_method} onChange={set('payment_method')}>
    <option value="">-</option>{VEHICLE_PAYMENT_METHODS.map((m) => <option key={m} value={m}>{methodLabel(m)}</option>)}</select></label>;
  return <Modal title={t(`veh.rec.${kind}.${record ? 'editTitle' : 'addTitle'}` as MessageKey)} className="vehDialog" onClose={onClose}>
    <form className="vehForm" noValidate onSubmit={(e) => void submit(e)}>
      {kind === 'fuel' && <div className="vehTwo">
        <label className="field"><span>{t('veh.form.filledAt')}</span><input type="datetime-local" max={localInput()} value={f.filled_at} onChange={set('filled_at')} /></label>
        <label className="field"><span>{t('veh.form.station')}</span><input dir="auto" maxLength={120} value={f.station} onChange={set('station')} /></label>
        <label className="field"><span>{t('veh.form.litres')}</span><input inputMode="decimal" required value={f.litres} onChange={set('litres')} /></label>
        <label className="field"><span>{t('veh.form.pumpTotal', { currency: vehicle.currency })}</span><input inputMode="decimal" value={f.total} onChange={set('total')} /></label>
        <label className="field"><span>{t('veh.form.odometerNow')}</span><input inputMode="decimal" value={f.odometer_km} onChange={set('odometer_km')} /></label>
        <label className="field"><span>{t('veh.form.fuel')}</span><select value={f.fuel_type} onChange={set('fuel_type')}>{VEHICLE_FUEL_TYPES.map((v) => <option key={v} value={v}>{fuelLabel(v)}</option>)}</select></label>
        {methods}
        <label className="vehCheck"><input type="checkbox" checked={f.full_tank} onChange={(e) => setF({ ...f, full_tank: e.target.checked })} />{t('veh.form.fullTank')}</label>
      </div>}
      {kind === 'trips' && <div className="vehTwo">
        <label className="field"><span>{t('veh.form.started')}</span><input type="datetime-local" max={localInput()} value={f.started_at} onChange={set('started_at')} /></label>
        <label className="field"><span>{t('veh.form.ended')}</span><input type="datetime-local" max={localInput()} value={f.ended_at} onChange={set('ended_at')} /></label>
        <label className="field"><span>{t('veh.form.startKm')}</span><input inputMode="decimal" value={f.start_odometer_km} onChange={set('start_odometer_km')} /></label>
        <label className="field"><span>{t('veh.form.endKm')}</span><input inputMode="decimal" value={f.end_odometer_km} onChange={set('end_odometer_km')} /></label>
        <label className="field"><span>{t('veh.form.purpose')}</span><select value={f.purpose} onChange={set('purpose')}>{(['business', 'personal', 'commute'] as VehicleTripPurpose[]).map((p) => <option key={p} value={p}>{purposeLabel(p)}</option>)}</select></label>
        <label className="field"><span>{t('veh.form.project')}</span><input dir="auto" maxLength={80} value={f.project_id} onChange={set('project_id')} /></label>
        <label className="field"><span>{t('veh.form.purposeNote')}</span><input dir="auto" maxLength={200} value={f.purpose_note} onChange={set('purpose_note')} /></label>
        <label className="field"><span>{t('veh.form.origin')}</span><input dir="auto" maxLength={120} value={f.origin} onChange={set('origin')} /></label>
        <label className="field"><span>{t('veh.form.destination')}</span><input dir="auto" maxLength={120} value={f.destination} onChange={set('destination')} /></label>
      </div>}
      {kind === 'trips' && <p className="muted vehNote">{t('veh.form.placesNote')}</p>}
      {kind === 'service' && <div className="vehTwo">
        <label className="field"><span>{t('veh.form.kind')}</span><select value={f.kind} onChange={set('kind')}>{VEHICLE_SERVICE_KINDS.map((k) => <option key={k} value={k}>{serviceLabel(k as VehicleServiceKind)}</option>)}</select></label>
        <label className="field"><span>{t('veh.form.serviceOn')}</span><input type="date" max={today()} value={f.service_on} onChange={set('service_on')} /></label>
        <label className="field"><span>{t('veh.form.workshop')}</span><input dir="auto" maxLength={120} value={f.workshop} onChange={set('workshop')} /></label>
        <label className="field"><span>{t('veh.form.odometerNow')}</span><input inputMode="decimal" value={f.odometer_km} onChange={set('odometer_km')} /></label>
        <label className="field"><span>{t('veh.form.receipt', { currency: vehicle.currency })}</span><input inputMode="decimal" value={f.receipt_total} onChange={set('receipt_total')} /></label>
        <label className="field"><span>{t('veh.form.reference')}</span><input dir="auto" maxLength={60} value={f.reference} onChange={set('reference')} /></label>
        <label className="field"><span>{t('veh.form.nextOn')}</span><input type="date" value={f.next_due_on} onChange={set('next_due_on')} /></label>
        <label className="field"><span>{t('veh.form.nextKm')}</span><input inputMode="decimal" value={f.next_due_km} onChange={set('next_due_km')} /></label>
        <label className="field"><span>{t('veh.form.warranty')}</span><input type="date" value={f.warranty_until} onChange={set('warranty_until')} /></label>
        {methods}
      </div>}
      {kind === 'service' && <label className="field"><span>{t('veh.form.description')}</span><textarea dir="auto" maxLength={500} rows={2} value={f.description} onChange={set('description')} /></label>}
      {kind !== 'service' && <label className="field"><span>{t('veh.form.notes')}</span><textarea dir="auto" maxLength={500} rows={2} value={f.notes} onChange={set('notes')} /></label>}
      <FormError id="veh-rec-error" text={error} />
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('veh.saving') : t('veh.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- The payment picker --------------------------------------------------------------------------------

export function PaymentDialog({ kind, recordId, payment, hint, onClose, onDone }: {
  kind: VehicleCostKind; recordId: string; payment: VehiclePayment | null; hint: { day: string | null; amount: string | null; currency: string };
  onClose: () => void; onDone: (text: string) => void;
}) {
  const [mode, setMode] = useState<'link' | 'cash'>('link');
  const [q, setQ] = useState('');
  const [list, setList] = useState<Load<VehiclePaymentCandidate[]>>({ kind: 'loading' });
  const [accounts, setAccounts] = useState<Load<FinanceAccount[]>>({ kind: 'loading' });
  const [cash, setCash] = useState({ account_id: '', amount: hint.amount ?? '', posted_on: hint.day ?? today() });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key] = useState(() => newKey('pay'));
  const search = useCallback(async (text: string) => {
    setList({ kind: 'loading' });
    try { setList({ kind: 'ready', value: (await api.paymentCandidates(text, hint.day, hint.amount)).items }); } catch (e) { setList(failed(e)); }
  }, [hint.day, hint.amount]);
  useEffect(() => { if (!payment || payment.status === 'void') void search(''); }, [search, payment]);
  useEffect(() => {
    if (mode !== 'cash' || accounts.kind === 'ready') return;
    void api.financeAccounts().then((rows) => { setAccounts({ kind: 'ready', value: rows }); setCash((c) => ({ ...c, account_id: c.account_id || (rows.find((a) => a.kind === 'cash') ?? rows[0])?.id || '' })); },
      (e) => setAccounts(failed(e)));
  }, [mode, accounts.kind]);
  const act = async (work: () => Promise<unknown>, text: string) => {
    setBusy(true); setError(null);
    try { await work(); onDone(text); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const linked = payment && payment.status !== 'void';
  return <Modal title={t('veh.pay.title')} className="vehDialog" onClose={onClose}>
    <p className="muted vehNote">{t('veh.pay.rule')}</p>
    {linked && <div className="vehPayNow">
      <p><strong>{paymentText(payment)}</strong>{payment!.recorded_as_cash ? ` · ${t('veh.pay.cashRecorded')}` : ''}</p>
      {payment!.status === 'needs_review' && <Notice>{t('veh.pay.reviewText')}</Notice>}
      {payment!.link_id && <button type="button" className="secondary danger" disabled={busy} onClick={() => void act(() => api.unlinkPayment(kind, recordId), t('veh.pay.unlinked'))}><Unlink aria-hidden="true" />{t('veh.pay.unlink')}</button>}
      <p className="muted vehNote">{t('veh.pay.unlinkNote')}</p>
    </div>}
    {!linked && <>
      {payment?.status === 'void' && <Notice>{paymentText(payment)}</Notice>}
      <div className="cmSegment" role="radiogroup" aria-label={t('veh.pay.how')}>
        <button type="button" role="radio" aria-checked={mode === 'link'} onClick={() => setMode('link')}>{t('veh.pay.modeLink')}</button>
        <button type="button" role="radio" aria-checked={mode === 'cash'} onClick={() => setMode('cash')}>{t('veh.pay.modeCash')}</button>
      </div>
      {mode === 'link' && <>
        <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); void search(q.trim()); }}>
          <label className="cmInline cmSearch"><span>{t('veh.pay.search')}</span><input type="search" dir="auto" value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} /></label>
          <button type="submit" className="secondary">{t('veh.list.find')}</button>
        </form>
        {list.kind === 'loading' && <Notice busy>{t('veh.pay.loading')}</Notice>}
        {list.kind === 'denied' && <Notice>{t('veh.pay.noFinance')}</Notice>}
        {list.kind === 'error' && <LoadError message={t('veh.error.payments', { message: list.message })} retry={() => void search(q.trim())} />}
        {list.kind === 'ready' && list.value.length === 0 && <p className="muted">{t('veh.pay.none.found')}</p>}
        {list.kind === 'ready' && list.value.length > 0 && <ul className="cmList vehPayList">{list.value.map((c) => <li key={`${c.canonical_id}:${c.currency}`}>
          <div><strong dir="auto">{c.merchant}</strong><small>{c.posted_on} · <span dir="ltr">{formatAmount(c.amount, c.currency)}</span>
            {c.remaining !== c.amount ? ` · ${t('veh.pay.left', { amount: formatAmount(c.remaining, c.currency) })}` : ''}</small></div>
          <button type="button" className="secondary" disabled={busy || Number(c.remaining) <= 0} aria-label={t('veh.pay.chooseLabel', { what: `${c.merchant} ${c.posted_on}` })}
            onClick={() => void act(() => api.linkPayment(kind, recordId, { canonical_id: c.canonical_id }, key), t('veh.pay.linked'))}>{t('veh.pay.choose')}</button>
        </li>)}</ul>}
      </>}
      {mode === 'cash' && <form className="vehForm" noValidate onSubmit={(e) => { e.preventDefault(); const amount = decimal(cash.amount);
        if (!amount || !cash.account_id) { setError(t('veh.pay.cashNeeds')); return; }
        void act(() => api.recordCash(kind, recordId, { account_id: cash.account_id, amount, posted_on: cash.posted_on }, key), t('veh.pay.cashDone')); }}>
        <p className="muted vehNote">{t('veh.pay.cashRule')}</p>
        {accounts.kind === 'loading' && <Notice busy>{t('veh.loading')}</Notice>}
        {(accounts.kind === 'error' || accounts.kind === 'denied') && <Notice error>{accounts.kind === 'denied' ? t('veh.pay.noFinance') : accounts.message}</Notice>}
        {accounts.kind === 'ready' && accounts.value.length === 0 && <p className="muted">{t('veh.pay.noAccounts')} <a href="#finance">{t('veh.pay.openFinance')}</a></p>}
        {accounts.kind === 'ready' && accounts.value.length > 0 && <div className="vehTwo">
          <label className="field"><span>{t('veh.pay.account')}</span><select value={cash.account_id} onChange={(e) => setCash({ ...cash, account_id: e.target.value })}>
            {accounts.value.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
          <label className="field"><span>{t('veh.pay.amount')}</span><input inputMode="decimal" value={cash.amount} onChange={(e) => setCash({ ...cash, amount: e.target.value })} /></label>
          <label className="field"><span>{t('veh.pay.date')}</span><input type="date" max={today()} value={cash.posted_on} onChange={(e) => setCash({ ...cash, posted_on: e.target.value })} /></label>
        </div>}
        <div className="actions"><button type="submit" disabled={busy || accounts.kind !== 'ready' || accounts.value.length === 0}>{busy ? t('veh.saving') : t('veh.pay.cashSave')}</button></div>
      </form>}
    </>}
    <FormError id="veh-pay-error" text={error} />
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
  </Modal>;
}

// ---- Odometer ------------------------------------------------------------------------------------------

function OdometerPanel({ vehicle, onChanged }: { vehicle: Vehicle; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<VehiclePage<VehicleOdometerReading>>>({ kind: 'loading' });
  const [value, setValue] = useState('');
  const [correction, setCorrection] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const load = useCallback(async () => {
    try { setPage({ kind: 'ready', value: await api.listReadings(vehicle.id, offset, PAGE_SIZE) }); } catch (e) { setPage(failed(e)); }
  }, [vehicle.id, offset]);
  useEffect(() => { void load(); }, [load]);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    const reading = decimal(value, 1);
    if (!reading) return setMessage({ text: t('veh.form.badNumber'), error: true });
    if (correction && reason.trim().length < 3) return setMessage({ text: t('veh.odo.needReason'), error: true });
    setBusy(true); setMessage(null);
    try {
      await api.addReading(vehicle.id, { reading_km: reading, correction, reason: correction ? reason.trim() : null });
      setValue(''); setReason(''); setCorrection(false); setMessage({ text: t('veh.odo.saved') }); void load(); onChanged();
    } catch (err) { setMessage({ text: describeError(err), error: true }); } finally { setBusy(false); }
  };
  return <section className="surface cmPanel" aria-labelledby="veh-odo-title">
    <h2 id="veh-odo-title">{t('veh.odo.title')}</h2>
    <p className="muted vehNote">{t('veh.odo.rule')}</p>
    {!vehicle.archived_at && <form className="vehForm vehOdoForm" noValidate onSubmit={(e) => void add(e)}>
      <label className="field"><span>{t('veh.odo.reading')}</span><input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} /></label>
      {vehicle.can_edit && <label className="vehCheck"><input type="checkbox" checked={correction} onChange={(e) => setCorrection(e.target.checked)} />{t('veh.odo.correction')}</label>}
      {correction && <label className="field"><span>{t('veh.odo.reason')}</span><input dir="auto" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} /></label>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('veh.saving') : t('veh.odo.add')}</button></div>
    </form>}
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {page.kind === 'loading' && <Notice busy>{t('veh.loading')}</Notice>}
    {(page.kind === 'error' || page.kind === 'denied') && <LoadError message={t('veh.error.records', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && <p className="muted">{t('veh.odo.none')}</p>}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <ul className="cmList">{page.value.items.map((r) => <li key={r.id}><div>
        <strong>{km(r.reading_km)}{r.correction ? ` · ${t('veh.odo.isCorrection')}` : ''}</strong>
        <small>{formatDateTime(r.read_at)} · {t(`veh.odo.src.${r.source}` as MessageKey)}{r.reason ? ` · ${r.reason}` : ''}</small></div></li>)}</ul>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
  </section>;
}

// ---- Report --------------------------------------------------------------------------------------------

function ReportPanel({ report }: { report: VehicleReport }) {
  const dep = report.ownership_cost.depreciation;
  return <div className="vehGrid">
    <section className="surface cmPanel" aria-labelledby="veh-cost-title">
      <h2 id="veh-cost-title">{t('veh.report.costs')}</h2>
      <dl className="vehFacts">
        <dt>{t('veh.report.costPerKm')}</dt><dd dir="ltr">{report.cost_per_km.length ? report.cost_per_km.map((c) => `${c.amount} ${c.currency}`).join(' · ') : '-'}</dd>
        <dt>{t('veh.report.litres')}</dt><dd>{litres(report.fuel_litres)}</dd>
        <dt>{t('veh.report.operating')}</dt><dd dir="ltr">{moneyList(report.ownership_cost.operating)}</dd>
        <dt>{t('veh.report.depreciation')}</dt><dd>{dep ? t('veh.report.depText', { monthly: formatAmount(dep.monthly, dep.currency), accumulated: formatAmount(dep.accumulated, dep.currency), book: formatAmount(dep.book_value, dep.currency) }) : t('veh.report.noDep')}</dd>
      </dl>
      <p className="muted vehNote">{t('veh.report.capitalNote')}</p>
      {report.by_category.length > 0 && <ul className="cmList">{report.by_category.map((c) => <li key={`${c.currency}:${c.category}`}><div><strong>{c.category === 'fuel' ? t('veh.dtab.fuel') : serviceLabel(c.category)}</strong></div><span className="num" dir="ltr">{formatAmount(c.amount, c.currency)}</span></li>)}</ul>}
    </section>
    <section className="surface cmPanel" aria-labelledby="veh-cons-title">
      <h2 id="veh-cons-title">{t('veh.report.consumption')}</h2>
      <p className="muted vehNote">{t('veh.report.fullToFull')}</p>
      {report.consumption.segments.length === 0 ? <p className="muted">{t('veh.report.noSegments')}</p>
        : <div className="vehTableWrap"><table className="cmTable vehTable"><thead><tr><th scope="col">{t('veh.col.ended')}</th><th scope="col" className="num">{t('veh.col.km')}</th><th scope="col" className="num">{t('veh.col.litres')}</th><th scope="col" className="num">{t('veh.col.per100')}</th></tr></thead>
          <tbody>{report.consumption.segments.map((s) => <tr key={s.end_id}><td>{s.ended_on}</td><td className="num">{km(s.km)}</td><td className="num">{litres(s.litres)}</td><td className="num">{s.per_100}</td></tr>)}</tbody></table></div>}
    </section>
    <section className="surface cmPanel" aria-labelledby="veh-price-title">
      <h2 id="veh-price-title">{t('veh.report.prices')}</h2>
      {report.fuel_price_trend.length === 0 ? <p className="muted">{t('veh.report.noPrices')}</p>
        : <ul className="cmList">{report.fuel_price_trend.map((p) => <li key={`${p.month}:${p.currency}:${p.fuel_type}`}><div><strong>{p.month} · {fuelLabel(p.fuel_type)}</strong></div><span className="num" dir="ltr">{p.price_per_litre} {p.currency}</span></li>)}</ul>}
    </section>
    <section className="surface cmPanel" aria-labelledby="veh-anom-title">
      <h2 id="veh-anom-title">{t('veh.report.anomalies')}</h2>
      {report.anomalies.length === 0 ? <p className="muted">{t('veh.report.noAnomalies')}</p>
        : <ul className="cmList">{report.anomalies.map((a, i) => <li key={`${a.kind}:${a.record_id}:${i}`}><div><strong>{anomalyLabel(a.kind)}</strong>
          <small dir="ltr">{Object.entries(a.detail).map(([k, v]) => `${k}: ${v}`).join(' · ')}</small></div></li>)}</ul>}
    </section>
  </div>;
}

// ---- Details: edit, archive, delete, purchase payment, export -----------------------------------------

function DetailsPanel({ vehicle, onChanged, onDeleted }: { vehicle: Vehicle; onChanged: () => void; onDeleted: () => void }) {
  const [editing, setEditing] = useState(false);
  const [paying, setPaying] = useState(false);
  const [confirm, setConfirm] = useState<'archive' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);
  const run = async (work: () => Promise<unknown>, after: () => void) => {
    setBusy(true); setMessage(null);
    try { await work(); setConfirm(null); after(); } catch (e) { setConfirm(null); setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const row = (label: MessageKey, value: ReactNode) => <><dt>{t(label)}</dt><dd>{value || '-'}</dd></>;
  return <div className="vehGrid">
    <section className="surface cmPanel" aria-labelledby="veh-facts-title">
      <div className="vehBar"><h2 id="veh-facts-title">{t('veh.details.title')}</h2>
        {vehicle.can_edit && <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('veh.rec.edit')}</button>}</div>
      <dl className="vehFacts">
        {row('veh.form.registration', <bdi>{vehicle.registration}</bdi>)}
        {row('veh.form.vin', vehicle.vin ? <span dir="ltr">{vehicle.vin}</span> : null)}
        {row('veh.form.make', [vehicle.make, vehicle.model, vehicle.year].filter(Boolean).join(' '))}
        {row('veh.form.type', typeLabel(vehicle.vehicle_type))}
        {row('veh.form.tank', vehicle.tank_litres ? litres(vehicle.tank_litres) : null)}
        {row('veh.form.currency', vehicle.currency)}
        {row('veh.form.insurance', vehicle.insurance_expires_on)}
        {row('veh.form.registrationExpiry', vehicle.registration_expires_on)}
        {row('veh.form.inspection', vehicle.inspection_expires_on)}
        {row('veh.form.acquired', vehicle.acquisition_date)}
        {row('veh.form.price', vehicle.purchase_price ? formatAmount(vehicle.purchase_price, vehicle.currency) : null)}
        {row('veh.form.notes', vehicle.notes)}
      </dl>
      {!vehicle.can_edit && <p className="muted vehNote">{t('veh.details.readOnly')}</p>}
    </section>
    <section className="surface cmPanel" aria-labelledby="veh-actions-title">
      <h2 id="veh-actions-title">{t('veh.details.actions')}</h2>
      <p>{t('veh.details.purchase')}: <PaymentChip payment={vehicle.purchase_payment} /></p>
      <div className="vehRowButtons">
        {vehicle.can_edit && <button type="button" className="secondary" onClick={() => setPaying(true)}><Link2 aria-hidden="true" />{t('veh.details.purchasePay')}</button>}
        <button type="button" className="secondary" disabled={busy} onClick={() => void run(() => api.exportCsv(vehicle.id), () => setMessage({ text: t('veh.details.exported') }))}><Download aria-hidden="true" />{t('veh.details.export')}</button>
        {vehicle.can_edit && <button type="button" className="secondary" onClick={() => setConfirm('archive')}>{vehicle.archived_at ? <ArchiveRestore aria-hidden="true" /> : <Archive aria-hidden="true" />}{vehicle.archived_at ? t('veh.details.restore') : t('veh.details.archive')}</button>}
        {vehicle.can_edit && <button type="button" className="secondary danger" onClick={() => setConfirm('delete')}><Trash2 aria-hidden="true" />{t('veh.details.delete')}</button>}
      </div>
      {message && <Notice error={message.error}>{message.text}</Notice>}
    </section>
    {editing && <VehicleForm vehicle={vehicle} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(); }} />}
    {paying && <PaymentDialog kind="purchase" recordId={vehicle.id} payment={vehicle.purchase_payment} hint={{ day: vehicle.acquisition_date, amount: vehicle.purchase_price, currency: vehicle.currency }}
      onClose={() => setPaying(false)} onDone={() => { setPaying(false); onChanged(); }} />}
    {confirm === 'archive' && <ConfirmDialog title={vehicle.archived_at ? t('veh.details.restore') : t('veh.details.archive')} confirmLabel={vehicle.archived_at ? t('veh.details.restore') : t('veh.details.archive')} busy={busy}
      onConfirm={() => void run(() => api.archiveVehicle(vehicle.id, vehicle.version, !vehicle.archived_at), onChanged)} onCancel={() => setConfirm(null)}>
      <p>{vehicle.archived_at ? t('veh.details.restoreText') : t('veh.details.archiveText')}</p></ConfirmDialog>}
    {confirm === 'delete' && <ConfirmDialog title={t('veh.details.delete')} confirmLabel={t('veh.details.delete')} danger busy={busy}
      onConfirm={() => void run(() => api.deleteVehicle(vehicle.id), onDeleted)} onCancel={() => setConfirm(null)}>
      <p>{t('veh.details.deleteText', { name: vehicle.registration })}</p></ConfirmDialog>}
  </div>;
}
