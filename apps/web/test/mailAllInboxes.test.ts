// Unit tests for All inboxes (src/lib/mailAllInboxes.ts, docs/email.md#all-inboxes).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  ALL_INBOXES, NO_ALL_FILTERS, accountOf, allFiltersActive, allInboxesQuery, appendPage, badDateRange, inboxFolder, mailboxBadge, messageKey, setFlag, unreadable,
  type AllInboxMessage, type AllInboxPage,
} from '../src/lib/mailAllInboxes.ts';
import { mailAllEn } from '../src/lib/messages/mailAll.en.ts';
import { mailAllAr } from '../src/lib/messages/mailAll.ar.ts';

const message = (accountId: string, uid: number, extra: Partial<AllInboxMessage> = {}): AllInboxMessage => ({
  uid, folder: 'INBOX', subject: `Subject ${uid}`, sender: { name: 'Sara', address: 'sara@example.com' }, to: [], cc: [], date: '2026-10-14T09:00:00Z', message_id: null,
  seen: false, flagged: false, answered: false, size: 100, maybe_attachments: false, account_id: accountId, account_address: `${accountId}@example.com`, account_name: null, access: 'owner',
  ...extra,
});

describe('the query the API takes', () => {
  test('only what is set is sent, with the page size', () => {
    assert.equal(allInboxesQuery(NO_ALL_FILTERS), 'limit=25');
    const query = new URLSearchParams(allInboxesQuery({ q: '  tyre\u0007 order  ', unread: true, flagged: true, accountId: 'mailbox_b', since: '2026-10-01', until: '2026-10-14' }, 'abc', 10));
    assert.equal(query.get('q'), 'tyre order');
    assert.equal(query.get('unread'), 'true');
    assert.equal(query.get('flagged'), 'true');
    assert.equal(query.get('account_id'), 'mailbox_b');
    assert.equal(query.get('since'), '2026-10-01');
    assert.equal(query.get('until'), '2026-10-14');
    assert.equal(query.get('cursor'), 'abc');
    assert.equal(query.get('limit'), '10');
  });
  test('a half-typed date is not sent', () => {
    assert.equal(new URLSearchParams(allInboxesQuery({ ...NO_ALL_FILTERS, since: '2026-1' })).has('since'), false);
  });
  test('filters are active when any is set; a reversed range is caught before asking', () => {
    assert.equal(allFiltersActive(NO_ALL_FILTERS), false);
    assert.equal(allFiltersActive({ ...NO_ALL_FILTERS, accountId: 'x' }), true);
    assert.equal(allFiltersActive({ ...NO_ALL_FILTERS, until: '2026-10-01' }), true);
    assert.equal(badDateRange({ since: '2026-10-14', until: '2026-10-01' }), true);
    assert.equal(badDateRange({ since: '2026-10-01', until: '2026-10-01' }), false);
    assert.equal(badDateRange({ since: '2026-10-14', until: '' }), false);
  });
});

describe('the list', () => {
  test('the same UID in two mailboxes is two messages; a next page never repeats one', () => {
    const first = [message('a', 5), message('b', 5)];
    assert.notEqual(messageKey(first[0]), messageKey(first[1]));
    const next = appendPage(first, [message('b', 5), message('a', 4)]);
    assert.deepEqual(next.map(messageKey), ['a:5', 'b:5', 'a:4']);
  });
  test('the badge names the mailbox, else its address', () => {
    assert.equal(mailboxBadge(message('a', 1)), 'a@example.com');
    assert.equal(mailboxBadge(message('a', 1, { account_name: '  Sales ' })), 'Sales');
  });
  test('mailboxes that could not be read are listed; done and ok are not', () => {
    const page: Pick<AllInboxPage, 'mailboxes'> = { mailboxes: [
      { account_id: 'a', address: 'a@example.com', display_name: null, access: 'owner', state: 'ok', error: null, total: 3 },
      { account_id: 'b', address: 'b@example.com', display_name: null, access: 'read', state: 'offline', error: 'Server offline.', total: null },
      { account_id: 'c', address: 'c@example.com', display_name: null, access: 'send', state: 'done', error: null, total: 0 },
    ] };
    assert.deepEqual(unreadable(page).map((m) => m.account_id), ['b']);
  });
  test('a flag changes only that message', () => {
    const items = [message('a', 1), message('b', 1)];
    const next = setFlag(items, 'b:1', 'flagged', true);
    assert.equal(next[0].flagged, false);
    assert.equal(next[1].flagged, true);
  });
  test('a message opens in its own mailbox, in its inbox', () => {
    const accounts = [{ id: 'a', address: 'a@example.com', display_name: null, status: 'connected' as const, last_error: null, last_checked_at: null, created_at: '' }];
    assert.equal(accountOf(accounts, message('a', 1))?.address, 'a@example.com');
    assert.equal(accountOf(accounts, message('gone', 1)), null);
    assert.equal(inboxFolder().id, 'INBOX');
    assert.equal(inboxFolder().role, 'inbox');
    assert.equal(ALL_INBOXES.startsWith('mailbox_'), false);
  });
});

describe('strings', () => {
  test('Arabic has every English key', () => {
    assert.deepEqual(Object.keys(mailAllAr).sort(), Object.keys(mailAllEn).sort());
  });
});
