// Finance, Debts and goals, Loans (docs/finance.md#bank-loans): a bank or finance loan entered with its dates, its
// schedule from the start to the end, every instalment marked paid (with its payment), missed, upcoming or ambiguous,
// the totals, the payments ANUM found (accepted at once, undone at once) and the loans already paid off or closed.
// Every figure comes from the API (GET /loans, GET /debts/{id}/loan); nothing is linked without the owner's accept or pick.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { CalendarClock, CheckCircle2, Landmark, Layers, Pencil, Search, Undo2, Wallet } from 'lucide-react';
import type { FinanceAccount, FinanceDebt, FinanceLoanList, FinanceLoanRow, FinanceLoanSummary, FinanceLoanView, FinanceSuggestion } from '@anum/contracts';
import * as plan from './lib/financePlanningApi';
import * as rx from './lib/financeRulesApi';
import { formatAmount, isolate } from './lib/finance';
import { aheadBehind, loanBody, loanForm, loanSuggestion, rowTone, type LoanForm } from './lib/financeLoans';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';

const PAGE = 24;
const longDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const amount = (value: string | null | undefined, currency: string) => isolate(formatAmount(value ?? '0', currency));

/** The Loans section: every loan with terms (open first), or one loan's page. */
export function LoansSection({ debts, accounts, loans, selected, onSelect, onReload, onChanged }: {
  debts: FinanceDebt[]; accounts: FinanceAccount[]; loans: Load<FinanceLoanList>; selected: string | null; onSelect: (debtId: string | null) => void;
  onReload: () => Promise<void> | void; onChanged: () => void;
}) {
  const [picked, setAdding] = useState<FinanceDebt | null>(null);
  if (loans.kind === 'denied') return <Denied />;
  if (loans.kind === 'error') return <LoadError message={t('floan.error.load', { message: loans.message })} retry={onReload} />;
  if (loans.kind === 'loading') return <Notice busy>{t('floan.loading')}</Notice>;
  const withTerms = new Set(loans.value.items.map((l) => l.debt_id));
  if (selected && withTerms.has(selected)) return <LoanPage debtId={selected} accounts={accounts} onBack={() => onSelect(null)} onChanged={() => { void onReload(); onChanged(); }} />;
  // A debt picked from the Debts list that has no terms yet: its terms form opens here.
  const adding = picked ?? (selected ? debts.find((d) => d.id === selected) ?? null : null);
  const without = debts.filter((d) => !withTerms.has(d.id) && (d.kind === 'loan' || d.kind === 'mortgage' || d.kind === 'other'));
  return <div className="floanPage">
    <FinBanner tone="info" icon={<Landmark />}>{t('floan.intro')}</FinBanner>
    <FinCard title={t('floan.list.title')} icon={<Layers />} subtitle={t('floan.list.sub')}>
      {loans.value.items.length === 0 ? <Empty icon={<Landmark aria-hidden="true" />} title={t('floan.list.empty')} text={t('floan.list.emptyText')} />
        : <ul className="fplRows">{loans.value.items.map((loan) => <LoanListRow key={loan.debt_id} loan={loan} onOpen={() => onSelect(loan.debt_id)} />)}</ul>}
    </FinCard>
    {without.length > 0 && <FinCard title={t('floan.add.title')} subtitle={t('floan.add.sub')}>
      <ul className="fplRows">{without.map((d) => <li key={d.id} className="fplRow compact">
        <span className="fplRowIcon" aria-hidden="true"><Landmark /></span>
        <div className="fplRowMain"><strong dir="auto">{d.name}</strong><small>{t('fplan.debt.balance', { amount: amount(d.balance, d.currency) })}</small></div>
        <button type="button" className="secondary" onClick={() => setAdding(d)}>{t('floan.add.button')}</button>
      </li>)}</ul>
    </FinCard>}
    {adding && <Modal title={t('floan.terms.title', { name: adding.name })} className="formDialog" onClose={() => { setAdding(null); onSelect(null); }}>
      <LoanTermsForm debt={adding} terms={null} accounts={accounts} onDone={(saved) => {
        setAdding(null); onSelect(null);
        if (saved) void Promise.resolve(onReload()).then(() => { onChanged(); onSelect(saved); });
      }} />
    </Modal>}
  </div>;
}

