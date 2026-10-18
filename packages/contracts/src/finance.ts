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
  /** Stage 4: what the Guardian rules say about this record (on create and change only). */
  guardian?: FinanceGuardianDecision | null;
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
  /** Stage 4: imported rows that break a Guardian rule (on a confirmed import). */
  rule_breaks?: number;
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
  /** Stage 3: what is put aside each month; null works it out from the target date. */
  monthly_contribution: FinanceMoney | null;
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
  monthly_contribution?: FinanceMoney | null;
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
  | 'account_balance' | 'bill_due' | 'minimum_due' | 'minimum_no_day' | 'minimum_passed' | 'goal_set' | 'goal_derived'
  | 'goal_no_plan' | 'goal_reached' | 'buffer' | 'account_card' | 'account_loan' | 'account_investment' | 'account_archived';

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
}

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
  recording: { strategies: boolean; runs: boolean; decisions: boolean };
  pending_requests: number;
  agent_requests: number;
  note: string;
  generated_at: string;
}

/** A page that also says whether the backend records this kind of row yet. */
export interface FinanceIntelPage<T> extends FinancePage<T> { recording: boolean; note: string | null }

export interface FinanceStrategy {
  id: string;
  key: string | null;
  name: string;
  version: number | null;
  state: FinanceStrategyState;
  symbols: string[];
  params: Record<string, string | number | boolean | null>;
  reason: string | null;
  updated_at: string | null;
  simulated: true;
}

export interface FinanceStrategyFold { index: number; train_start: string | null; train_end: string | null; test_start: string | null; test_end: string | null; metrics: Record<string, number | null> }

export interface FinanceStrategyRun {
  id: string;
  kind: string;
  created_at: string | null;
  data_source: string | null;
  data_hash: string | null;
  metrics: Record<string, number | null>;
  benchmark: Record<string, number | null>;
  verdict: string | null;
  reasons: string[];
  folds: FinanceStrategyFold[];
  simulated: true;
}

export interface FinanceAgentDecision {
  id: string;
  agent: string;
  action: string;
  outcome: string | null;
  reasons: string[];
  strategy_id: string | null;
  intent_id: string | null;
  created_at: string | null;
  simulated: true;
}
