// iPhone SMS banking and the double-entry ledger (docs/finance-sms.md). Amounts are decimal strings.
import type { FinanceCurrencyAmount } from './finance.js';

export type FinanceSmsKind =
  | 'salary' | 'deposit' | 'transfer_in' | 'transfer_out' | 'own_transfer' | 'card_purchase' | 'atm_withdrawal' | 'card_repayment'
  | 'loan_disbursement' | 'loan_repayment' | 'bank_fee' | 'refund' | 'reversal' | 'unknown';
export type FinanceSmsStatus = 'posted' | 'review' | 'duplicate' | 'attached' | 'rejected' | 'dismissed' | 'failed';
export type FinanceSmsDeviceStatus = 'active' | 'paused' | 'revoked';
export type FinanceLedgerKind = 'personal' | 'business';
export type FinanceDeviceScope = 'finance.sms' | 'finance.wallet';

export interface FinanceSmsAccountMapping { last4: string; account_id: string; credit_limit?: string | null }
export interface FinanceSmsSenderMapping { sender: string; bank: string }
export interface FinanceWalletCard { card: string; last4?: string | null; account_id: string }

export interface FinanceSmsSettings {
  enabled: boolean;
  ledger_kind: FinanceLedgerKind;
  auto_post: boolean;
  body_retention_days: number;
  verified_senders: string[];
  extra_senders: FinanceSmsSenderMapping[];
  account_map: FinanceSmsAccountMapping[];
  wallet_cards: FinanceWalletCard[];
  default_account_id: string | null;
  cash_account_id: string | null;
  ai: { enabled: boolean; opted_in_at: string | null; daily_cap: number; used_on: string | null; used: number };
  self_test_passed_at: string | null;
  integration_switches?: Record<string, boolean>;
  version: number;
  updated_at: string | null;
}

export interface FinanceSmsSettingsInput {
  version: number;
  enabled?: boolean;
  ledger_kind?: FinanceLedgerKind;
  auto_post?: boolean;
  body_retention_days?: number;
  verified_senders?: string[];
  extra_senders?: FinanceSmsSenderMapping[];
  account_map?: FinanceSmsAccountMapping[];
  wallet_cards?: FinanceWalletCard[];
  default_account_id?: string | null;
  cash_account_id?: string | null;
  ai_enabled?: boolean;
  ai_consent?: boolean;
  ai_daily_cap?: number;
}

export interface FinanceSmsStatusView {
  enabled: boolean;
  ledger_kind: FinanceLedgerKind;
  self_test_passed_at: string | null;
  ready: boolean;
  ingest_path: string | null;
  devices_active: number;
  last_received_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  counts: Record<string, number>;
  review_count: number;
}

export interface FinanceSmsSelfTest { passed: boolean; checks: { name: string; passed: boolean }[]; passed_at: string | null }

export interface FinanceSmsDevice {
  id: string;
  name: string;
  status: FinanceSmsDeviceStatus;
  token_hint: string;
  scopes: FinanceDeviceScope[];
  created_at: string;
  rotated_at: string | null;
  revoked_at: string | null;
  last_seen_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  message_count: number;
}

export interface FinanceSmsDeviceToken {
  device: FinanceSmsDevice;
  token: string;
  ingest_path: string;
  headers: Record<string, string>;
  json_fields: Record<string, string>;
}

export interface FinanceSmsBank {
  key: string;
  name: string;
  country: string;
  currency: string;
  senders: string[];
  verified_by_anum: boolean;
  confirmed_by_you: boolean;
}

export interface FinanceSmsMessage {
  id: string;
  device_id: string | null;
  source: string;
  received_at: string;
  sender: string;
  bank_key: string | null;
  body_masked: string;
  body_kept_until: string | null;
  status: FinanceSmsStatus;
  reason: string | null;
  parsed: Record<string, string>;
  rule: string;
  entry_id: string | null;
  duplicate_of: string | null;
  observed_balance: string | null;
  observed_currency: string | null;
  attempts: number;
}

export interface FinanceSmsMessagePage { items: FinanceSmsMessage[]; total: number; limit: number; offset: number }

export interface FinanceSmsCorrection {
  kind?: FinanceSmsKind;
  amount?: string;
  currency?: string;
  converted_amount?: string;
  account_id?: string;
  counter_account_id?: string;
  merchant?: string;
  category?: string;
  occurred_on?: string;
  reference?: string;
  interest?: string;
  fee?: string;
  last4?: string;
  allow_duplicate?: boolean;
  confirm_format?: boolean;
  teach?: boolean;
}

