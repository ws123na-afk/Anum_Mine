// Business books (docs/business-books.md#api), mirror of services/api/anum_api/business/api_books.py.
// The books extend Business accounting: revenue is the business's own invoices, payments and credit notes;
// costs are vendor bills and the allocation rows members made from their own Finance ledger. Nothing here
// posts money. Amounts are exact decimal strings in their own currency; figures in the base currency are
// converted only with a dated rate whose source is listed, else they stay apart as "not converted".
import type { TaxCode } from './businessTax.js';

export type BooksSection = 'cost_of_sales' | 'operating_expense' | 'capital';
export type BooksTagSection = BooksSection | 'excluded';
export type BooksBasis = 'accrual' | 'cash';
export type BooksRecurrence = 'monthly' | 'quarterly' | 'yearly';
export type BooksPaymentKind = 'recorded' | 'allocation';
export type BooksPaymentMethod = 'allocation' | 'bank_transfer' | 'cash' | 'card' | 'cheque' | 'other';
export type BooksPaymentState = 'unpaid' | 'partly_paid' | 'paid' | 'void' | 'draft';
export type BooksBillFilter = 'open' | 'unpaid' | 'partly_paid' | 'paid' | 'overdue' | 'void' | 'recurring' | 'draft';
export type BooksAdjustmentKind =
  | 'accrual' | 'prepayment' | 'capital_item' | 'loan_received' | 'loan_repayment' | 'loan_interest'
  | 'owner_contribution' | 'owner_drawing' | 'owner_reimbursement' | 'loan_interest_accrued' | 'loan_interest_paid';
export type BooksIntercompanyKind = 'recharge' | 'loan' | 'settlement';
export type BooksDirection = 'receivable' | 'payable';
export type BooksAllocationFilter = 'active' | 'needs_review' | 'unpaid_bill' | 'untagged';

export const BOOKS_SECTIONS: BooksSection[] = ['cost_of_sales', 'operating_expense', 'capital'];
export const BOOKS_BASES: BooksBasis[] = ['accrual', 'cash'];
export const BOOKS_RECURRENCES: BooksRecurrence[] = ['monthly', 'quarterly', 'yearly'];
export const BOOKS_PAYMENT_METHODS: BooksPaymentMethod[] = ['bank_transfer', 'cash', 'card', 'cheque', 'other'];
export const BOOKS_BILL_FILTERS: BooksBillFilter[] = ['open', 'overdue', 'unpaid', 'partly_paid', 'paid', 'recurring', 'draft', 'void'];
/** How often a recurring bill repeats: monthly, quarterly, yearly, or every N days, weeks or months. */
export type BooksFrequency = 'monthly' | 'quarterly' | 'yearly' | 'days' | 'weeks' | 'months';
export const BOOKS_FREQUENCIES: BooksFrequency[] = ['monthly', 'quarterly', 'yearly', 'days', 'weeks', 'months'];
export type BooksTemplateStatus = 'active' | 'paused' | 'ended';
export type BooksRateOrigin = 'manual' | 'ecb';
export const BOOKS_ADJUSTMENT_KINDS: BooksAdjustmentKind[] = [
  'accrual', 'prepayment', 'capital_item', 'loan_received', 'loan_repayment', 'loan_interest', 'loan_interest_accrued', 'loan_interest_paid',
  'owner_contribution', 'owner_drawing', 'owner_reimbursement',
];
export const BOOKS_INTERCOMPANY_KINDS: BooksIntercompanyKind[] = ['recharge', 'loan', 'settlement'];

export interface BooksPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface BooksAmount {
  currency: string;
  amount: string;
}

export interface BooksSettings {
  id: string;
  base_currency: string;
  country: string | null;
  fiscal_year_start_month: number;
  basis: BooksBasis;
  tax_label: string;
  category_sections: Record<string, BooksTagSection>;
  rate_max_age_days: number;
  /** The owner's own accounting policy text (at most 1,000 characters), shown with the generated policy notes. */
  policy_note?: string;
  /** Bills and adjustments at or above this (base currency) need a supporting document; null keeps the flag off. */
  evidence_threshold?: string | null;
  /** An accountant's adjustment at or above this (base currency) waits for another owner or accountant; null: no step. */
  adjustment_approval_threshold?: string | null;
  version: number;
  updated_by: string;
  updated_at: string;
  created_at: string;
}

