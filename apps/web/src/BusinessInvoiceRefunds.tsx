// Refunds of a card payment asked for from ANUM, inside the invoice drawer (docs/business.md#refunds-from-anum).
// A member or owner asks; only an owner approves in Approvals with the hash shown here, and only then does the money
// go back to the card through Stripe or Moyasar. The server checks the amount again; this form only helps.
import { useState, type FormEvent } from 'react';
import { RotateCcw } from 'lucide-react';
import type { BusinessInvoice, InvoicePayment, RefundRequest } from '@anum/contracts';
import { t } from './lib/i18n';
import { formatMoney } from './lib/business';
import { shortHash } from './lib/approvals';
import { emptyRefund, newRefundKey, providerName, refundBody, refundableOf, requestStatusLabel, requestTone, requestsOf, type RefundDraft } from './lib/invoiceRefunds';
import * as inv from './lib/businessInvoicesApi';
import { ConfirmDialog, Modal } from './Dialog';
import { Badge, Note, day, failure } from './BusinessParts';

export function PaymentRefunds({ invoice, payment, onChanged }: { invoice: BusinessInvoice; payment: InvoicePayment; onChanged: (next: BusinessInvoice, message: string) => void }) {
  const max = refundableOf(invoice, payment);
  const requests = requestsOf(invoice, payment.id);
  const [asking, setAsking] = useState(false), [withdrawing, setWithdrawing] = useState<RefundRequest | null>(null);
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  if (max <= 0 && requests.length === 0) return null;
  const money = (value: number) => formatMoney(value, invoice.currency);
  async function withdraw(request: RefundRequest) {
    setBusy(true); setProblem(null);
    try { onChanged(await inv.withdrawRefund(invoice.id, request.id), t('inv.rr.withdrawn')); setWithdrawing(null); }
    catch (error) { setProblem(failure(error, t('inv.rr.withdraw'))); setWithdrawing(null); }
    finally { setBusy(false); }
  }
  return <div className="bzRefunds" role="group" aria-label={t('inv.rr.list')}>
    {requests.length > 0 && <>
      <strong>{t('inv.rr.list')}</strong>
      <ul>{requests.map((request) => <li key={request.id}>
        <span className="bzInline"><span>{t('inv.rr.line', { amount: money(request.amount), by: request.requested_by, date: day(request.created_at) })}</span>
          <Badge tone={requestTone(request.status)}>{requestStatusLabel(request.status)}</Badge></span>
        <small dir="auto">{request.reason}</small>
        {request.status === 'waiting_approval' && <small>{t('inv.rr.code', { hash: shortHash(request.payload_hash) })}</small>}
        {request.detail && request.status !== 'waiting_approval' && <small dir="auto">{request.detail}</small>}
        {request.status === 'waiting_approval' && invoice.can.refund && <span className="bzInline"><button type="button" className="secondary" disabled={busy}
          onClick={() => setWithdrawing(request)}>{t('inv.rr.withdraw')}</button></span>}
      </li>)}</ul></>}
    {max > 0 && <div className="bzInline">
      <button type="button" className="secondary" onClick={() => setAsking(true)}><RotateCcw aria-hidden="true" />{t('inv.rr.ask')}</button>
      <small className="muted">{t('inv.rr.who')}</small></div>}
    {problem && <Note error>{problem}</Note>}
    {asking && <RefundDialog invoice={invoice} payment={payment} max={max} onCancel={() => setAsking(false)}
      onSaved={(next) => { setAsking(false); onChanged(next, t('inv.rr.asked')); }} />}
    {withdrawing && <ConfirmDialog title={t('inv.rr.withdrawTitle')} confirmLabel={t('inv.rr.withdraw')} danger busy={busy}
      onCancel={() => setWithdrawing(null)} onConfirm={() => void withdraw(withdrawing)}><p>{t('inv.rr.withdrawText')}</p></ConfirmDialog>}
  </div>;
}

function RefundDialog({ invoice, payment, max, onCancel, onSaved }: { invoice: BusinessInvoice; payment: InvoicePayment; max: number; onCancel: () => void;
  onSaved: (next: BusinessInvoice) => void }) {
  const [draft, setDraft] = useState<RefundDraft>(() => emptyRefund(max)), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  // One key for this form: a retried submit (a lost answer, a double click) is the same request.
  const [key] = useState(() => newRefundKey());
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = refundBody(draft, max, invoice.currency);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved((await inv.requestRefund(invoice.id, payment.id, body.value, key)).invoice); }
    catch (error) { setProblem(failure(error, t('inv.rr.title'))); setBusy(false); }
  }
  const set = (field: keyof RefundDraft) => (event: { target: { value: string } }) => setDraft({ ...draft, [field]: event.target.value });
  return <Modal title={t('inv.rr.title')} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.rr.title')} noValidate>
      <p className="muted bzHint">{t('inv.rr.help', { provider: providerName(payment.provider ?? ''), max: formatMoney(max, invoice.currency) })}</p>
      <label className="field"><span>{t('inv.rr.amount')}</span><input inputMode="decimal" value={draft.amount} onChange={set('amount')} data-autofocus /></label>
      <label className="field"><span>{t('inv.rr.reason')}</span><input value={draft.reason} onChange={set('reason')} dir="auto" maxLength={300} aria-describedby={`rr-help-${payment.id}`} />
        <small id={`rr-help-${payment.id}`} className="muted">{t('inv.rr.reasonHelp')}</small></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.rr.submit')}</button>
        <button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}
