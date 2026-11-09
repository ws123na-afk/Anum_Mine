// Accounts and Transactions helpers (src/lib/financeRegister.ts, docs/finance.md#register-and-account-overview).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { FinanceAccount, FinanceAccountOverview } from '@anum/contracts';
import {
  NO_REGISTER_FILTERS, TRANSACTION_TYPES, accountTab, activityScale, maskedNumber, monthBounds, narrowed, percentOf, primarySource, registerQuery,
  statusTone,
} from '../src/lib/financeRegister.ts';
import { placeholdersOf } from '../src/lib/i18n.ts';
import { financeRegisterAr } from '../src/lib/messages/financeRegister.ar.ts';
import { financeRegisterEn } from '../src/lib/messages/financeRegister.en.ts';

const account = (over: Partial<FinanceAccount> = {}): FinanceAccount => ({
  id: 'fin_acct_1', name: 'Current', kind: 'bank', currency: 'SAR', institution: null, last4: '4721', opening_balance: '0', balance: '100.00',
  archived: false, transaction_count: 3, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', ...over,
});
const overview = (over: Partial<FinanceAccountOverview> = {}): FinanceAccountOverview => ({
  account_id: 'fin_acct_1', sources: ['manual'], credit_limit: null, observed_balance: null, observed_at: null, difference: null, last_activity_on: null,
  last_import_at: null, last_reconciled_at: null, imported: 0, matched: 0, needs_attention: [], payment_due: null, ...over,
});

describe('register filters', () => {
  test('only set filters reach the query, and the period alone is not narrowing', () => {
    assert.deepEqual(registerQuery({ ...NO_REGISTER_FILTERS, q: 'fuel', from: '2026-11-01', to: '2026-11-30' }), { q: 'fuel', from: '2026-11-01', to: '2026-11-30' });
    assert.equal(narrowed({ ...NO_REGISTER_FILTERS, from: '2026-11-01' }), false);
    assert.equal(narrowed({ ...NO_REGISTER_FILTERS, origin: 'sms' }), true);
  });
  test('the type filter reaches the API as `type` and narrows the list', () => {
    assert.deepEqual(registerQuery({ ...NO_REGISTER_FILTERS, type: 'uncategorized' }), { type: 'uncategorized' });
    assert.equal(narrowed({ ...NO_REGISTER_FILTERS, type: 'income' }), true);
  });
  test('every transaction type is listed once and has a label in both languages', () => {
    assert.equal(TRANSACTION_TYPES.length, 21);
    assert.equal(new Set(TRANSACTION_TYPES).size, TRANSACTION_TYPES.length);
    const en = financeRegisterEn as Record<string, string>, ar = financeRegisterAr as Record<string, string>;
    for (const type of TRANSACTION_TYPES) assert.ok(en[`freg.type.${type}`] && ar[`freg.type.${type}`], type);
  });
  test('month bounds include the last day, leap years too', () => {
    assert.deepEqual(monthBounds('2026-11'), { from: '2026-11-01', to: '2026-11-30' });
    assert.deepEqual(monthBounds('2028-02'), { from: '2028-02-01', to: '2028-02-29' });
  });
});

describe('accounts', () => {
  test('a masked number never shows more than the last four digits', () => {
    assert.equal(maskedNumber('4721', true), '•••• 4721');
    assert.equal(maskedNumber('4721', false), '••••');
    assert.equal(maskedNumber(null, true), '-');
  });
  test('tabs: archived, cards, Apple Wallet, cash and bank, the rest', () => {
    assert.equal(accountTab(account({ archived: true, kind: 'card' }), undefined), 'archived');
    assert.equal(accountTab(account({ kind: 'card' }), overview({ sources: ['apple_wallet'] })), 'cards');
    assert.equal(accountTab(account(), overview({ sources: ['apple_wallet', 'sms'] })), 'wallets');
    assert.equal(accountTab(account({ kind: 'cash' }), undefined), 'cash');
    assert.equal(accountTab(account({ kind: 'investment' }), undefined), 'other');
  });
  test('the source badge is a real source, never a live connection', () => {
    assert.equal(primarySource(['statement', 'sms']), 'sms');
    assert.equal(primarySource(['statement']), 'statement');
    assert.equal(primarySource(undefined), 'manual');
  });
  test('status chips and percentages', () => {
    assert.equal(statusTone('duplicate_candidate'), 'bad');
    assert.equal(statusTone('needs_review'), 'warn');
    assert.equal(percentOf(58, 62), 94);
    assert.equal(percentOf(0, 0), null);
  });
  test('the activity scale rounds up to a readable top', () => {
    const scale = activityScale({ days: [{ day: '2026-11-01', inflow: '15000.00', outflow: '540.00' }, { day: '2026-11-02', inflow: '0', outflow: '7500' }] });
    assert.equal(scale.empty, false);
    assert.equal(scale.top, 20000);
    assert.deepEqual(scale.ticks, [0, 5000, 10000, 15000, 20000]);
    assert.equal(activityScale({ days: [{ day: '2026-11-01', inflow: '0', outflow: '0' }] }).empty, true);
  });
});

describe('catalogs', () => {
  test('Arabic has every key with the same placeholders, and no string claims a live bank connection', () => {
    for (const [key, value] of Object.entries(financeRegisterEn)) {
      const ar = financeRegisterAr[key as keyof typeof financeRegisterEn];
      assert.ok(ar, key);
      const en = typeof value === 'string' ? value : value.other;
      const arText = typeof ar === 'string' ? ar : ar.other;
      assert.deepEqual(placeholdersOf(arText).sort(), placeholdersOf(en).sort(), key);
      assert.doesNotMatch(en, /\b(synced|is connected|consent expires)\b/i, key);
    }
    assert.equal(Object.keys(financeRegisterAr).length, Object.keys(financeRegisterEn).length);
  });
});
