// Finance, Connections and data capture helpers (src/lib/financeConnections.ts). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { accountSources, connectionsTabFor, importFormat, lastActivity, lastImportByAccount, maskedToken, searchAccounts, shortcutRoutes, smsCounts } from '../src/lib/financeConnections.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { financeConnectionsAr } from '../src/lib/messages/financeConnections.ar.ts';
import { financeConnectionsEn } from '../src/lib/messages/financeConnections.en.ts';

afterEach(() => setLocale('en'));

const settings = (over: Partial<Parameters<typeof accountSources>[1] & object> = {}) => ({
  account_map: [], wallet_cards: [], default_account_id: null, cash_account_id: null, ...over,
});

describe('account sources', () => {
  test('an account fed by nothing is kept by hand', () => {
    assert.deepEqual(accountSources({ id: 'a' }, settings(), []), ['manual']);
    assert.deepEqual(accountSources({ id: 'a' }, null, []), ['manual']);
  });
  test('mapped last four digits, a Wallet card, the default or the cash account make it an SMS account', () => {
    assert.deepEqual(accountSources({ id: 'a' }, settings({ account_map: [{ last4: '1234', account_id: 'a', credit_limit: null }] }), []), ['sms']);
    assert.deepEqual(accountSources({ id: 'a' }, settings({ wallet_cards: [{ card: 'Visa', last4: null, account_id: 'a' }] }), []), ['sms']);
    assert.deepEqual(accountSources({ id: 'a' }, settings({ default_account_id: 'a' }), []), ['sms']);
    assert.deepEqual(accountSources({ id: 'a' }, settings({ cash_account_id: 'a' }), []), ['sms']);
    assert.deepEqual(accountSources({ id: 'b' }, settings({ default_account_id: 'a' }), []), ['manual']);
  });
  test('statement imports add the statement source', () => {
    assert.deepEqual(accountSources({ id: 'a' }, settings({ default_account_id: 'a' }), [{ account_id: 'a' }]), ['sms', 'statement']);
    assert.deepEqual(accountSources({ id: 'a' }, settings(), [{ account_id: 'b' }]), ['manual']);
  });
});

describe('activity', () => {
  test('the newest import per account', () => {
    assert.deepEqual(lastImportByAccount([
      { account_id: 'a', created_at: '2026-11-01T10:00:00Z' }, { account_id: 'a', created_at: '2026-11-03T10:00:00Z' }, { account_id: 'b', created_at: '2026-10-01T10:00:00Z' },
    ]), { a: '2026-11-03T10:00:00Z', b: '2026-10-01T10:00:00Z' });
  });
  test('the last data received is the newest real event, with its source', () => {
    assert.equal(lastActivity(null, [], []), null);
    assert.deepEqual(lastActivity({ last_received_at: '2026-11-05T09:00:00Z' }, [{ created_at: '2026-11-04T09:00:00Z' }], [{ last_success_at: null }]),
      { at: '2026-11-05T09:00:00Z', source: 'sms' });
    assert.deepEqual(lastActivity({ last_received_at: null }, [{ created_at: '2026-11-04T09:00:00Z' }], [{ last_success_at: '2026-11-04T10:00:00Z' }]),
      { at: '2026-11-04T10:00:00Z', source: 'rates' });
  });
});

describe('shortcut routes and counts', () => {
  const device = (status: 'active' | 'paused' | 'revoked', scopes: string[]) => ({ status, scopes }) as never;
  const integration = (scope: string, enabled: boolean) => ({ scope, enabled }) as never;
  test('a route works only when it is on and an active device carries its scope', () => {
    assert.deepEqual(shortcutRoutes(null), { live: 0, total: 0 });
    assert.deepEqual(shortcutRoutes({
      devices: [device('active', ['finance.sms']), device('paused', ['finance.wallet'])],
      integrations: [integration('finance.sms', true), integration('finance.wallet', true), integration('attendance', false)],
    }), { live: 1, total: 3 });
  });
  test('SMS counts: recorded is posted plus attached, the test ping is not a message', () => {
    assert.deepEqual(smsCounts({ counts: { posted: 4, attached: 1, review: 2, rejected: 3, test_ok: 5 }, review_count: 2 }),
      { received: 10, recorded: 5, review: 2, ignored: 3 });
    assert.deepEqual(smsCounts({ counts: {}, review_count: 0 }), { received: 0, recorded: 0, review: 0, ignored: 0 });
  });
});

describe('words', () => {
  test('a token is never shown again: only its hint behind dots', () => {
    assert.equal(maskedToken('wxyz'), '••••••••••••wxyz');
    assert.ok(!maskedToken('ab').includes('anum_sms'));
  });
  test('import formats name PDF and scanned PDF statements', () => {
    assert.equal(importFormat('pdf'), 'PDF');
    assert.equal(importFormat('pdf_scan'), 'PDF (scanned)');
    assert.equal(importFormat('uae_generic'), 'CSV');
    setLocale('ar');
    assert.equal(importFormat('pdf_scan'), 'PDF (ممسوح ضوئيا)');
  });
  test('account search covers name, bank and last four digits', () => {
    const list = [{ name: 'Current', institution: 'Test Bank', last4: '1234' }, { name: 'Wallet', institution: null, last4: null }];
    assert.equal(searchAccounts(list, '').length, 2);
    assert.deepEqual(searchAccounts(list, 'test').map((a) => a.name), ['Current']);
    assert.deepEqual(searchAccounts(list, '123').map((a) => a.name), ['Current']);
    assert.deepEqual(searchAccounts(list, 'wal').map((a) => a.name), ['Wallet']);
  });
  test('#finance-sms opens the SMS tab, the rest the accounts', () => {
    assert.equal(connectionsTabFor('sms'), 'sms');
    assert.equal(connectionsTabFor('connections'), 'accounts');
  });
  test('every English key has an Arabic twin with the same placeholders, and no long dashes', () => {
    for (const [key, value] of Object.entries(financeConnectionsEn)) {
      const ar = financeConnectionsAr[key as keyof typeof financeConnectionsEn];
      assert.ok(ar, key);
      assert.deepEqual(placeholdersOf(ar), placeholdersOf(value), key);
      for (const text of [value, ar].flatMap((m) => (typeof m === 'string' ? [m] : Object.values(m)))) assert.doesNotMatch(String(text), /[–—]/, key);
    }
    assert.deepEqual(Object.keys(financeConnectionsAr).sort(), Object.keys(financeConnectionsEn).sort());
  });
});
