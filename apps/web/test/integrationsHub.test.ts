// Unit tests for the Integrations hub tabs and figures (src/lib/integrationsHub.ts).
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import type { IntegrationState } from '../src/lib/catalog.ts';
import { setLocale } from '../src/lib/i18n.ts';
import type { SetupItem } from '../src/lib/integrationSetup.ts';
import {
  INTEGRATION_TABS, callbackRows, eventServices, expiryState, hashOf, hubFigures, logActionLabel, logOutcomeLabel, logTargetName, permissionRows, sourceLabel,
  syncRows, tabFromHash,
} from '../src/lib/integrationsHub.ts';

const item = (over: Partial<SetupItem> = {}): SetupItem => ({
  id: 'email_gmail', group: 'email', status: 'ready', connect_view: 'inbox', connections: 0, failing: 0, missing: [], redirect_urls: [], doc: 'x', checkable: false, ...over,
});
const service = (over: Partial<IntegrationState> = {}): IntegrationState => ({
  id: 'valkey', name: 'Valkey', kind: 'cache', status: 'connected', endpoint: 'redis://valkey:6379', latency_ms: 2, detail: 'ok',
  credentials: { configured: true, source: 'environment', scopes: ['cache:read'], expires_at: null }, ...over,
});

afterEach(() => setLocale('en'));

describe('tabs and hashes', () => {
  test('#integrations is Connected apps and each other tab has its own hash', () => {
    assert.deepEqual([...INTEGRATION_TABS], ['apps', 'sync', 'webhooks', 'permissions', 'logs']);
    assert.equal(tabFromHash('#integrations'), 'apps');
    assert.equal(tabFromHash('#connections'), 'apps');
    assert.equal(tabFromHash('#integrations-logs'), 'logs');
    assert.equal(tabFromHash('#integrations-nonsense'), 'apps');
    for (const tab of INTEGRATION_TABS) assert.equal(tabFromHash(hashOf(tab)), tab);
    assert.equal(hashOf('apps'), '#integrations');
  });
});

describe('hubFigures', () => {
  test('counts connected setup items and live services, and leaves switched-off services out', () => {
    const f = hubFigures(
      [item({ status: 'connected' }), item({ id: 'whatsapp', status: 'error' }), item({ id: 'commerce', status: 'ready' }), item({ id: 'push', status: 'not_configured' })],
      [service({ checked_at: '2026-11-01T10:00:00Z' }), service({ id: 'pg', status: 'degraded', checked_at: '2026-11-02T10:00:00Z' }), service({ id: 'nats', status: 'disabled' })],
    );
    assert.equal(f.connected, 2);
    assert.equal(f.inUse, 6);
    assert.equal(f.issues, 2);
    assert.equal(f.toSetUp, 2);
    assert.equal(f.healthPercent, 50);
    assert.equal(f.lastChecked, '2026-11-02T10:00:00Z');
  });
  test('nothing checked means no health figure rather than 0% or 100%', () => {
    const f = hubFigures([item()], [service({ status: 'configured' })]);
    assert.equal(f.healthPercent, null);
    assert.equal(f.lastChecked, null);
    assert.equal(f.connected, 0);
  });
});

describe('syncRows', () => {
  test('lists only items that keep accounts, failing first, then by size', () => {
    const rows = syncRows([
      item({ id: 'email_gmail', connections: 3 }), item({ id: 'whatsapp', connections: 1, failing: 1 }), item({ id: 'model', connections: null }), item({ id: 'commerce', connections: 0 }),
    ]);
    assert.deepEqual(rows.map((r) => [r.id, r.state]), [['whatsapp', 'failing'], ['email_gmail', 'ok'], ['commerce', 'none']]);
  });
});

describe('callbacks, event services and permissions', () => {
  test('one row per callback address', () => {
    const rows = callbackRows([item({ redirect_urls: ['https://a/cb', 'https://a/hook'] }), item({ id: 'model' })]);
    assert.equal(rows.length, 2);
    assert.equal(rows[1].url, 'https://a/hook');
  });
  test('event services are the webhook, event bus and workflow engine', () => {
    const ids = eventServices([service(), service({ id: 'nats', kind: 'event_bus' }), service({ id: 'external-webhook', kind: 'tool' }), service({ id: 'temporal', kind: 'workflow' })]).map((s) => s.id);
    assert.deepEqual(ids, ['nats', 'external-webhook', 'temporal']);
  });
  test('expiry reads soon within 30 days and expired once past', () => {
    const now = Date.parse('2026-11-06T00:00:00Z');
    assert.equal(expiryState(null, now), 'none');
    assert.equal(expiryState('2026-11-20T00:00:00Z', now), 'soon');
    assert.equal(expiryState('2027-02-01T00:00:00Z', now), 'ok');
    assert.equal(expiryState('2026-11-01T00:00:00Z', now), 'expired');
    assert.equal(expiryState('not a date', now), 'none');
  });
  test('permission rows say whether a workspace token is stored only where one is accepted', () => {
    const rows = permissionRows([service(), service({ id: 'hook', accepts_workspace_credential: true, workspace_credential: { integration_id: 'hook', configured: true, hint: '…ab', updated_by: 'u', updated_at: 'x' } as never })]);
    assert.equal(rows[0].workspaceToken, null);
    assert.equal(rows[1].workspaceToken, true);
    assert.equal(sourceLabel('environment', true), 'Server setting');
    assert.equal(sourceLabel('environment', false), 'None');
    assert.equal(sourceLabel('vault', true), 'vault');
  });
});

describe('log labels', () => {
  test('known actions and outcomes read in words, in both languages', () => {
    assert.equal(logActionLabel('integration.tested'), 'Connection tested');
    assert.equal(logActionLabel('integration.future'), 'integration.future');
    assert.equal(logOutcomeLabel('failed'), 'Failed');
    assert.equal(logOutcomeLabel(''), '-');
    setLocale('ar');
    assert.equal(logActionLabel('integration.tested'), 'اختُبر الاتصال');
  });
  test('the target reads as the integration name when the page knows it', () => {
    assert.equal(logTargetName('valkey', [], [service()]), 'Valkey');
    assert.equal(logTargetName('unknown', [], []), 'unknown');
    assert.equal(logTargetName('', [], []), '-');
  });
});
