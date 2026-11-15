// Promises to pay and the agreed balance on a loan with a person (docs/finance.md#lent-and-borrowed). Whether a promise was
// kept is read by the API from the repayments linked from the day it was made to the day promised; the page never sets it.
import { useId, useState, type FormEvent } from 'react';
import { Trash2 } from 'lucide-react';
import * as dx from './lib/financeDebtsApi';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { FormError, Money, Notice } from './FinanceParts';
import { StatusChip } from './FinanceShell';
import './financeDebts.css';

const day = (value: string | null) => (value ? formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '-');
const tone = (status: string) => (status === 'kept' ? 'ok' : status === 'broken' ? 'bad' : status === 'part_kept' ? 'warn' : 'info');

export function LoanPromises({ loanId, currency, promises, agreed, onChanged }: {
  loanId: string; currency: string; promises: (dx.PromiseView | (Omit<dx.PromiseView, 'status' | 'paid'> & { status?: string; paid?: string }))[];
  agreed: string | null; onChanged: () => void;
}) {
  const errorId = useId();
  const [amount, setAmount] = useState(''), [dueBy, setDueBy] = useState(''), [note, setNote] = useState('');
  const [balance, setBalance] = useState(agreed ?? '');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [done, setDone] = useState<string | null>(null);
  async function run(work: () => Promise<unknown>, text: string) {
    setBusy(true); setError(null); setDone(null);
    try { await work(); setDone(text); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  function addPromise(event: FormEvent) {
    event.preventDefault();
    if (!amount.trim() || !dueBy) { setError(t('fdebt.promise.needed')); return; }
    void run(() => dx.addPromise(loanId, { amount: amount.trim(), due_by: dueBy, note: note.trim() || null }), t('fdebt.promise.added'))
      .then(() => { setAmount(''); setDueBy(''); setNote(''); });
  }
  return <section className="fdebtPromises" aria-labelledby={`${errorId}-title`}>
    <h3 id={`${errorId}-title`} className="finAdvisorSub">{t('fdebt.promise.title')}</h3>
    {promises.length === 0 ? <p className="muted">{t('fdebt.promise.none')}</p> : <ul className="fdebtList">
      {promises.map((p) => <li key={p.id}>
        <span>{t('fdebt.promise.by', { date: day(p.due_by) })}</span><Money value={p.amount} currency={currency} />
        {p.status && <StatusChip tone={tone(p.status)}>{t(`fdebt.promise.${p.status}` as MessageKey)}</StatusChip>}
        {p.note && <small className="muted"><bdi>{p.note}</bdi></small>}
        <button type="button" className="secondary iconOnly" disabled={busy} aria-label={t('fdebt.promise.remove', { date: day(p.due_by) })}
          onClick={() => void run(() => dx.removePromise(loanId, p.id), t('fdebt.promise.removed'))}><Trash2 aria-hidden="true" /></button>
      </li>)}
    </ul>}
    <form className="finForm fdebtInline" onSubmit={addPromise} noValidate>
      <label>{t('fdebt.promise.amount', { currency })}<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} aria-describedby={error ? errorId : undefined} /></label>
      <label>{t('fdebt.promise.dueBy')}<input type="date" value={dueBy} onChange={(e) => setDueBy(e.target.value)} /></label>
      <label>{t('fdebt.promise.note')}<input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} /></label>
      <button type="submit" disabled={busy}>{t('fdebt.promise.add')}</button>
    </form>
    <form className="finForm fdebtInline" noValidate onSubmit={(e) => { e.preventDefault(); void run(() => dx.saveAgreedBalance(loanId, balance.trim() || null, null), t('fdebt.agreed.saved')); }}>
      <label>{t('fdebt.agreed.label', { currency })}<input inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value)} />
        <small className="muted">{t('fdebt.agreed.hint')}</small></label>
      <button type="submit" className="secondary" disabled={busy}>{t('fdebt.agreed.save')}</button>
    </form>
    <FormError id={errorId} text={error} />
    {done && <Notice>{done}</Notice>}
  </section>;
}
