// The fixed asset register, loan schedules, the period-end revaluation and the group balance sheet of the business books
// (docs/business-books.md#fixed-asset-register, #loan-schedules, #period-end-revaluation, #group-balance-sheet). Amounts are
// exact decimal strings in their currency.

export type DepreciationMethod = 'straight_line' | 'reducing_balance';
export const DEPRECIATION_METHODS: DepreciationMethod[] = ['straight_line', 'reducing_balance'];

export interface FixedAsset {
  id: string;
  name: string;
  category: string;
  source_kind: 'bill' | 'adjustment';
  source_id: string;
  /** False once the bill or entry that bought it is voided: it is then out of the books. */
  source_live: boolean;
  acquired_on: string;
  currency: string;
  cost: string;
  useful_life_months: number | null;
  residual_value: string;
  method: DepreciationMethod;
  rate_pct: string | null;
  depreciation_start: string;
  status: 'active' | 'disposed';
  disposed_on: string | null;
  disposal_kind: 'sale' | 'write_off' | null;
  proceeds: string;
  accumulated_depreciation: string;
  impairment: string;
  net_book_value: string;
  as_of: string;
  gain_or_loss: string | null;
  version: number;
  pending_impairments: number;
}

export interface AssetImpairment {
  id: string;
  asset_id: string;
  entry_date: string;
  amount: string;
  currency: string;
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  version: number;
}

export interface AssetScheduleRow { month: string; amount: string; accumulated: string; state: 'closed' | 'posted' | 'scheduled' }

export interface FixedAssetDetail extends FixedAsset {
  schedule: AssetScheduleRow[];
  impairments: AssetImpairment[];
  can_edit: boolean;
  disposal: Record<string, string> | null;
  closed_through: string | null;
  me: string;
}

export interface AssetRollForward {
  currency: string;
  opening_cost: string; additions: string; disposals_cost: string; closing_cost: string;
  opening_depreciation: string; depreciation: string; impairment: string; disposals_depreciation: string; closing_depreciation: string;
  opening_nbv: string; closing_nbv: string;
}

export interface FixedAssetList {
  items: FixedAsset[];
  total: number;
  limit: number;
  offset: number;
  period: { from: string; to: string };
  as_of: string;
  roll_forward: AssetRollForward[];
  base_currency: string;
  closed_through: string | null;
  can_edit: boolean;
  pending_impairments: number;
}

export interface FixedAssetUpdate {
  name: string;
  category: string;
  useful_life_months: number | null;
  residual_value: string;
  method: DepreciationMethod;
  rate_pct: string | null;
  depreciation_start: string;
  version: number;
}

export type LoanFrequency = 'monthly' | 'quarterly' | 'yearly';
export type LoanMethod = 'annuity' | 'equal_principal';
export type EarlyRepayment = 'reduce_term' | 'reduce_payment';

export interface BusinessLoan {
  id: string;
  lender: string;
  principal: string;
  currency: string;
  annual_rate: string;
  start_date: string;
  term_months: number;
  frequency: LoanFrequency;
  method: LoanMethod;
  early_repayment: EarlyRepayment;
  received_adjustment_id: string | null;
  status: 'active' | 'void';
  version: number;
  payment: string | null;
  periods: number;
  maturity: string | null;
  outstanding: string;
  interest_accrued: string;
  repaid: string | null;
  next_due: string | null;
  next_payment: string | null;
  repayments: number;
  void_reason: string;
}

export interface LoanScheduleRow { number: number; due: string; payment: string; interest: string; principal: string; balance: string }

export interface LoanActualRow extends LoanScheduleRow {
  paid: string;
  paid_interest: string;
  paid_principal: string;
  difference: string;
  state: 'paid' | 'part_paid' | 'overdue' | 'due' | 'upcoming';
}

export interface LoanRepayment {
  id: string;
  loan_id: string;
  paid_on: string;
  amount: string;
  interest: string;
  principal: string;
  currency: string;
  bill_id: string | null;
  adjustment_id: string | null;
  status: 'active' | 'void';
  created_by: string;
  created_at: string;
  void_reason: string;
}

export interface BusinessLoanDetail extends BusinessLoan {
  schedule: LoanScheduleRow[];
  actual: LoanActualRow[];
  revised: LoanScheduleRow[];
  repayment_rows: LoanRepayment[];
  accruals: { month_end: string; amount: string }[];
  can_edit: boolean;
}

export interface LoanMatchOption { id: string; date: string; amount: string; currency: string; description: string; reference?: string }

export interface BusinessLoanList {
  items: BusinessLoan[];
  total: number;
  limit: number;
  offset: number;
  base_currency: string;
  can_edit: boolean;
  match: { received: LoanMatchOption[]; repayments: LoanMatchOption[]; bills: LoanMatchOption[] };
}

export interface BusinessLoanInput {
  lender: string;
  principal: string;
  currency: string;
  annual_rate: string;
  start_date: string;
  term_months: number;
  frequency: LoanFrequency;
  method: LoanMethod;
  early_repayment: EarlyRepayment;
  received_adjustment_id: string | null;
}

export interface LoanRepaymentInput { paid_on: string; amount: string; bill_id: string | null; adjustment_id: string | null }

export interface RevaluationAccount {
  account_id: string;
  code: string;
  name: string;
  system_key: string | null;
  original: string;
  booked: string;
  closing: string | null;
  difference: string | null;
}

export interface RevaluationCurrency {
  currency: string;
  status: 'revalued' | 'unchanged' | 'missing_rate' | 'nothing_to_revalue' | 'to_revalue' | 'ready';
  rate: string | null;
  rate_date: string | null;
  source: string | null;
  difference: string;
  accounts: RevaluationAccount[];
}

export interface RevaluationPreview {
  period_end: string;
  base_currency: string | null;
  currencies: RevaluationCurrency[];
  closed_through: string | null;
  can_edit: boolean;
  ended: boolean;
}

export interface RevaluationResult {
  period_end: string;
  base_currency: string;
  currencies: RevaluationCurrency[];
  posted: number;
  reversed: number;
  missing: string[];
}

export type GroupBalanceRow =
  | 'current_assets' | 'non_current_assets' | 'total_assets' | 'current_liabilities' | 'non_current_liabilities' | 'total_liabilities' | 'capital'
  | 'retained_earnings' | 'translation_reserve' | 'intercompany_difference' | 'total_equity' | 'liabilities_and_equity';

export interface GroupBalanceSheet {
  enough: boolean;
  as_of: string;
  skipped: { workspace_id: string; name: string; reason: string }[];
  available: { workspace_id: string; name: string }[];
  currency?: string;
  rows?: { key: GroupBalanceRow; businesses: Record<string, string>; eliminations: string; total: string }[];
  included?: string[];
  not_converted?: { workspace_id: string; name: string; currency: string; missing: { currency: string; day: string; what: string }[]; missing_days: number }[];
  rates?: { workspace_id: string; from_currency: string; to_currency: string; rate: string; as_of: string; source: string }[];
  balanced?: boolean;
  difference?: string;
  complete?: boolean;
  businesses?: { workspace_id: string; name: string; base_currency: string; complete: boolean; notes: string[]; included: boolean; translated: boolean }[];
}
