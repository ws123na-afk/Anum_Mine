// The Personal Finance dashboard (docs/finance.md#dashboard): what the overview shows for one currency, read from the
// summary's own figures. Pure, so `test:unit` covers it. Nothing is added up or converted here: the server worked
// every figure out once (GET /finance/summary), and the phone (apps/mobile, finance_core_models.dart `dashboardTotals`)
// reads the same answer the same way. `test/financeDashboard.test.ts` and the Dart twin parse one shared fixture
// (packages/contracts/fixtures/finance-summary.json) and expect the same totals.
import type {
  FinanceAccountFreshness, FinanceCurrencyAmount, FinanceExcludedKind, FinancePosition, FinanceSummary, FinanceUpcomingPayment,
} from '@anum/contracts';
import { minorDigits } from './finance.ts';

export interface DashboardTotals {
  currency: string;
  balance: string;
  cash: string;
  cardOwed: string;
  /** null: no card in this currency has a known limit. */
  cardAvailable: string | null;
  income: string;
  spend: string;
  net: string;
  paymentsDue: string;
  upcomingCount: number;
  /** What was left out of income and spend this month, by why: money in and out as the server counted them. */
  excluded: Partial<Record<FinanceExcludedKind, { in: string; out: string; count: number }>>;
  reviewTotal: number;
  staleAccounts: number;
  salaryReceived: string | null;
}

const zero = (currency: string) => (0).toFixed(minorDigits(currency));

export function amountFor(rows: readonly FinanceCurrencyAmount[] | undefined, currency: string): string {
  return rows?.find((r) => r.currency === currency)?.amount ?? zero(currency);
}

export function positionFor(summary: FinanceSummary, currency: string): FinancePosition | null {
  return summary.positions?.find((p) => p.currency === currency) ?? null;
}

export function dashboardTotals(summary: FinanceSummary, currency: string): DashboardTotals {
  const position = positionFor(summary, currency);
  const excluded: DashboardTotals['excluded'] = {};
  for (const row of summary.excluded ?? []) {
    if (row.currency === currency) excluded[row.kind] = { in: row.money_in, out: row.money_out, count: row.count };
  }
  const payments = (summary.upcoming_payments ?? []).filter((p) => p.currency === currency);
  return {
    currency,
    balance: amountFor(summary.balances, currency),
    cash: position?.cash ?? zero(currency),
    cardOwed: position?.card_owed ?? zero(currency),
    cardAvailable: position?.card_available ?? null,
    income: amountFor(summary.income, currency),
    spend: amountFor(summary.spend, currency),
    net: amountFor(summary.net, currency),
    paymentsDue: amountFor(summary.payments_due, currency),
    upcomingCount: payments.length,
    excluded,
    reviewTotal: summary.review?.total ?? summary.uncategorized_count ?? 0,
    staleAccounts: (summary.freshness ?? []).filter((f) => f.state === 'stale').length,
    salaryReceived: summary.salary && summary.salary.currency === currency ? summary.salary.received : null,
  };
}

/** The upcoming payments, falling back to the bills alone for a server that does not send them yet. */
export function upcomingPayments(summary: FinanceSummary): FinanceUpcomingPayment[] {
  if (summary.upcoming_payments) return summary.upcoming_payments;
  return summary.upcoming_bills.map((b) => ({ kind: 'bill', id: b.id, name: b.name, provider: null, amount: b.amount, currency: b.currency, due_on: b.due_on,
    days_until: b.days_until, overdue: Boolean(b.overdue), status: 'pending' }));
}

export function freshnessFor(summary: Pick<FinanceSummary, 'freshness'>, accountId: string): FinanceAccountFreshness | null {
  return summary.freshness?.find((f) => f.account_id === accountId) ?? null;
}

/** The newest automated capture (SMS, Apple Pay or a statement import), or null when the account is typed by hand only. */
export function lastCapture(f: FinanceAccountFreshness): { at: string; source: 'sms' | 'apple_wallet' | 'statement' } | null {
  const seen = ([['sms', f.last_sms_at], ['apple_wallet', f.last_apple_pay_at], ['statement', f.last_import_at]] as const)
    .filter((pair): pair is readonly ['sms' | 'apple_wallet' | 'statement', string] => Boolean(pair[1]))
    .sort((a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : 0));
  return seen[0] ? { source: seen[0][0], at: seen[0][1] } : null;
}

/** Whole days between an ISO timestamp and `now` (never negative). */
export function daysSince(at: string, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(at).getTime()) / 86_400_000));
}
