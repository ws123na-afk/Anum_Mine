// Settings, Integrations, iPhone view helpers (src/lib/iphoneIntegrations.ts, docs/iphone-integrations.md).
// Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { IphoneIntegration } from '@anum/contracts';
import { IPHONE_GUIDES, SCOPES, codeLabel, fullUrl, guideFor, integrationState, integrationTitle, issuableScopes, outcomeLabel, pingBody, scopeLabel } from '../src/lib/iphoneIntegrations.ts';
import { placeholdersOf, setLocale, t } from '../src/lib/i18n.ts';
import { viewFromHash } from '../src/lib/shell.ts';
import { iphoneAr } from '../src/lib/messages/iphone.ar.ts';
import { iphoneEn } from '../src/lib/messages/iphone.en.ts';

afterEach(() => setLocale('en'));

const item = (over: Partial<IphoneIntegration> = {}): IphoneIntegration => ({
  key: 'finance.sms', scope: 'finance.sms', enabled: true, allowed: true, ingest_path: '/api/v1/finance/sms/ingest', method: 'POST',
  body_fields: ['message', 'sender'], per_minute: 30, per_day: 1000, devices_with_scope: 1, last_connection_at: '2026-11-04T10:00:00Z',
  last_ok_at: '2026-11-04T10:00:00Z', failed_7d: 0, needs_safety_check: false, ...over,
});

describe('iPhone integrations page', () => {
  test('#iphone opens Settings', () => {
    assert.equal(viewFromHash('#iphone'), 'settings');
  });

  test('every guide has its words in both languages and steps that carry the address', () => {
    const keys = new Set(IPHONE_GUIDES.map((g) => g.key));
    assert.equal(keys.size, IPHONE_GUIDES.length, 'one row per integration');
    for (const guide of IPHONE_GUIDES) {
      assert.ok(guide.title in iphoneEn && guide.purpose in iphoneEn, guide.key);
      const steps = guide.steps('https://api.example.test/x');
      assert.ok(steps.length >= 3 && steps.some((s) => s.includes('https://api.example.test/x')), guide.key);
      assert.ok(guide.body.length > 0 && guide.body.every((line) => line.includes(' = ')), guide.key);
      setLocale('ar');
      assert.notEqual(t(guide.title), guide.title);
      setLocale('en');
    }
    assert.deepEqual(['finance.sms', 'finance.wallet', 'attendance.location'].map((k) => guideFor(k)?.key), ['finance.sms', 'finance.wallet', 'attendance.location']);
    assert.equal(guideFor('purchases.share')?.settingsHash, '#purchases-settings');
    assert.equal(guideFor('future.integration'), null);
    assert.equal(integrationTitle({ key: 'future.integration' }), 'future.integration');  // an API integration without a row still shows
  });

  test('Arabic has the same keys and placeholders as English, and no long dashes', () => {
    assert.deepEqual(Object.keys(iphoneAr).sort(), Object.keys(iphoneEn).sort());
    for (const [key, value] of Object.entries(iphoneEn)) {
      assert.deepEqual(placeholdersOf(iphoneAr[key as keyof typeof iphoneEn]), placeholdersOf(value), key);
      for (const text of [String(value), String(iphoneAr[key as keyof typeof iphoneEn])]) assert.ok(!/[–—]/.test(text), key);
    }
  });

  test('labels fall back to the code itself, never to a blank', () => {
    assert.equal(codeLabel('rate_limited'), 'Too many requests');
    assert.equal(codeLabel('something_new'), 'something_new');
    assert.equal(codeLabel(null), '-');
    assert.equal(scopeLabel('attendance'), 'Attendance');
    assert.equal(scopeLabel('future.scope'), 'future.scope');
    assert.equal(outcomeLabel('denied'), 'Refused');
    setLocale('ar');
    assert.equal(scopeLabel('attendance'), 'الحضور');
    assert.deepEqual(SCOPES.map(scopeLabel).filter((label) => label.includes('.')), []);
  });

  test('only integrations the role may use can be ticked on a new token', () => {
    const list = [item(), item({ key: 'finance.wallet', scope: 'finance.wallet' }), item({ key: 'attendance.location', scope: 'attendance', allowed: false })];
    assert.deepEqual(issuableScopes(list), ['finance.sms', 'finance.wallet']);
    assert.deepEqual(issuableScopes([]), []);
  });

  test('one word for each integration state', () => {
    assert.equal(integrationState(item({ enabled: false })), 'off');
    assert.equal(integrationState(item({ devices_with_scope: 0 })), 'no_device');
    assert.equal(integrationState(item({ last_connection_at: null, last_ok_at: null })), 'never');
    assert.equal(integrationState(item({ failed_7d: 2, last_ok_at: '2026-11-04T09:00:00Z' })), 'failing');
    assert.equal(integrationState(item({ failed_7d: 2 })), 'ok');  // failures earlier, the last request worked
  });

  test('addresses and the ping body', () => {
    assert.equal(fullUrl('https://api.example.test/', '/api/v1/integrations/iphone/ping'), 'https://api.example.test/api/v1/integrations/iphone/ping');
    assert.equal(pingBody('attendance.location'), '{"integration":"attendance.location"}');
  });
});
