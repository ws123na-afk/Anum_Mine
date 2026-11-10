// Finance, Lent and borrowed: one payment split across several loans with the same person, an amount each
// (docs/finance.md#lent-and-borrowed). The parts are in the payment's currency and add up to no more than it; the API
// makes every part or none and checks each figure again. ANUM only links records: it never moves money.
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import type { FinancePaymentCandidate, FinancePersonLoan } from '@anum/contracts';
import * as pm from './lib/financePersonalApi';
import { formatAmount, isolate } from './lib/finance';
import { checkSplit, splitCandidates, suggestedPart, type SplitLine } from './lib/financeDepth2';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Denied, LoadError, Money, Notice, failed, type Load } from './FinanceParts';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const amount = (value: string, currency: string) => isolate(formatAmount(value, currency));

export function LoanSplitDialog({ loan, onDone, onClose }: { loan: FinancePersonLoan; onDone: (text: string) => void; onClose: () => void }) {
  const direction: 'in' | 'out' = loan.direction === 'lent' ? 'in' : 'out';
  const [loans, setLoans] = useState<Load<FinancePersonLoan[]>>({ kind: 'loading' });
  const [rows, setRows] = useState<Load<FinancePaymentCandidate[]>>({ kind: 'loading' });
  const [q, setQ] = useState('');
  const [chosen, setChosen] = useState<FinancePaymentCandidate | null>(null);
  const [lines, setLines] = useState<SplitLine[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const searchId = useId();
  const loadLoans = useCallback(() => {
    setLoans({ kind: 'loading' });
    pm.listLoans({ q: loan.person, direction: loan.direction, status: 'all', limit: 100, offset: 0 })
      .then((page) => {
        const found = splitCandidates(page.items, loan);
        setLoans({ kind: 'ready', value: found });
        setLines(found.map((l) => ({ loanId: l.id, amount: suggestedPart(l) })));
      }).catch((e) => setLoans(failed(e)));
  }, [loan]);
  const loadRows = useCallback(() => {
    setRows({ kind: 'loading' });
    pm.candidates('person_loan', direction, { currency: loan.currency, q: q || null }).then((r) => setRows({ kind: 'ready', value: r.items })).catch((e) => setRows(failed(e)));
  }, [direction, loan.currency, q]);
  useEffect(() => { loadLoans(); }, [loadLoans]);
  useEffect(() => { loadRows(); }, [loadRows]);
  const open = loans.kind === 'ready' ? loans.value : [];
  const check = useMemo(() => checkSplit(lines, open, chosen?.remaining ?? '0', loan.currency), [lines, open, chosen, loan.currency]);
  const save = async () => {
    if (!chosen || check.problem) return;
    setBusy(true); setError(null);
    try {
      const made = await pm.splitLoans({ canonical_id: chosen.canonical_id, kind: 'repayment', parts: check.parts.map((p) => ({ loan_id: p.loanId, amount: p.amount })) });
      onDone(t('fdepth2.split.done', { count: made.links.length }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <Modal title={t('fdepth2.split.title', { name: loan.person })} onClose={onClose} className="fpmDialog">
    <p className="muted">{t('fdepth2.split.help')}</p>
    {loans.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
    {loans.kind === 'error' && <LoadError message={t('fpm.error', { message: loans.message })} retry={loadLoans} />}
    {loans.kind === 'denied' && <Denied />}
    {loans.kind === 'ready' && open.length < 2 && <Notice>{t('fdepth2.split.oneLoan')}</Notice>}
    {loans.kind === 'ready' && open.length >= 2 && (chosen === null ? <>
      <h3 className="finAdvisorSub">{t('fdepth2.split.pick')}</h3>
      <label className="field" htmlFor={searchId}><span>{t('fpm.link.search')}</span>
        <input id={searchId} value={q} onChange={(e) => setQ(e.target.value)} dir="auto" /></label>
      {rows.kind === 'loading' && <Notice busy>{t('fpm.loading')}</Notice>}
      {rows.kind === 'denied' && <Denied />}
      {rows.kind === 'error' && <LoadError message={t('fpm.error', { message: rows.message })} retry={loadRows} />}
      {rows.kind === 'ready' && (rows.value.length === 0 ? <p className="muted">{t('fpm.link.none')}</p>
        : <ul className="finMoveList fpmCandidates">{rows.value.map((c) => <li key={`${c.canonical_id}-${c.currency}`}>
          <div><span dir="auto">{c.merchant}</span><small>{day(c.posted_on)} · {t('fpm.link.left', { amount: amount(c.remaining, c.currency) })}</small></div>
          <Money value={c.amount} currency={c.currency} />
          <button type="button" className="secondary" onClick={() => setChosen(c)} aria-label={t('fpm.link.useAria', { name: c.merchant, date: day(c.posted_on) })}>
            {t('fdepth2.split.choose')}</button>
        </li>)}</ul>)}
    </> : <>
      <p className="fpmSplitChosen"><span dir="auto">{t('fdepth2.split.chosen', { name: chosen.merchant, date: day(chosen.posted_on), amount: amount(chosen.remaining, chosen.currency) })}</span>
        <button type="button" className="secondary" onClick={() => setChosen(null)}>{t('fdepth2.split.change')}</button></p>
      <h3 className="finAdvisorSub">{t('fdepth2.split.loans', { name: loan.person, currency: loan.currency })}</h3>
      <ul className="fpmSplitLines">{open.map((l) => {
        const line = lines.find((x) => x.loanId === l.id);
        return <li key={l.id}>
          <label className="field"><span>{t('fdepth2.split.loanLine', { date: day(l.lent_on), left: amount(l.outstanding, l.currency) })}</span>
            <input inputMode="decimal" dir="ltr" value={line?.amount ?? ''} aria-label={t('fdepth2.split.partAria', { date: day(l.lent_on) })}
              onChange={(e) => setLines((all) => all.map((x) => (x.loanId === l.id ? { ...x, amount: e.target.value } : x)))} /></label>
        </li>;
      })}</ul>
      <p className="fpmSplitTotal" aria-live="polite">{t('fdepth2.split.total', { total: amount(check.total, loan.currency), remaining: amount(chosen.remaining, chosen.currency) })}</p>
      {check.problem && <Notice error>{t(`fdepth2.split.problem.${check.problem}` as MessageKey)}</Notice>}
    </>)}
    {error && <Notice error>{error}</Notice>}
    <div className="actions">
      <button type="button" className="secondary" onClick={onClose}>{t('fpm.close')}</button>
      {chosen && <button type="button" disabled={busy || check.problem !== null} onClick={() => void save()}>{busy ? t('finance.saving') : t('fdepth2.split.save')}</button>}
    </div>
  </Modal>;
}