function LoanListRow({ loan, onOpen }: { loan: FinanceLoanSummary; onOpen: () => void }) {
  const tt = loan.totals, c = loan.currency;
  const pace = aheadBehind(tt.months_ahead);
  return <li className="fplRow">
    <span className="fplRowIcon" aria-hidden="true">{tt.status === 'open' ? <Landmark /> : <CheckCircle2 />}</span>
    <div className="fplRowMain">
      <strong dir="auto">{loan.name}{loan.lender ? <span className="muted"> · <bdi>{loan.lender}</bdi></span> : null}</strong>
      <small>{tt.status === 'open'
        ? t('floan.row.open', { balance: amount(tt.balance, c), paid: tt.paid_count, total: tt.due_count })
        : t(`floan.status.${tt.status}` as MessageKey)}</small>
    </div>
    {tt.status === 'open' && <StatusChip tone={tt.months_ahead < 0 ? 'bad' : 'ok'}>{t(pace.key, { count: pace.count })}</StatusChip>}
    {tt.found + tt.ambiguous > 0 && tt.status === 'open' && <StatusChip tone="info">{t('floan.row.found', { count: tt.found + tt.ambiguous })}</StatusChip>}
    <button type="button" className="secondary" onClick={onOpen} aria-label={t('floan.row.openLabel', { name: loan.name })}>{t('floan.row.openButton')}</button>
  </li>;
}

