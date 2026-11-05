// Finance reports and Safe to spend, the figures behind the pages (docs/finance.md#reports). Pure, so
// `pnpm --filter @anum/web test:unit` covers it. Amounts stay decimal strings: sums and differences are
// done in whole thousandths (BigInt), never in floating point, and never across currencies.
import type { BooksReport, FinanceCategory, FinanceGuardianRules, FinanceSafeToSpend, FinanceSummary, FinanceUpcomingBill } from '@anum/contracts';
import { minorDigits } from './finance.ts';

const SCALE = 1000n;

/** "1234.5" to thousandths; anything that is not a decimal reads as zero. */
export function toMilli(value: string | null | undefined): bigint {
  if (!value || !/^-?\d+(\.\d+)?$/.test(value)) return 0n;
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace('-', '').split('.');
  const milli = BigInt(whole) * SCALE + BigInt((fraction + '000').slice(0, 3));
  return negative ? -milli : milli;
}

/** Thousandths back to the wire string with the currency's digits ("1234.50"). */
export function fromMilli(milli: bigint, currency: string): string {
  const digits = minorDigits(currency);
  const negative = milli < 0n;
  let abs = negative ? -milli : milli;
  // Round half away from zero to the currency's digits.
  const step = 10n ** BigInt(3 - digits);
  abs = ((abs + step / 2n) / step) * step;
  const whole = abs / SCALE;
  const fraction = (abs % SCALE).toString().padStart(3, '0').slice(0, digits);
  const text = digits ? `${whole}.${fraction}` : `${whole}`;
  return negative && abs !== 0n ? `-${text}` : text;
}

export const add = (currency: string, ...values: (string | null | undefined)[]) => fromMilli(values.reduce((sum, v) => sum + toMilli(v), 0n), currency);
export const subtract = (a: string | null | undefined, b: string | null | undefined, currency: string) => fromMilli(toMilli(a) - toMilli(b), currency);

/** `part` as a percentage of `whole`, one decimal; null when the whole is zero (no invented ratio). */
export function percentOf(part: string | null | undefined, whole: string | null | undefined): number | null {
  const w = toMilli(whole);
  if (w === 0n) return null;
  return Math.round((Number(toMilli(part)) * 1000) / Number(w)) / 10;
}

const amountIn = (rows: readonly { currency: string; amount: string }[], currency: string) => rows.find((r) => r.currency === currency)?.amount ?? '0';

/** The currencies a set of monthly summaries has figures in, the base currency first. */
export function reportCurrencies(summaries: readonly FinanceSummary[], base: string): string[] {
  const set = new Set<string>();
  for (const s of summaries) for (const row of [...s.income, ...s.spend, ...s.balances]) set.add(row.currency);
  const rest = [...set].filter((c) => c !== base).sort();
  return set.has(base) || set.size === 0 ? [base, ...rest] : rest;
}

export interface MonthFlow { month: string; income: string; expenses: string; net: string }

/** Income, spending and the net per month in one currency, in the order given. */
export function personalFlows(summaries: readonly FinanceSummary[], currency: string): MonthFlow[] {
  return summaries.map((s) => ({ month: s.month, income: amountIn(s.income, currency), expenses: amountIn(s.spend, currency), net: amountIn(s.net, currency) }));
}

/** The business books as the same shape: revenue, costs and profit per month from the report's trend. */
export function businessFlows(report: BooksReport, months: number): MonthFlow[] {
  return report.trend.slice(-months).map((row) => ({ month: row.month.slice(0, 7), income: row.revenue, expenses: row.costs, net: row.profit }));
}

export interface CategoryRow { category: FinanceCategory; now: string; before: string; change: string; percent: number | null; share: number | null; count: number }

