// Unit tests for Integrations > Connection setup (src/lib/integrationSetup.ts).
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { CATALOGS, setLocale } from '../src/lib/i18n.ts';
import {
  GROUP_ORDER, RUNBOOK_URL, checkSentence, docUrl, groupName, itemCost, itemName, itemText, statusBadge, statusSentence, type SetupItem,
} from '../src/lib/integrationSetup.ts';

const item = (over: Partial<SetupItem> = {}): SetupItem => ({
  id: 'email_gmail', group: 'email', status: 'ready', connect_view: 'inbox', connections: 0, failing: 0, missing: [], redirect_urls: [],
  doc: '5-email-gmail-outlook-and-zoho', checkable: false, ...over,
});
// The ids the API lists (services/api/anum_api/integration_setup.py ENTRIES).
const API_IDS = [
  'model', 'speech', 'transcription', 'web_search', 'email_gmail', 'email_outlook', 'email_zoho', 'whatsapp', 'meetings_zoom', 'meetings_teams',
  'push', 'notification_email', 'sign_in_google', 'sign_in_apple', 'error_reporting', 'files', 'external_webhook', 'marketing_meta',
  'marketing_linkedin', 'marketing_x', 'marketing_tiktok', 'marketing_google', 'commerce', 'fleet_data', 'developer',
];

afterEach(() => setLocale('en'));

describe('connection setup wording', () => {
  test('every item the API lists has a name, a description and a cost in both languages', () => {
    for (const locale of ['en', 'ar'] as const) {
      setLocale(locale);
      for (const id of API_IDS) {
        assert.notEqual(itemName(id), id, `${locale} ${id} name`);
        assert.ok(itemText(id).length > 10, `${locale} ${id} text`);
        assert.ok(itemCost(id).length > 3, `${locale} ${id} cost`);
      }
      for (const group of GROUP_ORDER) assert.notEqual(groupName(group), group, `${locale} group ${group}`);
    }
    assert.equal(Object.keys(CATALOGS.ar).length, Object.keys(CATALOGS.en).length);
  });

  test('an unknown item or group from a newer API falls back to its id', () => {
    assert.equal(itemName('telegram'), 'telegram');
    assert.equal(itemText('telegram'), '');
    assert.equal(groupName('chat'), 'chat');
  });

  test('status badges and sentences say what to do next', () => {
    assert.deepEqual(statusBadge('connected'), { label: 'Connected', tone: 'success' });
    assert.deepEqual(statusBadge('not_configured'), { label: 'Not set up', tone: 'warning' });
    assert.equal(statusBadge('error').tone, 'dangerTone');
    assert.equal(statusSentence(item({ status: 'connected', connections: 2 }), true), 'Working: 2 accounts connected in this workspace.');
    assert.equal(statusSentence(item({ status: 'connected', connections: null }), true), 'Working.');
    assert.match(statusSentence(item(), false), /Connect an account/);
    assert.equal(statusSentence(item({ status: 'ready', connect_view: null }), false), 'Set up on the server.');
    assert.match(statusSentence(item({ status: 'error', failing: 1, connections: 1 }), false), /One connected account is failing/);
    assert.match(statusSentence(item({ status: 'not_configured', missing: ['ANUM_EMAIL_GOOGLE_CLIENT_ID'] }), true), /add the settings below/);
    assert.match(statusSentence(item({ status: 'not_configured' }), false), /administrator connects it/);
    setLocale('ar');
    assert.equal(statusBadge('connected').label, 'متصل');
  });

  test('check results name the error class, never a host', () => {
    assert.equal(checkSentence({ id: 'x', reachable: true, latency_ms: 42, detail: 'Reachable', checked_at: '2026-10-12T10:00:00Z' }, 'Gmail'), 'Gmail answered in 42 ms.');
    assert.match(checkSentence({ id: 'x', reachable: false, latency_ms: null, detail: 'TimeoutError', checked_at: '2026-10-12T10:00:00Z' }, 'Gmail'), /did not answer \(TimeoutError\)/);
  });

  test('each item links to its numbered section of the runbook', () => {
    assert.equal(docUrl(item()), `${RUNBOOK_URL}#5-email-gmail-outlook-and-zoho`);
  });
});
