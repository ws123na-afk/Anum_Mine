// Business tax (docs/business-tax.md): the Tax section of Business books. Settings (jurisdiction, VAT registration, filing
// frequency, the rate table, the ZATCA switch), the VAT return read from the journal (boxes, warnings, the tie-out to the
// VAT accounts, documents behind each box, CSV) and the ZATCA readiness panel (what is missing, the not-verified notice,
// generated e-invoice XML). The API is the authority: it files nothing and refuses every ZATCA submission.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Download, FileCode2, Plus, Save, ShieldAlert, Trash2 } from 'lucide-react';
import type { BooksSetup, TaxCode, TaxOverview, TaxRate, TaxReturn, TaxReturnDocument, ZatcaPanel } from '@anum/contracts';
import { TAX_CODES, TAX_FREQUENCIES, TAX_JURISDICTIONS } from '@anum/contracts';
import * as api from './lib/businessTaxApi';
import {
  boxGroups, boxLabel, codeLabel, formFrom, hasAdjustments, infoLabel, notesFor, jurisdictionLabel, missingLabel, sectionTitle, settingsBody, settingsProblem, shortHash, warningText,
  type TaxForm,
} from './lib/businessTax';
import { isNegative, money, todayIso } from './lib/businessBooks';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { Drawer } from './Dialog';
import { Badge, Denied, Empty, Failed, Note, Pager, day, failure } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';

type Part = 'settings' | 'return' | 'zatca';
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied'; message: string } | { state: 'error'; message: string };
const PARTS: Part[] = ['return', 'settings', 'zatca'];
const label = (key: string) => t(key as MessageKey);
const dayText = (value: string | null | undefined) => (value ? day(value) : '-');

/** ``quiet``: a reload after a change keeps showing what is on screen until the fresh answer arrives (no flash, no lost message). */
function useLoad<T>(fetch: () => Promise<T>, deps: unknown[], quiet = false): [Load<T>, () => Promise<void>] {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' });
  const run = useCallback(async () => {
    setLoad((current) => (quiet && current.state === 'ready' ? current : { state: 'loading' }));
    try { setLoad({ state: 'ready', value: await fetch() }); }
    catch (error) { setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) }); }
  }, deps);
  useEffect(() => { void run(); }, [run]);
  return [load, run];
}

function Amount({ value, currency }: { value: string; currency: string }) {
  return <span className={isNegative(value) ? 'bkNeg' : undefined}><bdi>{money(value, currency)}</bdi></span>;
}

export function TaxSection({ setup }: { setup: BooksSetup }) {
  const [tick, setTick] = useState(0);
  const [overview, reload] = useLoad<TaxOverview>(() => api.getOverview(), [tick], true);
  const [part, setPart] = useState<Part | null>(null);
  if (overview.state === 'loading') return <Note>{t('tax.loading')}</Note>;
  if (overview.state === 'denied') return <Denied text={overview.message} />;
  if (overview.state === 'error') return <Failed text={overview.message} onRetry={() => void reload()} />;
  const value = overview.value;
  const current: Part = part ?? (value.settings ? 'return' : 'settings');
  return <>
    <p className="muted bzHint">{t('tax.help')}</p>
    <nav className="bkNav taxParts" aria-label={t('tax.parts')}>{PARTS.map((item) =>
      <button key={item} type="button" className="secondary" aria-current={current === item ? 'page' : undefined} onClick={() => setPart(item)}>{label(`tax.part.${item}`)}</button>)}</nav>
    {current === 'settings' && <SettingsPart overview={value} country={setup.settings?.country ?? null} onSaved={() => { setPart('settings'); setTick((n) => n + 1); }} />}
    {current === 'return' && <ReturnPart overview={value} onSettings={() => setPart('settings')} />}
    {current === 'zatca' && <ZatcaPart onSettings={() => setPart('settings')} />}
  </>;
}

