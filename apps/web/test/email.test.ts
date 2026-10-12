// Unit tests for the unified inbox helpers (src/lib/email.ts, docs/email.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  EMPTY_FILTERS, accountStatus, connectErrorText, draftProvenance, formatSize, hasFilters, isStale, messagesQuery, parseRecipients,
  priorityTone, readConnectReturn, senderName, syncedLabel, type EmailAccount,
} from '../src/lib/email.ts';
import { setLocale } from '../src/lib/i18n.ts';

const account: EmailAccount = {
  id: 'emb_1', provider: 'gmail', provider_name: 'Gmail / Google Workspace', address: 'shop@example.com', display_name: null, connected_by: 'u',
  visibility: 'workspace', status: 'connected', scopes: [], last_sync_at: null, last_sync_status: null, last_error: null, syncing: false,
  message_count: 0, unread_count: 0, created_at: '2026-10-11T10:00:00Z', can_manage: true,
};

describe('messagesQuery', () => {
  test('sends only the filters that are set, trimmed and capped', () => {
    assert.equal(messagesQuery(EMPTY_FILTERS), 'limit=25');
    const query = new URLSearchParams(messagesQuery({ accountId: 'emb_1', category: 'invoice', priority: 'high', unread: true, archived: true, query: '  tyre   order ' }, 'eml_9', 10));
    assert.deepEqual(Object.fromEntries(query), { limit: '10', account_id: 'emb_1', category: 'invoice', priority: 'high', unread: 'true', archived: 'true', q: 'tyre order', before: 'eml_9' });
    assert.equal(new URLSearchParams(messagesQuery({ ...EMPTY_FILTERS, query: 'x'.repeat(500) })).get('q')?.length, 200);
  });
  test('hasFilters is false only for the empty filter set', () => {
    assert.equal(hasFilters(EMPTY_FILTERS), false);
    assert.equal(hasFilters({ ...EMPTY_FILTERS, query: '  ' }), false);
    assert.equal(hasFilters({ ...EMPTY_FILTERS, unread: true }), true);
  });
});

describe('recipients', () => {
  test('splits on commas, semicolons and spaces, lowercases and removes duplicates', () => {
    assert.deepEqual(parseRecipients('Sara@Gmail.com, <team@shop.example>; sara@gmail.com'), { ok: true, value: ['sara@gmail.com', 'team@shop.example'] });
    assert.deepEqual(parseRecipients(''), { ok: true, value: [] });
  });
  test('names the first thing that is not an address', () => {
    setLocale('en');
    const result = parseRecipients('a@b.example, not-an-address');
    assert.equal(result.ok, false);
    assert.match(result.ok ? '' : result.error, /not-an-address/);
  });
});

describe('labels', () => {
  test('draft provenance names the model, or the person who edited it', () => {
    setLocale('en');
    assert.equal(draftProvenance({ source: 'model', model_provider: 'anthropic', model_name: 'claude-sonnet-5-5' }), 'Drafted by AI (anthropic/claude-sonnet-5-5)');
    assert.equal(draftProvenance({ source: 'person', model_provider: 'anthropic', model_name: 'claude-sonnet-5-5' }), 'Edited by a person; first drafted by AI (anthropic/claude-sonnet-5-5)');
    assert.equal(draftProvenance({ source: 'person', model_provider: null, model_name: null }), 'Written by a person');
  });
  test('account status explains a mailbox that needs reconnecting', () => {
    setLocale('en');
    assert.equal(accountStatus(account).tone, 'success');
    const reauth = accountStatus({ ...account, status: 'reauth_required', last_error: null });
    assert.equal(reauth.tone, 'dangerTone');
    assert.match(reauth.detail ?? '', /reconnects/);
    assert.equal(accountStatus({ ...account, last_sync_status: 'error', last_error: 'Gmail answered 500' }).detail, 'Gmail answered 500');
  });
  test('sync age, sizes, priorities and senders', () => {
    setLocale('en');
    const now = new Date('2026-10-11T12:00:00Z');
    assert.equal(syncedLabel(null, now), 'never synced');
    assert.equal(syncedLabel('2026-10-11T11:55:00Z', now), 'synced 5 min ago');
    assert.equal(syncedLabel('2026-10-11T09:00:00Z', now), 'synced 3 hours ago');
    assert.equal(formatSize(512), '512 B');
    assert.equal(formatSize(12_288), '12 KB');
    assert.equal(formatSize(3.5 * 1024 * 1024), '3.5 MB');
    assert.equal(priorityTone('high'), 'dangerTone');
    assert.equal(priorityTone('low'), 'neutral');
    assert.equal(senderName({ from_name: ' ', from_address: 'a@b.example' }), 'a@b.example');
  });
  test('Arabic plural forms for the sync age', () => {
    setLocale('ar');
    const now = new Date('2026-10-11T12:00:00Z');
    assert.equal(syncedLabel('2026-10-11T11:58:00Z', now), 'تمت المزامنة قبل دقيقتين');
    setLocale('en');
  });
});

describe('stale accounts', () => {
  test('syncs on open when older than two minutes, never while reauth is needed or a sync runs', () => {
    const now = new Date('2026-10-11T12:00:00Z');
    assert.equal(isStale(account, now), true);
    assert.equal(isStale({ ...account, last_sync_at: '2026-10-11T11:59:00Z' }, now), false);
    assert.equal(isStale({ ...account, last_sync_at: '2026-10-11T11:00:00Z' }, now), true);
    assert.equal(isStale({ ...account, status: 'reauth_required' }, now), false);
    assert.equal(isStale({ ...account, syncing: true }, now), false);
  });
});

describe('connect return', () => {
  test('reads the connection id and removes it from the address', () => {
    assert.deepEqual(readConnectReturn('https://app.example.com/?email_connect=eoc_1#inbox'), { connection: 'eoc_1', error: null, cleanUrl: '/#inbox' });
    assert.deepEqual(readConnectReturn('https://app.example.com/?x=1&email_connect_error=invalid_state#inbox'), { connection: null, error: 'invalid_state', cleanUrl: '/?x=1#inbox' });
    assert.equal(readConnectReturn('https://app.example.com/#tasks'), null);
  });
  test('explains each error code', () => {
    setLocale('en');
    assert.match(connectErrorText('invalid_state'), /expired/);
    assert.match(connectErrorText('unknown_request'), /already finished/);
    assert.match(connectErrorText('weird'), /\(weird\)/);
  });
});
