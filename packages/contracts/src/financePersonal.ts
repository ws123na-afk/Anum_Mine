// Money lent and borrowed with people, payslips, remittances, fixed commitments and the Plan view
// (docs/finance.md#lent-and-borrowed, #payslips, #sending-money-home, #fixed-commitments, #planning-and-advice).
// Amounts are decimal strings next to their currency; nothing is added across currencies. ANUM only advises.
import type { FinanceLink } from './financeLinks.js';

export type FinanceLoanDirection = 'lent' | 'borrowed';
export type FinanceLoanStatus = 'open' | 'due_soon' | 'overdue' | 'repaid' | 'closed';

/** A transaction linked to a record (a loan, a payslip or a remittance). */
export interface FinanceMovement { link: FinanceLink; posted_on: string | null; description: string | null; account_id: string | null }

export interface FinancePersonLoan {
  id: string;
  direction: FinanceLoanDirection;
  person: string;
  amount: string;
  currency: string;
  lent_on: string;
  due_on: string | null;
  notes: string | null;
  remind: boolean;
  remind_days_before: number;
  closed_on: string | null;
  /** Annual interest in percent (null: interest free) and a monthly schedule (migration 0158_finance_depth). */
  interest_rate: string | null;
  installments: number | null;
  first_due_on: string | null;
  repaid: string;
  /** The amount plus scheduled interest, less what is repaid. */
  outstanding: string;
  handed: string;
  status: FinanceLoanStatus;
  days_until_due: number | null;
  interest: string;
  total_due: string;
  next_due_on: string | null;
  next_due_amount: string;
  overdue_amount: string;
  links: number;
  created_at: string;
  updated_at: string;
}

export type FinanceInstallmentState = 'repaid' | 'part_repaid' | 'overdue' | 'due';

export interface FinanceLoanInstallment { number: number; due_on: string; principal: string; interest: string; amount: string; state: FinanceInstallmentState }

export interface FinancePersonLoanDetail extends FinancePersonLoan { movements: FinanceMovement[]; schedule: FinanceLoanInstallment[] }

export interface FinancePersonLoanInput {
  direction: FinanceLoanDirection;
  person: string;
  amount: string;
  currency?: string | null;
  lent_on: string;
  due_on?: string | null;
  notes?: string | null;
  remind?: boolean;
  remind_days_before?: number;
  closed?: boolean;
  interest_rate?: string | null;
  installments?: number | null;
  first_due_on?: string | null;
}

export interface FinanceOwed { currency: string; they_owe_me: string; i_owe: string; they_owe_me_overdue: string; i_owe_overdue: string }

export interface FinancePersonLoanPage {
  items: FinancePersonLoan[];
  total: number;
  limit: number;
  offset: number;
  owed: FinanceOwed[];
  overdue: number;
  note: string;
}

export type FinanceDeductionKind = 'social_insurance' | 'medical_insurance' | 'loan' | 'advance' | 'tax' | 'other';
export const FINANCE_DEDUCTION_KINDS: readonly FinanceDeductionKind[] = ['social_insurance', 'medical_insurance', 'loan', 'advance', 'tax', 'other'];

export interface FinanceDeduction { kind: FinanceDeductionKind; amount: string; label?: string | null }