/** One loan: totals, the payments found, the schedule and the accepted batches. */
export function LoanPage({ debtId, accounts, onBack, onChanged }: { debtId: string; accounts: FinanceAccount[]; onBack: () => void; onChanged: () => void }) {
  const [state, setState] = useState<Load<FinanceLoanView>>({ kind: 'loading' });
  const [suggestion, setSuggestion] = useState<FinanceSuggestion | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false), [editing, setEditing] = useState(false), [removing, setRemoving] = useState(false);
  const [picking, setPicking] = useState<FinanceLoanRow | null>(null), [offset, setOffset] = useState<number | null>(null);
  const load = useCallback(async () => {
    try {
      const [view, queue] = await Promise.all([plan.loan(debtId), rx.listSuggestions({ kind: 'loan_payments', limit: 100, offset: 0 }).catch(() => null)]);
      setState({ kind: 'ready', value: view });
      setSuggestion(queue ? loanSuggestion(queue.items, debtId) : null);
    } catch (e) { setState(failed(e)); }
  }, [debtId]);
  useEffect(() => { void load(); }, [load]);
  const back = <button type="button" className="secondary" onClick={onBack}>{t('floan.back')}</button>;
  if (state.kind === 'denied') return <><div className="actions">{back}</div><Denied /></>;
  if (state.kind === 'error') return <><div className="actions">{back}</div><LoadError message={t('floan.error.load', { message: state.message })} retry={() => void load()} /></>;
  if (state.kind === 'loading') return <Notice busy>{t('floan.loading')}</Notice>;
  const loan = state.value, tt = loan.totals, c = loan.currency;
  const pace = aheadBehind(tt.months_ahead);
  // The first page shows the instalments around today: the first one not paid yet.
  const firstOpen = Math.max(0, loan.rows.findIndex((r) => r.status !== 'paid' && r.status !== 'settled'));
  const start = offset ?? Math.floor(firstOpen / PAGE) * PAGE;
  const rows = loan.rows.slice(start, start + PAGE);

  async function accept() {
    if (!suggestion) return;
    setBusy(true); setNotice(null);
    try {
      await rx.acceptSuggestion(suggestion.key, suggestion.payload_hash);
      setNotice({ text: t('floan.found.accepted', { count: Number(suggestion.params.found ?? 0) }) }); await load(); onChanged();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); await load(); } finally { setBusy(false); }
  }
  async function dismiss() {
    if (!suggestion) return;
    setBusy(true);
    try { await rx.rejectSuggestion(suggestion.key); setNotice({ text: t('floan.found.dismissed') }); await load(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(false); }
  }
  async function undo(batchId: string) {
    setBusy(true);
    try { await plan.undoLoanBatch(batchId); setNotice({ text: t('floan.batch.undone') }); await load(); onChanged(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(false); }
  }
  async function pick(row: FinanceLoanRow, canonicalId: string) {
    await plan.matchDebtPayment(loan.debt_id, { canonical_id: canonicalId }, plan.attemptKey());
    setPicking(null); setNotice({ text: t('floan.pick.done', { number: row.number }) }); await load(); onChanged();
  }
  async function remove() {
    setBusy(true);
    try { await plan.removeLoan(loan.debt_id); setRemoving(false); onChanged(); onBack(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); setRemoving(false); } finally { setBusy(false); }
  }

  const found = Number(suggestion?.params.found ?? 0), ambiguous = Number(suggestion?.params.ambiguous ?? 0), missing = Number(suggestion?.params.missing ?? 0);
  return <div className="floanPage">
    <div className="floanHead">
      <div>
        <h2 dir="auto">{loan.name}</h2>
        <p className="muted">{[loan.terms.lender, t(`floan.repayment.${loan.terms.repayment}` as MessageKey), t(`floan.basis.${tt.basis}` as MessageKey)].filter(Boolean).join(' · ')}</p>
      </div>
      <div className="actions">{back}
        <button type="button" className="secondary" onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('floan.terms.edit')}</button>
        <button type="button" className="secondary" onClick={() => setRemoving(true)}>{t('floan.terms.remove')}</button>
      </div>
    </div>
    <KpiRow label={t('floan.kpis')}>
      <KpiCard icon={<Wallet />} label={t('floan.kpi.paid')} value={amount(tt.paid, c)} hint={t('floan.kpi.paidHint', { paid: tt.paid_count, total: loan.rows.length })} />
      <KpiCard icon={<Layers />} label={t('floan.kpi.interestPaid')} value={tt.interest_paid !== null ? amount(tt.interest_paid, c) : '-'}
        hint={tt.split_known ? t('floan.kpi.interestTotal', { amount: amount(tt.total_interest, c) }) : t('floan.kpi.noRate')} />
      <KpiCard icon={<Landmark />} label={t('floan.kpi.balance')} value={amount(tt.balance, c)} hint={t(tt.split_known ? 'floan.kpi.balanceHint' : 'floan.kpi.balancePlainHint')} />
      <KpiCard icon={<CalendarClock />} label={t('floan.kpi.payoff')} value={tt.payoff_on ? longDate(tt.payoff_on) : '-'}
        hint={tt.status === 'open' ? t(pace.key, { count: pace.count }) : t(`floan.status.${tt.status}` as MessageKey)} tone={tt.months_ahead < 0 ? 'bad' : ''} />
    </KpiRow>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {tt.from_salary && <FinBanner tone="info">{t('floan.fromSalary')}</FinBanner>}
    {tt.never_ends && <FinBanner tone="warn">{t('floan.neverEnds')}</FinBanner>}
    {!tt.split_known && <FinBanner tone="warn">{t('floan.noRate')}</FinBanner>}
    {suggestion && tt.status === 'open' && <FinCard title={found > 0 ? t('floan.found.title', { count: found, name: loan.name }) : t('floan.found.ambiguousTitle', { count: ambiguous })}
      icon={<Search />} subtitle={t(loan.terms.repayment === 'salary_deduction' ? 'floan.found.whySalary' : 'floan.found.whyBank')}>
      <p>{t('floan.found.counts', { found, missing, ambiguous })}</p>
      <ul className="floanMonths" aria-label={t('floan.found.monthsLabel')}>{(suggestion.months ?? []).map((m) => <li key={m.number}>
        <StatusChip tone={m.state === 'found' ? 'ok' : m.state === 'missing' ? 'bad' : 'warn'}>{t(`floan.found.state.${m.state}` as MessageKey, { number: isolate(String(m.number)) })}</StatusChip>
        <small><bdi>{longDate(m.due_on)}</bdi></small>
      </li>)}</ul>
      <div className="actions">
        {found > 0 && <button type="button" disabled={busy} onClick={() => void accept()}><CheckCircle2 aria-hidden="true" />{busy ? t('finance.saving') : t('floan.found.accept', { count: found })}</button>}
        <button type="button" className="secondary" disabled={busy} onClick={() => void dismiss()}>{t('fsug.dismiss')}</button>
      </div>
      <p className="muted finHelp">{t('floan.found.note')}</p>
    </FinCard>}
    <FinCard title={t('floan.schedule.title')} subtitle={t('floan.schedule.sub', { count: loan.rows.length, installment: amount(tt.installment, c) })}>
      {loan.rows.length === 0 ? <Empty icon={<CalendarClock aria-hidden="true" />} title={t('floan.schedule.empty')} text={t('floan.neverEnds')} />
        : <><div className="finTableWrap" tabIndex={0} role="region" aria-label={t('floan.schedule.title')}><table className="finGrid floanTable">
          <thead><tr><th scope="col">#</th><th scope="col">{t('floan.col.date')}</th><th scope="col" className="num">{t('floan.col.payment')}</th>
            <th scope="col" className="num">{t('floan.col.principal')}</th><th scope="col" className="num">{t('floan.col.interest')}</th>
            <th scope="col" className="num">{t('floan.col.balance')}</th><th scope="col">{t('floan.col.status')}</th><th scope="col">{t('floan.col.paidWith')}</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.number}>
            <td>{row.number}</td><td><bdi>{longDate(row.due_on)}</bdi></td>
            <td className="num"><Money value={row.payment} currency={c} /></td>
            <td className="num">{row.principal !== null ? <Money value={row.principal} currency={c} /> : '-'}</td>
            <td className="num">{row.interest !== null ? <Money value={row.interest} currency={c} /> : '-'}</td>
            <td className="num"><Money value={row.balance_after} currency={c} /></td>
            <td><StatusChip tone={rowTone(row.status)}>{t(`floan.row.${row.status}` as MessageKey)}</StatusChip></td>
            <td>{row.payments.length > 0 ? row.payments.map((p, i) => <small key={i} className="floanPaid">
              {t(p.kind === 'salary' ? 'floan.paid.salary' : 'floan.paid.bank', { date: longDate(p.on), amount: amount(p.amount, c) })}</small>)
              : row.status === 'ambiguous' ? <button type="button" className="secondary small" onClick={() => setPicking(row)}>{t('floan.pick.button')}</button>
                : row.found ? <small className="muted">{t('floan.paid.found', { date: longDate(row.found.on) })}</small> : '-'}</td>
          </tr>)}</tbody>
        </table></div>
        {loan.rows.length > PAGE && <nav className="fsugPager" aria-label={t('floan.schedule.pages')}>
          <button type="button" className="secondary" disabled={start === 0} onClick={() => setOffset(Math.max(0, start - PAGE))}>{t('fsug.prev')}</button>
          <span>{t('fsug.page', { from: start + 1, to: Math.min(loan.rows.length, start + PAGE), total: loan.rows.length })}</span>
          <button type="button" className="secondary" disabled={start + PAGE >= loan.rows.length} onClick={() => setOffset(start + PAGE)}>{t('fsug.next')}</button>
        </nav>}</>}
    </FinCard>
    <FinCard title={t('floan.batch.title')} subtitle={t('floan.batch.sub')}>
      {loan.batches.length === 0 ? <p className="muted">{t('floan.batch.empty')}</p>
        : <ul className="fplRows">{loan.batches.map((b) => <li key={b.id} className="fplRow compact">
          <span className="fplRowIcon" aria-hidden="true"><CheckCircle2 /></span>
          <div className="fplRowMain"><strong>{t('floan.batch.row', { count: b.count, amount: b.currency ? amount(b.amount, b.currency) : '-' })}</strong>
            <small>{formatDateTime(b.created_at)}{b.status === 'undone' ? ` · ${t('floan.batch.undoneTag')}` : ''}</small></div>
          {b.status === 'active' && <button type="button" className="secondary" disabled={busy} onClick={() => void undo(b.id)}><Undo2 aria-hidden="true" />{t('floan.batch.undo')}</button>}
        </li>)}</ul>}
    </FinCard>
    {editing && <Modal title={t('floan.terms.title', { name: loan.name })} className="formDialog" onClose={() => setEditing(false)}>
      <LoanTermsForm debt={{ id: loan.debt_id, name: loan.name, currency: c, account_id: loan.terms.account_id, due_day: loan.terms.payment_day }} terms={loan.terms}
        accounts={accounts} onDone={(saved) => { setEditing(false); if (saved) { setNotice({ text: t('floan.terms.saved') }); void load(); onChanged(); } }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('floan.terms.removeTitle', { name: loan.name })} confirmLabel={t('floan.terms.remove')} danger busy={busy}
      onConfirm={() => void remove()} onCancel={() => setRemoving(false)}><p>{t('floan.terms.removeText')}</p></ConfirmDialog>}
    {picking && <Modal title={t('floan.pick.title', { number: picking.number, date: longDate(picking.due_on) })} onClose={() => setPicking(null)}>
      <PickList row={picking} currency={c} onPick={(id) => pick(picking, id)} />
    </Modal>}
  </div>;
}

