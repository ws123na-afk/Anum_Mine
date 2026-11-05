// Finance, Holdings (docs/finance.md#holdings): values the person's manual holdings with the prices they
// recorded (GET /finance/prices). Pure, so the unit tests cover it. ANUM has no market-price feed: a holding
// without a recorded price in its own currency has no value here, a stale price is flagged, and currencies
// are never mixed or converted.
import type { FinanceAccount, FinanceHolding, FinancePrice } from '@anum/contracts';
import { minorDigits } from './finance.ts';

/** A recorded price older than this many days is shown as stale (the paper simulator's default too). */
export const STALE_DAYS = 3;
export const POSITIONS_PAGE = 25;
/** Chart periods, in months; 0 is every recorded price. */
export const PERIODS = [3, 6, 12, 0] as const;
export type Period = (typeof PERIODS)[number];
export const DEFAULT_PERIOD: Period = 6;

const num = (value: string | null | undefined): number | null => {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

/** Fixed to the currency's minor digits, with no negative zero. */
export function money(value: number, currency: string): string {
  const text = value.toFixed(minorDigits(currency));
  return /^-0(\.0+)?$/.test(text) ? text.slice(1) : text;
}

/** Day difference between two ISO dates (YYYY-MM-DD), b minus a. */
export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b.slice(0, 10)}T00:00:00Z`) - Date.parse(`${a.slice(0, 10)}T00:00:00Z`)) / 86_400_000);
}

const priceKey = (symbol: string, currency: string) => `${symbol.toUpperCase()}|${currency.toUpperCase()}`;

/** Prices per symbol and currency, oldest first, one per day (the latest recorded wins on a day). */
export function priceHistory(prices: readonly FinancePrice[]): Map<string, FinancePrice[]> {
  const byKey = new Map<string, Map<string, FinancePrice>>();
  for (const p of prices) {
    const key = priceKey(p.symbol, p.currency);
    const days = byKey.get(key) ?? new Map<string, FinancePrice>();
    const day = p.as_of.slice(0, 10);
    const seen = days.get(day);
    if (!seen || seen.recorded_at < p.recorded_at) days.set(day, p);
    byKey.set(key, days);
  }
  const out = new Map<string, FinancePrice[]>();
  for (const [key, days] of byKey) out.set(key, [...days.values()].sort((a, b) => a.as_of.localeCompare(b.as_of)));
  return out;
}

export interface ValuedHolding {
  holding: FinanceHolding;
  accountName: string;
  /** Cost per unit, or null when the quantity is zero. */
  averageCost: string | null;
  price: FinancePrice | null;
  /** The recorded price before the latest one, for the change since then. */
  previous: FinancePrice | null;
  value: string | null;
  gain: string | null;
  gainPercent: number | null;
  change: string | null;
  stale: boolean;
  ageDays: number | null;
  /** Up to the last 12 recorded prices, oldest first, for the sparkline. */
  trend: number[];
}

export function valueHoldings(holdings: readonly FinanceHolding[], accounts: readonly FinanceAccount[], history: Map<string, FinancePrice[]>, today: string): ValuedHolding[] {
  const names = new Map(accounts.map((a) => [a.id, a.name]));
  return holdings.map((h) => {
    const series = history.get(priceKey(h.symbol, h.currency)) ?? [];
    const price = series[series.length - 1] ?? null, previous = series[series.length - 2] ?? null;
    const qty = num(h.quantity) ?? 0, cost = num(h.cost_basis) ?? 0;
    const p = price ? num(price.price) : null, before = previous ? num(previous.price) : null;
    const value = p === null ? null : qty * p;
    const ageDays = price ? daysBetween(price.as_of, today) : null;
    return {
      holding: h,
      accountName: names.get(h.account_id) ?? '',
      averageCost: qty > 0 ? String(cost / qty) : null,
      price, previous,
      value: value === null ? null : money(value, h.currency),
      gain: value === null ? null : money(value - cost, h.currency),
      gainPercent: value === null || cost === 0 ? null : ((value - cost) / cost) * 100,
      change: p === null || before === null ? null : money(qty * (p - before), h.currency),
      stale: ageDays !== null && ageDays > STALE_DAYS,
      ageDays,
      trend: series.slice(-12).map((s) => num(s.price) ?? 0),
    };
  });
}

/** The currencies the holdings are in, the most held first (then A to Z). */
export function holdingCurrencies(holdings: readonly FinanceHolding[]): string[] {
  const counts = new Map<string, number>();
  for (const h of holdings) counts.set(h.currency, (counts.get(h.currency) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([c]) => c);
}

export interface HoldingsTotals {
  currency: string;
  positions: number;
  priced: number;
  unpriced: number;
  stale: number;
  value: string | null;
  cost: string;
  /** Value minus cost of the priced positions only. */
  gain: string | null;
  gainPercent: number | null;
  /** Change since each priced position's previous recorded price; null when none has two prices. */
  change: string | null;
  changePercent: number | null;
  /** The oldest latest price among the priced positions. */
  oldestPrice: string | null;
  cash: string | null;
}

/** Totals for one currency. Cash is the balance of the investment accounts in that currency. */
export function totals(valued: readonly ValuedHolding[], currency: string, accounts: readonly FinanceAccount[], accountId: string | null): HoldingsTotals {
  const rows = valued.filter((v) => v.holding.currency === currency);
  const priced = rows.filter((v) => v.value !== null);
  const sum = (items: readonly (string | null)[]) => items.reduce((acc, x) => acc + (num(x) ?? 0), 0);
  const value = sum(priced.map((v) => v.value));
  const pricedCost = sum(priced.map((v) => v.holding.cost_basis));
  const withChange = priced.filter((v) => v.change !== null);
  const change = sum(withChange.map((v) => v.change));
  const before = value - change;
  const cashAccounts = accounts.filter((a) => a.kind === 'investment' && !a.archived && a.currency === currency && (!accountId || a.id === accountId));
  return {
    currency,
    positions: rows.length,
    priced: priced.length,
    unpriced: rows.length - priced.length,
    stale: priced.filter((v) => v.stale).length,
    value: priced.length ? money(value, currency) : null,
    cost: money(sum(rows.map((v) => v.holding.cost_basis)), currency),
    gain: priced.length ? money(value - pricedCost, currency) : null,
    gainPercent: priced.length && pricedCost !== 0 ? ((value - pricedCost) / pricedCost) * 100 : null,
    change: withChange.length ? money(change, currency) : null,
    changePercent: withChange.length && before !== 0 ? (change / before) * 100 : null,
    oldestPrice: priced.length ? priced.map((v) => v.price!.as_of.slice(0, 10)).sort()[0] : null,
    cash: cashAccounts.length ? money(sum(cashAccounts.map((a) => a.balance)), currency) : null,
  };
}

export interface Slice { key: string; label: string; value: number; share: number }

/** Allocation by position value in one currency, largest first; positions without a price are left out. */
export function allocation(valued: readonly ValuedHolding[], currency: string, cash: string | null, cashLabel: string): Slice[] {
  const items = valued.filter((v) => v.holding.currency === currency && v.value !== null && (num(v.value) ?? 0) > 0)
    .map((v) => ({ key: v.holding.id, label: v.holding.name || v.holding.symbol, value: num(v.value) ?? 0 }));
  const c = num(cash);
  if (c !== null && c > 0) items.push({ key: 'cash', label: cashLabel, value: c });
  const total = items.reduce((acc, x) => acc + x.value, 0);
  return items.sort((a, b) => b.value - a.value).map((x) => ({ ...x, share: total ? (x.value / total) * 100 : 0 }));
}

export interface ValuePoint { day: string; value: number | null }

/** Today's quantities valued at each day a price was recorded: the latest price on or before that day per
 * position. A day before some position has any price is a gap (null), never a guess. */
export function valueSeries(valued: readonly ValuedHolding[], currency: string, history: Map<string, FinancePrice[]>, today: string, period: Period): ValuePoint[] {
  const rows = valued.filter((v) => v.holding.currency === currency && v.price !== null);
  if (!rows.length) return [];
  const series = rows.map((v) => ({ qty: num(v.holding.quantity) ?? 0, prices: history.get(priceKey(v.holding.symbol, currency)) ?? [] }));
  const from = period === 0 ? '' : shiftDay(today, -period);
  const days = [...new Set(series.flatMap((s) => s.prices.map((p) => p.as_of.slice(0, 10))))].filter((d) => d >= from && d <= today).sort();
  return days.map((day) => {
    let total = 0;
    for (const s of series) {
      let price: number | null = null;
      for (const p of s.prices) { if (p.as_of.slice(0, 10) <= day) price = num(p.price); else break; }
      if (price === null) return { day, value: null };
      total += s.qty * price;
    }
    return { day, value: Number(money(total, currency)) };
  });
}

/** The date `months` months from an ISO date (negative goes back), clamped to the month's last day. */
export function shiftDay(day: string, months: number): string {
  const [y, m, d] = day.slice(0, 10).split('-').map(Number);
  const index = y * 12 + (m - 1) + months;
  const year = Math.floor(index / 12), month = index - year * 12 + 1;
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(Math.min(d, last)).padStart(2, '0')}`;
}

