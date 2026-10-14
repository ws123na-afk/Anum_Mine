// Finance stages 6 to 8 view helpers (src/lib/financeTrading.ts, docs/finance.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { FINANCE_TABS, hashForTab, tabFromHash } from '../src/lib/finance.ts';
import {
  assumptionsText, bpsLabel, checkBps, checkOrder, checkPrice, checkQuantity, checkSymbol, codeText, draftFromLimits, formatDecimal, intentLine,
  intentTone, limitsFromDraft, newIdempotencyKey, orderTone,
} from '../src/lib/financeTrading.ts';
import { setLocale, t } from '../src/lib/i18n.ts';
import { financeTradingAr } from '../src/lib/messages/financeTrading.ar.ts';
import { financeTradingEn } from '../src/lib/messages/financeTrading.en.ts';
import { viewFromHash } from '../src/lib/shell.ts';

afterEach(() => setLocale('en'));

describe('paper trading tabs', () => {
  test('the paper trading and execution tabs are deep links in the browser history', () => {
    for (const tab of ['trading', 'execution'] as const) {
      assert.ok(FINANCE_TABS.includes(tab));
      assert.equal(tabFromHash(hashForTab(tab)), tab);
      assert.equal(viewFromHash(hashForTab(tab)), 'finance');
    }
  });
});

describe('figures', () => {
  test('decimal strings keep their exact digits, trailing zeros dropped', () => {
    assert.equal(formatDecimal('187.2500000000'), '187.25');
    assert.equal(formatDecimal('10.00000000', 8), '10');
    assert.equal(formatDecimal('12345.5'), '12,345.5');
    assert.equal(formatDecimal('-3.10'), '-3.1');
    assert.equal(formatDecimal(null), '-');
    setLocale('ar');
    assert.equal(formatDecimal('12345.5'), '12,345.5');
  });

  test('costs are shown as percentages with every simulated figure', () => {
    assert.equal(bpsLabel('10'), '0.10%');
    assert.equal(bpsLabel('5.00'), '0.05%');
    assert.equal(assumptionsText({ fee_bps: '10.00', fee_min: '1.00', slippage_bps: '5.00' }),
      'Assumptions: fee 0.10% of the order value (at least 1), slippage 0.05% against you.');
  });
});

describe('order form checks', () => {
  test('symbols, quantities, prices and basis points are checked like the API checks them', () => {
    assert.deepEqual(checkSymbol(' exmpl ', false), { ok: true, value: 'EXMPL' });
    assert.equal(checkSymbol('BRK.B', false).ok, true);
    assert.equal(checkSymbol('no spaces', false).ok, false);
    assert.equal(checkSymbol('USDX', true).ok, false);
    assert.deepEqual(checkQuantity('٢.٥'), { ok: true, value: '2.5' });
    assert.equal(checkQuantity('0').ok, false);
    assert.equal(checkQuantity('1.123456789').ok, false);
    assert.equal(checkPrice('187.25').ok, true);
    assert.equal(checkPrice('-1').ok, false);
    assert.equal(checkBps('10.5').ok, true);
    assert.equal(checkBps('1001').ok, false);
  });

  test('a whole order is checked before it is sent', () => {
    const base = { kind: 'security' as const, symbol: 'exmpl', currency: 'usd', side: 'buy' as const, type: 'market' as const, quantity: '10', limit: '' };
    assert.deepEqual(checkOrder(base), { ok: true, value: { instrument_kind: 'security', symbol: 'EXMPL', currency: 'USD', side: 'buy', order_type: 'market', quantity: '10', limit_price: null } });
    assert.equal(checkOrder({ ...base, type: 'limit' }).ok, false);
    assert.equal(checkOrder({ ...base, type: 'limit', limit: '99.5' }).ok, true);
    assert.equal(checkOrder({ ...base, kind: 'fx', symbol: 'usd' }).ok, false);
    assert.equal(checkOrder({ ...base, currency: 'US' }).ok, false);
  });

  test('every form submission carries its own idempotency key', () => {
    const one = newIdempotencyKey(() => 0.25), two = newIdempotencyKey(() => 0.5);
    assert.match(one, /^web-[a-z0-9]+-[a-z0-9]+$/);
    assert.notEqual(one, two);
    assert.ok(one.length >= 8 && one.length <= 80);
  });
});

