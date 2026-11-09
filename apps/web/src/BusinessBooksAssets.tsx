// Business accounting completion (docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation,
// #group-balance-sheet): Fixed assets (the register, the roll-forward, each asset's depreciation, disposal and impairments
// with a second person's approval), Loans (the schedule, repayments split into interest and principal, schedule against
// actual, the revised schedule after an early repayment), Revaluation (balances in other currencies at a month end's closing
// rate) and the group balance sheet on All businesses. The API is the authority: it posts every figure, never guesses a
// rate and refuses anything dated in a closed period; the page shows what it says, in every state.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Download, HandCoins, Plus, RefreshCw, Save } from 'lucide-react';
import type {
  AssetImpairment, BooksSetup, BusinessLoan, BusinessLoanDetail, BusinessLoanList, FixedAsset, FixedAssetDetail, FixedAssetList, GroupBalanceSheet,
  LoanRepayment, LoanScheduleRow, RevaluationPreview,
} from '@anum/contracts';
import { DEPRECIATION_METHODS } from '@anum/contracts';
import * as api from './lib/businessAssetsApi';
import {
  GROUP_TOTAL_ROWS, actualStateLabel, assetBody, assetFormFrom, assetProblem, earlyLabel, emptyLoan, frequencyLabel, groupRowLabel, loanBody, loanMethodLabel,
  loanProblem, methodLabel, monthEnds, repaidShare, repaymentBody, repaymentProblem, revaluationStatusLabel, type AssetForm, type LoanForm, type RepaymentForm,
} from './lib/businessAssets';
import { BOOK_CURRENCIES, isNegative, money, todayIso } from './lib/businessBooks';
import { accountName } from './lib/businessLedger';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { Drawer, Modal } from './Dialog';
import { Badge, Denied, Empty, Failed, Note, ReasonDialog, day, failure } from './BusinessParts';

const label = (key: string) => t(key as MessageKey);
const dayText = (value: string | null | undefined) => (value ? day(value) : '-');
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied'; message: string } | { state: 'error'; message: string };
type Flash = { text: string; error?: boolean } | null;

function useLoad<T>(fetch: () => Promise<T>, deps: unknown[]): [Load<T>, () => Promise<void>] {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' });
  const run = useCallback(async () => {
    setLoad((current) => (current.state === 'ready' ? current : { state: 'loading' }));
    try { setLoad({ state: 'ready', value: await fetch() }); }
    catch (error) { setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) }); }
  }, deps);
  useEffect(() => { void run(); }, [run]);
  return [load, run];
}

function Amount({ value, currency }: { value: string | null | undefined; currency: string }) {
  if (value === null || value === undefined) return <>-</>;
  return <span className={isNegative(value) ? 'bkNeg' : undefined}><bdi>{money(value, currency)}</bdi></span>;
}

function Table({ caption, children }: { caption: string; children: React.ReactNode }) {
  return <div className="dataTableWrap" tabIndex={0} role="region" aria-label={caption}><table className="dataTable bzTable">
    <caption className="visuallyHidden">{caption}</caption>{children}</table></div>;
}

// ---- fixed assets --------------------------------------------------------------------------------------------------------------

