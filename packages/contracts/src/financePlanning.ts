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

/** A full provider installment statement row. This is SOURCE-REPORTED status, never a bank payment. */
export interface FinanceInstallmentStatementEntry {
  provider: string;
  order_id: string;
  merchant: string;
  purchase_on: string | null;
  due_on: string;
  installment: number;
  total_installments: number;
  amount: string;
  currency: string;
  /** `refunded`: the provider's own statement marks this installment refunded or cancelled. */
  source_status: 'paid' | 'scheduled' | 'review' | 'refunded';
  source_email: string | null;
  review_notes: string | null;
  /** Set only by ANUM when the owner applies refund evidence: how much of `amount` is no longer due. */
  refunded_amount?: string;
  /** A lower amount a later statement import reported for the same installment and due date. */
  reported_amount?: string | null;
}
export interface FinanceInstallmentStatementView { items: FinanceInstallmentStatementEntry[]; total: number }
export interface FinanceInstallmentStatementSyncResult {
  created: number;
  updated: number;
  unchanged: number;
  conflicts: string[];
  total: number;
  refund_signals?: string[];
  notices_saved?: number;
}

/** A Tamara refund or order-updated email kept as refund evidence (never as money received). */
export interface FinanceInstallmentRefundNotice {
  provider: string;
  order_id: string;
  amount: string | null;
  currency: string;
  full: boolean;
  received_on: string;
  message_ref: string;
  source_email: string | null;
}

export type FinanceRefundEvidenceCode =
  | 'email_amount' | 'email_full' | 'email_duplicate' | 'email_no_amount'
  | 'statement_refunded' | 'statement_lower_amount' | 'statement_review'
  | 'ledger_order' | 'ledger_matches_email' | 'ledger_merchant' | 'ledger_amount_only';
export type FinanceRefundReason =
  | 'estimate_only' | 'weak_only' | 'more_than_order' | 'applied_more_than_evidence' | 'statement_differs' | 'email_without_amount';
export type FinanceRefundOrderState = 'active' | 'partly_refunded' | 'refunded' | 'needs_review';
export type FinanceRefundInstallmentState = 'paid' | 'due' | 'partly_refunded' | 'refunded';

export interface FinanceRefundEvidence {
  id: string;
  kind: 'email' | 'statement' | 'ledger';
  strength: 'strong' | 'weak';
  role: 'counted' | 'schedule' | 'money_back' | 'review';
  code: FinanceRefundEvidenceCode;
  amount: string | null;
  currency: string;
  on: string | null;
  installment: number | null;
  link: string | null;
  transaction_id: string | null;
}

export interface FinanceRefundInstallment {
  installment: number;
  total_installments: number;
  due_on: string;
  amount: string;
  source_status: string;
  refunded_before: string;
  refunded_after: string;
  due_before: string;
  due_after: string;
  /** Only for a partial refund without Tamara's new schedule: an estimate, never applied. */
  estimate_due: string | null;
  state_before: FinanceRefundInstallmentState;
  state_after: FinanceRefundInstallmentState;
}

export interface FinanceOrderRefundPlan {
  provider: string;
  order_id: string;
  merchant: string;
  currency: string;
  state: FinanceRefundOrderState;
  applied_state: FinanceRefundOrderState;
  mode: 'none' | 'cancel_all' | 'schedule' | 'estimate';
  applicable: boolean;
  pending: boolean;
  order_total: string;
  paid_total: string;
  refund_total: string;
  credit_back: string;
  credit_received: string;
  outstanding_before: string;
  outstanding_after: string;
  next_due_before: string | null;
  next_due_after: string | null;
  evidence: FinanceRefundEvidence[];
  installments: FinanceRefundInstallment[];
  reasons: FinanceRefundReason[];
  plan_hash: string;
}

export interface FinanceRefundRecord {
  id: string;
  provider: string;
  order_id: string;
  currency: string;
  applied_at: string;
  refund_total: string;
  credit_back: string;
  installments: number;
  bills: number;
  undone: boolean;
  can_undo: boolean;
}