export interface Activity { id: string; at: string; kind: 'added' | 'updated' | 'price'; label: string; detail: string | null; currency: string }

/** What changed lately: holdings added or edited and prices recorded for held symbols, newest first. */
export function recentActivity(holdings: readonly FinanceHolding[], prices: readonly FinancePrice[], limit = 8): Activity[] {
  const held = new Set(holdings.map((h) => priceKey(h.symbol, h.currency)));
  const items: Activity[] = holdings.map((h) => ({
    id: `h-${h.id}`, at: h.updated_at, kind: h.updated_at > h.created_at ? 'updated' as const : 'added' as const, label: h.name || h.symbol, detail: null, currency: h.currency,
  }));
  for (const p of prices) {
    if (held.has(priceKey(p.symbol, p.currency))) items.push({ id: `p-${p.id}`, at: p.recorded_at, kind: 'price', label: p.symbol, detail: p.price, currency: p.currency });
  }
  return items.sort((a, b) => b.at.localeCompare(a.at)).slice(0, limit);
}

export type SortKey = 'asset' | 'value' | 'gain' | 'quantity';

/** Search by symbol, name or account, then sort; positions without a value sort last. */
export function filterPositions(valued: readonly ValuedHolding[], search: string, sort: SortKey, descending: boolean): ValuedHolding[] {
  const q = search.trim().toLowerCase();
  const rows = q ? valued.filter((v) => [v.holding.symbol, v.holding.name ?? '', v.accountName].some((s) => s.toLowerCase().includes(q))) : [...valued];
  const pick = (v: ValuedHolding): number | string | null => sort === 'asset' ? (v.holding.name || v.holding.symbol).toLowerCase()
    : sort === 'quantity' ? num(v.holding.quantity) : sort === 'gain' ? num(v.gain) : num(v.value);
  return rows.sort((a, b) => {
    const x = pick(a), y = pick(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    const order = typeof x === 'string' ? x.localeCompare(String(y)) : x - (y as number);
    return descending ? -order : order;
  });
}

/** Signed percentage for reading ("+2.04%"), or null. */
export function percent(value: number | null, digits = 2): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  const text = Math.abs(value).toFixed(digits);
  if (Number(text) === 0) return `${(0).toFixed(digits)}%`;
  return `${value > 0 ? '+' : '-'}${text}%`;
}

/** At most `max` slices keep their own colour; the rest fold into one "Other" slice (never a new hue). */
export function foldSlices(slices: readonly Slice[], max: number, otherLabel: string): Slice[] {
  if (slices.length <= max) return [...slices];
  const kept = slices.slice(0, max - 1), rest = slices.slice(max - 1);
  return [...kept, { key: 'other', label: otherLabel, value: rest.reduce((a, s) => a + s.value, 0), share: rest.reduce((a, s) => a + s.share, 0) }];
}
