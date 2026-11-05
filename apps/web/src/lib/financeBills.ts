// Bills and budgets: the figures the page shows, from the bill schedule and the budget report the API
// returns (docs/finance.md#bills-and-budgets). Pure, so the unit tests cover them without a browser.
// Amounts stay per currency: different currencies are never added together.
import type { FinanceBillOccurrence, FinanceBudgetLine, FinanceBudgetReport, FinanceOccurrenceStatus } from '@anum/contracts';
import { fromMinor, toMinor } from './financeDebtPlan.ts';

export interface CurrencyTotal { currency: string; amount: string; count: number }

function totals(rows: readonly FinanceBillOccurrence[], pick: (r: FinanceBillOccurrence) => string): CurrencyTotal[] {
  const sums = new Map<string, { minor: number; count: number }>();
  for (const row of rows) {
    const found = sums.get(row.currency) ?? { minor: 0, count: 0 };
    found.minor += toMinor(pick(row), row.currency);
    found.count += 1;
    sums.set(row.currency, found);
  }
  return [...sums.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([currency, v]) => ({ currency, amount: fromMinor(v.minor, currency), count: v.count }));
}

/** What is still expected: due dates not paid yet (a match waiting for review still counts). */
export const isOpen = (row: Pick<FinanceBillOccurrence, 'status'>) => row.status !== 'paid';

export interface BillKpis {
  /** Every due date in the period with what is expected. */
  month: CurrencyTotal[];
  /** Open due dates from today to 7 days ahead. */
  week: CurrencyTotal[];
  /** Open due dates already past. */
  overdue: CurrencyTotal[];
  paid: CurrencyTotal[];
}

export function billKpis(rows: readonly FinanceBillOccurrence[]): BillKpis {
  return {
    month: totals(rows, (r) => r.expected),
    week: totals(rows.filter((r) => isOpen(r) && r.days_until >= 0 && r.days_until <= 7), (r) => r.expected),
    overdue: totals(rows.filter((r) => isOpen(r) && r.days_until < 0), (r) => r.expected),
    paid: totals(rows.filter((r) => r.status === 'paid'), (r) => r.paid_amount),
  };
}

/** The total in one currency, or the first currency's when that one has none. */
export function pickTotal(rows: readonly CurrencyTotal[], currency: string): CurrencyTotal | null {
  return rows.find((r) => r.currency === currency) ?? rows[0] ?? null;
}

export interface StripDay { day: string; weekday: number; date: number; dueCount: number; today: boolean }

/** Every day from `from` to `to` ("YYYY-MM-DD"), with how many bills fall due that day. */
export function dayStrip(from: string, to: string, rows: readonly Pick<FinanceBillOccurrence, 'due_on'>[], today: string): StripDay[] {
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(r.due_on, (counts.get(r.due_on) ?? 0) + 1);
  const out: StripDay[] = [];
  for (let t = Date.parse(`${from}T00:00:00Z`), end = Date.parse(`${to}T00:00:00Z`); t <= end && out.length < 400; t += 86_400_000) {
    const d = new Date(t);
    const day = d.toISOString().slice(0, 10);
    out.push({ day, weekday: d.getUTCDay(), date: d.getUTCDate(), dueCount: counts.get(day) ?? 0, today: day === today });
  }
  return out;
}

/** A month as weeks of 7 cells for the calendar (null pads the first and last week). Weeks start on `weekStart` (0 Sunday, 1 Monday, 6 Saturday). */
export function calendarWeeks(month: string, weekStart = 0): (string | null)[][] {
  const [year, number] = month.split('-').map(Number);
  const days = new Date(Date.UTC(year, number, 0)).getUTCDate();
  const first = new Date(Date.UTC(year, number - 1, 1)).getUTCDay();
  const cells: (string | null)[] = Array.from({ length: (first - weekStart + 7) % 7 }, () => null);
  for (let d = 1; d <= days; d++) cells.push(`${month}-${String(d).padStart(2, '0')}`);
  while (cells.length % 7) cells.push(null);
  const weeks: (string | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/** The chip tone for a due date's status. */
export function statusTone(status: FinanceOccurrenceStatus): 'ok' | 'warn' | 'bad' | 'info' | 'muted' {
  return status === 'paid' ? 'ok' : status === 'overdue' ? 'bad' : status === 'due_soon' || status === 'review' ? 'warn' : 'muted';
}

/** Recurring bills as one list per bill (the schedule repeats a bill per due date). */
export function uniqueBills<T extends Pick<FinanceBillOccurrence, 'bill_id'>>(rows: readonly T[]): T[] {
  const seen = new Set<string>();
  return rows.filter((r) => (seen.has(r.bill_id) ? false : (seen.add(r.bill_id), true)));
}

export interface BudgetKpis { active: number; remaining: CurrencyTotal[]; budget: CurrencyTotal[] }

export function budgetKpis(report: Pick<FinanceBudgetReport, 'budgets'>): BudgetKpis {
  const sum = (pick: (b: FinanceBudgetLine) => string) => {
    const by = new Map<string, number>();
    for (const b of report.budgets) by.set(b.currency, (by.get(b.currency) ?? 0) + toMinor(pick(b), b.currency));
    return [...by.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([currency, minor]) => ({ currency, amount: fromMinor(minor, currency), count: report.budgets.filter((b) => b.currency === currency).length }));
  };
  return { active: report.budgets.length, remaining: sum((b) => b.remaining), budget: sum((b) => b.cap) };
}

/** The budget most at risk this month: the one whose pace ends furthest past its cap (already over counts first). */
export function budgetAtRisk(report: Pick<FinanceBudgetReport, 'budgets'>): FinanceBudgetLine | null {
  const risky = report.budgets.filter((b) => b.state === 'over' || b.projected_over !== null);
  if (risky.length === 0) return null;
  const score = (b: FinanceBudgetLine) => (b.state === 'over' ? 1e15 + Number(b.over) : Number(b.projected_over ?? 0));
  return [...risky].sort((a, b) => score(b) - score(a))[0];
}

/** The bar tone for a budget. */
export function budgetTone(line: Pick<FinanceBudgetLine, 'state'>): 'ok' | 'warn' | 'bad' {
  return line.state === 'over' ? 'bad' : line.state === 'near' ? 'warn' : 'ok';
}