export interface FinanceRefundPreview {
  today: string;
  orders: FinanceOrderRefundPlan[];
  pending: number;
  needs_review: number;
  records: FinanceRefundRecord[];
}

export interface FinanceRefundApplyResult {
  applied: number;
  installments: number;
  bills_paused: number;
  bills_lowered: number;
  record_ids: string[];
  preview: FinanceRefundPreview;
}

// -- bank and finance loans on a debt (docs/finance.md#bank-loans) ----------------------------------------------------

export interface FinanceLoanTermsInput {
  lender?: string | null;
  start_on: string;
  amount: string;
  rate_type?: 'reducing' | 'flat';
  /** A yearly percentage; empty when not known (never guessed). */
  rate?: string | null;
  term_months?: number | null;
  /** The lender's instalment, when known: it wins over the computed one. */
  installment?: string | null;
  payment_day?: number | null;
  repayment?: 'bank_debit' | 'salary_deduction' | 'manual';
  account_id?: string | null;
  status?: 'open' | 'paid_off' | 'closed';
  closed_on?: string | null;
}

export interface FinanceLoanTerms {
  lender: string | null; start_on: string; amount: string; rate_type: 'reducing' | 'flat'; rate: string | null; term_months: number | null;
  installment: string | null; payment_day: number | null; repayment: 'bank_debit' | 'salary_deduction' | 'manual'; account_id: string | null;
  status: 'open' | 'paid_off' | 'closed'; closed_on: string | null;
}

export interface FinanceLoanPayment { kind: 'link' | 'salary'; ref: string; on: string; amount: string; link_id: string | null; batch_id: string | null }
export interface FinanceLoanCandidate { canonical_id: string; posted_on: string; amount: string; currency: string; merchant: string; account_id: string }

export type FinanceLoanRowStatus = 'paid' | 'part_paid' | 'missed' | 'upcoming' | 'ambiguous' | 'settled';

export interface FinanceLoanRow {
  number: number;
  due_on: string;
  payment: string;
  /** null without a rate: the split into principal and interest is not known. */
  principal: string | null;
  interest: string | null;
  balance_after: string;
  status: FinanceLoanRowStatus;
  paid: string;
  payments: FinanceLoanPayment[];
  /** Ambiguous: the debits that fit; pick one. */
  candidates: FinanceLoanCandidate[];
  /** A payment found for it that waits for the suggestion's accept. */
  found: FinanceLoanPayment | null;
}

export interface FinanceLoanTotals {
  status: 'open' | 'paid_off' | 'closed';
  basis: 'reducing' | 'flat' | 'plain';
  installment: string;
  installment_from: 'lender' | 'computed';
  split_known: boolean;
  never_ends: boolean;
  effective_rate: string | null;
  total_payable: string;
  total_interest: string | null;
  paid: string;
  interest_paid: string | null;
  principal_paid: string | null;
  balance: string;
  payoff_on: string | null;
  due_count: number;
  paid_count: number;
  /** Instalments paid in full less those due by today: negative is behind. */
  months_ahead: number;
  next_due_on: string | null;
  next_amount: string;
  /** The salary's expected net already takes the instalment, so it is not a commitment of its own. */
  from_salary: boolean;
  found: number;
  missing: number;
  ambiguous: number;
}

export interface FinanceLoanBatch { id: string; debt_id: string; status: 'active' | 'undone'; created_at: string; undone_at: string | null; count: number; amount: string; currency: string | null }

export interface FinanceLoanView {
  debt_id: string; name: string; kind: string; currency: string; terms: FinanceLoanTerms; totals: FinanceLoanTotals; rows: FinanceLoanRow[]; batches: FinanceLoanBatch[];
}

export interface FinanceLoanSummary { debt_id: string; name: string; lender: string | null; currency: string; repayment: string; totals: FinanceLoanTotals }
export interface FinanceLoanList { items: FinanceLoanSummary[] }
