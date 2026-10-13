// Unit tests for the Integrations page layout (src/lib/integrationsPage.ts).
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { IntegrationState } from '../src/lib/catalog.ts';
import { setLocale } from '../src/lib/i18n.ts';
import type { SetupItem } from '../src/lib/integrationSetup.ts';
import {
  CATEGORY_ORDER, actionLabel, actionName, buildSections, categoryName, matchesQuery, pageCounts, sectionStartsOpen, sectionSummary,
  serviceAction, serviceBucket, serviceEntry, servicePurpose, setupAction, setupBucket, setupCategory, setupEntry,
} from '../src/lib/integrationsPage.ts';

const item = (over: Partial<SetupItem> = {}): SetupItem => ({
  id: 'email_gmail', group: 'email', status: 'ready', connect_view: 'inbox', connections: 0, failing: 0, missing: [], redirect_urls: [],
  doc: '5-email-gmail-outlook-and-zoho', checkable: false, ...over,
});
const service = (over: Partial<IntegrationState> = {}): IntegrationState => ({
  id: 'valkey', name: 'Valkey', kind: 'cache', status: 'connected', endpoint: 'redis://valkey:6379', latency_ms: 2, detail: 'Health check passed.',
  credentials: { configured: true, source: 'environment', scopes: [], expires_at: null }, environment_variable: 'ANUM_VALKEY_URL', ...over,
});
// The ids and groups the API lists (services/api/anum_api/integration_setup.py ENTRIES).
const API_ITEMS: [string, string][] = [
  ['model', 'ai'], ['speech', 'ai'], ['transcription', 'ai'], ['web_search', 'ai'], ['email_gmail', 'email'], ['email_outlook', 'email'], ['email_zoho', 'email'],
  ['whatsapp', 'messaging'], ['meetings_zoom', 'meetings'], ['meetings_teams', 'meetings'], ['push', 'notifications'], ['notification_email', 'notifications'],
  ['sign_in_google', 'sign_in'], ['sign_in_apple', 'sign_in'], ['error_reporting', 'monitoring'], ['files', 'storage'], ['external_webhook', 'automation'],
  ['marketing_meta', 'marketing'], ['marketing_linkedin', 'marketing'], ['marketing_x', 'marketing'], ['marketing_tiktok', 'marketing'], ['marketing_google', 'marketing'],
  ['commerce', 'commerce'], ['fleet_data', 'operations'], ['developer', 'developer'],
];

afterEach(() => setLocale('en'));

describe('categories', () => {
  test('every API item lands in a named category, never Other', () => {
    for (const [id, group] of API_ITEMS) assert.notEqual(setupCategory({ id, group }), 'other', id);
    assert.equal(setupCategory({ id: 'speech', group: 'ai' }), 'calls');
    assert.equal(setupCategory({ id: 'whatsapp', group: 'messaging' }), 'messaging');
    assert.equal(setupCategory({ id: 'external_webhook', group: 'automation' }), 'developer');
    assert.equal(setupCategory({ id: 'telegram', group: 'chat' }), 'other');
  });

  test('every category and service kind has a name in both languages', () => {
    for (const locale of ['en', 'ar'] as const) {
      setLocale(locale);
      for (const category of CATEGORY_ORDER) assert.doesNotMatch(categoryName(category), /^setup\./, `${locale} ${category}`);
      for (const kind of ['database', 'identity', 'event_bus', 'workflow', 'cache', 'object_storage', 'search', 'tool', 'email']) {
        assert.ok(servicePurpose({ kind, detail: 'x' }).length > 10, `${locale} ${kind}`);
      }
    }
    assert.equal(servicePurpose({ kind: 'quantum', detail: 'Health check passed.' }), 'Health check passed.');
  });
});

