// Business invoices (docs/business.md#invoices): the money tiles per currency, the list, the new
// invoice flow built from milestones, billable costs, an offer and manual lines, and the invoice
// drawer with the actions the API allows (`can`): edit a draft, ask an owner to approve sending,
// record payments by hand or from a customer's report, credit notes, void, print or download a PDF. Sending is never done from here: an owner approves it
// in Approvals with the hash they were shown, and only then does the invoice get its number.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { Download, Plus, Printer, Search, Send, Trash2 } from 'lucide-react';
import type {
  Billable, BusinessAccountRow, BusinessCurrency, BusinessInvoice, BusinessProject, BusinessSetup, CreditNote, InvoiceSummary, PaymentMethod, PaymentNotice,
} from '@anum/contracts';
import { getLocale, t } from './lib/i18n';
import { CURRENCIES, formatMoney, formatPct } from './lib/business';
import {
  INVOICE_STATUSES, LINE_KINDS, PAYMENT_METHODS, PRINT_LANGUAGES, createBody, creditBody, creditNoteTone, defaultInvoiceFilters, emptyCredit, emptyFields, emptyManualLine,
  emptyNewInvoice, emptyPayment, invoiceFiltersActive, invoiceQuery, invoiceStatusLabel, invoiceTitle, invoiceTone, lineKindLabel, listDate, noticeStatusLabel, noticeTone,
  overdueText, paymentBody, paymentMethodLabel, paymentStateLabel, summaryTiles, today, updateBody, type FieldsDraft, type InvoiceFilters, type ManualLineDraft, type NewInvoiceDraft, type PrintLanguage,
} from './lib/invoices';
import { shortHash } from './lib/approvals';
import * as b from './lib/businessApi';
import * as inv from './lib/businessInvoicesApi';
import { printHtml } from './lib/printWindow';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Empty, Note, Pager, ReasonDialog, day, failure, when } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';

const money = (value: number | null, currency: BusinessCurrency) => formatMoney(value, currency);
const dayText = (value: string | null) => (value ? day(value) : '-');

// ---- the tab -----------------------------------------------------------------------------------

export function InvoicesTab({ setup, openId, onOpen, onChanged }: { setup: BusinessSetup; openId: string | null; onOpen: (id: string | null) => void; onChanged: () => void }) {
  const [filters, setFilters] = useState<InvoiceFilters>(defaultInvoiceFilters), [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false), [tick, setTick] = useState(0);
  const applied: InvoiceFilters = { ...filters, q: search };
  const list = usePaged((offset) => inv.listInvoices(invoiceQuery(applied, offset)), [filters.status, filters.overdue, search, tick]);
  const searchId = useId(), statusId = useId();
  const write = setup.permissions.write;
  const refresh = () => { setTick((value) => value + 1); onChanged(); };
  const active = invoiceFiltersActive(applied);
  const clear = () => { setFilters(defaultInvoiceFilters()); setSearch(''); };
  const empty = list.load.kind === 'ready' && list.items.length === 0;
  return <section className="bzSection" aria-label={t('biz.tab.invoices')}>
    <SummaryBand reload={tick} />
    <div className="bzToolbar">
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setSearch(filters.q.trim()); setTick((value) => value + 1); }}>
        <label className="visuallyHidden" htmlFor={searchId}>{t('inv.search')}</label>
        <input id={searchId} type="search" value={filters.q} placeholder={t('inv.search')} onChange={(event) => { setFilters({ ...filters, q: event.target.value }); if (!event.target.value) setSearch(''); }} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
      <label className="bzCompact" htmlFor={statusId}><span>{t('inv.filter.status')}</span>
        <select id={statusId} value={filters.status} disabled={filters.overdue} onChange={(event) => setFilters({ ...filters, status: event.target.value as InvoiceFilters['status'] })}>
          <option value="">{t('inv.filter.allStatuses')}</option>{INVOICE_STATUSES.map((status) => <option key={status} value={status}>{invoiceStatusLabel(status)}</option>)}</select></label>
      <label className="bzCheck"><input type="checkbox" checked={filters.overdue} onChange={(event) => setFilters({ ...filters, overdue: event.target.checked, status: '' })} />{t('inv.filter.overdue')}</label>
      <div className="bzToolbarEnd">{write && <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('inv.new')}</button>}</div>
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={active} onRetry={() => void list.first()} onClear={clear} />
    {empty && !active && <Empty title={t('inv.emptyTitle')} text={write ? t('inv.emptyText') : t('inv.emptyMember')}
      action={write ? <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('inv.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <p className="bzCount" aria-live="polite">{t('inv.count', { count: list.total })}</p>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('biz.tab.invoices')}</caption>
        <thead><tr><th scope="col">{t('inv.col.invoice')}</th><th scope="col">{t('inv.col.status')}</th><th scope="col" className="num">{t('inv.col.total')}</th><th scope="col" className="num">{t('inv.col.balance')}</th><th scope="col" className="bzHideMd">{t('inv.col.date')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(item.id)}><span dir="auto">{invoiceTitle(item)}</span></button><small dir="auto">{item.account_name}{item.project_name ? `, ${item.project_name}` : ''}</small></th>
          <td><div className="invBadges"><Badge tone={invoiceTone(item.status, item.overdue)}>{invoiceStatusLabel(item.status)}</Badge>
            {item.overdue && <Badge tone="dangerTone">{overdueText(item)}</Badge>}
            {item.notices_pending > 0 && <Badge tone="warning">{t('inv.notice.badge', { count: item.notices_pending })}</Badge>}</div></td>
          <td className="num">{money(item.total, item.currency)}</td>
          <td className="num">{item.status === 'sent' || item.status === 'paid' ? money(item.balance, item.currency) : '-'}</td>
          <td className="bzHideMd nowrap">{dayText(listDate(item))}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {creating && <NewInvoiceDialog onClose={() => setCreating(false)} onCreated={(created) => { setCreating(false); refresh(); onOpen(created.id); }} />}
    {openId && <InvoiceDrawer setup={setup} id={openId} onClose={() => onOpen(null)} onChanged={refresh} />}
  </section>;
}

// ---- summary: one block per currency, never added together -----------------------------------

function SummaryBand({ reload }: { reload: number }) {
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'ready'; value: InvoiceSummary } | { kind: 'error'; text: string }>({ kind: 'loading' });
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await inv.getInvoiceSummary() }); }
    catch (error) { const text = failure(error, t('inv.sum.title')); setState({ kind: 'error', text }); }
  }, []);
  useEffect(() => { void load(); }, [load, reload]);
  const heading = useId();
  if (state.kind === 'loading') return <Note>{t('biz.loading')}</Note>;
  if (state.kind === 'error') return <Note error>{state.text} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>;
  const blocks = summaryTiles(state.value);
  const waiting = state.value.notices_pending;
  if (blocks.length === 0 && waiting === 0) return null;
  return <section className="invSummary" aria-labelledby={heading}>
    <h2 id={heading} className="visuallyHidden">{t('inv.sum.title')}</h2>
    {waiting > 0 && <Note>{t('inv.notice.summary', { count: waiting })}</Note>}
    {blocks.map((block) => <div key={block.currency} className="surface invCurrency" role="group" aria-label={t('inv.sum.inCurrency', { currency: block.currency })}>
      <h3>{block.currency}</h3>
      <dl className="invTiles">{block.tiles.map((tile) => <div key={tile.id} className={`invTile ${tile.tone}`}><dt>{tile.label}</dt><dd><strong>{tile.value}</strong><small>{tile.hint}</small></dd></div>)}</dl>
    </div>)}
    {state.value.truncated && <Note>{t('inv.sum.truncated')}</Note>}
    {blocks.length > 0 && <p className="muted bzHint">{t('inv.sum.never')}</p>}
  </section>;
}

