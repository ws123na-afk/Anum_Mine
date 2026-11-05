// Business books (docs/business-books.md#api), mirror of services/api/anum_api/business/api_books.py.
// The books extend Business accounting: revenue is the business's own invoices, payments and credit notes;
// costs are vendor bills and the allocation rows members made from their own Finance ledger. Nothing here
// posts money. Amounts are exact decimal strings in their own currency; figures in the base currency are
// converted only with a dated rate whose source is listed, else they stay apart as "not converted".

export type BooksSection = 'cost_of_sales' | 'operating_expense' | 'capital';
export type BooksTagSection = BooksSection | 'excluded';
export type BooksBasis = 'accrual' | 'cash';
export type BooksRecurrence = 'monthly' | 'quarterly' | 'yearly';
export type BooksPaymentKind = 'recorded' | 'allocation';
export type BooksPaymentMethod = 'allocation' | 'bank_transfer' | 'cash' | 'card' | 'cheque' | 'other';
export type BooksPaymentState = 'unpaid' | 'partly_paid' | 'paid' | 'void';
export type BooksBillFilter = 'open' | 'unpaid' | 'partly_paid' | 'paid' | 'overdue' | 'void' | 'recurring';
export type BooksAdjustmentKind =
  | 'accrual' | 'prepayment' | 'capital_item' | 'loan_received' | 'loan_repayment' | 'loan_interest'
  | 'owner_contribution' | 'owner_drawing' | 'owner_reimbursement';
export type BooksIntercompanyKind = 'recharge' | 'loan' | 'settlement';
export type BooksDirection = 'receivable' | 'payable';
export type BooksAllocationFilter = 'active' | 'needs_review' | 'unpaid_bill' | 'untagged';

export const BOOKS_SECTIONS: BooksSection[] = ['cost_of_sales', 'operating_expense', 'capital'];
export const BOOKS_BASES: BooksBasis[] = ['accrual', 'cash'];
export const BOOKS_RECURRENCES: BooksRecurrence[] = ['monthly', 'quarterly', 'yearly'];
export const BOOKS_PAYMENT_METHODS: BooksPaymentMethod[] = ['bank_transfer', 'cash', 'card', 'cheque', 'other'];
export const BOOKS_BILL_FILTERS: BooksBillFilter[] = ['open', 'overdue', 'unpaid', 'partly_paid', 'paid', 'recurring', 'void'];
export const BOOKS_ADJUSTMENT_KINDS: BooksAdjustmentKind[] = [
  'accrual', 'prepayment', 'capital_item', 'loan_received', 'loan_repayment', 'loan_interest', 'owner_contribution', 'owner_drawing', 'owner_reimbursement',
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
  permissions: { read: boolean; write: boolean; manage: boolean };
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
  status: 'open' | 'void';
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
  amount_paid: string;
  balance: string;
  payment_state: BooksPaymentState;
  overdue: boolean;
  days_overdue: number;
  can: { edit: boolean; edit_money: boolean; pay: boolean; void: boolean };
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
  status: 'active' | 'void';
  created_by: string;
  created_at: string;
  voided_by: string | null;
  voided_at: string | null;
  void_reason: string;
  replayed?: boolean;
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
    intercompany: { workspace_id: string; name: string; currency: string; amount: string }[];
  };
  equity: { contributions: BooksAmount[]; drawings: BooksAmount[] };
  trend: { month: string; revenue: string; costs: string; profit: string; unconverted: boolean }[];
  customers: BooksBreakdown[];
  projects: BooksBreakdown[];
  cost_centres: BooksBreakdown[];
  budgets: {
    id: string; name: string; period_start: string; period_end: string; currency: string; budget: string; actual: string | null; variance: string | null;
    used_pct: string | null; comparable: boolean; unconverted?: boolean;
  }[];
  recurring: {
    items: { bill_id: string; vendor_id: string; vendor_name: string; category: string; recurrence: BooksRecurrence | null; currency: string; amount: string;
      per_month: string; last_bill_date: string }[];
    per_month: BooksAmount[];
  };
  allocations_in_review: number;
  conversions: BooksConversion[];
  notes: string[];
  truncated: boolean;
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
