// iPhone SMS and ledger view helpers (src/lib/financeSms.ts, docs/finance-sms.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceSmsMessage } from '@anum/contracts';
import { FINANCE_TABS, hashForTab, tabFromHash } from '../src/lib/finance.ts';
import { correctionBody, correctionDefaults, ingestUrl, kindLabel, ledgerAccountLabel, reasonLabel, shortcutSteps } from '../src/lib/financeSms.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { financeSmsAr } from '../src/lib/messages/financeSms.ar.ts';
import { financeSmsEn } from '../src/lib/messages/financeSms.en.ts';

afterEach(() => setLocale('en'));

const message = (parsed: Record<string, string>): FinanceSmsMessage => ({
  id: 'm1', device_id: 'd1', source: 'device', received_at: '2026-11-03T10:00:00Z', sender: 'testbank', bank_key: null, body_masked: 'SYNTHETIC',
  body_kept_until: null, status: 'review', reason: 'format_unverified', parsed, rule: 'generic', entry_id: null, duplicate_of: null,
  observed_balance: null, observed_currency: null, attempts: 1,
});

describe('iPhone SMS and ledger tabs', () => {
  test('both tabs are deep links', () => {
    for (const tab of ['sms', 'ledger'] as const) {
      assert.ok(FINANCE_TABS.includes(tab));
      assert.equal(tabFromHash(hashForTab(tab)), tab);
    }
  });

  test('the endpoint is shown only when the server gives it', () => {
    assert.equal(ingestUrl('https://api.example.test/', '/api/v1/finance/sms/ingest'), 'https://api.example.test/api/v1/finance/sms/ingest');
    assert.equal(ingestUrl('https://api.example.test', null), null);
  });

  test('reasons, kinds and ledger accounts read as plain words, unknown codes as they are', () => {
    assert.equal(reasonLabel('format_unverified'), 'Format not confirmed yet');
    assert.equal(reasonLabel('something_new'), 'something_new');
    assert.equal(reasonLabel(null), '-');
    assert.equal(kindLabel('card_repayment'), 'Card repayment');
    assert.equal(ledgerAccountLabel('liability:acc1', { acc1: 'Card' }), 'Card');
    assert.equal(ledgerAccountLabel('expense:groceries', {}), 'Expense: groceries');
    assert.equal(ledgerAccountLabel('suspense:own_transfer', {}), 'In transit');
    setLocale('ar');
    assert.equal(kindLabel('salary'), 'راتب');
  });

  test('the correction form starts from what ANUM read and sends only filled fields', () => {
    const form = correctionDefaults(message({ kind: 'card_purchase', amount: '45.50', currency: 'sar', merchant: 'SHOP' }));
    assert.equal(form.kind, 'card_purchase');
    assert.deepEqual(correctionBody({ ...form, merchant: '  ', confirm_format: true }), { kind: 'card_purchase', amount: '45.50', currency: 'SAR', confirm_format: true });
    assert.equal(correctionDefaults(message({ kind: 'unknown' })).kind, undefined);
  });

  test('the Shortcut steps include the address', () => {
    const steps = shortcutSteps('https://api.example.test/api/v1/finance/sms/ingest');
    assert.equal(steps.length, 6);
    assert.ok(steps[3].includes('https://api.example.test/api/v1/finance/sms/ingest'));
  });

  test('every English key has an Arabic twin with the same placeholders', () => {
    assert.deepEqual(Object.keys(financeSmsAr).sort(), Object.keys(financeSmsEn).sort());
    for (const [key, value] of Object.entries(financeSmsEn)) {
      assert.deepEqual(placeholdersOf(financeSmsAr[key as keyof typeof financeSmsEn]), placeholdersOf(value), key);
    }
  });
});