// ---- shared form pieces ------------------------------------------------------------------------

function FieldsForm({ draft, onChange, disabled, billToHelp }: { draft: FieldsDraft; onChange: (next: FieldsDraft) => void; disabled?: boolean; billToHelp?: string }) {
  const set = (key: keyof FieldsDraft) => (event: { target: { value: string } }) => onChange({ ...draft, [key]: event.target.value });
  return <fieldset className="bzFieldset" disabled={disabled}>
    <legend>{t('inv.form.details')}</legend>
    <div className="bzGrid3">
      <label className="field"><span>{t('inv.form.dueDays')}</span><input inputMode="numeric" value={draft.dueDays} onChange={set('dueDays')} placeholder={t('inv.form.dueDaysDefault')} /></label>
      <label className="field"><span>{t('inv.form.taxRate')}</span><input inputMode="decimal" value={draft.taxRate} onChange={set('taxRate')} /></label>
      <label className="field"><span>{t('inv.form.reference')}</span><input value={draft.reference} onChange={set('reference')} dir="auto" /></label>
    </div>
    <label className="field"><span>{t('inv.form.terms')}</span><textarea rows={2} value={draft.terms} onChange={set('terms')} dir="auto" /></label>
    <label className="field"><span>{t('inv.form.notes')}</span><textarea rows={2} value={draft.notes} onChange={set('notes')} dir="auto" /></label>
    <fieldset className="bzFieldset">
      <legend>{t('inv.form.billTo')}</legend>
      {billToHelp && <p className="muted bzHint">{billToHelp}</p>}
      <div className="bzGrid2">
        <label className="field"><span>{t('inv.form.billName')}</span><input value={draft.billName} onChange={set('billName')} dir="auto" /></label>
        <label className="field"><span>{t('inv.form.billEmail')}</span><input type="email" value={draft.billEmail} onChange={set('billEmail')} dir="ltr" /></label>
        <label className="field"><span>{t('inv.form.billTax')}</span><input value={draft.billTax} onChange={set('billTax')} dir="auto" /></label>
        <label className="field"><span>{t('inv.form.billAddress')}</span><textarea rows={2} value={draft.billAddress} onChange={set('billAddress')} dir="auto" /></label>
      </div>
    </fieldset>
  </fieldset>;
}

function ManualLines({ lines, onChange, taxHint }: { lines: ManualLineDraft[]; onChange: (next: ManualLineDraft[]) => void; taxHint: string }) {
  const set = (index: number, patch: Partial<ManualLineDraft>) => onChange(lines.map((line, at) => (at === index ? { ...line, ...patch } : line)));
  return <fieldset className="bzFieldset">
    <legend>{t('inv.form.manual')}</legend>
    <p className="muted bzHint">{t('inv.form.manualHelp')}</p>
    <div className="bzRows">{lines.map((line, index) => <div key={index} className="bzRow invLineRow" role="group" aria-label={t('inv.form.lineN', { number: index + 1 })}>
      <label className="field"><span>{t('inv.form.lineDesc')}</span><input value={line.description} onChange={(event) => set(index, { description: event.target.value })} dir="auto" /></label>
      <label className="field"><span>{t('inv.form.lineKind')}</span><select value={line.kind} onChange={(event) => set(index, { kind: event.target.value as ManualLineDraft['kind'] })}>{LINE_KINDS.map((kind) => <option key={kind} value={kind}>{lineKindLabel(kind)}</option>)}</select></label>
      <label className="field"><span>{t('inv.form.lineQty')}</span><input inputMode="decimal" value={line.quantity} onChange={(event) => set(index, { quantity: event.target.value })} /></label>
      <label className="field"><span>{t('inv.form.linePrice')}</span><input inputMode="decimal" value={line.unitPrice} onChange={(event) => set(index, { unitPrice: event.target.value })} /></label>
      <label className="field"><span>{t('inv.form.lineTax')}</span><input inputMode="decimal" value={line.taxPct} placeholder={taxHint} onChange={(event) => set(index, { taxPct: event.target.value })} /></label>
      <button type="button" className="iconButton" aria-label={t('inv.form.removeLineN', { number: index + 1 })} title={t('inv.form.removeLine')} onClick={() => onChange(lines.filter((_, at) => at !== index))}><Trash2 aria-hidden="true" /></button>
    </div>)}</div>
    <div className="bzInline"><button type="button" className="secondary" onClick={() => onChange([...lines, emptyManualLine()])}><Plus aria-hidden="true" />{t('inv.form.addLine')}</button></div>
  </fieldset>;
}