// ---- settings and rates ------------------------------------------------------------------------------------------------

function SettingsPart({ overview, country, onSaved }: { overview: TaxOverview; country: string | null; onSaved: () => void }) {
  const settings = overview.settings;
  const [form, setForm] = useState<TaxForm>(() => formFrom(settings, country));
  const [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const edit = overview.can_edit;
  const ids = { jurisdiction: useId(), number: useId(), date: useId(), frequency: useId(), cycle: useId(), crn: useId(), street: useId(), building: useId(),
    district: useId(), city: useId(), postal: useId() };
  const set = (patch: Partial<TaxForm>) => setForm((value) => ({ ...value, ...patch }));
  async function save(event: FormEvent) {
    event.preventDefault();
    const wrong = settingsProblem(form, todayIso());
    if (wrong) { setMessage({ text: t(wrong), error: true }); return; }
    setBusy(true); setMessage(null);
    try { await api.saveSettings(settingsBody(form, settings?.version ?? null)); setMessage({ text: t('tax.settings.saved') }); onSaved(); }
    catch (error) { setMessage({ text: failure(error, t('tax.settings.save')), error: true }); }
    finally { setBusy(false); }
  }
  return <>
    {!edit && <Note>{t('tax.settings.ownersOnly')}</Note>}
    {!settings && <Note>{t('tax.settings.notSaved')}</Note>}
    <form className="surface bzCard taxForm" onSubmit={(event) => void save(event)} noValidate aria-labelledby={`${ids.jurisdiction}-title`}>
      <h2 id={`${ids.jurisdiction}-title`} className="bkSub">{t('tax.settings.title')}</h2>
      <p className="muted bzHint">{t('tax.settings.help')}</p>
      <fieldset className="bkFieldset" disabled={!edit || busy}>
        <div className="bzGrid2">
          <label className="field" htmlFor={ids.jurisdiction}><span>{t('tax.settings.jurisdiction')}</span>
            <select id={ids.jurisdiction} value={form.jurisdiction} onChange={(event) => set({ jurisdiction: event.target.value as TaxForm['jurisdiction'] })}>
              {TAX_JURISDICTIONS.map((item) => <option key={item} value={item}>{jurisdictionLabel(item)}</option>)}</select></label>
          <label className="field" htmlFor={ids.number}><span>{t('tax.settings.number')}</span>
            <input id={ids.number} inputMode={form.jurisdiction === 'OTHER' ? 'text' : 'numeric'} maxLength={30} value={form.registration_number} dir="ltr"
              aria-describedby={`${ids.number}-help`} onChange={(event) => set({ registration_number: event.target.value })} />
            <small id={`${ids.number}-help`} className="muted">{t('tax.settings.numberHelp')}</small></label>
          <label className="field" htmlFor={ids.date}><span>{t('tax.settings.date')}</span>
            <input id={ids.date} type="date" max={todayIso()} value={form.registration_date ?? ''} onChange={(event) => set({ registration_date: event.target.value || null })} /></label>
          <label className="field" htmlFor={ids.frequency}><span>{t('tax.settings.frequency')}</span>
            <select id={ids.frequency} value={form.filing_frequency} onChange={(event) => set({ filing_frequency: event.target.value as TaxForm['filing_frequency'] })}>
              {TAX_FREQUENCIES.map((item) => <option key={item} value={item}>{label(`tax.frequency.${item}`)}</option>)}</select></label>
          {form.filing_frequency === 'quarterly' && <label className="field" htmlFor={ids.cycle}><span>{t('tax.settings.cycle')}</span>
            <select id={ids.cycle} value={form.period_start_month} aria-describedby={`${ids.cycle}-help`} onChange={(event) => set({ period_start_month: Number(event.target.value) })}>
              {[1, 2, 3].map((item) => <option key={item} value={item}>{item}</option>)}</select>
            <small id={`${ids.cycle}-help`} className="muted">{t('tax.settings.cycleHelp')}</small></label>}
        </div>
        <label className="bzCheck"><input type="checkbox" checked={form.registered} onChange={(event) => set({ registered: event.target.checked })} />{t('tax.settings.registered')}</label>
        {form.jurisdiction === 'SA' && <fieldset className="bkFieldset taxZatcaFields">
          <legend className="bkSub">{t('tax.settings.zatcaTitle')}</legend>
          <p className="notice taxNotice" role="note"><ShieldAlert aria-hidden="true" />{t('tax.zatca.notice')}</p>
          <label className="bzCheck"><input type="checkbox" checked={form.zatca_enabled} onChange={(event) => set({ zatca_enabled: event.target.checked })} />{t('tax.settings.zatcaSwitch')}</label>
          <p className="muted bzHint">{t('tax.settings.zatcaHelp')}</p>
          <div className="bzGrid3">
            {([['crn', 'seller_crn', 20, 'numeric'], ['street', 'seller_street', 120, 'text'], ['building', 'seller_building', 10, 'numeric'],
              ['district', 'seller_district', 120, 'text'], ['city', 'seller_city', 80, 'text'], ['postal', 'seller_postal_code', 10, 'numeric']] as const).map(([name, field, max, mode]) =>
              <label key={name} className="field" htmlFor={ids[name]}><span>{label(`tax.settings.${name}`)}</span>
                <input id={ids[name]} maxLength={max} inputMode={mode} value={form[field]} dir="auto" onChange={(event) => set({ [field]: event.target.value } as Partial<TaxForm>)} /></label>)}
          </div>
        </fieldset>}
        {message && <Note error={message.error}>{message.text}</Note>}
        {edit && <div className="actions"><button type="submit" disabled={busy}><Save aria-hidden="true" />{busy ? t('dialog.working') : t('tax.settings.save')}</button></div>}
      </fieldset>
    </form>
    <RatesCard overview={overview} onChanged={onSaved} />
  </>;
}

function RatesCard({ overview, onChanged }: { overview: TaxOverview; onChanged: () => void }) {
  const [form, setForm] = useState({ code: 'standard' as TaxCode, rate_pct: '', effective_from: '', effective_to: '', note: '' });
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  const ids = { code: useId(), rate: useId(), from: useId(), to: useId(), note: useId() };
  const edit = overview.can_edit && Boolean(overview.settings);
  const today = todayIso();
  async function add(event: FormEvent) {
    event.preventDefault();
    const rate = Number(form.rate_pct);
    if (!form.rate_pct.trim() || !Number.isFinite(rate) || rate < 0 || rate > 100 || !form.effective_from) { setMessage({ text: t('tax.problem.rate'), error: true }); return; }
    setBusy(true); setMessage(null);
    try {
      await api.addRate({ ...form, rate_pct: form.rate_pct.trim(), effective_to: form.effective_to || null });
      setMessage({ text: t('tax.rates.added') }); setForm({ ...form, rate_pct: '', effective_from: '', effective_to: '', note: '' }); onChanged();
    } catch (error) { setMessage({ text: failure(error, t('tax.rates.add')), error: true }); } finally { setBusy(false); }
  }
  async function remove(rate: TaxRate) {
    setMessage(null);
    try { await api.removeRate(rate.id); onChanged(); } catch (error) { setMessage({ text: failure(error, t('tax.rates.remove')), error: true }); }
  }
  return <section className="surface bzCard" aria-labelledby={`${ids.code}-title`}>
    <h2 id={`${ids.code}-title`} className="bkSub">{t('tax.rates.title')}</h2>
    <p className="muted bzHint">{t('tax.rates.help')}</p>
    {overview.rates.length === 0 ? <p className="muted">{t('tax.rates.empty')}</p>
      : <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('tax.rates.caption')}><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('tax.rates.caption')}</caption>
        <thead><tr><th scope="col">{t('tax.rates.code')}</th><th scope="col">{t('tax.rates.rate')}</th><th scope="col">{t('tax.rates.from')}</th>
          <th scope="col" className="bzHideMd">{t('tax.rates.to')}</th>{edit && <th scope="col"><span className="visuallyHidden">{t('books.col.actions')}</span></th>}</tr></thead>
        <tbody>{overview.rates.map((rate) => <tr key={rate.id}>
          <th scope="row">{codeLabel(rate.code)}{rate.note && <small dir="auto">{rate.note}</small>}</th>
          <td className="nowrap"><bdi>{rate.rate_pct}%</bdi></td><td className="nowrap">{dayText(rate.effective_from)}</td><td className="nowrap bzHideMd">{dayText(rate.effective_to)}</td>
          {edit && <td>{rate.effective_from > today && <button type="button" className="secondary" onClick={() => void remove(rate)}
            aria-label={t('tax.rates.removeLabel', { code: codeLabel(rate.code), date: dayText(rate.effective_from) })}><Trash2 aria-hidden="true" />{t('tax.rates.remove')}</button>}</td>}
        </tr>)}</tbody></table></div>}
    {edit && <form onSubmit={(event) => void add(event)} noValidate className="taxRateForm">
      <div className="bzGrid3">
        <label className="field" htmlFor={ids.code}><span>{t('tax.rates.code')}</span><select id={ids.code} value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value as TaxCode })}>
          {TAX_CODES.map((item) => <option key={item} value={item}>{codeLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.rate}><span>{t('tax.rates.rate')}</span><input id={ids.rate} inputMode="decimal" maxLength={10} value={form.rate_pct}
          onChange={(event) => setForm({ ...form, rate_pct: event.target.value })} /></label>
        <label className="field" htmlFor={ids.from}><span>{t('tax.rates.from')}</span><input id={ids.from} type="date" value={form.effective_from}
          onChange={(event) => setForm({ ...form, effective_from: event.target.value })} /></label>
        <label className="field" htmlFor={ids.to}><span>{t('tax.rates.to')}</span><input id={ids.to} type="date" min={form.effective_from || undefined} value={form.effective_to}
          onChange={(event) => setForm({ ...form, effective_to: event.target.value })} /></label>
        <label className="field" htmlFor={ids.note}><span>{t('tax.rates.note')}</span><input id={ids.note} maxLength={200} value={form.note} dir="auto"
          onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>
      </div>
      <div className="actions"><button type="submit" disabled={busy}><Plus aria-hidden="true" />{busy ? t('dialog.working') : t('tax.rates.add')}</button></div>
    </form>}
    {message && <Note error={message.error}>{message.text}</Note>}
  </section>;
}