export interface FinancePayslip {
  id: string;
  employer: string;
  month: string;
  paid_on: string | null;
  currency: string;
  gross: string;
  deductions: FinanceDeduction[];
  notes: string | null;
  total_deductions: string;
  net: string;
  received: string;
  difference: string;
  status: 'not_linked' | 'matched' | 'differs';
  by_kind: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface FinancePayslipDetail extends FinancePayslip { movements: FinanceMovement[] }

export interface FinancePayslipInput {
  employer: string;
  month: string;
  paid_on?: string | null;
  currency?: string | null;
  gross: string;
  deductions: FinanceDeduction[];
  notes?: string | null;
}

export interface FinancePayMonth {
  month: string;
  currency: string;
  gross: string;
  deductions: string;
  net: string;
  received: string;
  by_kind: Record<string, string>;
  payslips: number;
}

export interface FinancePayslipPage { items: FinancePayslip[]; total: number; limit: number; offset: number; months: FinancePayMonth[] }

export interface FinanceRemittance {
  id: string;
  sent_on: string;
  amount: string;
  currency: string;
  fee: string;
  fx_rate: string | null;
  received_amount: string | null;
  received_currency: string | null;
  recipient: string;
  provider: string;
  regular: boolean;
  notes: string | null;
  total_paid: string;
  fee_percent: string | null;
  effective_rate: string | null;
  linked: string;
  created_at: string;
  updated_at: string;
}

export interface FinanceRemittanceDetail extends FinanceRemittance { movements: FinanceMovement[] }

export interface FinanceRemittanceInput {
  sent_on: string;
  amount: string;
  currency?: string | null;
  fee?: string;
  fx_rate?: string | null;
  received_amount?: string | null;
  received_currency?: string | null;
  recipient: string;
  provider: string;
  regular?: boolean;
  notes?: string | null;
}

export interface FinanceRemitMonth { month: string; currency: string; sent: string; fees: string; count: number; received: Record<string, string> }

export interface FinanceRemitProvider {
  provider: string;
  corridor: string;
  transfers: number;
  average_fee_percent: string | null;
  best_effective_rate: string | null;
  latest_effective_rate: string | null;
  latest_on: string;
}

export interface FinanceRemittancePage {
  items: FinanceRemittance[];
  total: number;
  limit: number;
  offset: number;
  months: FinanceRemitMonth[];
  providers: FinanceRemitProvider[];
}

export type FinanceCommitmentKind = 'rent' | 'subscription' | 'bill' | 'installment' | 'loan_minimum' | 'card_minimum' | 'remittance';

export interface FinanceCommitment {
  kind: FinanceCommitmentKind;
  name: string;
  currency: string;
  monthly: string;
  source: 'bill' | 'installment' | 'debt' | 'person_loan' | 'remittance';
  ref_id: string;
  /** weekly, quarterly, yearly, once, in_bill (counted as the bill), with_fee, due_this_month (money borrowed from a person, due this month). */
  note: string | null;
}

export interface FinanceIncomeShare {
  currency: string;
  /** The one income figure (salary profile, else the records), or null without income. */
  monthly: string | null;
  source: 'salary_profile' | 'records' | null;
  /** Fixed costs as a share of income, rounded; null without income. */
  share_percent: number | null;
}

export interface FinanceCommitments {
  month: string;
  items: FinanceCommitment[];
  not_counted: FinanceCommitment[];
  totals: { currency: string; amount: string }[];
  /** Per currency with fixed costs (older servers leave it out). */
  income?: FinanceIncomeShare[];
  note: string;
}

export interface FinancePayoffDebt {
  id: string; name: string; balance: string; apr_percent: string; minimum: string; avalanche_month: number | null; snowball_month: number | null;
  /** `person_loan`: money borrowed from a person (Lent and borrowed), no interest. Older servers leave it out (a Debt). */
  source?: 'debt' | 'person_loan';
}

export interface FinancePayoffOrder {
  strategy: 'avalanche' | 'snowball'; months: number | null; total_interest: string; order: string[];
  /** The month each debt is paid off (1 = the coming month), or null. */
  paid_off_in: Record<string, number | null>;
  /** Total owed at the end of each of the next 12 months (fewer when paid off sooner). */
  balances: string[];
}

export interface FinancePayoff {
  currency: string;
  payment: string;
  planned_payment: string | null;
  /** Each debt gets only its minimum: nothing rolls over (`?minimums_only=true`). */
  minimums_only: boolean;
  below_minimums: boolean;
  debts: FinancePayoffDebt[];
  avalanche: FinancePayoffOrder;
  snowball: FinancePayoffOrder;
  cheaper: 'avalanche' | 'snowball' | 'same' | null;
  interest_saved: string;
  first_win_months: number | null;
  missing_rates: string[];
}

export interface FinancePayoffPage { currencies: FinancePayoff[]; note: string }

export type FinanceAffordVerdict =
  | 'yes_from_cash' | 'yes_from_savings' | 'cash_leaves_little' | 'monthly_comfortable' | 'monthly_tight' | 'too_much' | 'not_enough_data';

export interface FinanceAffordInput { price: string; currency?: string | null; item?: string | null; monthly_cost?: string | null }

export interface FinanceAfford {
  currency: string;
  item: string | null;
  price: string;
  monthly_cost: string | null;
  safe_to_spend: string | null;
  liquid: string;
  income_monthly: string | null;
  commitments_monthly: string;
  essential_monthly: string | null;
  ratio_now: string | null;
  ratio_after: string | null;
  months_left_after_cash: string | null;
  verdict: FinanceAffordVerdict;
  reasons: string[];
  pay_with: { account_id: string; name: string; kind: string; currency: string; balance: string; note: string }[];
  avoid: { code: string; currency: string | null; params: Record<string, string> }[];
  note: string;
}

export type FinanceOptionKind = 'cash' | 'bank_loan' | 'installments';

export interface FinanceOptionInput {
  kind: FinanceOptionKind;
  label?: string | null;
  months?: number;
  rate_percent?: string | null;
  basis?: 'reducing' | 'flat';
  fees?: string;
  down_payment?: string;
  /** The https address of the live source the rate was read from; leave out for your own rate. */
  rate_source?: string | null;
  rate_read_on?: string | null;
}

export interface FinanceCompareInput { price: string; currency?: string | null; options: FinanceOptionInput[] }

export interface FinanceOption {
  kind: FinanceOptionKind;
  label: string;
  upfront: string | null;
  monthly: string | null;
  months: number;
  total_cost: string | null;
  interest: string | null;
  extra_over_cash: string | null;
  rate_percent: string | null;
  basis: 'reducing' | 'flat';
  rate_source: string;
  rate_read_on: string | null;
  status: 'ok' | 'rate_unknown';
}

export interface FinanceCompare { currency: string; price: string; options: FinanceOption[]; note: string }

export type FinanceResearchTopic = 'price' | 'loan_rate' | 'installments' | 'remittance';

export interface FinanceResearchInput { topic: FinanceResearchTopic; query: string; country?: string | null }

export interface FinanceResearch {
  status: 'ok' | 'unavailable' | 'blocked' | 'failed' | 'not_found';
  query: string;
  read_on: string;
  items: { title: string; url: string; read_on: string; figures: { kind: 'percent' | 'amount'; value: string; currency: string | null }[] }[];
  message: string;
  note: string;
}

// ---- Salary (docs/finance.md#salary) --------------------------------------------------------------

export type FinanceSalaryRuleKind = 'deduction' | 'advance';
export type FinanceSalaryDeductionReason = 'loan' | 'advance' | 'absence' | 'social_insurance' | 'other';

export interface FinanceSalaryRule {
  id: string;
  kind: FinanceSalaryRuleKind;
  reason: FinanceSalaryDeductionReason;
  label: string | null;
  /** Taken off the salary each month. */
  amount: string;
  /** YYYY-MM, the first month. */
  starts: string;
  /** A deduction's last month, or null (until removed). */
  ends: string | null;
  /** An advance: what was received; repaid `amount` a month until it is back. */
  total: string | null;
  received_on: string | null;
}

export interface FinanceSalaryRuleInput {
  id?: string | null; kind: FinanceSalaryRuleKind; reason?: FinanceSalaryDeductionReason; label?: string | null; amount: string;
  starts: string; ends?: string | null; total?: string | null; received_on?: string | null;
}

export interface FinanceSalaryProfile {
  id: string;
  employer: string;
  joined_on: string;
  currency: string;
  expected_gross: string;
  pay_day: number;
  account_id: string | null;
  rules: FinanceSalaryRule[];
  created_at: string;
  updated_at: string;
  /** This month's expected net (a payslip for the month overrides it). */
  expected_net: string;
  next_pay_on: string;
  advances: { rule_id: string; label: string | null; total: string; left: string }[];
}

export interface FinanceSalaryProfileInput {
  employer: string; joined_on: string; currency?: string | null; expected_gross: string; pay_day: number; account_id?: string | null;
  /** Omitted: the saved rules stay. */
  rules?: FinanceSalaryRuleInput[];
}

export type FinanceSalaryStatus = 'received' | 'late' | 'partial' | 'missing' | 'upcoming';

export interface FinanceSalaryMonth {
  month: string;
  currency: string;
  pay_on: string;
  status: FinanceSalaryStatus;
  flags: string[];
  expected: { gross: string; deductions: { reason: string; label: string | null; amount: string }[]; advance_repayment: string; net: string; payslip: boolean };
  received: string;
  /** Received less the expected net. */
  difference: string;
  found: { transaction_id: string; posted_on: string; amount: string; description: string; account_id: string; confidence: 'high' | 'medium' | 'low'; score: number } | null;
  /** confirmed by the person, linked to the payslip, or suggested (waiting for a confirm or reject). */
  basis: 'confirmed' | 'payslip' | 'suggested' | null;
  rejected: number;
}

export interface FinanceSalaryMonthsPage {
  currency: string | null; months: FinanceSalaryMonth[]; total: number; missing: number; waiting: number; limit: number; offset: number;
}

/** One transaction split across several loans, an amount each (docs/finance.md#lent-and-borrowed). */
export interface FinancePersonLoanSplitInput {
  canonical_id: string;
  kind?: 'handed' | 'repayment';
  parts: { loan_id: string; amount: string }[];
}
export interface FinancePersonLoanSplitResult { links: FinanceLink[]; replayed: boolean }

/** A personal trip or project that transactions are tagged to (docs/finance.md#trips-and-projects). */
export interface FinanceTag {
  id: string;
  kind: 'trip' | 'project';
  name: string;
  starts_on: string | null;
  ends_on: string | null;
  keywords: string | null;
  notes: string | null;
  archived: boolean;
  /** What is tagged per currency, never added across currencies. */
  totals: Record<string, string>;
  links: number;
}
export interface FinanceTagDetail extends FinanceTag { movements: FinanceMovement[] }
export interface FinanceTagPage { items: FinanceTag[]; total: number; limit: number; offset: number; note: string }
export interface FinanceTagInput {
  kind: 'trip' | 'project';
  name: string;
  starts_on?: string | null;
  ends_on?: string | null;
  keywords?: string | null;
  notes?: string | null;
  archived?: boolean;
}

/** A foreign-currency balance valued in the base currency (docs/finance.md#foreign-currency-balances). Display only. */
export interface FinanceFxRevaluationRow {
  account_id: string;
  name: string;
  currency: string;
  base_currency: string;
  balance: string;
  status: 'ok' | 'rate_not_available' | 'cost_rate_not_available' | 'nothing_to_revalue';
  value: string | null;
  rate: string | null;
  rate_as_of: string | null;
  rate_source: string | null;
  cost: string | null;
  unrealised: string | null;
  missing_rate_dates: string[];
}
export interface FinanceFxRevaluation {
  base_currency: string;
  as_of: string;
  rate_max_age_days: number;
  items: FinanceFxRevaluationRow[];
  unrealised_total: string | null;
  incomplete: number;
  truncated: boolean;
  note: string;
}
