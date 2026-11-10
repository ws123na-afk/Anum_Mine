// Bank and card reconciliation sessions and connection health (docs/finance.md#reconciliation-sessions).
// Amounts are decimal strings in the account's currency, in the ledger's sign (a card that owes money is negative).
// Nothing here moves money: every resolution is the owner's, and a difference is never forced to zero.

export type FinanceReconExceptionKind =
  | 'not_in_ledger' | 'unmatched_payment' | 'unmatched_deposit' | 'pending' | 'possible_duplicate' | 'wrong_direction'
  | 'reversal_pair' | 'unknown_currency' | 'unrecognized_fee' | 'missing_period' | 'opening_differs' | 'amount_due_differs';
export type FinanceReconSeverity = 'blocking' | 'warning' | 'info';
export type FinanceReconAction = 'match' | 'correct' | 'attach' | 'pending' | 'accept_reversal' | 'open' | 'import';
export type FinanceReconBlocker = 'difference' | 'statement_rows_not_in_ledger';

export interface FinanceReconFigures {
  currency: string;
  book_opening: string;
  deposits: string;
  payments: string;
  book_closing: string;
  statement_opening: string | null;
  statement_closing: string;
  /** statement closing - book closing */
  difference: string;
  /** The rows the owner marked "not on this statement". */
  pending_total: string;
  adjusted_book_closing: string;
  /** statement closing - adjusted book closing: must be 0 to complete. */
  unexplained: string;
  opening_difference: string | null;
}

export interface FinanceReconCard {
  amount_due: string | null;
  minimum_due: string | null;
  due_on: string | null;
  /** The card's liability at the period's end, positive when owed. */
  owed_in_ledger: string;
  due_difference: string | null;
  paid_since: string;
  minimum_covered: boolean | null;
}

export interface FinanceReconException {
  id: string;
  kind: FinanceReconExceptionKind;
  severity: FinanceReconSeverity;
  actions: FinanceReconAction[];
  posted_on: string | null;
  amount: string | null;
  description: string | null;
  transaction_id: string | null;
  other_transaction_id: string | null;
  statement_ref: string | null;
  /** A short code: the disposition of a statement row, the source of a pending row, why a direction looks wrong. */
  reason: string | null;
  month: string | null;
  age_days: number | null;
  details: Record<string, unknown>;
}

export interface FinanceReconResolution {
  id: string;
  kind: 'match' | 'attach' | 'pending' | 'reversal' | 'correction';
  statement_ref: string | null;
  transaction_id: string | null;
  other_transaction_id: string | null;
  note: string | null;
  created_at: string;
}

export interface FinanceReconHistory {
  action: 'completed' | 'reopened';
  at: string;
  by: string;
  reason?: string | null;
  unexplained?: string | null;
  statement_closing?: string | null;
  book_closing?: string | null;
}

export interface FinanceReconSummary {
  id: string;
  account_id: string;
  account_name: string;
  account_kind: string;
  currency: string;
  period_start: string;
  period_end: string;
  source: 'import' | 'typed';
  import_id: string | null;
  status: 'open' | 'completed';
  state: 'balanced' | 'unresolved';
  /** Completed, and a ledger row up to the period's end changed since: reopen it. */
  changed: boolean;
  can_complete: boolean;
  blockers: FinanceReconBlocker[];
  statement_closing: string;
  book_closing: string;
  unexplained: string;
  exceptions: number;
  blocking: number;
  completed_at: string | null;
  completed_by: string | null;
  version: number;
}

export interface FinanceReconDetail extends FinanceReconSummary {
  figures: FinanceReconFigures;
  card: FinanceReconCard | null;
  counts: Record<string, number>;
  items: FinanceReconException[];
  total: number;
  limit: number;
  offset: number;
  resolutions: FinanceReconResolution[];
  history: FinanceReconHistory[];
  snapshot: Record<string, unknown>;
}

export interface FinanceReconPage { items: FinanceReconSummary[]; total: number; limit: number; offset: number }

export interface FinanceReconInput {
  account_id: string;
  import_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  statement_opening?: string | null;
  statement_closing?: string | null;
  amount_due?: string | null;
  minimum_due?: string | null;
  due_on?: string | null;
}

export interface FinanceReconResolutionInput {
  kind: 'match' | 'attach' | 'pending' | 'reversal';
  statement_ref?: string | null;
  transaction_id?: string | null;
  other_transaction_id?: string | null;
  note?: string | null;
}

export interface FinanceReconCorrectionPreview {
  statement_ref: string;
  account_id: string;
  posted_on: string;
  amount: string;
  currency: string;
  description: string;
  category: string;
  legs: { ledger_account: string; amount: string; currency: string }[];
  /** Send it back to post exactly what was shown. */
  payload_hash: string;
}

export type FinanceReconNextStep = 'import_statement' | 'reconcile' | 'finish_reconciliation' | 'review_changes' | 'up_to_date' | 'none';

export interface FinanceReconHealth {
  account_id: string;
  account_name: string;
  kind: string;
  currency: string;
  last_statement_end: string | null;
  last_import_at: string | null;
  last_sms_on: string | null;
  last_apple_pay_on: string | null;
  missing_months: string[];
  gaps: number;
  reconciled_through: string | null;
  open_reconciliation_id: string | null;
  changed_reconciliation_id: string | null;
  next_step: FinanceReconNextStep;
  next_month: string | null;
  /** ANUM has no live bank feed: statements, SMS and Apple Pay only. */
  bank_feed: 'not_connected';
}

export interface FinanceReconHealthPage { accounts: FinanceReconHealth[]; today: string }

/** One open exception as the unified review queue lists it. */
export interface FinanceReconQueueItem {
  source: 'reconciliation';
  reconciliation_id: string | null;
  account_id: string;
  account_name: string;
  period_start: string | null;
  period_end: string | null;
  exception: FinanceReconException;
}

export interface FinanceReconQueuePage { items: FinanceReconQueueItem[]; total: number; limit: number; offset: number }