// ---- new invoice -------------------------------------------------------------------------------

type Loaded<T> = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ready'; value: T } | { kind: 'error'; text: string };

function NewInvoiceDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (invoice: BusinessInvoice) => void }) {
  const [accountQuery, setAccountQuery] = useState(''), [accounts, setAccounts] = useState<Loaded<BusinessAccountRow[]>>({ kind: 'loading' });
  const [projects, setProjects] = useState<BusinessProject[]>([]);
  const [accountId, setAccountId] = useState(''), [projectId, setProjectId] = useState('');
  const [billable, setBillable] = useState<Loaded<Billable>>({ kind: 'idle' });
  const [draft, setDraft] = useState<NewInvoiceDraft>(emptyNewInvoice);
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const accountField = useId(), projectField = useId(), searchField = useId(), currencyField = useId();

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setAccounts({ kind: 'loading' });
      b.listAccounts({ q: accountQuery.trim() || undefined, limit: 100, offset: 0 }).then((page) => setAccounts({ kind: 'ready', value: page.items }))
        .catch((error) => setAccounts({ kind: 'error', text: failure(error, t('inv.new.accounts')) }));
    }, accountQuery ? 250 : 0);
    return () => window.clearTimeout(handle);
  }, [accountQuery]);
  useEffect(() => { void b.listProjects({ limit: 100, offset: 0 }).then((page) => setProjects(page.items)).catch(() => setProjects([])); }, []);

  const accountProjects = projects.filter((item) => item.account_id === accountId);
  useEffect(() => {
    if (!accountId && !projectId) { setBillable({ kind: 'idle' }); return; }
    let live = true;
    setBillable({ kind: 'loading' });
    inv.getBillable(projectId ? { project_id: projectId } : { account_id: accountId })
      .then((value) => { if (live) { setBillable({ kind: 'ready', value }); setDraft((old) => ({ ...emptyNewInvoice(), currency: old.currency, dueDays: old.dueDays, taxRate: String(value.tax_rate_pct), reference: old.reference, notes: old.notes, terms: old.terms })); } })
      .catch((error) => { if (live) setBillable({ kind: 'error', text: failure(error, t('inv.new.billable')) }); });
    return () => { live = false; };
  }, [accountId, projectId]);

  const pickAccount = (value: string) => { setAccountId(value); setProjectId(''); setProblem(null); };
  const set = (patch: Partial<NewInvoiceDraft>) => setDraft((old) => ({ ...old, ...patch }));
  const toggle = (key: 'milestoneIds' | 'usageIds', value: string) => set({ [key]: draft[key].includes(value) ? draft[key].filter((item) => item !== value) : [...draft[key], value] });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (billable.kind !== 'ready') { setProblem(t('inv.new.pickFirst')); return; }
    const body = createBody({ accountId: billable.value.account_id, projectId: billable.value.project_id }, draft, billable.value);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onCreated(await inv.createInvoice(body.value)); }
    catch (error) { setProblem(failure(error, t('inv.new.create'))); setBusy(false); }
  }
  const ready = billable.kind === 'ready' ? billable.value : null;
  const offer = ready?.offers.find((item) => item.id === draft.offerId);
  return <Modal title={t('inv.new')} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.new')} noValidate>
      <p className="muted bzHint">{t('inv.new.intro')}</p>
      <fieldset className="bzFieldset">
        <legend>{t('inv.new.who')}</legend>
        <div className="bzGrid2">
          <label className="field" htmlFor={searchField}><span>{t('inv.new.findClient')}</span><input id={searchField} type="search" value={accountQuery} onChange={(event) => setAccountQuery(event.target.value)} dir="auto" data-autofocus /></label>
          <label className="field" htmlFor={accountField}><span>{t('inv.new.client')}</span>
            <select id={accountField} value={accountId} onChange={(event) => pickAccount(event.target.value)}>
              <option value="">{accounts.kind === 'loading' ? t('biz.loading') : t('inv.new.chooseClient')}</option>
              {accounts.kind === 'ready' && accounts.value.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        </div>
        {accounts.kind === 'error' && <Note error>{accounts.text}</Note>}
        {accounts.kind === 'ready' && accounts.value.length === 0 && <p className="muted bzHint">{t('inv.new.noClients')}</p>}
        {accountId && <div className="bzGrid2">
          <label className="field" htmlFor={projectField}><span>{t('inv.new.project')}</span>
            <select id={projectField} value={projectId} onChange={(event) => { setProjectId(event.target.value); setProblem(null); }}>
              <option value="">{t('inv.new.noProject')}</option>{accountProjects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          {!projectId && <label className="field" htmlFor={currencyField}><span>{t('inv.form.currency')}</span>
            <select id={currencyField} value={draft.currency} onChange={(event) => set({ currency: event.target.value as BusinessCurrency })}>{CURRENCIES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>}
        </div>}
      </fieldset>
      {billable.kind === 'loading' && <Note>{t('inv.new.loadingBillable')}</Note>}
      {billable.kind === 'error' && <Note error>{billable.text}</Note>}
      {ready && <>
        {ready.project_id && <fieldset className="bzFieldset">
          <legend>{t('inv.new.milestones')}</legend>
          {ready.milestones.length === 0 && !ready.contract && <p className="muted">{t('inv.new.noMilestones')}</p>}
          <ul className="invPicks">{ready.milestones.map((item) => <li key={item.id}>
            <label className="bzCheck"><input type="checkbox" disabled={!item.billable} checked={draft.milestoneIds.includes(item.id)} onChange={() => toggle('milestoneIds', item.id)} />
              <span dir="auto">{item.title}</span><strong>{money(item.amount, ready.currency)}</strong>
              {item.change_order_number !== null && <Badge tone="warning">{t('inv.new.changeOrderN', { number: item.change_order_number })}</Badge>}</label>
            {!item.billable && <small className="muted" dir="auto">{item.reason}</small>}</li>)}</ul>
          {ready.contract && <>
            <p className="muted bzHint">{t('inv.new.contractHelp', { total: money(ready.contract.contract_total, ready.currency), invoiced: money(ready.contract.invoiced, ready.currency), remaining: money(ready.contract.remaining, ready.currency) })}</p>
            <label className="field"><span>{t('inv.new.contractAmount')}</span><input inputMode="decimal" value={draft.contractAmount} onChange={(event) => set({ contractAmount: event.target.value })} /></label>
          </>}
        </fieldset>}
        <fieldset className="bzFieldset">
          <legend>{t('inv.new.costs')}</legend>
          {ready.usage.length === 0 ? <p className="muted">{t('inv.new.noCosts')}</p> : <>
            <ul className="invPicks">{ready.usage.map((item) => <li key={item.id}>
              <label className="bzCheck"><input type="checkbox" disabled={!item.billable} checked={draft.usageIds.includes(item.id)} onChange={() => toggle('usageIds', item.id)} />
                <span dir="auto">{item.description}</span><span className="muted">{day(item.occurred_on)}</span><strong>{money(item.amount, item.currency)}</strong></label>
              {!item.billable && <small className="muted" dir="auto">{item.reason}</small>}</li>)}</ul>
            <label className="field bzCompactField"><span>{t('inv.new.markup')}</span><input inputMode="decimal" value={draft.markup} onChange={(event) => set({ markup: event.target.value })} /></label>
          </>}
        </fieldset>
        {ready.offers.length > 0 && <fieldset className="bzFieldset">
          <legend>{t('inv.new.offer')}</legend>
          <div className="bzGrid3">
            <label className="field"><span>{t('inv.new.offerPick')}</span><select value={draft.offerId} onChange={(event) => set({ offerId: event.target.value })}>
              <option value="">{t('inv.new.noOffer')}</option>{ready.offers.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
            {draft.offerId && <label className="field"><span>{t('inv.form.lineQty')}</span><input inputMode="decimal" value={draft.offerQty} onChange={(event) => set({ offerQty: event.target.value })} /></label>}
            {offer && offer.price_type !== 'fixed' && <label className="field"><span>{t('inv.new.offerPrice')}</span><input inputMode="decimal" value={draft.offerPrice} onChange={(event) => set({ offerPrice: event.target.value })} /></label>}
          </div>
          {offer && offer.price_type === 'fixed' && offer.price !== null && <p className="muted bzHint">{t('inv.new.offerFixed', { price: money(offer.price, offer.currency) })}</p>}
        </fieldset>}
        <ManualLines lines={draft.lines} onChange={(lines) => set({ lines })} taxHint={formatPct(ready.tax_rate_pct)} />
        <FieldsForm draft={draft} onChange={(next) => setDraft({ ...draft, ...next })} billToHelp={t('inv.new.billToHelp')} />
      </>}
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy || !ready}>{busy ? t('dialog.working') : t('inv.new.create')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- the invoice drawer ------------------------------------------------------------------------

function fieldsOf(invoice: BusinessInvoice): FieldsDraft {
  return { ...emptyFields(String(invoice.due_in_days)), reference: invoice.customer_reference, notes: invoice.notes, terms: invoice.payment_terms, billName: invoice.bill_to_name,
    billAddress: invoice.bill_to_address, billEmail: invoice.bill_to_email, billTax: invoice.bill_to_tax_number };
}
const manualOf = (invoice: BusinessInvoice): ManualLineDraft[] => invoice.lines.filter((line) => line.source === 'manual')
  .map((line) => ({ description: line.description, kind: line.kind, quantity: String(line.quantity), unitPrice: String(line.unit_price), taxPct: String(line.tax_rate_pct) }));

export function InvoiceDrawer({ setup, id, onClose, onChanged }: { setup: BusinessSetup; id: string; onClose: () => void; onChanged: () => void }) {
  const [item, setItem] = useState<BusinessInvoice | null>(null), [problem, setProblem] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState<'void' | 'delete' | 'payment' | 'credit' | { payment: string } | { voidCredit: CreditNote } | { confirm: PaymentNotice } | { reject: PaymentNotice } | null>(null);
  const [language, setLanguage] = useState<PrintLanguage>(getLocale() === 'ar' ? 'ar' : 'en'), [printNote, setPrintNote] = useState(''), [pdfNote, setPdfNote] = useState('');
  const languageId = useId();
  const reload = useCallback(async () => { try { setItem(await inv.getInvoice(id)); setProblem(null); } catch (error) { setProblem(failure(error, t('inv.load'))); } }, [id]);
  useEffect(() => { void reload(); }, [reload]);
  if (!item) return <Drawer title={t('inv.title')} onClose={onClose}>{problem ? <><Note error>{problem}</Note><div className="bzInline"><button type="button" className="secondary" onClick={() => void reload()}>{t('common.tryAgain')}</button></div></> : <Note>{t('biz.loading')}</Note>}</Drawer>;
  const invoice = item;
  const can = invoice.can;
  async function run(what: string, fn: () => Promise<BusinessInvoice | void>, success?: string) {
    setBusy(true); setProblem(null); setNotice(null);
    try { const next = await fn(); if (next) setItem(next); if (success) setNotice(success); onChanged(); }
    catch (error) { setProblem(failure(error, what)); } finally { setBusy(false); }
  }
  async function print() {
    setPrintNote(t('inv.print.opening')); setProblem(null);
    try { await printHtml(() => inv.invoicePrintable(invoice.id, language)); setPrintNote(t('inv.print.opened')); }
    catch (error) { setPrintNote(''); setProblem(failure(error, t('inv.print.button'))); }
  }
  async function downloadPdf() {
    setPdfNote(t('inv.pdf.working')); setProblem(null);
    try { setPdfNote(t('inv.pdf.saved', { name: await inv.downloadInvoicePdf(invoice.id) })); }
    catch (error) { setPdfNote(''); setProblem(failure(error, t('inv.pdf.button'))); }
  }
  const title = invoiceTitle(invoice);
  return <Drawer title={title} onClose={onClose}>
    <div className="bzDrawer">
      <div className="bzLeadHead">
        <Badge tone={invoiceTone(invoice.status, invoice.overdue)}>{invoiceStatusLabel(invoice.status)}</Badge>
        {invoice.overdue && <Badge tone="dangerTone">{overdueText(invoice)}</Badge>}
        {(invoice.status === 'sent' || invoice.status === 'paid') && <span className="muted">{paymentStateLabel(invoice.payment_state)}</span>}
        <span className="muted" dir="auto">{invoice.account_name}{invoice.project_name ? `, ${invoice.project_name}` : ''}</span>
      </div>
      <div aria-live="polite">{notice && <Note>{notice}</Note>}</div>
      {problem && <Note error>{problem}</Note>}
      {invoice.decision_note && invoice.status === 'draft' && <Note>{t('inv.decisionNote', { note: invoice.decision_note })}</Note>}
      {invoice.status === 'pending_approval' && <div className="bzApproval" role="group" aria-label={t('inv.approval.label')}>
        <p className="bzApprovalTitle">{t('inv.approval.waiting')}</p>
        <p className="muted">{t('inv.approval.ownerDecides')} <a href="#approvals">{t('inv.approval.open')}</a></p>
        <p className="bzHash">{t('biz.approval.hash')} <code title={invoice.requested_hash ?? ''}>{shortHash(invoice.requested_hash)}</code></p>
        <p className="muted bzHint">{t('inv.approval.frozen')}</p>
      </div>}
      {invoice.status === 'void' && <Note>{t('inv.voidedNote', { when: when(invoice.voided_at), reason: invoice.void_reason })}</Note>}
      {invoice.status === 'draft' && invoice.issues.length > 0 && <div className="bzGaps"><strong>{t('inv.issues')}</strong><ul>{invoice.issues.map((issue) => <li key={issue} dir="auto">{issue}</li>)}</ul></div>}

      {editing && can.edit
        ? <EditForm invoice={invoice} onCancel={() => setEditing(false)} onSaved={(next) => { setItem(next); setEditing(false); setNotice(t('inv.saved')); onChanged(); }} />
        : <InvoiceBody invoice={invoice} />}

      {!editing && <div className="bzInline" role="group" aria-label={t('inv.actions')}>
        {can.edit && <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(true)}>{t('inv.edit')}</button>}
        {can.request_send && <button type="button" disabled={busy || invoice.issues.length > 0} onClick={() => void run(t('inv.requestApproval'), () => inv.requestInvoiceApproval(invoice.id), t('inv.requested'))}><Send aria-hidden="true" />{t('inv.requestApproval')}</button>}
        {can.cancel_request && <button type="button" className="secondary" disabled={busy} onClick={() => void run(t('inv.cancelRequest'), () => inv.cancelInvoiceRequest(invoice.id), t('inv.cancelled'))}>{t('inv.cancelRequest')}</button>}
        {can.record_payment && <button type="button" disabled={busy} onClick={() => setAsking('payment')}>{t('inv.pay.record')}</button>}
        {can.void && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking('void')}>{t('inv.void')}</button>}
        {can.delete && <button type="button" className="danger" disabled={busy} onClick={() => setAsking('delete')}><Trash2 aria-hidden="true" />{t('inv.delete')}</button>}
      </div>}
      {!editing && invoice.status === 'draft' && can.request_send && invoice.issues.length > 0 && <p className="muted bzHint">{t('inv.requestBlocked')}</p>}
      {!editing && invoice.status === 'draft' && !can.request_send && <p className="muted bzHint">{t('inv.memberDraftOnly')}</p>}
      {!editing && invoice.status === 'sent' && !can.void && invoice.amount_paid > 0 && setup.permissions.manage && <p className="muted bzHint">{t('inv.voidHasPayments')}</p>}
      {!editing && invoice.status === 'sent' && !can.void && invoice.amount_paid === 0 && invoice.amount_credited > 0 && setup.permissions.manage && <p className="muted bzHint">{t('inv.credit.hasCredits')}</p>}
      {!editing && invoice.status === 'sent' && !setup.permissions.manage && <p className="muted bzHint">{t('inv.ownerVoids')}</p>}

      <section className="bzBlock" aria-labelledby={`${languageId}-h`}>
        <h3 id={`${languageId}-h`}>{t('inv.print.title')}</h3>
        <p className="muted bzHint">{invoice.status === 'draft' || invoice.status === 'pending_approval' ? t('inv.print.draftHelp') : t('inv.print.help')}</p>
        <div className="bzInline">
          <label className="bzCompact" htmlFor={languageId}><span>{t('inv.print.language')}</span>
            <select id={languageId} value={language} onChange={(event) => setLanguage(event.target.value as PrintLanguage)}>{PRINT_LANGUAGES.map((code) => <option key={code} value={code}>{t(`inv.print.lang.${code}`)}</option>)}</select></label>
          <button type="button" className="secondary" onClick={() => void print()}><Printer aria-hidden="true" />{t('inv.print.button')}</button>
          <button type="button" className="secondary" onClick={() => void downloadPdf()}><Download aria-hidden="true" />{t('inv.pdf.button')}</button>
        </div>
        <p className="muted bzHint">{t('inv.pdf.help')}</p>
        <p className="muted bzHint" role="status">{printNote || pdfNote}</p>
      </section>

      <PaymentsBlock invoice={invoice} canRemove={can.remove_payment} busy={busy} onRemove={(paymentId) => setAsking({ payment: paymentId })} />
      <NoticesBlock invoice={invoice} canDecide={can.decide_notice} busy={busy} onConfirm={(notice) => setAsking({ confirm: notice })} onReject={(notice) => setAsking({ reject: notice })} />
      <CreditNotesBlock invoice={invoice} manage={setup.permissions.manage} busy={busy} onIssue={() => setAsking('credit')} onVoid={(note) => setAsking({ voidCredit: note })} />
    </div>

    {asking === 'void' && <ReasonDialog title={t('inv.void.title', { number: invoice.number ?? '' })} label={t('inv.void.reason')} confirm={t('inv.void')} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => { const next = await inv.voidInvoice(invoice.id, reason); setItem(next); setAsking(null); setNotice(t('inv.voided')); onChanged(); }} />}
    {asking === 'delete' && <ConfirmDialog title={t('inv.delete.title')} confirmLabel={t('inv.delete')} danger busy={busy} onCancel={() => setAsking(null)}
      onConfirm={() => { void (async () => { setBusy(true); try { await inv.deleteInvoice(invoice.id); onChanged(); onClose(); } catch (error) { setProblem(failure(error, t('inv.delete'))); setAsking(null); } finally { setBusy(false); } })(); }}>
      <p>{t('inv.delete.text')}</p></ConfirmDialog>}
    {asking === 'payment' && <PaymentDialog invoice={invoice} onCancel={() => setAsking(null)} onSaved={(next) => { setItem(next); setAsking(null); setNotice(next.status === 'paid' ? t('inv.pay.nowPaid') : t('inv.pay.saved')); onChanged(); }} />}
    {asking === 'credit' && <CreditDialog invoice={invoice} onCancel={() => setAsking(null)} onSaved={(next) => { setItem(next); setAsking(null); setNotice(next.balance <= 0 ? t('inv.credit.settled') : t('inv.credit.saved')); onChanged(); }} />}
    {asking !== null && typeof asking === 'object' && 'voidCredit' in asking && <ReasonDialog title={t('inv.credit.voidTitle', { number: asking.voidCredit.number })} label={t('inv.credit.voidReason')} confirm={t('inv.credit.void')} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => { const next = await inv.voidCreditNote(invoice.id, asking.voidCredit.id, reason); setItem(next); setAsking(null); setNotice(t('inv.credit.voided')); onChanged(); }} />}
    {asking !== null && typeof asking === 'object' && 'reject' in asking && <ReasonDialog title={t('inv.notice.rejectTitle')} label={t('inv.notice.rejectReason')} confirm={t('inv.notice.reject')} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => { const next = await inv.rejectPaymentNotice(invoice.id, asking.reject.id, reason); setItem(next); setAsking(null); setNotice(t('inv.notice.rejected')); onChanged(); }} />}
    {asking !== null && typeof asking === 'object' && 'confirm' in asking && <ConfirmNoticeDialog invoice={invoice} notice={asking.confirm} onCancel={() => setAsking(null)}
      onSaved={(next) => { setItem(next); setAsking(null); setNotice(next.status === 'paid' ? t('inv.notice.confirmedPaid') : t('inv.notice.confirmed')); onChanged(); }} />}
    {asking !== null && typeof asking === 'object' && 'payment' in asking && <ConfirmDialog title={t('inv.pay.removeTitle')} confirmLabel={t('inv.pay.remove')} danger busy={busy} onCancel={() => setAsking(null)}
      onConfirm={() => { const paymentId = asking.payment; setAsking(null); void run(t('inv.pay.remove'), () => inv.removePayment(invoice.id, paymentId), t('inv.pay.removed')); }}>
      <p>{t('inv.pay.removeText')}</p></ConfirmDialog>}
  </Drawer>;
}

