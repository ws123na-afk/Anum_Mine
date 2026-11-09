// The cash-flow forecast and the emergency reserve on the web (docs/finance.md#forecast, #emergency-reserve).
// The figures come from GET /finance/forecast and GET /finance/reserve: nothing here works a forecast out, it
// only shapes the API's answer for the chart, the warnings and the reserve form. Pure, so the unit tests cover it.
import type { FinanceCurrencyForecast, FinanceForecast, FinanceForecastWarning, FinanceReserve, FinanceReserveSuggestion } from '@anum/contracts';
import { fromMilli, toMilli } from './financeStatements.ts';

/** One day of the chart: the expected balance, its low and high band, and what moves money that day. */
export interface ForecastChartPoint { day: string; balance: string; low: string; high: string; bills: { name: string; amount: string }[] }

export type ForecastHorizon = 'month' | 'three';

/** The currency's forecast, or null when the API has none for it (no cash account and nothing coming). */
export function currencyForecast(forecast: FinanceForecast | null, currency: string): FinanceCurrencyForecast | null {
  return forecast?.currencies.find((c) => c.currency === currency) ?? null;
}

/** The points to the end of this month, or over the whole three-month horizon, with each day's events. */
export function chartPoints(row: FinanceCurrencyForecast, until: string): ForecastChartPoint[] {
  const byDay = new Map<string, { name: string; amount: string }[]>();
  for (const e of row.events) byDay.set(e.on, [...(byDay.get(e.on) ?? []), { name: e.label, amount: e.amount }]);
  return row.points.filter((p) => p.on <= until).map((p) => ({ day: p.on, balance: p.expected, low: p.low, high: p.high, bills: byDay.get(p.on) ?? [] }));
}

/** The warning to show first: below zero, then below the reserve, then "may go below zero" (the low line only). */
export function firstWarning(row: FinanceCurrencyForecast, until?: string): FinanceForecastWarning | null {
  const order = ['below_zero', 'below_reserve', 'may_go_below_zero'] as const;
  const within = row.warnings.filter((w) => !until || w.on <= until);
  for (const kind of order) {
    const found = within.find((w) => w.kind === kind);
    if (found) return found;
  }
  return null;
}

/** The month-end figure for the horizon chosen: this month, or the last of the three months. */
export function horizonEnd(row: FinanceCurrencyForecast, horizon: ForecastHorizon) {
  return horizon === 'month' ? row.month_end : row.months[row.months.length - 1] ?? row.month_end;
}

/** Expected movement from today to the month end (month end less today's actual balance), exact. */
export function changeToMonthEnd(row: FinanceCurrencyForecast): string {
  return fromMilli(toMilli(row.month_end.expected) - toMilli(row.actual), row.currency);
}

/** The events up to a day, soonest first, money out and in alike (the table under the chart). */
export function eventsUntil(row: FinanceCurrencyForecast, until: string) {
  return row.events.filter((e) => e.on <= until);
}

/** A reserve's target for `months` of essential spending, exact to the currency's digits (null without essentials). */
export function monthsTarget(essential: string | null, months: number, currency: string): string | null {
  if (!essential || !Number.isInteger(months) || months < 1) return null;
  const milli = toMilli(essential);
  return milli > 0n ? fromMilli(milli * BigInt(months), currency) : null;
}

/** Whether today's recalculated target has moved more than 5% from the saved one (the page offers Update target). */
export function targetDrifted(reserve: Pick<FinanceReserve, 'target' | 'recalculated_target'>): boolean {
  if (!reserve.recalculated_target) return false;
  const saved = toMilli(reserve.target), now = toMilli(reserve.recalculated_target);
  if (saved <= 0n) return now > 0n;
  const gap = now > saved ? now - saved : saved - now;
  return gap * 100n > saved * 5n;
}

/** The progress bar's value, 0 to 100. */
export function progressValue(reserve: Pick<FinanceReserve, 'progress_percent'>): number {
  return Math.max(0, Math.min(100, reserve.progress_percent ?? 0));
}

/** The reserve in a currency, else the suggestion for it, else null. */
export function reserveFor(reserves: { reserves: FinanceReserve[]; suggestions: FinanceReserveSuggestion[] } | null, currency: string) {
  if (!reserves) return { reserve: null, suggestion: null };
  return { reserve: reserves.reserves.find((r) => r.currency === currency) ?? null, suggestion: reserves.suggestions.find((s) => s.currency === currency) ?? null };
}
