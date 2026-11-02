// One Email module (docs/email.md#one-email-module): the pure model of the Email page over both back ends.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  appendUnique, backendNotes, findMailbox, landingPane, needsAttention, paneFromHash, paneHash, senders, sourceKey, totalUnread, unifiedQuery,
  type UnifiedMailbox, type UnifiedMailboxes, type UnifiedMessage,
} from '../src/lib/emailHub.ts';
import { NAV, TABS_OF, navParent, viewFromHash, visibleViews } from '../src/lib/shell.ts';

const box = (ref: string, extra: Partial<UnifiedMailbox> = {}): UnifiedMailbox => ({
  ref, id: ref.split(':')[1]!, backend: ref.startsWith('provider') ? 'provider' : 'server', source: ref.startsWith('provider') ? 'gmail' : 'mailserver',
  address: `${ref.split(':')[1]}@example.com`, display_name: null, status: 'connected', access: 'owner', unread: 2, can_send: true, ...extra,
});
const found = (items: UnifiedMailbox[], extra: Partial<UnifiedMailboxes> = {}): UnifiedMailboxes => ({
  items, provider: { state: 'ok', detail: null }, server: { state: 'ok', detail: null }, can_act: true, can_manage: true, ...extra,
});
const msg = (ref: string): UnifiedMessage => ({
  ref, backend: 'provider', source: 'gmail', mailbox_id: 'a', mailbox_address: 'a@example.com', message_id: ref, folder: null, uid: null, from_name: null,
  from_address: 'x@example.com', subject: ref, snippet: '', received_at: '2026-10-01T08:00:00Z', unread: false, flagged: false, has_attachments: false,
  read_only: false, server_message: null,
});

describe('one Email entry in the navigation', () => {
  test('Inbox is no longer its own sidebar entry; its link opens inside Email', () => {
    const sidebar = NAV.flatMap((group) => group.views);
    assert.ok(sidebar.includes('email'));
    assert.ok(!sidebar.includes('inbox'));
    assert.equal(TABS_OF.inbox, 'email');
    assert.equal(navParent('inbox'), 'email');
    assert.ok(!visibleViews(true).includes('inbox'));
  });
  test('old and new links open the Email page', () => {
    assert.equal(viewFromHash('#inbox'), 'inbox');
    assert.equal(viewFromHash('#email'), 'email');
    assert.equal(viewFromHash('#email/all'), 'email');
    assert.equal(viewFromHash('#email/mailbox/server%3Ambx_1'), 'email');
    assert.equal(viewFromHash('#whatsapp/conv_1'), 'whatsapp');
  });
});

describe('panes', () => {
  test('a link names the pane; unknown or unsafe ones land by the mailboxes', () => {
    assert.deepEqual(paneFromHash('#inbox'), { kind: 'provider' });
    assert.deepEqual(paneFromHash('#email/all'), { kind: 'all' });
    assert.deepEqual(paneFromHash('#email/connections'), { kind: 'connections' });
    assert.deepEqual(paneFromHash('#email/server'), { kind: 'server' });
    assert.deepEqual(paneFromHash('#email/mailbox/provider%3Aacct_1'), { kind: 'mailbox', ref: 'provider:acct_1' });
    assert.equal(paneFromHash('#email/mailbox/other%3Ax'), null);
    assert.equal(paneFromHash('#email/mailbox/%E0%A4%A'), null);
    assert.equal(paneFromHash('#email'), null);
    for (const pane of [{ kind: 'all' }, { kind: 'connections' }, { kind: 'server' }, { kind: 'provider' }, { kind: 'mailbox', ref: 'server:m_1' }] as const) {
      assert.deepEqual(paneFromHash(paneHash(pane)), pane);
    }
  });
  test('landing: All inboxes from two mailboxes on, the one mailbox, else how to connect', () => {
    assert.deepEqual(landingPane(found([box('provider:a'), box('server:b')])), { kind: 'all' });
    assert.deepEqual(landingPane(found([box('server:b')])), { kind: 'mailbox', ref: 'server:b' });
    assert.deepEqual(landingPane(found([])), { kind: 'server' });
    assert.deepEqual(landingPane(found([], { server: { state: 'off', detail: 'off' } })), { kind: 'connections' });
    assert.deepEqual(landingPane(found([], { can_act: false })), { kind: 'connections' });
    assert.deepEqual(landingPane(null), { kind: 'server' });
  });
});

describe('mailboxes and messages', () => {
  test('senders, unread total, attention and the source badge', () => {
    const list = found([box('provider:a'), box('server:b', { can_send: false, access: 'read', unread: null }), box('server:c', { status: 'auth_failed' })]);
    assert.deepEqual(senders(list).map((m) => m.ref), ['provider:a', 'server:c']);
    assert.equal(totalUnread(list), 4);
    assert.equal(findMailbox(list, 'server:b')?.access, 'read');
    assert.equal(findMailbox(list, 'server:zz'), null);
    assert.ok(needsAttention(list.items[2]!) && !needsAttention(list.items[0]!));
    assert.equal(sourceKey('mailserver'), 'emailHub.source.mailserver');
    assert.deepEqual(senders(null), []);
  });
  test('load more never lists a message twice; the query carries search, unread and the cursor', () => {
    assert.deepEqual(appendUnique([msg('p:1'), msg('p:2')], [msg('p:2'), msg('p:3')]).map((m) => m.ref), ['p:1', 'p:2', 'p:3']);
    assert.equal(unifiedQuery({ q: ' sara ', unread: true, cursor: 'abc', limit: 10 }), 'q=sara&unread=true&cursor=abc&limit=10');
    assert.equal(unifiedQuery({}), 'limit=20');
  });
  test('a back end that is off or failed is named; one that is fine or empty is not', () => {
    const notes = backendNotes(found([], { provider: { state: 'none', detail: null }, server: { state: 'not_configured', detail: 'Set ANUM_MAILSERVER_IMAP_HOST' } }));
    assert.deepEqual(notes, [{ backend: 'server', info: { state: 'not_configured', detail: 'Set ANUM_MAILSERVER_IMAP_HOST' } }]);
    assert.deepEqual(backendNotes(null), []);
  });
});
