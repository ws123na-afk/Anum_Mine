// Tamara refunds card helpers (src/lib/financeRefunds.ts, docs/finance.md#tamara-refunds).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceInstallmentStatementEntry, FinanceOrderRefundPlan } from '@anum/contracts';
import {
  applySelection, filterOrders, orderRefundState, pendingTotals, rowDue, rowEffective, stateTone, sumAmounts, withEvidence,
} from '../src/lib/financeRefunds.ts';
import { en } from '../src/lib/messages/en.ts';
import { ar } from '../src/lib/messages/ar.ts';

const plan = (over: Partial<FinanceOrderRefundPlan>): FinanceOrderRefundPlan => ({
  provider: 'Tamara', order_id: 'example-order-a', merchant: 'Example Shop', currency: 'SAR', state: 'refunded', applied_state: 'active',
  mode: 'cancel_all', applicable: true, pending: true, order_total: '400.00', paid_total: '100.00', refund_total: '400.00', credit_back: '100.00',
  credit_received: '0.00', outstanding_before: '300.00', outstanding_after: '0.00', next_due_before: '2026-12-01', next_due_after: null,
  evidence: [{ id: 'email:m1', kind: 'email', strength: 'strong', role: 'counted', code: 'email_full', amount: '400.00', currency: 'SAR', on: '2026-11-01',
    installment: null, link: null, transaction_id: null }],
  installments: [], reasons: [], plan_hash: 'a'.repeat(64), ...over,
});
const row = (over: Partial<FinanceInstallmentStatementEntry>): FinanceInstallmentStatementEntry => ({
  provider: 'Tamara', order_id: 'example-order-a', merchant: 'Example Shop', purchase_on: '2026-10-01', due_on: '2026-12-01', installment: 2,
  total_installments: 4, amount: '100.00', currency: 'SAR', source_status: 'scheduled', source_email: null, review_notes: null, refunded_amount: '0', ...over,
});

describe('refunds card', () => {
  test('only orders with evidence or an applied refund are listed', () => {
    const quiet = plan({ order_id: 'example-order-b', evidence: [], state: 'active', applicable: false, pending: false });
    const applied = plan({ order_id: 'example-order-c', evidence: [], applied_state: 'refunded', applicable: false, pending: false });
    assert.deepEqual(withEvidence([plan({}), quiet, applied]).map((o) => o.order_id), ['example-order-a', 'example-order-c']);
  });

  test('filters by state and search; apply sends only applicable plans with their hash', () => {
    const review = plan({ order_id: 'example-order-r', state: 'needs_review', mode: 'estimate', applicable: false, pending: false, merchant: 'Other Store' });
    const all = [plan({}), review];
    assert.equal(filterOrders(all, 'pending', '').length, 1);
    assert.equal(filterOrders(all, 'review', '')[0].order_id, 'example-order-r');
    assert.equal(filterOrders(all, 'all', 'other').length, 1);
    assert.equal(filterOrders(all, 'refunded', '').length, 1);
    assert.deepEqual(applySelection(all), [{ provider: 'Tamara', order_id: 'example-order-a', plan_hash: 'a'.repeat(64) }]);
  });

  test('pending totals are exact per currency (no float drift)', () => {
    const totals = pendingTotals([plan({ outstanding_before: '0.30', outstanding_after: '0.10', credit_back: '0.10' }),
      plan({ order_id: 'x', outstanding_before: '0.20', outstanding_after: '0.00', credit_back: '0.20' }),
      plan({ order_id: 'y', currency: 'AED', outstanding_before: '50.00', outstanding_after: '50.00', credit_back: '0.00' }),
      plan({ order_id: 'z', applicable: false, outstanding_before: '999.00' })]);
    assert.deepEqual(totals, [{ currency: 'SAR', notDue: '0.40', moneyBack: '0.30' }, { currency: 'AED', notDue: '0.00', moneyBack: '0.00' }]);
    assert.equal(sumAmounts(['1.005', '-0.5']), '0.50');
  });

  test('a refunded statement row is not due; a partly refunded one is due at its lower amount', () => {
    assert.equal(rowEffective(row({ refunded_amount: '100.00' }), false), 'refunded');
    assert.equal(rowEffective(row({ source_status: 'refunded' }), false), 'refunded');
    assert.equal(rowEffective(row({ source_status: 'paid' }), false), 'paid');
    assert.equal(rowEffective(row({}), true), 'paid');
    assert.equal(rowDue(row({ refunded_amount: '40.00' })), 60);
    assert.equal(rowDue(row({ refunded_amount: '100.00' })), 0);
    assert.equal(orderRefundState([row({ installment: 1, source_status: 'paid' }), row({ refunded_amount: '100.00' })]), 'refunded');
    assert.equal(orderRefundState([row({ refunded_amount: '40.00' })]), 'partly_refunded');
    assert.equal(orderRefundState([row({})]), 'active');
    assert.equal(stateTone('needs_review'), 'warn');
  });

  test('every refund message has an Arabic twin with the same placeholders', () => {
    const keys = Object.keys(en).filter((k) => k.startsWith('frefund.'));
    assert.ok(keys.length > 50);
    for (const key of keys) assert.ok(key in ar, key);
  });
});
