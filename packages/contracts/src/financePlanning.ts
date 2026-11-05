// Bills matched to recorded payments, debt payments, the debt plan and budgets
// (docs/finance.md#bills-and-budgets, #debts-and-goals). Amounts are decimal strings.
import type { FinanceCadence, FinanceCategory } from './finance.js';
import type { FinanceLinkStatus } from './financeLinks.js';

/** A due date's status: `paid` only with an active matched payment; a match waiting for review is `review`. */
export type FinanceOccurrenceStatus = 'paid' | 'review' | 'overdue' | 'due_soon' | 'planned';

export interface FinanceBillOccurrence {
  bill_id: string;
  name: string;
  category: FinanceCategory;
  cadence: FinanceCadence;
  account_id: string | null;
  due_on: string;
  days_until: number;
  expected: string;
  currency: string;
  status: FinanceOccurrenceStatus;
  paid_amount: string;
  link_ids: string[];
  canonical_ids: string[];
}

/** GET /api/v1/finance/bill-schedule?from&to */
export interface FinanceBillSchedule { today: string; start: string; end: string; items: FinanceBillOccurrence[] }

/** GET /api/v1/finance/payment-candidates?for=bill|debt */
export interface FinancePaymentCandidate {
  canonical_id: string;
  posted_on: string;
  merchant: string;
  category: string | null;
  account_id: string | null;
  amount: string;
  currency: string;
  /** What is left of the payment to match in this dimension. */
  remaining: string;
}

/** POST /bills/{id}/matches (due_on required) and /debts/{id}/payments. */
export interface FinanceMatchInput { canonical_id: string; due_on?: string | null; amount?: string | null }

export interface FinanceDebtPayment {
  link_id: string;
  debt_id: string;
  debt_name: string | null;
  canonical_id: string;
  posted_on: string | null;
  merchant: string | null;
  account_id: string | null;
  amount: string;
  currency: string;
  status: FinanceLinkStatus;
}

export interface FinanceDebtPaymentPage {
  items: FinanceDebtPayment[];
  total: number;
  limit: number;
  offset: number;
  /** Active matched payments per debt and currency. */
  paid: { debt_id: string; currency: string; amount: string }[];
}

export type FinanceDebtStrategy = 'avalanche' | 'snowball';

export interface FinanceDebtPlan { currency: string; strategy: FinanceDebtStrategy; monthly_payment: string | null }
export interface FinanceDebtPlanView { plans: FinanceDebtPlan[] }

export type FinanceBudgetState = 'ok' | 'near' | 'over';

export interface FinanceBudgetLine {
  category: FinanceCategory;
  currency: string;
  cap: string;
  spent: string;
  remaining: string;
  over: string;
  percent: number;
  state: FinanceBudgetState;
  alert_percent: number;
  alert_near: boolean;
  alert_over: boolean;
  /** At this month's pace (current month only). */
  projected: string | null;
  projected_over: string | null;
}

export interface FinanceBudgetSeries { currency: string; budget: string; spent: string; points: { day: string; spent: string }[] }

/** GET /api/v1/finance/budgets?month */
export interface FinanceBudgetReport {
  month: string;
  start: string;
  end: string;
  today: string;
  rules_version: number;
  budgets: FinanceBudgetLine[];
  series: FinanceBudgetSeries[];
}

export interface FinanceBudgetAlert { category: FinanceCategory; currency: string; alert_percent: number; alert_near: boolean; alert_over: boolean }
