// The transaction register and account overview (docs/finance.md#register-and-account-overview).
// Read only; amounts are decimal strings and never added across currencies.
import type { FinanceCategory, FinanceCategorySource, FinanceCurrencyAmount, FinanceTransaction } from './finance.js';

export type FinanceOrigin = 'sms' | 'apple_pay' | 'statement' | 'manual';
export type FinanceRowStatus = 'linked' | 'needs_review' | 'duplicate_candidate' | 'uncategorized' | 'recorded';
export type FinanceEvidence = 'linked' | 'missing' | 'not_needed';

export interface FinanceRegisterRow extends FinanceTransaction {
  /** The canonical transaction other modules link to; null for a row recorded before the journal. */
  canonical_id: string | null;
  origin: FinanceOrigin;
  statement_format: 'csv' | 'pdf' | null;
  sources: ('sms' | 'wallet' | 'statement')[];
  /** A statement confirmed an SMS or Apple Pay record of the same payment. */
  reconciled: boolean;
  receipts: number;
  evidence: FinanceEvidence;
  links_need_review: number;
  /** The older record this one may duplicate (same account, amount and currency within two days). */
  duplicate_of: string | null;
  status: FinanceRowStatus;
  /** The account's balance after this row; only when the register is filtered to one account. */
  running_balance: string | null;
}

export interface FinanceRegisterTotals {
  count: number;
  categorized: number;
  receipts_missing: number;
  needs_review: number;
  duplicate_candidates: number;
  matched: number;
  inflow: FinanceCurrencyAmount[];
  outflow: FinanceCurrencyAmount[];
}

export interface FinanceRegisterPage {
  items: FinanceRegisterRow[];
  total: number;
  limit: number;
  offset: number;
  /** More than 10,000 rows matched: narrow the period. */
  truncated: boolean;
  totals: FinanceRegisterTotals;
  balances: { currency: string; beginning: string; ending: string }[];
  /** Messages waiting in the SMS review queue. */
  review_count: number;
  /** This workspace's ledger, as set on Finance, iPhone SMS (docs/finance-sms.md#dashboard). */
  ledger_kind: 'personal' | 'business';
}

export interface FinanceRegisterSource {
  kind: 'sms' | 'wallet' | 'statement';
  status: string;
  received_at: string;
  sender: string | null;
  /** The masked message, only while the retention setting keeps it. */
  text: string | null;
  kept_until: string | null;
}

export interface FinanceRegisterDetail {
  row: FinanceRegisterRow;
  account_name: string;
  kind: string | null;
  merchant: string | null;
  reference: string | null;
  suggested_category: FinanceCategory;
  suggested_by: FinanceCategorySource;
  sources: FinanceRegisterSource[];
  receipt_document_ids: string[];
  duplicate: FinanceRegisterRow | null;
}

export type FinanceAccountSource = 'manual' | 'sms' | 'apple_wallet' | 'statement';
export type FinanceAttention = 'balance_differs' | 'links_need_review' | 'possible_duplicates';

export interface FinanceAccountOverview {
  account_id: string;
  sources: FinanceAccountSource[];
  credit_limit: string | null;
  observed_balance: string | null;
  observed_at: string | null;
  difference: string | null;
  last_activity_on: string | null;
  last_import_at: string | null;
  last_reconciled_at: string | null;
  imported: number;
  matched: number;
  needs_attention: FinanceAttention[];
  payment_due: { name: string; due_on: string; amount: string; currency: string } | null;
}

export interface FinanceAccountsOverview {
  accounts: FinanceAccountOverview[];
  review_count: number;
  last_reconciled_at: string | null;
  base_currency: string;
  ledger_kind: 'personal' | 'business';
}

export interface FinanceAccountActivity {
  account_id: string;
  currency: string;
  days: { day: string; inflow: string; outflow: string }[];
}
