// Unit tests for the purchase helpers (src/lib/purchases.ts, docs/purchases.md#web).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { PurchaseDueItem, PurchaseMoneyLink } from '@anum/contracts';
import {
  decimal, dueText, filterLabel, hashForTab, linesTotal, linkState, linkText, moneyList, newKey, purchaseName, refundLeft, returnStatusLabel, shipmentLabel,
  openFromHash, purchaseHref, receiptChoices, statusLabel, statusTone, tabFromHash,
} from '../src/lib/purchases.ts';
import { guideFor } from '../src/lib/iphoneIntegrations.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { purchasesAr } from '../src/lib/messages/purchases.ar.ts';
import { purchasesEn } from '../src/lib/messages/purchases.en.ts';
import { iphoneEn } from '../src/lib/messages/iphone.en.ts';
import { viewFromHash } from '../src/lib/shell.ts';
import { PURCHASE_STATUSES } from '../../../packages/contracts/src/purchases.ts';

const link = (extra: Partial<PurchaseMoneyLink> = {}): PurchaseMoneyLink => ({
  link_id: 'l1', canonical_id: 'c1', status: 'active', status_reason: null, amount: '450.00', currency: 'SAR', paid_by: 'u', recorded_as_cash: false,
  linked_at: '2026-11-01T10:00:00Z', original_canonical_id: null, ...extra,
});
const due = (extra: Partial<PurchaseDueItem>): PurchaseDueItem => ({
  purchase_id: 'p', merchant: 'Amazon', order_ref: 'AMZ-1', kind: 'delivery_late', source_id: 'p', due_on: '2026-11-02', days_left: -2, ...extra,
});

