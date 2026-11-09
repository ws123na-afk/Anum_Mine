// The forms of the business books (docs/business-books.md#web): settings with cost centres and exchange rates, vendors,
// bills and their payments (by hand, or with an allocation a member made from their own Finance ledger), how an
// allocation is counted, budgets, journal adjustments and intercompany entries. Each form checks what it can before
// sending; the server checks everything again and its refusal is shown as it says it.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import type {
  BooksAdjustmentInput, BooksAdjustmentKind, BooksAllocation, BooksBasis, BooksBill, BooksBudget, BooksBudgetInput, BooksCostCentre, BooksCounterparty,
  BooksIntercompanyInput, BooksIntercompanyKind, BooksDirection, BooksPaymentMethod, BooksRate, BooksSection, BooksSetup, BooksTagSection, BooksVendor,
  BusinessProject,
} from '@anum/contracts';
import { BOOKS_ADJUSTMENT_KINDS, BOOKS_INTERCOMPANY_KINDS, BOOKS_PAYMENT_METHODS, BOOKS_RECURRENCES, BOOKS_SECTIONS } from '@anum/contracts';
import * as api from './lib/businessBooksApi';
import {
  BOOK_CURRENCIES, COUNTRIES, adjustmentHasSection, adjustmentLabel, adjustmentSpreads, billBody, billProblem, budgetProblem, emptyBill, intercompanyProblem,
  lineLabel, money, monthLabel, newKey, normalizeCategory, paymentStateLabel, paymentStateTone, sectionLabel, settingsBody, todayIso, validAmount,
  validCategory, validRate, type BillDraft,
} from './lib/businessBooks';
import { describeError } from './lib/errors';
import { minorDigits } from './lib/finance';
import { t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Note, ReasonDialog, day, failure } from './BusinessParts';
import { EvidencePanel } from './BusinessBooksEvidence';

const label = (key: string) => t(key as MessageKey);
const dayText = (value: string | null | undefined) => (value ? day(value) : '-');

export function Actions({ busy, save, onCancel }: { busy: boolean; save: string; onCancel: () => void }) {
  return <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : save}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>;
}

export function useProjects() {
  const [projects, setProjects] = useState<BusinessProject[]>([]);
  useEffect(() => { api.businessProjects().then((page) => setProjects(page.items), () => setProjects([])); }, []);
  return projects;
}

export function CurrencySelect({ id, value, onChange, disabled }: { id: string; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  const options = BOOK_CURRENCIES.includes(value as never) ? BOOK_CURRENCIES : [value, ...BOOK_CURRENCIES];
  return <select id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{options.map((code) => <option key={code} value={code}>{code}</option>)}</select>;
}

export function CentreSelect({ id, setup, value, onChange }: { id: string; setup: BooksSetup; value: string; onChange: (value: string) => void }) {
  return <select id={id} value={value} onChange={(event) => onChange(event.target.value)}><option value="">{t('books.none')}</option>
    {setup.cost_centres.map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}</select>;
}

// ---- settings, cost centres and rates --------------------------------------------------------------------------------

export function SettingsSection({ setup, onSaved, extra }: { setup: BooksSetup; onSaved: () => void; extra?: ReactNode }) {
  const current = setup.settings;
  const manage = setup.permissions.manage;
  const [form, setForm] = useState(() => ({
    base_currency: current?.base_currency ?? 'SAR', country: current?.country ?? '', fiscal_year_start_month: current?.fiscal_year_start_month ?? 1,
    basis: (current?.basis ?? 'accrual') as BooksBasis, tax_label: current?.tax_label ?? '', rate_max_age_days: String(current?.rate_max_age_days ?? 31),
    mappings: Object.entries(current?.category_sections ?? {}).map(([category, section]) => ({ category, section })) as { category: string; section: BooksTagSection }[],
    policy_note: current?.policy_note ?? '',
    evidence_threshold: current?.evidence_threshold ? String(Number(current.evidence_threshold)) : '',
  }));
  const [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const ids = { currency: useId(), country: useId(), month: useId(), basis: useId(), tax: useId(), age: useId(), policy: useId(), evidence: useId() };
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!/^\d{1,3}$/.test(form.rate_max_age_days) || Number(form.rate_max_age_days) < 1 || Number(form.rate_max_age_days) > 366) { setMessage({ text: t('books.settings.ageInvalid'), error: true }); return; }
    if (form.mappings.some((row) => row.category && !validCategory(normalizeCategory(row.category)))) { setMessage({ text: t('books.categoryInvalid'), error: true }); return; }
    if (form.evidence_threshold.trim() && !validAmount(form.evidence_threshold.trim(), true)) { setMessage({ text: t('books.evidence.thresholdInvalid'), error: true }); return; }
    setBusy(true); setMessage(null);
    try { await api.saveSettings(settingsBody(form, current)); setMessage({ text: t('books.settings.saved') }); onSaved(); }
    catch (error) { setMessage({ text: failure(error, t('books.settings.title')), error: true }); } finally { setBusy(false); }
  }
  const categories = setup.allocation_categories.filter((item) => !form.mappings.some((row) => normalizeCategory(row.category) === item));
  return <div className="bzOverview bkGrid">
    <section className="surface bzCard bkWide" aria-labelledby="bk-settings"><h2 id="bk-settings">{t('books.settings.title')}</h2>
      <p className="muted bzHint">{current ? t('books.settings.help') : t('books.settings.first')}</p>
      <form onSubmit={(event) => void save(event)} aria-label={t('books.settings.title')}>
        <fieldset disabled={!manage} className="bkFieldset"><legend className="visuallyHidden">{t('books.settings.title')}</legend>
          <div className="bzGrid2">
            <label className="field" htmlFor={ids.currency}><span>{t('books.settings.base')}</span><CurrencySelect id={ids.currency} value={form.base_currency} onChange={(value) => setForm({ ...form, base_currency: value })} /></label>
            <label className="field" htmlFor={ids.country}><span>{t('books.settings.country')}</span><select id={ids.country} value={form.country} onChange={(event) => setForm({ ...form, country: event.target.value })}>
              <option value="">{t('books.none')}</option>{COUNTRIES.map((code) => <option key={code} value={code}>{label(`books.country.${code}`)}</option>)}</select></label>
            <label className="field" htmlFor={ids.month}><span>{t('books.settings.fyStart')}</span><select id={ids.month} value={form.fiscal_year_start_month}
              onChange={(event) => setForm({ ...form, fiscal_year_start_month: Number(event.target.value) })}>{Array.from({ length: 12 }, (_, index) => index + 1).map((month) => <option key={month} value={month}>{monthLabel(month)}</option>)}</select></label>
            <label className="field" htmlFor={ids.basis}><span>{t('books.settings.basis')}</span><select id={ids.basis} value={form.basis} onChange={(event) => setForm({ ...form, basis: event.target.value as BooksBasis })}>
              <option value="accrual">{label('books.basis.accrual')}</option><option value="cash">{label('books.basis.cash')}</option></select></label>
            <label className="field" htmlFor={ids.tax}><span>{t('books.settings.taxLabel')}</span><input id={ids.tax} value={form.tax_label} maxLength={40} placeholder={t('books.settings.taxPlaceholder')} onChange={(event) => setForm({ ...form, tax_label: event.target.value })} /></label>
            <label className="field" htmlFor={ids.age}><span>{t('books.settings.rateAge')}</span><input id={ids.age} inputMode="numeric" value={form.rate_max_age_days} onChange={(event) => setForm({ ...form, rate_max_age_days: event.target.value })} /></label>
          </div>
          <p className="muted bzHint">{t('books.settings.taxHelp')}</p>
          {setup.closed_through && <p className="muted bzHint">{t('ledger.settings.frozen', { day: setup.closed_through })}</p>}
          <label className="field" htmlFor={ids.evidence}><span>{t('books.evidence.threshold', { currency: form.base_currency })}</span>
            <input id={ids.evidence} inputMode="decimal" value={form.evidence_threshold} placeholder={t('books.evidence.thresholdOff')}
              aria-describedby={`${ids.evidence}-help`} onChange={(event) => setForm({ ...form, evidence_threshold: event.target.value })} /></label>
          <p id={`${ids.evidence}-help`} className="muted bzHint">{t('books.evidence.thresholdHelp')}</p>
          <label className="field" htmlFor={ids.policy}><span>{t('ledger.settings.policy')}</span>
            <textarea id={ids.policy} rows={3} maxLength={1000} value={form.policy_note} onChange={(event) => setForm({ ...form, policy_note: event.target.value })} /></label>
          <h3 className="bkSub">{t('books.settings.mapTitle')}</h3>
          <p className="muted bzHint">{t('books.settings.mapHelp')}</p>
          {form.mappings.map((row, index) => <div key={index} className="bkRow">
            <label className="field"><span>{t('books.col.category')}</span><input value={row.category} onChange={(event) => setForm({ ...form, mappings: form.mappings.map((item, i) => (i === index ? { ...item, category: event.target.value } : item)) })} /></label>
            <label className="field"><span>{t('books.col.countsAs')}</span><select value={row.section} onChange={(event) => setForm({ ...form, mappings: form.mappings.map((item, i) => (i === index ? { ...item, section: event.target.value as BooksTagSection } : item)) })}>
              {(['cost_of_sales', 'operating_expense', 'capital', 'excluded'] as const).map((item) => <option key={item} value={item}>{sectionLabel(item)}</option>)}</select></label>
            <button type="button" className="secondary iconText" onClick={() => setForm({ ...form, mappings: form.mappings.filter((_, i) => i !== index) })} aria-label={t('books.settings.removeMap', { category: row.category || '-' })}><Trash2 aria-hidden="true" /></button>
          </div>)}
          <div className="bzInline"><button type="button" className="secondary" onClick={() => setForm({ ...form, mappings: [...form.mappings, { category: categories[0] ?? '', section: 'cost_of_sales' }] })}><Plus aria-hidden="true" />{t('books.settings.addMap')}</button></div>
        </fieldset>
        {manage ? <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('books.save')}</button></div> : <p className="muted">{t('books.ownersOnly')}</p>}
        {message && <Note error={message.error}>{message.text}</Note>}
      </form>
    </section>
    {current && <CostCentres setup={setup} onChanged={onSaved} />}
    {current && <Rates setup={setup} />}
    {extra}
  </div>;
}

