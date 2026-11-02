// IBKR trading view helpers (src/lib/financeIbkr.ts, docs/finance-live-trading.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { IbkrConnection, IbkrOverview } from '@anum/contracts';
import { checkTicket, connectionFor, needsLogin, normalizeFingerprint, notional, orderTone, secondsLeft, shortHash, sign } from '../src/lib/financeIbkr.ts';
import { financeIbkrEn } from '../src/lib/messages/financeIbkr.en.ts';
import { financeIbkrAr } from '../src/lib/messages/financeIbkr.ar.ts';

const connection = (patch: Partial<IbkrConnection>): IbkrConnection => ({
  id: 'c', environment: 'paper', gateway_host: 'ibkr-gateway', hint: 'DU...567', status: 'active', session: 'authenticated', session_message: null,
  session_checked_at: null, verified_at: null, trading_type: null, last_sync_at: null, last_error: null, statements_connected: false, created_at: '2026-11-02T00:00:00Z',
  ...patch,
});

describe('IBKR helpers', () => {
  test('notional is exact and rounds half up to cents', () => {
    assert.equal(notional('1', '9.50'), '9.50');
    assert.equal(notional('0.3333', '3'), '1.00');
    assert.equal(notional('3', '0.335'), '1.01');
    assert.equal(notional('1', 'abc'), null);
    assert.equal(notional('-1', '2'), null);
  });

  test('the ticket checks the allowed list, whole shares and the per-order cap', () => {
    const base = { symbol: 'SPY', quantity: '1', limit_price: '9.5' };
    assert.equal(checkTicket(base, ['SPY'], false, '10'), null);
    assert.equal(checkTicket({ ...base, symbol: 'TSLA' }, ['SPY'], false, '10'), 'symbol');
    assert.equal(checkTicket({ ...base, quantity: '0' }, ['SPY'], false, '10'), 'quantity');
    assert.equal(checkTicket({ ...base, quantity: '0.5' }, ['SPY'], false, '10'), 'wholeShares');
    assert.equal(checkTicket({ ...base, quantity: '0.5' }, ['SPY'], true, '10'), null);
    assert.equal(checkTicket({ ...base, limit_price: '' }, ['SPY'], false, '10'), 'price');
    assert.equal(checkTicket({ ...base, limit_price: '10.01' }, ['SPY'], false, '10'), 'overOrder');
    assert.equal(checkTicket({ ...base, limit_price: '10.00' }, ['SPY'], false, '10'), null);
  });

  test('fingerprints accept the openssl form and refuse anything else', () => {
    assert.equal(normalizeFingerprint(`sha256 Fingerprint=${'AB:'.repeat(31)}AB`.replace(' ', '')), 'ab'.repeat(32));
    assert.equal(normalizeFingerprint('ab'.repeat(31)), null);
    assert.equal(normalizeFingerprint('zz'.repeat(32)), null);
  });

  test('connections, login state, tones, signs and the countdown', () => {
    const overview = { connections: [connection({}), connection({ id: 'l', environment: 'live', status: 'revoked' })] } as unknown as IbkrOverview;
    assert.equal(connectionFor(overview, 'paper')?.id, 'c');
    assert.equal(connectionFor(overview, 'live'), null);
    assert.equal(needsLogin(connection({ session: 'expired' })), true);
    assert.equal(needsLogin(connection({})), false);
    assert.equal(orderTone('filled'), 'success');
    assert.equal(orderTone('unknown'), 'danger');
    assert.deepEqual([sign('-1.20'), sign('0.00'), sign('3'), sign(null), sign('-0')], [-1, 0, 1, 0, 0]);
    assert.equal(secondsLeft('2026-11-02T00:05:00Z', Date.parse('2026-11-02T00:04:30Z')), 30);
    assert.equal(secondsLeft('2026-11-02T00:05:00Z', Date.parse('2026-11-02T00:06:00Z')), 0);
    assert.equal(shortHash('a'.repeat(64)), 'aaaaaaaa...aaaaaa');
  });

  test('every English string has an Arabic twin with the same placeholders and no promise of earnings', () => {
    for (const [key, en] of Object.entries(financeIbkrEn)) {
      const ar = financeIbkrAr[key as keyof typeof financeIbkrEn];
      assert.ok(ar, key);
      const holes = (text: string) => [...String(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
      assert.deepEqual(holes(String(ar)), holes(String(en)), key);
      assert.doesNotMatch(String(en), /guarantee|will earn|expected return|projected/i, key);
    }
    assert.match(String(financeIbkrEn['ibkr.limits.lossText']), /never sells/);
  });
});