export interface BooksSettingsInput {
  base_currency: string;
  country: string | null;
  fiscal_year_start_month: number;
  basis: BooksBasis;
  tax_label: string;
  category_sections: Record<string, BooksTagSection>;
  rate_max_age_days: number;
  policy_note?: string;
  evidence_threshold?: string | null;
  adjustment_approval_threshold?: string | null;
  version: number | null;
}

export interface BooksCostCentre {
  id: string;
  code: string;
  name: string;
  archived: boolean;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface BooksSetup {
  settings: BooksSettings | null;
  /** ``account``: owners and accountants (adjustments, the journal, closing periods, approving others' draft bills). */
  permissions: { read: boolean; write: boolean; manage: boolean; accountant?: boolean; account?: boolean };
  /** The last day of the last closed period, or null. */
  closed_through?: string | null;
  /** The person asking (a pending adjustment they recorded is approved by someone else). */
  user_id?: string;
  counts: Record<'vendors' | 'bills' | 'cost_centres' | 'budgets' | 'rates' | 'adjustments' | 'intercompany' | 'allocations', number>;
  default_period: { from: string; to: string } | null;
  cost_centres: BooksCostCentre[];
  allocation_categories: string[];
  sections: BooksSection[];
  adjustment_kinds: BooksAdjustmentKind[];
}

export interface BooksVendor {
  id: string;
  name: string;
  tax_number: string;
  country: string | null;
  default_category: string | null;
  default_section: BooksSection | null;
  notes: string;
  archived: boolean;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface BooksVendorInput {
  name: string;
  tax_number: string;
  country: string | null;
  default_category: string | null;
  default_section: BooksSection | null;
  notes: string;
}

export interface BooksBillPayment {
  id: string;
  bill_id: string;
  kind: BooksPaymentKind;
  allocation_id: string | null;
  amount: string;
  currency: string;
  paid_on: string;
  method: BooksPaymentMethod;
  reference: string;
  note: string;
  recorded_by: string;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
  created_at: string;
  counts: boolean;
  allocation_removed: boolean;
}

export interface BooksBill {
  id: string;
  vendor_id: string;
  vendor_name: string;
  reference: string;
  description: string;
  bill_date: string;
  due_date: string;
  currency: string;
  subtotal: string;
  tax: string;
  total: string;
  category: string;
  section: BooksSection;
  cost_centre_id: string | null;
  project_id: string | null;
  recurrence: BooksRecurrence | null;
  useful_life_months: number | null;
  /** docs/business-tax.md#tax-codes; null for a bill recorded before tax codes ("unknown" in the VAT return). */
  tax_code?: TaxCode | null;
  /** VAT the business accounts for itself on a reverse-charge purchase. */
  reverse_charge_tax?: string;
  status: 'open' | 'void' | 'draft';
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
  /** Made by a recurring bill, for this period. */
  template_id: string | null;
  period_date: string | null;
  amount_paid: string;
  balance: string;
  payment_state: BooksPaymentState;
  overdue: boolean;
  days_overdue: number;
  can: { edit: boolean; edit_money: boolean; pay: boolean; void: boolean; approve: boolean };
  payments?: BooksBillPayment[];
  replayed?: boolean;
}

export interface BooksBillPage extends BooksPage<BooksBill> {
  summary: { open: BooksAmount[]; overdue: BooksAmount[] };
}

export interface BooksBillInput {
  vendor_id: string;
  reference: string;
  description: string;
  bill_date: string;
  due_date: string | null;
  currency: string;
  subtotal: string;
  tax: string;
  category: string | null;
  section: BooksSection | null;
  cost_centre_id: string | null;
  project_id: string | null;
  recurrence: BooksRecurrence | null;
  useful_life_months: number | null;
  tax_code?: TaxCode | null;
}

export interface BooksPaymentInput {
  kind: BooksPaymentKind;
  amount?: string | null;
  paid_on?: string | null;
  method?: BooksPaymentMethod;
  reference?: string;
  note?: string;
  allocation_id?: string | null;
}

export interface BooksAllocationTag {
  id: string;
  allocation_id: string;
  cost_centre_id: string | null;
  section: BooksTagSection | null;
  note: string;
  version: number;
  updated_by: string;
  updated_at: string;
  created_at: string;
}

/** An allocation row as the books count it: never the member's bank, card, merchant text or message. */
export interface BooksAllocation {
  id: string;
  source_ref: string;
  posted_on: string;
  amount: string;
  currency: string;
  category: string;
  project_id: string | null;
  memo: string | null;
  treatment: 'owner_contribution' | 'reimbursable';
  status: 'active' | 'needs_review';
  allocated_by: string;
  section: BooksTagSection;
  tag: BooksAllocationTag | null;
  paying_bill_id: string | null;
}

export interface BooksBudget {
  id: string;
  name: string;
  period_start: string;
  months: 1 | 3 | 6 | 12;
  currency: string;
  amount: string;
  section: BooksSection | null;
  category: string | null;
  project_id: string | null;
  cost_centre_id: string | null;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface BooksBudgetInput {
  name: string;
  period_start: string;
  months: 1 | 3 | 6 | 12;
  amount: string;
  section: BooksSection | null;
  category: string | null;
  project_id: string | null;
  cost_centre_id: string | null;
}

export interface BooksRate {
  id: string;
  base: string;
  quote: string;
  rate: string;
  as_of: string;
  source: string;
  created_by: string;
  created_at: string;
  /** Typed by an owner, or fetched from the ECB by the daily job. */
  origin: BooksRateOrigin;
}

export interface BooksRateInput {
  base: string;
  quote: string;
  rate: string;
  as_of: string;
  source: string;
}

export interface BooksAdjustment {
  id: string;
  kind: BooksAdjustmentKind;
  entry_date: string;
  currency: string;
  amount: string;
  section: BooksSection | null;
  category: string | null;
  cost_centre_id: string | null;
  project_id: string | null;
  description: string;
  start_month: string | null;
  months: number | null;
  reverse_on: string | null;
  /** pending: an accountant's adjustment above the approval threshold, not posted until another owner or accountant approves it. */
  status: 'active' | 'void' | 'pending';
  created_by: string;
  created_at: string;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
  approved_by?: string | null;
  approved_at?: string | null;
  replayed?: boolean;
}

/** A receipt in the Document vault proposed for a bill without evidence (GET /evidence/receipt-matches). */
export interface BooksReceiptMatch {
  document_id: string;
  document_title: string;
  bill_id: string;
  bill_reference: string;
  vendor: string;
  merchant: string;
  bill_date: string;
  receipt_date: string;
  amount: string;
  currency: string;
  vendor_agrees: boolean;
  confidence: string;
}

/** A receipt dismissed for one bill (POST /evidence/receipt-matches/dismiss): that pair is never proposed again until restored. */
export interface BooksReceiptDismissal {
  id: string;
  bill_id: string;
  document_id: string;
  dismissed_by: string;
  dismissed_at: string;
  bill_reference: string;
  vendor: string;
  bill_date: string | null;
  amount: string | null;
  currency: string | null;
  document_title: string;
  bill_found: boolean;
  document_found: boolean;
}

export interface BooksReceiptDismissalPage {
  items: BooksReceiptDismissal[];
  total: number;
  limit: number;
  offset: number;
}

export interface BooksAdjustmentInput {
  kind: BooksAdjustmentKind;
  entry_date: string;
  currency: string;
  amount: string;
  section: 'cost_of_sales' | 'operating_expense' | null;
  category: string | null;
  cost_centre_id: string | null;
  project_id: string | null;
  description: string;
  start_month: string | null;
  months: number | null;
  reverse_on: string | null;
}

export interface BooksIntercompany {
  id: string;
  pair_id: string;
  counterparty_workspace_id: string;
  counterparty_name: string;
  kind: BooksIntercompanyKind;
  direction: BooksDirection;
  entry_date: string;
  currency: string;
  amount: string;
  description: string;
  status: 'active' | 'void';
  created_by: string;
  created_at: string;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
}

export interface BooksIntercompanyInput {
  workspace_id: string;
  kind: BooksIntercompanyKind;
  direction: BooksDirection;
  entry_date: string;
  currency: string;
  amount: string;
  description: string;
}

export interface BooksCounterparty {
  workspace_id: string;
  name: string;
}

export interface BooksLine {
  key: string;
  amount: string;
}

export interface BooksStatementGroup {
  lines: BooksLine[];
  total: string;
}

export interface BooksStatement {
  currency: string;
  revenue: BooksStatementGroup;
  cost_of_sales: BooksStatementGroup;
  gross_profit: string;
  gross_margin_pct: string | null;
  operating_expenses: BooksStatementGroup;
  operating_profit: string;
  other_income: BooksStatementGroup;
  finance_costs: BooksStatementGroup;
  fx_differences: string;
  net_profit: string;
  net_margin_pct: string | null;
  not_converted: { currency: string; groups: Record<string, string> }[];
  complete: boolean;
}

export interface BooksAged {
  currency: string;
  total: string;
  current?: string;
  days_1_30?: string;
  days_31_60?: string;
  days_61_90?: string;
  days_over_90?: string;
}

export interface BooksBreakdown {
  id: string;
  name: string;
  revenue: string;
  costs: string;
  margin: string;
  margin_pct: string | null;
  /** False when some figures could not be converted (listed per currency): never read as a zero. */
  complete?: boolean;
  not_converted?: { currency: string; net: string }[];
  recorded?: boolean;
}

export interface BooksConversion {
  from_currency: string;
  to_currency: string;
  rate: string;
  as_of: string;
  source: string;
  via: string | null;
  figures: number;
}

export interface BooksReport {
  period: { from: string; to: string; basis: BooksBasis };
  base_currency: string;
  pnl: BooksStatement;
  tax: {
    label: string;
    country: string | null;
    by_currency: { currency: string; output: string; input: string; net: string }[];
    allocations_without_tax_split: number;
  };
  cash_flow: {
    lines: Record<string, BooksAmount[]>;
    net: BooksAmount[];
    paid_by_members: Record<string, BooksAmount[]>;
  };
  balances: {
    as_of: string;
    aged_on: string;
    receivables: BooksAged[];
    outstanding_invoices: { invoice_id: string; number: string | null; account_id: string; account_name: string; due_date: string | null;
      days_overdue: number; currency: string; balance: string }[];
    outstanding_invoices_count: number;
    payables: BooksAged[];
    unpaid_bills: { bill_id: string; vendor_name: string; reference: string; due_date: string; days_overdue: number; currency: string; balance: string }[];
    unpaid_bills_count: number;
    owed_to_members: BooksAmount[];
    debt: BooksAmount[];
    interest_payable?: BooksAmount[];
    intercompany: { workspace_id: string; name: string; currency: string; amount: string }[];
  };
  equity: { contributions: BooksAmount[]; drawings: BooksAmount[] };
  /** Exchange differences are apart from revenue (``fx_differences``); the profit includes them. */
  trend: { month: string; revenue: string; costs: string; fx_differences?: string; profit: string; unconverted: boolean }[];
  customers: BooksBreakdown[];
  projects: BooksBreakdown[];
  cost_centres: BooksBreakdown[];
  customers_total?: number;
  projects_total?: number;
  cost_centres_total?: number;
  budgets: {
    id: string; name: string; period_start: string; period_end: string; currency: string; budget: string; actual: string | null; variance: string | null;
    used_pct: string | null; comparable: boolean; unconverted?: boolean;
  }[];
  recurring: {
    /** A recurring bill (``template_id``) or the newest bill only marked recurring (``bill_id``), with its cost per month. */
    items: { bill_id: string | null; template_id: string | null; vendor_id: string; vendor_name: string; category: string;
      recurrence: BooksFrequency | null; interval: number; currency: string; amount: string; per_month: string; last_bill_date: string | null;
      next_date: string | null; status: BooksTemplateStatus | null }[];
    per_month: BooksAmount[];
    upcoming: BooksUpcomingBill[];
  };
  allocations_in_review: number;
  conversions: BooksConversion[];
  notes: string[];
  truncated: boolean;
  /** Bills and adjustments above the evidence threshold with no document attached (note ``evidence_missing``). */
  evidence_missing?: { enabled: boolean; count: number; threshold: string | null };
}

export interface BooksProjectRow {
  id: string;
  name: string;
  account_id: string;
  status: string;
  currency: string;
  books: { revenue: string; costs: string; margin: string; margin_pct: string | null };
  contract: { revenue: number | null; contract_total: number | null; cash_collected: number | null; outstanding: number | null;
    gross_margin: number | null; gross_margin_pct: number | null; currency: string } | null;
}

export interface BooksProjectPage extends BooksPage<BooksProjectRow> {
  base_currency: string;
  period: { from: string; to: string; basis: BooksBasis };
}

// ---- recurring bills ----------------------------------------------------------------------------------------------------

export interface BooksUpcomingBill {
  template_id: string;
  vendor_name: string;
  description: string;
  bill_date: string;
  due_date: string;
  currency: string;
  total: string;
  create_as: 'open' | 'draft';
}

export interface BooksTemplate {
  id: string;
  vendor_id: string;
  vendor_name: string;
  reference: string;
  description: string;
  currency: string;
  subtotal: string;
  tax: string;
  total: string;
  category: string;
  section: BooksSection;
  cost_centre_id: string | null;
  project_id: string | null;
  useful_life_months: number | null;
  frequency: BooksFrequency;
  interval: number;
  start_date: string;
  due_days: number;
  end_date: string | null;
  max_count: number | null;
  create_as: 'open' | 'draft';
  status: BooksTemplateStatus;
  anchor_date: string;
  next_index: number;
  next_date: string | null;
  created_count: number;
  last_period: string | null;
  end_reason: '' | 'owner' | 'end_date' | 'count';
  ended_at: string | null;
  version: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
  per_month: string;
  upcoming: string[];
  can: { edit: boolean; pause: boolean; resume: boolean; end: boolean };
  bills?: BooksBill[];
  bills_total?: number;
}

export interface BooksTemplatePage extends BooksPage<BooksTemplate> {
  upcoming: BooksUpcomingBill[];
}

export interface BooksTemplateInput {
  vendor_id: string;
  reference: string;
  description: string;
  currency: string;
  subtotal: string;
  tax: string;
  category: string | null;
  section: BooksSection | null;
  cost_centre_id: string | null;
  project_id: string | null;
  useful_life_months: number | null;
  frequency: BooksFrequency;
  interval: number;
  start_date: string;
  due_days: number;
  end_date: string | null;
  max_count: number | null;
  create_as: 'open' | 'draft';
}

// ---- automatic exchange rates ---------------------------------------------------------------------------------------------

export interface BooksRateFeed {
  id?: string;
  enabled: boolean;
  currencies: string[];
  /** The currencies the job stores a rate for: the ones chosen and the base currency (never EUR, the rates are per euro). */
  followed: string[];
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_as_of: string | null;
  last_error: string;
  unavailable: string[];
  version: number | null;
  source: string;
  source_url: string;
  can_manage: boolean;
  added?: number;
}

// ---- consolidated books ---------------------------------------------------------------------------------------------------

export type BooksGroupPnlRow = 'revenue' | 'cost_of_sales' | 'gross_profit' | 'operating_expenses' | 'operating_profit' | 'other_income' | 'finance_costs'
  | 'fx_differences' | 'net_profit';
export type BooksGroupBalanceRow = 'receivables' | 'payables' | 'owed_to_members' | 'debt' | 'intercompany_receivable' | 'intercompany_payable';

export interface BooksGroupRow {
  key: string;
  /** Workspace id to the amount in the reporting currency. */
  businesses: Record<string, string>;
  eliminations: string;
  total: string;
}

export interface BooksGroupMissing {
  /** The business, or null for what the total still lacks. */
  workspace_id: string | null;
  row: string;
  currency: string;
  amount: string;
}

export interface BooksGroupBusiness {
  workspace_id: string;
  name: string;
  base_currency: string;
  notes?: string[];
}

export interface BooksGroup {
  enough: boolean;
  businesses: BooksGroupBusiness[];
  skipped: { workspace_id: string; name: string; reason: 'books_not_set_up' }[];
  /** Businesses with books left out of a default view because more exist than one view holds (``limit``); the owner chooses. */
  excluded: { workspace_id: string; name: string; reason: 'limit' }[];
  limit: number;
  /** Every business the caller owns whose books are set up (the choices). */
  available: { workspace_id: string; name: string }[];
  period?: { from: string; to: string; basis: BooksBasis };
  currency?: string;
  as_of?: string;
  pnl?: BooksGroupRow[];
  balances?: BooksGroupRow[];
  cash_flow?: BooksGroupRow[];
  not_converted?: { pnl: BooksGroupMissing[]; balances: BooksGroupMissing[]; cash: BooksGroupMissing[] };
  complete?: boolean;
  conversions?: (BooksConversion & { workspace_id: string })[];
  eliminated?: boolean;
}


// ---- the journal (docs/business-books.md#the-journal) ------------------------------------------------------------------

export type LedgerAccountType = 'asset' | 'liability' | 'equity' | 'revenue' | 'expense';
export type LedgerSubtype =
  | 'bank' | 'receivable' | 'tax_receivable' | 'prepayment' | 'current_asset' | 'fixed_asset' | 'accumulated_depreciation'
  | 'payable' | 'tax_payable' | 'accrual' | 'interest_payable' | 'owed_to_members' | 'loan' | 'current_liability'
  | 'capital' | 'drawings' | 'retained_earnings' | 'current_result'
  | 'revenue' | 'other_income' | 'cost_of_sales' | 'operating_expense' | 'depreciation' | 'finance_cost' | 'fx';
export const LEDGER_SUBTYPES: LedgerSubtype[] = [
  'bank', 'receivable', 'tax_receivable', 'prepayment', 'current_asset', 'fixed_asset', 'accumulated_depreciation',
  'payable', 'tax_payable', 'accrual', 'interest_payable', 'owed_to_members', 'loan', 'current_liability',
  'capital', 'drawings', 'retained_earnings', 'current_result', 'revenue', 'other_income', 'cost_of_sales', 'operating_expense', 'depreciation',
  'finance_cost', 'fx',
];
export type LedgerSourceKind =
  | 'invoice' | 'credit_note' | 'invoice_payment' | 'invoice_refund' | 'bill' | 'bill_payment' | 'allocation' | 'adjustment' | 'intercompany' | 'manual' | 'closing'
  | 'asset' | 'loan' | 'loan_repayment' | 'revaluation';
export const LEDGER_SOURCE_KINDS: LedgerSourceKind[] = [
  'invoice', 'credit_note', 'invoice_payment', 'invoice_refund', 'bill', 'bill_payment', 'allocation', 'adjustment', 'intercompany', 'manual', 'closing',
  'asset', 'loan', 'loan_repayment', 'revaluation',
];

export interface LedgerAccount {
  id: string;
  code: string;
  name: string;
  subtype: LedgerSubtype;
  type: LedgerAccountType;
  /** The account a posting rule uses: renamed or renumbered, never archived. */
  system_key: string | null;
  category: string | null;
  archived: boolean;
  version: number;
}

export interface LedgerAccountList {
  items: LedgerAccount[];
  subtypes: Record<LedgerSubtype, LedgerAccountType>;
  can_edit: boolean;
}

export interface LedgerAccountInput {
  code: string;
  name: string;
  subtype: LedgerSubtype;
  category: string | null;
}

export interface LedgerLine {
  id: string;
  entry_id: string;
  line_no: number;
  entry_date: string;
  source_kind: LedgerSourceKind;
  account_id: string;
  account_code?: string;
  account_name?: string;
  account_key?: string | null;
  debit: string;
  credit: string;
  /** The original currency and signed amount, with the rate, its day and its source when it was converted. */
  currency: string;
  amount: string;
  rate: string | null;
  rate_date: string | null;
  rate_source: string | null;
  category: string | null;
  project_id: string | null;
  cost_centre_id: string | null;
  flow: string | null;
  counterparty_workspace_id: string | null;
  pair_id: string | null;
}

export interface LedgerEntry {
  id: string;
  entry_date: string;
  event_date: string;
  source_kind: LedgerSourceKind;
  source_id: string | null;
  description: string;
  status: 'posted' | 'reversed';
  reverses_id: string | null;
  reversed_by_id: string | null;
  /** Dated in a closed period, so posted on the first open day. */
  late: boolean;
  created_by: string;
  created_at: string;
  can_reverse: boolean;
  lines: LedgerLine[];
  total: string;
  /** Where its supporting documents are attached (its bill, invoice, adjustment or itself), and those the caller can see. */
  evidence_source?: { type: BooksEvidenceSource; id: string } | null;
  evidence?: BooksEvidenceDocument[];
}

export interface LedgerJournalPage extends BooksPage<LedgerEntry> {
  base_currency: string;
  can_account: boolean;
}

export interface LedgerManualInput {
  entry_date: string;
  description: string;
  lines: { account_id: string; debit: string; credit: string }[];
}

export interface LedgerIssue {
  event_key: string;
  source_kind: LedgerSourceKind;
  source_id: string | null;
  event_date: string;
  /** ``missing_rate``: no exchange rate for that currency and day (ANUM never guesses one); ``too_small``;
   * ``missing_closing_rate``: no rate on a month end, so that currency is not revalued (docs/business-books.md#period-end-revaluation). */
  reason: 'missing_rate' | 'too_small' | 'missing_closing_rate';
  currency: string | null;
  rate_date: string | null;
  description: string;
}

export interface LedgerStatus {
  to_post: number;
  to_reverse: number;
  issues: number;
  issue_rows: LedgerIssue[];
  scheduled: number;
  truncated: boolean;
  complete: boolean;
  closed_through: string | null;
  can_post: boolean;
  can_account: boolean;
  can_reopen: boolean;
}

export interface LedgerPostResult {
  posted: number;
  reversed: number;
  late: number;
  issues: number;
  issue_rows: LedgerIssue[];
  scheduled: number;
  unchanged: number;
  truncated: boolean;
}

export interface LedgerGeneralLine extends LedgerLine {
  description: string;
  source_id: string | null;
  late: boolean;
  reversal: boolean;
  balance: string;
}

export interface LedgerGeneral {
  account: LedgerAccount;
  base_currency: string;
  period: { from: string; to: string };
  opening: string;
  closing: string;
  items: LedgerGeneralLine[];
  total: number;
  limit: number;
  offset: number;
}

export interface LedgerRow {
  account_id: string | null;
  code: string;
  name: string;
  subtype: string;
  system_key: string | null;
  amount: string;
}

export interface LedgerBlock {
  rows: LedgerRow[];
  total: string;
}

export interface LedgerTrialRow {
  account_id: string;
  code: string;
  name: string;
  system_key: string | null;
  subtype: LedgerSubtype;
  type: LedgerAccountType;
  opening_debit: string;
  opening_credit: string;
  debit: string;
  credit: string;
  closing_debit: string;
  closing_credit: string;
}

export interface LedgerPnl {
  currency: string;
  revenue: LedgerBlock;
  cost_of_sales: LedgerBlock;
  gross_profit: string;
  gross_margin_pct: string | null;
  operating_expenses: LedgerBlock;
  operating_profit: string;
  other_income: LedgerBlock;
  finance_costs: LedgerBlock;
  fx_differences: string;
  net_profit: string;
  net_margin_pct: string | null;
}

export interface LedgerBalanceSheet {
  currency: string;
  as_of: string;
  assets: { current: LedgerBlock; non_current: LedgerBlock; total: string };
  liabilities: { current: LedgerBlock; non_current: LedgerBlock; total: string };
  equity: LedgerBlock;
  liabilities_and_equity: string;
  /** Assets equal liabilities plus equity. */
  balanced: boolean;
  difference: string;
  /** False while anything dated on or before the day is not posted. */
  complete: boolean;
}

export interface LedgerCashActivity {
  lines: { key: string; amount: string }[];
  total: string;
}

export interface LedgerStatements {
  period: { from: string; to: string };
  base_currency: string;
  complete: boolean;
  pending: Omit<LedgerStatus, 'closed_through' | 'can_post' | 'can_account' | 'can_reopen'>;
  notes: string[];
  evidence_missing?: { enabled: boolean; count: number; threshold: string | null };
  late_entries: number;
  closed_through: string | null;
  trial_balance: { rows: LedgerTrialRow[]; totals: Record<string, string>; balanced: boolean; difference: string };
  balance_sheet: LedgerBalanceSheet;
  pnl: LedgerPnl;
  /** The journal's P&L next to the P&L derived from the records (accrual), line by line. */
  reconciliation: { rows: { key: string; journal: string; derived: string; difference: string; explained?: boolean }[]; agrees: boolean; to: string } | null;
  /** Unrealised exchange differences of the period-end revaluation in the period (only in the journal). */
  unrealised_fx?: string;
  equity: { currency: string; rows: { key: string; capital: string; drawings: string; retained_earnings: string; result: string; total: string }[];
    balanced: boolean };
  cash_flow: { by_currency: { currency: string; opening: string; operating: LedgerCashActivity; investing: LedgerCashActivity;
    financing: LedgerCashActivity; net: string; closing: string; reconciles: boolean }[]; base_currency: string; base: Record<string, string> };
  comparatives: Record<string, { from: string; to: string; pnl: LedgerPnl; balance_sheet: LedgerBalanceSheet }>;
  policies: { key: string; text: string }[];
}

export interface LedgerClose {
  id: string;
  period_start: string;
  period_end: string;
  kind: 'month' | 'year';
  status: 'closed' | 'reopened';
  closed_by: string;
  closed_at: string;
  closing_entry_id: string | null;
  reopened_by: string | null;
  reopened_at: string | null;
  reopen_reason: string;
}

export interface LedgerCloseStatus {
  closed_through: string | null;
  next: { through: string; year_end: boolean; ended: boolean; ready: boolean; issues: number; issue_rows: LedgerIssue[]; drafts: number;
    allocations_in_review: number; adjustments_pending?: number; to_post: number };
  history: LedgerClose[];
  history_total: number;
  fiscal_year_start_month: number;
  can_close: boolean;
  can_reopen: boolean;
}

export interface LedgerAccountant {
  user_id: string;
  added_by: string;
  added_at: string;
}

// ---- Accountant review (docs/business-books.md#accountant-review) ------------------------------------------------

export type BooksReviewKind = 'draft_bill' | 'adjustment_approval' | 'posting_issue' | 'unconverted' | 'allocation_review' | 'vat' | 'receipt_match' | 'external';
export const BOOKS_REVIEW_KINDS: readonly BooksReviewKind[] = ['draft_bill', 'adjustment_approval', 'unconverted', 'posting_issue', 'allocation_review', 'vat',
  'receipt_match', 'external'];

/** One thing waiting on an owner or an accountant, with the Books section where it is acted on. */
export interface BooksReviewItem {
  key: string;
  producer: string;
  kind: BooksReviewKind;
  detected: string;
  params: Record<string, string>;
  /** blocking: the month cannot close until it is resolved. */
  severity: 'blocking' | 'warning';
  occurred_on: string | null;
  amount: string | null;
  currency: string | null;
  ref_id: string | null;
  section: string;
  action: 'approve' | 'link' | 'open';
  can_act: boolean;
  /** own_draft / own_adjustment: this person made it, so someone else approves it. */
  why_not: string | null;
}

export interface BooksReviewMonth {
  month: string;
  start: string;
  end: string;
  status: 'closed' | 'open' | 'not_ended';
  issues: number;
  drafts: number;
  allocations_in_review: number;
  adjustments_pending?: number;
  ready: boolean;
}

export interface BooksReview {
  items: BooksReviewItem[];
  total: number;
  limit: number;
  offset: number;
  counts: Partial<Record<BooksReviewKind, number>>;
  blocking: number;
  close_months: BooksReviewMonth[];
  closed_through: string | null;
  to_post: number;
  producers: string[];
  unavailable: string[];
}

// -- supporting documents and the evidence pack (docs/business-books.md#supporting-documents-and-evidence) -------------

export type BooksEvidenceSource = 'bill' | 'invoice' | 'adjustment' | 'journal';

/** A Document vault document attached to a bill, an invoice, an adjustment or a manual journal entry. */
export interface BooksEvidenceDocument {
  link_id: string;
  document_id: string;
  title: string;
  category: string;
  added_by: string;
  added_at: string;
}

export interface BooksEvidencePage {
  items: Record<string, { documents: BooksEvidenceDocument[]; evidence_missing: boolean }>;
  can_attach: boolean;
}

export interface BooksEvidenceMissing {
  enabled: boolean;
  threshold: string | null;
  currency: string | null;
  count: number;
  items: { type: 'bill' | 'adjustment'; id: string; date: string; currency: string; amount: string; base_amount: string | null;
    not_converted: boolean; reference: string }[];
  period?: { from: string; to: string };
}
