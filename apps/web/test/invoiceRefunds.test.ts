// Unit tests for refunds of a card payment asked for from ANUM and provider refunds ANUM could not match
// (docs/business.md#refunds-from-anum, docs/business.md#refunds-anum-could-not-match).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { BusinessInvoice, InvoicePayment, RefundReview } from '@anum/contracts';
import {
  emptyRefund, newRefundKey, providerName, refundBody, refundableOf, requestStatusLabel, requestTone, requestsOf, reviewAmount,
} from '../src/lib/invoiceRefunds.ts';
import { setLocale } from '../src/lib/i18n.ts';

const payment = (extra: Partial<InvoicePayment> = {}): InvoicePayment => ({
  id: 'binpay_1', tenant_id: 't', workspace_id: 'w', created_at: '2026-11-01T10:00:00Z', updated_at: '2026-11-01T10:00:00Z', invoice_id: 'binv_1',
  amount: 1050, paid_on: '2026-11-01', method: 'card', reference: '', note: '', recorded_by: 'system:stripe', provider: 'stripe', provider_payment_id: 'pi_1', ...extra,
});
const invoice = (extra: Partial<BusinessInvoice> = {}): BusinessInvoice => ({
  can: { refund: true }, refundable: { binpay_1: 750 }, refund_requests: [
    { id: 'binrfq_1', payment_id: 'binpay_1', status: 'waiting_approval' }, { id: 'binrfq_2', payment_id: 'binpay_2', status: 'succeeded' }],
  ...extra,
} as unknown as BusinessInvoice);

describe('refunds from ANUM', () => {
  test('the form starts at what is left and refuses above it, zero, or no reason', () => {
    setLocale('en');
    assert.deepEqual(emptyRefund(750), { amount: '750.00', reason: '' });
    assert.deepEqual(emptyRefund(0), { amount: '', reason: '' });
    assert.deepEqual(refundBody({ amount: '300', reason: '  Returned   item ' }, 750, 'AED'), { ok: true, value: { amount: 300, reason: 'Returned item' } });
    for (const amount of ['0', '-5', 'abc', '750.01']) {
      const checked = refundBody({ amount, reason: 'Returned item' }, 750, 'AED');
      assert.equal(checked.ok, false, amount);
      if (!checked.ok) assert.match(checked.error, /at most AED/);
    }
    const short = refundBody({ amount: '10', reason: 'no' }, 750, 'AED');
    assert.equal(short.ok, false);
    if (!short.ok) assert.match(short.error, /at least 3 characters/);
    setLocale('ar');
    const arabic = refundBody({ amount: '900', reason: 'سبب' }, 750, 'SAR');
    assert.equal(arabic.ok, false);
    if (!arabic.ok) assert.match(arabic.error, /لا يزيد على/);
  });

  test('one key per form, safe for the header', () => {
    assert.equal(newRefundKey(() => 'a b/c'), 'refund-abc');
    assert.notEqual(newRefundKey(), newRefundKey());
    assert.ok(newRefundKey().length <= 80);
  });

  test('only a card payment with something left, on a server that offers refunds, can be refunded', () => {
    assert.equal(refundableOf(invoice(), payment()), 750);
    assert.equal(refundableOf(invoice(), payment({ id: 'binpay_2' })), 0);
    assert.equal(refundableOf(invoice(), payment({ provider: null })), 0);
    assert.equal(refundableOf(invoice({ can: { refund: false } } as Partial<BusinessInvoice>), payment()), 0);
    assert.equal(refundableOf(invoice({ refundable: undefined }), payment()), 0);
    assert.deepEqual(requestsOf(invoice(), 'binpay_1').map((item) => item.id), ['binrfq_1']);
    assert.deepEqual(requestsOf(invoice({ refund_requests: undefined }), 'binpay_1'), []);
  });

  test('statuses and tones in English and Arabic', () => {
    setLocale('en');
    assert.equal(requestStatusLabel('waiting_approval'), 'Waiting for approval');
    assert.equal(requestStatusLabel('pending_provider'), 'The provider is finishing it');
    assert.equal(requestTone('succeeded'), 'success');
    assert.equal(requestTone('failed'), 'dangerTone');
    assert.equal(requestTone('cancelled'), 'neutral');
    setLocale('ar');
    assert.equal(requestStatusLabel('succeeded'), 'استُرد');
    assert.equal(providerName('stripe'), 'Stripe');
    assert.equal(providerName('moyasar'), 'Moyasar');
  });

  test('a review shows its own currency, known or not', () => {
    setLocale('en');
    const review = { amount: 25, currency: 'AED' } as RefundReview;
    assert.match(reviewAmount(review), /25\.00/);
    assert.equal(reviewAmount({ amount: 9.5, currency: 'EUR' } as RefundReview), 'EUR 9.50');
  });
});
