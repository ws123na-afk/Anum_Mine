// The invoice drawer's "Email and reminders" and "Card payment link" blocks (docs/business.md#emailing-an-invoice-and-reminders,
// docs/business.md#payment-links). Asking to email an invoice or send a reminder never sends it: the API plans the email as a
// delivery that waits for an owner's approval in Approvals. A payment link is a Moyasar page or a Stripe Payment Link the customer pays on; ANUM never
// sees card details.
import { useEffect, useId, useState, type FormEvent } from 'react';
import { BellRing, Copy, CreditCard, ExternalLink, Mail } from 'lucide-react';
import type { BusinessInvoice, BusinessPaymentProvider } from '@anum/contracts';
import { getLocale, t, type MessageKey } from './lib/i18n';
import { formatMoney } from './lib/business';
import { SEND_LANGUAGES, emailBody, linkProvider, messageStatusLabel, messageTitle, messageTone, waitingMessages } from './lib/invoices';
import * as inv from './lib/businessInvoicesApi';
import { Modal } from './Dialog';
import { Badge, Note, failure, when } from './BusinessParts';

type Mode = 'invoice' | 'reminder';

export function SendBlock({ invoice, write, onChanged }: { invoice: BusinessInvoice; write: boolean; onChanged: (next: BusinessInvoice, notice: string) => void }) {
  const [mode, setMode] = useState<Mode | null>(null);
  const headingId = useId();
  if (invoice.status === 'draft' || invoice.status === 'pending_approval' || invoice.status === 'void') {
    if (invoice.status === 'void' && invoice.messages.length === 0) return null;
    if (invoice.status !== 'void') return <section className="bzBlock" aria-labelledby={headingId}>
      <h3 id={headingId}>{t('inv.send.title')}</h3><p className="muted bzHint">{t('inv.send.notIssued')}</p></section>;
  }
  const can = invoice.can;
  const invoiceWaiting = waitingMessages(invoice.messages, 'invoice'), reminderWaiting = waitingMessages(invoice.messages, 'reminder');
  return <section className="bzBlock" aria-labelledby={headingId}>
    <h3 id={headingId}>{t('inv.send.title')}</h3>
    <p className="muted bzHint">{t('inv.send.help')}</p>
    {invoice.next_reminder !== null && <Note>{t('inv.send.reminderDue', { number: invoice.next_reminder, days: invoice.days_overdue })}</Note>}
    {write && <div className="bzInline">
      {can.email && <button type="button" className="secondary" disabled={invoiceWaiting} onClick={() => setMode('invoice')}><Mail aria-hidden="true" />{t('inv.send.email')}</button>}
      {can.remind && <button type="button" className={invoice.next_reminder !== null ? undefined : 'secondary'} disabled={reminderWaiting} onClick={() => setMode('reminder')}>
        <BellRing aria-hidden="true" />{t('inv.send.remind')}</button>}
    </div>}
    {invoice.status === 'sent' && !can.remind && invoice.balance > 0 && <p className="muted bzHint">{t('inv.send.notOverdue')}</p>}
    {invoice.messages.length === 0 ? <p className="muted">{t('inv.send.none')}</p>
      : <ul className="bzTrail">{[...invoice.messages].reverse().map((item) => <li key={item.id}>
        <div className="bzInline"><strong>{messageTitle(item)}</strong><Badge tone={messageTone(item.status)}>{messageStatusLabel(item.status)}</Badge>
          {item.status === 'waiting_approval' && <a href="#approvals">{t('inv.send.openApprovals')}</a>}</div>
        <small dir="auto">{item.subject}</small>
        <small><bdi>{t('inv.send.line', { to: item.to_email, by: item.requested_by, when: when(item.created_at) })}</bdi></small>
        {item.detail && item.status !== 'waiting_approval' && <small dir="auto">{item.detail}</small>}</li>)}</ul>}
    {mode && <EmailDialog invoice={invoice} mode={mode} onCancel={() => setMode(null)}
      onSaved={(next) => { setMode(null); onChanged(next, t('inv.send.requested')); }} />}
  </section>;
}

