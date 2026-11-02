// Unit tests for emailing an invoice, payment reminders, payment links and idempotent payments
// (docs/business.md#emailing-an-invoice-and-reminders, docs/business.md#payment-links).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { emailBody, linkProvider, messageStatusLabel, messageTitle, messageTone, newPaymentKey, waitingMessages } from '../src/lib/invoices.ts';
import { setLocale, t } from '../src/lib/i18n.ts';

describe('invoice emails and reminders', () => {
  test('an empty address means the buyer email; a bad one is refused before the request', () => {
    setLocale('en');
    assert.deepEqual(emailBody('', 'ar'), { ok: true, value: { language: 'ar' } });
    assert.deepEqual(emailBody('  ap@amal.example ', 'en'), { ok: true, value: { to: 'ap@amal.example', language: 'en' } });
    for (const bad of ['not an address', 'a@b', '<x>@y.example', 'a b@c.example']) {
      const checked = emailBody(bad, 'en');
      assert.equal(checked.ok, false, bad);
      if (!checked.ok) assert.match(checked.error, /valid email/);
    }
  });

  test('statuses, tones and titles in English and Arabic', () => {
    setLocale('en');
    assert.equal(messageStatusLabel('waiting_approval'), 'Waiting for approval');
    assert.equal(messageTone('sent'), 'success');
    assert.equal(messageTone('waiting_approval'), 'warning');
    assert.equal(messageTone('failed'), 'dangerTone');
    assert.equal(messageTitle({ kind: 'reminder', reminder_number: 2 }), 'Reminder 2');
    assert.equal(messageTitle({ kind: 'invoice', reminder_number: null }), 'Invoice email');
    setLocale('ar');
    assert.equal(messageStatusLabel('sent'), 'أُرسلت');
    assert.equal(messageTitle({ kind: 'reminder', reminder_number: 1 }), 'التذكير 1');
    assert.match(t('inv.link.off'), /Moyasar/);
    setLocale('en');
  });

  test('one request of a kind waits at a time', () => {
    const items = [{ kind: 'invoice' as const, status: 'sent' as const }, { kind: 'reminder' as const, status: 'waiting_approval' as const }];
    assert.equal(waitingMessages(items, 'invoice'), false);
    assert.equal(waitingMessages(items, 'reminder'), true);
  });
});

describe('idempotent payments', () => {
  test('each payment form gets its own key the API accepts', () => {
    const key = newPaymentKey(() => '1b2c-3d4e');
    assert.equal(key, 'pay-1b2c-3d4e');
    assert.match(newPaymentKey(), /^pay-[A-Za-z0-9_-]{8,76}$/);
    assert.notEqual(newPaymentKey(), newPaymentKey());
    assert.equal(newPaymentKey(() => 'a/b c+d'), 'pay-abcd');
  });
});

describe('card payment link provider (Moyasar or Stripe)', () => {
  test('a shown link names its own provider', () => {
    assert.equal(linkProvider({ provider: 'stripe' }, { moyasar: 'on', stripe: 'on', provider: 'moyasar', mode: 'test' }), 'stripe');
    assert.equal(linkProvider({ provider: 'moyasar' }, null), 'moyasar');
  });
  test('without a link, the provider a new link would use, or none when card payments are off', () => {
    assert.equal(linkProvider(null, { moyasar: 'off', stripe: 'on', provider: 'stripe', mode: 'live' }), 'stripe');
    assert.equal(linkProvider(undefined, { moyasar: 'off', stripe: 'off', provider: null, mode: null }), null);
    assert.equal(linkProvider(null, null), null);
  });
  test('each provider has a line in English and Arabic', () => {
    for (const locale of ['en', 'ar'] as const) {
      setLocale(locale);
      for (const key of ['inv.link.via.moyasar', 'inv.link.via.stripe', 'inv.pay.viaStripe'] as const) assert.notEqual(t(key), key);
    }
    setLocale('en');
    assert.match(t('inv.link.off'), /Stripe/);
  });
});