// ---- the VAT return --------------------------------------------------------------------------------------------------------

function ReturnPart({ overview, onSettings }: { overview: TaxOverview; onSettings: () => void }) {
  const first = overview.periods[1] ?? overview.periods[0];
  const [period, setPeriod] = useState({ from: first?.from ?? '', to: first?.to ?? '' });
  const [chosen, setChosen] = useState(first ? `${first.from}|${first.to}` : '');
  const [exported, setExported] = useState<{ text: string; error?: boolean } | null>(null);
  const [drill, setDrill] = useState<string | null | undefined>(undefined);
  const [load, reload] = useLoad<TaxReturn>(() => api.getReturn(period), [period.from, period.to]);
  const ids = { preset: useId(), from: useId(), to: useId() };
  if (!overview.settings) return <Empty title={t('tax.return.setupTitle')} text={t('tax.return.setupText')}
    action={<button type="button" onClick={onSettings}>{t('tax.return.goSettings')}</button>} />;
  async function exportCsv() {
    setExported(null);
    try { await api.exportReturn(period); setExported({ text: t('tax.return.exported') }); } catch (error) { setExported({ text: failure(error, t('tax.return.export')), error: true }); }
  }
  const value = load.state === 'ready' ? load.value : null;
  return <>
    <div className="bzToolbar bkPeriod">
      <label className="bzCompact" htmlFor={ids.preset}><span>{t('tax.return.period')}</span>
        <select id={ids.preset} value={chosen} onChange={(event) => {
          setChosen(event.target.value);
          const [from, to] = event.target.value.split('|');
          if (from && to) setPeriod({ from, to });
        }}><option value="">{t('tax.return.custom')}</option>
          {overview.periods.map((item) => <option key={item.from} value={`${item.from}|${item.to}`}>{dayText(item.from)} {t('tax.return.to').toLowerCase()} {dayText(item.to)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.from}><span>{t('tax.return.from')}</span><input id={ids.from} type="date" value={period.from} max={period.to || undefined}
        onChange={(event) => { setChosen(''); setPeriod({ ...period, from: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.to}><span>{t('tax.return.to')}</span><input id={ids.to} type="date" value={period.to} min={period.from || undefined}
        onChange={(event) => { setChosen(''); setPeriod({ ...period, to: event.target.value }); }} /></label>
      <div className="bzToolbarEnd"><button type="button" className="secondary" disabled={!value} onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('tax.return.export')}</button></div>
    </div>
    <p className="muted bzHint">{t('tax.return.help')}</p>
    {exported && <Note error={exported.error}>{exported.text}</Note>}
    {load.state === 'loading' && <Note>{t('tax.return.reading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {value && <ReturnBody value={value} onDrill={setDrill} />}
    {drill !== undefined && <Drawer title={drill ? t('tax.return.docsTitle', { box: drill }) : t('tax.return.docsAll')} onClose={() => setDrill(undefined)}>
      <ReturnDocuments period={period} box={drill} currency={value?.base_currency ?? ''} />
    </Drawer>}
  </>;
}

function ReturnBody({ value, onDrill }: { value: TaxReturn; onDrill: (box: string | null) => void }) {
  const base = value.base_currency;
  const empty = value.boxes.every((box) => box.documents === 0) && value.info.length === 0;
  const adjustments = hasAdjustments(value.boxes);
  return <>
    <div className="bzInline taxStatus">
      <Badge tone={value.complete ? 'success' : 'warning'}>{value.complete ? t('tax.return.complete') : t('tax.return.incomplete')}</Badge>
      <span className="muted">{jurisdictionLabel(value.jurisdiction)}{value.registration_number && <> · <bdi>{value.registration_number}</bdi></>} · {t('tax.return.base', { currency: base })}</span>
      {value.closed && <Badge tone="neutral">{t('tax.return.closed')}</Badge>}
    </div>
    {value.warnings.length > 0 && <section className="surface bzCard taxWarnings" aria-labelledby="tax-warnings-title">
      <h2 id="tax-warnings-title" className="bkSub">{t('tax.return.warningsTitle')}</h2>
      <ul className="bkNotes">{value.warnings.map((item) => <li key={item.code}>{warningText(item)}</li>)}</ul>
    </section>}
    {empty ? <Empty title={t('tax.return.emptyTitle')} text={t('tax.return.emptyText')} /> : <>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('tax.return.caption')}><table className="dataTable bzTable bkStatement taxBoxes">
        <caption className="visuallyHidden">{t('tax.return.caption')}</caption>
        <thead><tr><th scope="col">{t('tax.return.col.box')}</th><th scope="col">{t('tax.return.col.label')}</th><th scope="col" className="num">{t('tax.return.col.amount')}</th>
          <th scope="col" className="num">{t('tax.return.col.vat')}</th>
          {adjustments && <><th scope="col" className="num bzHideMd">{t('tax.return.col.adjAmount')}</th><th scope="col" className="num bzHideMd">{t('tax.return.col.adjVat')}</th></>}
          <th scope="col"><span className="visuallyHidden">{t('tax.return.col.docs')}</span></th></tr></thead>
        {boxGroups(value.boxes).map((group) => <tbody key={group.section}>
          <tr className="bk-group"><th scope="rowgroup" colSpan={adjustments ? 7 : 5}>{sectionTitle(group.section)}</th></tr>
          {group.rows.map((box) => <tr key={box.box} className={box.section.endsWith('total') || box.section === 'net' ? 'bk-total' : 'bk-line'}>
            <td className="nowrap"><bdi>{box.box}</bdi></td>
            <th scope="row">{boxLabel(value.jurisdiction, box)}</th>
            <td className="num nowrap">{box.section === 'net' ? '-' : <Amount value={box.amount} currency={base} />}</td>
            <td className="num nowrap"><Amount value={box.vat} currency={base} /></td>
            {adjustments && <><td className="num nowrap bzHideMd"><Amount value={box.adjustment_amount} currency={base} /></td>
              <td className="num nowrap bzHideMd"><Amount value={box.adjustment_vat} currency={base} /></td></>}
            <td>{box.documents > 0 && <button type="button" className="secondary" onClick={() => onDrill(box.box)}
              aria-label={t('tax.return.documentsLabel', { box: box.box, count: box.documents })}>{t('tax.return.documentsCount', { count: box.documents })}</button>}</td>
          </tr>)}
        </tbody>)}
      </table></div>
      <div className="bzInline"><button type="button" className="secondary" onClick={() => onDrill(null)}>{t('tax.return.allDocuments')}</button></div>
    </>}
    {value.info.length > 0 && <section className="surface bzCard" aria-labelledby="tax-info-title">
      <h2 id="tax-info-title" className="bkSub">{t('tax.return.infoTitle')}</h2>
      <ul className="bkList">{value.info.map((item) => <li key={item.key}><span>{infoLabel(item.key, item.label)}<small>{t('tax.return.col.docs')}: {item.documents}</small></span>
        <bdi className="nowrap">{money(item.amount, base)} · {t('tax.return.col.vat')} {money(item.vat, base)}</bdi></li>)}</ul>
    </section>}
    <section className="surface bzCard" aria-labelledby="tax-journal-title">
      <h2 id="tax-journal-title" className="bkSub">{t('tax.return.journalTitle')}</h2>
      <Note error={!value.journal.ties}>{value.journal.ties ? t('tax.return.journalTies') : t('tax.return.journalDiffers')}</Note>
      <ul className="bkList">
        <li><span>{t('tax.return.outputMove')}</span><Amount value={value.journal.output_vat_movement} currency={base} /></li>
        <li><span>{t('tax.return.outputIn')}</span><Amount value={value.journal.output_vat_in_return} currency={base} /></li>
        <li><span>{t('tax.return.inputMove')}</span><Amount value={value.journal.input_vat_movement} currency={base} /></li>
        <li><span>{t('tax.return.inputIn')}</span><Amount value={value.journal.input_vat_in_return} currency={base} /></li>
        <li><span>{t('tax.return.manual')}</span><bdi className="nowrap">{money(value.journal.manual_output, base)}, {money(value.journal.manual_input, base)}</bdi></li>
      </ul>
    </section>
    {value.notes.length > 0 && <section aria-labelledby="tax-notes-title"><h2 id="tax-notes-title" className="bkSub">{t('tax.return.notesTitle')}</h2>
      <ul className="bkNotes">{notesFor(value.jurisdiction, value.notes).map((note) => <li key={note}>{note}</li>)}</ul></section>}
  </>;
}

function ReturnDocuments({ period, box, currency }: { period: { from: string; to: string }; box: string | null; currency: string }) {
  const list = usePaged((offset) => api.returnDocuments(period, box ?? '', offset, 25), [period.from, period.to, box]);
  return <>
    <ListState load={list.load} empty={list.items.length === 0} filtered={false} onRetry={() => void list.first()} onClear={() => undefined} />
    {list.load.kind === 'ready' && list.items.length === 0 && <p className="muted">{t('tax.return.docsEmpty')}</p>}
    {list.items.length > 0 && <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('tax.return.docsCaption')}><table className="dataTable bzTable">
      <caption className="visuallyHidden">{t('tax.return.docsCaption')}</caption>
      <thead><tr><th scope="col">{t('tax.return.col.date')}</th><th scope="col">{t('tax.return.col.document')}</th><th scope="col">{t('tax.return.col.code')}</th>
        <th scope="col" className="num">{t('tax.return.col.net')}</th><th scope="col" className="num">{t('tax.return.col.vat')}</th></tr></thead>
      <tbody>{list.items.map((row: TaxReturnDocument) => <tr key={`${row.entry_id}-${row.tax_code}`}>
        <td className="nowrap">{dayText(row.entry_date)}</td>
        <th scope="row"><span dir="auto">{row.description}</span><small>{label(`tax.source.${row.source_kind}`)} · {t('tax.return.col.box')} <bdi>{row.box}</bdi></small>
          {row.adjustment && <> <Badge tone="neutral">{t('tax.return.adjustment')}</Badge></>}</th>
        <td>{codeLabel(row.tax_code)}{row.inferred && <> <Badge tone="warning">{t('tax.return.inferred')}</Badge></>}</td>
        <td className="num nowrap"><Amount value={row.net} currency={currency} /></td><td className="num nowrap"><Amount value={row.vat} currency={currency} /></td>
      </tr>)}</tbody></table></div>}
    <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
  </>;
}

// ---- ZATCA readiness -------------------------------------------------------------------------------------------------------

function ZatcaPart({ onSettings }: { onSettings: () => void }) {
  const [tick, setTick] = useState(0);
  const [offset, setOffset] = useState(0);
  const [load, reload] = useLoad<ZatcaPanel>(() => api.getZatca(offset, 25), [tick, offset]);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState<string | null>(null);
  async function generate(invoiceId: string) {
    setBusy(invoiceId); setMessage(null);
    try { await api.generateZatca(invoiceId); setTick((n) => n + 1); } catch (error) { setMessage({ text: failure(error, t('tax.zatca.generate')), error: true }); }
    finally { setBusy(null); }
  }
  async function submit(documentId: string) {
    setMessage(null);
    try { await api.submitZatca(documentId); } catch (error) { setMessage({ text: describeError(error), error: true }); }
  }
  async function xml(documentId: string) {
    setMessage(null);
    try { await api.downloadXml(documentId); } catch (error) { setMessage({ text: failure(error, t('tax.zatca.download')), error: true }); }
  }
  return <>
    <p className="notice taxNotice" role="note"><ShieldAlert aria-hidden="true" />{t('tax.zatca.notice')}</p>
    <p className="muted bzHint">{t('tax.zatca.help')}</p>
    {load.state === 'loading' && <Note>{t('tax.loading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {load.state === 'ready' && <ZatcaBody panel={load.value} busy={busy} onSettings={onSettings} onGenerate={generate} onSubmit={submit} onXml={xml}
      onPage={setOffset} />}
    {message && <Note error={message.error}>{message.text}</Note>}
  </>;
}

function ZatcaBody({ panel, busy, onSettings, onGenerate, onSubmit, onXml, onPage }: {
  panel: ZatcaPanel; busy: string | null; onSettings: () => void; onGenerate: (id: string) => void; onSubmit: (id: string) => void; onXml: (id: string) => void;
  onPage: (offset: number) => void;
}) {
  const page = panel.invoices;
  return <>
    <section className="surface bzCard" aria-labelledby="zatca-status-title">
      <h2 id="zatca-status-title" className="bkSub">{t('tax.zatca.title')}</h2>
      <p><Badge tone={panel.enabled ? 'success' : 'neutral'}>{panel.enabled ? t('tax.zatca.on') : t('tax.zatca.off')}</Badge>{' '}
        {t('tax.zatca.status', { state: panel.enabled ? t('tax.zatca.on') : t('tax.zatca.off') })}</p>
      {!panel.enabled && <Note>{t('tax.zatca.offText')}</Note>}
      {panel.enabled && <p className="muted">{t('tax.zatca.chain', { counter: panel.counter, generated: panel.generated })}</p>}
      <h3 className="bkSub">{t('tax.zatca.missingTitle')}</h3>
      <ul className="bkNotes taxMissing">{panel.missing.map((key) => <li key={key}>{missingLabel(key)}</li>)}</ul>
      {panel.can_edit && <div className="bzInline"><button type="button" className="secondary" onClick={onSettings}>{t('tax.zatca.goSettings')}</button></div>}
    </section>
    <h2 className="bkSub">{t('tax.zatca.invoicesTitle')}</h2>
    {page.total === 0 ? <Empty title={t('tax.zatca.emptyTitle')} text={t('tax.zatca.emptyText')} />
      : <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('tax.zatca.caption')}><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('tax.zatca.caption')}</caption>
        <thead><tr><th scope="col">{t('tax.zatca.col.invoice')}</th><th scope="col" className="bzHideMd">{t('tax.zatca.col.buyer')}</th>
          <th scope="col" className="bzHideMd">{t('tax.zatca.col.kind')}</th><th scope="col" className="num">{t('tax.zatca.col.total')}</th>
          <th scope="col">{t('tax.zatca.col.data')}</th></tr></thead>
        <tbody>{page.items.map((row) => <tr key={row.invoice_id}>
          <th scope="row"><bdi>{row.number ?? '-'}</bdi><small>{dayText(row.issue_date)}</small>
            {row.uncoded_lines > 0 && <small>{t('tax.zatca.uncoded', { count: row.uncoded_lines })}</small>}</th>
          <td className="bzHideMd"><span dir="auto">{row.buyer || '-'}</span></td>
          <td className="bzHideMd">{label(`tax.zatca.kind.${row.kind}`)}</td>
          <td className="num nowrap"><Amount value={row.total} currency={row.currency} /></td>
          <td>{row.document ? <div className="bzInline taxDoc">
            <small title={row.document.invoice_hash}>{t('tax.zatca.generated', { counter: row.document.counter, hash: shortHash(row.document.invoice_hash) })}</small>
            <button type="button" className="secondary" onClick={() => onXml(row.document!.id)} aria-label={t('tax.zatca.downloadLabel', { number: row.number ?? '' })}>
              <FileCode2 aria-hidden="true" />{t('tax.zatca.download')}</button>
            {panel.can_edit && <button type="button" className="secondary" onClick={() => onSubmit(row.document!.id)} aria-label={t('tax.zatca.submitLabel', { number: row.number ?? '' })}>
              {t('tax.zatca.submit')}</button>}
          </div> : panel.enabled && panel.can_edit ? <button type="button" disabled={busy === row.invoice_id} onClick={() => onGenerate(row.invoice_id)}
            aria-label={t('tax.zatca.generateLabel', { number: row.number ?? '' })}>{busy === row.invoice_id ? t('dialog.working') : t('tax.zatca.generate')}</button> : '-'}</td>
        </tr>)}</tbody></table></div>}
    {page.total > page.limit && <div className="bzInline bzMore">
      <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}>{t('tax.page.previous')}</button>
      <span className="muted">{t('tax.page.range', { from: page.offset + 1, to: Math.min(page.offset + page.limit, page.total), total: page.total })}</span>
      <button type="button" className="secondary" disabled={page.offset + page.limit >= page.total} onClick={() => onPage(page.offset + page.limit)}>{t('tax.page.next')}</button>
    </div>}
  </>;
}
