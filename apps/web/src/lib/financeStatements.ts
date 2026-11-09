// Finance reports and Safe to spend, the figures behind the pages (docs/finance.md#reports). Pure, so
// `pnpm --filter @anum/web test:unit` covers it. Amounts stay decimal strings: sums and differences are
// done in whole thousandths (BigInt), never in floating point, and never across currencies.
import type { BooksReport, FinanceCategory, FinanceGuardianRules, FinanceSafeToSpend, FinanceSummary, FinanceUpcomingBill } from '@anum/contracts';
import { minorDigits } from './finance.ts';

const SCALE = 1000n;

/** Round `value / step` half to even, the server's one rounding rule (services/api/anum_api/finance/money.py). */
function halfEven(value: bigint, step: bigint): bigint {
  const quotient = value / step, rest = value % step;
  if (rest * 2n > step || (rest * 2n === step && quotient % 2n === 1n)) return quotient + 1n;
  return quotient;
}

/** "1234.5" to thousandths; anything that is not a decimal reads as zero. More than three decimals round half to even. */
export function toMilli(value: string | null | undefined): bigint {
  if (!value || !/^-?\d+(\.\d+)?$/.test(value)) return 0n;
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace('-', '').split('.');
  const extra = Math.max(0, fraction.length - 3);
  const exact = BigInt(whole + (fraction + '000').slice(0, Math.max(3, fraction.length)));
  const milli = extra ? halfEven(exact, 10n ** BigInt(extra)) : exact;
  return negative ? -milli : milli;
}

/** Thousandths back to the wire string with the currency's digits ("1234.50"). */
export function fromMilli(milli: bigint, currency: string): string {
  const digits = minorDigits(currency);
  const negative = milli < 0n;
  let abs = negative ? -milli : milli;
  // Round half to even to the currency's digits, as the server does.
  const step = 10n ** BigInt(3 - digits);
  abs = halfEven(abs, step) * step;
  const whole = abs / SCALE;
  const fraction = (abs % SCALE).toString().padStart(3, '0').slice(0, digits);
  const text = digits ? `${whole}.${fraction}` : `${whole}`;
  return negative && abs !== 0n ? `-${text}` : text;
}

/**
 * A positive amount typed or imported in `currency` as the wire string ("1,250.00" to "1250.00", KWD "1.250"), or null.
 * Exact decimals, never floats; thousands separators are dropped; more decimals than the currency has are refused.
 */
export function parseImportAmount(value: string, currency: string): string | null {
  const raw = value.trim().replace(/,(?=\d{3}(?:\D|$))/g, '');
  if (!/^\d+(?:\.\d+)?$/.test(raw) || (raw.split('.')[1] ?? '').length > minorDigits(currency)) return null;
  const milli = toMilli(raw);
  return milli > 0n ? fromMilli(milli, currency) : null;
}