export function AssetsSection({ setup }: { setup: BooksSetup }) {
  const [period, setPeriod] = useState({ from: setup.default_period?.from ?? '', to: setup.default_period?.to ?? '' });
  const [status, setStatus] = useState<'all' | 'active' | 'disposed'>('all');
  const [q, setQ] = useState(''), [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [load, reload] = useLoad<FixedAssetList>(() => api.listAssets({ ...period, status, q: search, offset, limit: 25 }),
    [period.from, period.to, status, search, offset, tick]);
  const ids = { from: useId(), to: useId(), status: useId(), q: useId() };
  async function exportCsv() {
    setFlash(null);
    try { await api.exportAssets(period); setFlash({ text: t('books.exported') }); } catch (error) { setFlash({ text: failure(error, t('books.export')), error: true }); }
  }
  const value = load.state === 'ready' ? load.value : null;
  return <>
    <p className="muted bzHint">{t('assets.help')}</p>
    <div className="bzToolbar bkPeriod">
      <label className="bzCompact" htmlFor={ids.from}><span>{t('assets.from')}</span><input id={ids.from} type="date" value={period.from} max={period.to || undefined}
        onChange={(event) => { setOffset(0); setPeriod({ ...period, from: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.to}><span>{t('assets.to')}</span><input id={ids.to} type="date" value={period.to} min={period.from || undefined}
        onChange={(event) => { setOffset(0); setPeriod({ ...period, to: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.status}><span>{t('assets.status')}</span>
        <select id={ids.status} value={status} onChange={(event) => { setOffset(0); setStatus(event.target.value as typeof status); }}>
          {(['all', 'active', 'disposed'] as const).map((item) => <option key={item} value={item}>{label(`assets.filter.${item}`)}</option>)}</select></label>
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(q.trim()); }}>
        <label className="visuallyHidden" htmlFor={ids.q}>{t('assets.search')}</label>
        <input id={ids.q} type="search" value={q} placeholder={t('assets.search')} onChange={(event) => { setQ(event.target.value); if (!event.target.value) setSearch(''); }} />
      </form>
      <div className="bzToolbarEnd"><button type="button" className="secondary" disabled={!value} onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('books.export')}</button></div>
    </div>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    {load.state === 'loading' && <Note>{t('assets.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {value && <>
      {value.pending_impairments > 0 && <Note>{t('assets.pendingNote', { count: value.pending_impairments })}</Note>}
      <RollForward value={value} />
      {value.total === 0 ? (search || status !== 'all'
        ? <Empty title={t('assets.noMatchTitle')} text={t('assets.noMatchText')} action={<button type="button" className="secondary" onClick={() => { setQ(''); setSearch(''); setStatus('all'); }}>{t('assets.clear')}</button>} />
        : <Empty title={t('assets.emptyTitle')} text={t('assets.emptyText')} />)
        : <section className="surface bzCard bkWide" aria-labelledby="asset-register-title">
          <h2 id="asset-register-title" className="bkSub">{t('assets.register', { day: dayText(value.as_of) })}</h2>
          <Table caption={t('assets.registerCaption')}>
            <thead><tr><th scope="col">{t('assets.col.asset')}</th><th scope="col" className="bzHideMd">{t('assets.col.acquired')}</th>
              <th scope="col" className="num">{t('assets.col.cost')}</th><th scope="col" className="bzHideSm">{t('assets.col.method')}</th>
              <th scope="col" className="num bzHideSm">{t('assets.col.depreciation')}</th><th scope="col" className="num">{t('assets.col.nbv')}</th></tr></thead>
            <tbody>{value.items.map((item) => <tr key={item.id}>
              <th scope="row"><button type="button" className="linkButton" onClick={() => setOpen(item.id)} aria-label={t('assets.openLabel', { name: item.name })}>
                <span dir="auto">{item.name}</span></button>
                <small>{label(`assets.source.${item.source_kind}`)}{item.status === 'disposed' && <> <Badge tone="neutral">{t('assets.disposed')}</Badge></>}
                  {!item.source_live && <> <Badge tone="warning">{t('assets.sourceVoided')}</Badge></>}
                  {item.pending_impairments > 0 && <> <Badge tone="warning">{t('assets.pendingBadge')}</Badge></>}</small></th>
              <td className="nowrap bzHideMd">{dayText(item.acquired_on)}</td>
              <td className="num nowrap"><Amount value={item.cost} currency={item.currency} /></td>
              <td className="bzHideSm">{item.useful_life_months ? <>{methodLabel(item.method)}<small>{t('assets.lifeMonths', { count: item.useful_life_months })}</small></> : <Badge tone="warning">{t('assets.noLife')}</Badge>}</td>
              <td className="num nowrap bzHideSm"><Amount value={item.accumulated_depreciation} currency={item.currency} /></td>
              <td className="num nowrap"><Amount value={item.net_book_value} currency={item.currency} /></td>
            </tr>)}</tbody>
          </Table>
          {value.total > value.limit && <div className="bzInline bzMore">
            <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - value.limit))}>{t('tax.page.previous')}</button>
            <span className="muted">{t('tax.page.range', { from: offset + 1, to: Math.min(offset + value.limit, value.total), total: value.total })}</span>
            <button type="button" className="secondary" disabled={offset + value.limit >= value.total} onClick={() => setOffset(offset + value.limit)}>{t('tax.page.next')}</button>
          </div>}
        </section>}
    </>}
    {open && <AssetDrawer assetId={open} onClose={() => setOpen(null)} onChanged={() => setTick((n) => n + 1)} />}
  </>;
}

function RollForward({ value }: { value: FixedAssetList }) {
  if (value.roll_forward.length === 0) return null;
  const rows = [['opening_cost', 'additions', 'disposals_cost', 'closing_cost'], ['opening_depreciation', 'depreciation', 'impairment', 'disposals_depreciation',
    'closing_depreciation'], ['opening_nbv', 'closing_nbv']] as const;
  return <section className="surface bzCard bkWide" aria-labelledby="asset-roll-title">
    <h2 id="asset-roll-title" className="bkSub">{t('assets.rollTitle', { from: dayText(value.period.from), to: dayText(value.period.to) })}</h2>
    <Table caption={t('assets.rollCaption')}>
      <thead><tr><th scope="col">{t('books.col.line')}</th>{value.roll_forward.map((item) => <th key={item.currency} scope="col" className="num">{item.currency}</th>)}</tr></thead>
      {rows.map((group, index) => <tbody key={index}>{group.map((key) => <tr key={key} className={key.startsWith('closing') ? 'bk-total' : 'bk-line'}>
        <th scope="row">{label(`assets.roll.${key}`)}</th>
        {value.roll_forward.map((item) => <td key={item.currency} className="num nowrap"><Amount value={item[key]} currency={item.currency} /></td>)}</tr>)}</tbody>)}
    </Table>
  </section>;
}

function AssetDrawer({ assetId, onClose, onChanged }: { assetId: string; onClose: () => void; onChanged: () => void }) {
  const [tick, setTick] = useState(0);
  const [load, reload] = useLoad<FixedAssetDetail>(() => api.getAsset(assetId), [assetId, tick]);
  const changed = () => { setTick((n) => n + 1); onChanged(); };
  const title = load.state === 'ready' ? load.value.name : t('assets.drawerTitle');
  return <Drawer title={title} onClose={onClose}>
    {load.state === 'loading' && <Note>{t('assets.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {load.state === 'ready' && <AssetBody asset={load.value} onChanged={changed} />}
  </Drawer>;
}

function AssetBody({ asset, onChanged }: { asset: FixedAssetDetail; onChanged: () => void }) {
  const cur = asset.currency;
  const active = asset.status === 'active' && asset.source_live;
  return <>
    <ul className="bkList">
      <li><span>{t('assets.col.acquired')}</span><span>{dayText(asset.acquired_on)}</span></li>
      <li><span>{t('assets.col.cost')}</span><Amount value={asset.cost} currency={cur} /></li>
      <li><span>{t('assets.col.depreciation')}</span><Amount value={asset.accumulated_depreciation} currency={cur} /></li>
      <li><span>{t('assets.impairment')}</span><Amount value={asset.impairment} currency={cur} /></li>
      <li><span>{t('assets.col.nbv')}</span><Amount value={asset.net_book_value} currency={cur} /></li>
      {asset.status === 'disposed' && <li><span>{t('assets.disposalOn', { day: dayText(asset.disposed_on) })}</span>
        <span>{label(`assets.kind.${asset.disposal_kind ?? 'write_off'}`)}: <Amount value={asset.gain_or_loss} currency={cur} /></span></li>}
    </ul>
    {!asset.source_live && <Note error>{t('assets.sourceVoidedText')}</Note>}
    {!asset.can_edit && <Note>{t('assets.accountantsOnly')}</Note>}
    {asset.can_edit && active && <DepreciationForm asset={asset} onSaved={onChanged} />}
    <Impairments asset={asset} onChanged={onChanged} />
    {asset.can_edit && active && <DisposeForm asset={asset} onDone={onChanged} />}
    <h3 className="bkSub">{t('assets.scheduleTitle')}</h3>
    {asset.schedule.length === 0 ? <p className="muted">{t('assets.scheduleEmpty')}</p> : <Table caption={t('assets.scheduleTitle')}>
      <thead><tr><th scope="col">{t('assets.col.month')}</th><th scope="col" className="num">{t('assets.col.amount')}</th><th scope="col" className="num bzHideSm">{t('assets.col.accumulated')}</th>
        <th scope="col">{t('assets.col.state')}</th></tr></thead>
      <tbody>{asset.schedule.map((row) => <tr key={row.month}><th scope="row" className="nowrap">{dayText(row.month)}</th>
        <td className="num nowrap"><Amount value={row.amount} currency={cur} /></td><td className="num nowrap bzHideSm"><Amount value={row.accumulated} currency={cur} /></td>
        <td><Badge tone={row.state === 'scheduled' ? 'neutral' : row.state === 'closed' ? 'neutral' : 'success'}>{label(`assets.state.${row.state}`)}</Badge></td></tr>)}</tbody>
    </Table>}
  </>;
}

function DepreciationForm({ asset, onSaved }: { asset: FixedAssetDetail; onSaved: () => void }) {
  const [form, setForm] = useState<AssetForm>(() => assetFormFrom(asset));
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flash>(null);
  const ids = { name: useId(), category: useId(), life: useId(), residual: useId(), method: useId(), rate: useId(), start: useId() };
  const set = (patch: Partial<AssetForm>) => setForm((value) => ({ ...value, ...patch }));
  async function save(event: FormEvent) {
    event.preventDefault();
    const wrong = assetProblem(form, asset.cost);
    if (wrong) { setFlash({ text: t(wrong), error: true }); return; }
    setBusy(true); setFlash(null);
    try { await api.updateAsset(asset.id, assetBody(form, asset.version)); setFlash({ text: t('assets.saved') }); onSaved(); }
    catch (error) { setFlash({ text: failure(error, t('assets.save')), error: true }); } finally { setBusy(false); }
  }
  return <form className="surface bzCard" onSubmit={(event) => void save(event)} noValidate aria-labelledby={`${ids.name}-title`}>
    <h3 id={`${ids.name}-title`} className="bkSub">{t('assets.settingsTitle')}</h3>
    <div className="bzGrid2">
      <label className="field" htmlFor={ids.name}><span>{t('assets.name')}</span><input id={ids.name} maxLength={120} dir="auto" value={form.name} onChange={(event) => set({ name: event.target.value })} /></label>
      <label className="field" htmlFor={ids.category}><span>{t('assets.category')}</span><input id={ids.category} maxLength={40} dir="auto" value={form.category} onChange={(event) => set({ category: event.target.value })} /></label>
      <label className="field" htmlFor={ids.method}><span>{t('assets.method')}</span><select id={ids.method} value={form.method} onChange={(event) => set({ method: event.target.value as AssetForm['method'] })}>
        {DEPRECIATION_METHODS.map((item) => <option key={item} value={item}>{methodLabel(item)}</option>)}</select></label>
      {form.method === 'reducing_balance' && <label className="field" htmlFor={ids.rate}><span>{t('assets.rate')}</span>
        <input id={ids.rate} inputMode="decimal" maxLength={8} value={form.rate_pct} onChange={(event) => set({ rate_pct: event.target.value })} /></label>}
      <label className="field" htmlFor={ids.life}><span>{t('assets.life')}</span><input id={ids.life} inputMode="numeric" maxLength={3} value={form.useful_life_months}
        onChange={(event) => set({ useful_life_months: event.target.value })} /></label>
      <label className="field" htmlFor={ids.residual}><span>{t('assets.residual', { currency: asset.currency })}</span><input id={ids.residual} inputMode="decimal" maxLength={16}
        value={form.residual_value} onChange={(event) => set({ residual_value: event.target.value })} /></label>
      <label className="field" htmlFor={ids.start}><span>{t('assets.start')}</span><input id={ids.start} type="month" value={form.depreciation_start}
        min={asset.acquired_on.slice(0, 7)} onChange={(event) => set({ depreciation_start: event.target.value })} /></label>
    </div>
    <p className="muted bzHint">{t('assets.settingsHelp')}</p>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    <div className="actions"><button type="submit" disabled={busy}><Save aria-hidden="true" />{busy ? t('dialog.working') : t('assets.save')}</button></div>
  </form>;
}

function DisposeForm({ asset, onDone }: { asset: FixedAssetDetail; onDone: () => void }) {
  const [form, setForm] = useState({ disposed_on: todayIso(), kind: 'sale' as 'sale' | 'write_off', proceeds: '' });
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flash>(null);
  const ids = { day: useId(), kind: useId(), proceeds: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.disposed_on || form.disposed_on > todayIso()) { setFlash({ text: t('assets.problem.disposalDay'), error: true }); return; }
    if (form.kind === 'sale' && !(Number(form.proceeds) > 0)) { setFlash({ text: t('assets.problem.proceeds'), error: true }); return; }
    setBusy(true); setFlash(null);
    try { await api.disposeAsset(asset.id, { ...form, proceeds: form.kind === 'sale' ? form.proceeds.trim() : '0', version: asset.version }); onDone(); }
    catch (error) { setFlash({ text: failure(error, t('assets.dispose')), error: true }); } finally { setBusy(false); }
  }
  return <form className="surface bzCard" onSubmit={(event) => void submit(event)} noValidate aria-labelledby={`${ids.day}-title`}>
    <h3 id={`${ids.day}-title`} className="bkSub">{t('assets.disposeTitle')}</h3>
    <p className="muted bzHint">{t('assets.disposeHelp')}</p>
    <div className="bzGrid3">
      <label className="field" htmlFor={ids.day}><span>{t('assets.disposedOn')}</span><input id={ids.day} type="date" max={todayIso()} min={asset.acquired_on} value={form.disposed_on}
        onChange={(event) => setForm({ ...form, disposed_on: event.target.value })} /></label>
      <label className="field" htmlFor={ids.kind}><span>{t('assets.disposalKind')}</span><select id={ids.kind} value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as 'sale' | 'write_off' })}>
        <option value="sale">{t('assets.kind.sale')}</option><option value="write_off">{t('assets.kind.write_off')}</option></select></label>
      {form.kind === 'sale' && <label className="field" htmlFor={ids.proceeds}><span>{t('assets.proceeds', { currency: asset.currency })}</span>
        <input id={ids.proceeds} inputMode="decimal" maxLength={16} value={form.proceeds} onChange={(event) => setForm({ ...form, proceeds: event.target.value })} /></label>}
    </div>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    <div className="actions"><button type="submit" className="danger" disabled={busy}>{busy ? t('dialog.working') : t('assets.dispose')}</button></div>
  </form>;
}

function Impairments({ asset, onChanged }: { asset: FixedAssetDetail; onChanged: () => void }) {
  const [form, setForm] = useState({ entry_date: todayIso(), amount: '', reason: '' });
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flash>(null);
  const ids = { day: useId(), amount: useId(), reason: useId() };
  async function ask(event: FormEvent) {
    event.preventDefault();
    if (!(Number(form.amount) > 0)) { setFlash({ text: t('assets.problem.impairment'), error: true }); return; }
    if (form.reason.trim().length < 5) { setFlash({ text: t('assets.problem.reason'), error: true }); return; }
    setBusy(true); setFlash(null);
    try { await api.requestImpairment(asset.id, { ...form, amount: form.amount.trim(), reason: form.reason.trim() }); setForm({ ...form, amount: '', reason: '' });
      setFlash({ text: t('assets.impairmentAsked') }); onChanged(); }
    catch (error) { setFlash({ text: failure(error, t('assets.askImpairment')), error: true }); } finally { setBusy(false); }
  }
  async function decide(row: AssetImpairment, decision: 'approve' | 'reject') {
    setFlash(null);
    try { await api.decideImpairment(asset.id, row.id, decision, row.version); onChanged(); }
    catch (error) { setFlash({ text: failure(error, t(decision === 'approve' ? 'assets.approve' : 'assets.reject')), error: true }); }
  }
  const active = asset.status === 'active' && asset.source_live;
  return <section className="surface bzCard" aria-labelledby={`${ids.day}-title`}>
    <h3 id={`${ids.day}-title`} className="bkSub">{t('assets.impairmentsTitle')}</h3>
    <p className="muted bzHint">{t('assets.impairmentsHelp')}</p>
    {asset.impairments.length === 0 ? <p className="muted">{t('assets.impairmentsEmpty')}</p> : <ul className="bkList">{asset.impairments.map((row) => <li key={row.id}>
      <span><span dir="auto">{row.reason}</span><small>{dayText(row.entry_date)} · {label(`assets.impStatus.${row.status}`)}</small></span>
      <span className="bzInline"><Amount value={row.amount} currency={row.currency} />
        {asset.can_edit && row.status === 'pending' && (row.requested_by === asset.me ? <small className="muted">{t('assets.waitingOther')}</small> : <>
          <button type="button" onClick={() => void decide(row, 'approve')} aria-label={t('assets.approveLabel', { day: dayText(row.entry_date) })}>{t('assets.approve')}</button>
          <button type="button" className="secondary" onClick={() => void decide(row, 'reject')} aria-label={t('assets.rejectLabel', { day: dayText(row.entry_date) })}>{t('assets.reject')}</button></>)}</span>
    </li>)}</ul>}
    {asset.can_edit && active && <form onSubmit={(event) => void ask(event)} noValidate>
      <div className="bzGrid3">
        <label className="field" htmlFor={ids.day}><span>{t('assets.impairmentDay')}</span><input id={ids.day} type="date" max={todayIso()} value={form.entry_date}
          onChange={(event) => setForm({ ...form, entry_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.amount}><span>{t('assets.impairmentAmount', { currency: asset.currency })}</span><input id={ids.amount} inputMode="decimal" maxLength={16}
          value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
        <label className="field" htmlFor={ids.reason}><span>{t('assets.impairmentReason')}</span><input id={ids.reason} maxLength={300} dir="auto" value={form.reason}
          onChange={(event) => setForm({ ...form, reason: event.target.value })} /></label>
      </div>
      <div className="actions"><button type="submit" className="secondary" disabled={busy}>{busy ? t('dialog.working') : t('assets.askImpairment')}</button></div>
    </form>}
    {flash && <Note error={flash.error}>{flash.text}</Note>}
  </section>;
}

// ---- loans ---------------------------------------------------------------------------------------------------------------------

export function LoansSection({ setup }: { setup: BooksSetup }) {
  const [status, setStatus] = useState<'active' | 'void' | 'all'>('active');
  const [tick, setTick] = useState(0);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [load, reload] = useLoad<BusinessLoanList>(() => api.listLoans(status, 0, 100), [status, tick]);
  const statusId = useId();
  const value = load.state === 'ready' ? load.value : null;
  return <>
    <p className="muted bzHint">{t('loans.help')}</p>
    <div className="bzToolbar">
      <label className="bzCompact" htmlFor={statusId}><span>{t('loans.status')}</span><select id={statusId} value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
        {(['active', 'void', 'all'] as const).map((item) => <option key={item} value={item}>{label(`loans.filter.${item}`)}</option>)}</select></label>
      {value?.can_edit && <div className="bzToolbarEnd"><button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('loans.new')}</button></div>}
    </div>
    {load.state === 'loading' && <Note>{t('loans.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {value && !value.can_edit && <Note>{t('loans.accountantsOnly')}</Note>}
    {value && (value.total === 0 ? <Empty title={t('loans.emptyTitle')} text={t('loans.emptyText')}
      action={value.can_edit ? <button type="button" onClick={() => setCreating(true)}>{t('loans.new')}</button> : undefined} />
      : <section className="surface bzCard bkWide" aria-labelledby="loans-title"><h2 id="loans-title" className="bkSub">{t('loans.title')}</h2>
        <Table caption={t('loans.caption')}>
          <thead><tr><th scope="col">{t('loans.col.lender')}</th><th scope="col" className="num">{t('loans.col.principal')}</th><th scope="col" className="bzHideMd">{t('loans.col.terms')}</th>
            <th scope="col" className="num">{t('loans.col.outstanding')}</th><th scope="col" className="bzHideSm">{t('loans.col.next')}</th></tr></thead>
          <tbody>{value.items.map((item) => <LoanRow key={item.id} item={item} onOpen={() => setOpen(item.id)} />)}</tbody>
        </Table>
      </section>)}
    {creating && value && <LoanDialog list={value} base={setup.settings?.base_currency ?? 'SAR'} onClose={() => setCreating(false)}
      onCreated={(loan) => { setCreating(false); setTick((n) => n + 1); setOpen(loan.id); }} />}
    {open && value && <LoanDrawer loanId={open} list={value} manage={setup.permissions.manage} onClose={() => setOpen(null)} onChanged={() => setTick((n) => n + 1)} />}
  </>;
}

function LoanRow({ item, onOpen }: { item: BusinessLoan; onOpen: () => void }) {
  const share = repaidShare(item.principal, item.outstanding);
  return <tr>
    <th scope="row"><button type="button" className="linkButton" onClick={onOpen} aria-label={t('loans.openLabel', { lender: item.lender })}><span dir="auto">{item.lender}</span></button>
      <small>{dayText(item.start_date)}{item.status === 'void' && <> <Badge tone="neutral">{t('loans.void')}</Badge></>}</small></th>
    <td className="num nowrap"><Amount value={item.principal} currency={item.currency} /></td>
    <td className="bzHideMd"><bdi>{item.annual_rate}%</bdi> · {loanMethodLabel(item.method)}<small>{t('loans.periods', { count: item.periods, frequency: frequencyLabel(item.frequency) })}</small></td>
    <td className="num nowrap"><Amount value={item.outstanding} currency={item.currency} />
      <div className="bkProgress" role="img" aria-label={t('loans.repaidShare', { pct: share })}><span style={{ inlineSize: `${share}%` }} /></div></td>
    <td className="bzHideSm nowrap">{item.next_due ? <>{dayText(item.next_due)}<small><Amount value={item.next_payment} currency={item.currency} /></small></> : '-'}</td>
  </tr>;
}

function LoanDialog({ list, base, onClose, onCreated }: { list: BusinessLoanList; base: string; onClose: () => void; onCreated: (loan: BusinessLoan) => void }) {
  const [form, setForm] = useState<LoanForm>(() => emptyLoan(base, todayIso()));
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flash>(null);
  const ids = { lender: useId(), principal: useId(), currency: useId(), rate: useId(), start: useId(), term: useId(), frequency: useId(), method: useId(), early: useId(), match: useId() };
  const set = (patch: Partial<LoanForm>) => setForm((value) => ({ ...value, ...patch }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const wrong = loanProblem(form, todayIso());
    if (wrong) { setFlash({ text: t(wrong), error: true }); return; }
    setBusy(true); setFlash(null);
    try { onCreated(await api.createLoan(loanBody(form))); } catch (error) { setFlash({ text: failure(error, t('loans.create')), error: true }); setBusy(false); }
  }
  const received = list.match.received.filter((row) => row.currency === form.currency);
  return <Modal title={t('loans.new')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} noValidate aria-label={t('loans.new')}>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.lender}><span>{t('loans.lender')}</span><input id={ids.lender} maxLength={120} dir="auto" value={form.lender} data-autofocus onChange={(event) => set({ lender: event.target.value })} /></label>
        <label className="field" htmlFor={ids.principal}><span>{t('loans.principal')}</span><input id={ids.principal} inputMode="decimal" maxLength={16} value={form.principal} onChange={(event) => set({ principal: event.target.value })} /></label>
        <label className="field" htmlFor={ids.currency}><span>{t('books.col.currency')}</span><select id={ids.currency} value={form.currency} onChange={(event) => set({ currency: event.target.value, received_adjustment_id: '' })}>
          {(BOOK_CURRENCIES.includes(form.currency as never) ? BOOK_CURRENCIES : [form.currency, ...BOOK_CURRENCIES]).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label className="field" htmlFor={ids.rate}><span>{t('loans.rate')}</span><input id={ids.rate} inputMode="decimal" maxLength={8} value={form.annual_rate} onChange={(event) => set({ annual_rate: event.target.value })} /></label>
        <label className="field" htmlFor={ids.start}><span>{t('loans.start')}</span><input id={ids.start} type="date" max={todayIso()} value={form.start_date} onChange={(event) => set({ start_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.term}><span>{t('loans.term')}</span><input id={ids.term} inputMode="numeric" maxLength={3} value={form.term_months} onChange={(event) => set({ term_months: event.target.value })} /></label>
        <label className="field" htmlFor={ids.frequency}><span>{t('loans.frequency')}</span><select id={ids.frequency} value={form.frequency} onChange={(event) => set({ frequency: event.target.value as LoanForm['frequency'] })}>
          {(['monthly', 'quarterly', 'yearly'] as const).map((item) => <option key={item} value={item}>{frequencyLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.method}><span>{t('loans.method')}</span><select id={ids.method} value={form.method} onChange={(event) => set({ method: event.target.value as LoanForm['method'] })}>
          {(['annuity', 'equal_principal'] as const).map((item) => <option key={item} value={item}>{loanMethodLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.early}><span>{t('loans.early')}</span><select id={ids.early} value={form.early_repayment} onChange={(event) => set({ early_repayment: event.target.value as LoanForm['early_repayment'] })}>
          {(['reduce_term', 'reduce_payment'] as const).map((item) => <option key={item} value={item}>{earlyLabel(item)}</option>)}</select></label>
        {received.length > 0 && <label className="field" htmlFor={ids.match}><span>{t('loans.matchReceived')}</span><select id={ids.match} value={form.received_adjustment_id}
          onChange={(event) => set({ received_adjustment_id: event.target.value })}><option value="">{t('loans.matchNone')}</option>
          {received.map((row) => <option key={row.id} value={row.id}>{dayText(row.date)} · {money(row.amount, row.currency)}</option>)}</select></label>}
      </div>
      <p className="muted bzHint">{t('loans.formHelp')}</p>
      {flash && <Note error={flash.error}>{flash.text}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('loans.create')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

function LoanDrawer({ loanId, list, manage, onClose, onChanged }: { loanId: string; list: BusinessLoanList; manage: boolean; onClose: () => void; onChanged: () => void }) {
  const [tick, setTick] = useState(0);
  const [load, reload] = useLoad<BusinessLoanDetail>(() => api.getLoan(loanId), [loanId, tick]);
  const changed = () => { setTick((n) => n + 1); onChanged(); };
  return <Drawer title={load.state === 'ready' ? load.value.lender : t('loans.drawerTitle')} onClose={onClose}>
    {load.state === 'loading' && <Note>{t('loans.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {load.state === 'ready' && <LoanBody loan={load.value} list={list} manage={manage} onChanged={changed} />}
  </Drawer>;
}

function ScheduleTable({ rows, currency, caption }: { rows: LoanScheduleRow[]; currency: string; caption: string }) {
  return <Table caption={caption}>
    <thead><tr><th scope="col">{t('loans.col.due')}</th><th scope="col" className="num">{t('loans.col.payment')}</th><th scope="col" className="num bzHideSm">{t('loans.col.interest')}</th>
      <th scope="col" className="num bzHideSm">{t('loans.col.principalPart')}</th><th scope="col" className="num">{t('loans.col.balance')}</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.number}><th scope="row" className="nowrap">{dayText(row.due)}</th><td className="num nowrap"><Amount value={row.payment} currency={currency} /></td>
      <td className="num nowrap bzHideSm"><Amount value={row.interest} currency={currency} /></td><td className="num nowrap bzHideSm"><Amount value={row.principal} currency={currency} /></td>
      <td className="num nowrap"><Amount value={row.balance} currency={currency} /></td></tr>)}</tbody>
  </Table>;
}

function LoanBody({ loan, list, manage, onChanged }: { loan: BusinessLoanDetail; list: BusinessLoanList; manage: boolean; onChanged: () => void }) {
  const cur = loan.currency;
  const [voiding, setVoiding] = useState<LoanRepayment | 'loan' | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const latest = loan.repayment_rows.find((row) => row.status === 'active');
  return <>
    <ul className="bkList">
      <li><span>{t('loans.principal')}</span><Amount value={loan.principal} currency={cur} /></li>
      <li><span>{t('loans.terms')}</span><span><bdi>{loan.annual_rate}%</bdi> · {loanMethodLabel(loan.method)} · {t('loans.periods', { count: loan.periods, frequency: frequencyLabel(loan.frequency) })}</span></li>
      <li><span>{t('loans.col.outstanding')}</span><Amount value={loan.outstanding} currency={cur} /></li>
      <li><span>{t('loans.accrued')}</span><Amount value={loan.interest_accrued} currency={cur} /></li>
      <li><span>{t('loans.maturity')}</span><span>{dayText(loan.maturity)}</span></li>
      {loan.received_adjustment_id && <li><span>{t('loans.matchedReceived')}</span><span>{t('loans.yes')}</span></li>}
    </ul>
    {loan.status === 'void' && <Note>{t('loans.voidText')}</Note>}
    {loan.can_edit && loan.status === 'active' && <RepaymentForm loan={loan} list={list} onDone={onChanged} />}
    <h3 className="bkSub">{t('loans.actualTitle')}</h3>
    <Table caption={t('loans.actualTitle')}>
      <thead><tr><th scope="col">{t('loans.col.due')}</th><th scope="col" className="num">{t('loans.col.payment')}</th><th scope="col" className="num">{t('loans.col.paid')}</th>
        <th scope="col" className="num bzHideSm">{t('loans.col.difference')}</th><th scope="col">{t('loans.col.state')}</th></tr></thead>
      <tbody>{loan.actual.map((row) => <tr key={row.number}><th scope="row" className="nowrap">{dayText(row.due)}</th><td className="num nowrap"><Amount value={row.payment} currency={cur} /></td>
        <td className="num nowrap"><Amount value={row.paid} currency={cur} /></td><td className="num nowrap bzHideSm"><Amount value={row.difference} currency={cur} /></td>
        <td><Badge tone={row.state === 'paid' ? 'success' : row.state === 'overdue' ? 'dangerTone' : row.state === 'part_paid' || row.state === 'due' ? 'warning' : 'neutral'}>{actualStateLabel(row.state)}</Badge></td></tr>)}</tbody>
    </Table>
    {loan.revised.length > 0 && <><h3 className="bkSub">{t('loans.revisedTitle')}</h3><p className="muted bzHint">{t('loans.revisedHelp', { rule: earlyLabel(loan.early_repayment) })}</p>
      <ScheduleTable rows={loan.revised} currency={cur} caption={t('loans.revisedTitle')} /></>}
    <h3 className="bkSub">{t('loans.repaymentsTitle')}</h3>
    {loan.repayment_rows.length === 0 ? <p className="muted">{t('loans.repaymentsEmpty')}</p> : <ul className="bkList">{loan.repayment_rows.map((row) => <li key={row.id}>
      <span>{dayText(row.paid_on)}<small>{t('loans.split', { interest: money(row.interest, cur), principal: money(row.principal, cur) })}
        {row.bill_id && ` · ${t('loans.matchedBill')}`}{row.adjustment_id && ` · ${t('loans.matchedAdjustment')}`}{row.status === 'void' && ` · ${t('loans.void')}`}</small></span>
      <span className="bzInline"><Amount value={row.amount} currency={cur} />{loan.can_edit && row.status === 'active' && row.id === latest?.id &&
        <button type="button" className="secondary" onClick={() => setVoiding(row)} aria-label={t('loans.voidRepaymentLabel', { day: dayText(row.paid_on) })}>{t('loans.voidRepayment')}</button>}</span>
    </li>)}</ul>}
    {loan.accruals.length > 0 && <><h3 className="bkSub">{t('loans.accrualsTitle')}</h3><ul className="bkList">{loan.accruals.map((row) =>
      <li key={row.month_end}><span>{dayText(row.month_end)}</span><Amount value={row.amount} currency={cur} /></li>)}</ul></>}
    <h3 className="bkSub">{t('loans.scheduleTitle')}</h3>
    <ScheduleTable rows={loan.schedule} currency={cur} caption={t('loans.scheduleTitle')} />
    {manage && loan.status === 'active' && loan.repayments === 0 && <div className="actions"><button type="button" className="danger" onClick={() => setVoiding('loan')}>{t('loans.voidLoan')}</button></div>}
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    {voiding && <ReasonDialog title={voiding === 'loan' ? t('loans.voidLoan') : t('loans.voidRepayment')} label={t('loans.voidReason')} confirm={t('loans.voidConfirm')}
      onCancel={() => setVoiding(null)} onConfirm={async (reason) => {
        if (reason.length < 5) throw new Error(t('loans.problem.reason'));
        if (voiding === 'loan') await api.voidLoan(loan.id, reason); else await api.voidRepayment(loan.id, voiding.id, reason);
        setVoiding(null); setFlash({ text: t('loans.voided') }); onChanged();
      }} />}
  </>;
}

function RepaymentForm({ loan, list, onDone }: { loan: BusinessLoanDetail; list: BusinessLoanList; onDone: () => void }) {
  const [form, setForm] = useState<RepaymentForm>({ paid_on: todayIso(), amount: loan.next_payment ?? '', match: '' });
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flash>(null);
  const ids = { day: useId(), amount: useId(), match: useId() };
  const options = [...list.match.bills.filter((row) => row.currency === loan.currency).map((row) => ({ value: `bill:${row.id}`, row, kind: 'bill' })),
    ...list.match.repayments.filter((row) => row.currency === loan.currency).map((row) => ({ value: `adjustment:${row.id}`, row, kind: 'adjustment' }))];
  async function submit(event: FormEvent) {
    event.preventDefault();
    const wrong = repaymentProblem(form, todayIso(), loan.start_date);
    if (wrong) { setFlash({ text: t(wrong), error: true }); return; }
    setBusy(true); setFlash(null);
    try {
      const made = await api.recordRepayment(loan.id, repaymentBody(form));
      setFlash({ text: t('loans.recorded', { interest: money(made.interest, made.currency), principal: money(made.principal, made.currency) }) }); onDone();
    } catch (error) { setFlash({ text: failure(error, t('loans.record')), error: true }); } finally { setBusy(false); }
  }
  return <form className="surface bzCard" onSubmit={(event) => void submit(event)} noValidate aria-labelledby={`${ids.day}-title`}>
    <h3 id={`${ids.day}-title`} className="bkSub">{t('loans.recordTitle')}</h3>
    <div className="bzGrid3">
      <label className="field" htmlFor={ids.day}><span>{t('loans.paidOn')}</span><input id={ids.day} type="date" min={loan.start_date} max={todayIso()} value={form.paid_on}
        onChange={(event) => setForm({ ...form, paid_on: event.target.value })} /></label>
      <label className="field" htmlFor={ids.amount}><span>{t('loans.amount', { currency: loan.currency })}</span><input id={ids.amount} inputMode="decimal" maxLength={16} value={form.amount}
        onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
      {options.length > 0 && <label className="field" htmlFor={ids.match}><span>{t('loans.match')}</span><select id={ids.match} value={form.match}
        onChange={(event) => { const found = options.find((item) => item.value === event.target.value); setForm({ ...form, match: event.target.value,
          ...(found ? { amount: found.row.amount, paid_on: found.row.date } : {}) }); }}>
        <option value="">{t('loans.matchCash')}</option>
        {options.map((item) => <option key={item.value} value={item.value}>{label(`loans.matchKind.${item.kind}`)}: {dayText(item.row.date)} · {money(item.row.amount, item.row.currency)}</option>)}</select></label>}
    </div>
    <p className="muted bzHint">{t('loans.recordHelp')}</p>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    <div className="actions"><button type="submit" disabled={busy}><HandCoins aria-hidden="true" />{busy ? t('dialog.working') : t('loans.record')}</button></div>
  </form>;
}

// ---- revaluation ----------------------------------------------------------------------------------------------------------------

export function RevaluationSection({ onSection }: { onSection: (section: string) => void }) {
  const ends = monthEnds(todayIso());
  const [periodEnd, setPeriodEnd] = useState(ends[0]);
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false), [flash, setFlash] = useState<Flash>(null);
  const [load, reload] = useLoad<RevaluationPreview>(() => api.getRevaluation(periodEnd), [periodEnd, tick]);
  const endId = useId();
  async function run() {
    setBusy(true); setFlash(null);
    try {
      const result = await api.revalue(periodEnd);
      setFlash(result.missing.length ? { text: t('reval.doneMissing', { currencies: result.missing.join(', ') }), error: true }
        : { text: t('reval.done', { posted: result.posted, reversed: result.reversed }) });
      setTick((n) => n + 1);
    } catch (error) { setFlash({ text: failure(error, t('reval.run')), error: true }); } finally { setBusy(false); }
  }
  const value = load.state === 'ready' ? load.value : null;
  const closed = Boolean(value?.closed_through && value.closed_through >= periodEnd);
  return <>
    <p className="muted bzHint">{t('reval.help')}</p>
    <div className="bzToolbar">
      <label className="bzCompact" htmlFor={endId}><span>{t('reval.monthEnd')}</span><select id={endId} value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)}>
        {ends.map((item) => <option key={item} value={item}>{dayText(item)}</option>)}</select></label>
      {value?.can_edit && <div className="bzToolbarEnd"><button type="button" disabled={busy || closed || !value.ended || value.currencies.length === 0} onClick={() => void run()}>
        <RefreshCw aria-hidden="true" />{busy ? t('dialog.working') : t('reval.run')}</button></div>}
    </div>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    {load.state === 'loading' && <Note>{t('reval.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {value && <>
      {closed && <Note>{t('reval.closed', { day: dayText(value.closed_through) })}</Note>}
      {!value.can_edit && <Note>{t('reval.accountantsOnly')}</Note>}
      {value.currencies.length === 0 ? <Empty title={t('reval.emptyTitle')} text={t('reval.emptyText', { currency: value.base_currency ?? '' })} />
        : value.currencies.map((item) => <section key={item.currency} className="surface bzCard bkWide" aria-labelledby={`reval-${item.currency}`}>
          <h2 id={`reval-${item.currency}`} className="bkSub">{t('reval.currencyTitle', { currency: item.currency })} <Badge tone={item.status === 'missing_rate' ? 'warning' : item.status === 'revalued' ? 'success' : 'neutral'}>
            {revaluationStatusLabel(item.status)}</Badge></h2>
          {item.status === 'missing_rate' ? <Note error>{t('reval.missing', { currency: item.currency, day: dayText(value.period_end) })}{' '}
            <button type="button" className="linkButton" onClick={() => onSection('settings')}>{t('reval.addRate')}</button></Note>
            : <p className="muted">{t('reval.rate', { currency: item.currency, rate: item.rate ?? '-', base: value.base_currency ?? '', day: dayText(item.rate_date), source: item.source ?? '-' })}</p>}
          <Table caption={t('reval.caption', { currency: item.currency })}>
            <thead><tr><th scope="col">{t('reval.col.account')}</th><th scope="col" className="num">{t('reval.col.original', { currency: item.currency })}</th>
              <th scope="col" className="num bzHideSm">{t('reval.col.booked')}</th><th scope="col" className="num bzHideSm">{t('reval.col.closing')}</th>
              <th scope="col" className="num">{t('reval.col.difference')}</th></tr></thead>
            <tbody>{item.accounts.map((row) => <tr key={row.account_id}><th scope="row"><bdi>{row.code}</bdi> {accountName({ system_key: row.system_key, name: row.name })}</th>
              <td className="num nowrap"><Amount value={row.original} currency={item.currency} /></td>
              <td className="num nowrap bzHideSm"><Amount value={row.booked} currency={value.base_currency ?? ''} /></td>
              <td className="num nowrap bzHideSm"><Amount value={row.closing} currency={value.base_currency ?? ''} /></td>
              <td className="num nowrap"><Amount value={row.difference} currency={value.base_currency ?? ''} /></td></tr>)}
              {item.status !== 'missing_rate' && <tr className="bk-total"><th scope="row">{t('reval.total')}</th><td /><td className="bzHideSm" /><td className="bzHideSm" />
                <td className="num nowrap"><Amount value={item.difference} currency={value.base_currency ?? ''} /></td></tr>}</tbody>
          </Table>
        </section>)}
    </>}
  </>;
}

// ---- the group balance sheet ------------------------------------------------------------------------------------------------------

export function GroupBalanceSheetCard({ base }: { base: string }) {
  const [asOf, setAsOf] = useState(todayIso());
  const [currency, setCurrency] = useState(base);
  const [load, reload] = useLoad<GroupBalanceSheet>(() => api.getGroupBalanceSheet({ as_of: asOf, currency }), [asOf, currency]);
  const ids = { asOf: useId(), currency: useId() };
  const value = load.state === 'ready' ? load.value : null;
  const names = new Map((value?.businesses ?? []).map((item) => [item.workspace_id, item.name || item.workspace_id]));
  const included = value?.included ?? [];
  return <section className="surface bzCard bkWide groupBs" aria-labelledby="group-bs-title">
    <h2 id="group-bs-title" className="bkSub">{t('groupbs.title')}</h2>
    <p className="muted bzHint">{t('groupbs.help')}</p>
    <div className="bzToolbar">
      <label className="bzCompact" htmlFor={ids.asOf}><span>{t('groupbs.asOf')}</span><input id={ids.asOf} type="date" max={todayIso()} value={asOf} onChange={(event) => setAsOf(event.target.value || todayIso())} /></label>
      <label className="bzCompact" htmlFor={ids.currency}><span>{t('groupbs.currency')}</span><select id={ids.currency} value={currency} onChange={(event) => setCurrency(event.target.value)}>
        {(BOOK_CURRENCIES.includes(currency as never) ? BOOK_CURRENCIES : [currency, ...BOOK_CURRENCIES]).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
    </div>
    {load.state === 'loading' && <Note>{t('groupbs.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {value && !value.enough && <Empty title={t('groupbs.notEnoughTitle')} text={t('groupbs.notEnoughText')} />}
    {value?.enough && value.rows && <>
      <div className="bzInline"><Badge tone={value.balanced ? 'success' : 'dangerTone'}>{value.balanced ? t('groupbs.balanced') : t('groupbs.unbalanced', { difference: value.difference ?? '' })}</Badge>
        <Badge tone={value.complete ? 'success' : 'warning'}>{value.complete ? t('groupbs.complete') : t('groupbs.incomplete')}</Badge></div>
      <Table caption={t('groupbs.caption', { currency: value.currency ?? '' })}>
        <thead><tr><th scope="col">{t('books.col.line')}</th>{included.map((workspace) => <th key={workspace} scope="col" className="num"><span dir="auto">{names.get(workspace)}</span></th>)}
          <th scope="col" className="num">{t('groupbs.eliminations')}</th><th scope="col" className="num">{t('groupbs.total')}</th></tr></thead>
        <tbody>{value.rows.map((row) => <tr key={row.key} className={GROUP_TOTAL_ROWS.includes(row.key) ? 'bk-total' : 'bk-line'}>
          <th scope="row">{groupRowLabel(row.key)}</th>
          {included.map((workspace) => <td key={workspace} className="num nowrap"><Amount value={row.businesses[workspace]} currency={value.currency ?? ''} /></td>)}
          <td className="num nowrap"><Amount value={row.eliminations} currency={value.currency ?? ''} /></td><td className="num nowrap"><Amount value={row.total} currency={value.currency ?? ''} /></td>
        </tr>)}</tbody>
      </Table>
      {(value.businesses ?? []).some((item) => !item.complete) && <Note>{t('groupbs.notPosted', { names: (value.businesses ?? []).filter((item) => !item.complete).map((item) => item.name).join(', ') })}</Note>}
      {(value.not_converted ?? []).length > 0 && <section aria-labelledby="group-bs-missing"><h3 id="group-bs-missing" className="bkSub">{t('groupbs.notConvertedTitle')}</h3>
        <ul className="bkNotes">{(value.not_converted ?? []).map((item) => <li key={item.workspace_id}>{t('groupbs.notConverted', { name: item.name, currency: item.currency, count: item.missing.length + item.missing_days })}</li>)}</ul></section>}
      {(value.rates ?? []).length > 0 && <section aria-labelledby="group-bs-rates"><h3 id="group-bs-rates" className="bkSub">{t('groupbs.ratesTitle')}</h3>
        <ul className="bkNotes">{(value.rates ?? []).map((item) => <li key={item.workspace_id}>{t('groupbs.rate', { name: names.get(item.workspace_id) ?? '', from: item.from_currency, rate: item.rate,
          to: item.to_currency, day: dayText(item.as_of), source: item.source })}</li>)}</ul></section>}
      <p className="muted bzHint">{t('groupbs.method')}</p>
    </>}
  </section>;
}

