// Symbol research view logic (docs/finance-research.md), pure so the unit suite covers it. The view only
// shows the server's figures with their sources; it never computes a projection or a target.
import type { BacktestInput, BacktestRule, ResearchFact } from '@anum/contracts';

export const PERIODS = ['1y', '3y', '5y', '10y'] as const;
export const RULES: readonly BacktestRule[] = ['monthly', 'buy_hold', 'rebalance', 'dip'];
const AMOUNT = /^\d{1,7}(\.\d{1,2})?$/;

/** "12.3%" with an explicit sign for changes, or null when the figure is not available. */
export function percentText(value: string | null | undefined, signed = true): string | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return `${signed && n > 0 ? '+' : ''}${value}%`;
}

/** The tone of a signed figure: 'positive', 'negative' or ''. */
export function tone(value: string | null | undefined): '' | 'positive' | 'negative' {
  const n = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'positive' : 'negative';
}

/** A fact's shown value, or null when it is missing (the view then shows "Not available" and the reason). */
export function factValue(fact: ResearchFact | null | undefined): string | null {
  if (!fact || fact.value === null) return null;
  if (fact.unit === 'percent') return percentText(fact.value);
  if (fact.unit === 'usd') return `USD ${groupDigits(fact.value)}`;
  if (fact.unit === 'count') return groupDigits(fact.value);
  return fact.value;
}

/** 987654321 -> "987,654,321" (decimals kept), digits only, no rounding. */
export function groupDigits(value: string): string {
  const [whole, fraction] = value.split('.');
  const sign = whole.startsWith('-') ? '-' : '';
  const digits = sign ? whole.slice(1) : whole;
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}${grouped}${fraction !== undefined ? `.${fraction}` : ''}`;
}

export type BacktestDraft = {
  rule: BacktestRule; first: string; second: string; monthly: string; total: string; perOrder: string; years: number;
  weight: number; every: 3 | 6 | 12; dipPercent: number; dipAmount: string; perShare: string; minimum: string; maxPercent: string;
};
export type DraftError = 'amount' | 'overOrder' | 'overTotal' | 'twoFunds' | 'monthlyOverOrder';

/** The form's own checks before the server checks everything again with the owner's limits. */
export function checkDraft(d: BacktestDraft, limits: { max_order: string; max_capital: string }): DraftError | null {
  const amounts = [d.monthly, d.total, d.perOrder, d.dipAmount];
  if (amounts.some((a) => !AMOUNT.test(a.trim()) || Number(a) <= 0)) return 'amount';
  if (Number(d.perOrder) > Number(limits.max_order)) return 'overOrder';
  if (Number(d.total) > Number(limits.max_capital)) return 'overTotal';
  if (Number(d.monthly) > Number(d.perOrder) || (d.rule === 'dip' && Number(d.dipAmount) > Number(d.perOrder))) return 'monthlyOverOrder';
  if (d.rule === 'rebalance' && (!d.second || d.second === d.first)) return 'twoFunds';
  return null;
}

export function toInput(d: BacktestDraft): BacktestInput {
  const body: BacktestInput = {
    rule: d.rule, symbols: d.rule === 'rebalance' ? [d.first, d.second] : [d.first], monthly_amount: d.monthly.trim(), total_limit: d.total.trim(),
    per_order_limit: d.perOrder.trim(), years: d.years, commission: { per_share: d.perShare.trim(), minimum: d.minimum.trim(), max_percent: d.maxPercent.trim() },
  };
  if (d.rule === 'rebalance') Object.assign(body, { weight_percent: d.weight, rebalance_months: d.every });
  if (d.rule === 'dip') Object.assign(body, { dip_percent: d.dipPercent, dip_amount: d.dipAmount.trim() });
  return body;
}

/** Points for a small line chart of account value and money put in (SVG path data, 0..width x 0..height). */
export function chartPaths(timeline: { value: string; put_in: string }[], width: number, height: number): { value: string; putIn: string; max: number } {
  if (timeline.length < 2) return { value: '', putIn: '', max: 0 };
  const values = timeline.map((p) => Number(p.value)), put = timeline.map((p) => Number(p.put_in));
  const max = Math.max(...values, ...put, 1);
  const x = (i: number) => (i / (timeline.length - 1)) * width;
  const y = (v: number) => height - (v / max) * height;
  const path = (series: number[]) => series.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return { value: path(values), putIn: path(put), max };
}