/** This month's spending by category next to the month before, largest first, with each one's share. */
export function categoryRows(current: FinanceSummary, previous: FinanceSummary | null, currency: string): CategoryRow[] {
  const now = current.spend_by_category.filter((r) => r.currency === currency);
  const before = previous?.spend_by_category.filter((r) => r.currency === currency) ?? [];
  const total = amountIn(current.spend, currency);
  const categories = [...new Set([...now.map((r) => r.category), ...before.map((r) => r.category)])];
  return categories.map((category) => {
    const a = now.find((r) => r.category === category), b = before.find((r) => r.category === category);
    const nowAmount = a?.amount ?? '0', beforeAmount = b?.amount ?? '0';
    return {
      category, now: fromMilli(toMilli(nowAmount), currency), before: fromMilli(toMilli(beforeAmount), currency), change: subtract(nowAmount, beforeAmount, currency),
      percent: previous ? percentOf(subtract(nowAmount, beforeAmount, currency), beforeAmount) : null, share: percentOf(nowAmount, total), count: a?.count ?? 0,
    };
  }).sort((x, y) => (toMilli(y.now) > toMilli(x.now) ? 1 : toMilli(y.now) < toMilli(x.now) ? -1 : x.category.localeCompare(y.category)));
}

/** The largest changes from last month (by amount), at most `count`; unchanged categories are left out. */
export function keyMovements(rows: readonly CategoryRow[], count = 3): CategoryRow[] {
  const abs = (v: bigint) => (v < 0n ? -v : v);
  return rows.filter((r) => toMilli(r.change) !== 0n).sort((x, y) => (abs(toMilli(y.change)) > abs(toMilli(x.change)) ? 1 : -1)).slice(0, count);
}

export interface Quality { transactions: number; spendCount: number; uncategorized: number; categorizedPercent: number | null }

/** How complete the month's records are: share of spending with a real category (not "other"). */
export function reportQuality(summary: FinanceSummary, currency: string): Quality {
  const rows = summary.spend_by_category.filter((r) => r.currency === currency);
  const spendCount = rows.reduce((n, r) => n + r.count, 0);
  const uncategorized = rows.filter((r) => r.category === 'other').reduce((n, r) => n + r.count, 0);
  return { transactions: summary.transaction_count, spendCount, uncategorized, categorizedPercent: spendCount ? Math.round(((spendCount - uncategorized) / spendCount) * 1000) / 10 : null };
}

export interface BusinessTotals { currency: string; income: string; expenses: string; gross: string; operating: string; net: string; margin: number | null; complete: boolean }

/** The P&L in the books' base currency: revenue, every cost, gross, operating and net profit, operating margin. */
export function businessTotals(report: BooksReport): BusinessTotals {
  const p = report.pnl, c = report.base_currency;
  const income = add(c, p.revenue.total, p.other_income.total);
  return {
    currency: c, income, expenses: add(c, p.cost_of_sales.total, p.operating_expenses.total, p.finance_costs.total), gross: p.gross_profit,
    operating: p.operating_profit, net: p.net_profit, margin: percentOf(p.operating_profit, p.revenue.total), complete: p.complete,
  };
}

export interface NetWorthRow { currency: string; assets: string; debts: string; net: string }

/** Account balances less debts, per currency (never combined across currencies). */
export function netWorth(summary: FinanceSummary): NetWorthRow[] {
  const currencies = [...new Set([...summary.balances, ...summary.debts].map((r) => r.currency))].sort();
  return currencies.map((currency) => {
    const assets = amountIn(summary.balances, currency), debts = amountIn(summary.debts, currency);
    return { currency, assets: fromMilli(toMilli(assets), currency), debts: fromMilli(toMilli(debts), currency), net: subtract(assets, debts, currency) };
  });
}

// ---- Safe to spend -------------------------------------------------------------------------------

export interface RunwayPoint { day: string; balance: string; bills: { name: string; amount: string }[] }

