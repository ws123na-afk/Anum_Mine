// Unit tests for the customer portal helpers (src/lib/portal.ts, docs/business-portal.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  accessLabel, accessState, buildPortalLink, checkSigner, confirmText, decisionLine, expiryState, invoiceStatusText, invoiceStatusTone, isPortalPath, parseAccessFragment,
  problemKind, readStoredToken, resolveToken, storeToken, ticketStatusLabel, waitingTotal, withoutFragment, PORTAL_STORAGE_KEY,
} from '../src/lib/portal.ts';
import { setLocale } from '../src/lib/i18n.ts';

const TOKEN = 'anum_portal.tenant_local.workspace_foundation.bpacc_1.abcdefghijklmnopqrstuvwxyz0123456789ABCDEF';

function memory(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => void data.set(key, value), removeItem: (key: string) => void data.delete(key), data };
}

describe('customer portal link', () => {
  test('the portal page is a public path', () => {
    assert.equal(isPortalPath('/portal'), true);
    assert.equal(isPortalPath('/portal/'), true);
    assert.equal(isPortalPath('/portals'), false);
    assert.equal(isPortalPath('/'), false);
  });

  test('the token is read from the fragment and bad ones are ignored', () => {
    assert.equal(parseAccessFragment(`#access=${TOKEN}`), TOKEN);
    assert.equal(parseAccessFragment(`access=${TOKEN}`), TOKEN);
    assert.equal(parseAccessFragment(`#x=1&access=${encodeURIComponent(TOKEN)}`), TOKEN);
    assert.equal(parseAccessFragment('#access=not-a-token'), null);
    assert.equal(parseAccessFragment('#access=anum_portal.a.b.c.short'), null);
    assert.equal(parseAccessFragment('#access=%E0%A4%A'), null);
    assert.equal(parseAccessFragment(''), null);
    assert.equal(parseAccessFragment('#offers'), null);
  });

  test('the address loses its fragment and keeps the path and query', () => {
    assert.equal(withoutFragment({ pathname: '/portal', search: '' }), '/portal');
    assert.equal(withoutFragment({ pathname: '/portal', search: '?lang=ar' }), '/portal?lang=ar');
  });

  test('a fragment wins over the saved link; a refresh uses the saved one; storage that throws is survived', () => {
    const store = memory();
    const first = resolveToken(`#access=${TOKEN}`, store);
    assert.deepEqual(first, { token: TOKEN, fromFragment: true });
    storeToken(store, first.token);
    assert.equal(store.data.get(PORTAL_STORAGE_KEY), TOKEN);
    assert.deepEqual(resolveToken('', store), { token: TOKEN, fromFragment: false });
    const other = TOKEN.replace('bpacc_1', 'bpacc_2');
    assert.equal(resolveToken(`#access=${other}`, store).token, other);
    storeToken(store, null);
    assert.equal(readStoredToken(store), null);
    assert.deepEqual(resolveToken('', store), { token: null, fromFragment: false });
    // A malformed fragment still has to be removed from the address bar.
    assert.deepEqual(resolveToken('#access=garbage', memory()), { token: null, fromFragment: true });
    const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };
    assert.equal(readStoredToken(broken), null);
    assert.doesNotThrow(() => storeToken(broken, TOKEN));
    assert.equal(readStoredToken(memory({ [PORTAL_STORAGE_KEY]: 'tampered' })), null);
  });

  test('the owner builds the shareable address from the API path', () => {
    assert.equal(buildPortalLink('https://app.anum360os.com', `/portal#access=${TOKEN}`), `https://app.anum360os.com/portal#access=${TOKEN}`);
    assert.equal(buildPortalLink('https://app.anum360os.com/', `/portal#access=${TOKEN}`), `https://app.anum360os.com/portal#access=${TOKEN}`);
    assert.equal(buildPortalLink('http://localhost:5173', 'portal#access=x'), 'http://localhost:5173/portal#access=x');
  });
});

