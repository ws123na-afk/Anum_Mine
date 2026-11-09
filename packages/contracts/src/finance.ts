// Wire shapes of /api/v1/finance (services/api/anum_api/finance, docs/finance.md), snake_case as the
// API sends them. Money is a decimal STRING ("1234.50") next to its ISO 4217 currency, so no client
// turns it into a binary float; totals are per currency and never added across currencies.
// Everything here is the signed-in person's own ledger. Stage 2 is read only: nothing moves money.

export type FinanceMoney = string;
export type FinanceAccountKind = 'bank' | 'card' | 'cash' | 'loan' | 'investment';
export type FinanceCategory =
  | 'income' | 'cashback' | 'refunds' | 'transfer' | 'groceries' | 'dining' | 'transport' | 'fuel' | 'utilities' | 'telecom' | 'housing'
  | 'food' | 'room_rent' | 'shopping' | 'health' | 'education' | 'entertainment' | 'subscriptions' | 'travel' | 'insurance' | 'fees' | 'interest'
  | 'cash' | 'other';
/** `match`: set by matching with another record (an own-transfer pair, a refund's purchase); `system`: by ANUM. Never the owner's choice. */
export type FinanceCategorySource = 'rule' | 'owner_rule' | 'manual' | 'none' | 'match' | 'system';
/**
 * What a transaction is, separate from its category (docs/finance.md#transaction-types); it decides how the row counts.
 * `uncategorized`: no rule recognised it, held for review and counted as neither income nor spend.
 */
export type FinanceTransactionType =
  | 'income' | 'expense' | 'internal_transfer' | 'card_payment' | 'loan_received' | 'loan_principal' | 'interest' | 'refund' | 'cashback'
  | 'bank_fee' | 'investment_contribution' | 'investment_withdrawal' | 'owner_contribution' | 'owner_drawing' | 'reimbursement'
  | 'receivable_collection' | 'payable_settlement' | 'fx_exchange' | 'tax' | 'reversal' | 'uncategorized';
export type FinanceTypeSource = 'rule' | 'owner' | 'system' | 'sms' | 'match';
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
  /** Cards and loans: what is owed is a negative opening balance; a positive one needs `credit_balance` (an overpaid card). */
  opening_balance?: FinanceMoney;
  credit_balance?: boolean;
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
  /** The transaction type and why (docs/finance.md#transaction-types). Optional: older API releases do not send it. */
  transaction_type?: FinanceTransactionType | null;
  type_source?: FinanceTypeSource | null;
  /** 0 to 1, a decimal string. */
  type_confidence?: string | null;
  /** A short machine code, for example `no_rule_matched` or `transfer_out_third_party`. */
  type_reason?: string | null;
  /** The person's own purchase a refund gives back (a match, or a suggestion when the confidence is low). */
  related_transaction_id?: string | null;
  /** The owner's subcategory under `category` (docs/finance.md#subcategories). Optional. */
  subcategory_id?: string | null;
  /** Stage 4: what the Guardian rules say about this record (on create and change only). */
  guardian?: FinanceGuardianDecision | null;
}

export interface FinanceTransactionInput {
  account_id: string;
  posted_on: string;
  amount: FinanceMoney;
  description: string;
  category?: FinanceCategory | null;
  /** The owner's own reading of what the row is; never changed by a rule or a match. */
  transaction_type?: FinanceTransactionType | null;
  notes?: string | null;
  remember_category_match?: string | null;
  /** PATCH only: one of the owner's subcategories under the row's category; null clears it. */
  subcategory_id?: string | null;
  /** PATCH only: the category came from the AI suggestion (kept, never learned from). */
  from_ai_suggestion?: boolean;
}

export interface FinancePage<T> { items: T[]; total: number; limit: number; offset: number }

export interface FinanceColumnMapping {
  date: string;
  description: string;
  amount?: string | null;
  debit?: string | null;
  credit?: string | null;
  direction?: string | null;
  currency?: string | null;
  original_amount?: string | null;
  original_currency?: string | null;
  billed_amount?: string | null;
  billed_currency?: string | null;
  fx_rate?: string | null;
  fee?: string | null;
  balance?: string | null;
  reference?: string | null;
}

/** The statement matching engine (docs/finance.md#statement-matching). */
export type FinanceMatchStatus = 'new' | 'exact_duplicate' | 'matched_existing' | 'possible_duplicate';

export interface FinanceMatchedRecord {
  record_id: string;
  canonical_id: string | null;
  transaction_id: string | null;
  account_id: string;
  /** Who recorded it first: `sms`, `wallet` (Apple Pay), `manual` or `statement`. */
  source: string;
  last4: string | null;
  posted_on: string;
  amount: FinanceMoney;
  currency: string;
  description: string;
}

export interface FinanceRowMatch {
  status: FinanceMatchStatus;
  /** `imported_before`, `reconciled_before`, `waiting_review`, `same_statement_row`, `same_amount_merchant`, `same_reference`,
   *  `same_original_amount`, `two_candidates`, `amount_date_only`, `no_match`. */
  code: string;
  /** The reason in plain English (the web shows its own translation of `code`). */
  reason: string;
  record: FinanceMatchedRecord | null;
  candidates: FinanceMatchedRecord[];
  lag_days: number | null;
  /** Statement billed minus recorded, when the bank billed a foreign purchase differently. */
  amount_difference: FinanceMoney | null;
  bill: { bill_id: string; name: string; due_on: string; expected: FinanceMoney; currency: string; strength: 'strong' | 'weak'; reasons: string[] } | null;
  transfer: { direction: 'to' | 'from'; account_id: string; account_name: string; account_last4: string | null; strength: 'strong' | 'weak';
    reasons: string[]; lag_days: number; other: FinanceMatchedRecord } | null;
  default_import: boolean;
}

/** A row's foreign currency details, as printed (or `implied` from two printed amounts); never an invented rate. */
export interface FinanceRowFx {
  original_amount: FinanceMoney | null;
  original_currency: string | null;
  rate: string | null;
  rate_source: 'printed' | 'implied' | 'column' | null;
  fee: FinanceMoney | null;
  fee_currency: string | null;
  markup_percent: string | null;
  expected: FinanceMoney | null;
  difference: FinanceMoney | null;
  check: 'ties' | 'differs' | null;
  implied_markup_percent: string | null;
}

export interface FinanceMatchCounts { new: number; exact_duplicate: number; matched_existing: number; possible_duplicate: number; bills: number; transfers: number }

export interface FinanceReconciliation {
  status: 'tied' | 'differs' | 'unknown';
  opening: FinanceMoney | null;
  closing: FinanceMoney | null;
  rows_total: FinanceMoney;
  expected_closing: FinanceMoney | null;
  difference: FinanceMoney | null;
  opening_source: string;
  closing_source: string;
  breaks: number[];
}

/** The person's choice for one previewed row; rows not listed follow the preview's default. */
export interface FinanceRowDecision {
  line: number;
  action?: 'import' | 'skip' | null;
  bill?: boolean | null;
  transfer?: boolean | null;
  correct?: boolean | null;
}

