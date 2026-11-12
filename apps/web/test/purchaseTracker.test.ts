// Unit tests for the purchase tracker helpers (src/lib/purchases.ts, docs/purchases.md#web): date ranges, masked tracking,
// carrier pages only for known carriers, the task actions, the drawer's steps and refund match, and the row title.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { PurchaseDetail, PurchaseReturn } from '@anum/contracts';
import {
  NO_FILTERS, carrierUrl, chipTone, trackingLinks, currentReturn, dueWhen, filtersActive, maskTracking, rangeStart, refundMatch, rowTitle, taskAction, trackerSteps,
} from '../src/lib/purchases.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { purchasesAr } from '../src/lib/messages/purchases.ar.ts';
import { purchasesEn } from '../src/lib/messages/purchases.en.ts';

const ret = (extra: Partial<PurchaseReturn> = {}): PurchaseReturn => ({
  id: 'r1', owner_user_id: 'u', purchase_id: 'p1', status: 'requested', requested_on: '2026-11-04', reason: 'wrong_item', reason_note: null, items: [], amount: '189.00',
  currency: 'SAR', deadline_on: null, pickup_on: null, courier: null, tracking_number: null, proof_document_id: null, confirmation_ref: null, refund_ref: null,
  returned_on: null, expected_refund_on: null, refunded_on: null, dispute_note: null, refunds: [], refunded: '0.00', refunded_all: [], version: 1, can_edit: true,
  can_refund: true, allowed_moves: [], ...extra,
});
const detail = (extra: Partial<PurchaseDetail> = {}) => ({ status: 'ordered', ordered_on: '2026-11-03', delivered_on: null, shipments: [], returns: [], ...extra }) as unknown as PurchaseDetail;

