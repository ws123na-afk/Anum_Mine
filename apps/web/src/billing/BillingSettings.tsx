// Settings > Billing (docs/billing.md#organisation-billing-owners-and-org-admins): the organisation's
// plan and status, usage against the plan's limits, changing or cancelling the plan with a preview,
// invoices with their PDFs, how to pay, and the billing contact. Only workspace owners and org_admin
// may open it; anyone else sees the API's sentence naming who can.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { AlertTriangle, ArrowUpRight, CreditCard, Download, Landmark, Lock, ReceiptText, RefreshCw, ShieldAlert, Sparkles, XCircle } from 'lucide-react';
import * as b from '../lib/billingApi';
import {
  COUNTRIES, PAGE_SIZE, blockedFromError, countryName, formatMoney, meterKey, subscriptionTone,
  type AccountBody, type Blocked, type Invoice, type InvoiceStatus, type InvoiceSummary, type Overview, type Page, type Plan, type Preview,
} from '../lib/billing';
import { describeError, isPermissionDenied } from '../lib/errors';
import { t, type MessageKey } from '../lib/i18n';
import { ConfirmDialog, Drawer, Modal } from '../Dialog';
import { BankDetailsBlock, Badge, Empty, InvoiceBody, InvoiceFilters, InvoiceTable, Meters, Note, Pager, PlanCard, date, invoiceLabel, priceLine } from './parts';

type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied' | 'error'; message: string };
type Flash = { text: string; error?: boolean } | null;
const failed = (e: unknown): Load<never> => ({ state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e) });


