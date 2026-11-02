// Symbol research (docs/finance-research.md): figures with their source and date, the AI summary that
// may only cite numbers in the data, backtests of simple rules and the paper run. Not financial advice.
import type { IbkrBuyPlan, IbkrEnvironment } from './financeIbkr.js';

/** One figure: a value with its source and date, or `value: null` with the reason it is not available. */
export interface ResearchFact { value: string | null; source: string | null; as_of: string | null; unavailable: string | null; unit: string | null }

export interface ResearchShariah { screened: boolean; label: string; screen: string | null; issuer: string | null; source: string | null; note: string }

export interface ResearchSymbol { symbol: string; name: string; allowed: boolean; shariah: ResearchShariah }

export interface ResearchSymbols {
  mode: IbkrEnvironment;
  symbols: ResearchSymbol[];
  etfs: Record<string, string>;
  shariah_etfs: string[];
  limits: { max_order: string; max_capital: string };
  summary: { daily_limit: number };
  disclaimer: string;
  plans: IbkrBuyPlan[];
}

export interface ResearchDrawdown {
  peak_date: string; peak: string; trough_date: string; trough: string; fall_percent: string;
  recovered_date: string | null; months_to_recover: number | null; months_falling: number;
}

export interface ResearchSource { key: string; name: string; url: string | null; used: boolean }

export type ResearchSuggestion = 'buy' | 'wait' | 'avoid';

export interface ResearchSummaryText { good: string[]; bad: string[]; history: string; range: string; suggestion: ResearchSuggestion; reasons: string[] }

export interface ResearchSummary {
  status: 'ok' | 'withheld' | 'failed';
  reason: string | null;
  summary: ResearchSummaryText | null;
  unknown_numbers: string[];
  label: string;
  created_at: string;
  /** True when it was written from the figures shown now. */
  current: boolean;
}

export interface ResearchReport {
  symbol: string;
  name: string;
  mode: IbkrEnvironment;
  shariah: ResearchShariah;
  gathered_at: string;
  expires_at: string;
  disclaimer: string;
  prices: {
    source: string | null; label?: string; adjusted?: string; from?: string; to?: string; points?: number; fetched_at?: string;
    last_close: ResearchFact; unavailable: string | null;
  };
  returns: Record<string, { total: ResearchFact; yearly: ResearchFact }>;
  drawdowns: { source: string | null; items: ResearchDrawdown[]; unavailable: string | null };
  volatility: { yearly: ResearchFact; worst_month: ResearchFact; best_month: ResearchFact; months: number };
  fund: {
    source: string | null; url?: string | null; filed?: string | null; unavailable: string | null;
    issuer: ResearchFact; holdings_count: ResearchFact; net_assets: ResearchFact; expense_ratio: ResearchFact;
    top_holdings: { items: { name: string; percent: string }[]; source: string | null; as_of: string | null };
  };
  dividends: {
    fund_history: ResearchFact; yield: ResearchFact;
    yours: { items: { date: string | null; amount: string | null; kind: string | null }[]; source: string; as_of: string | null; unavailable: string | null };
    us_withholding_percent: string; withholding_note: string;
  };
  news: { items: { title: string; url: string; source: string | null; published: string | null }[]; source: string | null; unavailable: string | null; untrusted: boolean };
  sources: ResearchSource[];
  source_problems: { source: string; reason: string }[];
  data_hash: string;
  summary: ResearchSummary | null;
}

export type BacktestRule = 'monthly' | 'buy_hold' | 'rebalance' | 'dip';

export interface BacktestInput {
  rule: BacktestRule;
  symbols: string[];
  monthly_amount: string;
  total_limit: string;
  per_order_limit: string;
  years: number;
  weight_percent?: number;
  rebalance_months?: 3 | 6 | 12;
  dip_percent?: number;
  dip_amount?: string;
  commission?: { per_share: string; minimum: string; max_percent: string };
}

export interface BacktestFigures {
  final_value: string; put_in: string; total_return_percent: string | null; worst_fall_percent: string; months_below_put_in: number; fees: string;
}

export interface BacktestResult {
  ok: boolean;
  reason?: string;
  warning: string;
  rule?: BacktestRule;
  symbols?: string[];
  from?: string;
  to?: string;
  result?: BacktestFigures & {
    cash_left: string; gain: string; worst_fall_from: string | null; worst_fall_to: string | null; months: number;
    dividends_net: string; tax_withheld: string; orders: number; skipped_buys: number; shares: Record<string, number>;
    timeline: { month: string; value: string; put_in: string }[];
  };
  simply_holding?: BacktestFigures | null;
  dividends_included?: boolean;
  dividends_note?: string;
  assumptions?: { whole_shares_only: boolean; price: string; commission: Record<string, string>; withholding_percent: string; per_order_limit: string; total_limit: string };
  prices?: Record<string, { source: string | null; adjusted: string | null; to: string | null }>;
  shariah?: Record<string, boolean>;
  paper_run?: { available: boolean; reason: string | null };
}

export interface ResearchPortfolio {
  mode: IbkrEnvironment;
  connected: boolean;
  synced_at: string | null;
  net_liquidation?: string | null;
  daily_pnl?: string | null;
  unrealized_pnl?: string | null;
  realized_pnl?: string | null;
  positions: { symbol: string | null; quantity: string | null; market_value: string | null; unrealized_pnl: string | null; shariah: boolean }[];
  dividends_received?: string | null;
  tax_withheld?: string | null;
}
