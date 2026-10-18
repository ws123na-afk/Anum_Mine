// One customer organisation in the billing console: its subscription with the platform owner's
// actions (change, pause, resume, cancel, reactivate), usage, and invoices with record payment,
// credit note, void, email (waits in Approvals) and the PDF.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { ArrowLeft, Ban, Download, FilePlus2, Mail, ReceiptText, RefreshCw, Wallet } from 'lucide-react';
import * as b from '../lib/billingApi';
import {
  PAGE_SIZE, PAYMENT_METHODS, formatMoney, minorToInput, parseMoney, proofProblem, subscriptionTone,
  type AdminPlan, type ConsoleOrganisation as Org, type EmailOutcome, type Invoice, type InvoiceStatus, type InvoiceSummary, type Page, type PaymentMethod, type ProofType,
} from '../lib/billing';
import { describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { ConfirmDialog, Drawer, Modal } from '../Dialog';
import { Badge, Empty, InvoiceBody, InvoiceFilters, InvoiceTable, Meters, Note, Pager, date, priceLine } from './parts';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' | 'error'; message: string };
const failed = (e: unknown): Load<never> => ({ state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e) });
type Action = 'pause' | 'resume' | 'cancel' | 'reactivate';

export function ConsoleOrganisation({ tenantId, onBack, onChanged }: { tenantId: string; onBack: () => void; onChanged: () => void }) {
  const [org, setOrg] = useState<Load<Org>>({ state: 'loading' });
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [confirm, setConfirm] = useState<Action | null>(null), [changing, setChanging] = useState(false), [busy, setBusy] = useState(false), [tick, setTick] = useState(0);
  const load = useCallback(async () => { try { setOrg({ state: 'ready', value: await b.getOrganisation(tenantId) }); } catch (e) { setOrg(failed(e)); } }, [tenantId]);
  useEffect(() => { void load(); }, [load]);
  const act = async (action: Action) => {
    setBusy(true); setFlash(null);
    try { await b.subscriptionAction(tenantId, action); setFlash({ text: t(`billing.console.did.${action}` as MessageKey) }); setConfirm(null); await load(); setTick((n) => n + 1); onChanged(); }
    catch (e) { setFlash({ text: t('billing.failed', { message: describeError(e) }), error: true }); setConfirm(null); }
    finally { setBusy(false); }
  };
  const back = <button type="button" className="secondary" onClick={onBack}><ArrowLeft aria-hidden="true" className="blFlip" />{t('billing.console.back')}</button>;
  if (org.state === 'loading') return <div className="blStack">{back}<Note>{t('billing.console.loadingOrg')}</Note></div>;
  if (org.state !== 'ready') return <div className="blStack">{back}<div className="blRetry"><Note error>{t('billing.console.orgFailed', { message: org.message })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>;
  const value = org.value, sub = value.subscription, name = value.account?.display_name || tenantId;
  const actions: Action[] = !sub ? [] : sub.status === 'cancelled' ? ['reactivate'] : [sub.status === 'paused' ? 'resume' : 'pause', sub.cancel_at_period_end ? 'reactivate' : 'cancel'];
  return <div className="blStack">
    {back}
    <section className="surface blPanel" aria-labelledby="console-org-title">
      <div className="blHead"><div><p className="eyebrow" dir="ltr">{tenantId}</p><h2 id="console-org-title" dir="auto">{name}</h2></div>
        {sub && <div className="actions blInline">
          {sub.status !== 'cancelled' && <button type="button" aria-haspopup="dialog" disabled={busy} onClick={() => setChanging(true)}>{t('billing.changePlan')}</button>}
          {actions.map((action) => <button key={action} type="button" className={`secondary ${action === 'cancel' ? 'dangerText' : ''}`} disabled={busy} onClick={() => setConfirm(action)}>{t(`billing.console.action.${action}` as MessageKey)}</button>)}
        </div>}
      </div>
      {flash && <Note error={flash.error}>{flash.text}</Note>}
      {value.account?.state === 'read_only' && <Note error>{t('billing.console.orgReadOnly')}</Note>}
      {!sub ? <Empty title={t('billing.console.notBilled')} text={t('billing.console.notBilledText')} />
        : <div className="blPlanNow">
          <div className="blPlanName"><h3 dir="auto">{sub.plan_name}</h3><Badge tone={subscriptionTone(sub.status)}>{t(`billing.status.${sub.status}` as MessageKey)}</Badge></div>
          <p className="blPrice">{priceLine(sub)}{sub.per_seat && <span className="muted"> {t('billing.seats', { count: sub.seats })}</span>}</p>
          <dl className="blFacts">
            {sub.trial_end && sub.status === 'trialing' && <div><dt>{t('billing.trialEnds')}</dt><dd>{date(sub.trial_end)}</dd></div>}
            {sub.current_period_end && <div><dt>{sub.cancel_at_period_end ? t('billing.endsOn') : t('billing.renewsOn')}</dt><dd>{date(sub.current_period_end)}</dd></div>}
            {sub.past_due_since && <div><dt>{t('billing.console.pastDueSince')}</dt><dd>{date(sub.past_due_since)}</dd></div>}
            {sub.grace_until && <div><dt>{t('billing.console.graceUntil')}</dt><dd>{date(sub.grace_until)}</dd></div>}
            {value.account && <div><dt>{t('billing.contact.email')}</dt><dd dir="ltr">{value.account.contact_email || '-'}</dd></div>}
          </dl>
        </div>}
      <h3 className="blSub">{t('billing.usage.title')}</h3>
      <Meters meters={value.usage.meters} />
    </section>
    <OrgInvoices key={tick} tenantId={tenantId} contactEmail={value.account?.contact_email ?? ''} onChanged={() => { void load(); onChanged(); }} />
    {confirm && <ConfirmDialog title={t(`billing.console.confirm.${confirm}` as MessageKey, { name })} confirmLabel={t(`billing.console.action.${confirm}` as MessageKey)} danger={confirm === 'cancel'} busy={busy}
      onCancel={() => setConfirm(null)} onConfirm={() => void act(confirm)}><p>{t(`billing.console.explain.${confirm}` as MessageKey)}</p></ConfirmDialog>}
    {changing && sub && <ChangeDialog tenantId={tenantId} currentPlanId={sub.plan_id} onClose={() => setChanging(false)} onDone={(text) => { setChanging(false); setFlash({ text }); void load(); setTick((n) => n + 1); onChanged(); }} />}
  </div>;
}

function ChangeDialog({ tenantId, currentPlanId, onClose, onDone }: { tenantId: string; currentPlanId: string; onClose: () => void; onDone: (text: string) => void }) {
  const [plans, setPlans] = useState<AdminPlan[] | null>(null), [planId, setPlanId] = useState(''), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const selectId = useId();
  useEffect(() => { void b.listCatalog().then((list) => setPlans(list.filter((p) => p.active && p.id !== currentPlanId))).catch((e) => { setPlans([]); setProblem(describeError(e)); }); }, [currentPlanId]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!planId) { setProblem(t('billing.console.pickPlan')); return; }
    setBusy(true); setProblem(null);
    try { await b.subscriptionAction(tenantId, 'change', { plan_id: planId }); onDone(t('billing.console.did.change')); }
    catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={t('billing.changePlan')} className="formDialog" onClose={onClose}><form onSubmit={(e) => void submit(e)} noValidate>
    <p className="muted">{t('billing.console.changeLead')}</p>
    {plans === null && <Note>{t('common.loading')}</Note>}
    {plans?.length === 0 && <Note>{t('billing.console.noOtherPlans')}</Note>}
    <label className="field" htmlFor={selectId}><span>{t('billing.col.plan')}</span><select id={selectId} value={planId} onChange={(e) => setPlanId(e.target.value)}><option value="">{t('billing.console.pickPlan')}</option>{(plans ?? []).map((p) => <option key={p.id} value={p.id}>{p.name} ({priceLine(p)})</option>)}</select></label>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy || !plans?.length}>{busy ? t('common.loading') : t('billing.change.confirm')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
  </form></Modal>;
}

function OrgInvoices({ tenantId, contactEmail, onChanged }: { tenantId: string; contactEmail: string; onChanged: () => void }) {
  const [page, setPage] = useState<Load<Page<InvoiceSummary>>>({ state: 'loading' });
  const [query, setQuery] = useState(''), [status, setStatus] = useState<InvoiceStatus | ''>(''), [offset, setOffset] = useState(0), [openId, setOpenId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(async () => {
    setPage({ state: 'loading' });
    try { setPage({ state: 'ready', value: await b.listOrgInvoices(tenantId, { q: query, status, limit: PAGE_SIZE, offset }) }); } catch (e) { setPage(failed(e)); }
  }, [tenantId, query, status, offset]);
  useEffect(() => { const timer = setTimeout(() => void load(), query ? 250 : 0); return () => clearTimeout(timer); }, [load, query]);
  const download = async (invoice: InvoiceSummary) => { setProblem(null); try { await b.downloadOrgInvoice(tenantId, invoice); } catch (e) { setProblem(t('billing.invoices.downloadFailed', { message: describeError(e) })); } };
  const filtered = Boolean(query.trim() || status);
  return <section className="surface blPanel" aria-labelledby="console-invoices-title">
    <div className="blHead"><div><h2 id="console-invoices-title">{t('billing.invoices.title')}</h2></div></div>
    <InvoiceFilters query={query} status={status} onQuery={(v) => { setQuery(v); setOffset(0); }} onStatus={(v) => { setStatus(v); setOffset(0); }} />
    {problem && <Note error>{problem}</Note>}
    {page.state === 'loading' && <Note>{t('billing.invoices.loading')}</Note>}
    {(page.state === 'error' || page.state === 'denied') && <div className="blRetry"><Note error>{t('billing.invoices.failed', { message: page.message })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {page.state === 'ready' && (page.value.items.length === 0
      ? <Empty icon={<ReceiptText aria-hidden="true" />} title={filtered ? t('billing.invoices.noMatch') : t('billing.invoices.none')} text={filtered ? t('billing.invoices.noMatchText') : t('billing.console.noInvoicesText')}
        action={filtered ? <button type="button" className="secondary" onClick={() => { setQuery(''); setStatus(''); }}>{t('billing.clearFilter')}</button> : undefined} />
      : <InvoiceTable items={page.value.items} caption={t('billing.invoices.caption')} onOpen={(invoice) => setOpenId(invoice.id)} onDownload={(invoice) => void download(invoice)} />)}
    {page.state === 'ready' && <Pager total={page.value.total} offset={offset} shown={page.value.items.length} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />}
    {openId && <Drawer title={t('billing.invoice.title')} onClose={() => setOpenId(null)}><OperatorInvoice tenantId={tenantId} invoiceId={openId} contactEmail={contactEmail} onChanged={() => { void load(); onChanged(); }} /></Drawer>}
  </section>;
}

type Dialog = 'payment' | 'credit' | 'void' | 'email' | null;

function OperatorInvoice({ tenantId, invoiceId, contactEmail, onChanged }: { tenantId: string; invoiceId: string; contactEmail: string; onChanged: () => void }) {
  const [invoice, setInvoice] = useState<Load<Invoice>>({ state: 'loading' });
  const [dialog, setDialog] = useState<Dialog>(null), [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null), [emailed, setEmailed] = useState<EmailOutcome | null>(null), [busy, setBusy] = useState(false);
  const load = useCallback(async () => { try { setInvoice({ state: 'ready', value: await b.getOrgInvoice(tenantId, invoiceId) }); } catch (e) { setInvoice(failed(e)); } }, [tenantId, invoiceId]);
  useEffect(() => { void load(); }, [load]);
  if (invoice.state === 'loading') return <Note>{t('billing.invoice.loading')}</Note>;
  if (invoice.state !== 'ready') return <div className="blRetry"><Note error>{t('billing.invoice.failed', { message: invoice.message })}</Note><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></div>;
  const value = invoice.value;
  const updated = (next: Invoice, text: string) => { setInvoice({ state: 'ready', value: next }); setDialog(null); setFlash({ text }); onChanged(); };
  const download = async () => { setFlash(null); try { await b.downloadOrgInvoice(tenantId, value); } catch (e) { setFlash({ text: t('billing.invoices.downloadFailed', { message: describeError(e) }), error: true }); } };
  const doVoid = async () => {
    setBusy(true);
    try { updated(await b.voidInvoice(tenantId, value.id), t('billing.console.voided', { number: value.number ?? '' })); }
    catch (e) { setDialog(null); setFlash({ text: t('billing.failed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); }
  };
  const open = value.status === 'open', issued = Boolean(value.number) && value.status !== 'draft';
  const creditable = value.total_minor - value.amount_credited_minor;
  return <div className="blDrawer">
    <div className="actions blInline">
      {open && value.amount_due_minor > 0 && <button type="button" onClick={() => setDialog('payment')}><Wallet aria-hidden="true" />{t('billing.console.recordPayment')}</button>}
      {issued && value.status !== 'void' && creditable > 0 && <button type="button" className="secondary" onClick={() => setDialog('credit')}><FilePlus2 aria-hidden="true" />{t('billing.console.creditNote')}</button>}
      {issued && (open || value.status === 'paid') && <button type="button" className="secondary" onClick={() => setDialog('email')}><Mail aria-hidden="true" />{t('billing.console.email')}</button>}
      {value.number && <button type="button" className="secondary" onClick={() => void download()}><Download aria-hidden="true" />{t('billing.invoice.download')}</button>}
      {open && value.payments.length === 0 && <button type="button" className="secondary dangerText" onClick={() => setDialog('void')}><Ban aria-hidden="true" />{t('billing.console.void')}</button>}
    </div>
    {flash && <Note error={flash.error}>{flash.text}</Note>}
    {emailed && <p className="notice" role="status"><Mail aria-hidden="true" /><span>{emailed.message} {emailed.status === 'waiting_approval' && <a href="#approvals">{t('billing.console.openApprovals')}</a>}</span></p>}
    {value.last_email?.status && !emailed && <p className="muted blSmall">{t('billing.console.lastEmail', { status: value.last_email.status })}</p>}
    <InvoiceBody invoice={value} />
    {dialog === 'payment' && <PaymentDialog tenantId={tenantId} invoice={value} onClose={() => setDialog(null)} onDone={(next) => updated(next, t('billing.console.paymentRecorded', { number: value.number ?? '' }))} />}
    {dialog === 'credit' && <CreditDialog tenantId={tenantId} invoice={value} max={creditable} onClose={() => setDialog(null)} onDone={(next) => updated(next, t('billing.console.creditIssued', { number: value.number ?? '' }))} />}
    {dialog === 'email' && <EmailDialog tenantId={tenantId} invoice={value} contactEmail={contactEmail} onClose={() => setDialog(null)} onDone={(outcome) => { setDialog(null); setEmailed(outcome); void load(); }} />}
    {dialog === 'void' && <ConfirmDialog title={t('billing.console.voidTitle', { number: value.number ?? '' })} confirmLabel={t('billing.console.void')} danger busy={busy} onCancel={() => setDialog(null)} onConfirm={() => void doVoid()}><p>{t('billing.console.voidText')}</p></ConfirmDialog>}
  </div>;
}

function PaymentDialog({ tenantId, invoice, onClose, onDone }: { tenantId: string; invoice: Invoice; onClose: () => void; onDone: (invoice: Invoice) => void }) {
  const [amount, setAmount] = useState(minorToInput(invoice.amount_due_minor, invoice.currency)), [method, setMethod] = useState<PaymentMethod>('bank_transfer');
  const [reference, setReference] = useState(invoice.number ?? ''), [received, setReceived] = useState(''), [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const minor = parseMoney(amount, invoice.currency);
    if (!minor) { setProblem(t('billing.console.amountInvalid')); return; }
    if (file) { const wrong = proofProblem(file); if (wrong) { setProblem(t(wrong)); return; } }
    setBusy(true); setProblem(null);
    try {
      const proof = file ? { name: file.name, content_type: file.type as ProofType, data_base64: await b.fileToBase64(file) } : undefined;
      const result = await b.recordPayment(tenantId, invoice.id, { amount_minor: minor, method, reference: reference.trim(), ...(received ? { received_at: new Date(`${received}T12:00:00Z`).toISOString() } : {}), ...(proof ? { proof } : {}) });
      onDone(result.invoice);
    } catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={t('billing.console.recordPaymentFor', { number: invoice.number ?? '' })} className="formDialog" onClose={onClose}><form onSubmit={(e) => void submit(e)} noValidate>
    <p className="muted">{t('billing.console.paymentLead', { due: formatMoney(invoice.amount_due_minor, invoice.currency) })}</p>
    <div className="blTwo">
      <label className="field" htmlFor={`${formId}-amount`}><span>{t('billing.console.amount', { currency: invoice.currency })}</span><input id={`${formId}-amount`} inputMode="decimal" dir="ltr" value={amount} onChange={(e) => setAmount(e.target.value)} required /></label>
      <label className="field" htmlFor={`${formId}-method`}><span>{t('billing.console.method')}</span><select id={`${formId}-method`} value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>{PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`billing.method.${m}` as MessageKey)}</option>)}</select></label>
    </div>
    <label className="field" htmlFor={`${formId}-ref`}><span>{t('billing.console.reference')}</span><input id={`${formId}-ref`} maxLength={200} dir="auto" value={reference} onChange={(e) => setReference(e.target.value)} /></label>
    <label className="field" htmlFor={`${formId}-date`}><span>{t('billing.console.received')}</span><input id={`${formId}-date`} type="date" value={received} onChange={(e) => setReceived(e.target.value)} /></label>
    <label className="field" htmlFor={`${formId}-proof`}><span>{t('billing.console.proof')}</span><input id={`${formId}-proof`} type="file" accept="application/pdf,image/png,image/jpeg" aria-describedby={`${formId}-proof-hint`} onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
    <small className="muted" id={`${formId}-proof-hint`}>{t('billing.console.proofHint')}</small>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('billing.console.recordPayment')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
  </form></Modal>;
}

function CreditDialog({ tenantId, invoice, max, onClose, onDone }: { tenantId: string; invoice: Invoice; max: number; onClose: () => void; onDone: (invoice: Invoice) => void }) {
  const [amount, setAmount] = useState(''), [reason, setReason] = useState(''), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const minor = parseMoney(amount, invoice.currency);
    if (!minor || minor > max) { setProblem(t('billing.console.creditRange', { max: formatMoney(max, invoice.currency) })); return; }
    if (reason.trim().length < 3) { setProblem(t('billing.console.reasonRequired')); return; }
    setBusy(true); setProblem(null);
    try { onDone((await b.issueCreditNote(tenantId, invoice.id, { total_minor: minor, reason: reason.trim() })).invoice); }
    catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={t('billing.console.creditFor', { number: invoice.number ?? '' })} className="formDialog" onClose={onClose}><form onSubmit={(e) => void submit(e)} noValidate>
    <p className="muted">{t('billing.console.creditLead', { max: formatMoney(max, invoice.currency) })}</p>
    <label className="field" htmlFor={`${formId}-amount`}><span>{t('billing.console.creditAmount', { currency: invoice.currency })}</span><input id={`${formId}-amount`} inputMode="decimal" dir="ltr" value={amount} onChange={(e) => setAmount(e.target.value)} required /></label>
    <label className="field" htmlFor={`${formId}-reason`}><span>{t('billing.console.reason')}</span><textarea id={`${formId}-reason`} rows={3} maxLength={500} dir="auto" value={reason} onChange={(e) => setReason(e.target.value)} required /></label>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('billing.console.issueCredit')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
  </form></Modal>;
}

function EmailDialog({ tenantId, invoice, contactEmail, onClose, onDone }: { tenantId: string; invoice: Invoice; contactEmail: string; onClose: () => void; onDone: (outcome: EmailOutcome) => void }) {
  const [to, setTo] = useState(''), [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (to.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to.trim())) { setProblem(t('billing.contact.badEmail')); return; }
    if (!to.trim() && !contactEmail) { setProblem(t('billing.console.emailNeeded')); return; }
    setBusy(true); setProblem(null);
    try { onDone(await b.emailInvoice(tenantId, invoice.id, to)); }
    catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <Modal title={t('billing.console.emailFor', { number: invoice.number ?? '' })} className="formDialog" onClose={onClose}><form onSubmit={(e) => void submit(e)} noValidate>
    <p className="muted">{t('billing.console.emailLead')}</p>
    <label className="field" htmlFor={`${formId}-to`}><span>{t('billing.console.emailTo')}</span><input id={`${formId}-to`} type="email" dir="ltr" value={to} placeholder={contactEmail || 'billing@example.com'} onChange={(e) => setTo(e.target.value)} aria-describedby={`${formId}-hint`} /></label>
    <small className="muted" id={`${formId}-hint`}>{contactEmail ? t('billing.console.emailDefault', { email: contactEmail }) : t('billing.console.emailNoContact')}</small>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('billing.console.emailAsk')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
  </form></Modal>;
}
