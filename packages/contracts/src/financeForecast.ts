// The cash-flow forecast and the emergency reserve (docs/finance.md#forecast, #emergency-reserve).
// GET /api/v1/finance/forecast and GET /api/v1/finance/reserve. Every figure is per currency and never
// converted; amounts are decimal strings. An estimate from the person's own records, never a promise.
import type { FinanceMoney } from './finance.js';

export type FinanceForecastEventKind = 'salary' | 'income' | 'expected_income' | 'bill' | 'minimum_payment' | 'installment' | 'remittance';
export type FinanceForecastEventStatus = 'expected' | 'overdue' | 'late';
export type FinanceForecastWarningKind = 'below_zero' | 'below_reserve' | 'may_go_below_zero';

/** One expected movement: money in is positive, money out negative. An overdue or late one counts today. */
export interface FinanceForecastEvent {
  on: string;
  kind: FinanceForecastEventKind;
  label: string;
  ref_id: string | null;
  currency: string;
  amount: FinanceMoney;
  status: FinanceForecastEventStatus;
  /** salary_expected, salary_late, income_recurring, bill_due, bill_overdue, minimum_due, minimum_overdue, minimum_no_day, person_loan_due, person_loan_overdue, installment_due, installment_overdue, remittance_regular */
  note: string;
  params: Record<string, string>;
}

export interface FinanceForecastPoint { on: string; expected: FinanceMoney; low: FinanceMoney; high: FinanceMoney }

export interface FinanceForecastMonthEnd {
  month: string;
  on: string;
  expected: FinanceMoney;
  low: FinanceMoney;
  high: FinanceMoney;
  money_in: FinanceMoney;
  money_out: FinanceMoney;
  spending: FinanceMoney;
  /** Interest expected on cards with a known APR: owed to the card, not taken from bank money. */
  card_interest?: FinanceMoney;
}

/** Interest a card is expected to charge on a day (balance after the minimum times APR over 12). */
export interface FinanceInterestCharge { on: string; label: string; ref_id: string; balance: FinanceMoney; apr_percent: string; amount: FinanceMoney }

/** One-off income the person expects on a date (GET|POST /expected-income, PATCH|DELETE /expected-income/{id}). */
export interface FinanceExpectedIncome {
  id: string;
  label: string;
  amount: FinanceMoney;
  currency: string;
  expected_on: string;
  received_on: string | null;
  notes: string | null;
  state: 'expected' | 'late' | 'received';
  created_at: string;
  updated_at: string;
}

export interface FinanceExpectedIncomeInput {
  label: string;
  amount: string;
  currency?: string | null;
  expected_on: string;
  received_on?: string | null;
  notes?: string | null;
}

export interface FinanceExpectedIncomePage { items: FinanceExpectedIncome[]; total: number; limit: number; offset: number; note: string }

export interface FinanceForecastWarning { kind: FinanceForecastWarningKind; on: string; balance: FinanceMoney; threshold: FinanceMoney }

/** What the forecast assumed (or left out), as a code the page words, with the English `detail` for the assistant. */
export interface FinanceForecastAssumption { code: string; params: Record<string, string>; detail: string }

export interface FinanceForecastSpend {
  basis: 'months' | 'last_30_days' | 'none';
  months: string[];
  monthly: FinanceMoney;
  daily: FinanceMoney;
  /** The quietest month's pace (the high line) and the busiest month's (the low line). */
  daily_low: FinanceMoney;
  daily_high: FinanceMoney;
}

export interface FinanceCurrencyForecast {
  currency: string;
  /** Today's bank and cash balance: actual, not an estimate. */
  actual: FinanceMoney;
  accounts_counted: number;
  reserve_target: FinanceMoney | null;
  spend: FinanceForecastSpend;
  month_end: FinanceForecastMonthEnd;
  months: FinanceForecastMonthEnd[];
  points: FinanceForecastPoint[];
  events: FinanceForecastEvent[];
  warnings: FinanceForecastWarning[];
  assumptions: FinanceForecastAssumption[];
  left_out: FinanceForecastAssumption[];
  card_interest?: FinanceInterestCharge[];
}

export interface FinanceForecast {
  today: string;
  month_end: string;
  horizon_end: string;
  timezone: string;
  base_currency: string;
  currencies: FinanceCurrencyForecast[];
  note: string;
}

export interface FinanceReserve {
  goal_id: string;
  name: string;
  currency: string;
  mode: 'months' | 'amount';
  months: number | null;
  /** The goal's target: what Safe to spend and the forecast use. */
  target: FinanceMoney;
  /** Months x today's essential spending (months mode), to compare with the saved target. */
  recalculated_target: FinanceMoney | null;
  essential_monthly: FinanceMoney | null;
  essential_basis: 'essential' | 'bare' | 'none';
  saved: FinanceMoney;
  progress_percent: number | null;
  months_covered: string | null;
  held_in: 'account' | 'goal';
  account_id: string | null;
  account_name: string | null;
  paused: boolean;
  monthly_contribution: FinanceMoney | null;
  status: 'reached' | 'building' | 'empty';
}

export interface FinanceReserveSuggestion { currency: string; essential_monthly: FinanceMoney; essential_basis: 'essential' | 'bare'; months: number; target: FinanceMoney }

export interface FinanceReserves { reserves: FinanceReserve[]; suggestions: FinanceReserveSuggestion[]; default_months: number; note: string }
