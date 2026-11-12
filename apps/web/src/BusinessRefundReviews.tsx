// Card refunds Stripe or Moyasar reported for a payment ANUM has no record of (docs/business.md#refunds-anum-could-not-match).
// Nothing was taken off an invoice; an owner looks each one up in the provider's dashboard and marks it checked with a
// note (or opens it again). Members read the list. Loaded on demand from Business, Invoices (#business-invoices/refunds).
import { useId, useState, type FormEvent } from 'react';
import { CheckCircle2, RotateCcw } from 'lucide-react';
import type { BusinessSetup, RefundReview } from '@anum/contracts';
import { t } from './lib/i18n';
import { providerName, reviewAmount } from './lib/invoiceRefunds';
import * as inv from './lib/businessInvoicesApi';
import { Modal } from './Dialog';
import { Badge, Empty, Note, Pager, day, failure, when } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';

type State = 'open' | 'checked' | 'all';

export function RefundReviews({ setup, onOpenInvoice }: { setup: BusinessSetup; onOpenInvoice: (id: string) => void }) {
  const [state, setState] = useState<State>('open'), [tick, setTick] = useState(0);
  const [checking, setChecking] = useState<RefundReview | null>(null), [notice, setNotice] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const list = usePaged((offset) => inv.listRefundReviews({ state, limit: 25, offset }), [state, tick]);
  const owner = setup.permissions.manage;
  const heading = useId(), filterId = useId();
  async function reopen(review: RefundReview) {
    setBusy(review.id); setProblem(null);
    try { await inv.reopenRefundReview(review.id); setNotice(t('inv.rv.saved')); setTick((value) => value + 1); }
    catch (error) { setProblem(failure(error, t('inv.rv.reopen'))); }
    finally { setBusy(null); }
  }
  const empty = list.load.kind === 'ready' && list.items.length === 0;
  // The table below is the region (it scrolls on a phone); the section only groups it with its heading.
  return <section className="bzBlock">
    <h2 id={heading}>{t('inv.rv.title')}</h2>
    <p className="muted bzHint">{t('inv.rv.help')}</p>
    {!owner && <Note>{t('inv.rv.ownerOnly')}</Note>}
    <div className="bzToolbar">
      <label className="bzCompact" htmlFor={filterId}><span>{t('inv.rv.filter')}</span>
        <select id={filterId} value={state} onChange={(event) => setState(event.target.value as State)}>
          <option value="open">{t('inv.rv.filter.open')}</option><option value="checked">{t('inv.rv.filter.checked')}</option><option value="all">{t('inv.rv.filter.all')}</option>
        </select></label>
    </div>
    {notice && <Note>{notice}</Note>}
    {problem && <Note error>{problem}</Note>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={false} onRetry={() => void list.first()} onClear={() => setState('open')} />
    {empty && <Empty title={t('inv.rv.emptyTitle')} text={state === 'checked' ? t('inv.rv.emptyChecked') : t('inv.rv.emptyText')} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <p className="bzCount" aria-live="polite">{t('inv.rv.count', { count: list.total })}</p>
      <div className="pane dataTableWrap" role="region" aria-label={t('inv.rv.title')} tabIndex={0}><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('inv.rv.title')}</caption>
        <thead><tr><th scope="col">{t('inv.rv.col.refund')}</th><th scope="col">{t('inv.rv.col.invoice')}</th><th scope="col" className="num">{t('inv.rv.col.amount')}</th>
          <th scope="col">{t('inv.rv.col.state')}</th></tr></thead>
        <tbody>{list.items.map((review) => <tr key={review.id}>
          <th scope="row"><span>{t('inv.rv.refundLine', { provider: providerName(review.provider), refund: review.provider_refund_id })}</span>
            <small>{t('inv.rv.paymentLine', { payment: review.provider_payment_id, date: day(review.created_at) })}</small></th>
          <td>{review.invoice_id ? <button type="button" className="linkButton" onClick={() => onOpenInvoice(review.invoice_id ?? '')}>
            <span dir="auto">{t('inv.rv.openInvoice', { number: review.invoice_number || review.invoice_id })}</span></button> : t('inv.rv.noInvoice')}</td>
          <td className="num">{reviewAmount(review)}</td>
          <td><div className="invBadges"><Badge tone={review.status === 'open' ? 'warning' : 'success'}>{t(review.status === 'open' ? 'inv.rv.state.open' : 'inv.rv.state.checked')}</Badge></div>
            {review.status === 'checked' && <small>{t('inv.rv.checkedBy', { by: review.checked_by ?? '', date: when(review.checked_at) })}</small>}
            {review.note && <small dir="auto">{review.note}</small>}
            {owner && (review.status === 'open'
              ? <button type="button" className="secondary" onClick={() => setChecking(review)}><CheckCircle2 aria-hidden="true" />{t('inv.rv.check')}</button>
              : <button type="button" className="secondary" disabled={busy === review.id} onClick={() => void reopen(review)}><RotateCcw aria-hidden="true" />{t('inv.rv.reopen')}</button>)}
          </td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {checking && <CheckDialog review={checking} onCancel={() => setChecking(null)}
      onSaved={() => { setChecking(null); setNotice(t('inv.rv.saved')); setTick((value) => value + 1); }} />}
  </section>;
}

function CheckDialog({ review, onCancel, onSaved }: { review: RefundReview; onCancel: () => void; onSaved: () => void }) {
  const [note, setNote] = useState(''), [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const field = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem(null);
    try { await inv.checkRefundReview(review.id, note.replace(/\s+/g, ' ').trim()); onSaved(); }
    catch (error) { setProblem(failure(error, t('inv.rv.check'))); setBusy(false); }
  }
  return <Modal title={t('inv.rv.checkTitle')} className="formDialog" onClose={onCancel}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.rv.checkTitle')} noValidate>
      <p className="muted bzHint">{t('inv.rv.refundLine', { provider: providerName(review.provider), refund: review.provider_refund_id })}, {reviewAmount(review)}</p>
      <label className="field" htmlFor={field}><span>{t('inv.rv.note')}</span><input id={field} value={note} onChange={(event) => setNote(event.target.value)} dir="auto" maxLength={300} data-autofocus /></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.rv.check')}</button>
        <button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}