describe('purchase tracker', () => {
  test('date ranges start on the right day and end today', () => {
    const now = new Date(2026, 10, 15);
    assert.equal(rangeStart('', now), null);
    assert.equal(rangeStart('month', now), '2026-11-01');
    assert.equal(rangeStart('year', now), '2026-01-01');
    assert.equal(rangeStart('30', now), '2026-10-17');
    assert.equal(filtersActive(NO_FILTERS), false);
    assert.equal(filtersActive({ ...NO_FILTERS, source: 'iphone' }), true);
  });

  test('tracking numbers show their last four only, and the carrier link exists only for known carriers', () => {
    assert.equal(maskTracking('DHL-S3K7Q9'), '••••••K7Q9');
    assert.equal(maskTracking('AB1'), 'AB1');
    assert.equal(maskTracking(null), '-');
    assert.match(carrierUrl('DHL', 'S3K7Q9') ?? '', /^https:\/\/www\.dhl\.com\/.*tracking-id=S3K7Q9$/);
    assert.match(carrierUrl('aramex', '31234567890') ?? '', /^https:\/\/www\.aramex\.com\//);
    assert.equal(carrierUrl('Example Courier', 'TRK12345678'), null);
    assert.equal(carrierUrl('DHL', null), null);
    assert.equal(carrierUrl('DHL', 'x y<script>'), null, 'only plain tracking numbers go into a link');
  });

  test('the server detection wins, several candidates give one link each, and the local list is the fallback', () => {
    const base = { carrier: null, tracking_number: '1234567891' };
    const candidates = [
      { carrier: 'dhl', carrier_name: 'DHL Express', url: 'https://www.dhl.com/x?tracking-id=1234567891', confidence: 'likely' as const },
      { carrier: 'aramex', carrier_name: 'Aramex', url: 'https://www.aramex.com/x?ShipmentNumber=1234567891', confidence: 'possible' as const },
    ];
    const tracking = { number: '1234567891', carrier: null, carrier_name: null, url: null, detected_from: null, candidates, status_provider: 'off' };
    assert.deepEqual(trackingLinks({ ...base, tracking }).map((l) => l.name), ['DHL Express', 'Aramex']);
    const named = { ...tracking, carrier: 'ups', carrier_name: 'UPS', url: 'https://www.ups.com/track?tracknum=1Z', detected_from: 'number' as const };
    assert.deepEqual(trackingLinks({ ...base, tracking: named }), [{ url: 'https://www.ups.com/track?tracknum=1Z', name: 'UPS' }]);
    assert.equal(trackingLinks({ carrier: 'DHL', tracking_number: 'S3K7Q9' })[0]?.name, 'DHL');
    assert.deepEqual(trackingLinks({ carrier: 'Example Courier', tracking_number: 'TRK12345678' }), []);
  });

  test('each task kind offers one real action', () => {
    assert.equal(taskAction('delivery_late'), 'shipment');
    assert.equal(taskAction('proof_needed'), 'proof');
    assert.equal(taskAction('return_window'), 'return');
    assert.equal(taskAction('refund_overdue'), 'refund');
    assert.equal(taskAction('missing_confirmation'), 'open');
  });

  test('steps follow the status and the return', () => {
    setLocale('en');
    assert.deepEqual(trackerSteps(detail()).map((s) => s.state), ['done', 'current', 'todo']);
    assert.deepEqual(trackerSteps(detail({ status: 'shipped' })).map((s) => s.state), ['done', 'done', 'current']);
    const returned = trackerSteps(detail({ status: 'return_requested', delivered_on: '2026-11-04', returns: [ret()] }));
    assert.deepEqual(returned.map((s) => [s.id, s.state]), [['ordered', 'done'], ['shipped', 'done'], ['delivered', 'done'], ['refund', 'current']]);
    assert.equal(returned[3].label, 'Refund: Requested');
    assert.deepEqual(trackerSteps(detail({ status: 'cancelled' })).map((s) => s.state), ['done', 'todo', 'todo']);
  });

  test('the drawer shows the open return and how its refund matches', () => {
    const closed = ret({ id: 'old', status: 'refunded', requested_on: '2026-10-01' });
    const open = ret({ id: 'new', requested_on: '2026-11-04' });
    assert.equal(currentReturn({ returns: [closed, open] } as PurchaseDetail)?.id, 'new');
    assert.equal(currentReturn({ returns: [] } as unknown as PurchaseDetail), null);
    assert.equal(refundMatch(ret()), 'waiting');
    assert.equal(refundMatch(ret({ status: 'rejected' })), 'none');
    const linked = { link_id: 'l', canonical_id: 'c', status: 'active' as const, status_reason: null, amount: '189.00', currency: 'SAR', paid_by: 'u', recorded_as_cash: false, linked_at: '', original_canonical_id: null };
    assert.equal(refundMatch(ret({ refunds: [linked] })), 'matched');
    assert.equal(refundMatch(ret({ refunds: [{ ...linked, status: 'needs_review' }] })), 'review');
  });

  test('chips, row titles and due words in English and Arabic', () => {
    setLocale('en');
    assert.equal(chipTone('delivered'), 'ok');
    assert.equal(chipTone('disputed'), 'bad');
    assert.equal(chipTone('cancelled'), 'muted');
    assert.equal(rowTitle({ merchant: 'Noon', first_item: 'Scanner', item_count: 3 }), 'Scanner and 2 more');
    assert.equal(rowTitle({ merchant: 'Noon', first_item: null }), 'Noon');
    const format = (d: string) => d;
    assert.equal(dueWhen({ days_left: 0, due_on: '2026-11-05' }, format), 'Due: Today');
    assert.equal(dueWhen({ days_left: 2, due_on: '2026-11-07' }, format), 'Due: 2026-11-07');
    setLocale('ar');
    assert.equal(dueWhen({ days_left: 0, due_on: '2026-11-05' }, format), 'الموعد: اليوم');
    setLocale('en');
  });

  test('every new English message has an Arabic one, without long dashes', () => {
    for (const key of Object.keys(purchasesEn)) assert.ok(key in purchasesAr, key);
    for (const text of [...Object.values(purchasesEn), ...Object.values(purchasesAr)]) assert.doesNotMatch(JSON.stringify(text), /[–—]/);
  });
});
