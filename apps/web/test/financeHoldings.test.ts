// Finance Holdings valuation and chart geometry (src/lib/financeHoldings.ts, src/lib/financeCharts.ts,
// docs/finance.md#holdings). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceAccount, FinanceHolding, FinancePrice } from '@anum/contracts';
import {
  allocation, daysBetween, filterPositions, foldSlices, holdingCurrencies, money, percent, priceHistory, recentActivity, shiftDay, totals, valueHoldings, valueSeries,
} from '../src/lib/financeHoldings.ts';
import { donut, labelIndices, nearest, niceTicks, plot, sparkline } from '../src/lib/financeCharts.ts';
import { setLocale, t } from '../src/lib/i18n.ts';
import { financeHoldingsAr } from '../src/lib/messages/financeHoldings.ar.ts';
import { financeHoldingsEn } from '../src/lib/messages/financeHoldings.en.ts';

afterEach(() => setLocale('en'));

const TODAY = '2026-11-05';
const account = (id: string, currency: string, balance: string, kind: FinanceAccount['kind'] = 'investment'): FinanceAccount => ({
  id, name: `Account ${id}`, kind, currency, institution: null, last4: null, opening_balance: '0.00', balance, archived: false, transaction_count: 0, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
});
const holding = (id: string, symbol: string, quantity: string, cost: string, currency = 'SAR', at = '2026-10-01T00:00:00Z'): FinanceHolding => ({
  id, account_id: 'a1', symbol, name: `${symbol} fund`, quantity, cost_basis: cost, currency, notes: null, created_at: at, updated_at: at,
});
const price = (symbol: string, value: string, day: string, recorded = `${day}T08:00:00Z`, currency = 'SAR'): FinancePrice => ({ id: `${symbol}-${day}-${recorded}`, symbol, currency, price: value, as_of: day, source: 'manual', recorded_at: recorded });

const accounts = [account('a1', 'SAR', '9620.00'), account('a2', 'SAR', '100.00', 'bank')];
const holdings = [holding('h1', 'ETF', '120', '11400.00'), holding('h2', 'GOLD', '42', '3990.00'), holding('h3', 'NEW', '10', '1000.00'), holding('h4', 'US1', '2', '100.00', 'USD')];
const prices = [
  price('ETF', '95', '2026-09-01'), price('ETF', '98', '2026-10-06'), price('ETF', '99', '2026-11-05', '2026-11-05T07:00:00Z'), price('ETF', '100', '2026-11-05', '2026-11-05T09:00:00Z'),
  price('GOLD', '100', '2026-10-26'), price('US1', '60', '2026-11-05', undefined, 'USD'), price('ETF', '1', '2026-11-05', undefined, 'USD'),
];

describe('valuation', () => {
  const history = priceHistory(prices);
  const valued = valueHoldings(holdings, accounts, history, TODAY);

  test('one price per day per symbol and currency, the latest recorded wins, oldest first', () => {
    assert.deepEqual(history.get('ETF|SAR')?.map((p) => p.price), ['95', '98', '100']);
    assert.deepEqual(history.get('ETF|USD')?.map((p) => p.price), ['1']);
  });

  test('a holding is valued only with a price in its own currency; stale and missing are flagged', () => {
    const [etf, gold, fresh] = valued;
    assert.equal(etf.value, '12000.00');
    assert.equal(etf.gain, '600.00');
    assert.equal(etf.change, '240.00');
    assert.equal(etf.averageCost, '95');
    assert.equal(etf.stale, false);
    assert.equal(gold.stale, true);
    assert.equal(gold.ageDays, 10);
    assert.equal(fresh.price, null);
    assert.equal(fresh.value, null);
    assert.equal(valued[3].value, '120.00');
  });

  test('totals never mix currencies and count only investment accounts as cash', () => {
    const sar = totals(valued, 'SAR', accounts, null);
    assert.equal(sar.value, '16200.00');
    assert.equal(sar.gain, '810.00');
    assert.equal(sar.unpriced, 1);
    assert.equal(sar.stale, 1);
    assert.equal(sar.change, '240.00');
    assert.equal(sar.cash, '9620.00');
    assert.equal(sar.oldestPrice, '2026-10-26');
    assert.equal(sar.cost, '16390.00');
    const usd = totals(valued, 'USD', accounts, null);
    assert.equal(usd.value, '120.00');
    assert.equal(usd.cash, null);
    assert.equal(totals([], 'SAR', accounts, 'other').cash, null);
    assert.deepEqual(holdingCurrencies(holdings), ['SAR', 'USD']);
  });

  test('allocation by value with cash, largest first; extra slices fold into Other', () => {
    const slices = allocation(valued, 'SAR', '9620.00', 'Cash');
    assert.deepEqual(slices.map((s) => s.label), ['ETF fund', 'Cash', 'GOLD fund']);
    assert.equal(Math.round(slices.reduce((a, s) => a + s.share, 0)), 100);
    const many = Array.from({ length: 7 }, (_, i) => ({ key: `k${i}`, label: `L${i}`, value: 7 - i, share: (7 - i) * 3.57 }));
    const folded = foldSlices(many, 5, 'Other');
    assert.equal(folded.length, 5);
    assert.equal(folded[4].key, 'other');
    assert.equal(folded[4].value, 3 + 2 + 1);
  });

  test('the value line uses today’s quantities, carries prices forward and leaves gaps before a first price', () => {
    const series = valueSeries(valued, 'SAR', history, TODAY, 0);
    assert.deepEqual(series.map((p) => p.day), ['2026-09-01', '2026-10-06', '2026-10-26', '2026-11-05']);
    assert.deepEqual(series.map((p) => p.value), [null, null, 120 * 98 + 42 * 100, 16200]);
    assert.deepEqual(valueSeries(valued, 'SAR', history, TODAY, 1).map((p) => p.day), ['2026-10-06', '2026-10-26', '2026-11-05']);
    assert.deepEqual(valueSeries([], 'SAR', history, TODAY, 0), []);
  });

  test('recent activity lists edits and prices of held symbols only, newest first', () => {
    const items = recentActivity([{ ...holdings[0], updated_at: '2026-11-06T00:00:00Z' }], prices, 3);
    assert.deepEqual(items.map((i) => i.kind), ['updated', 'price', 'price']);
    assert.ok(items.every((i) => i.currency === 'SAR'));
  });

  test('search and sort, with unpriced rows last', () => {
    assert.deepEqual(filterPositions(valued, 'gold', 'value', true).map((v) => v.holding.symbol), ['GOLD']);
    assert.deepEqual(filterPositions(valued, '', 'value', true).map((v) => v.holding.symbol), ['ETF', 'GOLD', 'US1', 'NEW']);
    assert.deepEqual(filterPositions(valued, '', 'asset', false).map((v) => v.holding.symbol), ['ETF', 'GOLD', 'NEW', 'US1']);
  });

  test('small helpers', () => {
    assert.equal(money(-0.001, 'SAR'), '0.00');
    assert.equal(money(1.5, 'KWD'), '1.500');
    assert.equal(daysBetween('2026-10-26', TODAY), 10);
    assert.equal(shiftDay('2026-03-31', -1), '2026-02-28');
    assert.equal(shiftDay('2026-01-15', -2), '2025-11-15');
    assert.equal(percent(2.0449), '+2.04%');
    assert.equal(percent(-1.9), '-1.90%');
    assert.equal(percent(0.001), '0.00%');
    assert.equal(percent(null), null);
  });
});