function InvoiceBody({ invoice }: { invoice: BusinessInvoice }) {
  const c = invoice.currency;
  const seller = invoice.seller;
  return <>
    <dl className="bzFacts">
      <div><dt>{t('inv.f.issued')}</dt><dd>{dayText(invoice.issue_date)}</dd></div>
      <div><dt>{t('inv.f.due')}</dt><dd>{invoice.due_date ? dayText(invoice.due_date) : t('inv.f.dueIn', { count: invoice.due_in_days })}</dd></div>
      <div><dt>{t('inv.f.reference')}</dt><dd dir="auto">{invoice.customer_reference || '-'}</dd></div>
      <div><dt>{t('inv.f.terms')}</dt><dd dir="auto">{invoice.payment_terms || '-'}</dd></div>
    </dl>
    <section className="bzBlock" aria-labelledby={`l-${invoice.id}`}>
      <h3 id={`l-${invoice.id}`}>{t('inv.lines')}</h3>
      <div className="pane dataTableWrap" role="region" aria-label={t('inv.lines')} tabIndex={0}><table className="dataTable bzTable invLines">
        <caption className="visuallyHidden">{t('inv.lines')}</caption>
        <thead><tr><th scope="col">{t('inv.col.description')}</th><th scope="col" className="num">{t('inv.col.net')}</th><th scope="col" className="num">{t('inv.col.tax')}</th><th scope="col" className="num">{t('inv.col.lineTotal')}</th></tr></thead>
        <tbody>{invoice.lines.map((line, index) => <tr key={index}>
          <th scope="row"><span dir="auto">{line.description}</span><small>{t('inv.lineDetail', { source: t(`inv.source.${line.source}`), quantity: line.quantity, price: money(line.unit_price, c), rate: formatPct(line.tax_rate_pct) })}</small></th>
          <td className="num">{money(line.net, c)}</td><td className="num">{money(line.tax, c)}</td><td className="num">{money(line.total, c)}</td></tr>)}</tbody></table></div>
      <dl className="bzTotals" aria-label={t('inv.totals')}>
        <div><dt>{t('inv.f.subtotal')}</dt><dd>{money(invoice.subtotal, c)}</dd></div>
        <div><dt>{t('inv.f.tax')}</dt><dd>{money(invoice.tax, c)}</dd></div>
        <div><dt>{t('inv.f.total')}</dt><dd><strong>{money(invoice.total, c)}</strong></dd></div>
        {invoice.amount_credited > 0 && <div><dt>{t('inv.f.credited')}</dt><dd>{money(-invoice.amount_credited, c)}</dd></div>}
        <div><dt>{t('inv.f.paid')}</dt><dd>{money(invoice.amount_paid, c)}</dd></div>
        <div><dt>{t('inv.f.balance')}</dt><dd><strong>{money(invoice.balance, c)}</strong></dd></div>
      </dl>
    </section>
    <section className="bzBlock" aria-labelledby={`p-${invoice.id}`}>
      <h3 id={`p-${invoice.id}`}>{t('inv.parties')}</h3>
      <div className="bzGrid2">
        <div><h4 className="invParty">{t('inv.billTo')}</h4><p dir="auto" className="invAddress"><strong>{invoice.bill_to_name || '-'}</strong>{invoice.bill_to_address ? `\n${invoice.bill_to_address}` : ''}</p>
          <p className="muted" dir="auto">{[invoice.bill_to_email, invoice.bill_to_tax_number].filter(Boolean).join(' / ') || '-'}</p></div>
        <div><h4 className="invParty">{t('inv.seller')}</h4>{seller ? <><p dir="auto" className="invAddress"><strong>{seller.legal_name || '-'}</strong>{seller.address ? `\n${seller.address}` : ''}</p>
          <p className="muted" dir="auto">{[seller.tax_number && `${seller.tax_label}: ${seller.tax_number}`, seller.email, seller.phone].filter(Boolean).join(' / ') || '-'}</p></>
          : <p className="muted">{t('inv.sellerLater')}</p>}</div>
      </div>
      {invoice.notes && <p dir="auto" className="invAddress">{invoice.notes}</p>}
    </section>
    {invoice.sent_at && <p className="muted bzHint">{t('inv.sentOn', { by: invoice.sent_by ?? '', when: when(invoice.sent_at) })}</p>}
  </>;
}

