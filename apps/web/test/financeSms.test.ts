// iPhone SMS and ledger view helpers (src/lib/financeSms.ts, docs/finance-sms.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { FinanceSmsFix, FinanceSmsLearningNotice, FinanceSmsMessage } from '@anum/contracts';
import { FINANCE_TABS, hashForTab, tabFromHash } from '../src/lib/finance.ts';
import {
  ONE_TAP_FIXES, REMEMBERED_KINDS, correctionBody, correctionDefaults, fixText, ingestUrl, kindLabel, learnedValueLabel, ledgerAccountLabel, noticeText, reasonLabel,
  shortcutSteps, walletSteps, walletUrl,
} from '../src/lib/financeSms.ts';
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

  test('the Apple Wallet address and steps', () => {
    assert.equal(walletUrl('https://api.example.test/api/v1/finance/sms/ingest'), 'https://api.example.test/api/v1/finance/wallet/ingest');
    assert.equal(walletUrl(null), null);
    assert.ok(walletSteps('https://x.test/w')[1].includes('https://x.test/w'));
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

describe('learning from reviews (docs/finance-sms.md#learning)', () => {
  const fix = (over: Partial<FinanceSmsFix>): FinanceSmsFix => ({ action: 'record', sender: null, last4: null, account_id: null, account_name: null, party: null, kind: null, ref: null, ...over });

  test('every fix says the one step in plain words, in both languages', () => {
    assert.equal(fixText(fix({ action: 'trust_sender', sender: 'testbank' })), 'Always record messages from testbank');
    assert.equal(fixText(fix({ action: 'map_card', last4: '1234', account_name: 'Current' })), 'Map card ending 1234 to Current');
    assert.equal(fixText(fix({ action: 'map_card', last4: '1234' })), 'Map card ending 1234 to an account');
    assert.equal(fixText(fix({ action: 'remember_kind', party: 'ACME', kind: 'salary' })), 'Treat money from ACME as Salary');
    assert.equal(fixText(fix({ action: 'undo_learning', party: 'ZEBRA', kind: 'health' })), 'Recorded as Health for ZEBRA because you chose it before');
    assert.match(fixText(fix({ action: 'decide' })), /never records it on its own/);
    setLocale('ar');
    for (const action of ['trust_sender', 'map_card', 'auto_post_on', 'clear_hold', 'remember_kind', 'undo_learning', 'open_settings', 'decide', 'record'] as const) {
      assert.doesNotMatch(fixText(fix({ action, sender: 's', last4: '1', party: 'p', kind: 'salary' })), /fsms\./);
    }
  });

  test('notices say what was learned; only some fixes are one tap', () => {
    const notice = (kind: FinanceSmsLearningNotice['kind'], over: Partial<FinanceSmsLearningNotice> = {}): FinanceSmsLearningNotice =>
      ({ id: 'n', kind, sender: 'testbank', party: 'ACME', value: 'salary', last4: '1234', account_id: 'a', account_name: 'Card', ref: 'r', created_at: '', ...over });
    assert.equal(noticeText(notice('sender_trusted')), 'ANUM will now record messages from testbank on its own.');
    assert.equal(noticeText(notice('kind_remembered')), 'Money from ACME will be recorded as Salary.');
    assert.equal(noticeText(notice('category_remembered', { value: 'groceries' })), 'ACME will be filed under Groceries.');
    assert.match(noticeText(notice('card_proposed')), /Card twice for card ending 1234/);
    assert.equal(noticeText(notice('rechecked', { value: '3' })), 'Waiting messages from testbank recorded: 3. Each can be undone on its row.');
    assert.match(noticeText(notice('rechecked', { value: '2', sender: null })), /card ending 1234 recorded: 2/);
    assert.equal(fixText(fix({ action: 'undo_recheck' })), 'Recorded when waiting messages were re-checked');
    assert.equal(learnedValueLabel('own_transfer'), kindLabel('own_transfer'));
    assert.ok(ONE_TAP_FIXES.includes('trust_sender') && !ONE_TAP_FIXES.includes('decide') && REMEMBERED_KINDS.includes('salary'));
  });
});
