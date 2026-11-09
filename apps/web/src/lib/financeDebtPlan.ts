// Debts and goals: display helpers for the payoff plan, the what-if and the debt and savings outlook
// (docs/finance.md#debts-and-goals). The payoff itself (order, months, interest, month-end balances) is worked
// out by the API's one payoff engine (`GET /planner/payoff`, services/api/anum_api/finance/planner.py): this
// file only reads its answer into whole minor units (fils or halalas) and works out goal figures and dates.
//
// The figures are estimates: the rates and balances are what the person typed, interest is the yearly
// rate divided by 12 on the balance at the start of each month, and nothing models fees or promotions.
import type { FinanceDebt, FinanceDebtStrategy, FinanceGoal, FinancePayoff } from '@anum/contracts';
import { minorDigits } from './finance.ts';

export interface PlanDebt { id: string; name: string; balance: number; aprPercent: number; minimum: number }

/** "1234.50" in `currency` as whole minor units (123450). Empty or unreadable is 0. */
export function toMinor(value: string | null | undefined, currency: string): number {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n)) return 0;
  return Math.round(n * 10 ** minorDigits(currency));
}

/** Whole minor units back to a decimal string with the currency's digits. */
export function fromMinor(value: number, currency: string): string {
  const digits = minorDigits(currency);
  return (value / 10 ** digits).toFixed(digits);
}

/** The debts of one currency, as the planner reads them. Settled debts (balance 0) are left out. */
export function planDebts(debts: readonly FinanceDebt[], currency: string): PlanDebt[] {
  return debts.filter((d) => d.currency === currency).map((d) => ({
    id: d.id, name: d.name, balance: toMinor(d.balance, currency), aprPercent: Number(d.apr_percent) || 0, minimum: toMinor(d.minimum_payment, currency),
  })).filter((d) => d.balance > 0);
}

export interface Payoff {
  /** Months until every debt is paid, or null when the payments never catch up with the interest (or 50 years pass). */
  months: number | null;
  /** Total owed at the end of each month, month 1 first (the next 12 months at most). */
  balances: number[];
  /** Interest added over the whole plan (to payoff, or over 50 years). */
  interest: number;
  /** The month each debt is paid off (1 = this coming month), or null. */
  paidOffIn: Record<string, number | null>;
  /** The monthly payment used: the minimums together when no plan is set. */
  payment: number;
  /** The planned payment is below the minimums together: the plan pays the minimums only. */
  belowMinimums: boolean;
  /** Debt ids in the order extra money goes. */
  order: string[];
}

/** One strategy of the API's payoff answer, in whole minor units of its currency. */
export function payoffFor(view: FinancePayoff, strategy: FinanceDebtStrategy): Payoff {
  const result = view[strategy], currency = view.currency;
  return {
    months: result.months, balances: (result.balances ?? []).map((b) => toMinor(b, currency)), interest: toMinor(result.total_interest, currency),
    paidOffIn: { ...(result.paid_off_in ?? {}) }, payment: toMinor(view.payment, currency), belowMinimums: view.below_minimums, order: [...result.order],
  };
}

/** The API's debts in the order extra money goes for `strategy` (the plan table's rows). */
export function orderedDebts(view: FinancePayoff, strategy: FinanceDebtStrategy): PlanDebt[] {
  const byId = new Map(view.debts.map((d) => [d.id, d]));
  return view[strategy].order.flatMap((id) => {
    const d = byId.get(id);
    return d ? [{ id: d.id, name: d.name, balance: toMinor(d.balance, view.currency), aprPercent: Number(d.apr_percent) || 0, minimum: toMinor(d.minimum, view.currency) }] : [];
  });
}

/** "YYYY-MM" `months` after `month` ("2026-11" + 6 = "2027-05"). */
export function addMonths(month: string, months: number): string {
  const [year, number] = month.split('-').map(Number);
  const index = year * 12 + (number - 1) + months;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
}

/** The month the last payment falls in: the plan's first payment is in `startMonth`. */
export function payoffMonth(startMonth: string, months: number | null): string | null {
  return months === null ? null : addMonths(startMonth, months - 1);
}

export interface WhatIf { extra: number; baseMonths: number | null; withMonths: number | null; monthsSaved: number | null; interestSaved: number }

/** Paying `extra` more each month on top of the plan (`base`), as the API worked out `withExtra`: how many months sooner, and how much interest is saved. */
export function whatIf(base: Payoff, withExtra: Payoff, extra: number): WhatIf {
  const monthsSaved = base.months !== null && withExtra.months !== null ? base.months - withExtra.months : null;
  return { extra, baseMonths: base.months, withMonths: withExtra.months, monthsSaved, interestSaved: Math.max(0, base.interest - withExtra.interest) };
}