function CostCentres({ setup, onChanged }: { setup: BooksSetup; onChanged: () => void }) {
  const [code, setCode] = useState(''), [name, setName] = useState(''), [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [archived, setArchived] = useState<BooksCostCentre[] | null>(null);
  const ids = { code: useId(), name: useId() };
  const write = setup.permissions.write;
  async function add(event: FormEvent) {
    event.preventDefault();
    if (!/^[A-Za-z0-9._-]{1,20}$/.test(code.trim()) || name.trim().length < 2) { setMessage({ text: t('books.centres.invalid'), error: true }); return; }
    setBusy(true); setMessage(null);
    try { await api.createCostCentre({ code: code.trim(), name: name.trim() }); setCode(''); setName(''); setMessage({ text: t('books.centres.added') }); onChanged(); }
    catch (error) { setMessage({ text: failure(error, t('books.centres.add')), error: true }); } finally { setBusy(false); }
  }
  async function toggle(item: BooksCostCentre) {
    try { await api.updateCostCentre(item.id, { archived: !item.archived, version: item.version }); setArchived(null); onChanged(); }
    catch (error) { setMessage({ text: failure(error, t('books.centres.title')), error: true }); }
  }
  const showArchived = async () => { try { setArchived((await api.listCostCentres(true, 0, 100)).items); } catch (error) { setMessage({ text: describeError(error), error: true }); } };
  return <section className="surface bzCard" aria-labelledby="bk-centres-set"><h2 id="bk-centres-set">{t('books.centres.title')}</h2>
    {setup.cost_centres.length === 0 ? <p className="muted">{t('books.centres.empty')}</p> : <ul className="bkList">{setup.cost_centres.map((item) => <li key={item.id}>
      <span><strong>{item.code}</strong> <span dir="auto">{item.name}</span></span>{write && <button type="button" className="secondary" onClick={() => void toggle(item)} aria-label={t('books.centres.archiveLabel', { code: item.code })}>{t('books.archive')}</button>}</li>)}</ul>}
    {archived && <ul className="bkList">{archived.length === 0 ? <li className="muted">{t('books.centres.noArchived')}</li> : archived.map((item) => <li key={item.id}><span><strong>{item.code}</strong> <span dir="auto">{item.name}</span></span>
      {write && <button type="button" className="secondary" onClick={() => void toggle(item)}>{t('books.restore')}</button>}</li>)}</ul>}
    {!archived && <div className="bzInline"><button type="button" className="secondary" onClick={() => void showArchived()}>{t('books.showArchived')}</button></div>}
    {write && <form onSubmit={(event) => void add(event)} aria-label={t('books.centres.add')} className="bkRow">
      <label className="field" htmlFor={ids.code}><span>{t('books.centres.code')}</span><input id={ids.code} value={code} maxLength={20} onChange={(event) => setCode(event.target.value)} /></label>
      <label className="field" htmlFor={ids.name}><span>{t('books.col.name')}</span><input id={ids.name} value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
      <button type="submit" disabled={busy}>{t('books.centres.add')}</button></form>}
    {message && <Note error={message.error}>{message.text}</Note>}
  </section>;
}

function Rates({ setup }: { setup: BooksSetup }) {
  const base = setup.settings!.base_currency;
  const [rates, setRates] = useState<BooksRate[] | null>(null), [total, setTotal] = useState(0), [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ base: base === 'USD' ? 'EUR' : 'USD', rate: '', as_of: todayIso(), source: '' });
  const [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null), [removing, setRemoving] = useState<BooksRate | null>(null);
  const ids = { base: useId(), rate: useId(), day: useId(), source: useId() };
  const manage = setup.permissions.manage;
  const load = useCallback(async () => {
    setError(null);
    try { const page = await api.listRates(0, 50, 'manual'); setRates(page.items); setTotal(page.total); } catch (caught) { setError(describeError(caught)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function add(event: FormEvent) {
    event.preventDefault();
    if (!validRate(form.rate)) { setMessage({ text: t('books.rates.invalid'), error: true }); return; }
    if (form.source.trim().length < 3) { setMessage({ text: t('books.rates.needSource'), error: true }); return; }
    setBusy(true); setMessage(null);
    try { await api.createRate({ base: form.base, quote: base, rate: form.rate.trim(), as_of: form.as_of, source: form.source.trim() }); setForm({ ...form, rate: '', source: '' }); setMessage({ text: t('books.rates.added') }); await load(); }
    catch (caught) { setMessage({ text: failure(caught, t('books.rates.add')), error: true }); } finally { setBusy(false); }
  }
  async function remove() {
    if (!removing) return;
    try { await api.deleteRate(removing.id); setRemoving(null); await load(); } catch (caught) { setMessage({ text: failure(caught, t('books.rates.remove')), error: true }); setRemoving(null); }
  }
  return <section className="surface bzCard" aria-labelledby="bk-rates-set"><h2 id="bk-rates-set">{t('books.rates.title')}</h2>
    <p className="muted bzHint">{t('books.rates.help', { currency: base, days: setup.settings!.rate_max_age_days })}</p>
    {error && <Note error>{error} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {rates === null && !error && <Note>{t('books.reading')}</Note>}
    {rates && rates.length === 0 && <p className="muted">{t('books.rates.empty')}</p>}
    {rates && rates.length > 0 && <ul className="bkList">{rates.map((row) => <li key={row.id}><span>{t('books.rates.line', { from: row.base, rate: row.rate, to: row.quote })}
      <small dir="auto">{t('books.rates.recorded', { day: dayText(row.as_of), source: row.source })}</small></span>
      {manage && <button type="button" className="secondary" onClick={() => setRemoving(row)} aria-label={t('books.rates.removeLabel', { pair: `${row.base}/${row.quote}`, day: dayText(row.as_of) })}>{t('books.rates.remove')}</button>}</li>)}</ul>}
    {rates && total > rates.length && <p className="muted bzHint">{t('books.rates.more', { count: total - rates.length })}</p>}
    {manage && <form onSubmit={(event) => void add(event)} aria-label={t('books.rates.add')}>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.base}><span>{t('books.rates.from', { currency: base })}</span><CurrencySelect id={ids.base} value={form.base} onChange={(value) => setForm({ ...form, base: value })} /></label>
        <label className="field" htmlFor={ids.rate}><span>{t('books.rates.rate', { from: form.base, to: base })}</span><input id={ids.rate} inputMode="decimal" value={form.rate} onChange={(event) => setForm({ ...form, rate: event.target.value })} /></label>
        <label className="field" htmlFor={ids.day}><span>{t('books.rates.day')}</span><input id={ids.day} type="date" value={form.as_of} max={todayIso()} onChange={(event) => setForm({ ...form, as_of: event.target.value })} /></label>
        <label className="field" htmlFor={ids.source}><span>{t('books.rates.sourceLabel')}</span><input id={ids.source} value={form.source} maxLength={200} placeholder={t('books.rates.sourcePlaceholder')} onChange={(event) => setForm({ ...form, source: event.target.value })} /></label>
      </div>
      <div className="actions"><button type="submit" disabled={busy || form.base === base}>{busy ? t('dialog.working') : t('books.rates.add')}</button></div></form>}
    {message && <Note error={message.error}>{message.text}</Note>}
    {removing && <ConfirmDialog title={t('books.rates.removeTitle')} confirmLabel={t('books.rates.remove')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('books.rates.removeText')}</p></ConfirmDialog>}
  </section>;
}

// ---- vendors ----------------------------------------------------------------------------------------------------------

export function VendorDialog({ vendor, onClose, onSaved }: { vendor: BooksVendor | null; onClose: () => void; onSaved: (vendor: BooksVendor) => void }) {
  const [form, setForm] = useState({ name: vendor?.name ?? '', tax_number: vendor?.tax_number ?? '', country: vendor?.country ?? '', default_category: vendor?.default_category ?? '',
    default_section: (vendor?.default_section ?? '') as BooksSection | '', notes: vendor?.notes ?? '', archived: vendor?.archived ?? false });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const ids = { name: useId(), tax: useId(), country: useId(), category: useId(), section: useId(), notes: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (form.name.trim().length < 2) { setProblem(t('books.vendors.needName')); return; }
    const category = form.default_category ? normalizeCategory(form.default_category) : '';
    if (category && !validCategory(category)) { setProblem(t('books.categoryInvalid')); return; }
    const body = { name: form.name.trim(), tax_number: form.tax_number.trim(), country: form.country || null, default_category: category || null,
      default_section: form.default_section || null, notes: form.notes.trim() };
    setBusy(true); setProblem(null);
    try { onSaved(vendor ? await api.updateVendor(vendor.id, { ...body, archived: form.archived, version: vendor.version }) : await api.createVendor(body)); }
    catch (error) { setProblem(failure(error, t('books.vendors.title'))); setBusy(false); }
  }
  return <Modal title={vendor ? t('books.vendors.edit') : t('books.vendors.new')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={vendor ? t('books.vendors.edit') : t('books.vendors.new')}>
      <label className="field" htmlFor={ids.name}><span>{t('books.col.name')}</span><input id={ids.name} value={form.name} maxLength={200} onChange={(event) => setForm({ ...form, name: event.target.value })} data-autofocus /></label>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.tax}><span>{t('books.col.taxNumber')}</span><input id={ids.tax} dir="ltr" value={form.tax_number} maxLength={60} onChange={(event) => setForm({ ...form, tax_number: event.target.value })} /></label>
        <label className="field" htmlFor={ids.country}><span>{t('books.col.country')}</span><select id={ids.country} value={form.country} onChange={(event) => setForm({ ...form, country: event.target.value })}>
          <option value="">{t('books.none')}</option>{COUNTRIES.map((code) => <option key={code} value={code}>{label(`books.country.${code}`)}</option>)}</select></label>
        <label className="field" htmlFor={ids.category}><span>{t('books.vendors.category')}</span><input id={ids.category} value={form.default_category} placeholder="office_rent" onChange={(event) => setForm({ ...form, default_category: event.target.value })} /></label>
        <label className="field" htmlFor={ids.section}><span>{t('books.col.countsAs')}</span><select id={ids.section} value={form.default_section} onChange={(event) => setForm({ ...form, default_section: event.target.value as BooksSection | '' })}>
          <option value="">{t('books.none')}</option>{BOOKS_SECTIONS.map((item) => <option key={item} value={item}>{sectionLabel(item)}</option>)}</select></label>
      </div>
      <label className="field" htmlFor={ids.notes}><span>{t('books.notes')}</span><textarea id={ids.notes} rows={2} maxLength={1000} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
      {vendor && <label className="bzCheck"><input type="checkbox" checked={form.archived} onChange={(event) => setForm({ ...form, archived: event.target.checked })} />{t('books.vendors.archived')}</label>}
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.save')} onCancel={onClose} />
    </form>
  </Modal>;
}

// ---- bills ------------------------------------------------------------------------------------------------------------

export function BillDialog({ setup, bill, onClose, onSaved }: { setup: BooksSetup; bill?: BooksBill; onClose: () => void; onSaved: (bill: BooksBill) => void }) {
  const base = setup.settings!.base_currency;
  const [draft, setDraft] = useState<BillDraft>(() => bill ? {
    vendor_id: bill.vendor_id, reference: bill.reference, description: bill.description, bill_date: bill.bill_date, due_date: bill.due_date, currency: bill.currency,
    subtotal: bill.subtotal, tax: bill.tax, category: bill.category, section: bill.section, cost_centre_id: bill.cost_centre_id ?? '', project_id: bill.project_id ?? '',
    recurrence: bill.recurrence ?? '', useful_life_months: bill.useful_life_months ? String(bill.useful_life_months) : '',
  } : emptyBill(todayIso(), base));
  const [vendors, setVendors] = useState<BooksVendor[] | null>(null), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const [key] = useState(() => newKey('bill'));
  const projects = useProjects();
  const ids = { vendor: useId(), ref: useId(), description: useId(), date: useId(), due: useId(), currency: useId(), net: useId(), tax: useId(), category: useId(),
    section: useId(), centre: useId(), project: useId(), recurrence: useId(), life: useId() };
  const [vendorQuery, setVendorQuery] = useState(''), [vendorTotal, setVendorTotal] = useState(0);
  const findId = useId();
  useEffect(() => {
    const timer = setTimeout(() => {
      api.listVendors(vendorQuery.trim(), false, 0, 200).then((page) => {
        // Keep the bill's own vendor in the list while searching, so the select never loses its value.
        const own = bill && !page.items.some((item) => item.id === bill.vendor_id) ? [{ id: bill.vendor_id, name: bill.vendor_name } as BooksVendor] : [];
        setVendors([...own, ...page.items]); setVendorTotal(page.total);
      }, (error) => { setVendors([]); setProblem(describeError(error)); });
    }, vendorQuery ? 250 : 0);
    return () => clearTimeout(timer);
  }, [vendorQuery]);
  const set = (patch: Partial<BillDraft>) => setDraft((current) => ({ ...current, ...patch }));
  const pickVendor = (id: string) => {
    const vendor = vendors?.find((item) => item.id === id);
    set({ vendor_id: id, ...(vendor && !draft.category && vendor.default_category ? { category: vendor.default_category } : {}),
      ...(vendor && !draft.section && vendor.default_section ? { section: vendor.default_section } : {}) });
  };
  const moneyLocked = Boolean(bill && !bill.can.edit_money);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const issue = billProblem(draft);
    if (issue) { setProblem(t(issue)); return; }
    setBusy(true); setProblem(null);
    try { onSaved(bill ? await api.updateBill(bill.id, { ...billBody(draft), version: bill.version }) : await api.createBill(billBody(draft), key)); }
    catch (error) { setProblem(failure(error, t('books.bills.title'))); setBusy(false); }
  }
  const total = validAmount(draft.subtotal, true) && validAmount(draft.tax || '0', true) ? (Number(draft.subtotal) + Number(draft.tax || '0')).toFixed(minorDigits(draft.currency)) : null;
  return <Modal title={bill ? t('books.bills.edit') : t('books.bills.new')} className="formDialog bkWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={bill ? t('books.bills.edit') : t('books.bills.new')}>
      {vendors !== null && vendors.length === 0 && !vendorQuery && <Note>{t('books.bills.noVendors')}</Note>}
      {(vendorTotal > 200 || vendorQuery) && <label className="field" htmlFor={findId}><span>{t('books.bills.findVendor')}</span>
        <input id={findId} type="search" value={vendorQuery} onChange={(event) => setVendorQuery(event.target.value)} /></label>}
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.vendor}><span>{t('books.col.vendor')}</span><select id={ids.vendor} value={draft.vendor_id} onChange={(event) => pickVendor(event.target.value)} data-autofocus>
          <option value="">{vendors === null ? t('books.reading') : t('books.bills.pickVendor')}</option>{(vendors ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field" htmlFor={ids.ref}><span>{t('books.bills.reference')}</span><input id={ids.ref} dir="auto" value={draft.reference} maxLength={80} onChange={(event) => set({ reference: event.target.value })} /></label>
      </div>
      <label className="field" htmlFor={ids.description}><span>{t('books.bills.description')}</span><input id={ids.description} dir="auto" value={draft.description} maxLength={300} onChange={(event) => set({ description: event.target.value })} /></label>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.date}><span>{t('books.bills.date')}</span><input id={ids.date} type="date" value={draft.bill_date} onChange={(event) => set({ bill_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.due}><span>{t('books.col.due')}</span><input id={ids.due} type="date" value={draft.due_date} min={draft.bill_date} onChange={(event) => set({ due_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.currency}><span>{t('books.col.currency')}</span><CurrencySelect id={ids.currency} value={draft.currency} disabled={moneyLocked} onChange={(value) => set({ currency: value })} /></label>
        <label className="field" htmlFor={ids.net}><span>{t('books.bills.net')}</span><input id={ids.net} inputMode="decimal" value={draft.subtotal} disabled={moneyLocked} onChange={(event) => set({ subtotal: event.target.value })} /></label>
        <label className="field" htmlFor={ids.tax}><span>{t('books.bills.tax')}</span><input id={ids.tax} inputMode="decimal" value={draft.tax} disabled={moneyLocked} onChange={(event) => set({ tax: event.target.value })} /></label>
        <p className="bkTotal" aria-live="polite">{t('books.bills.total', { amount: total ? money(total, draft.currency) : '-' })}</p>
      </div>
      {moneyLocked && <p className="muted bzHint">{t('books.bills.moneyLocked')}</p>}
      {draft.currency !== base && <p className="muted bzHint">{t('books.bills.otherCurrency', { currency: draft.currency, base })}</p>}
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.category}><span>{t('books.col.category')}</span><input id={ids.category} value={draft.category} placeholder="office_rent" onChange={(event) => set({ category: event.target.value })} /></label>
        <label className="field" htmlFor={ids.section}><span>{t('books.col.countsAs')}</span><select id={ids.section} value={draft.section} onChange={(event) => set({ section: event.target.value as BooksSection | '' })}>
          <option value="">{t('books.bills.sectionDefault')}</option>{BOOKS_SECTIONS.map((item) => <option key={item} value={item}>{sectionLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.centre}><span>{t('books.col.costCentre')}</span><CentreSelect id={ids.centre} setup={setup} value={draft.cost_centre_id} onChange={(value) => set({ cost_centre_id: value })} /></label>
        <label className="field" htmlFor={ids.project}><span>{t('books.col.project')}</span><select id={ids.project} value={draft.project_id} onChange={(event) => set({ project_id: event.target.value })}>
          <option value="">{t('books.none')}</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field" htmlFor={ids.recurrence}><span>{t('books.bills.recurrence')}</span><select id={ids.recurrence} value={draft.recurrence} onChange={(event) => set({ recurrence: event.target.value as BillDraft['recurrence'] })}>
          <option value="">{t('books.bills.once')}</option>{BOOKS_RECURRENCES.map((item) => <option key={item} value={item}>{label(`books.recurrence.${item}`)}</option>)}</select></label>
        {draft.section === 'capital' && <label className="field" htmlFor={ids.life}><span>{t('books.bills.life')}</span><input id={ids.life} inputMode="numeric" value={draft.useful_life_months} onChange={(event) => set({ useful_life_months: event.target.value })} /></label>}
      </div>
      {draft.section === 'capital' && <p className="muted bzHint">{t('books.bills.capitalHelp')}</p>}
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.save')} onCancel={onClose} />
    </form>
  </Modal>;
}

export function BillDrawer({ setup, billId, onClose, onChanged }: { setup: BooksSetup; billId: string; onClose: () => void; onChanged: () => void }) {
  const [bill, setBill] = useState<BooksBill | null>(null), [error, setError] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  const [mode, setMode] = useState<'pay' | 'edit' | 'void' | { voidPayment: string } | null>(null), [approving, setApproving] = useState(false);
  const load = useCallback(async () => { setError(null); try { setBill(await api.getBill(billId)); } catch (caught) { setError(describeError(caught)); } }, [billId]);
  useEffect(() => { void load(); }, [load]);
  const changed = (next: BooksBill, text: string) => { setBill(next); setMode(null); setMessage(text); onChanged(); };
  async function approve() {
    if (!bill) return;
    setApproving(true);
    try { changed(await api.approveBill(bill.id, bill.version), t('books.bills.approved')); }
    catch (caught) { setMessage(failure(caught, t('books.bills.approve'))); } finally { setApproving(false); }
  }
  return <Drawer title={bill ? `${bill.vendor_name}${bill.reference ? `, ${bill.reference}` : ''}` : t('books.bills.title')} onClose={onClose}>
    {error && <Note error>{error} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {!bill && !error && <Note>{t('books.reading')}</Note>}
    {bill && <div className="bzDrawer">
      <div className="bzLeadHead"><Badge tone={paymentStateTone(bill.payment_state, bill.overdue)}>{paymentStateLabel(bill.payment_state)}</Badge>
        {bill.overdue && <Badge tone="dangerTone">{t('books.daysLate', { days: bill.days_overdue })}</Badge>}{bill.recurrence && <Badge tone="neutral">{label(`books.recurrence.${bill.recurrence}`)}</Badge>}</div>
      <dl className="bzFacts">
        <div><dt>{t('books.bills.description')}</dt><dd dir="auto">{bill.description}</dd></div>
        <div><dt>{t('books.bills.date')}</dt><dd>{dayText(bill.bill_date)}</dd></div>
        <div><dt>{t('books.col.due')}</dt><dd>{dayText(bill.due_date)}</dd></div>
        <div><dt>{t('books.bills.net')}</dt><dd>{money(bill.subtotal, bill.currency)}</dd></div>
        <div><dt>{t('books.bills.tax')}</dt><dd>{money(bill.tax, bill.currency)}</dd></div>
        <div><dt>{t('books.col.total')}</dt><dd><strong>{money(bill.total, bill.currency)}</strong></dd></div>
        <div><dt>{t('books.bills.paid')}</dt><dd>{money(bill.amount_paid, bill.currency)}</dd></div>
        <div><dt>{t('books.col.balance')}</dt><dd>{money(bill.balance, bill.currency)}</dd></div>
        <div><dt>{t('books.col.category')}</dt><dd>{lineLabel(bill.category)}, {sectionLabel(bill.section)}</dd></div>
        {bill.useful_life_months && <div><dt>{t('books.bills.life')}</dt><dd>{bill.useful_life_months}</dd></div>}
        {bill.status === 'void' && <div><dt>{t('books.void')}</dt><dd dir="auto">{bill.void_reason}</dd></div>}
      </dl>
      {bill.template_id && <p className="muted bzHint">{t(bill.status === 'draft' ? 'books.bills.draftHelp' : 'books.bills.fromRecurring', { day: dayText(bill.period_date) })}</p>}
      <div className="bzInline">
        {bill.can.approve && <button type="button" disabled={approving} onClick={() => void approve()}>{approving ? t('dialog.working') : t('books.bills.approve')}</button>}
        {bill.can.pay && <button type="button" onClick={() => setMode('pay')}>{t('books.pay.action')}</button>}
        {bill.can.edit && <button type="button" className="secondary" onClick={() => setMode('edit')}>{t('books.bills.edit')}</button>}
        {bill.can.void && <button type="button" className="secondary danger" onClick={() => setMode('void')}>{t('books.bills.void')}</button>}
      </div>
      {message && <Note>{message}</Note>}
      <EvidencePanel sourceType="bill" sourceId={bill.id} onChanged={onChanged} />
      <section className="bzBlock" aria-labelledby="bk-payments"><h3 id="bk-payments">{t('books.pay.title')}</h3>
        {(bill.payments ?? []).length === 0 ? <p className="muted">{t('books.pay.none')}</p> : <ul className="bkList">{(bill.payments ?? []).map((payment) => <li key={payment.id}>
          <span>{money(payment.amount, payment.currency)} <small>{dayText(payment.paid_on)}, {label(`books.method.${payment.method}`)}{payment.reference ? `, ${payment.reference}` : ''}</small>
            {payment.voided_at && <small>{t('books.voidReason', { reason: payment.void_reason })}</small>}
            {payment.allocation_removed && <small className="bzBlocked">{t('books.pay.allocationGone')}</small>}</span>
          {!payment.voided_at && setup.permissions.manage && <button type="button" className="secondary" onClick={() => setMode({ voidPayment: payment.id })}
            aria-label={t('books.voidLabel', { what: money(payment.amount, payment.currency) })}>{t('books.voidAction')}</button>}
          {payment.voided_at && <Badge tone="neutral">{t('books.void')}</Badge>}</li>)}</ul>}
      </section>
    </div>}
    {bill && mode === 'pay' && <PayDialog bill={bill} onClose={() => setMode(null)} onPaid={(next) => changed(next, t('books.pay.saved'))} />}
    {bill && mode === 'edit' && <BillDialog setup={setup} bill={bill} onClose={() => setMode(null)} onSaved={(next) => changed(next, t('books.bills.saved'))} />}
    {bill && mode === 'void' && <ReasonDialog title={t('books.bills.voidTitle')} label={t('books.reason')} confirm={t('books.bills.void')}
      onConfirm={async (reason) => changed(await api.voidBill(bill.id, reason), t('books.bills.voided'))} onCancel={() => setMode(null)} />}
    {bill && mode && typeof mode === 'object' && <ReasonDialog title={t('books.pay.voidTitle')} label={t('books.reason')} confirm={t('books.voidAction')}
      onConfirm={async (reason) => changed(await api.voidPayment(bill.id, mode.voidPayment, reason), t('books.pay.voided'))} onCancel={() => setMode(null)} />}
  </Drawer>;
}

function PayDialog({ bill, onClose, onPaid }: { bill: BooksBill; onClose: () => void; onPaid: (bill: BooksBill) => void }) {
  const [kind, setKind] = useState<'recorded' | 'allocation'>('recorded');
  const [form, setForm] = useState({ amount: bill.balance, paid_on: todayIso(), method: 'bank_transfer' as BooksPaymentMethod, reference: '', note: '', allocation_id: '' });
  const [allocations, setAllocations] = useState<BooksAllocation[] | null>(null), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const [key] = useState(() => newKey('pay'));
  const ids = { amount: useId(), day: useId(), method: useId(), ref: useId(), allocation: useId(), note: useId() };
  useEffect(() => {
    if (kind !== 'allocation' || allocations !== null) return;
    api.listAllocations('unpaid_bill', '', 0, 200, bill.currency).then((page) => setAllocations(page.items.filter((row) => Number(row.amount) <= Number(bill.balance))),
      (error) => { setAllocations([]); setProblem(describeError(error)); });
  }, [kind, allocations, bill.currency, bill.balance]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (kind === 'recorded' && (!validAmount(form.amount) || Number(form.amount) > Number(bill.balance))) { setProblem(t('books.pay.amountInvalid', { balance: money(bill.balance, bill.currency) })); return; }
    if (kind === 'allocation' && !form.allocation_id) { setProblem(t('books.pay.pickAllocation')); return; }
    setBusy(true); setProblem(null);
    try {
      onPaid(await api.payBill(bill.id, kind === 'allocation' ? { kind, allocation_id: form.allocation_id, note: form.note.trim() }
        : { kind, amount: form.amount.trim(), paid_on: form.paid_on, method: form.method, reference: form.reference.trim(), note: form.note.trim() }, key));
    } catch (error) { setProblem(failure(error, t('books.pay.title'))); setBusy(false); }
  }
  return <Modal title={t('books.pay.dialog', { balance: money(bill.balance, bill.currency) })} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('books.pay.action')}>
      <fieldset className="bkChoice"><legend>{t('books.pay.how')}</legend>
        <label className="bzCheck"><input type="radio" name="kind" checked={kind === 'recorded'} onChange={() => setKind('recorded')} />{t('books.pay.recorded')}</label>
        <label className="bzCheck"><input type="radio" name="kind" checked={kind === 'allocation'} onChange={() => setKind('allocation')} />{t('books.pay.allocation')}</label>
      </fieldset>
      {kind === 'recorded' ? <div className="bzGrid2">
        <label className="field" htmlFor={ids.amount}><span>{t('books.pay.amount', { currency: bill.currency })}</span><input id={ids.amount} inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} data-autofocus /></label>
        <label className="field" htmlFor={ids.day}><span>{t('books.pay.day')}</span><input id={ids.day} type="date" value={form.paid_on} max={todayIso()} onChange={(event) => setForm({ ...form, paid_on: event.target.value })} /></label>
        <label className="field" htmlFor={ids.method}><span>{t('books.pay.method')}</span><select id={ids.method} value={form.method} onChange={(event) => setForm({ ...form, method: event.target.value as BooksPaymentMethod })}>
          {BOOKS_PAYMENT_METHODS.map((item) => <option key={item} value={item}>{label(`books.method.${item}`)}</option>)}</select></label>
        <label className="field" htmlFor={ids.ref}><span>{t('books.pay.reference')}</span><input id={ids.ref} dir="auto" value={form.reference} maxLength={120} onChange={(event) => setForm({ ...form, reference: event.target.value })} /></label>
      </div> : <>
        <p className="muted bzHint">{t('books.pay.allocationHelp')}</p>
        {allocations === null ? <Note>{t('books.reading')}</Note> : allocations.length === 0 ? <Note>{t('books.pay.noAllocations', { currency: bill.currency })}</Note> :
          <label className="field" htmlFor={ids.allocation}><span>{t('books.pay.whichAllocation')}</span><select id={ids.allocation} value={form.allocation_id} onChange={(event) => setForm({ ...form, allocation_id: event.target.value })}>
            <option value="">{t('books.pay.pickAllocation')}</option>{allocations.map((row) => <option key={row.id} value={row.id}>{`${money(row.amount, row.currency)}, ${dayText(row.posted_on)}, ${lineLabel(row.category)}, #${row.source_ref}`}</option>)}</select></label>}
      </>}
      <label className="field" htmlFor={ids.note}><span>{t('books.notes')}</span><input id={ids.note} dir="auto" value={form.note} maxLength={300} onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.pay.action')} onCancel={onClose} />
    </form>
  </Modal>;
}

// ---- allocation tags --------------------------------------------------------------------------------------------------

export function TagDialog({ setup, allocation, onClose, onSaved }: { setup: BooksSetup; allocation: BooksAllocation; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ section: (allocation.tag?.section ?? '') as BooksTagSection | '', cost_centre_id: allocation.tag?.cost_centre_id ?? '', note: allocation.tag?.note ?? '' });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const ids = { section: useId(), centre: useId(), note: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { await api.tagAllocation(allocation.id, { section: form.section || null, cost_centre_id: form.cost_centre_id || null, note: form.note.trim(), version: allocation.tag?.version ?? null }); onSaved(); }
    catch (error) { setProblem(failure(error, t('books.alloc.tagTitle'))); setBusy(false); }
  }
  return <Modal title={t('books.alloc.tagTitle')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('books.alloc.tagTitle')}>
      <p className="muted bzHint">{t('books.alloc.tagHelp', { amount: money(allocation.amount, allocation.currency), category: lineLabel(allocation.category) })}</p>
      <label className="field" htmlFor={ids.section}><span>{t('books.col.countsAs')}</span><select id={ids.section} value={form.section} onChange={(event) => setForm({ ...form, section: event.target.value as BooksTagSection | '' })} data-autofocus>
        <option value="">{t('books.alloc.bySettings')}</option>{(['cost_of_sales', 'operating_expense', 'capital', 'excluded'] as const).map((item) => <option key={item} value={item}>{sectionLabel(item)}</option>)}</select></label>
      <label className="field" htmlFor={ids.centre}><span>{t('books.col.costCentre')}</span><CentreSelect id={ids.centre} setup={setup} value={form.cost_centre_id} onChange={(value) => setForm({ ...form, cost_centre_id: value })} /></label>
      <label className="field" htmlFor={ids.note}><span>{t('books.notes')}</span><input id={ids.note} dir="auto" value={form.note} maxLength={300} onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.save')} onCancel={onClose} />
    </form>
  </Modal>;
}

// ---- budgets ----------------------------------------------------------------------------------------------------------

export function BudgetDialog({ setup, budget, onClose, onSaved }: { setup: BooksSetup; budget: BooksBudget | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<BooksBudgetInput>(() => ({
    name: budget?.name ?? '', period_start: budget?.period_start ?? `${todayIso().slice(0, 8)}01`, months: budget?.months ?? 1, amount: budget?.amount ?? '',
    section: budget?.section ?? null, category: budget?.category ?? null, project_id: budget?.project_id ?? null, cost_centre_id: budget?.cost_centre_id ?? null,
  }));
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const projects = useProjects();
  const ids = { name: useId(), start: useId(), months: useId(), amount: useId(), section: useId(), category: useId(), centre: useId(), project: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = { ...form, name: form.name.trim(), amount: form.amount.trim(), category: form.category ? normalizeCategory(form.category) : null };
    const issue = budgetProblem(body);
    if (issue) { setProblem(t(issue)); return; }
    setBusy(true); setProblem(null);
    try { if (budget) await api.updateBudget(budget.id, { ...body, version: budget.version }); else await api.createBudget(body); onSaved(); }
    catch (error) { setProblem(failure(error, t('books.budget.title'))); setBusy(false); }
  }
  return <Modal title={budget ? t('books.budget.edit') : t('books.budget.new')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={budget ? t('books.budget.edit') : t('books.budget.new')}>
      <label className="field" htmlFor={ids.name}><span>{t('books.col.name')}</span><input id={ids.name} dir="auto" value={form.name} maxLength={120} onChange={(event) => setForm({ ...form, name: event.target.value })} data-autofocus /></label>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.start}><span>{t('books.budget.start')}</span><input id={ids.start} type="month" value={form.period_start.slice(0, 7)} onChange={(event) => setForm({ ...form, period_start: event.target.value ? `${event.target.value}-01` : '' })} /></label>
        <label className="field" htmlFor={ids.months}><span>{t('books.budget.length')}</span><select id={ids.months} value={form.months} onChange={(event) => setForm({ ...form, months: Number(event.target.value) as 1 | 3 | 6 | 12 })}>
          {([1, 3, 6, 12] as const).map((item) => <option key={item} value={item}>{label(`books.budget.len.${item}`)}</option>)}</select></label>
        <label className="field" htmlFor={ids.amount}><span>{t('books.budget.amount', { currency: setup.settings!.base_currency })}</span><input id={ids.amount} inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
        <label className="field" htmlFor={ids.section}><span>{t('books.col.countsAs')}</span><select id={ids.section} value={form.section ?? ''} onChange={(event) => setForm({ ...form, section: (event.target.value || null) as BooksSection | null })}>
          <option value="">{t('books.budget.allCosts')}</option>{(['cost_of_sales', 'operating_expense'] as const).map((item) => <option key={item} value={item}>{sectionLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.category}><span>{t('books.col.category')}</span><input id={ids.category} value={form.category ?? ''} placeholder="office_rent" onChange={(event) => setForm({ ...form, category: event.target.value || null })} /></label>
        <label className="field" htmlFor={ids.centre}><span>{t('books.col.costCentre')}</span><CentreSelect id={ids.centre} setup={setup} value={form.cost_centre_id ?? ''} onChange={(value) => setForm({ ...form, cost_centre_id: value || null })} /></label>
        <label className="field" htmlFor={ids.project}><span>{t('books.col.project')}</span><select id={ids.project} value={form.project_id ?? ''} onChange={(event) => setForm({ ...form, project_id: event.target.value || null })}>
          <option value="">{t('books.none')}</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </div>
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.save')} onCancel={onClose} />
    </form>
  </Modal>;
}

// ---- journal adjustments ----------------------------------------------------------------------------------------------

export function AdjustmentDialog({ setup, onClose, onSaved }: { setup: BooksSetup; onClose: () => void; onSaved: () => void }) {
  const base = setup.settings!.base_currency;
  const [form, setForm] = useState<BooksAdjustmentInput>({ kind: 'accrual', entry_date: todayIso(), currency: base, amount: '', section: null, category: null, cost_centre_id: null,
    project_id: null, description: '', start_month: `${todayIso().slice(0, 8)}01`, months: 12, reverse_on: null });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const [key] = useState(() => newKey('adj'));
  const projects = useProjects();
  const ids = { kind: useId(), date: useId(), currency: useId(), amount: useId(), section: useId(), category: useId(), centre: useId(), project: useId(), description: useId(),
    start: useId(), months: useId(), reverse: useId() };
  const spread = adjustmentSpreads(form.kind);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!validAmount(form.amount)) { setProblem(t('books.amountInvalid')); return; }
    if (form.description.trim().length < 3) { setProblem(t('books.needDescription3')); return; }
    const category = form.category ? normalizeCategory(form.category) : null;
    if (category && !validCategory(category)) { setProblem(t('books.categoryInvalid')); return; }
    if (spread && (!form.start_month || !form.months || form.months < 1)) { setProblem(t('books.journal.needSpread')); return; }
    setBusy(true); setProblem(null);
    try {
      await api.createAdjustment({ ...form, amount: form.amount.trim(), description: form.description.trim(), category,
        section: adjustmentHasSection(form.kind) ? form.section : null, start_month: spread ? form.start_month : null, months: spread ? form.months : null,
        reverse_on: form.kind === 'accrual' ? form.reverse_on : null }, key);
      onSaved();
    } catch (error) { setProblem(failure(error, t('books.journal.title'))); setBusy(false); }
  }
  return <Modal title={t('books.journal.new')} className="formDialog bkWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('books.journal.new')}>
      <label className="field" htmlFor={ids.kind}><span>{t('books.journal.kind')}</span><select id={ids.kind} value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as BooksAdjustmentKind })} data-autofocus>
        {BOOKS_ADJUSTMENT_KINDS.map((item) => <option key={item} value={item}>{adjustmentLabel(item)}</option>)}</select></label>
      <p className="muted bzHint">{label(`books.adjHelp.${form.kind}`)}</p>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.date}><span>{t('books.col.date')}</span><input id={ids.date} type="date" value={form.entry_date} onChange={(event) => setForm({ ...form, entry_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.currency}><span>{t('books.col.currency')}</span><CurrencySelect id={ids.currency} value={form.currency} onChange={(value) => setForm({ ...form, currency: value })} /></label>
        <label className="field" htmlFor={ids.amount}><span>{t('books.col.total')}</span><input id={ids.amount} inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
        {adjustmentHasSection(form.kind) && <label className="field" htmlFor={ids.section}><span>{t('books.col.countsAs')}</span><select id={ids.section} value={form.section ?? ''} onChange={(event) => setForm({ ...form, section: (event.target.value || null) as BooksAdjustmentInput['section'] })}>
          <option value="">{sectionLabel('operating_expense')}</option><option value="cost_of_sales">{sectionLabel('cost_of_sales')}</option></select></label>}
        {spread && <label className="field" htmlFor={ids.start}><span>{t('books.journal.start')}</span><input id={ids.start} type="month" value={(form.start_month ?? '').slice(0, 7)} onChange={(event) => setForm({ ...form, start_month: event.target.value ? `${event.target.value}-01` : null })} /></label>}
        {spread && <label className="field" htmlFor={ids.months}><span>{t('books.journal.months')}</span><input id={ids.months} inputMode="numeric" value={form.months ?? ''} onChange={(event) => setForm({ ...form, months: Number(event.target.value) || null })} /></label>}
        {form.kind === 'accrual' && <label className="field" htmlFor={ids.reverse}><span>{t('books.journal.reverseOn')}</span><input id={ids.reverse} type="date" value={form.reverse_on ?? ''} min={form.entry_date} onChange={(event) => setForm({ ...form, reverse_on: event.target.value || null })} /></label>}
        <label className="field" htmlFor={ids.category}><span>{t('books.col.category')}</span><input id={ids.category} value={form.category ?? ''} onChange={(event) => setForm({ ...form, category: event.target.value || null })} /></label>
        <label className="field" htmlFor={ids.centre}><span>{t('books.col.costCentre')}</span><CentreSelect id={ids.centre} setup={setup} value={form.cost_centre_id ?? ''} onChange={(value) => setForm({ ...form, cost_centre_id: value || null })} /></label>
        <label className="field" htmlFor={ids.project}><span>{t('books.col.project')}</span><select id={ids.project} value={form.project_id ?? ''} onChange={(event) => setForm({ ...form, project_id: event.target.value || null })}>
          <option value="">{t('books.none')}</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </div>
      <label className="field" htmlFor={ids.description}><span>{t('books.journal.why')}</span><input id={ids.description} dir="auto" value={form.description} maxLength={300} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      <p className="muted bzHint">{t('books.journal.trail')}</p>
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.journal.record')} onCancel={onClose} />
    </form>
  </Modal>;
}

// ---- intercompany -----------------------------------------------------------------------------------------------------

export function IntercompanyDialog({ setup, onClose, onSaved }: { setup: BooksSetup; onClose: () => void; onSaved: () => void }) {
  const [parties, setParties] = useState<BooksCounterparty[] | null>(null);
  const [form, setForm] = useState<BooksIntercompanyInput>({ workspace_id: '', kind: 'recharge', direction: 'receivable', entry_date: todayIso(), currency: setup.settings!.base_currency,
    amount: '', description: '' });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const ids = { party: useId(), kind: useId(), direction: useId(), date: useId(), currency: useId(), amount: useId(), description: useId() };
  useEffect(() => { api.listCounterparties().then((page) => setParties(page.items), (error) => { setParties([]); setProblem(describeError(error)); }); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const issue = intercompanyProblem(form);
    if (issue) { setProblem(t(issue)); return; }
    setBusy(true); setProblem(null);
    try { await api.createIntercompany({ ...form, amount: form.amount.trim(), description: form.description.trim() }); onSaved(); }
    catch (error) { setProblem(failure(error, t('books.ic.title'))); setBusy(false); }
  }
  return <Modal title={t('books.ic.new')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('books.ic.new')}>
      <p className="muted bzHint">{t('books.ic.formHelp')}</p>
      {parties !== null && parties.length === 0 && <Note>{t('books.ic.noParties')}</Note>}
      <label className="field" htmlFor={ids.party}><span>{t('books.ic.party')}</span><select id={ids.party} value={form.workspace_id} onChange={(event) => setForm({ ...form, workspace_id: event.target.value })} data-autofocus>
        <option value="">{parties === null ? t('books.reading') : t('books.ic.pickParty')}</option>{(parties ?? []).map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}</select></label>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.kind}><span>{t('books.ic.kindLabel')}</span><select id={ids.kind} value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as BooksIntercompanyKind })}>
          {BOOKS_INTERCOMPANY_KINDS.map((item) => <option key={item} value={item}>{label(`books.ic.kind.${item}`)}</option>)}</select></label>
        <label className="field" htmlFor={ids.direction}><span>{t('books.ic.direction')}</span><select id={ids.direction} value={form.direction} onChange={(event) => setForm({ ...form, direction: event.target.value as BooksDirection })}>
          {(['receivable', 'payable'] as const).map((item) => <option key={item} value={item}>{label(`books.ic.dir.${form.kind}.${item}`)}</option>)}</select></label>
        <label className="field" htmlFor={ids.date}><span>{t('books.col.date')}</span><input id={ids.date} type="date" value={form.entry_date} max={todayIso()} onChange={(event) => setForm({ ...form, entry_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.currency}><span>{t('books.col.currency')}</span><CurrencySelect id={ids.currency} value={form.currency} onChange={(value) => setForm({ ...form, currency: value })} /></label>
        <label className="field" htmlFor={ids.amount}><span>{t('books.col.total')}</span><input id={ids.amount} inputMode="decimal" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></label>
      </div>
      <label className="field" htmlFor={ids.description}><span>{t('books.ic.what')}</span><input id={ids.description} dir="auto" value={form.description} maxLength={300} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      {problem && <Note error>{problem}</Note>}
      <Actions busy={busy} save={t('books.ic.record')} onCancel={onClose} />
    </form>
  </Modal>;
}