describe('requests', () => {
  test('a request reads as what it asks for, in both languages', () => {
    const intent = { kind: 'trade' as const, side: 'buy' as const, quantity: '10.00000000', symbol: 'EXMPL', order_type: 'limit' as const, limit_price: '99.5000000000', currency: 'USD' };
    assert.equal(intentLine(intent), 'Buy 10 EXMPL at 99.5 USD or better');
    assert.equal(intentLine({ ...intent, side: 'sell', order_type: 'market' }), 'Sell 10 EXMPL');
    setLocale('ar');
    assert.equal(intentLine({ ...intent, order_type: 'market' }).replace(/[\u2066-\u2069]/g, ''), 'شراء 10 EXMPL');
  });

  test('statuses get a tone and codes a sentence, with the server text as the fallback', () => {
    assert.equal(intentTone('executed'), 'success');
    assert.equal(intentTone('pending_approval'), 'neutral');
    assert.equal(intentTone('denied'), 'warning');
    assert.equal(orderTone('filled'), 'success');
    assert.equal(orderTone('refused'), 'warning');
    assert.match(codeText('no_price', 'server'), /No recent price/);
    assert.equal(codeText('something_new', 'Server text.'), 'Server text.');
    assert.equal(codeText(null, null), '');
  });
});

describe('live limits', () => {
  test('limits are checked per currency and listed once each', () => {
    const draft = draftFromLimits({ max_per_action: [{ currency: 'AED', amount: '100.00' }], max_per_day: [], max_per_month: [], instruments: ['EXMPL'], accounts: [], currencies: ['AED'], cooling_off_hours: 24 });
    assert.equal(draft.currencies, 'AED');
    const checked = limitsFromDraft({ ...draft, perDay: [{ currency: 'aed', amount: '200' }], instruments: 'exmpl, other.b', currencies: 'aed، usd' });
    assert.ok(checked.ok);
    if (checked.ok) {
      assert.deepEqual(checked.value.max_per_day, [{ currency: 'AED', amount: '200' }]);
      assert.deepEqual(checked.value.instruments, ['EXMPL', 'OTHER.B']);
      assert.deepEqual(checked.value.currencies, ['AED', 'USD']);
    }
    assert.equal(limitsFromDraft({ ...draft, perDay: [{ currency: 'AED', amount: '1' }, { currency: 'AED', amount: '2' }] }).ok, false);
    assert.equal(limitsFromDraft({ ...draft, coolingHours: '0' }).ok, false);
    assert.equal(limitsFromDraft({ ...draft, currencies: 'DIRHAM' }).ok, false);
  });
});

describe('copy', () => {
  test('both catalogs have every key with the same placeholders', () => {
    const placeholders = (value: unknown) => [...new Set(JSON.stringify(value).match(/\{[a-z]+\}/gi) ?? [])].sort();
    for (const [key, value] of Object.entries(financeTradingEn)) {
      assert.ok(key in financeTradingAr, key);
      assert.deepEqual(placeholders(financeTradingAr[key as keyof typeof financeTradingAr]), placeholders(value), key);
    }
  });

  test('nothing promises a profit or calls a simulation real', () => {
    const text = JSON.stringify([financeTradingEn, financeTradingAr]).toLowerCase();
    for (const word of ['guarantee', 'risk-free', 'risk free', 'sure profit', 'certain profit', 'مضمون', 'بلا مخاطر']) assert.ok(!text.includes(word), word);
    assert.match(t('finance.trading.simulatedText'), /say nothing certain/);
    assert.match(t('finance.live.off'), /not enabled/);
  });
});