/** Payoff progress for one debt: what was paid off of the starting balance, 0 to 100, or null without a starting balance. */
export function debtProgress(debt: Pick<FinanceDebt, 'balance' | 'original_balance' | 'currency'>): number | null {
  const start = toMinor(debt.original_balance ?? null, debt.currency), now = toMinor(debt.balance, debt.currency);
  if (!debt.original_balance || start <= 0) return null;
  return Math.max(0, Math.min(100, Math.floor(((start - now) * 100) / start)));
}

/** The same over several debts of one currency: only debts with a starting balance count. */
export function totalProgress(debts: readonly Pick<FinanceDebt, 'balance' | 'original_balance' | 'currency'>[]): { percent: number; start: number; now: number } | null {
  const known = debts.filter((d) => d.original_balance && toMinor(d.original_balance, d.currency) > 0);
  if (known.length === 0) return null;
  const start = known.reduce((s, d) => s + toMinor(d.original_balance!, d.currency), 0), now = known.reduce((s, d) => s + toMinor(d.balance, d.currency), 0);
  return { percent: Math.max(0, Math.min(100, Math.floor(((start - now) * 100) / start))), start, now };
}

/** Whole months from `today` to `targetOn`, counting this month (at least 1), like safe to spend does. */
export function monthsLeft(today: string, targetOn: string): number {
  const [ty, tm] = today.split('-').map(Number), [gy, gm] = targetOn.split('-').map(Number);
  return Math.max(1, (gy - ty) * 12 + (gm - tm) + 1);
}

/** What a goal sets aside each month: the amount the person set, else what is left over the months left (rounded up), else null. Paused or reached: 0. */
export function goalMonthly(goal: Pick<FinanceGoal, 'target_amount' | 'saved_amount' | 'currency' | 'monthly_contribution' | 'target_on' | 'paused'>, today: string): number | null {
  const left = toMinor(goal.target_amount, goal.currency) - toMinor(goal.saved_amount, goal.currency);
  if (left <= 0 || goal.paused) return 0;
  const set = toMinor(goal.monthly_contribution ?? null, goal.currency);
  if (goal.monthly_contribution && set > 0) return Math.min(set, left);
  if (goal.target_on) return Math.min(Math.ceil(left / monthsLeft(today, goal.target_on)), left);
  return null;
}

export function goalPercent(goal: Pick<FinanceGoal, 'target_amount' | 'saved_amount' | 'currency'>): number {
  const target = toMinor(goal.target_amount, goal.currency);
  return target > 0 ? Math.max(0, Math.min(100, Math.floor((toMinor(goal.saved_amount, goal.currency) * 100) / target))) : 0;
}

export interface OutlookPoint { month: string; debt: number; savings: number }

/**
 * The next `months` months for one currency: what is owed under the plan at the end of each month (the API's
 * month-end `balances`; 0 once paid off), and what the goals hold if each gets its monthly amount (never past its
 * target; a paused goal stays where it is). Month 0 is today's figures (`startDebt`).
 */
export function outlook(startDebt: number, balances: readonly number[], paidOff: boolean,
  goals: readonly Pick<FinanceGoal, 'target_amount' | 'saved_amount' | 'currency' | 'monthly_contribution' | 'target_on' | 'paused'>[],
  currency: string, today: string, months = 12): OutlookPoint[] {
  const mine = goals.filter((g) => g.currency === currency);
  const start = today.slice(0, 7);
  const points: OutlookPoint[] = [];
  let debt = startDebt;
  for (let m = 0; m <= months; m++) {
    if (m > 0) debt = balances[m - 1] ?? (paidOff ? 0 : debt);
    const savings = mine.reduce((sum, g) => {
      const saved = toMinor(g.saved_amount, currency), target = toMinor(g.target_amount, currency);
      const monthly = goalMonthly(g, today) ?? 0;
      return sum + Math.min(Math.max(saved, target), saved + monthly * m);
    }, 0);
    points.push({ month: addMonths(start, m), debt, savings });
  }
  return points;
}

/** The next date a debt's payment is due (its due day, this month or next; short months use their last day). */
export function nextDueOn(dueDay: number | null, today: string): string | null {
  if (!dueDay) return null;
  const [y, m, d] = today.split('-').map(Number);
  const pick = (year: number, month: number) => {
    const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return `${year}-${String(month).padStart(2, '0')}-${String(Math.min(dueDay, last)).padStart(2, '0')}`;
  };
  const here = pick(y, m);
  if (Number(here.slice(8)) >= d) return here;
  return m === 12 ? pick(y + 1, 1) : pick(y, m + 1);
}

/** Days from `today` to `day` (both "YYYY-MM-DD"). */
export function daysBetween(today: string, day: string): number {
  return Math.round((Date.parse(`${day}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000);
}

/** The currencies that have debts or goals, the base currency first when present. */
export function planCurrencies(debts: readonly { currency: string }[], goals: readonly { currency: string }[], base: string): string[] {
  const all = [...new Set([...debts, ...goals].map((r) => r.currency))].sort();
  return all.includes(base) ? [base, ...all.filter((c) => c !== base)] : all;
}