export interface FinanceImportInput {
  account_id: string;
  filename: string;
  content: string;
  preset?: string | null;
  date_format?: string | null;
  mapping?: FinanceColumnMapping | null;
  decisions?: FinanceRowDecision[];
  opening_balance?: string | null;
  closing_balance?: string | null;
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
  rows: { line: number; posted_on: string; amount: FinanceMoney; currency: string; description: string; category: FinanceCategory; duplicate: boolean; direction_warning?: string | null; notes?: string | null; original_amount?: FinanceMoney | null; original_currency?: string | null; reported_fee?: FinanceMoney | null;
    balance?: FinanceMoney | null; reference?: string | null; match: FinanceRowMatch; fx?: FinanceRowFx | null }[];
  errors: { line: number; message: string }[];
  counts: FinanceMatchCounts;
  reconciliation: FinanceReconciliation;
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
  /** Stage 4: imported rows that break a Guardian rule (on a confirmed import). */
  rule_breaks?: number;
  /** Rows that reported a payment already recorded (SMS, Apple Pay, by hand): attached, not added (docs/finance-sms.md#reconciliation). */
  reconciled?: number;
  /** Rows that could match more than one recorded payment: waiting in Finance, iPhone SMS, Review. */
  held_for_review?: number;
  /** Records corrected to the bank-billed amount. */
  corrected?: number;
  skipped?: number;
  bills_paid?: number;
  bills_review?: number;
  transfers?: number;
  reconciliation?: { status: 'tied' | 'differs' | 'unknown'; opening: string | null; closing: string | null; rows_total: string | null; difference: string | null; not_in_ledger?: string | null } | null;
  /** Rows the reader could not use (unreadable, outside the statement period): listed on the import. */
  dropped?: number;
  /** Statement rows the ledger does not hold (held for review or left out by the person). */
  not_in_ledger?: number;
}

export type FinanceRowDispositionKind = 'imported' | 'held_for_review' | 'attached_to_existing' | 'duplicate_skipped' | 'unticked' | 'dropped_by_reader';

/** What an import did with one statement row (`GET /imports/{id}`). */
export interface FinanceRowDisposition {
  line: number;
  posted_on: string | null;
  amount: string | null;
  description: string;
  disposition: FinanceRowDispositionKind;
  reason: string | null;
  message: string | null;
  /** The transaction or record the row was imported as, attached to, or duplicates. */
  record_id: string | null;
}

export interface FinanceImportDetail extends FinanceImport {
  rows: FinanceRowDisposition[];
  rows_total: number;
  limit: number;
  offset: number;
}

/** Own transfers already in the ledger, not yet paired (`GET /transfers/candidates`). */
export interface FinanceTransferPair {
  out: FinanceMatchedRecord;
  into: FinanceMatchedRecord;
  strength: 'strong' | 'weak';
  reasons: string[];
  lag_days: number;
  out_account_name: string;
  into_account_name: string;
  into_last4: string | null;
}

export interface FinanceTransferCandidates { start: string; end: string; pairs: FinanceTransferPair[]; strong: number; weak: number }
export interface FinanceTransferResult { paired: number; recategorized: number; failed: string[] }

/** PDF statements (docs/finance.md#pdf-statements): one role per column the reader found. */
export type FinancePdfRole = 'date' | 'description' | 'debit' | 'credit' | 'amount' | 'balance' | 'reference' | 'currency' | 'foreign_amount'
  | 'fx_rate' | 'fee' | 'vat' | 'skip';

export interface FinancePdfRow {
  line: number;
  page: number;
  posted_on: string | null;
  description: string;
  amount: string | null;
  debit: string | null;
  credit: string | null;
  balance: string | null;
  reference: string | null;
  /** Original row currency; foreign charges require an explicit bank-billed amount in the account currency before import. */
  currency: string | null;
  /** Card statements: the amount in the currency spent, its rate, fees and VAT, as printed. */
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  fee: string | null;
  vat: string | null;
  /** From a "<type>Time:HH:MM:SS**Note:<details>" description: the type, the time and the other party. */
  kind: string | null;
  time: string | null;
  counterparty: string | null;
  /** A charge row's payment ("Transaction Charges" after its transfer): the line it belongs to. */
  parent_line: number | null;
  /** 0 to 1: OCR quality, how the sign was found, whether the balance agrees. */
  confidence: number;
  category: string;
  /** Already in the ledger (same hash as a CSV import): importing it again adds nothing. */
  duplicate: boolean;
  /** `sign_unsure`, `sign_from_balance`, `date_unreadable`, `amount_missing`, `amount_zero`, `hijri_date`, `balance_break`,
   *  `foreign_currency`, `columns_swapped`, `low_confidence`. */
  flags: string[];
  /** The matching engine's answer; null for a row without a date or amount. */
  match?: FinanceRowMatch | null;
  fx?: FinanceRowFx | null;
}

export interface FinancePdfCheck {
  status: 'matched' | 'mismatch' | 'unknown';
  opening: string | null;
  closing: string | null;
  rows_total: string;
  expected_closing: string | null;
  difference: string | null;
  opening_source: 'statement' | 'rows' | 'none';
  closing_source: 'statement' | 'rows' | 'none';
  /** Rows after which the running balance does not agree with the printed balance. */
  breaks: number[];
  /** Pages whose balance brought forward is not the balance the page before ended with. */
  page_breaks: number[];
  /** The statement's own counts and totals against the rows read. */
  summary: { name: 'deposits_count' | 'withdrawals_count' | 'deposits_total' | 'withdrawals_total' | 'total_due'; stated: string; found: string; ok: boolean }[];
}

/** A row or line the reader left out, and why: shown in the review, never dropped silently. */
export interface FinancePdfDroppedRow {
  page: number;
  /** `outside_period`: a dated row outside the statement's period. `unattached_line`: a line no row could take. */
  reason: 'outside_period' | 'unattached_line';
  text: string;
  posted_on: string | null;
  amount: string | null;
}

/** What `POST /imports/pdf/read` found. Nothing is saved until the reviewed rows are imported. */
export interface FinancePdfStatement {
  account_id: string;
  currency: string;
  filename: string;
  file_sha256: string;
  source: 'text' | 'ocr';
  page_count: number;
  layout: 'table' | 'columns' | 'lines';
  columns: { index: number; label: string; role: FinancePdfRole }[];
  roles: FinancePdfRole[];
  /** The layout's fingerprint and the taught template it was read with, if any. */
  fingerprint: string | null;
  template_id: string | null;
  confidence: number;
  /** A current account (running balance) or a credit card (charges against an amount due). */
  kind: 'account' | 'card';
  statement_date: string | null;
  due_date: string | null;
  skipped_pages: { page: number; reason: 'not_transactions' | 'no_text' }[];
  date_format: string;
  date_formats: string[];
  date_format_ambiguous: boolean;
  period_start: string | null;
  period_end: string | null;
  /** Only the last four digits of the account or card number; never more. */
  account_last4: string | null;
  account_last4_matches: boolean | null;
  statement_currency: string | null;
  opening_balance: string | null;
  closing_balance: string | null;
  check: FinancePdfCheck;
  row_count: number;
  duplicate_count: number;
  skipped_lines: number;
  /** `currency_differs`, `no_rows`, `page_breaks`, `summary_differs`, `low_confidence`, `debit_credit_swapped`, `columns_from_content`, `rows_left_out`. */
  warnings: string[];
  rows: FinancePdfRow[];
  counts: FinanceMatchCounts;
  /** Rows outside the statement's period and lines no row could take: listed, not imported. */
  dropped_rows: FinancePdfDroppedRow[];
}

export interface FinancePdfImportRow {
  posted_on: string;
  description: string;
  amount: string;
  balance?: string | null;
  reference?: string | null;
  currency?: string | null;
  original_amount?: string | null;
  original_currency?: string | null;
  billed_amount?: string | null;
  billed_currency?: string | null;
  fx_rate?: string | null;
  fee?: string | null;
  vat?: string | null;
  time?: string | null;
  action?: 'import' | 'skip' | null;
  bill?: boolean | null;
  transfer?: boolean | null;
  correct?: boolean | null;
  /** False: unticked. Still sent so identical rows keep their place; never checked or imported. */
  include?: boolean;
}

