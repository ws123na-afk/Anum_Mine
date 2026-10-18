// Unit tests for the platform operator's mail health (src/lib/mailOps.ts, docs/email.md#operator-view).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { mailHealthMatches, mailHealthTone, notConnected, rateLimited, sortMailHealth, type OrganisationMailHealth } from '../src/lib/mailOps.ts';

const counts = { sent: 3, send_failed: 0, send_unknown: 0, send_rate_limited: 2, connect_rate_limited: 1, auth_failed: 0, server_offline: 0 };
const row = (name: string, health: OrganisationMailHealth['health']): OrganisationMailHealth => ({
  tenant_id: name.toLowerCase().replace(/ /g, '_'), display_name: name, mailboxes: 2, connected: 1, auth_failed: 1, offline: 0, error: 1, stale: 0,
  last_sync_at: null, oldest_sync_at: null, counts, health,
});

describe('mail health helpers', () => {
  test('adds both rate limits and every mailbox that is not connected', () => {
    assert.equal(rateLimited(counts), 3);
    assert.equal(notConnected(row('Acme', 'ok')), 2);
  });
  test('organisations needing attention come first, then by name', () => {
    const sorted = sortMailHealth([row('Zed', 'ok'), row('Beta', 'quiet'), row('Acme', 'ok'), row('Gulf', 'attention')]);
    assert.deepEqual(sorted.map((r) => r.display_name), ['Gulf', 'Acme', 'Zed', 'Beta']);
  });
  test('search matches the name or the id, any case', () => {
    assert.equal(mailHealthMatches(row('Gulf Tyres', 'ok'), 'gulf_t'), true);
    assert.equal(mailHealthMatches(row('Gulf Tyres', 'ok'), 'TYRES'), true);
    assert.equal(mailHealthMatches(row('Gulf Tyres', 'ok'), 'acme'), false);
    assert.equal(mailHealthMatches(row('Gulf Tyres', 'ok'), '  '), true);
  });
  test('tones follow the shared status badges', () => {
    assert.equal(mailHealthTone('attention'), 'dangerTone');
    assert.equal(mailHealthTone('ok'), 'success');
    assert.equal(mailHealthTone('quiet'), 'neutral');
  });
});