export interface FinanceSmsRule {
  id: string;
  sender: string;
  bank_key: string | null;
  kind: string;
  currency: string | null;
  category: string | null;
  account_id: string | null;
  status: 'proposed' | 'active' | 'disabled';
  from_message_id: string | null;
  hits: number;
  created_at: string;
  pattern_preview: string;
}

export interface FinanceSmsCorrectionResult {
  message: FinanceSmsMessage;
  entry_id: string;
  canonical_id: string;
  reversed_entry_id: string | null;
  proposed_rule: FinanceSmsRule | null;
  rule_error: string | null;
}

export interface FinanceSmsIngestResult { status: string; reason: string | null; message_id: string | null; replayed: boolean; text: string }
export interface FinanceSmsSuggestion { fields: Record<string, string>; used_today: number; daily_cap: number; note: string }

export interface FinanceReconcilePair {
  statement_transaction_id: string;
  entry_id: string;
  account_id: string;
  posted_on: string;
  amount: string;
  currency: string;
  description: string;
}
export interface FinanceReconcileView { import_id: string; matched: FinanceReconcilePair[]; statement_only: number; sms_only: string[]; note: string }

export interface FinanceLedgerAccountLine {
  id: string;
  name: string;
  kind: string;
  currency: string;
  last4: string | null;
  balance: string;
  owed: string | null;
  credit_limit: string | null;
  utilization_percent: number | null;
  observed_balance: string | null;
  observed_at: string | null;
  difference: string | null;
}

export interface FinanceLedgerMonthLine { month: string; currency: string; income: string; expenses: string; net: string }

export interface FinanceLedgerDashboard {
  month: string;
  income: FinanceCurrencyAmount[];
  expenses: FinanceCurrencyAmount[];
  /** Income, cashback and unmatched refunds less expenses: the summary's net. */
  surplus: FinanceCurrencyAmount[];
  /** Card rewards and refunds that found no purchase: never income (docs/finance.md#how-figures-are-counted). */
  cashback?: FinanceCurrencyAmount[];
  refunds?: FinanceCurrencyAmount[];
  assets: FinanceCurrencyAmount[];
  liabilities: FinanceCurrencyAmount[];
  net_worth: FinanceCurrencyAmount[];
  in_transit: FinanceCurrencyAmount[];
  accounts: FinanceLedgerAccountLine[];
  cards: FinanceLedgerAccountLine[];
  loans: FinanceLedgerAccountLine[];
  categories: { category: string; currency: string; amount: string; count: number }[];
  merchants: { merchant: string; currency: string; amount: string; count: number }[];
  trend: FinanceLedgerMonthLine[];
  year: FinanceLedgerMonthLine[];
  currencies: string[];
  entry_count: number;
  /** The month's rows no rule recognised: in neither income nor expenses (docs/finance.md#transaction-types). */
  uncategorized_count?: number;
  ledger_kind: FinanceLedgerKind;
  base_currency: string;
  bills_due: { id: string; name: string; amount: string; currency: string; due_on: string; days_until: number }[];
  goals: { id: string; name: string; currency: string; target_amount: string; saved_amount: string; percent: number }[];
  debts: { id: string; name: string; currency: string; balance: string; minimum_payment: string | null; months_to_clear: number | null }[];
  sync: { enabled: boolean; devices_active: number; last_received_at: string | null; last_ok_at: string | null; last_error: string | null; last_error_at: string | null; review_count: number };
  filters: Record<string, string | null>;
}

export interface FinanceJournalEntry {
  id: string;
  /** The canonical transaction id: stable across corrections; other modules link to it. */
  canonical_id: string | null;
  posted_on: string;
  kind: string;
  category: string | null;
  merchant: string | null;
  description: string;
  source: string;
  source_ref: string | null;
  status: string;
  reverses_id: string | null;
  reversed_by_id: string | null;
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  reconciled_at: string | null;
  postings: { ledger_account: string; account_id: string | null; amount: string; currency: string }[];
  sources: { kind: 'sms' | 'wallet' | 'statement'; message_id: string; status: string; received_at: string }[];
}
export interface FinanceJournalPage { items: FinanceJournalEntry[]; total: number; limit: number; offset: number }
