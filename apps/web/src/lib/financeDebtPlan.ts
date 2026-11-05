// Debts and goals: the payoff strategy, the what-if forecast and the debt and savings outlook
// (docs/finance.md#debts-and-goals). Pure functions over the person's own records: real balances,
// rates and minimum payments as they entered them. Nothing here is a fixed figure. Money is worked in
// minor units (whole fils or halalas) so a month's interest rounds once, like a lender's statement.
//
// The figures are estimates: the rates and balances are what the person typed, interest is the yearly
// rate divided by 12 on the balance at the start of each month, and nothing models fees or promotions.
import type { FinanceDebt, FinanceDebtStrategy, FinanceGoal } from '@anum/contracts';
import { minorDigits } from './finance.ts';

/** At most this many months are simulated (50 years); a plan that does not end by then never ends. */
export const MAX_MONTHS = 600;

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

/** The order extra money goes in: avalanche = highest rate first; snowball = smallest balance first. Ties fall back to the other rule, then the name. */
export function payoffOrder(debts: readonly PlanDebt[], strategy: FinanceDebtStrategy): PlanDebt[] {
  return [...debts].sort((a, b) => {
    const byRate = b.aprPercent - a.aprPercent, byBalance = a.balance - b.balance;
    const first = strategy === 'avalanche' ? byRate || byBalance : byBalance || byRate;
    return first || a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
  });
}

export interface Payoff {
  /** Months until every debt is paid, or null when the payments never catch up with the interest (or 50 years pass). */
  months: number | null;
  /** Total owed at the end of each month, month 1 first (at most MAX_MONTHS). */
  balances: number[];
  /** Interest added over the whole plan (to payoff, or over MAX_MONTHS). */
  interest: number;
  /** The month each debt is paid off (1 = this coming month), or null. */
  paidOffIn: Record<string, number | null>;
  /** The monthly payment used: the minimums together when no plan is set. */
  payment: number;
  /** The planned payment is below the minimums together: the plan pays the minimums only. */
  belowMinimums: boolean;
}

/**
 * Month by month: interest is added (rate / 12 on the balance, rounded to the minor unit), each debt gets
 * its minimum (at most what it owes), and whatever is left of `payment` goes to the debts in strategy
 * order. A debt paid off frees its minimum for the next one (the payment stays the same each month).
 * Without a `payment` the person pays the minimums only, each debt on its own.
 */
export function simulatePayoff(debts: readonly PlanDebt[], strategy: FinanceDebtStrategy, payment: number | null, maxMonths = MAX_MONTHS): Payoff {
  const minimums = debts.reduce((sum, d) => sum + d.minimum, 0);
  const belowMinimums = payment !== null && payment < minimums;
  const budget = payment === null || belowMinimums ? null : payment;
  const order = payoffOrder(debts, strategy);
  const owed = new Map(order.map((d) => [d.id, d.balance]));
  const paidOffIn: Record<string, number | null> = Object.fromEntries(order.map((d) => [d.id, d.balance <= 0 ? 0 : null]));
  const balances: number[] = [];
  let interest = 0;
  let total = [...owed.values()].reduce((a, b) => a + b, 0);
  let month = 0;
  while (total > 0 && month < maxMonths) {
    month += 1;
    for (const d of order) {
      const balance = owed.get(d.id)!;
      if (balance <= 0) continue;
      const added = Math.round((balance * d.aprPercent) / 100 / 12);
      interest += added;
      owed.set(d.id, balance + added);
    }
    let left = budget ?? Infinity;
    for (const d of order) {
      const balance = owed.get(d.id)!;
      if (balance <= 0) continue;
      const pay = Math.min(d.minimum, balance, left);
      owed.set(d.id, balance - pay);
      left -= pay;
    }
    if (budget !== null) {
      for (const d of order) {
        if (left <= 0) break;
        const balance = owed.get(d.id)!;
        if (balance <= 0) continue;
        const pay = Math.min(balance, left);
        owed.set(d.id, balance - pay);
        left -= pay;
      }
    }
    for (const d of order) if (paidOffIn[d.id] === null && owed.get(d.id)! <= 0) paidOffIn[d.id] = month;
    const next = [...owed.values()].reduce((a, b) => a + b, 0);
    balances.push(next);
    total = next;
  }
  return { months: total <= 0 ? month : null, balances, interest, paidOffIn, payment: budget ?? minimums, belowMinimums };
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

/** Paying `extra` more each month on top of the plan: how many months sooner, and how much interest is saved. */
export function whatIf(debts: readonly PlanDebt[], strategy: FinanceDebtStrategy, payment: number | null, extra: number): WhatIf {
  const base = simulatePayoff(debts, strategy, payment);
  const withExtra = simulatePayoff(debts, strategy, base.payment + extra);
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
 * The next `months` months for one currency: what is owed under the plan (or the minimums) at the end of
 * each month, and what the goals hold if each gets its monthly amount (never past its target; a paused
 * goal stays where it is). Month 0 is today's figures.
 */
export function outlook(debts: readonly PlanDebt[], strategy: FinanceDebtStrategy, payment: number | null,
  goals: readonly Pick<FinanceGoal, 'target_amount' | 'saved_amount' | 'currency' | 'monthly_contribution' | 'target_on' | 'paused'>[],
  currency: string, today: string, months = 12): OutlookPoint[] {
  const plan = simulatePayoff(debts, strategy, payment, months);
  const mine = goals.filter((g) => g.currency === currency);
  const start = today.slice(0, 7);
  const points: OutlookPoint[] = [];
  let debt = debts.reduce((s, d) => s + d.balance, 0);
  for (let m = 0; m <= months; m++) {
    if (m > 0) debt = plan.balances[m - 1] ?? 0;
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