describe('buckets and actions', () => {
  test('setup and service statuses map to the summary buckets', () => {
    assert.equal(setupBucket('connected'), 'connected');
    assert.equal(setupBucket('ready'), 'setup');
    assert.equal(setupBucket('not_configured'), 'setup');
    assert.equal(setupBucket('error'), 'error');
    assert.equal(serviceBucket('connected'), 'connected');
    assert.equal(serviceBucket('degraded'), 'error');
    assert.equal(serviceBucket('disabled'), 'off');
  });

  test('one primary action per row, by status and role', () => {
    assert.equal(setupAction(item({ status: 'error' }), true), 'fix');
    assert.equal(setupAction(item({ status: 'not_configured' }), true), 'setUp');
    assert.equal(setupAction(item({ status: 'ready' }), false), 'connect');
    assert.equal(setupAction(item({ status: 'connected' }), true), 'manage');
    assert.equal(setupAction(item({ status: 'connected', connect_view: null }), false), 'details');
    assert.equal(setupAction(item({ status: 'not_configured' }), false), 'details');
    assert.equal(setupAction(item({ status: 'error' }), false), 'details');
    assert.equal(serviceAction(service({ status: 'degraded' }), true), 'fix');
    assert.equal(serviceAction(service(), true), 'manage');
    assert.equal(serviceAction(service({ status: 'degraded' }), false), 'details');
    assert.equal(actionLabel('setUp'), 'Set up');
    assert.equal(actionName('fix', 'WhatsApp Business'), 'Fix WhatsApp Business');
    setLocale('ar');
    assert.equal(actionLabel('manage'), 'إدارة');
  });
});

describe('sections', () => {
  const entries = [
    setupEntry(item({ id: 'model', group: 'ai', status: 'connected', connections: null })),
    setupEntry(item({ id: 'web_search', group: 'ai', status: 'connected' })),
    setupEntry(item({ id: 'email_gmail', status: 'not_configured', missing: ['ANUM_EMAIL_GOOGLE_CLIENT_ID'] })),
    setupEntry(item({ id: 'email_zoho', status: 'ready' })),
    setupEntry(item({ id: 'whatsapp', group: 'messaging', status: 'error', failing: 1, connections: 1 })),
    serviceEntry(service()),
    serviceEntry(service({ id: 'nats', name: 'NATS JetStream', kind: 'event_bus', status: 'disabled', environment_variable: 'ANUM_NATS_URL' })),
  ];

  test('counts every bucket across both lists', () => {
    assert.deepEqual(pageCounts(entries), { connected: 3, setup: 2, error: 1, off: 1, total: 7 });
  });

  test('groups in page order, problems first, and services last', () => {
    const sections = buildSections(entries, 'all', '');
    assert.deepEqual(sections.map((s) => s.category), ['messaging', 'ai', 'services']);
    assert.deepEqual(sections[0].entries.map((e) => e.value.id), ['whatsapp', 'email_gmail', 'email_zoho']);
    assert.equal(sections[0].needsAction, true);
    assert.equal(sections[1].needsAction, false);
    // A service that is simply not used is not something to act on.
    assert.equal(sections[2].needsAction, false);
  });

  test('filters and searches names, categories and setting names', () => {
    assert.deepEqual(buildSections(entries, 'error', '').flatMap((s) => s.entries.map((e) => e.value.id)), ['whatsapp']);
    assert.deepEqual(buildSections(entries, 'setup', '').flatMap((s) => s.entries.map((e) => e.value.id)), ['email_gmail', 'email_zoho']);
    assert.equal(buildSections(entries, 'connected', '').flatMap((s) => s.entries).length, 3);
    assert.deepEqual(buildSections(entries, 'all', 'google_client').flatMap((s) => s.entries.map((e) => e.value.id)), ['email_gmail']);
    assert.deepEqual(buildSections(entries, 'all', 'valkey_url').flatMap((s) => s.entries.map((e) => e.value.id)), ['valkey']);
    assert.equal(matchesQuery(entries[0], 'ai search'), true);
    assert.deepEqual(buildSections(entries, 'all', 'nothing like this'), []);
  });

  test('sections where everything works start collapsed unless the page is narrowed', () => {
    const [messaging, ai] = buildSections(entries, 'all', '');
    assert.equal(sectionStartsOpen(messaging, false), true);
    assert.equal(sectionStartsOpen(ai, false), false);
    assert.equal(sectionStartsOpen(ai, true), true);
  });

  test('section header counts', () => {
    const [messaging, ai] = buildSections(entries, 'all', '');
    assert.equal(sectionSummary(messaging.counts), '3 · 1 error · 2 need setup');
    assert.equal(sectionSummary(ai.counts), '2 · all connected');
  });
});