export function BillingSettings() {
  const [overview, setOverview] = useState<Load<Overview>>({ state: 'loading' });
  const [plans, setPlans] = useState<Plan[]>([]);
  const [flash, setFlash] = useState<Flash>(null);
  const [changing, setChanging] = useState(false);
  const [confirm, setConfirm] = useState<'cancel' | 'reactivate' | null>(null);
  const [busy, setBusy] = useState(false);
  const [invoiceTick, setInvoiceTick] = useState(0);
  const opened = useRef(false);
  const load = useCallback(async () => {
    try { const value = await b.getOverview(); setOverview({ state: 'ready', value }); if (value.account) void b.listPlans().then(setPlans).catch(() => setPlans([])); }
    catch (e) { setOverview(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  // #billing-plans opens the plan change; #billing-contact and #billing-invoices scroll to their section.
  useEffect(() => {
    if (overview.state !== 'ready' || opened.current) return;
    opened.current = true;
    const target = location.hash.replace(/^#billing-?/, '');
    if (target === 'plans' && overview.value.subscription && overview.value.subscription.status !== 'cancelled') setChanging(true);
    else if (target) requestAnimationFrame(() => { const section = document.getElementById(`billing-${target}`); section?.scrollIntoView({ block: 'start' }); section?.querySelector<HTMLElement>('input, select, button')?.focus({ preventScroll: true }); });
  }, [overview]);

  if (overview.state === 'loading') return <div className="settingsStack"><Note>{t('billing.loading')}</Note></div>;
  if (overview.state === 'denied') return <div className="settingsStack"><section className="surface adminForbidden blPanel" aria-labelledby="billing-denied">
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h2 id="billing-denied">{t('billing.deniedTitle')}</h2><p>{overview.message}</p></div>
  </section></div>;
  if (overview.state !== 'ready') return <div className="settingsStack"><section className="surface blPanel blProblem" aria-labelledby="billing-error">
    <ShieldAlert aria-hidden="true" /><div><h2 id="billing-error">{t('billing.errorTitle')}</h2><p className="muted">{t('billing.errorText', { message: overview.message })}</p>
      <div className="actions"><button type="button" onClick={() => { setOverview({ state: 'loading' }); void load(); }}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>
  </section></div>;

  const data = overview.value, sub = data.subscription, currency = data.currency || 'SAR';
  const pendingName = sub?.pending_plan_id ? plans.find((plan) => plan.id === sub.pending_plan_id)?.name ?? sub.pending_plan_id : null;
  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true); setFlash(null);
    try { await fn(); setFlash({ text: done }); setConfirm(null); await load(); setInvoiceTick((n) => n + 1); }
    catch (e) { setFlash({ text: t('billing.failed', { message: describeError(e) }), error: true }); setConfirm(null); }
    finally { setBusy(false); }
  };
  return <div className="settingsStack blPage">
    {data.state === 'read_only' && <p className="notice errorNotice blBanner" role="alert"><AlertTriangle aria-hidden="true" />{t('billing.readOnly', { amount: formatMoney(data.open_balance_minor, currency) })}</p>}
    {data.state !== 'read_only' && data.overdue && <p className="notice blBanner warn" role="status"><AlertTriangle aria-hidden="true" />{t('billing.overdueBanner', { amount: formatMoney(data.open_balance_minor, currency) })}</p>}
    {flash && <Note error={flash.error}>{flash.text}</Note>}

    <section className="surface blPanel" id="billing-plan" aria-labelledby="billing-plan-title">
      <div className="blHead"><div><p className="eyebrow">{t('billing.eyebrow')}</p><h2 id="billing-plan-title">{t('billing.planTitle')}</h2></div>
        {sub && <div className="actions blInline">
          {sub.status !== 'cancelled' && <button type="button" aria-haspopup="dialog" onClick={() => setChanging(true)} disabled={busy}><Sparkles aria-hidden="true" />{t('billing.changePlan')}</button>}
          {(sub.cancel_at_period_end || sub.status === 'cancelled') ? <button type="button" className="secondary" onClick={() => setConfirm('reactivate')} disabled={busy}><RefreshCw aria-hidden="true" />{t('billing.reactivate')}</button>
            : sub.status !== 'paused' && <button type="button" className="secondary dangerText" onClick={() => setConfirm('cancel')} disabled={busy}><XCircle aria-hidden="true" />{t('billing.cancel')}</button>}
        </div>}
      </div>
      {!sub ? <Empty icon={<ReceiptText aria-hidden="true" />} title={t('billing.noPlanTitle')} text={data.issuer.name ? t('billing.noPlanTextIssuer', { issuer: data.issuer.name }) : t('billing.noPlanText')}
        action={<>{data.issuer.email && <a className="blLink primary" href={`mailto:${data.issuer.email}`}>{t('billing.contactIssuer')}</a>}<a className="blLink" href="#pricing">{t('billing.seePricing')}<ArrowUpRight aria-hidden="true" /></a></>} />
        : <div className="blPlanNow">
          <div className="blPlanName"><h3 dir="auto">{sub.plan_name}</h3><Badge tone={subscriptionTone(sub.status)}>{t(`billing.status.${sub.status}` as MessageKey)}</Badge></div>
          <p className="blPrice">{priceLine(sub)}{sub.per_seat && <span className="muted"> {t('billing.seats', { count: sub.seats })}</span>}</p>
          <dl className="blFacts">
            {sub.status === 'trialing' && sub.trial_end && <div><dt>{t('billing.trialEnds')}</dt><dd>{date(sub.trial_end)}</dd></div>}
            {sub.current_period_end && sub.status !== 'cancelled' && <div><dt>{sub.cancel_at_period_end ? t('billing.endsOn') : t('billing.renewsOn')}</dt><dd>{date(sub.current_period_end)}</dd></div>}
            {sub.status === 'cancelled' && <div><dt>{t('billing.cancelledOn')}</dt><dd>{date(sub.cancelled_at)}</dd></div>}
            <div><dt>{t('billing.openBalance')}</dt><dd>{formatMoney(data.open_balance_minor, currency)}</dd></div>
            {(data.account?.credit_balance_minor ?? 0) > 0 && <div><dt>{t('billing.credit')}</dt><dd>{formatMoney(data.account!.credit_balance_minor, currency)}</dd></div>}
          </dl>
          {sub.cancel_at_period_end && <Note>{t('billing.cancelPending', { date: date(sub.current_period_end) })}</Note>}
          {pendingName && <Note>{t('billing.downgradePending', { plan: pendingName, date: date(sub.current_period_end) })}</Note>}
          {sub.status === 'past_due' && <Note error>{t('billing.pastDue', { date: date(sub.grace_until) })}</Note>}
          {sub.status === 'paused' && <Note>{t('billing.paused')}</Note>}
        </div>}
    </section>

    <section className="surface blPanel" id="billing-usage" aria-labelledby="billing-usage-title">
      <div className="blHead"><div><h2 id="billing-usage-title">{t('billing.usage.title')}</h2><p className="muted">{sub ? t('billing.usage.lead', { period: data.usage.period }) : t('billing.usage.noPlan')}</p></div></div>
      <Meters meters={data.usage.meters} />
    </section>

    <PaymentMethods overview={data} />
    <InvoicesSection key={invoiceTick} cardEnabled={data.payment.card_enabled} />
    {data.account ? <ContactForm overview={data} onSaved={(text) => { setFlash({ text }); void load(); }} />
      : <section className="surface blPanel" id="billing-contact" aria-labelledby="billing-contact-title"><h2 id="billing-contact-title">{t('billing.contact.title')}</h2><p className="muted">{t('billing.contact.notBilled')}</p></section>}

    {changing && sub && <ChangePlanDialog plans={plans} currentPlanId={sub.plan_id} onClose={() => setChanging(false)}
      onChanged={(text) => { setChanging(false); setFlash({ text }); void load(); setInvoiceTick((n) => n + 1); }} />}
    {confirm === 'cancel' && sub && <ConfirmDialog title={t('billing.cancelTitle')} confirmLabel={t('billing.cancelConfirm')} danger busy={busy} onCancel={() => setConfirm(null)}
      onConfirm={() => void act(b.cancelSubscription, t('billing.cancelled', { date: date(sub.current_period_end) }))}><p>{t('billing.cancelText', { date: date(sub.current_period_end) })}</p></ConfirmDialog>}
    {confirm === 'reactivate' && sub && <ConfirmDialog title={t('billing.reactivateTitle')} confirmLabel={t('billing.reactivate')} busy={busy} onCancel={() => setConfirm(null)}
      onConfirm={() => void act(b.reactivateSubscription, t('billing.reactivated'))}><p>{sub.status === 'cancelled' ? t('billing.reactivateRestart') : t('billing.reactivateText')}</p></ConfirmDialog>}
  </div>;
}

// ---- payment methods -------------------------------------------------------------------------------------

function PaymentMethods({ overview }: { overview: Overview }) {
  return <section className="surface blPanel" id="billing-payment" aria-labelledby="billing-payment-title">
    <div className="blHead"><div><h2 id="billing-payment-title">{t('billing.payment.title')}</h2><p className="muted">{t('billing.payment.lead')}</p></div></div>
    <div className="blMethods">
      <article className="blMethod"><h3><Landmark aria-hidden="true" />{t('billing.payment.bank')}</h3><BankDetailsBlock bank={overview.payment.bank_transfer} /><p className="muted">{t('billing.payment.bankHint')}</p></article>
      <article className="blMethod"><h3><CreditCard aria-hidden="true" />{t('billing.payment.card')}</h3>
        <p className="muted">{overview.payment.card_enabled ? t('billing.payment.cardOn') : t('billing.payment.cardOff')}</p>
        <Badge tone={overview.payment.card_enabled ? 'success' : 'neutral'}>{overview.payment.card_enabled ? t('billing.payment.available') : t('billing.payment.unavailable')}</Badge>
      </article>
    </div>
  </section>;
}

// ---- change plan ----------------------------------------------------------------------------------------------

function BlockedList({ items }: { items: Blocked[] }) {
  return <div className="notice errorNotice blBlocked" role="alert"><AlertTriangle aria-hidden="true" /><div><p>{t('billing.change.blocked')}</p>
    <ul>{items.map((item) => <li key={item.key}>{t('billing.change.blockedItem', { what: t(meterKey(item.key)), used: item.used, limit: item.limit })}</li>)}</ul></div></div>;
}

function ChangePlanDialog({ plans, currentPlanId, onClose, onChanged }: { plans: Plan[]; currentPlanId: string; onClose: () => void; onChanged: (text: string) => void }) {
  const [picked, setPicked] = useState<Plan | null>(null);
  const [preview, setPreview] = useState<Load<Preview> | null>(null);
  const [blocked, setBlocked] = useState<Blocked[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pick = async (plan: Plan) => {
    setPicked(plan); setPreview({ state: 'loading' }); setBlocked([]); setProblem(null);
    try { const value = await b.previewChange(plan.id); setPreview({ state: 'ready', value }); setBlocked(value.blocked_by ?? []); }
    catch (e) { setPreview(failed(e)); }
  };
  const confirm = async () => {
    if (!picked) return;
    setBusy(true); setProblem(null);
    try {
      const result = await b.changePlan(picked.id);
      onChanged(result.invoice?.number ? t('billing.change.doneInvoice', { plan: picked.name, number: result.invoice.number }) : t('billing.change.done', { plan: picked.name }));
    } catch (e) {
      const refused = blockedFromError(e);
      if (refused.length) setBlocked(refused); else setProblem(t('billing.failed', { message: describeError(e) }));
    } finally { setBusy(false); }
  };
  const ready = preview?.state === 'ready' ? preview.value : null;
  const currency = ready?.currency ?? picked?.currency ?? 'SAR';
  return <Modal title={t('billing.change.title')} className="formDialog blDialog" onClose={onClose}>
    {!picked ? <>
      <p className="muted">{t('billing.change.lead')}</p>
      {plans.length === 0 ? <Empty title={t('billing.change.noPlans')} text={t('billing.change.noPlansText')} />
        : <div className="blPlans">{plans.map((plan) => <PlanCard key={plan.id} plan={plan} badge={plan.current || plan.id === currentPlanId ? <Badge tone="success">{t('billing.current')}</Badge> : undefined}
          action={plan.current || plan.id === currentPlanId ? undefined : <button type="button" onClick={() => void pick(plan)} aria-label={t('billing.change.chooseNamed', { plan: plan.name })}>{t('billing.change.choose')}</button>} />)}</div>}
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </> : <div className="blPreview">
      <h3 dir="auto">{t('billing.change.to', { plan: picked.name })}</h3>
      <p className="muted">{priceLine(picked)}</p>
      {preview?.state === 'loading' && <Note>{t('billing.change.previewing')}</Note>}
      {(preview?.state === 'error' || preview?.state === 'denied') && <Note error>{t('billing.change.previewFailed', { message: preview.message })}</Note>}
      {ready && <dl className="blTotals" aria-label={t('billing.change.summary')}>
        <div><dt>{t('billing.change.effective')}</dt><dd>{ready.effective === 'now' ? t('billing.change.now') : t('billing.change.periodEnd')}</dd></div>
        <div><dt>{t('billing.change.amount')}</dt><dd>{formatMoney(ready.amount_minor, currency)}</dd></div>
        <div><dt>{t('billing.change.tax')}</dt><dd>{formatMoney(ready.tax_minor, currency)}</dd></div>
        <div className="blGrand"><dt>{t('billing.change.total')}</dt><dd>{formatMoney(ready.total_minor, currency)}</dd></div>
        {ready.credit_minor > 0 && <div><dt>{t('billing.change.credit')}</dt><dd>{formatMoney(ready.credit_minor, currency)}</dd></div>}
      </dl>}
      {ready && !blocked.length && <p className="muted">{ready.effective === 'now' ? (ready.total_minor > 0 ? t('billing.change.chargeNow') : t('billing.change.noCharge')) : t('billing.change.atPeriodEnd')}</p>}
      {blocked.length > 0 && <BlockedList items={blocked} />}
      {problem && <Note error>{problem}</Note>}
      <div className="actions">
        <button type="button" disabled={busy || !ready || blocked.length > 0} onClick={() => void confirm()}>{busy ? t('common.loading') : t('billing.change.confirm')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => { setPicked(null); setPreview(null); setBlocked([]); setProblem(null); }}>{t('billing.change.back')}</button>
      </div>
    </div>}
  </Modal>;
}

// ---- invoices ------------------------------------------------------------------------------------------------------

function InvoicesSection({ cardEnabled }: { cardEnabled: boolean }) {
  const [page, setPage] = useState<Load<Page<InvoiceSummary>>>({ state: 'loading' });
  const [query, setQuery] = useState(''), [status, setStatus] = useState<InvoiceStatus | ''>(''), [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(async () => {
    setPage({ state: 'loading' });
    try { setPage({ state: 'ready', value: await b.listInvoices({ q: query, status, limit: PAGE_SIZE, offset }) }); } catch (e) { setPage(failed(e)); }
  }, [query, status, offset]);
  useEffect(() => { const timer = setTimeout(() => void load(), query ? 250 : 0); return () => clearTimeout(timer); }, [load, query]);
  const download = async (invoice: InvoiceSummary) => { setProblem(null); try { await b.downloadInvoice(invoice); } catch (e) { setProblem(t('billing.invoices.downloadFailed', { message: describeError(e) })); } };
  const filtered = Boolean(query.trim() || status);
  return <section className="surface blPanel" id="billing-invoices" aria-labelledby="billing-invoices-title">
    <div className="blHead"><div><h2 id="billing-invoices-title">{t('billing.invoices.title')}</h2><p className="muted">{t('billing.invoices.lead')}</p></div></div>
    <InvoiceFilters query={query} status={status} onQuery={(v) => { setQuery(v); setOffset(0); }} onStatus={(v) => { setStatus(v); setOffset(0); }} />
    {problem && <Note error>{problem}</Note>}
    {page.state === 'loading' && <Note>{t('billing.invoices.loading')}</Note>}
    {(page.state === 'error' || page.state === 'denied') && <div className="blRetry"><Note error>{t('billing.invoices.failed', { message: page.message })}</Note><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {page.state === 'ready' && (page.value.items.length === 0
      ? <Empty icon={<ReceiptText aria-hidden="true" />} title={filtered ? t('billing.invoices.noMatch') : t('billing.invoices.none')} text={filtered ? t('billing.invoices.noMatchText') : t('billing.invoices.noneText')}
        action={filtered ? <button type="button" className="secondary" onClick={() => { setQuery(''); setStatus(''); }}>{t('billing.clearFilter')}</button> : <a className="blLink" href="#billing-plan" onClick={(e) => { e.preventDefault(); document.getElementById('billing-plan')?.scrollIntoView(); }}>{t('billing.invoices.seePlan')}</a>} />
      : <InvoiceTable items={page.value.items} caption={t('billing.invoices.caption')} onOpen={(invoice) => setOpenId(invoice.id)} onDownload={(invoice) => void download(invoice)} />)}
    {page.state === 'ready' && <Pager total={page.value.total} offset={offset} shown={page.value.items.length} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />}
    {openId && <Drawer title={t('billing.invoice.title')} onClose={() => setOpenId(null)}><InvoiceDrawer invoiceId={openId} cardEnabled={cardEnabled} /></Drawer>}
  </section>;
}

function InvoiceDrawer({ invoiceId, cardEnabled }: { invoiceId: string; cardEnabled: boolean }) {
  const [invoice, setInvoice] = useState<Load<Invoice>>({ state: 'loading' });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const load = useCallback(async () => { try { setInvoice({ state: 'ready', value: await b.getInvoice(invoiceId) }); } catch (e) { setInvoice(failed(e)); } }, [invoiceId]);
  useEffect(() => { void load(); }, [load]);
  if (invoice.state === 'loading') return <Note>{t('billing.invoice.loading')}</Note>;
  if (invoice.state !== 'ready') return <div className="blRetry"><Note error>{t('billing.invoice.failed', { message: invoice.message })}</Note><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></div>;
  const value = invoice.value;
  const pay = async () => {
    setBusy(true); setProblem(null);
    try { const { url } = await b.checkout(value.id); if (!/^https:\/\//.test(url)) throw new Error(t('billing.invoice.badCheckout')); window.location.assign(url); }
    catch (e) { setProblem(t('billing.invoice.payFailed', { message: describeError(e) })); setBusy(false); }
  };
  const download = async () => { setProblem(null); try { await b.downloadInvoice(value); } catch (e) { setProblem(t('billing.invoices.downloadFailed', { message: describeError(e) })); } };
  return <div className="blDrawer">
    <div className="actions blInline">
      {value.number && <button type="button" className="secondary" onClick={() => void download()}><Download aria-hidden="true" />{t('billing.invoice.download')}</button>}
      {cardEnabled && value.status === 'open' && value.amount_due_minor > 0 && <button type="button" disabled={busy} onClick={() => void pay()}><CreditCard aria-hidden="true" />{busy ? t('common.loading') : t('billing.invoice.payCard', { amount: formatMoney(value.amount_due_minor, value.currency) })}</button>}
    </div>
    {problem && <Note error>{problem}</Note>}
    <InvoiceBody invoice={value} />
    <p className="visuallyHidden" aria-live="polite">{invoiceLabel(value)}</p>
  </div>;
}

// ---- billing contact ----------------------------------------------------------------------------------------------------

const CONTACT_FIELDS: { key: keyof AccountBody; label: MessageKey; type?: string; auto?: string; wide?: boolean }[] = [
  { key: 'contact_name', label: 'billing.contact.name', auto: 'name' },
  { key: 'contact_email', label: 'billing.contact.email', type: 'email', auto: 'email' },
  { key: 'company_name', label: 'billing.contact.company', auto: 'organization', wide: true },
  { key: 'address_line1', label: 'billing.contact.address1', auto: 'address-line1', wide: true },
  { key: 'address_line2', label: 'billing.contact.address2', auto: 'address-line2', wide: true },
  { key: 'city', label: 'billing.contact.city', auto: 'address-level2' },
  { key: 'postal_code', label: 'billing.contact.postal', auto: 'postal-code' },
  { key: 'vat_number', label: 'billing.contact.vat' },
];

function ContactForm({ overview, onSaved }: { overview: Overview; onSaved: (text: string) => void }) {
  const account = overview.account!;
  const initial = (): AccountBody => ({
    contact_name: account.contact_name ?? '', contact_email: account.contact_email ?? '', company_name: account.company_name ?? '', address_line1: account.address_line1 ?? '',
    address_line2: account.address_line2 ?? '', city: account.city ?? '', postal_code: account.postal_code ?? '', country: account.country || 'SA', vat_number: account.vat_number ?? '',
  });
  const [form, setForm] = useState<AccountBody>(initial);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  const countries = COUNTRIES.includes(form.country) ? COUNTRIES : [form.country, ...COUNTRIES];
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (form.contact_email.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.contact_email.trim())) { setProblem(t('billing.contact.badEmail')); return; }
    setBusy(true); setProblem(null);
    try { await b.saveAccount({ ...form, contact_email: form.contact_email.trim() }); onSaved(t('billing.contact.saved')); }
    catch (e) { setProblem(t('billing.failed', { message: describeError(e) })); }
    finally { setBusy(false); }
  };
  return <form className="surface blPanel blContact" id="billing-contact" aria-labelledby="billing-contact-title" onSubmit={(e) => void save(e)} noValidate>
    <div className="blHead"><div><h2 id="billing-contact-title">{t('billing.contact.title')}</h2><p className="muted">{t('billing.contact.lead')}</p></div></div>
    <div className="formGrid blFormGrid">
      {CONTACT_FIELDS.map((field) => <label key={field.key} className={`field ${field.wide ? 'blWide' : ''}`} htmlFor={`${formId}-${field.key}`}><span>{t(field.label)}</span>
        <input id={`${formId}-${field.key}`} type={field.type ?? 'text'} autoComplete={field.auto} value={form[field.key]} maxLength={field.key === 'postal_code' ? 20 : 200} dir={field.type === 'email' ? 'ltr' : 'auto'}
          onChange={(event) => setForm({ ...form, [field.key]: event.target.value })} /></label>)}
      <label className="field" htmlFor={`${formId}-country`}><span>{t('billing.contact.country')}</span>
        <select id={`${formId}-country`} value={form.country} onChange={(event) => setForm({ ...form, country: event.target.value })}>{countries.map((code) => <option key={code} value={code}>{countryName(code)}</option>)}</select></label>
    </div>
    {problem && <Note error>{problem}</Note>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('common.loading') : t('billing.contact.save')}</button></div>
  </form>;
}
