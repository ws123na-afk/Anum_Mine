// Finance, Debts and goals (docs/finance.md#debts-and-goals), from the owner's mockup of 5 Nov 2026.
// Every figure comes from the person's own records: debts (balance, yearly rate, minimum payment, due
// day, starting balance), goals, the saved debt plan and the payments matched to a debt. The payoff
// order, dates, what-if and outlook are worked out by lib/financeDebtPlan.ts from those records.
// Nothing here pays a debt: a payment made at the bank is matched to the debt afterwards.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { AlertTriangle, BarChart3, CalendarDays, Car, CreditCard, Flag, Home, Landmark, Layers, Pause, Pencil, Play, Plus, Target, Wand2 } from 'lucide-react';
import type { FinanceAccount, FinanceDebt, FinanceDebtKind, FinanceDebtPaymentPage, FinanceDebtPlan, FinanceDebtStrategy, FinanceGoal } from '@anum/contracts';
import * as api from './lib/financeApi';
import * as plan from './lib/financePlanningApi';
import { formatAmount, isolate, minorDigits, monthLabel, parseAmount, rangeLabel, type FinanceTab } from './lib/finance';
import {
  daysBetween, debtProgress, fromMinor, goalMonthly, goalPercent, nextDueOn, outlook, payoffMonth, payoffOrder, planCurrencies, planDebts,
  simulatePayoff, toMinor, totalProgress, whatIf,
} from './lib/financeDebtPlan';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { DebtForm, GoalForm } from './FinancePlanningForms';
import { OutlookChart } from './FinancePlanningCharts';
import { MatchDialog, PersonalScope, Progress, RowMenu } from './FinancePlanningParts';
import './financePlanning.css';

type Section = 'debts' | 'goals' | 'plan' | 'history';
const SECTIONS: readonly Section[] = ['debts', 'goals', 'plan', 'history'];
const HISTORY_PAGE = 10;
const DEBT_ICON: Record<FinanceDebtKind, typeof CreditCard> = { card: CreditCard, loan: Car, mortgage: Home, other: Landmark };

const shortDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' });
const longDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const money = (minor: number, currency: string) => formatAmount(fromMinor(minor, currency), currency);