function EditForm({ invoice, onCancel, onSaved }: { invoice: BusinessInvoice; onCancel: () => void; onSaved: (next: BusinessInvoice) => void }) {
  const [fields, setFields] = useState(() => fieldsOf(invoice)), [manual, setManual] = useState(() => manualOf(invoice));
  const [removed, setRemoved] = useState<number[]>([]), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const hadManual = invoice.lines.some((line) => line.source === 'manual');
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = updateBody(fields, manual, removed, hadManual || manual.length > 0);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved(await inv.updateInvoice(invoice.id, body.value)); } catch (error) { setProblem(failure(error, t('inv.edit'))); setBusy(false); }
  }
  const sourced = invoice.lines.map((line, index) => ({ line, index })).filter(({ line }) => line.source !== 'manual');
  return <form className="bzProposalForm" onSubmit={(event) => void submit(event)} aria-label={t('inv.edit')} noValidate>
    {invoice.status === 'pending_approval' && <Note>{t('inv.edit.lapses')}</Note>}
    <FieldsForm draft={fields} onChange={setFields} />
    {sourced.length > 0 && <fieldset className="bzFieldset"><legend>{t('inv.edit.sourced')}</legend>
      <p className="muted bzHint">{t('inv.edit.sourcedHelp')}</p>
      <ul className="invPicks">{sourced.map(({ line, index }) => <li key={index}><label className="bzCheck">
        <input type="checkbox" checked={removed.includes(index)} onChange={() => setRemoved(removed.includes(index) ? removed.filter((value) => value !== index) : [...removed, index])} />
        <span dir="auto">{t('inv.edit.removeLine', { description: line.description })}</span><strong>{money(line.total, invoice.currency)}</strong></label></li>)}</ul></fieldset>}
    <ManualLines lines={manual} onChange={setManual} taxHint={formatPct(invoice.lines[0]?.tax_rate_pct ?? 0)} />
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.edit.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
  </form>;
}

