// Bill, debt and goal forms (docs/finance.md#bills-and-budgets, #debts-and-goals), shared by the Bills and
// budgets and the Debts and goals pages. Each sends the whole record; fields a form does not show (a goal
// paused elsewhere) are left out, so the API keeps them.
import { useId, useState, type FormEvent } from 'react';
import type { FinanceAccount, FinanceBill, FinanceCadence, FinanceCategory, FinanceDebt, FinanceDebtKind, FinanceGoal } from '@anum/contracts';
import * as api from './lib/financeApi';
import { categoryLabel, parseAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { FormActions, FormError } from './FinanceParts';

export const CADENCES: FinanceCadence[] = ['monthly', 'weekly', 'quarterly', 'yearly', 'once'];
export const DEBT_KINDS: FinanceDebtKind[] = ['loan', 'card', 'mortgage', 'other'];
export const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'cash', 'transfer', 'income', 'other',
];
const today = () => new Date().toISOString().slice(0, 10);

export function BillForm({ bill, accounts, base, onDone }: { bill: FinanceBill | null; accounts: FinanceAccount[]; base: string; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(bill?.name ?? ''), [amount, setAmount] = useState(bill?.amount ?? ''), [currency, setCurrency] = useState(bill?.currency ?? base);
  const [cadence, setCadence] = useState<FinanceCadence>(bill?.cadence ?? 'monthly'), [due, setDue] = useState(bill?.next_due_on ?? today());
  const [category, setCategory] = useState<FinanceCategory>(bill?.category ?? 'utilities'), [accountId, setAccountId] = useState(bill?.account_id ?? '');
  const [active, setActive] = useState(bill?.active ?? true), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    const checked = parseAmount(amount, currency, { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), amount: checked.value, currency, cadence, next_due_on: due, category, account_id: accountId || null, active, source: bill?.source ?? 'manual' as const };
    try { if (bill) await api.updateBill(bill.id, body); else await api.createBill(body); onDone(t('finance.item.saved', { name: body.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.amount', { currency })}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.cadence')}</span><select value={cadence} onChange={(e) => setCadence(e.target.value as FinanceCadence)}>{CADENCES.map((c) => <option key={c} value={c}>{t(`finance.cadence.${c}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.nextDue')}</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} required /></label>
      <label className="field"><span>{t('finance.col.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value as FinanceCategory)}>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.paidFrom')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)}><option value="">{t('finance.form.noAccount')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="field finCheck"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /><span>{t('finance.form.active')}</span></label>
    </div>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}


export function DebtForm({ debt, base, accounts, onDone }: { debt: FinanceDebt | null; base: string; accounts: FinanceAccount[]; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(debt?.name ?? ''), [kind, setKind] = useState<FinanceDebtKind>(debt?.kind ?? 'loan'), [currency, setCurrency] = useState(debt?.currency ?? base);
  const [balance, setBalance] = useState(debt?.balance ?? ''), [apr, setApr] = useState(debt ? String(Number(debt.apr_percent)) : '0'), [minimum, setMinimum] = useState(debt?.minimum_payment ?? '0');
  const [original, setOriginal] = useState(debt?.original_balance ?? ''), [accountId, setAccountId] = useState(debt?.account_id ?? '');
  const [dueDay, setDueDay] = useState(debt?.due_day ? String(debt.due_day) : ''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId(), startHelp = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    const owed = parseAmount(balance, currency, { signed: false }), min = parseAmount(minimum || '0', currency, { signed: false });
    if (!owed.ok) { setError(owed.error); return; }
    if (!min.ok) { setError(min.error); return; }
    // A new debt starts its payoff progress at what is owed today unless another starting balance is typed.
    const start = original.trim() ? parseAmount(original, currency, { signed: false }) : null;
    if (start && !start.ok) { setError(start.error); return; }
    if (!/^\d{1,3}(\.\d{1,4})?$/.test(apr.trim()) || Number(apr) > 100) { setError(t('finance.error.apr')); return; }
    if (dueDay && !/^([1-9]|[12]\d|3[01])$/.test(dueDay)) { setError(t('finance.error.dueDay')); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), kind, balance: owed.value, currency, apr_percent: apr.trim(), minimum_payment: min.value, due_day: dueDay ? Number(dueDay) : null, account_id: accountId || null,
      original_balance: start?.ok ? start.value : debt ? '' : owed.value };
    try { if (debt) await api.updateDebt(debt.id, body); else await api.createDebt(body); onDone(t('finance.item.saved', { name: body.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.kind')}</span><select value={kind} onChange={(e) => setKind(e.target.value as FinanceDebtKind)}>{DEBT_KINDS.map((k) => <option key={k} value={k}>{t(`finance.debtKind.${k}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.owed', { currency })}</span><input value={balance} onChange={(e) => setBalance(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.apr')}</span><input value={apr} onChange={(e) => setApr(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('finance.form.minimum', { currency })}</span><input value={minimum} onChange={(e) => setMinimum(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('fplan.form.original', { currency })}</span><input value={original} onChange={(e) => setOriginal(e.target.value)} inputMode="decimal" dir="ltr" aria-describedby={startHelp} /></label>
      <label className="field"><span>{t('fplan.form.account')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)}><option value="">{t('finance.form.noAccount')}</option>{accounts.filter((a) => !a.archived).map((a) => <option key={a.id} value={a.id}>{a.name}{a.last4 ? ` (${a.last4})` : ''}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.dueDay')}</span><input value={dueDay} onChange={(e) => setDueDay(e.target.value.replace(/\D/g, '').slice(0, 2))} inputMode="numeric" dir="ltr" /></label>
    </div>
    <p id={startHelp} className="muted finHelp">{t('fplan.form.originalHelp')}</p>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}


export function GoalForm({ goal, base, onDone }: { goal: FinanceGoal | null; base: string; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(goal?.name ?? ''), [currency, setCurrency] = useState(goal?.currency ?? base), [target, setTarget] = useState(goal?.target_amount ?? '');
  const [saved, setSaved] = useState(goal?.saved_amount ?? '0'), [by, setBy] = useState(goal?.target_on ?? ''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [monthly, setMonthly] = useState(goal?.monthly_contribution ?? '');
  const errorId = useId(), monthlyHelp = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    const goalAmount = parseAmount(target, currency, { signed: false, allowZero: false }), savedAmount = parseAmount(saved || '0', currency, { signed: false });
    if (!goalAmount.ok) { setError(goalAmount.error); return; }
    if (!savedAmount.ok) { setError(savedAmount.error); return; }
    const monthlyAmount = monthly.trim() ? parseAmount(monthly, currency, { signed: false }) : null;
    if (monthlyAmount && !monthlyAmount.ok) { setError(monthlyAmount.error); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), currency, target_amount: goalAmount.value, saved_amount: savedAmount.value, target_on: by || null, monthly_contribution: monthlyAmount?.ok ? monthlyAmount.value : null };
    try { if (goal) await api.updateGoal(goal.id, body); else await api.createGoal(body); onDone(t('finance.item.saved', { name: body.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.target', { currency })}</span><input value={target} onChange={(e) => setTarget(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.saved', { currency })}</span><input value={saved} onChange={(e) => setSaved(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('finance.form.by')}</span><input type="date" value={by} onChange={(e) => setBy(e.target.value)} /></label>
      <label className="field"><span>{t('finance.form.monthly', { currency })}</span><input value={monthly} onChange={(e) => setMonthly(e.target.value)} inputMode="decimal" dir="ltr" aria-describedby={monthlyHelp} /></label>
    </div>
    <p id={monthlyHelp} className="muted finHelp">{t('finance.form.monthlyHelp')}</p>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