function PickList({ row, currency, onPick }: { row: FinanceLoanRow; currency: string; onPick: (canonicalId: string) => Promise<void> }) {
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState('');
  return <>
    <p className="muted">{t('floan.pick.help', { amount: amount(row.payment, currency) })}</p>
    <ul className="fplRows">{row.candidates.map((c) => <li key={c.canonical_id} className="fplRow compact">
      <span className="fplRowIcon" aria-hidden="true"><Wallet /></span>
      <div className="fplRowMain"><strong dir="auto">{c.merchant || '-'}</strong><small><bdi>{longDate(c.posted_on)}</bdi> · {amount(c.amount, c.currency)}</small></div>
      <button type="button" disabled={busy !== ''} onClick={() => { setBusy(c.canonical_id); onPick(c.canonical_id).catch((e) => setError(describeError(e))).finally(() => setBusy('')); }}>
        {busy === c.canonical_id ? t('finance.saving') : t('floan.pick.this')}</button>
    </li>)}</ul>
    {error && <Notice error>{error}</Notice>}
  </>;
}

/** The loan's terms: all optional except what a schedule needs (the start date, the amount, the term or the instalment). */
export function LoanTermsForm({ debt, terms, accounts, onDone }: {
  debt: Pick<FinanceDebt, 'id' | 'name' | 'currency' | 'account_id' | 'due_day'>; terms: FinanceLoanView['terms'] | null; accounts: FinanceAccount[]; onDone: (savedDebtId: string | null) => void;
}) {
  const [form, setForm] = useState<LoanForm>(() => loanForm(terms, debt));
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId(), rateHelp = useId(), installmentHelp = useId();
  const set = <K extends keyof LoanForm>(key: K, value: LoanForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const c = debt.currency;
  const usable = accounts.filter((a) => !a.archived && a.currency === c && (a.kind === 'bank' || a.kind === 'cash'));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = loanBody(form, c);
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    try { await plan.saveLoan(debt.id, checked.body); onDone(debt.id); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('floan.form.lender')}</span><input value={form.lender} onChange={(e) => set('lender', e.target.value)} maxLength={120} dir="auto" /></label>
      <label className="field"><span>{t('floan.form.start')}</span><input type="date" value={form.startOn} onChange={(e) => set('startOn', e.target.value)} required /></label>
      <label className="field"><span>{t('floan.form.amount', { currency: c })}</span><input value={form.amount} onChange={(e) => set('amount', e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('floan.form.rateType')}</span><select value={form.rateType} onChange={(e) => set('rateType', e.target.value as LoanForm['rateType'])}>
        <option value="reducing">{t('floan.basis.reducing')}</option><option value="flat">{t('floan.basis.flat')}</option></select></label>
      <label className="field"><span>{t('floan.form.rate')}</span><input value={form.rate} onChange={(e) => set('rate', e.target.value)} inputMode="decimal" dir="ltr" aria-describedby={rateHelp} /></label>
      <label className="field"><span>{t('floan.form.term')}</span><input value={form.termMonths} onChange={(e) => set('termMonths', e.target.value.replace(/\D/g, '').slice(0, 3))} inputMode="numeric" dir="ltr" /></label>
      <label className="field"><span>{t('floan.form.installment', { currency: c })}</span><input value={form.installment} onChange={(e) => set('installment', e.target.value)} inputMode="decimal" dir="ltr" aria-describedby={installmentHelp} /></label>
      <label className="field"><span>{t('floan.form.day')}</span><input value={form.paymentDay} onChange={(e) => set('paymentDay', e.target.value.replace(/\D/g, '').slice(0, 2))} inputMode="numeric" dir="ltr" /></label>
      <label className="field"><span>{t('floan.form.repayment')}</span><select value={form.repayment} onChange={(e) => set('repayment', e.target.value as LoanForm['repayment'])}>
        {(['bank_debit', 'salary_deduction', 'manual'] as const).map((r) => <option key={r} value={r}>{t(`floan.repayment.${r}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('floan.form.account')}</span><select value={form.accountId} onChange={(e) => set('accountId', e.target.value)} required={form.repayment === 'bank_debit'}>
        <option value="">{t('finance.form.noAccount')}</option>{usable.map((a) => <option key={a.id} value={a.id}>{a.name}{a.last4 ? ` (${a.last4})` : ''}</option>)}</select></label>
      <label className="field"><span>{t('floan.form.status')}</span><select value={form.status} onChange={(e) => set('status', e.target.value as LoanForm['status'])}>
        {(['open', 'paid_off', 'closed'] as const).map((s) => <option key={s} value={s}>{t(`floan.status.${s}` as MessageKey)}</option>)}</select></label>
      {form.status !== 'open' && <label className="field"><span>{t('floan.form.closedOn')}</span><input type="date" value={form.closedOn} onChange={(e) => set('closedOn', e.target.value)} required /></label>}
    </div>
    <p id={rateHelp} className="muted finHelp">{t('floan.form.rateHelp')}</p>
    <p id={installmentHelp} className="muted finHelp">{t('floan.form.installmentHelp')}</p>
    {form.repayment === 'bank_debit' && usable.length === 0 && <p className="muted finHelp">{t('floan.form.noAccount', { currency: c })}</p>}
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

/** History: loans paid off or closed, each with its whole record one click away. */
export function FinishedLoansCard({ loans, onOpen }: { loans: FinanceLoanSummary[]; onOpen: (debtId: string) => void }) {
  return <FinCard title={t('floan.finished.title')} icon={<CheckCircle2 />} subtitle={t('floan.finished.sub')}>
    {loans.length === 0 ? <p className="muted">{t('floan.finished.empty')}</p>
      : <ul className="fplRows">{loans.map((l) => <li key={l.debt_id} className="fplRow compact">
        <span className="fplRowIcon" aria-hidden="true"><CheckCircle2 /></span>
        <div className="fplRowMain"><strong dir="auto">{l.name}</strong>
          <small>{t('floan.finished.row', { status: t(`floan.status.${l.totals.status}` as MessageKey), date: l.totals.payoff_on ? longDate(l.totals.payoff_on) : '-',
            paid: amount(l.totals.paid, l.currency) })}</small></div>
        <button type="button" className="secondary" onClick={() => onOpen(l.debt_id)} aria-label={t('floan.row.openLabel', { name: l.name })}>{t('floan.row.openButton')}</button>
      </li>)}</ul>}
  </FinCard>;
}