// ---- payments ----------------------------------------------------------------------------------

function PaymentsBlock({ invoice, canRemove, busy, onRemove }: { invoice: BusinessInvoice; canRemove: boolean; busy: boolean; onRemove: (paymentId: string) => void }) {
  if (invoice.status === 'draft' || invoice.status === 'pending_approval') return null;
  return <section className="bzBlock" aria-labelledby={`pay-${invoice.id}`}>
    <h3 id={`pay-${invoice.id}`}>{t('inv.pay.title')}</h3>
    {invoice.payments.length === 0 ? <p className="muted">{t('inv.pay.none')}</p>
      : <ul className="bzTrail">{invoice.payments.map((payment) => <li key={payment.id}>
        <div className="bzInline"><strong>{money(payment.amount, invoice.currency)}</strong><span>{day(payment.paid_on)}</span><span className="muted">{paymentMethodLabel(payment.method)}</span>
          {canRemove && <button type="button" className="secondary" disabled={busy} onClick={() => onRemove(payment.id)}>{t('inv.pay.remove')}</button>}</div>
        <small dir="auto">{[payment.reference, payment.note].filter(Boolean).join(' / ')}</small>
        <small>{t('inv.pay.recordedBy', { by: payment.recorded_by })}</small></li>)}</ul>}
  </section>;
}

