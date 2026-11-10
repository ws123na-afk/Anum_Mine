// Finance rules, learning from corrections, the suggestion queue and automation policies
// (docs/finance.md#rules-and-learning, #suggestions, #automation-policies). Money and confidences are decimal strings.
import type { FinanceCategory, FinanceTransactionType } from './finance.js';

export interface FinanceRuleMatch {
  merchant?: string | null;
  words?: string[];
  /** Plain text found in the description or reference; never a regular expression. */
  reference?: string | null;
  direction?: 'any' | 'in' | 'out';
  min_amount?: string | null;
  max_amount?: string | null;
  currency?: string | null;
  account_ids?: string[];
  senders?: string[];
}

export interface FinanceRuleContent {
  match: FinanceRuleMatch;
  category: FinanceCategory;
  subcategory_id?: string | null;
  transaction_type?: FinanceTransactionType | null;
  priority?: number;
  enabled?: boolean;
  /** The owner's per-rule automation policy: the daily job may apply it to earlier rows when auto-classify is on. */
  auto_apply?: boolean;
  note?: string | null;
}

export type FinanceRuleProvenance = 'owner' | 'learned' | 'imported' | 'agent';
export type FinanceRuleStatus = 'proposed' | 'approved' | 'rejected';

export interface FinanceRuleVersion {
  rule_id: string;
  version: number;
  status: FinanceRuleStatus;
  content: FinanceRuleContent;
  provenance: FinanceRuleProvenance;
  source_transaction_id: string | null;
  confidence: string;
  /** What the owner approves: send it back on approve. */
  payload_hash: string;
  created_by: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  reverted_from: number | null;
}

export interface FinanceRule { rule_id: string; current: FinanceRuleVersion | null; pending: FinanceRuleVersion | null; versions: number }
export interface FinanceRulePage { items: FinanceRule[]; total: number; limit: number; offset: number; legacy_rules: number; pending: number }
export interface FinanceRuleDetail { rule_id: string; versions: FinanceRuleVersion[] }

export interface FinanceRulePreviewRow {
  transaction_id: string;
  account_id: string;
  posted_on: string;
  description: string;
  amount: string;
  currency: string;
  before_category: FinanceCategory;
  before_subcategory_id: string | null;
  before_type: FinanceTransactionType | null;
  after_category: FinanceCategory;
  after_subcategory_id: string | null;
  after_type: FinanceTransactionType | null;
}

export interface FinanceRulePreview {
  matched: number;
  changes: number;
  unchanged: number;
  /** Set by the owner by hand: never changed. */
  manual_kept: number;
  /** Recorded from an SMS or Apple Pay message: corrected under SMS review instead. */
  sms_kept: number;
  truncated: boolean;
  preview_hash: string;
  rows: FinanceRulePreviewRow[];
}

export interface FinanceSimilar { content: FinanceRuleContent; preview: FinanceRulePreview; source_transaction_id: string }

export interface FinanceRuleBatch {
  id: string;
  origin: 'owner' | 'policy';
  rule_id: string | null;
  rule_version: number | null;
  suggestion_key: string | null;
  changed: number;
  status: 'applied' | 'undone';
  created_by: string;
  created_at: string;
  undone_at: string | null;
}
export interface FinanceRuleBatchPage { items: FinanceRuleBatch[]; total: number; limit: number; offset: number }
export interface FinanceRuleUndo { batch: FinanceRuleBatch; restored: number; kept: number }

export interface FinanceMerchantAlias { id: string; alias: string; merchant: string; created_at: string; updated_at: string }
export interface FinanceSubcategory { id: string; parent: FinanceCategory; name: string; archived: boolean }

export interface FinanceAiSuggestion { category: FinanceCategory | null; transaction_type: FinanceTransactionType | null; note: string }

export type FinanceSuggestionKind =
  | 'classification' | 'conflict' | 'uncategorized' | 'statement_hold' | 'sms_review' | 'duplicate' | 'refund_match' | 'payment_match'
  | 'bill_due' | 'subscription' | 'unusual_spend' | 'missing_statement' | 'cash_shortage' | 'reconciliation' | 'attribution' | 'fx_fee'
  | 'loan_overdue' | 'loan_repayment' | 'loan_payments' | 'external';

