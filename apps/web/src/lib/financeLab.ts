// Paper trading and portfolio lab (docs/finance.md#paper-trading-lab): pure helpers so the unit tests cover them.
// Every figure is SIMULATED and comes from the paper portfolio the API returns; currencies are never mixed.
import type { FinancePaperOrder, FinancePortfolio } from '@anum/contracts';
import { money, percent, type Slice } from './financeHoldings.ts';

export const LAB_TABS = ['overview', 'holdings', 'research', 'strategies', 'orders', 'brokers'] as const;
export type LabTab = (typeof LAB_TABS)[number];

/** `#finance-trading` is the overview; `#finance-trading-orders` and so on open a tab. */
export function labTabFromHash(hash: string): LabTab {
  const id = hash.replace(/^#finance-trading-?/, '');
  return (LAB_TABS as readonly string[]).includes(id) ? id as LabTab : 'overview';
}
export const labHash = (tab: LabTab) => (tab === 'overview' ? '#finance-trading' : `#finance-trading-${tab}`);

export interface PaperFigures {
  currency: string;
  start: string | null;
  cash: string | null;
  value: string | null;
  total: string | null;
  unpriced: number;
  returnPercent: string | null;
  returnTone: 'up' | 'down' | '';
  slices: Slice[];
}

/** Value, return and allocation in the portfolio's first starting currency. A position without a fresh recorded
 * price makes the value and return unknown (a gap, never a guess). */
export function paperFigures(p: Pick<FinancePortfolio, 'mode' | 'starting_cash' | 'cash' | 'holdings'>): PaperFigures {
  const currency = p.starting_cash[0]?.currency ?? p.cash[0]?.currency ?? p.holdings[0]?.currency ?? 'USD';
  const n = (v: string | null | undefined) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v));
  const held = p.holdings.filter((h) => h.currency === currency);
  const unpriced = held.filter((h) => n(h.market_value) === null).length;
  const cash = p.mode === 'paper' ? n(p.cash.find((c) => c.currency === currency)?.amount) ?? 0 : null;
  const start = p.mode === 'paper' ? n(p.starting_cash.find((c) => c.currency === currency)?.amount) : null;
  const invested = held.reduce((acc, h) => acc + (n(h.market_value) ?? 0), 0);
  const known = unpriced === 0;
  const value = known && (cash !== null || held.length) ? (cash ?? 0) + invested : null;
  const ret = value !== null && start ? ((value - start) / start) * 100 : null;
  const items: Slice[] = held.filter((h) => (n(h.market_value) ?? 0) > 0).map((h) => ({ key: `${h.instrument_kind}-${h.symbol}`, label: h.symbol, value: n(h.market_value) ?? 0, share: 0 }));
  if (cash !== null && cash > 0) items.push({ key: 'cash', label: 'cash', value: cash, share: 0 });
  const total = items.reduce((acc, s) => acc + s.value, 0);
  return {
    currency,
    start: start === null ? null : money(start, currency),
    cash: cash === null ? null : money(cash, currency),
    value: value === null ? null : money(value, currency),
    total: total ? money(total, currency) : null,
    unpriced,
    returnPercent: percent(ret),
    returnTone: ret === null || Math.abs(ret) < 0.005 ? '' : ret > 0 ? 'up' : 'down',
    slices: items.sort((a, b) => b.value - a.value).map((s) => ({ ...s, share: total ? (s.value / total) * 100 : 0 })),
  };
}

export interface OrderSort { key: 'date' | 'symbol' | 'side' | 'quantity' | 'price' | 'status'; descending: boolean }

/** Sort the orders shown (one page of the API's newest first). */
export function sortOrders(orders: readonly FinancePaperOrder[], sort: OrderSort): FinancePaperOrder[] {
  const pick = (o: FinancePaperOrder): string | number => {
    switch (sort.key) {
      case 'date': return o.filled_at ?? o.created_at;
      case 'symbol': return o.symbol;
      case 'side': return o.side;
      case 'status': return o.status;
      case 'quantity': return Number(o.quantity);
      case 'price': return Number(o.fill_price ?? o.limit_price ?? Number.NEGATIVE_INFINITY);
    }
  };
  return [...orders].sort((a, b) => {
    const x = pick(a), y = pick(b);
    const order = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
    return sort.descending ? -order : order;
  });
}