function EmailDialog({ invoice, mode, onCancel, onSaved }: { invoice: BusinessInvoice; mode: Mode; onCancel: () => void; onSaved: (next: BusinessInvoice) => void }) {
  const [to, setTo] = useState(''), [language, setLanguage] = useState<'en' | 'ar'>(getLocale() === 'ar' ? 'ar' : 'en');
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const title = mode === 'invoice' ? t('inv.send.dialogEmail', { number: invoice.number ?? '' })
    : t('inv.send.dialogRemind', { reminder: invoice.next_reminder ?? invoice.messages.filter((m) => m.kind === 'reminder' && (m.status === 'sent' || m.status === 'waiting_approval')).length + 1, number: invoice.number ?? '' });
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = emailBody(to, language);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try {
      const result = mode === 'invoice' ? await inv.emailInvoice(invoice.id, body.value) : await inv.remindInvoice(invoice.id, body.value);
      onSaved(result.invoice);
    } catch (error) { setProblem(failure(error, title)); setBusy(false); }
  }
  return <Modal title={title} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={title} noValidate>
      <p className="muted bzHint">{t('inv.send.help')}</p>
      <label className="field"><span>{t('inv.send.to')}</span>
        <input type="email" inputMode="email" autoComplete="email" dir="ltr" value={to} placeholder={invoice.bill_to_email || undefined} onChange={(event) => setTo(event.target.value)} data-autofocus /></label>
      <p className="muted bzHint"><bdi>{invoice.bill_to_email ? t('inv.send.toHelp', { email: invoice.bill_to_email }) : t('inv.send.toHelpNone')}</bdi></p>
      <label className="field"><span>{t('inv.send.language')}</span>
        <select value={language} onChange={(event) => setLanguage(event.target.value as 'en' | 'ar')}>{SEND_LANGUAGES.map((code) => <option key={code} value={code}>{t(`inv.send.lang.${code}`)}</option>)}</select></label>
      <p className="muted bzHint">{t('inv.send.attachNote')}</p>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.send.submit')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

type ProviderLoad = { kind: 'loading' } | { kind: 'ready'; value: BusinessPaymentProvider } | { kind: 'error'; message: string };

export function PaymentLinkBlock({ invoice, write, onChanged }: { invoice: BusinessInvoice; write: boolean; onChanged: (next: BusinessInvoice, notice: string) => void }) {
  const [provider, setProvider] = useState<ProviderLoad>({ kind: 'loading' }), [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null), [copyNote, setCopyNote] = useState('');
  const headingId = useId();
  const relevant = invoice.status === 'sent' && invoice.balance > 0;
  useEffect(() => {
    if (!relevant) return;
    let live = true;
    setProvider({ kind: 'loading' });
    inv.getPaymentProvider().then((value) => { if (live) setProvider({ kind: 'ready', value }); },
      (error) => { if (live) setProvider({ kind: 'error', message: failure(error, t('inv.link.failed')) }); });
    return () => { live = false; };
  }, [relevant, tick]);
  if (!relevant) return null;
  const link = invoice.payment_link;
  // The provider of the link shown, else the one a new link would use.
  const via = linkProvider(link, provider.kind === 'ready' ? provider.value : null);
  async function make() {
    setBusy(true); setProblem(null);
    try { onChanged(await inv.makePaymentLink(invoice.id), t('inv.link.made')); } catch (error) { setProblem(failure(error, t('inv.link.make'))); } finally { setBusy(false); }
  }
  async function copy(url: string) {
    try { await navigator.clipboard.writeText(url); setCopyNote(t('inv.link.copied')); } catch { setCopyNote(t('inv.link.copyFailed')); }
  }
  return <section className="bzBlock" aria-labelledby={headingId}>
    <h3 id={headingId}>{t('inv.link.title')}</h3>
    <p className="muted bzHint">{t('inv.link.help')}</p>
    {provider.kind === 'loading' && <p className="muted" role="status">{t('inv.link.loading')}</p>}
    {provider.kind === 'error' && <><Note error>{provider.message}</Note>
      <div className="bzInline"><button type="button" className="secondary" onClick={() => setTick((value) => value + 1)}>{t('common.tryAgain')}</button></div></>}
    {provider.kind === 'ready' && !provider.value.provider && !link && <p className="muted bzHint">{t('inv.link.off')}</p>}
    {via && <p className="muted bzHint">{t(`inv.link.via.${via}` as MessageKey)}</p>}
    {provider.kind === 'ready' && provider.value.mode === 'test' && <p className="muted bzHint">{t('inv.link.test')}</p>}
    {link && <div className="invLink">
      <p className="muted">{t('inv.link.amount', { amount: formatMoney(link.amount, link.currency) })}</p>
      <p className="invLinkUrl" dir="ltr"><a href={link.url} target="_blank" rel="noopener noreferrer">{link.url}</a></p>
      <div className="bzInline">
        <button type="button" className="secondary" onClick={() => void copy(link.url)}><Copy aria-hidden="true" />{t('inv.link.copy')}</button>
        <a className="button secondary" href={link.url} target="_blank" rel="noopener noreferrer"><ExternalLink aria-hidden="true" />{t('inv.link.open')}</a>
      </div>
      <p className="muted bzHint" role="status">{copyNote}</p>
    </div>}
    {!link && write && invoice.can.payment_link && provider.kind === 'ready' && provider.value.provider !== null &&
      <div className="bzInline"><button type="button" disabled={busy} onClick={() => void make()}><CreditCard aria-hidden="true" />{busy ? t('dialog.working') : t('inv.link.make')}</button></div>}
    {problem && <Note error>{problem}</Note>}
  </section>;
}