/** A statement layout taught by the owner (Finance, Import): the mapping kept against the layout's fingerprint. */
export interface FinancePdfTemplate {
  id: string;
  labels: string[];
  roles: FinancePdfRole[];
  date_format: string | null;
  uses: number;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
}

export interface FinancePdfImportInput {
  account_id: string;
  filename: string;
  file_sha256: string;
  source: 'text' | 'ocr';
  rows: FinancePdfImportRow[];
  opening_balance?: string | null;
  closing_balance?: string | null;
  /** Import even though the rows do not add up to the closing balance (the owner confirmed it). */
  accept_mismatch?: boolean;
  /** The layout the rows were read with; kept as a template when `remember` is true. */
  layout?: { fingerprint: string; labels: string[]; roles: FinancePdfRole[]; date_format: string | null; remember: boolean } | null;
  /** The statement's printed counts and totals from the read; rows that disagree need `accept_mismatch`. */
  summary?: { name: FinancePdfCheck['summary'][number]['name']; stated: string }[];
  /** The rows the reader left out, from the read: listed on the import, never imported. */
  dropped?: { page: number; reason: FinancePdfDroppedRow['reason']; text: string; posted_on: string | null; amount: string | null }[];
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
  /** Negative when the due date passed this month with no matched payment (`overdue`). */
  days_until: number;
  category: FinanceCategory;
  overdue?: boolean;
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
  /** What was owed when tracking started (payoff progress); null when not entered. */
  original_balance?: FinanceMoney | null;
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
  /** Left out: kept as it is; "" clears it. */
  original_balance?: FinanceMoney | null;
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
  /** Stage 3: what is put aside each month; null works it out from the target date. */
  monthly_contribution: FinanceMoney | null;
  /** A paused goal is not set aside by safe to spend (docs/finance.md#debts-and-goals). */
  paused?: boolean;
  /** What the goal is for (docs/finance.md#debts-and-goals); the planner keeps an emergency fund aside. */
  purpose?: FinanceGoalPurpose;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export type FinanceGoalPurpose = 'general' | 'emergency' | 'family' | 'purchase' | 'education' | 'travel';
export const FINANCE_GOAL_PURPOSES: readonly FinanceGoalPurpose[] = ['general', 'emergency', 'family', 'purchase', 'education', 'travel'];

export interface FinanceGoalInput {
  name: string;
  target_amount: FinanceMoney;
  saved_amount?: FinanceMoney;
  currency?: string | null;
  target_on?: string | null;
  account_id?: string | null;
  monthly_contribution?: FinanceMoney | null;
  /** Left out: kept as it is. */
  paused?: boolean | null;
  /** Left out: kept as it is (a new goal is general). */
  purpose?: FinanceGoalPurpose | null;
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

/** One currency's net worth (docs/finance.md#how-figures-are-counted). */
export interface FinanceNetWorthPart {
  currency: string;
  /** Account balances (cards and loans by their sign); an investment account with holdings counts them at cost instead. */
  accounts: FinanceMoney;
  /** Money lent to people, still owed back. */
  receivables: FinanceMoney;
  holdings_at_cost: FinanceMoney;
  /** Debts with no account of their own, money borrowed from people included. */
  debts: FinanceMoney;
  /** Still due on installment plans. */
  installments: FinanceMoney;
  net: FinanceMoney;
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
  /** Cash-back rewards, separate from earnings. */
  cashback?: FinanceCurrencyAmount[];
  /** Merchant refunds, not salary or new earnings. */
  refunds?: FinanceCurrencyAmount[];
  spend: FinanceCurrencyAmount[];
  net: FinanceCurrencyAmount[];
  transaction_count: number;
  upcoming_bills: FinanceUpcomingBill[];
  bills_due: FinanceCurrencyAmount[];
  /** What is owed now: a debt kept on a card or loan account follows that account, any other follows its matched payments. */
  debts: FinanceCurrencyAmount[];
  /** Debts no account counts yet: the only ones net worth subtracts on top of account balances. */
  unlinked_debts?: FinanceCurrencyAmount[];
  /** Account balances (cards and loans by their sign) less `unlinked_debts`, per currency. */
  net_worth?: FinanceCurrencyAmount[];
  /** Net worth per currency, line by line (older servers leave it out; the web then works it out from the accounts). */
  net_worth_parts?: FinanceNetWorthPart[];
  /** Left out of net worth, with the reason: a holding without a cost basis, or in another currency than its account. */
  net_worth_excluded?: { kind: 'holding'; name: string; currency: string; reason: 'no_cost_basis' | 'currency_differs' }[];
  minimum_payments: FinanceCurrencyAmount[];
  debt_count: number;
  /** The month had more rows than one read returns: the figures are partial. */
  month_rows_capped?: boolean;
  /** Rows no rule recognised: in none of the figures above, shown as "N uncategorized, not counted". */
  uncategorized_count?: number;
  uncategorized_in?: FinanceCurrencyAmount[];
  uncategorized_out?: FinanceCurrencyAmount[];
  goals: { id: string; name: string; currency: string; target_amount: FinanceMoney; saved_amount: FinanceMoney; percent: number; target_on: string | null }[];
}


// ---- Stage 3: safe-to-spend (docs/finance.md#safe-to-spend) -------------------------------------

export interface FinanceCurrencyBuffer { currency: string; amount: FinanceMoney }

export interface FinancePlan {
  buffers: FinanceCurrencyBuffer[];
  combine_currencies: boolean;
  fx_max_age_days: number;
  updated_at: string | null;
}

export interface FinancePlanInput {
  buffers: FinanceCurrencyBuffer[];
  combine_currencies: boolean;
  fx_max_age_days?: number;
}

export type FinanceSafeLineKind = 'balance' | 'bill' | 'minimum_payment' | 'goal' | 'buffer';
export type FinanceSafeNote =
  | 'account_balance' | 'bill_due' | 'bill_overdue' | 'minimum_due' | 'minimum_no_day' | 'minimum_passed' | 'minimum_overdue' | 'minimum_in_bill' | 'goal_set' | 'goal_derived'
  | 'goal_no_plan' | 'goal_reached' | 'goal_paused' | 'buffer' | 'account_card' | 'account_loan' | 'account_investment' | 'account_archived';

/** One line of the figure: positive for money you have, negative for a deduction. */
export interface FinanceSafeLine {
  kind: FinanceSafeLineKind;
  label: string;
  ref_id: string | null;
  currency: string;
  amount: FinanceMoney;
  note: FinanceSafeNote;
  params: Record<string, string>;
  detail: string;
}

export interface FinanceCurrencySafeToSpend {
  currency: string;
  available: FinanceMoney;
  bills: FinanceMoney;
  minimum_payments: FinanceMoney;
  goals: FinanceMoney;
  buffer: FinanceMoney;
  safe_to_spend: FinanceMoney;
  per_day: FinanceMoney;
  short: boolean;
  lines: FinanceSafeLine[];
  /** With a salary profile in this currency: the next usual pay day and the net expected then. */
  next_pay_on?: string | null;
  expected_pay?: FinanceMoney | null;
  days_to_payday?: number | null;
}

export interface FinanceConversion {
  from_currency: string;
  to_currency: string;
  amount: FinanceMoney;
  /** 1 from_currency = rate to_currency, with the rate's date and source. */
  rate: string;
  converted: FinanceMoney;
  as_of: string;
  source: string;
  via: string | null;
}

export interface FinanceSafeToSpend {
  month: string;
  today: string;
  month_end: string;
  days_left: number;
  base_currency: string;
  timezone: string;
  currencies: FinanceCurrencySafeToSpend[];
  excluded: FinanceSafeLine[];
  combine_currencies: boolean;
  combined_status: 'off' | 'single_currency' | 'ready' | 'missing_rates';
  combined: { currency: string; safe_to_spend: FinanceMoney; per_day: FinanceMoney; conversions: FinanceConversion[] } | null;
  missing_rates: string[];
  fx_max_age_days: number;
  accounts_counted: number;
  note: string;
}

// ---- Stage 4: the Guardian rules (docs/finance.md#guardian) --------------------------------------

export interface FinanceAmountLimit { currency: string; amount: FinanceMoney }
export interface FinanceCategoryCap extends FinanceAmountLimit { category: FinanceCategory }

export interface FinanceGuardianRules {
  allowed_currencies: string[];
  max_single: FinanceAmountLimit[];
  max_daily_total: FinanceAmountLimit[];
  category_caps: FinanceCategoryCap[];
  blocked_categories: FinanceCategory[];
  blocked_merchants: string[];
  approval_above: FinanceAmountLimit[];
  quiet_hours: { start: string; end: string } | null;
  cooling_off: { hours: number; at_or_above: FinanceAmountLimit[] } | null;
  /** Kinds the person paused: kept, but not applied until resumed. Absent on rules saved before pausing existed. */
  paused?: FinanceGuardianRuleKind[];
}

/** The kinds of rule, in the order the evaluator checks them and the page lists them. */
export type FinanceGuardianRuleKind =
  | 'allowed_currencies' | 'max_single' | 'max_daily_total' | 'category_caps' | 'blocked_categories' | 'blocked_merchants'
  | 'approval_above' | 'quiet_hours' | 'cooling_off';

export type FinanceGuardianOutcome = 'allow' | 'needs_approval' | 'deny';
export interface FinanceGuardianReason { code: string; outcome: FinanceGuardianOutcome; message: string; rule: string | null; params: Record<string, string> }
export interface FinanceGuardianDecision { outcome: FinanceGuardianOutcome; reasons: FinanceGuardianReason[] }

export type FinanceGuardianChangeStatus = 'pending' | 'confirmed' | 'rejected' | 'expired' | 'superseded';

/** Proposed rules. Nothing changes until the person confirms with this payload_hash. */
export interface FinanceGuardianChange {
  id: string;
  base_version: number;
  rules: FinanceGuardianRules;
  proposed_by: 'person' | 'agent';
  proposer: string;
  reason: string | null;
  payload_hash: string;
  /** The rule kinds this change loosens; an assistant can never propose one that does. */
  loosens: string[];
  status: FinanceGuardianChangeStatus;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
}

export interface FinanceGuardian {
  version: number;
  rules: FinanceGuardianRules;
  confirmed_at: string | null;
  confirmed_by: string | null;
  pending: FinanceGuardianChange[];
  kinds: string[];
}

export interface FinanceGuardianVersion { id: string; version: number; rules: FinanceGuardianRules; change_id: string; confirmed_by: string; created_at: string }

export interface FinanceGuardianAlert {
  code: 'cap_over' | 'cap_near' | 'merchant_blocked_seen' | 'category_blocked_seen' | 'daily_over' | 'single_over';
  severity: 'over' | 'near';
  message: string;
  rule: string;
  params: Record<string, string>;
}

export interface FinanceGuardianAlerts { month: string; version: number; alerts: FinanceGuardianAlert[] }

// The review queue (docs/finance.md#review-queue): payments a rule flags and the person's decisions. A note only.
export type FinanceGuardianItemState = 'awaiting' | 'expected' | 'reviewed' | 'snoozed';
export interface FinanceGuardianFlaggedItem {
  transaction_id: string;
  posted_on: string;
  description: string;
  /** Money out, positive. */
  amount: string;
  currency: string;
  category: FinanceCategory;
  account_id: string;
  account_name: string | null;
  recorded_at: string;
  outcome: 'needs_approval' | 'deny';
  reasons: FinanceGuardianReason[];
  rules: FinanceGuardianRuleKind[];
  /** Median of earlier payments with the same description and currency, up to a year back. */
  typical_amount: string | null;
  prior_count: number;
  state: FinanceGuardianItemState;
  decided_at: string | null;
  snoozed_until: string | null;
}
export interface FinanceGuardianRuleStat { kind: FinanceGuardianRuleKind; count: number; last: string | null }
export interface FinanceGuardianReviewQueue {
  month: string;
  version: number;
  timezone: string;
  counts: { flagged: number; awaiting: number; resolved: number; snoozed: number };
  rules: FinanceGuardianRuleStat[];
  items: FinanceGuardianFlaggedItem[];
  total: number;
  limit: number;
  offset: number;
}
export type FinanceGuardianReviewDecision = 'expected' | 'reviewed' | 'snoozed' | 'reopen';
export interface FinanceGuardianReviewResult { transaction_id: string; state: FinanceGuardianItemState; decided_at: string | null; snoozed_until: string | null }
export interface FinanceGuardianActivity { id: string; action: string; target: string; created_at: string; metadata: Record<string, unknown> }
export interface FinanceGuardianActivityPage { items: FinanceGuardianActivity[]; next_cursor: string | null }

export type FinanceActionKind = 'spend' | 'bill_payment' | 'transfer' | 'trade' | 'record';

export interface FinanceEvaluateInput {
  kind?: FinanceActionKind;
  amount: FinanceMoney;
  currency?: string | null;
  category?: FinanceCategory | null;
  merchant?: string | null;
  at?: string | null;
}

/** A dry run: the rules' verdict and the kernel's (which never lets money move). */
export interface FinanceEvaluation {
  version: number;
  action: { kind: FinanceActionKind; amount: FinanceMoney; currency: string; category: FinanceCategory | null; merchant: string | null; at: string };
  rules_decision: FinanceGuardianDecision;
  decision: FinanceGuardianDecision;
  note: string;
}

// ---- Stage 5: read-only connectors (docs/finance.md#connectors) ----------------------------------

export type FinanceConnectorKind = 'bank' | 'card' | 'broker' | 'crypto' | 'market_data' | 'fx';
export type FinanceConnectionStatus = 'active' | 'error' | 'disconnected';

export interface FinanceProvider {
  id: string;
  kind: FinanceConnectorKind;
  name: string;
  summary: string;
  /** Always read-only scopes (each ends in ":read"). */
  scopes: string[];
  available: boolean;
  needs_credentials: boolean;
  credential_fields: string[];
  sync_hours: number;
  source_url: string | null;
  terms: string | null;
  /** For a source that is not built: what the owner must set up first. */
  owner_action: string | null;
}

/** A connection. Credentials are write only: never sent back, only whether some are saved. */
export interface FinanceConnection {
  id: string;
  provider: string;
  provider_name: string;
  kind: FinanceConnectorKind;
  label: string;
  status: FinanceConnectionStatus;
  scopes: string[];
  has_credentials: boolean;
  credential_hint: string | null;
  enabled: boolean;
  next_sync_at: string;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  last_counts: Record<string, number>;
  created_at: string;
  updated_at: string;
}

export interface FinanceConnectionInput { provider: string; label?: string | null; credentials?: Record<string, string> | null }

export interface FinanceSyncReport { connection_id: string; status: 'ok' | 'error' | 'skipped'; message: string; counts: Record<string, number> }

export interface FinanceFxRate {
  id: string;
  base: string;
  quote: string;
  rate: string;
  as_of: string;
  source: string;
  source_url: string | null;
  connection_id: string | null;
  fetched_at: string;
}

export interface FinanceFxRateInput { base: string; quote: string; rate: string; as_of: string }

// ---- Stage 6: paper and shadow trading, SIMULATED (docs/finance.md#paper-and-shadow-trading) ------

export type FinancePortfolioMode = 'paper' | 'shadow';
export type FinanceInstrumentKind = 'security' | 'fx';
export type FinanceOrderSide = 'buy' | 'sell';
export type FinanceOrderType = 'market' | 'limit';
export type FinancePaperOrderStatus = 'waiting' | 'filled' | 'refused' | 'cancelled';

/** A price the person recorded. ANUM has no market-price source and never invents a price. */
export interface FinancePrice { id: string; symbol: string; currency: string; price: string; as_of: string; source: string; recorded_at: string }
export interface FinancePriceInput { symbol: string; currency: string; price: string; as_of: string }
export interface FinancePriceSaved { price: FinancePrice; orders_changed: number }

/** The fee and slippage model every simulated result uses (basis points: 10 = 0.10 percent). */
export interface FinanceTradingAssumptions { fee_bps: string; fee_min: string; slippage_bps: string; max_price_age_days: number }

export interface FinancePortfolioSummary {
  id: string;
  name: string;
  mode: FinancePortfolioMode;
  epoch: number;
  assumptions: FinanceTradingAssumptions;
  starting_cash: FinanceCurrencyAmount[];
  cash: FinanceCurrencyAmount[];
  realised: FinanceCurrencyAmount[];
  unrealised: FinanceCurrencyAmount[];
  positions: number;
  waiting: number;
  created_at: string;
  reset_at: string | null;
  /** Always true: nothing here is a real trade. */
  simulated: true;
}

export interface FinancePosition {
  instrument_kind: FinanceInstrumentKind;
  symbol: string;
  currency: string;
  quantity: string;
  cost: string;
  average_price: string | null;
  price: string | null;
  price_as_of: string | null;
  price_source: string | null;
  market_value: string | null;
  unrealised: string | null;
}

export interface FinanceShadowEntry {
  order_id: string;
  symbol: string;
  currency: string;
  side: FinanceOrderSide;
  quantity: string;
  fill_price: string;
  filled_at: string;
  fee: string;
  price: string | null;
  price_as_of: string | null;
  result: string | null;
  days: number;
}

export interface FinancePortfolio extends FinancePortfolioSummary {
  holdings: FinancePosition[];
  fees: FinanceCurrencyAmount[];
  slippage: FinanceCurrencyAmount[];
  unpriced: string[];
  shadow: FinanceShadowEntry[];
  shadow_totals: FinanceCurrencyAmount[];
  note: string;
}

export interface FinancePortfolioInput {
  name: string;
  mode: FinancePortfolioMode;
  starting_cash: { currency: string; amount: string }[];
  fee_bps: string;
  fee_min: string;
  slippage_bps: string;
  max_price_age_days: number;
}

export interface FinancePaperOrder {
  id: string;
  portfolio_id: string;
  intent_id: string;
  epoch: number;
  mode: FinancePortfolioMode;
  instrument_kind: FinanceInstrumentKind;
  symbol: string;
  currency: string;
  side: FinanceOrderSide;
  order_type: FinanceOrderType;
  quantity: string;
  limit_price: string | null;
  status: FinancePaperOrderStatus;
  reason_code: string | null;
  reason: string | null;
  requested_by: string;
  market_price: string | null;
  fill_price: string | null;
  price_as_of: string | null;
  price_source: string | null;
  notional: string | null;
  fee: string | null;
  slippage: string | null;
  guardian_outcome: string | null;
  guardian_codes: string[];
  created_at: string;
  filled_at: string | null;
  simulated: true;
}

// ---- Stage 7: the execution gateway (docs/finance.md#execution-gateway) ---------------------------

export type FinanceExecMode = 'paper' | 'shadow' | 'live' | 'settings';
export type FinanceIntentStatus = 'pending_approval' | 'denied' | 'refused' | 'executing' | 'executed' | 'failed' | 'expired' | 'rejected';

/** What a simulated order would fill at now, with the assumptions used. */
export interface FinanceTradeEstimate {
  market_price: string;
  fill_price: string;
  notional: string;
  fee: string;
  slippage: string;
  price_as_of: string;
  price_source: string;
  fee_bps: string;
  fee_min: string;
  slippage_bps: string;
  limit_met: boolean;
  simulated: true;
  note: string;
}

/** A request through the gateway. It runs only when the person approves this payload_hash. */
export interface FinanceIntent {
  id: string;
  mode: FinanceExecMode;
  kind: 'trade' | 'transfer' | 'spend' | 'bill_payment' | 'live_profile';
  portfolio_id: string | null;
  portfolio_name: string | null;
  instrument_kind: FinanceInstrumentKind | null;
  symbol: string | null;
  currency: string | null;
  side: FinanceOrderSide | null;
  order_type: FinanceOrderType | null;
  quantity: string | null;
  limit_price: string | null;
  amount: string | null;
  account_id: string | null;
  live_profile: { base_version: number; limits: FinanceLiveLimits; opt_in: boolean } | null;
  requested_by: 'person' | 'agent';
  reason: string | null;
  payload_hash: string;
  status: FinanceIntentStatus;
  decision: FinanceGuardianDecision | Record<string, never>;
  guardian_version: number;
  estimate: FinanceTradeEstimate | null;
  refusal_code: string | null;
  refusal: string | null;
  created_at: string;
  expires_at: string;
  approved_at: string | null;
  executed_at: string | null;
  result: { order_id?: string; order_status?: FinancePaperOrderStatus; reason_code?: string | null; simulated?: boolean; live_version?: number };
  simulated: boolean;
}

export interface FinanceIntentInput {
  mode: 'paper' | 'shadow' | 'live';
  kind?: 'trade';
  portfolio_id: string;
  instrument_kind: FinanceInstrumentKind;
  symbol: string;
  currency: string;
  side: FinanceOrderSide;
  order_type: FinanceOrderType;
  quantity: string;
  limit_price?: string | null;
  reason?: string | null;
  idempotency_key?: string | null;
}

export interface FinanceKillSwitch { engaged: boolean; changed_at: string | null; changed_by_you: boolean; reason: string | null; can_change: boolean }

export interface FinanceExecutionStatus {
  global_switch: FinanceKillSwitch;
  person_switch: FinanceKillSwitch;
  executors: { mode: 'paper' | 'shadow' | 'live'; available: boolean; simulated: boolean; note: string }[];
  /** Always false in this release: no live executor exists. */
  live_enabled: false;
  approval_minutes: number;
  pending: number;
}

// ---- Stage 8: limited live, framework only and OFF (docs/finance.md#limited-live) ------------------

export interface FinanceLiveLimits {
  max_per_action: FinanceAmountLimit[];
  max_per_day: FinanceAmountLimit[];
  max_per_month: FinanceAmountLimit[];
  instruments: string[];
  accounts: string[];
  currencies: string[];
  cooling_off_hours: number;
}

export interface FinanceLivePrecondition { code: string; met: boolean; message: string }

export interface FinanceLive {
  enabled: false;
  opt_in: boolean;
  opt_in_at: string | null;
  limits: FinanceLiveLimits;
  version: number;
  preconditions: FinanceLivePrecondition[];
  pending: FinanceIntent[];
  owner_steps: string[];
  note: string;
}

export interface FinanceLiveChangeInput { limits: FinanceLiveLimits; opt_in?: boolean; reason?: string | null }

// ---- Live eligibility preflight, read only (docs/finance.md#live-eligibility-preflight) --------------
// POST /api/v1/finance/broker/readiness. Ticked facts are the person's own notes, not evidence:
// whatever is sent, live trading stays off and no order is sent.

export type FinanceBrokerKind = 'alpaca' | 'interactive_brokers' | 'other';
export type FinanceBrokerAsset = 'stock' | 'crypto';
export type FinanceBrokerStage = 'not_connected' | 'paper_only' | 'read_only' | 'live_blocked';

/** The person's checklist, in the server's three groups (finance/broker_readiness.py). */
export type FinancePreflightDataFact =
  | 'regulated_provider_verified' | 'account_in_owner_name' | 'broker_country_eligibility_verified' | 'asset_permissions_verified'
  | 'funding_only_at_broker' | 'market_data_terms_accepted' | 'data_freshness_verified' | 'read_only_access_verified';
export type FinancePreflightResearchFact = 'historical_data_tests_passed' | 'paper_testing_passed' | 'guardian_regression_passed';
export type FinancePreflightReviewFact =
  | 'reconciliation_tests_passed' | 'independent_security_review_passed' | 'written_live_approval' | 'withdrawal_scope_absent'
  | 'credentials_in_secret_store' | 'order_idempotency_verified' | 'emergency_stop_verified';
export type FinancePreflightFact = FinancePreflightDataFact | FinancePreflightResearchFact | FinancePreflightReviewFact;

export interface FinanceBrokerProfile { broker: FinanceBrokerKind; country_of_residence: string; assets: FinanceBrokerAsset[] }

export interface FinancePreflightInput { facts: Partial<Record<FinancePreflightFact, boolean>>; profile: FinanceBrokerProfile | null }

export interface FinancePreflightNote { code: string; source: string }

export interface FinancePreflight {
  stage: FinanceBrokerStage;
  missing_data_requirements: FinancePreflightFact[];
  missing_research_requirements: FinancePreflightFact[];
  missing_live_review_requirements: FinancePreflightFact[];
  system_preconditions: FinanceLivePrecondition[];
  /** For Interactive Brokers: the IBKR connector's own live gates for this person (docs/finance-live-trading.md#locks). */
  broker_gates?: FinanceLivePrecondition[];
  notes: FinancePreflightNote[];
  broker: FinanceBrokerKind | null;
  assets: FinanceBrokerAsset[];
  /** Always false: the checklist is not evidence. */
  eligibility_confirmed: false;
  /** Always false: live trading is off. */
  live_trading_enabled: false;
  credentials_connected: false;
  deposits_into_anum_supported: false;
  /** Always 0: the preflight sends nothing. */
  orders_sent: 0;
  explanation: string;
}

// ---- Stage 9: wider live use, framework only and OFF (docs/finance.md#wider-live) ----------------

export interface FinanceWiderCriterion { code: string; met: boolean; value: string; needed: string }

/** A proposed step up one tier. It applies only when the owner approves this payload_hash, after a cooling-off. */
export interface FinanceWidening {
  id: string;
  base_tier: number;
  tier: number;
  limits: FinanceLiveLimits;
  kinds: string[];
  proposed_by: 'person' | 'agent';
  reason: string | null;
  payload_hash: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'superseded' | 'cancelled';
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  effective_at: string | null;
}

export interface FinanceLiveIncident { id: string; code: 'kill_switch_person' | 'kill_switch_global' | 'failed_execution' | 'repeated_denials'; tier_before: number; tier_after: number; created_at: string; reviewed_at: string | null }

export interface FinanceWider {
  enabled: boolean;
  /** Always false in this release: the gateway refuses every live request whatever the tier. */
  live_enabled: false;
  tier: number;
  kinds: string[];
  limits: FinanceLiveLimits;
  frozen: boolean;
  frozen_at: string | null;
  frozen_reason: string | null;
  graduated: boolean;
  criteria: FinanceWiderCriterion[];
  ceilings: { max_tier: number; step_percent: number; cooling_hours: number; window_days: number };
  widenings: FinanceWidening[];
  incidents: FinanceLiveIncident[];
  note: string;
}

export interface FinanceWideningInput { limits: FinanceLiveLimits; kinds: string[]; reason?: string | null }

// ---- Trading intelligence dashboard, read only (docs/finance-trading-intel.md#dashboard) ------------

export type FinanceStrategyState = 'DRAFT' | 'BACKTESTING' | 'VALIDATION' | 'PAPER_ACTIVE' | 'PAPER_SUSPENDED' | 'RETIRED';

export interface FinanceIntelVenue { code: string; name: string; timezone: string; state: 'open' | 'closed' | 'holiday'; local_time: string; holidays_known: boolean; reason: string; note: string }

export interface FinanceIntelPortfolio {
  id: string;
  name: string;
  mode: FinancePortfolioMode;
  cash: FinanceCurrencyAmount[];
  realised: FinanceCurrencyAmount[];
  unrealised: FinanceCurrencyAmount[];
  fees: FinanceCurrencyAmount[];
  slippage: FinanceCurrencyAmount[];
  holdings: FinancePosition[];
  holdings_total: number;
  unpriced: string[];
  waiting: number;
  simulated: true;
}

export interface FinanceIntelRisk {
  version: number;
  rules: FinanceGuardianRules;
  /** Paper and shadow orders always wait for the person's approval of the code shown. */
  paper_always_needs_approval: boolean;
  read_only: true;
  note: string;
}

/** The dashboard's first read. `simulation.live_enabled` is always false: live trading stays off. */
export interface FinanceIntelOverview {
  simulation: { simulation_only: true; live_enabled: boolean; wider_live_enabled: boolean; note: string };
  switches: { person_engaged: boolean; global_engaged: boolean; can_change_global: boolean };
  venues: FinanceIntelVenue[];
  portfolios: FinanceIntelPortfolio[];
  risk: FinanceIntelRisk;
  /** How many of your strategy versions are in each lifecycle state. */
  strategies: { state: FinanceStrategyState; count: number }[];
  pending_requests: number;
  agent_requests: number;
  note: string;
  generated_at: string;
}

/** One version of a strategy. A version is never edited after DRAFT: a change is a new version. */
export interface FinanceStrategy {
  id: string;
  lineage_id: string;
  key: string;
  code_version: string;
  name: string;
  version: number;
  state: FinanceStrategyState;
  symbols: string[];
  params: Record<string, string | number | boolean | null>;
  reason: string;
  portfolio_id: string | null;
  parent_id: string | null;
  evidence_run_id: string | null;
  created_by: string;
  state_changed_by: string;
  state_changed_at: string;
  created_at: string;
  updated_at: string;
  simulated: true;
}

export interface FinanceStrategyFold { index: number; train_start: string | null; train_end: string | null; test_start: string | null; test_end: string | null; metrics: Record<string, number | null> }

export type FinanceRunVerdict = 'passed' | 'rejected' | 'insufficient_data' | 'completed';

/** One backtest or walk-forward run of a strategy version, on the bars whose SHA-256 is `data_hash`. SIMULATED. */
export interface FinanceStrategyRun {
  id: string;
  strategy_id: string;
  kind: 'backtest' | 'walk_forward';
  created_at: string;
  data_source: string;
  data_hash: string;
  code_version: string;
  symbols: string[];
  costs: Record<string, string>;
  metrics: Record<string, number | null>;
  benchmark: Record<string, number | null>;
  verdict: FinanceRunVerdict;
  reasons: string[];
  warnings: string[];
  folds: FinanceStrategyFold[];
  /** A re-run on the same bars gave the same hash and metrics. */
  reproduced: boolean;
  requested_by: string;
  /** The reproducible evaluation report of a walk-forward run (empty for a backtest). */
  report?: FinanceEvaluationReport | Record<string, never>;
  simulated: true;
}

/** intel/report.py `evaluation_report`: everything needed to repeat and judge a walk-forward run. Never says profitable. */
export interface FinanceEvaluationReport {
  label: 'SIMULATED';
  disclaimer: string;
  strategy: { key: string; name: string; code_version: string; candidate: number; candidate_name: string; hypothesis: string; instruments: string; timeframe: string; entry: string; exit: string; params: Record<string, unknown> | null };
  data: { source: string; sha256: string; symbols: string[] };
  costs: Record<string, string>;
  method: string;
  verdict: FinanceRunVerdict;
  verdict_text: string;
  reasons: { code: string; text: string }[];
  warnings: { code: string; text: string }[];
  worst_drawdown: number | null;
  worst_window: { test: [string, string]; return: number; drawdown: number } | null;
  beats_benchmark: boolean | null;
}

// ---- Trading intelligence actions (finance/intel_control_api.py) ----------------------------------

export type FinanceIntelAssetClass = 'equity' | 'etf' | 'crypto' | 'forex' | 'commodity';
export type FinanceIntelPhase = 'pre_open' | 'intraday' | 'post_close' | 'weekly' | 'monthly';

export interface FinanceIntelInstrument {
  id: string; symbol: string; name: string | null; asset_class: FinanceIntelAssetClass; venue: string; currency: string;
  listed_on: string | null; delisted_on: string | null; bars: number; first_bar: string | null; last_bar: string | null; last_source: string | null;
}
export interface FinanceIntelInstrumentInput { symbol: string; asset_class: FinanceIntelAssetClass; venue: string; currency: string; name?: string | null; listed_on?: string | null }
export interface FinanceIntelBarsInput { provider: 'csv_import' | 'stooq' | 'binance_public'; payload: string; source?: string | null }
export interface FinanceIntelBarsResult { imported: number; changed: number; first: string | null; last: string | null; issues: { code: string; day: string; message: string }[] }
export interface FinanceIntelRule {
  key: string; name: string; code_version: string; candidate: number; candidate_name: string; hypothesis: string; instruments: string;
  timeframe: string; entry: string; exit: string; defaults: Record<string, number>; min_symbols: number; max_symbols: number;
}
export interface FinanceStrategyInput { key: string; instrument_ids: string[]; params?: Record<string, number> | null; name?: string | null; reason: string }
export interface FinanceIntelCycle {
  cycle_id: string; phase: FinanceIntelPhase; skipped: boolean; reason?: string; simulated: true; active_strategies?: number;
  proposed_orders?: number; refused_signals?: number; suspended?: number; halted?: number; decisions: number;
  /** Post-close only: the written daily report (`GET /intel/daily-reports/{id}`). */
  report_id?: string;
}

// ---- Trading intelligence: the post-close daily report (docs/finance-trading-intel.md#daily-report) ----

export interface FinanceDailyReportSummary {
  active_strategies: number; proposed_orders: number; refused_signals: number; suspended: number;
  waiting_for_approval: number; problems: number; market_closed: number;
}

/** `GET /api/v1/finance/intel/daily-reports`, newest first. SIMULATED. */
export interface FinanceDailyReportItem {
  id: string;
  date: string;
  created_at: string;
  summary: Partial<FinanceDailyReportSummary>;
  simulated: true;
}

/** Recorded text (reasons, sources, next steps) is data to show, never instructions. */
export interface FinanceDailyReportBody {
  label: 'SIMULATED';
  disclaimer: string;
  phase: string;
  date: string;
  generated_at: string;
  summary: FinanceDailyReportSummary;
  data_health: { symbol: string | null; venue: string | null; last_bar: string | null; sessions_behind: number | null; source: string | null; status: string; reasons: string[] }[];
  orders: { strategy: string; symbol: string | null; side: string | null; quantity: string | number | null; target_weight: number | null;
    estimate: { fill_price?: string; price_source?: string; fee?: string } | null; guardian: string | null; intent_id: string | null }[];
  refused: { strategy: string; agent: string; action: string; reasons: string[] }[];
  suspended: { strategy: string; agent: string; reasons: string[]; why: string | null }[];
  portfolios: { strategy: string; cash: FinanceCurrencyAmount[] | null; realised: FinanceCurrencyAmount[] | null; unrealised: FinanceCurrencyAmount[] | null; fees: FinanceCurrencyAmount[] | null; unpriced: string[] | null }[];
  performance: { strategy: string; since: string | null; return: number | null; benchmark_return: number | null; max_drawdown: number | null; flags: string[] }[];
  problems: { agent: string; action: string; outcome: string; reasons: string[]; strategy: string }[];
  next_steps: string[];
}

/** Model commentary on a report (docs/finance-trading-intel.md#model-commentary). Always shown with `label`. */
export interface FinanceReportCommentary {
  status: 'ok' | 'skipped' | 'withheld' | 'failed';
  /** Why there is no text: `no_model`, `disabled`, `monthly_limit`, `model_budget`, `profit_claim`, `empty_answer`, `model_unavailable`. */
  reason: string | null;
  text: string | null;
  model: string | null;
  label: string;
  created_at: string;
}

/** `GET /api/v1/finance/intel/daily-reports/{id}`. */
export interface FinanceDailyReport extends FinanceDailyReportItem {
  report: FinanceDailyReportBody;
  markdown: string;
  commentary?: FinanceReportCommentary | null;
}

export type FinanceIntelAgent = 'person' | 'trading_supervisor' | 'market_research' | 'technical_analysis' | 'strategy_research' | 'portfolio'
  | 'risk_guardian' | 'execution_coordinator' | 'performance_reviewer';

export interface FinanceAgentDecision {
  id: string;
  cycle_id: string | null;
  agent: FinanceIntelAgent;
  action: string;
  outcome: string;
  reasons: string[];
  strategy_id: string | null;
  intent_id: string | null;
  created_at: string;
  simulated: true;
}

// ---- Trading intelligence: SIMULATED P&L and drawdown over time (docs/finance-trading-intel.md#pl-and-drawdown-over-time) ----

/** One day of a portfolio's current run in one currency. Amounts are decimal strings in that currency. */
export interface FinanceEquityPoint {
  day: string;
  /** Paper: cash plus priced holdings. Shadow: null (no cash). Null on a gap. */
  value: string | null;
  /** Realised plus unrealised, fees and slippage included. Null on a gap. */
  pnl: string | null;
  /** How far below the running peak (zero or negative). Paper: of the value; shadow: of the P&L. */
  drawdown: string | null;
  /** Paper only: the drawdown as a fraction of the running peak value. */
  drawdown_pct: number | null;
  /** False when something held had no recorded price fresh enough that day: a gap, never a guess. */
  complete: boolean;
}

export interface FinanceEquitySeries {
  currency: string;
  points: FinanceEquityPoint[];
  max_drawdown: string | null;
  max_drawdown_pct: number | null;
  peak_day: string | null;
  trough_day: string | null;
  gaps: number;
  unpriced: string[];
  /** Buy and hold of the same symbols for comparison (absent from older servers). */
  benchmark?: FinanceEquityBenchmark | null;
}

/** Why a series has no buy and hold line: a shadow portfolio has no cash, the run traded no symbol in this currency,
 * or no day had a fresh price for every symbol. */
export type FinanceEquityBenchmarkReason = 'shadow' | 'no_symbols' | 'no_common_price';

/** Buy and hold of the symbols this run traded, equal weights, bought with the portfolio's value on `start`, no costs.
 * Its P&L starts from the portfolio's own P&L on `start`, so both lines compare. One point per curve day; null before
 * `start` and on a day a symbol has no fresh price. SIMULATED comparison, not advice. */
export interface FinanceEquityBenchmark {
  symbols: string[];
  start: string | null;
  points: { day: string; pnl: string | null }[];
  reason: FinanceEquityBenchmarkReason | null;
}

/** `GET /api/v1/finance/intel/portfolios/{id}/equity?days=`: one series per currency, never mixed. SIMULATED. */
export interface FinanceEquity {
  portfolio_id: string;
  name: string;
  mode: FinancePortfolioMode;
  start: string;
  end: string;
  days: number;
  /** Filled orders in this run. */
  orders: number;
  max_price_age_days: number;
  /** More prices are stored than the curve reads (the newest 5,000): older days may show gaps. */
  prices_truncated: boolean;
  series: FinanceEquitySeries[];
  note: string;
  generated_at: string;
  simulated: true;
}

// ---- The weekly Money advisor (docs/finance.md#money-advisor) ----------------------------------------
// ANUM only advises: these are read-outs of the person's own records. Nothing here moves money.

export type FinanceAdvisorScenarioKind = 'bare' | 'essential' | 'full';
export interface FinanceAdvisorScenario { kind: FinanceAdvisorScenarioKind; monthly_outflow: FinanceMoney; days: number | null; runs_out_on: string | null }
export interface FinanceAdvisorEssentialCategory { category: FinanceCategory; monthly_average: FinanceMoney; bills_monthly: FinanceMoney; counted: FinanceMoney }
export interface FinanceAdvisorRunway {
  status: 'ok' | 'not_enough_history' | 'no_records';
  history_days: number;
  first_record_on: string | null;
  basis: 'months' | 'last_30_days' | 'none';
  months: string[];
  liquid: FinanceMoney;
  accounts_counted: number;
  bills_monthly: FinanceMoney;
  minimums_monthly: FinanceMoney;
  essential_spending_monthly: FinanceMoney | null;
  total_spending_monthly: FinanceMoney | null;
  essential_categories: FinanceAdvisorEssentialCategory[];
  scenarios: FinanceAdvisorScenario[];
}
export interface FinanceAdvisorPeriod {
  start: string; end: string; amount: FinanceMoney; previous: FinanceMoney; previous_start: string; previous_end: string;
  change_percent: number | null; average: FinanceMoney | null; vs_average_percent: number | null;
}
export interface FinanceAdvisorCategoryLine {
  category: FinanceCategory; week: FinanceMoney; previous_week: FinanceMoney; month: FinanceMoney; previous_month: FinanceMoney; average_month: FinanceMoney | null;
}
export interface FinanceAdvisorPayee { name: string; amount: FinanceMoney; count: number }
export interface FinanceAdvisorSpike { category: FinanceCategory; period: 'month' | 'new'; amount: FinanceMoney; expected: FinanceMoney; percent: number | null }
export interface FinanceAdvisorSpending {
  week: FinanceAdvisorPeriod; month: FinanceAdvisorPeriod; categories: FinanceAdvisorCategoryLine[];
  payees_week: FinanceAdvisorPayee[]; payees_month: FinanceAdvisorPayee[]; spikes: FinanceAdvisorSpike[];
}
export type FinanceAdvisorComingKind = 'bill' | 'minimum' | 'card_minimum' | 'recurring' | 'subscription';
export interface FinanceAdvisorComing {
  kind: FinanceAdvisorComingKind; name: string; ref_id: string | null; amount: FinanceMoney; due_on: string | null; days_until: number | null;
  category: FinanceCategory | null; detected: boolean;
}
export interface FinanceAdvisorCurrency {
  currency: string;
  runway: FinanceAdvisorRunway;
  card_available: FinanceMoney | null;
  income_month: FinanceMoney;
  spend_month: FinanceMoney;
  spending: FinanceAdvisorSpending;
  coming: { items: FinanceAdvisorComing[]; total: FinanceMoney; saved_total: FinanceMoney; detected_total: FinanceMoney; within_7_days: FinanceMoney };
}
export type FinanceAdvisorCardMissing = 'debt_record' | 'rate' | 'due_day' | 'minimum' | 'limit';
export interface FinanceAdvisorCard {
  name: string; account_id: string | null; debt_id: string | null; currency: string; last4: string | null;
  owed: FinanceMoney; owed_source: 'ledger' | 'debt'; observed_balance: FinanceMoney | null; observed_at: string | null;
  credit_limit: FinanceMoney | null; available: FinanceMoney | null; utilization_percent: number | null;
  minimum_payment: FinanceMoney | null; due_on: string | null; days_until: number | null; apr_percent: FinanceMoney | null;
  interest_if_minimum: FinanceMoney | null; months_to_clear_at_minimum: number | null; total_interest_at_minimum: FinanceMoney | null;
  never_clears_at_minimum: boolean; missing: FinanceAdvisorCardMissing[];
}
export type FinanceAdvisorSeverity = 'urgent' | 'high' | 'medium' | 'info';
export interface FinanceAdvisorAction {
  code: string; severity: FinanceAdvisorSeverity; currency: string | null; params: Record<string, string>; text_en: string; text_ar: string; tab: string | null;
}
export interface FinanceAdvisorNote {
  version: number; today: string; timezone: string; base_currency: string; week_start: string; week_end: string; month_start: string;
  newest_record_on: string | null; history_days: number; has_accounts: boolean;
  currencies: FinanceAdvisorCurrency[]; cards: FinanceAdvisorCard[];
  income: {
    salary_found: boolean; last_salary_on: string | null;
    /** The one income figure: the salary profile's expected net, else the average of the records. Older notes leave these out. */
    currency?: string | null; monthly?: string | null; source?: 'salary_profile' | 'records' | null; next_pay_on?: string | null;
    missing_months?: string[]; expected_pay_day?: number | null;
  };
  actions: FinanceAdvisorAction[]; notice_en: string; notice_ar: string;
}
export interface FinanceAdvisorSettings { enabled: boolean; weekday: number; updated_at: string | null }
export interface FinanceAdvisorNoteView {
  id: string; week_start: string; trigger: 'schedule' | 'manual'; generated_at: string; delivered_at: string | null; note: FinanceAdvisorNote;
}
export interface FinanceAdvisorOverview {
  settings: FinanceAdvisorSettings; timezone: string; today: string; week_start: string; next_note_on: string | null; latest: FinanceAdvisorNoteView | null;
}
export interface FinanceAdvisorNoteSummary {
  id: string; week_start: string; trigger: 'schedule' | 'manual'; generated_at: string; delivered_at: string | null;
  survival: { currency: string; status: string; days: number | null }[]; coming: { currency: string; amount: FinanceMoney }[]; actions: number; urgent: number;
}
export interface FinanceAdvisorNotePage { items: FinanceAdvisorNoteSummary[]; total: number; limit: number; offset: number }