const addDays = (day: string, n: number) => new Date(Date.parse(`${day}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);

/**
 * The month's runway in one currency: today's estimate after goals and buffer, then each bill taken
 * off on its due day and the average daily spend (`perDay`, from the last 30 days) taken off every day.
 * A projection of the person's own inputs, not a forecast: the page says so.
 */
export function runway(data: FinanceSafeToSpend, currency: string, bills: readonly FinanceUpcomingBill[], perDay: string): RunwayPoint[] {
  const row = data.currencies.find((c) => c.currency === currency);
  if (!row) return [];
  // Start: what you have less goals and buffer; bills come off on their day.
  let balance = toMilli(row.available) - toMilli(row.goals) - toMilli(row.buffer) - toMilli(row.minimum_payments);
  const daily = toMilli(perDay);
  const due = bills.filter((b) => b.currency === currency && b.due_on >= data.today && b.due_on <= data.month_end);
  const points: RunwayPoint[] = [];
  for (let day = data.today, i = 0; day <= data.month_end && i < 40; day = addDays(day, 1), i++) {
    const today = due.filter((b) => b.due_on === day);
    if (i > 0) balance -= daily;
    for (const b of today) balance -= toMilli(b.amount);
    points.push({ day, balance: fromMilli(balance, currency), bills: today.map((b) => ({ name: b.name, amount: b.amount })) });
  }
  return points;
}

/** Average daily spending over the last 30 days from the two months' spend (the current month to date and the month before). */
export function dailyPace(spendThisMonth: string, daysSoFar: number, spendLastMonth: string, daysLastMonth: number, currency: string): string {
  const days = Math.max(1, Math.min(30, daysSoFar + daysLastMonth));
  // The current month counts in full; the rest of the 30 days comes from last month's average day.
  const fromLast = daysSoFar >= 30 ? 0n : (toMilli(spendLastMonth) * BigInt(Math.min(30 - daysSoFar, daysLastMonth))) / BigInt(Math.max(1, daysLastMonth));
  return fromMilli((toMilli(spendThisMonth) + fromLast) / BigInt(days), currency);
}

export interface Guardrail { category: FinanceCategory; spent: string; limit: string; percent: number; tone: 'ok' | 'warn' | 'bad' }

/** The Guardian's monthly category caps in one currency next to this month's spending, fullest first. */
export function guardrails(rules: FinanceGuardianRules, summary: FinanceSummary, currency: string): Guardrail[] {
  return rules.category_caps.filter((cap) => cap.currency === currency).map((cap) => {
    const spent = summary.spend_by_category.find((r) => r.category === cap.category && r.currency === currency)?.amount ?? '0';
    const percent = percentOf(spent, cap.amount) ?? 0;
    return { category: cap.category, spent: fromMilli(toMilli(spent), currency), limit: cap.amount, percent, tone: percent >= 100 ? 'bad' : percent >= 80 ? 'warn' : 'ok' } as Guardrail;
  }).sort((a, b) => b.percent - a.percent);
}

export interface LineRow { key: string; now: string; before: string; change: string; percent: number | null; share: number | null }

/** The business's costs by line (cost of sales and operating expenses) this period against the one before. */
export function businessExpenseRows(current: BooksReport, previous: BooksReport | null): LineRow[] {
  const c = current.base_currency;
  const lines = (r: BooksReport | null) => (r ? [...r.pnl.cost_of_sales.lines, ...r.pnl.operating_expenses.lines] : []);
  const now = lines(current), before = lines(previous);
  const total = add(c, current.pnl.cost_of_sales.total, current.pnl.operating_expenses.total);
  const keys = [...new Set([...now.map((l) => l.key), ...before.map((l) => l.key)])];
  const sum = (rows: { key: string; amount: string }[], key: string) => add(c, ...rows.filter((l) => l.key === key).map((l) => l.amount));
  return keys.map((key) => {
    const a = sum(now, key), b = sum(before, key);
    return { key, now: a, before: b, change: subtract(a, b, c), percent: previous ? percentOf(subtract(a, b, c), b) : null, share: percentOf(a, total) };
  }).sort((x, y) => (toMilli(y.now) > toMilli(x.now) ? 1 : toMilli(y.now) < toMilli(x.now) ? -1 : x.key.localeCompare(y.key)));
}