export function DebtsGoalsPage({ initial, accounts, base, today, onOpen, onChanged }: {
  initial: 'debts' | 'goals'; accounts: FinanceAccount[]; base: string; today: string; onOpen: (tab: FinanceTab) => void; onChanged: () => void;
}) {
  const [section, setSection] = useState<Section>(initial);
  const [debts, setDebts] = useState<Load<FinanceDebt[]>>({ kind: 'loading' });
  const [goals, setGoals] = useState<Load<FinanceGoal[]>>({ kind: 'loading' });
  const [plans, setPlans] = useState<Load<FinanceDebtPlan[]>>({ kind: 'loading' });
  const [history, setHistory] = useState<Load<FinanceDebtPaymentPage>>({ kind: 'loading' });
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [wizard, setWizard] = useState(false);
  const [editingDebt, setEditingDebt] = useState<FinanceDebt | 'new' | null>(null), [editingGoal, setEditingGoal] = useState<FinanceGoal | 'new' | null>(null);
  const [removing, setRemoving] = useState<{ kind: 'debt' | 'goal'; id: string; name: string } | null>(null), [busy, setBusy] = useState(false);
  const [matching, setMatching] = useState<FinanceDebt | null>(null);

  const loadDebts = useCallback(async () => { try { setDebts({ kind: 'ready', value: await api.listDebts() }); } catch (e) { setDebts(failed(e)); } }, []);
  const loadGoals = useCallback(async () => { try { setGoals({ kind: 'ready', value: await api.listGoals() }); } catch (e) { setGoals(failed(e)); } }, []);
  const loadPlans = useCallback(async () => { try { setPlans({ kind: 'ready', value: (await plan.getDebtPlan()).plans }); } catch (e) { setPlans(failed(e)); } }, []);
  const loadHistory = useCallback(async () => {
    try { setHistory({ kind: 'ready', value: await plan.debtPayments(HISTORY_PAGE, offset) }); } catch (e) { setHistory(failed(e)); }
  }, [offset]);
  useEffect(() => { void loadDebts(); void loadGoals(); void loadPlans(); }, [loadDebts, loadGoals, loadPlans]);
  useEffect(() => { void loadHistory(); }, [loadHistory]);

  const debtList = debts.kind === 'ready' ? debts.value : [];
  const goalList = goals.kind === 'ready' ? goals.value : [];
  const currencies = planCurrencies(debtList, goalList, base);
  const currency = picked && currencies.includes(picked) ? picked : currencies[0] ?? base;
  const myDebts = debtList.filter((d) => d.currency === currency), myGoals = goalList.filter((g) => g.currency === currency);
  const savedPlan = plans.kind === 'ready' ? plans.value.find((p) => p.currency === currency) ?? null : null;
  const strategy: FinanceDebtStrategy = savedPlan?.strategy ?? 'avalanche';
  const payment = savedPlan?.monthly_payment ? toMinor(savedPlan.monthly_payment, currency) : null;
  const pd = useMemo(() => planDebts(debtList, currency), [debtList, currency]);

  async function savePlan(next: FinanceDebtPlan) {
    const others = plans.kind === 'ready' ? plans.value.filter((p) => p.currency !== next.currency) : [];
    const saved = await plan.saveDebtPlan([...others, next]);
    setPlans({ kind: 'ready', value: saved.plans });
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try {
      if (removing.kind === 'debt') { await api.deleteDebt(removing.id); await loadDebts(); } else { await api.deleteGoal(removing.id); await loadGoals(); }
      setNotice({ text: t('finance.item.deleted', { name: removing.name }) }); onChanged();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(false); setRemoving(null); }
  }
  async function togglePause(goal: FinanceGoal) {
    try {
      await api.updateGoal(goal.id, { name: goal.name, target_amount: goal.target_amount, saved_amount: goal.saved_amount, currency: goal.currency, target_on: goal.target_on,
        account_id: goal.account_id, monthly_contribution: goal.monthly_contribution, notes: goal.notes, paused: !goal.paused });
      setNotice({ text: goal.paused ? t('fplan.goal.resumed', { name: goal.name }) : t('fplan.goal.pausedNow', { name: goal.name }) });
      await loadGoals(); onChanged();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }

  const head = <FinancePageHead title={t('fplan.debts.title')} subtitle={t('fplan.debts.subtitle')}
    actions={<button type="button" onClick={() => setWizard(true)} disabled={debts.kind !== 'ready'}><Wand2 aria-hidden="true" />{t('fplan.wizard.open')}</button>}
    controls={<>
      <PersonalScope />
      {currencies.length > 0 && <label className="finControl"><span className="finControlLabel">{t('fplan.currency')}</span>
        <select value={currency} onChange={(e) => setPicked(e.target.value)} disabled={currencies.length < 2}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    </>}
    tabs={<FinanceTabs label={t('fplan.debts.tabs')} idPrefix="fdg" value={section} onChange={setSection}
      items={SECTIONS.map((id) => ({ id, label: t(`fplan.debts.tab.${id}` as MessageKey) }))} />} />;

  if (debts.kind === 'denied' || goals.kind === 'denied') return <div className="fplPage">{head}<Denied /></div>;
  const loadError = debts.kind === 'error' ? debts.message : goals.kind === 'error' ? goals.message : null;
  if (loadError) return <div className="fplPage">{head}<LoadError message={t('fplan.error.load', { message: loadError })} retry={() => { void loadDebts(); void loadGoals(); }} /></div>;
  if (debts.kind === 'loading' || goals.kind === 'loading') return <div className="fplPage">{head}<Notice busy>{t('fplan.loading')}</Notice></div>;

  const totalDebt = pd.reduce((s, d) => s + d.balance, 0);
  const minimums = pd.reduce((s, d) => s + d.minimum, 0);
  const saved = myGoals.reduce((s, g) => s + toMinor(g.saved_amount, currency), 0), target = myGoals.reduce((s, g) => s + toMinor(g.target_amount, currency), 0);
  const goalShare = target > 0 ? Math.floor((saved * 100) / target) : 0;
  const now = simulatePayoff(pd, strategy, null), planned = simulatePayoff(pd, strategy, payment);
  const startMonth = today.slice(0, 7);
  const progress = totalProgress(myDebts);

  const debtsCard = <DebtsCard debts={myDebts} accounts={accounts} today={today} onAdd={() => setEditingDebt('new')} onEdit={setEditingDebt}
    onRemove={(d) => setRemoving({ kind: 'debt', id: d.id, name: d.name })} onMatch={setMatching} />;
  const strategyCard = pd.length > 0 && <StrategyCard debts={pd} currency={currency} strategy={strategy} payment={payment} minimums={minimums} now={now} planned={planned} startMonth={startMonth}
    progress={progress} onSave={async (next) => { await savePlan(next); setNotice({ text: t('fplan.plan.saved') }); }} />;
  const goalsCard = <GoalsCard goals={myGoals} today={today} onAdd={() => setEditingGoal('new')} onEdit={setEditingGoal} onPause={(g) => void togglePause(g)}
    onRemove={(g) => setRemoving({ kind: 'goal', id: g.id, name: g.name })} />;
  const outlookCard = (pd.length > 0 || myGoals.length > 0) && <FinCard title={t('fplan.outlook.title')} icon={<BarChart3 />} subtitle={t('fplan.outlook.sub', { currency })}>
    <OutlookChart points={outlook(pd, strategy, payment, myGoals, currency, today, 12)} currency={currency} digits={minorDigits(currency)} />
  </FinCard>;
  const whatIfCard = pd.length > 0 && <WhatIfCard currency={currency} strategy={strategy} payment={payment} minimums={planned.payment} debts={pd} />;
  const historyCard = (full: boolean) => <HistoryCard state={history} accounts={accounts} full={full} onPage={setOffset} retry={() => void loadHistory()} onAll={() => setSection('history')}
    onRemoved={async (row) => {
      try { await plan.unmatchDebtPayment(row.debt_id, row.link_id); setNotice({ text: t('fplan.history.removed') }); await loadHistory(); }
      catch (e) { setNotice({ error: true, text: describeError(e) }); }
    }} />;

  return <div className="fplPage">
    {head}
    <KpiRow label={t('fplan.debts.kpis')}>
      <KpiCard icon={<Layers />} label={t('fplan.kpi.totalDebt')} value={money(totalDebt, currency)} hint={pd.length ? t('fplan.kpi.debtCount', { count: pd.length }) : t('fplan.kpi.noDebts')} />
      <KpiCard icon={<CalendarDays />} label={t('fplan.kpi.monthly')} value={money(planned.payment, currency)} hint={payment ? t('fplan.kpi.planned') : t('fplan.kpi.minimums')} />
      <KpiCard icon={<Target />} label={t('fplan.kpi.saved')} value={money(saved, currency)} hint={myGoals.length ? t('fplan.kpi.goalCount', { count: myGoals.length }) : t('fplan.kpi.noGoals')} />
      <KpiCard icon={<BarChart3 />} label={t('fplan.kpi.goalProgress')} value={target > 0 ? `${goalShare}%` : '-'}
        hint={target > 0 ? <Progress percent={goalShare} label={t('fplan.kpi.goalProgressAria', { percent: goalShare })} /> : t('fplan.kpi.noGoals')} />
    </KpiRow>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <div {...tabPanel('fdg', section)}>
      {section === 'debts' && <div className="fplColumns">
        <div className="fplColumn">
          <FinBanner tone="warn" icon={<AlertTriangle />}>{t('fplan.estimates')}</FinBanner>
          {debtsCard}{strategyCard}{historyCard(false)}{outlookCard}
        </div>
        <div className="fplColumn">{goalsCard}{whatIfCard}<CommitmentsCard debts={myDebts} goals={myGoals} today={today} currency={currency} /></div>
      </div>}
      {section === 'goals' && <div className="fplColumns">
        <div className="fplColumn">{goalsCard}{outlookCard}</div>
        <div className="fplColumn"><CommitmentsCard debts={myDebts} goals={myGoals} today={today} currency={currency} /></div>
      </div>}
      {section === 'plan' && <>
        <FinBanner tone="warn" icon={<AlertTriangle />}>{t('fplan.estimates')}</FinBanner>
        {pd.length === 0 ? <FinCard title={t('fplan.plan.title')}><Empty icon={<Landmark aria-hidden="true" />} title={t('fplan.debts.empty')} text={t('fplan.debts.emptyText')}
          action={<button type="button" onClick={() => setEditingDebt('new')}><Plus aria-hidden="true" />{t('finance.debts.add')}</button>} /></FinCard>
          : <div className="fplColumns"><div className="fplColumn">{strategyCard}<OrderCard currency={currency} strategy={strategy} payment={payment} debts={pd} startMonth={startMonth} /></div>
            <div className="fplColumn">{whatIfCard}{outlookCard}</div></div>}
      </>}
      {section === 'history' && historyCard(true)}
    </div>
    {wizard && <PlanWizard currency={currency} debts={pd} saved={savedPlan} startMonth={startMonth} onAddDebt={() => { setWizard(false); setEditingDebt('new'); }}
      onClose={async (next) => { setWizard(false); if (next) { try { await savePlan(next); setNotice({ text: t('fplan.plan.saved') }); } catch (e) { setNotice({ error: true, text: describeError(e) }); } } }} />}
    {editingDebt && <Modal title={editingDebt === 'new' ? t('finance.debts.add') : t('finance.item.editTitle', { name: editingDebt.name })} className="formDialog" onClose={() => setEditingDebt(null)}>
      <DebtForm debt={editingDebt === 'new' ? null : editingDebt} base={currency} accounts={accounts}
        onDone={(text) => { setEditingDebt(null); if (text) { setNotice({ text }); void loadDebts(); onChanged(); } }} />
    </Modal>}
    {editingGoal && <Modal title={editingGoal === 'new' ? t('finance.goals.add') : t('finance.item.editTitle', { name: editingGoal.name })} className="formDialog" onClose={() => setEditingGoal(null)}>
      <GoalForm goal={editingGoal === 'new' ? null : editingGoal} base={currency} onDone={(text) => { setEditingGoal(null); if (text) { setNotice({ text }); void loadGoals(); onChanged(); } }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('finance.item.deleteTitle', { name: removing.name })} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p>{t('finance.item.deleteText')}</p>
    </ConfirmDialog>}
    {matching && <MatchDialog title={t('fplan.match.debtTitle', { name: matching.name })} kind="debt" around={nextDueOn(matching.due_day, today) ?? today} amount={matching.minimum_payment}
      currency={matching.currency} accounts={accounts} onOpenTransactions={() => onOpen('transactions')}
      onMatch={async (candidate, key) => { await plan.matchDebtPayment(matching.id, { canonical_id: candidate.canonical_id }, key); }}
      onClose={(matched) => { setMatching(null); if (matched) { setNotice({ text: t('fplan.match.done') }); void loadHistory(); } }} />}
  </div>;
}

// ---- Your debts --------------------------------------------------------------------------------

function DebtsCard({ debts, accounts, today, onAdd, onEdit, onRemove, onMatch }: {
  debts: FinanceDebt[]; accounts: FinanceAccount[]; today: string; onAdd: () => void; onEdit: (d: FinanceDebt) => void; onRemove: (d: FinanceDebt) => void; onMatch: (d: FinanceDebt) => void;
}) {
  const names = new Map(accounts.map((a) => [a.id, a]));
  return <FinCard title={t('fplan.debts.yours')} actions={<button type="button" className="secondary" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.debts.add')}</button>}>
    {debts.length === 0 ? <Empty icon={<Landmark aria-hidden="true" />} title={t('fplan.debts.empty')} text={t('fplan.debts.emptyText')}
      action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.debts.add')}</button>} />
      : <ul className="fplRows">{debts.map((d) => {
        const Icon = DEBT_ICON[d.kind];
        const account = d.account_id ? names.get(d.account_id) : undefined;
        const due = nextDueOn(d.due_day, today);
        const days = due ? daysBetween(today, due) : null;
        return <li key={d.id} className="fplRow">
          <span className="fplRowIcon" aria-hidden="true"><Icon /></span>
          <div className="fplRowMain">
            <strong dir="auto">{d.name}{account?.last4 ? <span className="muted"> · {t('finance.accounts.endingIn', { last4: account.last4 })}</span> : null}</strong>
            <small>{t('fplan.debt.balance', { amount: isolate(formatAmount(d.balance, d.currency)) })} · {t('fplan.debt.rate', { rate: Number(d.apr_percent).toString() })}</small>
          </div>
          <div className="fplRowFigure"><span>{d.kind === 'loan' || d.kind === 'mortgage' ? t('fplan.debt.installment') : t('fplan.debt.minimum')}</span><Money value={d.minimum_payment} currency={d.currency} /></div>
          {due ? <StatusChip tone={days !== null && days <= 7 ? 'warn' : 'muted'}>{t('fplan.due', { date: shortDate(due) })}</StatusChip> : <StatusChip>{t('fplan.debt.noDueDay')}</StatusChip>}
          <RowMenu label={t('fplan.menu', { name: d.name })} items={[
            { label: t('fplan.debt.matchPayment'), onSelect: () => onMatch(d) },
            { label: t('finance.item.edit', { name: d.name }), onSelect: () => onEdit(d) },
            { label: t('finance.item.delete', { name: d.name }), onSelect: () => onRemove(d), danger: true },
          ]} />
        </li>;
      })}</ul>}
  </FinCard>;
}

// ---- Payoff strategy and the planned payment ----------------------------------------------------

function StrategyCard({ debts, currency, strategy, payment, minimums, now, planned, startMonth, progress, onSave }: {
  debts: ReturnType<typeof planDebts>; currency: string; strategy: FinanceDebtStrategy; payment: number | null; minimums: number; now: ReturnType<typeof simulatePayoff>; planned: ReturnType<typeof simulatePayoff>;
  startMonth: string; progress: ReturnType<typeof totalProgress>; onSave: (next: FinanceDebtPlan) => Promise<void>;
}) {
  const [draft, setDraft] = useState(payment ? fromMinor(payment, currency) : ''), [preview, setPreview] = useState<ReturnType<typeof simulatePayoff> | null>(null);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const name = useId(), errorId = useId(), helpId = useId();
  useEffect(() => { setDraft(payment ? fromMinor(payment, currency) : ''); setPreview(null); }, [payment, currency]);
  const read = (): number | null | false => {
    if (!draft.trim()) return null;
    const checked = parseAmount(draft, currency, { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return false; }
    return toMinor(checked.value, currency);
  };
  async function save(next: Partial<FinanceDebtPlan>) {
    setBusy(true); setError(null);
    try { await onSave({ currency, strategy, monthly_payment: payment ? fromMinor(payment, currency) : null, ...next }); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  function previewPlan(event: FormEvent) {
    event.preventDefault();
    const value = read();
    if (value === false) return;
    setError(null);
    setPreview(simulatePayoff(debts, strategy, value));
  }
  const shownPlan = preview ?? planned;
  const nowDate = payoffMonth(startMonth, now.months), planDate = payoffMonth(startMonth, shownPlan.months);
  return <FinCard title={t('fplan.strategy.title')}>
    <div className="fplStrategy">
      <fieldset className="fplRadios" disabled={busy}>
        <legend>{t('fplan.strategy.legend')}</legend>
        {(['avalanche', 'snowball'] as const).map((s) => <label key={s}><input type="radio" name={name} checked={strategy === s} onChange={() => void save({ strategy: s })} />
          <span><strong>{t(`fplan.strategy.${s}` as MessageKey)}</strong> {t(`fplan.strategy.${s}Hint` as MessageKey)}</span></label>)}
      </fieldset>
      <div className="fplPayoff">
        <h3>{t('fplan.progress.title')}</h3>
        {progress ? <>
          <p className="fplFrom" dir="ltr">{money(progress.start, currency)} → {money(0, currency)}</p>
          <div className="fplBarRow"><Progress percent={progress.percent} label={t('fplan.progress.aria', { percent: progress.percent })} /><strong>{progress.percent}%</strong></div>
        </> : <p className="muted">{t('fplan.progress.none')}</p>}
        <p className="fplDates">
          <span>{t('fplan.progress.current')} <strong>{nowDate ? monthLabel(nowDate) : t('fplan.never')}</strong></span>
          <StatusChip tone="ok">{t('fplan.progress.projected')} {planDate ? monthLabel(planDate) : t('fplan.never')}</StatusChip>
        </p>
        {now.months === null && <p className="muted">{t('fplan.progress.neverText')}</p>}
      </div>
    </div>
    <PlannedPayment currency={currency} minimums={minimums} draft={draft} setDraft={setDraft} errorId={errorId} helpId={helpId} error={error} busy={busy}
      onPreview={previewPlan} onSave={() => { const v = read(); if (v !== false) void save({ monthly_payment: v === null ? null : fromMinor(v, currency) }); }}
      preview={preview} planned={planned} />
  </FinCard>;
}

function PlannedPayment({ currency, minimums, draft, setDraft, errorId, helpId, error, busy, onPreview, onSave, preview, planned }: {
  currency: string; minimums: number; draft: string; setDraft: (v: string) => void; errorId: string; helpId: string; error: string | null; busy: boolean;
  onPreview: (e: FormEvent) => void; onSave: () => void; preview: ReturnType<typeof simulatePayoff> | null; planned: ReturnType<typeof simulatePayoff>;
}) {
  return <form className="fplPayment" onSubmit={onPreview} aria-describedby={error ? errorId : helpId}>
    <label className="field"><span>{t('fplan.payment.label', { currency })}</span>
      <input value={draft} onChange={(e) => setDraft(e.target.value)} inputMode="decimal" dir="ltr" placeholder={fromMinor(minimums, currency)} /></label>
    <div className="fplPaymentActions">
      <button type="submit" className="secondary" disabled={busy}>{t('fplan.payment.preview')}</button>
      <button type="button" disabled={busy} onClick={onSave}>{busy ? t('finance.saving') : t('fplan.payment.save')}</button>
    </div>
    <p id={helpId} className="muted finHelp">{t('fplan.payment.help', { amount: money(minimums, currency) })}</p>
    {preview && preview.belowMinimums && <Notice error>{t('fplan.payment.below', { amount: money(minimums, currency) })}</Notice>}
    {preview && !preview.belowMinimums && <Notice>{t('fplan.payment.previewText', { months: preview.months ?? '-', interest: money(preview.interest, currency), was: planned.months ?? '-' })}</Notice>}
    {error && <p id={errorId} className="cmFieldError" role="alert">{error}</p>}
  </form>;
}

// ---- Plan tab: the order the debts are paid off -------------------------------------------------

function OrderCard({ currency, strategy, payment, debts, startMonth }: { currency: string; strategy: FinanceDebtStrategy; payment: number | null; debts: ReturnType<typeof planDebts>; startMonth: string }) {
  const result = simulatePayoff(debts, strategy, payment);
  return <FinCard title={t('fplan.order.title')} subtitle={t(`fplan.strategy.${strategy}` as MessageKey)}>
    <div className="finTableWrap" tabIndex={0}><table className="finGrid">
      <thead><tr><th scope="col">#</th><th scope="col">{t('fplan.col.debt')}</th><th scope="col" className="num">{t('fplan.col.rate')}</th><th scope="col" className="num">{t('fplan.col.balance')}</th><th scope="col">{t('fplan.col.paidOff')}</th></tr></thead>
      <tbody>{payoffOrder(debts, strategy).map((d, i) => {
        const month = payoffMonth(startMonth, result.paidOffIn[d.id] ?? null);
        return <tr key={d.id}><td>{i + 1}</td><td dir="auto">{d.name}</td><td className="num" dir="ltr">{d.aprPercent}%</td><td className="num" dir="ltr">{money(d.balance, currency)}</td><td>{month ? monthLabel(month) : t('fplan.never')}</td></tr>;
      })}</tbody>
    </table></div>
    <p className="muted">{t('fplan.order.interest', { amount: money(result.interest, currency) })}</p>
  </FinCard>;
}

// ---- What if? -----------------------------------------------------------------------------------

function WhatIfCard({ currency, strategy, payment, minimums, debts }: { currency: string; strategy: FinanceDebtStrategy; payment: number | null; minimums: number; debts: ReturnType<typeof planDebts> }) {
  // The first suggestion is a tenth of what is paid now, rounded to a whole 50: worked out from the plan, never a fixed figure.
  const unit = 50 * 10 ** minorDigits(currency);
  const suggested = Math.max(unit, Math.round(minimums / 10 / unit) * unit);
  const [extra, setExtra] = useState(fromMinor(suggested, currency));
  const value = toMinor(extra, currency);
  const result = value > 0 ? whatIf(debts, strategy, payment, value) : null;
  const inputId = useId();
  return <section className="fplWhatIf" aria-labelledby={`${inputId}-h`}>
    <BarChart3 aria-hidden="true" />
    <div>
      <h2 id={`${inputId}-h`}>{t('fplan.whatIf.title')}</h2>
      <label className="fplWhatIfInput" htmlFor={inputId}>{t('fplan.whatIf.label', { currency })}</label>
      <input id={inputId} value={extra} onChange={(e) => setExtra(e.target.value)} inputMode="decimal" dir="ltr" />
      <p aria-live="polite">{!result ? t('fplan.whatIf.enter')
        : result.monthsSaved === null ? t('fplan.whatIf.never')
          : t('fplan.whatIf.result', { amount: isolate(money(value, currency)), months: result.monthsSaved, interest: isolate(money(result.interestSaved, currency)) })}</p>
      <small className="muted">{t('fplan.whatIf.note')}</small>
    </div>
  </section>;
}

// ---- Goals --------------------------------------------------------------------------------------

function GoalsCard({ goals, today, onAdd, onEdit, onPause, onRemove }: {
  goals: FinanceGoal[]; today: string; onAdd: () => void; onEdit: (g: FinanceGoal) => void; onPause: (g: FinanceGoal) => void; onRemove: (g: FinanceGoal) => void;
}) {
  return <FinCard title={t('fplan.goals.yours')} actions={<button type="button" className="secondary" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.goals.add')}</button>}>
    {goals.length === 0 ? <Empty icon={<Flag aria-hidden="true" />} title={t('finance.goals.empty')} text={t('finance.goals.emptyText')}
      action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.goals.add')}</button>} />
      : <ul className="fplGoals">{goals.map((g) => {
        const percent = goalPercent(g), monthly = goalMonthly(g, today);
        return <li key={g.id} className={g.paused ? 'paused' : undefined}>
          <div className="fplGoalTop">
            <span className="fplRowIcon" aria-hidden="true"><Target /></span>
            <div className="fplRowMain"><strong dir="auto">{g.name}{g.paused && <StatusChip>{t('fplan.goal.paused')}</StatusChip>}</strong>
              <small dir="ltr">{formatAmount(g.saved_amount, g.currency)} / {formatAmount(g.target_amount, g.currency)}</small></div>
            <strong className="fplPercent">{percent}%</strong>
            <Progress percent={percent} label={t('finance.goals.progressLabel', { name: g.name, percent })} />
            <RowMenu label={t('fplan.menu', { name: g.name })} items={[{ label: t('finance.item.delete', { name: g.name }), onSelect: () => onRemove(g), danger: true }]} />
          </div>
          <div className="fplGoalMeta">
            <span><CalendarDays aria-hidden="true" />{t('fplan.goal.monthly')} <strong>{monthly === null ? t('fplan.goal.noPlan') : formatAmount(fromMinor(monthly, g.currency), g.currency)}</strong></span>
            <span><Flag aria-hidden="true" />{t('fplan.goal.due')} <strong>{g.target_on ? longDate(g.target_on) : '-'}</strong></span>
            <span className="fplGoalButtons">
              <button type="button" className="secondary small" onClick={() => onEdit(g)} aria-label={t('finance.item.edit', { name: g.name })}><Pencil aria-hidden="true" />{t('fplan.edit')}</button>
              <button type="button" className="secondary small" onClick={() => onPause(g)} aria-label={g.paused ? t('fplan.goal.resumeAria', { name: g.name }) : t('fplan.goal.pauseAria', { name: g.name })}>
                {g.paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}{g.paused ? t('fplan.goal.resume') : t('fplan.goal.pause')}</button>
            </span>
          </div>
        </li>;
      })}</ul>}
  </FinCard>;
}

// ---- Upcoming commitments -----------------------------------------------------------------------

function CommitmentsCard({ debts, goals, today, currency }: { debts: FinanceDebt[]; goals: FinanceGoal[]; today: string; currency: string }) {
  const rows: { key: string; icon: typeof CreditCard; name: string; kind: string; amount: number; due: string | null }[] = [];
  for (const d of debts) {
    const due = nextDueOn(d.due_day, today), amount = Math.min(toMinor(d.minimum_payment, d.currency), toMinor(d.balance, d.currency));
    if (amount > 0) rows.push({ key: d.id, icon: DEBT_ICON[d.kind], name: d.name, kind: d.kind === 'loan' || d.kind === 'mortgage' ? t('fplan.debt.installment') : t('fplan.debt.minimum'), amount, due });
  }
  const goalTotal = goals.reduce((s, g) => s + (goalMonthly(g, today) ?? 0), 0);
  if (goalTotal > 0) {
    const [y, m] = today.split('-').map(Number);
    const end = `${today.slice(0, 7)}-${String(new Date(Date.UTC(y, m, 0)).getUTCDate()).padStart(2, '0')}`;
    rows.push({ key: 'goals', icon: Target, name: t('fplan.commit.goals'), kind: t('fplan.commit.goalsKind'), amount: goalTotal, due: end });
  }
  rows.sort((a, b) => (a.due ?? '9999').localeCompare(b.due ?? '9999'));
  return <FinCard title={t('fplan.commit.title')}>
    {rows.length === 0 ? <p className="muted">{t('fplan.commit.none')}</p> : <ul className="fplRows">{rows.map((r) => {
      const Icon = r.icon;
      return <li key={r.key} className="fplRow compact">
        <span className="fplRowIcon" aria-hidden="true"><Icon /></span>
        <div className="fplRowMain"><strong dir="auto">{r.name}</strong><small>{r.kind}</small></div>
        <strong className="fplAmount" dir="ltr">{money(r.amount, currency)}</strong>
        {r.due ? <StatusChip tone={daysBetween(today, r.due) <= 7 ? 'warn' : 'muted'}>{t('fplan.due', { date: shortDate(r.due) })}</StatusChip> : <StatusChip>{t('fplan.debt.noDueDay')}</StatusChip>}
      </li>;
    })}</ul>}
  </FinCard>;
}

// ---- Debt payment history -----------------------------------------------------------------------

function HistoryCard({ state, accounts, full, onPage, retry, onAll, onRemoved }: {
  state: Load<FinanceDebtPaymentPage>; accounts: FinanceAccount[]; full: boolean; onPage: (offset: number) => void; retry: () => void; onAll: () => void;
  onRemoved: (row: FinanceDebtPaymentPage['items'][number]) => Promise<void>;
}) {
  const names = new Map(accounts.map((a) => [a.id, a.last4 ? `${a.name} (${a.last4})` : a.name]));
  const page = state.kind === 'ready' ? state.value : null;
  const rows = page ? (full ? page.items : page.items.slice(0, 3)) : [];
  return <FinCard title={t('fplan.history.title')} actions={!full && page && page.total > 3 ? <button type="button" className="cmLinkButton" onClick={onAll}>{t('fplan.history.all')}</button> : undefined}>
    {state.kind === 'loading' && <Notice busy>{t('fplan.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={state.message} retry={retry} />}
    {page && (page.total === 0 ? <p className="muted">{t('fplan.history.empty')}</p> : <>
      <div className="finTableWrap" tabIndex={0}><table className="finGrid">
        <thead><tr><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('fplan.col.debt')}</th><th scope="col">{t('finance.col.account')}</th><th scope="col" className="num">{t('fplan.col.paid')}</th><th scope="col">{t('fplan.col.status')}</th>{full && <th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th>}</tr></thead>
        <tbody>{rows.map((row) => <tr key={row.link_id}>
          <td><bdi dir="ltr">{row.posted_on ?? '-'}</bdi></td>
          <td dir="auto">{row.debt_name ?? '-'}</td>
          <td dir="auto">{row.account_id ? names.get(row.account_id) ?? '-' : '-'}</td>
          <td className="num"><Money value={row.amount} currency={row.currency} /></td>
          <td>{row.status === 'active' ? <StatusChip tone="ok">{t('fplan.history.matched')}</StatusChip> : <StatusChip tone="warn">{t('fplan.history.review')}</StatusChip>}</td>
          {full && <td><button type="button" className="secondary small" onClick={() => void onRemoved(row)}>{t('fplan.history.remove')}</button></td>}
        </tr>)}</tbody>
      </table></div>
      {full && page.total > page.limit && <nav className="cmPager" aria-label={t('fplan.history.pages')}>
        <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}>{t('finance.page.previous')}</button>
        <span className="muted" aria-live="polite">{rangeLabel(page.offset, page.items.length, page.total)}</span>
        <button type="button" className="secondary" disabled={page.offset + page.items.length >= page.total} onClick={() => onPage(page.offset + page.limit)}>{t('finance.page.next')}</button>
      </nav>}
    </>)}
    <p className="muted finHelp">{t('fplan.history.note')}</p>
  </FinCard>;
}

// ---- New plan wizard ----------------------------------------------------------------------------

function PlanWizard({ currency, debts, saved, startMonth, onAddDebt, onClose }: {
  currency: string; debts: ReturnType<typeof planDebts>; saved: FinanceDebtPlan | null; startMonth: string; onAddDebt: () => void; onClose: (next: FinanceDebtPlan | null) => void;
}) {
  const [step, setStep] = useState(0);
  const [strategy, setStrategy] = useState<FinanceDebtStrategy>(saved?.strategy ?? 'avalanche');
  const minimums = debts.reduce((s, d) => s + d.minimum, 0);
  const [amount, setAmount] = useState(saved?.monthly_payment ?? fromMinor(minimums, currency));
  const [error, setError] = useState<string | null>(null);
  const radio = useId();
  const value = toMinor(amount, currency);
  const result = simulatePayoff(debts, strategy, value > 0 ? value : null);
  const compare = (s: FinanceDebtStrategy) => simulatePayoff(debts, s, value > 0 ? value : null);
  const steps = [t('fplan.wizard.step1'), t('fplan.wizard.step2'), t('fplan.wizard.step3')];
  function finish() {
    const checked = parseAmount(amount, currency, { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return; }
    onClose({ currency, strategy, monthly_payment: checked.value });
  }
  return <Modal title={t('fplan.wizard.title', { currency })} className="formDialog fplWizard" onClose={() => onClose(null)}>
    <ol className="fplSteps">{steps.map((label, i) => <li key={label} aria-current={i === step ? 'step' : undefined} className={i < step ? 'done' : undefined}>{label}</li>)}</ol>
    {step === 0 && (debts.length === 0
      ? <Empty icon={<Landmark aria-hidden="true" />} title={t('fplan.debts.empty')} text={t('fplan.wizard.noDebts')} action={<button type="button" onClick={onAddDebt}><Plus aria-hidden="true" />{t('finance.debts.add')}</button>} />
      : <><p>{t('fplan.wizard.reviewText')}</p><ul className="cmList">{debts.map((d) => <li key={d.id}><div><strong dir="auto">{d.name}</strong>
        <small>{t('fplan.debt.rate', { rate: String(d.aprPercent) })} · {t('fplan.debt.minimumIs', { amount: isolate(money(d.minimum, currency)) })}</small></div><strong dir="ltr">{money(d.balance, currency)}</strong></li>)}</ul></>)}
    {step === 1 && <fieldset className="fplRadios"><legend>{t('fplan.strategy.legend')}</legend>
      {(['avalanche', 'snowball'] as const).map((s) => {
        const r = compare(s);
        return <label key={s}><input type="radio" name={radio} checked={strategy === s} onChange={() => setStrategy(s)} />
          <span><strong>{t(`fplan.strategy.${s}` as MessageKey)}</strong> {t(`fplan.strategy.${s}Hint` as MessageKey)}<br />
            <small className="muted">{r.months === null ? t('fplan.never') : t('fplan.wizard.compare', { months: r.months, interest: money(r.interest, currency) })}</small></span></label>;
      })}</fieldset>}
    {step === 2 && <div className="finForm">
      <label className="field"><span>{t('fplan.payment.label', { currency })}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <p className="muted finHelp">{t('fplan.payment.help', { amount: money(minimums, currency) })}</p>
      {result.belowMinimums ? <Notice error>{t('fplan.payment.below', { amount: money(minimums, currency) })}</Notice>
        : <Notice>{result.months === null ? t('fplan.progress.neverText') : t('fplan.wizard.result', { date: monthLabel(payoffMonth(startMonth, result.months)!), interest: money(result.interest, currency) })}</Notice>}
      {error && <p className="cmFieldError" role="alert">{error}</p>}
    </div>}
    <div className="actions">
      {step > 0 && <button type="button" className="secondary" onClick={() => setStep(step - 1)}>{t('fplan.wizard.back')}</button>}
      {step < 2 && <button type="button" disabled={debts.length === 0} onClick={() => setStep(step + 1)}>{t('fplan.wizard.next')}</button>}
      {step === 2 && <button type="button" disabled={result.belowMinimums} onClick={finish}>{t('fplan.wizard.save')}</button>}
      <button type="button" className="secondary" onClick={() => onClose(null)}>{t('common.cancel')}</button>
    </div>
  </Modal>;
}