function PaymentDialog({ invoice, onCancel, onSaved }: { invoice: BusinessInvoice; onCancel: () => void; onSaved: (next: BusinessInvoice) => void }) {
  const [draft, setDraft] = useState(() => emptyPayment(invoice.balance, today())), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = paymentBody(draft, invoice.balance, invoice.currency);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved(await inv.recordPayment(invoice.id, body.value)); } catch (error) { setProblem(failure(error, t('inv.pay.record'))); setBusy(false); }
  }
  const set = (key: keyof typeof draft) => (event: { target: { value: string } }) => setDraft({ ...draft, [key]: event.target.value });
  return <Modal title={t('inv.pay.record')} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.pay.record')} noValidate>
      <p className="muted bzHint">{t('inv.pay.help', { balance: money(invoice.balance, invoice.currency) })}</p>
      <div className="bzGrid2">
        <label className="field"><span>{t('inv.pay.amount')}</span><input inputMode="decimal" value={draft.amount} onChange={set('amount')} data-autofocus /></label>
        <label className="field"><span>{t('inv.pay.date')}</span><input type="date" value={draft.paidOn} onChange={set('paidOn')} /></label>
        <label className="field"><span>{t('inv.pay.method')}</span><select value={draft.method} onChange={(event) => setDraft({ ...draft, method: event.target.value as PaymentMethod })}>{PAYMENT_METHODS.map((method) => <option key={method} value={method}>{paymentMethodLabel(method)}</option>)}</select></label>
        <label className="field"><span>{t('inv.pay.reference')}</span><input value={draft.reference} onChange={set('reference')} dir="auto" /></label>
      </div>
      <label className="field"><span>{t('inv.pay.note')}</span><input value={draft.note} onChange={set('note')} dir="auto" /></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.pay.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- credit notes ------------------------------------------------------------------------------

