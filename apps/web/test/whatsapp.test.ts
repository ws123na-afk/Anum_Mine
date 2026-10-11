// Unit tests for the WhatsApp inbox helpers in src/lib/whatsapp.ts.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import {
  accountBody, categoryLabel, contactLabel, emptyForm, fillTemplate, mergeMessages, opensInline, parameterCount, provenanceLabel,
  randomVerifyToken, sendableTemplates, statusTone, webhookUrl, windowLabel, windowState, type WaAccount, type WaMessage, type WaTemplate,
} from '../src/lib/whatsapp.ts';

afterEach(() => setLocale('en'));

const account: WaAccount = {
  configured: true, can_manage: true, webhook_path: '/api/v1/whatsapp/webhook', deployment_problems: [], phone_number_id: '106540352242922',
  business_account_id: '102290129340398', display_phone_number: '+966 11 000 0000', verified_name: 'Tyre Shop', quality_rating: 'GREEN',
  access_token_hint: '...oken', app_secret_set: true, verify_token_set: true, owner_test_number: '966500000999', auto_send_templates: ['order_update'],
  triage_enabled: true, status: 'connected', last_error: null, last_checked_at: null, last_webhook_at: null, updated_at: null, updated_by: null,
};

const message = (id: string, timestamp: string): WaMessage => ({
  id, conversation_id: 'c', wamid: null, direction: 'inbound', kind: 'text', body: id, media: null, template: null, status: 'received',
  error: null, sent_by: null, approval_id: null, timestamp, approval: null,
});

describe('account form', () => {
  test('a new connection needs every secret; an update keeps the saved ones', () => {
    const form = { ...emptyForm(null), phoneNumberId: '106540352242922', businessAccountId: '102290129340398' };
    assert.deepEqual(accountBody(form, false), { ok: false, error: 'To connect, enter the access token, the app secret and a verify token.' });
    const update = accountBody(emptyForm(account), true);
    assert.ok(update.ok);
    assert.deepEqual(update.value, {
      phone_number_id: '106540352242922', business_account_id: '102290129340398', owner_test_number: '966500000999',
      triage_enabled: true, auto_send_templates: ['order_update'],
    });
  });

  test('ids, verify token and test number are checked before sending', () => {
    const base = emptyForm(account);
    assert.equal(accountBody({ ...base, phoneNumberId: 'abc' }, true).ok, false);
    assert.equal(accountBody({ ...base, businessAccountId: '12' }, true).ok, false);
    const short = accountBody({ ...base, verifyToken: 'short' }, true);
    assert.ok(!short.ok && short.error.includes('16 characters'));
    assert.equal(accountBody({ ...base, ownerTestNumber: 'call me' }, true).ok, false);
    const full = accountBody({ ...base, accessToken: ' example-token ', appSecret: 'example-secret', verifyToken: 'a'.repeat(20), ownerTestNumber: '+966 500 000 999', autoSend: ['b', 'a', 'a'] }, false);
    assert.ok(full.ok);
    assert.equal(full.value.access_token, 'example-token');
    assert.equal(full.value.owner_test_number, '966500000999');
    assert.deepEqual(full.value.auto_send_templates, ['a', 'b']);
  });

  test('a generated verify token is long and url-safe', () => {
    const token = randomVerifyToken((n) => Uint8Array.from({ length: n }, (_, i) => i * 7));
    assert.equal(token.length, 32);
    assert.match(token, /^[A-Za-z0-9_-]+$/);
  });
});

describe('24-hour window', () => {
  const now = new Date('2026-10-11T12:00:00Z');
  test('open with hours or minutes left, closed after 24 hours or before the customer wrote', () => {
    assert.deepEqual(windowState({ window_closes_at: '2026-10-11T17:30:00Z' }, now), { open: true, hoursLeft: 5, minutesLeft: 30 });
    assert.equal(windowLabel({ window_closes_at: '2026-10-11T17:30:00Z', last_inbound_at: '2026-10-10T17:30:00Z' }, now), 'Free-form replies for 5 more hours');
    assert.equal(windowLabel({ window_closes_at: '2026-10-11T12:20:00Z', last_inbound_at: 'x' }, now), 'Free-form replies for 20 more minutes');
    assert.equal(windowLabel({ window_closes_at: '2026-10-11T11:00:00Z', last_inbound_at: 'x' }, now), '24-hour window closed: templates only');
    assert.equal(windowLabel({ window_closes_at: null, last_inbound_at: null }, now), 'The customer has not written: templates only');
  });

  test('Arabic uses its own plural forms', () => {
    setLocale('ar');
    assert.equal(windowLabel({ window_closes_at: '2026-10-11T14:00:00Z', last_inbound_at: 'x' }, now), 'الرد الحر متاح لساعتين أخريين');
    assert.equal(categoryLabel('complaint'), 'شكوى');
  });
});

describe('templates', () => {
  test('parameters are counted and filled like the API does', () => {
    assert.equal(parameterCount('Hi {{1}}, order {{2}} for {{1}}'), 2);
    assert.equal(fillTemplate('Hi {{1}}, order {{2}}', ['Ali', '']), 'Hi Ali, order {{2}}');
    assert.equal(fillTemplate('Hi {{name}} and {{name}}', ['Sara']), 'Hi Sara and Sara');
  });

  test('only approved templates can be sent', () => {
    const template = (name: string, status: string): WaTemplate => ({ name, language: 'en_US', status, category: 'UTILITY', body: '', parameter_count: 0, header: null, footer: null, buttons: [], auto_send: false });
    assert.deepEqual(sendableTemplates([template('a', 'APPROVED'), template('b', 'PENDING'), template('c', 'REJECTED')]).map((t) => t.name), ['a']);
  });
});

describe('labels', () => {
  test('provenance names the model and counts untrusted messages', () => {
    const base = { text: 'x', category: 'other' as const, summary: '', language: 'en', generated_at: '', generated_by: 'u', untrusted_messages: 3 };
    assert.equal(provenanceLabel({ ...base, source: 'model', provider: 'anthropic', model: 'claude-haiku-5-5' }), 'AI draft by anthropic / claude-haiku-5-5 from 3 customer messages, read as untrusted text');
    assert.match(provenanceLabel({ ...base, source: 'rules', provider: null, model: null }), /Rule-based draft/);
  });

  test('contact, status tone, webhook URL and inline media', () => {
    assert.equal(contactLabel({ contact_name: '  ', wa_id: '966500000001' }), '+966500000001');
    assert.equal(contactLabel({ contact_name: 'أحمد', wa_id: '1' }), 'أحمد');
    assert.equal(statusTone('not_sent'), 'dangerTone');
    assert.equal(statusTone('awaiting_approval'), 'warning');
    assert.equal(statusTone('read'), 'success');
    assert.equal(webhookUrl('https://api.example.test/', '/api/v1/whatsapp/webhook'), 'https://api.example.test/api/v1/whatsapp/webhook');
    assert.ok(opensInline('image/jpeg') && opensInline('audio/ogg') && !opensInline('application/pdf') && !opensInline('image/svg+xml'));
  });
});

test('messages merge oldest first without duplicates', () => {
  const merged = mergeMessages([message('b', '2026-10-11T10:00:00Z'), message('c', '2026-10-11T11:00:00Z')], [message('a', '2026-10-11T09:00:00Z'), { ...message('c', '2026-10-11T11:00:00Z'), body: 'updated' }]);
  assert.deepEqual(merged.map((m) => [m.id, m.body]), [['a', 'a'], ['b', 'b'], ['c', 'updated']]);
});