/** One instalment of a bank loan as the finder saw it (docs/finance.md#bank-loans). */
export interface FinanceLoanMonth {
  number: number;
  due_on: string;
  state: 'found' | 'missing' | 'ambiguous';
  expected: string;
  currency: string;
  amount: string | null;
  canonical_id: string | null;
  source: 'bank_debit' | 'payslip' | 'salary_rule' | null;
  source_id: string | null;
  candidates: number;
}

/** What Accept links through the finance links API (docs/finance.md#attribution-suggestions). */
export interface FinanceSuggestionLink {
  canonical_id: string;
  dimension: 'bill' | 'debt' | 'vehicle' | 'purchase' | 'business' | 'person_loan' | 'trip' | 'project';
  target_type: string;
  target_id: string;
  amount: string;
  currency: string;
  /** The link role: payment (money out, the default) or deposit (money in, a repayment of money lent). */
  role?: 'payment' | 'deposit' | null;
  treatment: 'owner_contribution' | 'reimbursable' | null;
  category: string | null;
  project_id: string | null;
}

export interface FinanceSuggestion {
  key: string;
  producer: string;
  kind: FinanceSuggestionKind;
  /** English text for assistants; the web words it from `kind` and `params`. */
  detected: string;
  why: string;
  params: Record<string, string>;
  evidence: {
    label: string;
    ref_type: 'transaction' | 'import' | 'sms_message' | 'bill' | 'account' | 'rule' | 'debt' | 'vehicle' | 'purchase' | 'business' | 'other';
    ref_id: string | null;
    value: string | null;
  }[];
  confidence: string | null;
  effect: { amount: string; currency: string; direction: 'in' | 'out' | 'none'; note: string | null }[];
  action: {
    kind: 'classify' | 'confirm_refund' | 'acknowledge' | 'open' | 'link' | 'link_batch';
    transaction_ids: string[];
    category: FinanceCategory | null;
    subcategory_id: string | null;
    transaction_type: FinanceTransactionType | null;
    link?: FinanceSuggestionLink | null;
    /** A split: one transaction across several records, an amount each (a repayment covering several loans). */
    parts?: FinanceSuggestionLink[];
    /** `link_batch`: a bank loan's found payments, accepted at once (bank debits linked, salary months recorded). */
    debt_id?: string | null;
    links?: FinanceSuggestionLink[];
    loan_months?: FinanceLoanMonth[];
  };
  href: string | null;
  occurred_on: string | null;
  payload_hash: string;
  /** `loan_payments`: every instalment due so far with what was found. */
  months?: FinanceLoanMonth[];
}

export interface FinanceSuggestionQueue {
  items: FinanceSuggestion[];
  total: number;
  limit: number;
  offset: number;
  counts: { kind: FinanceSuggestionKind; count: number }[];
  unavailable: string[];
  dismissed: number;
}

export interface FinanceSuggestionDecision {
  key: string; status: 'accepted' | 'rejected' | 'open'; batch: FinanceRuleBatch | null; link_id?: string | null;
  /** Every part a split made. */
  link_ids?: string[];
  /** The batch a bank loan's found payments were accepted in (undo: POST /loan-batches/{id}/undo). */
  loan_batch_id?: string | null;
}

/** Exception notices the daily job may send, each off until the owner turns it on (docs/finance.md#exception-notices). */
export type FinanceNoticeKind = 'reconciliation_overdue' | 'statement_missing' | 'uncategorized_money' | 'business_unposted' | 'evidence_missing';

export interface FinanceAutomationPolicy {
  auto_classify: boolean;
  min_confidence: string;
  sources: ('rule' | 'history')[];
  notify_unresolved: boolean;
  notify_min_items: number;
  notices?: FinanceNoticeKind[];
  notice_exception_days?: number;
  notice_uncategorized_min?: string;
}
export interface FinanceAutomation { policy: FinanceAutomationPolicy; updated_at: string | null; never_automated: string[] }