function CreditNotesBlock({ invoice, manage, busy, onIssue, onVoid }: { invoice: BusinessInvoice; manage: boolean; busy: boolean; onIssue: () => void; onVoid: (note: CreditNote) => void }) {
  if (invoice.status === 'draft' || invoice.status === 'pending_approval') return null;
  if (invoice.status === 'void' && invoice.credit_notes.length === 0) return null;
  const c = invoice.currency;
  return <section className="bzBlock" aria-labelledby={`cn-${invoice.id}`}>
    <h3 id={`cn-${invoice.id}`}>{t('inv.credit.title')}</h3>
    {invoice.credit_notes.length === 0 ? <p className="muted">{t('inv.credit.none')}</p>
      : <ul className="bzTrail">{invoice.credit_notes.map((note) => <li key={note.id}>
        <div className="bzInline"><strong dir="auto">{note.number}</strong><span>{money(note.amount, c)}</span><span>{dayText(note.issue_date)}</span>
          {note.status === 'void' && <Badge tone={creditNoteTone(note.status)}>{t('inv.credit.voidTag')}</Badge>}
          {invoice.can.void_credit_note && note.status === 'issued' && <button type="button" className="secondary" disabled={busy} onClick={() => onVoid(note)}>{t('inv.credit.void')}</button>}</div>
        <small dir="auto">{note.reason}</small>
        <small>{t('inv.credit.split', { subtotal: money(note.subtotal, c), tax: money(note.tax, c) })}</small>
        {note.status === 'void' && <small dir="auto">{t('inv.credit.voidedOn', { reason: note.void_reason })}</small>}
        <small>{t('inv.pay.recordedBy', { by: note.issued_by })}</small></li>)}</ul>}
    {invoice.can.credit ? <div className="bzInline"><button type="button" className="secondary" disabled={busy} onClick={onIssue}>{t('inv.credit.issue')}</button></div>
      : invoice.status === 'sent' && !manage ? <p className="muted bzHint">{t('inv.credit.ownersOnly')}</p>
      : invoice.status === 'sent' || invoice.status === 'paid' ? <p className="muted bzHint">{invoice.balance <= 0 ? t('inv.credit.nothingLeft') : t('inv.credit.ownersOnly')}</p> : null}
  </section>;
}

function CreditDialog({ invoice, onCancel, onSaved }: { invoice: BusinessInvoice; onCancel: () => void; onSaved: (next: BusinessInvoice) => void }) {
  const [draft, setDraft] = useState(() => emptyCredit(invoice.balance)), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = creditBody(draft, invoice.balance, invoice.currency);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved(await inv.issueCreditNote(invoice.id, body.value)); } catch (error) { setProblem(failure(error, t('inv.credit.issue'))); setBusy(false); }
  }
  return <Modal title={t('inv.credit.issue')} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.credit.issue')} noValidate>
      <p className="muted bzHint">{t('inv.credit.help', { balance: money(invoice.balance, invoice.currency) })}</p>
      <label className="field"><span>{t('inv.credit.amount')}</span><input inputMode="decimal" value={draft.amount} onChange={(event) => setDraft({ ...draft, amount: event.target.value })} data-autofocus /></label>
      <label className="field"><span>{t('inv.credit.reason')}</span><textarea rows={2} maxLength={300} value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} dir="auto" /></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.credit.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---- payments the customer reported in the portal -----------------------------------------------

function NoticesBlock({ invoice, canDecide, busy, onConfirm, onReject }: { invoice: BusinessInvoice; canDecide: boolean; busy: boolean; onConfirm: (notice: PaymentNotice) => void; onReject: (notice: PaymentNotice) => void }) {
  if (invoice.status === 'draft' || invoice.status === 'pending_approval') return null;
  if (invoice.status === 'void' && invoice.payment_notices.length === 0) return null;
  const c = invoice.currency;
  return <section className="bzBlock" aria-labelledby={`pn-${invoice.id}`}>
    <h3 id={`pn-${invoice.id}`}>{t('inv.notice.title')}</h3>
    <p className="muted bzHint">{t('inv.notice.help')}</p>
    {invoice.payment_notices.length === 0 ? <p className="muted">{t('inv.notice.none')}</p>
      : <ul className="bzTrail">{invoice.payment_notices.map((notice) => <li key={notice.id}>
        <div className="bzInline"><strong>{money(notice.amount, c)}</strong><span>{day(notice.paid_on)}</span><span className="muted">{paymentMethodLabel(notice.method)}</span>
          <Badge tone={noticeTone(notice.status)}>{noticeStatusLabel(notice.status)}</Badge>
          {notice.status === 'pending' && canDecide && <>
            <button type="button" disabled={busy} onClick={() => onConfirm(notice)}>{t('inv.notice.confirm')}</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => onReject(notice)}>{t('inv.notice.reject')}</button></>}</div>
        <small dir="auto">{[notice.reference, notice.note].filter(Boolean).join(' / ')}</small>
        <small dir="auto">{t('inv.notice.by', { name: notice.payer_name, when: when(notice.created_at) })}</small>
        {notice.status !== 'pending' && <small dir="auto">{[t('inv.notice.decided', { by: notice.decided_by ?? '', when: when(notice.decided_at) }), notice.decision_note].filter(Boolean).join(': ')}</small>}</li>)}</ul>}
    {invoice.payment_notices.some((notice) => notice.status === 'pending') && !canDecide && <p className="muted bzHint">{t('inv.notice.viewOnly')}</p>}
  </section>;
}

function ConfirmNoticeDialog({ invoice, notice, onCancel, onSaved }: { invoice: BusinessInvoice; notice: PaymentNotice; onCancel: () => void; onSaved: (next: BusinessInvoice) => void }) {
  const [note, setNote] = useState(''), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { onSaved(await inv.confirmPaymentNotice(invoice.id, notice.id, note.trim())); } catch (error) { setProblem(failure(error, t('inv.notice.confirm'))); setBusy(false); }
  }
  return <Modal title={t('inv.notice.confirmTitle')} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.notice.confirmTitle')}>
      <p>{t('inv.notice.confirmText', { amount: money(notice.amount, invoice.currency), date: day(notice.paid_on) })}</p>
      <p className="muted" dir="auto">{[notice.payer_name, paymentMethodLabel(notice.method), notice.reference].filter(Boolean).join(' / ')}</p>
      <label className="field"><span>{t('inv.notice.confirmNote')}</span><input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} dir="auto" data-autofocus /></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.notice.confirm')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}
