// IBKR trading (docs/finance-live-trading.md): the shapes of /api/v1/finance/ibkr. Amounts, prices and
// quantities are exact decimal strings in USD. Every figure comes from IBKR's own records.

export type IbkrEnvironment = 'paper' | 'live';
export type IbkrSession = 'unknown' | 'authenticated' | 'expired' | 'unreachable';
export type IbkrOrderState =
  | 'submitting' | 'submitted' | 'partially_filled' | 'filled' | 'cancel_requested' | 'cancelled' | 'rejected' | 'unknown';

export interface IbkrCheck { code: string; ok: boolean; message: string }

export interface IbkrLimits {
  max_capital: string;
  max_order: string;
  max_daily_loss: string;
  max_orders_per_day: number;
  price_band_percent: string;
  session_buffer_minutes: number;
  fee_buffer: string;
}

/** shariah_screened: by the fund issuer's own Shariah board; ANUM does not certify it. */
export interface IbkrAllowedSymbol { symbol: string; conid: number; name: string; exchange: string; shariah_screened: boolean }

export interface IbkrBuyPlan {
  id: string; symbol: string; amount: string; day_of_month: number; active: boolean; last_proposed: string | null;
  /** Made by a research backtest's Paper run (docs/finance-research.md#paper-run): proposes paper orders only. */
  paper_only?: boolean; origin?: string | null;
}

export interface IbkrBuyPlanInput { id?: string | null; symbol: string; amount: string; day_of_month: number; active: boolean; paper_only?: boolean }

export interface IbkrLiveSwitch {
  on: boolean;
  requested_at: string | null;
  switched_on_at: string | null;
  effective_at: string | null;
  switched_off_at: string | null;
}

export interface IbkrReconciliation {
  state: 'not_checked' | 'ok' | 'mismatch';
  checked_at: string | null;
  mismatches: { code: string; ref: string; message: string }[];
  baseline_at: string | null;
  episodes: number;
  explained: number;
}

export interface IbkrSettings {
  limits: IbkrLimits;
  allowed: IbkrAllowedSymbol[];
  buy_plans: IbkrBuyPlan[];
  fractional_ok: boolean;
  cooling_off_hours: number;
  live: IbkrLiveSwitch;
  kill_switch: boolean;
  kill_switch_at: string | null;
  reconciliation: Partial<Record<IbkrEnvironment, IbkrReconciliation>>;
  version: number;
  updated_at: string | null;
}

export interface IbkrConnection {
  id: string;
  environment: IbkrEnvironment;
  gateway_host: string;
  /** Masked account id, like DU...567. The full id never leaves the API. */
  hint: string;
  status: 'active' | 'revoked';
  session: IbkrSession;
  session_message: string | null;
  session_checked_at: string | null;
  verified_at: string | null;
  trading_type: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  statements_connected: boolean;
  created_at: string;
}

export interface IbkrPosition {
  conid: number; symbol: string; quantity: string; market_price: string | null; market_value: string | null; average_cost: string | null;
  unrealized_pnl: string | null; realized_pnl: string | null; currency: string;
}

export interface IbkrBrokerOrder {
  order_id: string; conid: number | null; symbol: string; side: string; status: string; quantity: string | null; filled_quantity: string;
  remaining_quantity: string | null; limit_price: string | null; order_ref: string | null;
}

export interface IbkrSnapshot {
  account_ok?: boolean;
  cash?: Record<string, string>;
  settled_cash?: Record<string, string>;
  net_liquidation?: string | null;
  daily_pnl?: string | null;
  unrealized_pnl?: string | null;
  realized_pnl?: string | null;
  dividends_ledger?: string | null;
  positions?: IbkrPosition[];
  open_orders?: IbkrBrokerOrder[];
  executions?: { execution_id: string; symbol: string; side: string; quantity: string; price: string; trade_time: string }[];
  synced_at?: string | null;
  statements_connected?: boolean;
}

export interface IbkrStatement {
  ready?: boolean;
  message?: string;
  from_date?: string | null;
  to_date?: string | null;
  trades?: number;
  dividends_received?: string;
  withholding_tax?: string;
  fetched_at?: string;
}

export interface IbkrIntent {
  id: string;
  environment: IbkrEnvironment;
  mode: 'paper' | 'live';
  symbol: string;
  side: 'buy' | 'sell';
  order_type: 'limit';
  quantity: string;
  limit_price: string;
  amount: string;
  currency: 'USD';
  status: string;
  payload_hash: string;
  created_at: string;
  expires_at: string;
  requested_by: 'person' | 'agent';
  reason: string | null;
  refusal: string | null;
  refusal_code: string | null;
  estimate: Record<string, unknown> | null;
}

export interface IbkrOverview {
  mode: IbkrEnvironment;
  deployment_live_enabled: boolean;
  settings: IbkrSettings;
  connections: IbkrConnection[];
  snapshots: Partial<Record<IbkrEnvironment, IbkrSnapshot>>;
  statements: Partial<Record<IbkrEnvironment, IbkrStatement>>;
  live_gates: IbkrCheck[];
  switch_gates: IbkrCheck[];
  paper_proof: IbkrCheck[];
  market: { open: boolean; message: string; closes_at: string | null };
  pending: IbkrIntent[];
  unread_alerts: number;
  gateway_hosts: string[];
  etfs: Record<string, string>;
  shariah_etfs: string[];
  approval_minutes: number;
  paper_min_orders: number;
  paper_min_days: number;
  risk_note: string;
}

export interface IbkrConnectInput {
  environment: IbkrEnvironment;
  gateway_host: string;
  tls_fingerprint: string;
  account_id: string;
  flex_token: string;
  flex_query_id: string;
  cash_account_confirmed: boolean;
}

export interface IbkrOrderInput { symbol: string; side: 'buy' | 'sell'; quantity: string; limit_price: string; reason?: string | null }

export interface IbkrOrder {
  id: string; connection_id: string; environment: IbkrEnvironment; symbol: string; conid: number; side: 'buy' | 'sell'; quantity: string;
  limit_price: string; state: IbkrOrderState; broker_order_id: string | null; filled_quantity: string; average_price: string | null;
  message: string | null; attempts: number; submitted_at: string | null; created_at: string; updated_at: string;
}

export interface IbkrPage<T> { items: T[]; total: number; limit: number; offset: number }

export interface IbkrEvent {
  id: string; environment: IbkrEnvironment | null; kind: string; severity: 'info' | 'warning' | 'critical'; message: string; ref: string | null;
  alert: boolean; created_at: string; read_at: string | null;
}

export interface IbkrEventPage extends IbkrPage<IbkrEvent> { unread: number }

export interface IbkrQuote {
  symbol: string; last: string | null; bid: string | null; ask: string | null; delayed: boolean; availability: string | null;
  age_seconds: number | null; band_percent: string;
}

export interface IbkrKillReport { kill_switch: true; report: { environment: IbkrEnvironment; error?: string; orders: { order_id: string; cancelled: boolean }[] }[] }
