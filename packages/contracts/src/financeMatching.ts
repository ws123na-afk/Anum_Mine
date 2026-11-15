// Automatic matching, the record-linking policy, history backfill, installment status and capture health
// (docs/finance-matching.md). Amounts and confidences are decimal strings.

export type FinanceMatchKind = 'installment' | 'bill' | 'card_repayment' | 'bank_loan';
export type FinanceMatchOutcome = 'auto_linked' | 'review' | 'accepted' | 'rejected' | 'undone' | 'stale';
export type FinanceEvidenceState = 'pending' | 'provisional' | 'verified';
export type FinanceSettledSource = 'statement' | 'sms' | 'wallet' | 'manual';

export interface FinanceMatchingPolicy {
  match_transactions: boolean;
  reconcile_installments: boolean;
  link_recurring_bills: boolean;
  link_card_repayments: boolean;
  link_bank_loan_repayments: boolean;
  prevent_duplicates: boolean;
  reprocess_history: boolean;
  notify_exceptions: boolean;
  min_confidence: string;
  review_confidence: string;
  settled_sources: FinanceSettledSource[];
  source_account_ids: string[];
  revision: number;
  approved_at: string | null;
  approved_by: string | null;
}

export type FinanceMatchingPolicyInput = Omit<FinanceMatchingPolicy, 'revision' | 'approved_at' | 'approved_by'> & {
  approve?: boolean;
  expected_revision?: number | null;
};

export interface FinanceInstallmentProvider { key: string; name: string; reads: string[]; connected: boolean }

export interface FinanceMatchingPolicyView {
  policy: FinanceMatchingPolicy;
  version: string;
  approved: boolean;
  never_automated: string[];
  providers: FinanceInstallmentProvider[];
  spending_policy: string;
}

export interface FinanceMatchEvidence { code: string; value: string | null; weight: string }

/** The bank's own conversion figures behind a match or link across currencies (docs/finance-matching.md#across-currencies).
 * ``basis`` is ``missing`` (no original amount: review only) or ``owner_confirmed`` (the owner linked it); absent for the bank's figures. */
export interface FinanceFxEvidence {
  basis?: 'missing' | 'owner_confirmed';
  original_amount: string | null;
  original_currency: string | null;
  billed_amount?: string | null;
  purchase_amount?: string | null;
  bank_amount: string | null;
  bank_currency: string;
  rate: string | null;
  rate_kind: 'printed' | 'column' | 'implied' | 'not_stated';
  rate_source?: string | null;
  rate_date?: string | null;
  fee_amount?: string | null;
  fee_currency?: string | null;
  fee_source?: 'fee_row' | 'markup_stated' | 'fee_stated' | null;
  fee_transaction_id?: string | null;
}

export interface FinanceMatchRecord {
  id: string;
  canonical_id: string;
  kind: FinanceMatchKind;
  dimension: string;
  target_type: string;
  target_id: string;
  label: string;
  due_on: string | null;
  posted_on: string | null;
  amount: string;
  currency: string;
  outcome: FinanceMatchOutcome;
  evidence_state: FinanceEvidenceState;
  reason_code: string;
  confidence: string;
  evidence: FinanceMatchEvidence[];
  flags: string[];
  competitors: number;
  policy_version: string;
  run_id: string | null;
  link_id: string | null;
  /** Across currencies: ``amount``/``currency`` are the obligation's side; this is the bank side and the bank's figures. */
  fx: FinanceFxEvidence | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface FinanceMatchRecordPage {
  items: FinanceMatchRecord[];
  total: number;
  limit: number;
  offset: number;
  counts: Record<string, number>;
}

export interface FinanceMatchRunResult { counts: Record<string, number>; policy_version: string }

export type FinanceInstallmentStatus = 'verified_paid' | 'partly_paid' | 'provisional' | 'review' | 'provider_paid' | 'refunded' | 'overdue'
  | 'due_soon' | 'upcoming' | 'no_bill';

export interface FinanceInstallmentMatchRow {
  installment: number;
  total_installments: number;
  due_on: string;
  amount: string;
  still_due: string;
  currency: string;
  status: FinanceInstallmentStatus;
  bill_id: string | null;
  paid_amount: string;
  canonical_ids: string[];
  record_id: string | null;
  record_version: number | null;
  record_outcome: FinanceMatchOutcome | null;
  confidence: string | null;
  reason_code: string | null;
}

export interface FinanceInstallmentOrder {
  provider: string;
  order_id: string;
  merchant: string;
  purchase_on: string | null;
  currency: string;
  total: string;
  paid: string;
  still_due: string;
  next_due_on: string | null;
  installments: FinanceInstallmentMatchRow[];
}

export interface FinanceInstallmentMatchView { orders: FinanceInstallmentOrder[]; spending_policy: string; today: string }

export type FinanceBackfillMode = 'preview' | 'apply';
export type FinanceBackfillStatus = 'running' | 'done' | 'failed' | 'undone';

export interface FinanceBackfillRun {
  id: string;
  mode: FinanceBackfillMode;
  status: FinanceBackfillStatus;
  start_on: string | null;
  end_on: string | null;
  cursor_on: string | null;
  batches: number;
  counts: Record<string, number>;
  policy_version: string;
  error_code: string | null;
  attempts: number;
  created_by: string;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
  undone_at: string | null;
  version: number;
}

export interface FinanceBackfillPage { items: FinanceBackfillRun[]; total: number }
export interface FinanceBackfillUndo { run: FinanceBackfillRun; links_removed: number }

export interface FinanceCaptureDevice {
  id: string;
  name: string;
  status: string;
  scopes: string[];
  token_hint: string;
  last_seen_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
}

export interface FinanceCaptureChannel {
  channel: 'sms' | 'wallet';
  last_captured_at: string | null;
  last_failed_at: string | null;
  last_failed_code: string | null;
  refused_7d: number;
  awaiting_review: number;
  review_reasons: Record<string, number>;
}

export interface FinanceCaptureHealth {
  state: 'off' | 'ok' | 'attention';
  enabled: boolean;
  self_test_passed: boolean;
  devices: FinanceCaptureDevice[];
  channels: FinanceCaptureChannel[];
  unknown_senders: { sender: string; messages: number; last_at: string }[];
  unmapped_wallet_cards: number;
  mappings: { kind: 'card_number' | 'wallet_card'; label: string; account_id: string; account_name: string | null }[];
  accounts_without_mapping: string[];
  gaps: { start: string; end: string; hours: number }[];
  problems: string[];
  checked_at: string;
}