/** What is left of a budget, never below zero, in exact decimals. */
export function remainingOf(budget: string, spent: string, currency: string): string {
  const left = toMilli(budget) - toMilli(spent);
  return fromMilli(left > 0n ? left : 0n, currency);
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

const LIABILITY_KINDS = new Set(['card', 'loan']);

/**
 * Net worth per currency (never combined across currencies), counted once (docs/finance.md#how-figures-are-counted):
 * assets are bank, cash and investment balances (and a card in credit); debts are what cards and loans owe by their
 * balances plus only the debts that have no account of their own (`unlinked_debts`). A card kept as an account and as
 * a debt is therefore counted once.
 */
export function netWorth(summary: FinanceSummary): NetWorthRow[] {
  // The server's one definition: accounts, money lent and holdings at cost, less debts (money borrowed included) and installments.
  if (summary.net_worth_parts) {
    return summary.net_worth_parts.map((p) => ({
      currency: p.currency,
      assets: fromMilli(toMilli(p.accounts) + toMilli(p.receivables) + toMilli(p.holdings_at_cost), p.currency),
      debts: fromMilli(toMilli(p.debts) + toMilli(p.installments), p.currency),
      net: p.net,
    }));
  }
  const loose = summary.unlinked_debts ?? summary.debts;
  const accounts = summary.accounts.filter((a) => !a.archived);
  const assets = new Map<string, bigint>(), owed = new Map<string, bigint>();
  const bump = (map: Map<string, bigint>, currency: string, value: bigint) => map.set(currency, (map.get(currency) ?? 0n) + value);
  for (const a of accounts) {
    const balance = toMilli(a.balance);
    if (LIABILITY_KINDS.has(a.kind)) {
      if (balance < 0n) bump(owed, a.currency, -balance);
      else bump(assets, a.currency, balance);
    } else bump(assets, a.currency, balance);
  }
  // A summary without its accounts (an older reply) still has the per-currency balances.
  for (const row of summary.balances) if (!accounts.some((a) => a.currency === row.currency)) bump(assets, row.currency, toMilli(row.amount));
  for (const row of loose) bump(owed, row.currency, toMilli(row.amount));
  const currencies = [...new Set([...assets.keys(), ...owed.keys()])].sort();
  return currencies.map((currency) => {
    const a = assets.get(currency) ?? 0n, d = owed.get(currency) ?? 0n;
    return { currency, assets: fromMilli(a, currency), debts: fromMilli(d, currency), net: fromMilli(a - d, currency) };
  });
}

// ---- Safe to spend -------------------------------------------------------------------------------

export interface RunwayPoint { day: string; balance: string; bills: { name: string; amount: string }[] }

const addDays = (day: string, n: number) => new Date(Date.parse(`${day}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);

/**
 * The month's runway in one currency: today's estimate after goals and buffer, then each bill taken
 * off on its due day (an overdue one, not paid yet, today) and the average daily spend (`perDay`, from
 * the last 30 days without the categories bills cover, `spendOutsideBills`) taken off every day.
 * A projection of the person's own inputs, not a forecast: the page says so.
 */
export function runway(data: FinanceSafeToSpend, currency: string, bills: readonly FinanceUpcomingBill[], perDay: string): RunwayPoint[] {
  const row = data.currencies.find((c) => c.currency === currency);
  if (!row) return [];
  // Start: what you have less goals and buffer; bills come off on their day.
  let balance = toMilli(row.available) - toMilli(row.goals) - toMilli(row.buffer) - toMilli(row.minimum_payments);
  const daily = toMilli(perDay);
  const due = bills.filter((b) => b.currency === currency && b.due_on <= data.month_end && (b.due_on >= data.today || b.overdue));
  const points: RunwayPoint[] = [];
  for (let day = data.today, i = 0; day <= data.month_end && i < 40; day = addDays(day, 1), i++) {
    const today = due.filter((b) => (i === 0 ? b.due_on <= day : b.due_on === day));
    if (i > 0) balance -= daily;
    for (const b of today) balance -= toMilli(b.amount);
    points.push({ day, balance: fromMilli(balance, currency), bills: today.map((b) => ({ name: b.name, amount: b.amount })) });
  }
  return points;
}

/**
 * A month's spend without the categories the person's bills cover, so the daily pace does not count rent or
 * utilities that the runway already takes off on their due day.
 */
export function spendOutsideBills(summary: FinanceSummary, currency: string, bills: readonly Pick<FinanceUpcomingBill, 'category' | 'currency'>[]): string {
  const covered = new Set(bills.filter((b) => b.currency === currency).map((b) => b.category));
  const inBills = summary.spend_by_category.filter((r) => r.currency === currency && covered.has(r.category)).reduce((sum, r) => sum + toMilli(r.amount), 0n);
  const outside = toMilli(amountIn(summary.spend, currency)) - inBills;
  return fromMilli(outside > 0n ? outside : 0n, currency);
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