describe('customer portal states', () => {
  test('API refusals map to what the page does', () => {
    assert.equal(problemKind(401, 'This link has expired. Ask the business that sent it for a new one.'), 'link');
    assert.equal(problemKind(403, 'This link can read but not approve. Ask the business to turn on approvals for you.'), 'forbidden');
    assert.equal(problemKind(403, 'This business has paused its account, so nothing can be changed now. You can still read everything.'), 'archived');
    assert.equal(problemKind(404, 'Offer not found'), 'missing');
    assert.equal(problemKind(409, 'This offer changed after you opened it.'), 'changed');
    assert.equal(problemKind(429, 'You can open 5 requests a day from this link.'), 'limit');
    assert.equal(problemKind(0, null), 'network');
    assert.equal(problemKind(null, null), 'network');
    assert.equal(problemKind(500, null), 'error');
  });

  test('labels and tones for invoices and requests', () => {
    setLocale('en');
    assert.equal(invoiceStatusText({ status: 'sent', overdue: false, payment_state: 'unpaid' }), 'Open');
    assert.equal(invoiceStatusText({ status: 'sent', overdue: true, payment_state: 'unpaid' }), 'Overdue');
    assert.equal(invoiceStatusText({ status: 'sent', overdue: false, payment_state: 'partially_paid' }), 'Part paid');
    assert.equal(invoiceStatusText({ status: 'paid', overdue: false, payment_state: 'paid' }), 'Paid');
    assert.equal(invoiceStatusText({ status: 'void', overdue: false, payment_state: 'unpaid' }), 'Void');
    assert.equal(invoiceStatusTone({ status: 'sent', overdue: true, payment_state: 'unpaid' }), 'dangerTone');
    assert.equal(invoiceStatusTone({ status: 'paid', overdue: false, payment_state: 'paid' }), 'success');
    assert.equal(ticketStatusLabel('waiting_on_customer'), 'Waiting for you');
  });

  test('the link expiry notice shows from seven days', () => {
    const now = Date.parse('2026-10-19T12:00:00Z');
    assert.deepEqual(expiryState('2026-10-19T11:00:00Z', now), { kind: 'expired' });
    assert.deepEqual(expiryState('2026-10-22T12:00:00Z', now), { kind: 'soon', days: 3 });
    assert.deepEqual(expiryState('2026-10-26T12:00:00Z', now), { kind: 'soon', days: 7 });
    assert.deepEqual(expiryState('2026-11-26T12:00:00Z', now), { kind: 'ok', days: 38 });
    assert.deepEqual(expiryState('garbage', now), { kind: 'expired' });
    assert.equal(waitingTotal({ offers_waiting: 2, change_orders_waiting: 1 }), 3);
  });

  test('the signer must type a real name', () => {
    setLocale('en');
    assert.deepEqual(checkSigner('  Sara   Khan '), { ok: true, name: 'Sara Khan' });
    assert.deepEqual(checkSigner('سارة خان'), { ok: true, name: 'سارة خان' });
    assert.equal(checkSigner('S').ok, false);
    assert.equal(checkSigner('12345').ok, false);
    assert.equal(checkSigner('x'.repeat(161)).ok, false);
  });

  test('the confirm step names exactly what is decided and the amount', () => {
    setLocale('en');
    const approve = confirmText('offer', 'approved', 'Website rebuild', 5750, 'SAR');
    assert.match(approve, /approve the offer/);
    assert.match(approve, /Website rebuild/);
    assert.match(approve, /5,750\.00/);
    assert.match(confirmText('change', 'declined', 'Change order 2: Extra pages', 575, 'SAR'), /decline Change order 2: Extra pages/);
    assert.match(confirmText('offer', 'approved', 'X', null, 'SAR'), /-\.$/);
    setLocale('ar');
    assert.match(confirmText('offer', 'approved', 'Website', 100, 'SAR'), /توافق/);
    setLocale('en');
  });
});

describe('owner view of the portal', () => {
  test('a customer decision reads as a sentence with the name and date', () => {
    setLocale('en');
    const decision = { decision: 'approved' as const, signer_name: 'Sara Khan', note: '', decided_at: '2026-11-03T10:00:00Z', version: 2, content_hash: 'a'.repeat(64), current: true };
    assert.equal(decisionLine(decision, () => '3 Nov 2026'), 'Approved by Sara Khan in the portal on 3 Nov 2026');
    assert.equal(decisionLine({ ...decision, decision: 'declined' }, () => '3 Nov 2026'), 'Declined by Sara Khan in the portal on 3 Nov 2026');
  });

  test('a link is active, expired or turned off', () => {
    setLocale('en');
    assert.equal(accessState({ status: 'active', expired: false }), 'active');
    assert.equal(accessState({ status: 'active', expired: true }), 'expired');
    assert.equal(accessState({ status: 'revoked', expired: false }), 'revoked');
    assert.equal(accessLabel('revoked'), 'Turned off');
  });
});