describe('purchase helpers', () => {
  test('tabs are deep-linkable and the shell opens Purchases for them', () => {
    assert.equal(tabFromHash('#purchases'), 'overview');
    assert.equal(tabFromHash('#purchases-list'), 'list');
    assert.equal(tabFromHash('#purchases-returns'), 'returns');
    assert.equal(tabFromHash('#purchases-settings'), 'settings');
    assert.equal(tabFromHash('#purchases-nope'), 'overview');
    assert.equal(hashForTab('overview'), '#purchases');
    assert.equal(hashForTab('returns'), '#purchases-returns');
    assert.equal(viewFromHash('#purchases-returns'), 'purchases');
    assert.equal(viewFromHash('#purchases'), 'purchases');
  });

  test('money is listed per currency, never added across currencies', () => {
    setLocale('en');
    assert.equal(moneyList([]), '-');
    const text = moneyList([{ currency: 'AED', amount: '90.00' }, { currency: 'SAR', amount: '100.00' }]);
    assert.match(text, /AED\s?90\.00 · SAR\s?100\.00/);
    assert.ok(!text.includes('190'));
  });

  test('a payment or refund link says what it is: linked, changed, undone or none', () => {
    setLocale('en');
    assert.equal(linkState(null), 'none');
    assert.equal(linkText(null), 'No payment linked');
    assert.match(linkText(link()), /SAR\s?450\.00/);
    assert.match(linkText(link({ status: 'needs_review' })), /check in Finance/);
    assert.equal(linkText(link({ status: 'void', status_reason: 'transaction_undone' })), 'Payment undone in Finance');
    assert.equal(linkText(link({ status: 'void', status_reason: 'removed_by_owner' })), 'Link removed');
  });

  test('what is still owed on a return, due dates and names', () => {
    setLocale('en');
    assert.equal(refundLeft({ amount: '150.00', refunded: '100.00', currency: 'SAR' }), '50.00');
    assert.equal(refundLeft({ amount: '150.00', refunded: '150.00', currency: 'SAR' }), '0.00');
    assert.equal(refundLeft({ amount: null, refunded: '0.00', currency: 'SAR' }), null);
    assert.equal(refundLeft({ amount: '120.00', refunded: '0.00', currency: 'USD', refunded_all: [{ currency: 'SAR', amount: '150.00' }] }), null);
    assert.equal(dueText(due({ days_left: 0 })), 'today');
    assert.equal(dueText(due({ days_left: 1 })), 'in 1 day');
    assert.equal(dueText(due({ days_left: -2 })), '2 days ago');
    assert.equal(purchaseName({ merchant: 'noon', order_ref: null }), 'noon');
    assert.equal(purchaseName({ merchant: 'Amazon', order_ref: 'AMZ-1' }), 'Amazon AMZ-1');
    assert.notEqual(newKey(), newKey());
  });

  test('every status has a label and a tone, in English and Arabic', () => {
    for (const locale of ['en', 'ar'] as const) {
      setLocale(locale);
      for (const status of PURCHASE_STATUSES) assert.ok(!statusLabel(status).includes('_'), `${locale} ${status}`);
      assert.ok(!returnStatusLabel('pickup_scheduled').includes('_'));
      assert.ok(!shipmentLabel('returned_to_sender').includes('_'));
      assert.notEqual(filterLabel('awaiting'), 'awaiting');
    }
    setLocale('en');
    assert.equal(statusTone('disputed'), 'warn');
    assert.equal(statusTone('refunded'), 'done');
    assert.equal(statusTone('shipped'), 'move');
  });

  test('numbers typed with Arabic digits or commas are read, line totals are a detail', () => {
    assert.equal(decimal('٤٥٫٥'), '45.5');
    assert.equal(decimal('1,250.75'), '1250.75');
    assert.equal(decimal('12.345'), null);
    assert.equal(decimal('-3'), null);
    assert.equal(linesTotal([{ quantity: '2', unit_price: '75' }, { quantity: '1', unit_price: '300' }]), '450.00');
    assert.equal(linesTotal([{ quantity: '1', unit_price: '' }]), null);
  });

  test('the iPhone page has the share guide, which never reads every message', () => {
    setLocale('en');
    const guide = guideFor('purchases.share');
    assert.ok(guide);
    const steps = guide.steps('https://api.example.test/api/v1/purchases/device/share');
    assert.equal(steps.length, 5);
    assert.ok(steps[1].includes('https://api.example.test/api/v1/purchases/device/share'));
    assert.match(steps[3], /Never set it to every message/);
    assert.ok(guide.body.some((line) => line.startsWith('text = ')));
    assert.match(iphoneEn['iph.int.purchases.share.purpose'], /Never finance/);
  });

  test('English and Arabic catalogs have the same keys and the same placeholders, and no long dashes', () => {
    assert.deepEqual(Object.keys(purchasesAr).sort(), Object.keys(purchasesEn).sort());
    for (const [key, value] of Object.entries(purchasesEn)) {
      const names = (text: unknown) => [...JSON.stringify(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).filter((n) => n !== 'count').sort();
      assert.deepEqual([...new Set(names((purchasesAr as Record<string, unknown>)[key]))], [...new Set(names(value))], key);
    }
    for (const text of [...Object.values(purchasesEn), ...Object.values(purchasesAr)]) assert.ok(!/[–—]/.test(JSON.stringify(text)));
    assert.match(purchasesEn['pur.noPosting'], /never income/);
  });
});

describe('save as purchase from the Email Center', () => {
  test('a link opens one purchase and keeps the tab', () => {
    assert.equal(purchaseHref('pur_1'), '#purchases?open=pur_1');
    assert.equal(openFromHash(purchaseHref('pur_1')), 'pur_1');
    assert.equal(tabFromHash(purchaseHref('pur_1')), 'overview');
    assert.equal(openFromHash('#purchases'), null);
    assert.equal(openFromHash('#purchases?open=<script>'), null);
  });
  test('receipt choices: downloadable files only, PDFs then images first', () => {
    const files = [
      { index: 0, filename: 'logo.gif', size: 10, downloadable: true },
      { index: 1, filename: 'photo.JPG', size: 10, downloadable: true },
      { index: 2, filename: 'invoice.pdf', size: 10, downloadable: true },
      { index: 3, filename: 'huge.pdf', size: 10, downloadable: false },
    ];
    assert.deepEqual(receiptChoices(files).map((item) => item.index), [2, 1, 0]);
    assert.deepEqual(receiptChoices([]), []);
  });
  test('every Save as purchase message is in English and Arabic', () => {
    const keys = Object.keys(purchasesEn).filter((key) => key.startsWith('mailp.'));
    assert.ok(keys.length >= 10);
    for (const key of keys) assert.ok((purchasesAr as Record<string, string>)[key], key);
  });
});
