// Paper trading and portfolio lab helpers (src/lib/financeLab.ts, docs/finance.md#paper-trading-lab).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinancePaperOrder, FinancePosition } from '@anum/contracts';
import { hashForTab, tabFromHash } from '../src/lib/finance.ts';
import { LAB_TABS, labHash, labTabFromHash, paperFigures, sortOrders } from '../src/lib/financeLab.ts';
import { OWN_HEAD_TABS } from '../src/lib/financeNav.ts';
import { setLocale, t } from '../src/lib/i18n.ts';
import { financeLabAr } from '../src/lib/messages/financeLab.ar.ts';
import { financeLabEn } from '../src/lib/messages/financeLab.en.ts';

afterEach(() => setLocale('en'));

const position = (symbol: string, value: string | null, currency = 'USD'): FinancePosition => ({
  instrument_kind: 'security', symbol, currency, quantity: '10', cost: '1000.00', average_price: '100', price: value ? '105' : null, price_as_of: value ? '2026-11-04' : null,
  price_source: value ? 'manual' : null, market_value: value, unrealised: null,
});
const order = (id: string, symbol: string, quantity: string, price: string | null, at: string, status: FinancePaperOrder['status'] = 'filled') => ({
  id, portfolio_id: 'p', intent_id: 'i', epoch: 1, mode: 'paper', instrument_kind: 'security', symbol, currency: 'USD', side: 'buy', order_type: 'market', quantity, limit_price: null, status,
  reason_code: null, reason: null, requested_by: 'person', market_price: price, fill_price: price, price_as_of: null, price_source: null, notional: null, created_at: at, filled_at: at,
}) as unknown as FinancePaperOrder;

describe('lab tabs', () => {
  test('every lab tab has a deep link that opens Paper trading', () => {
    for (const tab of LAB_TABS) {
      assert.equal(labTabFromHash(labHash(tab)), tab);
      assert.equal(tabFromHash(labHash(tab)), 'trading');
    }
    assert.equal(labHash('overview'), hashForTab('trading'));
    assert.equal(labTabFromHash('#finance-trading-unknown'), 'overview');
    assert.equal(tabFromHash('#finance-trading-orders'), 'trading');
    assert.ok(OWN_HEAD_TABS.has('trading') && OWN_HEAD_TABS.has('holdings'));
  });
});

describe('simulated figures', () => {
  test('value is cash plus priced positions; the return is against starting cash', () => {
    const f = paperFigures({ mode: 'paper', starting_cash: [{ currency: 'USD', amount: '10000.00' }], cash: [{ currency: 'USD', amount: '8998.50' }], holdings: [position('EXMPL', '1000.00'), position('EUR1', '50.00', 'EUR')] });
    assert.equal(f.value, '9998.50');
    assert.equal(f.returnPercent, '-0.01%');
    assert.equal(f.returnTone, 'down');
    assert.deepEqual(f.slices.map((s) => s.key), ['cash', 'security-EXMPL']);
    assert.equal(Math.round(f.slices[0].share), 90);
  });

  test('a position without a fresh price makes the value unknown, never guessed', () => {
    const f = paperFigures({ mode: 'paper', starting_cash: [{ currency: 'USD', amount: '100.00' }], cash: [{ currency: 'USD', amount: '50.00' }], holdings: [position('NOPRICE', null)] });
    assert.equal(f.value, null);
    assert.equal(f.returnPercent, null);
    assert.equal(f.unpriced, 1);
  });

  test('a shadow portfolio has no cash and no return', () => {
    const f = paperFigures({ mode: 'shadow', starting_cash: [], cash: [], holdings: [position('EXMPL', '1050.00')] });
    assert.equal(f.cash, null);
    assert.equal(f.value, '1050.00');
    assert.equal(f.returnPercent, null);
  });
});

describe('order sorting', () => {
  const orders = [order('a', 'ZED', '5', '10', '2026-11-01T00:00:00Z'), order('b', 'ABC', '50', null, '2026-11-03T00:00:00Z', 'waiting'), order('c', 'MID', '1', '200', '2026-11-02T00:00:00Z')];
  test('by date, symbol, quantity and price in both directions', () => {
    assert.deepEqual(sortOrders(orders, { key: 'date', descending: true }).map((o) => o.id), ['b', 'c', 'a']);
    assert.deepEqual(sortOrders(orders, { key: 'symbol', descending: false }).map((o) => o.id), ['b', 'c', 'a']);
    assert.deepEqual(sortOrders(orders, { key: 'quantity', descending: true }).map((o) => o.id), ['b', 'a', 'c']);
    assert.deepEqual(sortOrders(orders, { key: 'price', descending: true }).map((o) => o.id), ['c', 'a', 'b']);
    assert.equal(orders[0].id, 'a');
  });
});

describe('catalogs', () => {
  test('Arabic has every key with the same placeholders, and no long dashes', () => {
    assert.deepEqual(Object.keys(financeLabAr).sort(), Object.keys(financeLabEn).sort());
    const holes = (m: unknown) => new Set(JSON.stringify(m).match(/\{[a-z]+\}/gi) ?? []);
    for (const [key, en] of Object.entries(financeLabEn)) {
      const ar = (financeLabAr as Record<string, unknown>)[key];
      for (const hole of holes(ar)) assert.ok(holes(en).has(hole), `${key} ${hole}`);
      assert.doesNotMatch(JSON.stringify(en) + JSON.stringify(ar), /[–—]/, key);
    }
    assert.equal(t('flab.kpi.strategiesValue', { running: 2, paused: 1 }), '2 running, 1 paused');
    setLocale('ar');
    assert.equal(t('flab.liveOff'), 'مقفل');
  });

  test('says simulated, never promises a return, keeps live locked', () => {
    assert.match(financeLabEn['flab.banner'], /SIMULATED/);
    assert.match(financeLabEn['flab.pastNote'], /not a guarantee/);
    assert.doesNotMatch(JSON.stringify(financeLabEn), /guaranteed|profitable|risk[- ]free/i);
  });
});