describe('chart geometry', () => {
  const box = { width: 300, height: 120, left: 40, right: 10, top: 10, bottom: 20 };

  test('ticks are round and cover the values', () => {
    assert.deepEqual(niceTicks(36_100, 45_900), [35_000, 37_500, 40_000, 42_500, 45_000, 47_500]);
    assert.deepEqual(niceTicks(0, 8), [0, 2, 4, 6, 8]);
    assert.deepEqual(niceTicks(5, 5).length > 1, true);
  });

  test('gaps split the line, the area closes on the baseline, and points keep their index', () => {
    const p = plot([1, 2, null, 4, 5], box);
    assert.equal((p.line.match(/M/g) ?? []).length, 2);
    assert.equal((p.area.match(/Z/g) ?? []).length, 2);
    assert.deepEqual(p.points.map((x) => x.index), [0, 1, 3, 4]);
    assert.equal(p.points[0].x, 40);
    assert.equal(p.points[3].x, 290);
    assert.equal(nearest(p.points, 200)?.index, 3);
    assert.equal(plot([3], box).line.startsWith('M'), true);
  });

  test('sparklines, donut segments and axis labels', () => {
    assert.equal(sparkline([1], 72, 24), '');
    assert.match(sparkline([1, 1, 1], 72, 24), /^M2,12L36,12L70,12$/);
    assert.equal(donut([1, 1], 50, 50, 40, 20).length, 2);
    assert.equal(donut([1], 50, 50, 40, 20)[0].includes('A40,40 0 1 1'), true);
    assert.deepEqual(donut([0, 0], 50, 50, 40, 20), []);
    assert.deepEqual(labelIndices(10, 3), [0, 5, 9]);
    assert.deepEqual(labelIndices(2, 6), [0, 1]);
  });
});

describe('catalogs', () => {
  test('Arabic has every key with the same placeholders, plurals work, and no long dashes', () => {
    assert.deepEqual(Object.keys(financeHoldingsAr).sort(), Object.keys(financeHoldingsEn).sort());
    const holes = (m: unknown) => new Set(JSON.stringify(m).match(/\{[a-z]+\}/gi) ?? []);
    for (const [key, en] of Object.entries(financeHoldingsEn)) {
      const ar = (financeHoldingsAr as Record<string, unknown>)[key];
      for (const hole of holes(ar)) assert.ok(holes(en).has(hole), `${key} ${hole}`);
      assert.doesNotMatch(JSON.stringify(en) + JSON.stringify(ar), /[–—]/, key);
    }
    assert.equal(t('fholdings.months', { count: 6 }), 'Last 6 months');
    setLocale('ar');
    assert.equal(t('fholdings.quality.unpriced', { count: 1 }), 'مركز واحد بلا سعر');
  });

  test('no promise of returns and no live price claim', () => {
    assert.doesNotMatch(JSON.stringify(financeHoldingsEn), /guarantee|profitable|real[- ]time|live price/i);
    assert.match(financeHoldingsEn['fholdings.notAdvice'], /not investment advice/);
  });
});
