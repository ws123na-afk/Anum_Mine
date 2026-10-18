// Unit tests for the Phone calls helpers (src/lib/calls.ts, docs/calls.md): the dialpad, tel: links,
// durations, list paths, hashes, the AI call guard, the provider form body and both catalogs.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  aiCallBlocker, backspace, callsHash, callsPath, callWith, contactsPath, costText, dialable, durationText, elapsedSeconds, initials,
  isDialable, longPressZero, outcomeTone, parseCallsHash, parseDuration, pasteNumber, pressKey, providerBody, providerSteps, telHref,
  type ApiCallsSetup,
} from '../src/lib/calls.ts';
import { callsAr } from '../src/lib/messages/calls.ar.ts';
import { callsEn } from '../src/lib/messages/calls.en.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { viewFromHash } from '../src/lib/shell.ts';

const setup = (over: Partial<ApiCallsSetup['capabilities']> = {}, canLog = true): ApiCallsSetup => ({
  can_read: true, can_log: canLog, can_manage: false, can_create_tasks: true, model_ready: true, provider_connected: true, transcription_configured: false,
  capabilities: { browser_calls: true, recording: false, transcription: false, ai_calls: true, inbound_ai: false, problem: null, ai_problem: null, cost_per_minute_cents: 5, recording_notice: null, ...over },
});

describe('dialpad', () => {
  test('keys add digits, * and #, + only first, at most 20', () => {
    let number = '';
    for (const key of ['+', '9', '7', '1', '+', '*', '#', 'x']) number = pressKey(number, key);
    assert.equal(number, '+971*#');
    assert.equal(pressKey('1'.repeat(20), '2'), '1'.repeat(20));
    assert.equal(backspace('+971'), '+97');
    assert.equal(backspace(''), '');
  });
  test('a long press of 0 types + only on an empty number', () => {
    assert.equal(longPressZero(''), '+');
    assert.equal(longPressZero('05'), '050');
  });
  test('pasted numbers keep what can be dialled', () => {
    assert.equal(pasteNumber('', '+971 (50) 123-4567'), '+971501234567');
    assert.equal(pasteNumber('', 'Call me on ٠٥٠١٢٣٤٥٦٧ please'), '0501234567');
    assert.equal(pasteNumber('05', '+0 1'), '0501');
  });
  test('tel: links are made only for dialable numbers and escape #', () => {
    assert.equal(telHref('+971 50 123 4567'), 'tel:+971501234567');
    assert.equal(telHref('*123#'), 'tel:*123%23');
    assert.equal(telHref('12'), null);
    assert.equal(telHref('javascript:alert(1)'), null);
    assert.equal(isDialable('999'), true);
    assert.equal(dialable('+97+1'), '+971');
  });
});

describe('durations', () => {
  test('format and parse minutes and seconds', () => {
    assert.equal(durationText(65), '1:05');
    assert.equal(durationText(3723), '1:02:03');
    assert.equal(durationText(null), '');
    assert.equal(parseDuration('2:30'), 150);
    assert.equal(parseDuration('٢:٣٠'), 150);
    assert.equal(parseDuration('90'), 90);
    assert.equal(parseDuration('3m'), 180);
    assert.equal(parseDuration('2:75'), null);
    assert.equal(parseDuration('soon'), null);
    assert.equal(parseDuration(''), null);
  });
  test('time away at the dialer is rounded and capped', () => {
    assert.equal(elapsedSeconds(1000, 66_400), 65);
    assert.equal(elapsedSeconds(5000, 1000), 0);
    assert.equal(elapsedSeconds(0, 10 ** 12), 86400);
  });
  test('cost is per started minute', () => {
    assert.equal(costText(61, 5), '$0.10');
    assert.equal(costText(120, null), '$0.00');
  });
});

describe('routes and paths', () => {
  test('list paths carry search, filters and paging', () => {
    assert.equal(callsPath({ query: '  tyres ', outcome: 'busy', mine: true, offset: 25 }), '/api/v1/calls/log?limit=25&offset=25&q=tyres&outcome=busy&mine=true');
    assert.equal(callsPath(), '/api/v1/calls/log?limit=25&offset=0');
    assert.equal(contactsPath({ favourites: true }), '/api/v1/calls/contacts?limit=25&offset=0&favourites=true');
  });
  test('hashes name the tab and the open record, and the shell opens the view', () => {
    assert.deepEqual(parseCallsHash('#calls'), { tab: 'recents', call: null, contact: null });
    assert.deepEqual(parseCallsHash('#calls?call=call_1'), { tab: 'recents', call: 'call_1', contact: null });
    assert.deepEqual(parseCallsHash('#calls-contacts?contact=c%201'), { tab: 'contacts', call: null, contact: 'c 1' });
    assert.equal(parseCallsHash('#calls-nope').tab, 'recents');
    assert.equal(callsHash('contacts', { contact: 'c 1' }), '#calls-contacts?contact=c%201');
    assert.equal(callsHash('ai', { contact: 'x' }), '#calls-ai');
    for (const hash of ['#calls', '#calls-provider', '#calls?call=x']) assert.equal(viewFromHash(hash), 'calls');
  });
});

describe('labels and guards', () => {
  test('who a call was with and its tone', () => {
    setLocale('en');
    assert.equal(callWith({ contact_name: 'Sara', phone: '+9715', peer_user_id: null }), 'Sara');
    assert.equal(callWith({ contact_name: null, phone: null, peer_user_id: null }), 'Unknown number');
    assert.equal(outcomeTone('answered'), 'success');
    assert.equal(outcomeTone('missed'), 'dangerTone');
    assert.equal(initials('Sara Ahmed'), 'SA');
    assert.equal(initials('أحمد'), 'أ');
  });
  test('an AI call needs consent, no opt-out, the role and the capability', () => {
    setLocale('en');
    const consent = { granted_at: '2026-10-01T00:00:00Z', recorded_by: 'owner', method: 'verbal' as const, note: null };
    assert.equal(aiCallBlocker({ ai_consent: consent, opted_out: false }, setup()), null);
    assert.match(aiCallBlocker({ ai_consent: null, opted_out: false }, setup()) ?? '', /consent/);
    assert.match(aiCallBlocker({ ai_consent: consent, opted_out: true }, setup()) ?? '', /opted out/);
    assert.match(aiCallBlocker({ ai_consent: consent, opted_out: false }, setup({}, false)) ?? '', /Viewers/);
    assert.equal(aiCallBlocker({ ai_consent: consent, opted_out: false }, setup({ ai_calls: false, ai_problem: 'AI calls are off.' })), 'AI calls are off.');
  });
  test('the provider body leaves empty secrets out so stored ones stay', () => {
    const body = providerBody({ account_sid: ' AC1 ', auth_token: '', api_key_secret: '  ', greeting: '', from_number: '+9714', recording_enabled: true, ai_max_seconds: 120 });
    assert.deepEqual(body, { account_sid: 'AC1', from_number: '+9714', recording_enabled: true, ai_max_seconds: 120 });
    assert.equal(providerSteps().length, 6);
  });
});

describe('catalogs', () => {
  test('Arabic has every English key with the same placeholders', () => {
    for (const [key, message] of Object.entries(callsEn)) {
      const arabic = (callsAr as Record<string, unknown>)[key];
      assert.ok(arabic, `missing Arabic for ${key}`);
      assert.deepEqual(placeholdersOf(arabic as never).sort(), placeholdersOf(message).sort(), key);
    }
    assert.equal(Object.keys(callsAr).length, Object.keys(callsEn).length);
  });
});
