// Wire shapes of /api/v1/finance (services/api/anum_api/finance, docs/finance.md), snake_case as the
// API sends them. Money is a decimal STRING ("1234.50") next to its ISO 4217 currency, so no client
// turns it into a binary float; totals are per currency and never added across currencies.
// Everything here is the signed-in person's own ledger. Stage 2 is read only: nothing moves money.

export type FinanceMoney = string;
export type FinanceAccountKind = 'bank' | 'card' | 'cash' | 'loan' | 'investment';
export type FinanceCategory =
  | 'income' | 'transfer' | 'groceries' | 'dining' | 'transport' | 'fuel' | 'utilities' | 'telecom' | 'housing'
  | 'shopping' | 'health' | 'education' | 'entertainment' | 'subscriptions' | 'travel' | 'insurance' | 'fees' | 'cash' | 'other';
export type FinanceCategorySource = 'rule' | 'owner_rule' | 'manual' | 'none';
export type FinanceCadence = 'weekly' | 'monthly' | 'quarterly' | 'yearly' | 'once';
export type FinanceBillSource = 'manual' | 'detected';
export type FinanceDebtKind = 'loan' | 'card' | 'mortgage' | 'other';

export interface FinanceCategoryRule { match: string; category: FinanceCategory }

export interface FinanceSettings {
  base_currency: string;
  timezone: string;
  category_rules: FinanceCategoryRule[];
  updated_at: string | null;
}

export interface FinanceFormats {
  presets: { id: string; label: string }[];
  date_formats: string[];
  categories: FinanceCategory[];
  account_kinds: FinanceAccountKind[];
  limits: { max_bytes: number; max_rows: number; max_page: number };
}

export interface FinanceCurrencyAmount { currency: string; amount: FinanceMoney }

export interface FinanceAccount {
  id: string;
  name: string;
  kind: FinanceAccountKind;
  currency: string;
  institution: string | null;
  /** The last four digits at most; ANUM never stores a full number. */
  last4: string | null;
  opening_balance: FinanceMoney;
  balance: FinanceMoney;
  archived: boolean;
  transaction_count: number;
  created_at: string;
  updated_at: string;
}

export interface FinanceAccountInput {
  name: string;
  kind: FinanceAccountKind;
  currency?: string | null;
  institution?: string | null;
  last4?: string | null;
  opening_balance?: FinanceMoney;
  archived?: boolean;
}

export interface FinanceTransaction {
  id: string;
  account_id: string;
  posted_on: string;
  /** Signed: money in is positive, money out negative. */
  amount: FinanceMoney;
  currency: string;
  /** From an imported statement: untrusted text, shown as text only. */
  description: string;
  category: FinanceCategory;
  category_source: FinanceCategorySource;
  notes: string | null;
  import_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceTransactionInput {
  account_id: string;
  posted_on: string;
  amount: FinanceMoney;
  description: string;
  category?: FinanceCategory | null;
  notes?: string | null;
}

export interface FinancePage<T> { items: T[]; total: number; limit: number; offset: number }

export interface FinanceColumnMapping {
  date: string;
  description: string;
  amount?: string | null;
  debit?: string | null;
  credit?: string | null;
}

export interface FinanceImportInput {
  account_id: string;
  filename: string;
  content: string;
  preset?: string | null;
  date_format?: string | null;
  mapping?: FinanceColumnMapping | null;
}

export interface FinanceImportPreview {
  account_id: string;
  currency: string;
  filename: string;
  preset: string;
  headers: string[];
  mapping: FinanceColumnMapping;
  date_format: string;
  date_formats: string[];
  date_format_ambiguous: boolean;
  row_count: number;
  valid_count: number;
  new_count: number;
  duplicate_count: number;
  error_count: number;
  rows: { line: number; posted_on: string; amount: FinanceMoney; currency: string; description: string; category: FinanceCategory; duplicate: boolean }[];
  errors: { line: number; message: string }[];
}

export interface FinanceImport {
  id: string;
  account_id: string;
  filename: string;
  preset: string;
  date_format: string;
  row_count: number;
  imported_count: number;
  duplicate_count: number;
  error_count: number;
  created_at: string;
}

export interface FinanceBill {
  id: string;
  name: string;
  amount: FinanceMoney;
  currency: string;
  cadence: FinanceCadence;
  next_due_on: string;
  category: FinanceCategory;
  account_id: string | null;
  source: FinanceBillSource;
  active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceBillInput {
  name: string;
  amount: FinanceMoney;
  currency?: string | null;
  cadence: FinanceCadence;
  next_due_on: string;
  category?: FinanceCategory;
  account_id?: string | null;
  source?: FinanceBillSource;
  active?: boolean;
  notes?: string | null;
}

export interface FinanceUpcomingBill {
  id: string;
  name: string;
  amount: FinanceMoney;
  currency: string;
  cadence: FinanceCadence;
  due_on: string;
  days_until: number;
  category: FinanceCategory;
}

export interface FinanceBillSuggestion {
  name: string;
  amount: FinanceMoney;
  currency: string;
  cadence: FinanceCadence;
  next_due_on: string;
  category: FinanceCategory;
  account_id: string;
  occurrences: number;
}

export interface FinanceDebt {
  id: string;
  name: string;
  kind: FinanceDebtKind;
  balance: FinanceMoney;
  currency: string;
  apr_percent: string;
  minimum_payment: FinanceMoney;
  due_day: number | null;
  account_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceDebtInput {
  name: string;
  kind: FinanceDebtKind;
  balance: FinanceMoney;
  currency?: string | null;
  apr_percent?: string;
  minimum_payment?: FinanceMoney;
  due_day?: number | null;
  account_id?: string | null;
  notes?: string | null;
}

export interface FinanceGoal {
  id: string;
  name: string;
  target_amount: FinanceMoney;
  saved_amount: FinanceMoney;
  currency: string;
  target_on: string | null;
  account_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceGoalInput {
  name: string;
  target_amount: FinanceMoney;
  saved_amount?: FinanceMoney;
  currency?: string | null;
  target_on?: string | null;
  account_id?: string | null;
  notes?: string | null;
}

/** A manual position: quantity and what it cost. No prices, no market data. */
export interface FinanceHolding {
  id: string;
  account_id: string;
  symbol: string;
  name: string | null;
  quantity: string;
  cost_basis: FinanceMoney;
  currency: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceHoldingInput {
  account_id: string;
  symbol: string;
  name?: string | null;
  quantity: string;
  cost_basis: FinanceMoney;
  currency?: string | null;
  notes?: string | null;
}

export interface FinanceSummary {
  month: string;
  start: string;
  end: string;
  today: string;
  base_currency: string;
  timezone: string;
  accounts: { id: string; name: string; kind: FinanceAccountKind; currency: string; institution: string | null; last4: string | null; balance: FinanceMoney; archived: boolean }[];
  balances: FinanceCurrencyAmount[];
  spend_by_category: { category: FinanceCategory; currency: string; amount: FinanceMoney; count: number }[];
  income: FinanceCurrencyAmount[];
  spend: FinanceCurrencyAmount[];
  net: FinanceCurrencyAmount[];
  transaction_count: number;
  upcoming_bills: FinanceUpcomingBill[];
  bills_due: FinanceCurrencyAmount[];
  debts: FinanceCurrencyAmount[];
  minimum_payments: FinanceCurrencyAmount[];
  debt_count: number;
  goals: { id: string; name: string; currency: string; target_amount: FinanceMoney; saved_amount: FinanceMoney; percent: number; target_on: string | null }[];
}
