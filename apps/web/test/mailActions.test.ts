// Unit tests for the Email Center phase 2 helpers (src/lib/mailActions.ts, docs/email.md#email-center).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  MAX_UIDS_PER_CALL, NO_FILTERS, SYNC_INTERVAL_MS, SYNC_MAX_INTERVAL_MS,
  adjustFolders, applyFlag, availableMoves, badgeText, chunkUids, filtersActive, flagFor, flagToggle, foldersWithNewMail, inboxUnread,
  messageListQuery, moveActionsFor, moveTarget, neighbour, nextAfterRemoval, nextSyncDelay, normalizeQuery, pruneSelection, removeUids,
  selectAllState, shortcutFor, toggleAll, toggleSelection, type FolderLike,
} from '../src/lib/mailActions.ts';

const items = [
  { uid: 9, seen: false, flagged: false },
  { uid: 7, seen: true, flagged: true },
  { uid: 5, seen: true, flagged: false },
  { uid: 3, seen: false, flagged: true },
];
const folders: FolderLike[] = [
  { id: 'INBOX', name: 'Inbox', role: 'inbox', total: 40, unread: 4 },
  { id: 'Archive', name: 'Archive', role: 'archive', total: 10, unread: 0 },
  { id: 'Junk', name: 'Junk', role: 'junk', total: 2, unread: 2 },
  { id: 'Trash', name: 'Trash', role: 'trash', total: 1, unread: 0 },
  { id: 'Projects', name: 'Projects', role: null, total: 3, unread: 1 },
];

describe('moves per folder', () => {
  test('delete for good is only offered in Trash and Junk', () => {
    for (const role of ['inbox', 'archive', 'sent', 'drafts', null]) assert.ok(!moveActionsFor(role).includes('deleteForever'), String(role));
    assert.ok(moveActionsFor('trash').includes('deleteForever'));
    assert.ok(moveActionsFor('junk').includes('deleteForever'));
  });
  test('each folder offers the way back', () => {
    assert.deepEqual(moveActionsFor('trash'), ['restore', 'deleteForever']);
    assert.deepEqual(moveActionsFor('junk'), ['notJunk', 'trash', 'deleteForever']);
    assert.equal(moveTarget('restore'), 'inbox');
    assert.equal(moveTarget('notJunk'), 'inbox');
    assert.equal(moveTarget('star'), null);
  });
  test('moves to a folder the mailbox lacks are hidden', () => {
    const noJunk = folders.filter((f) => f.role !== 'junk');
    assert.deepEqual(availableMoves('inbox', noJunk), ['archive', 'trash']);
    assert.deepEqual(availableMoves('trash', noJunk), ['restore', 'deleteForever']);
  });
});

describe('flags', () => {
  test('a toggle sets the flag when any picked message lacks it, else clears it', () => {
    assert.deepEqual(flagToggle(items, new Set([7, 3]), 'flagged'), { flag: 'flagged', value: false, uids: [7, 3] });
    assert.deepEqual(flagToggle(items, new Set([7, 5]), 'flagged'), { flag: 'flagged', value: true, uids: [7, 5] });
    assert.equal(flagToggle(items, new Set([9]), 'seen').value, true);
  });
  test('flag actions map to the API body', () => {
    assert.deepEqual(flagFor('unread'), { flag: 'seen', value: false });
    assert.deepEqual(flagFor('star'), { flag: 'flagged', value: true });
    assert.equal(flagFor('archive'), null);
  });
  test('applyFlag and removeUids change only the given messages', () => {
    const next = applyFlag(items, [9, 3], 'seen', true);
    assert.deepEqual(next.map((m) => m.seen), [true, true, true, true]);
    assert.equal(items[0].seen, false, 'the input is not changed');
    assert.deepEqual(removeUids(items, [7, 3]).map((m) => m.uid), [9, 5]);
  });
  test('folder counts move at once and never go below zero', () => {
    const moved = adjustFolders(folders, { folderId: 'INBOX', unreadDelta: -2, totalDelta: -3, targetRole: 'archive', movedUnread: 2, moved: 3 });
    assert.deepEqual(moved.find((f) => f.id === 'INBOX'), { ...folders[0], unread: 2, total: 37 });
    assert.deepEqual(moved.find((f) => f.id === 'Archive'), { ...folders[1], unread: 2, total: 13 });
    assert.equal(adjustFolders(folders, { folderId: 'Trash', unreadDelta: -5 }).find((f) => f.id === 'Trash')?.unread, 0);
  });
  test('selections are split into calls the API accepts', () => {
    const many = Array.from({ length: 450 }, (_, i) => 450 - i);
    const chunks = chunkUids([...many, 1, 2]);
    assert.deepEqual(chunks.map((c) => c.length), [MAX_UIDS_PER_CALL, MAX_UIDS_PER_CALL, 50]);
    assert.equal(chunks[0][0], 1);
  });
});

describe('selection', () => {
  test('click toggles and shift+click selects a range from the anchor', () => {
    let selected = toggleSelection(items, new Set(), 9, null, false);
    assert.deepEqual([...selected], [9]);
    selected = toggleSelection(items, selected, 5, 9, true);
    assert.deepEqual([...selected].sort(), [5, 7, 9]);
    selected = toggleSelection(items, selected, 7, 9, false);
    assert.deepEqual([...selected].sort(), [5, 9]);
  });
  test('select all picks every loaded message, then clears', () => {
    assert.equal(selectAllState(items, new Set()), 'none');
    assert.equal(selectAllState(items, new Set([9])), 'some');
    const all = toggleAll(items, new Set([9]));
    assert.equal(selectAllState(items, all), 'all');
    assert.equal(toggleAll(items, all).size, 0);
  });
  test('selections not in the list any more are dropped', () => {
    assert.deepEqual([...pruneSelection(items, new Set([9, 100]))], [9]);
  });
});

describe('search', () => {
  test('the query is cleaned the way the API cleans it', () => {
    assert.equal(normalizeQuery('  tyre\u0000  order\n'), 'tyre order');
    assert.equal(normalizeQuery('x'.repeat(500)).length, 200);
    assert.equal(filtersActive(NO_FILTERS), false);
    assert.equal(filtersActive({ q: '   ', unread: false, flagged: false }), false);
    assert.equal(filtersActive({ ...NO_FILTERS, flagged: true }), true);
  });
  test('the list query sends only what is set', () => {
    assert.equal(messageListQuery('INBOX', NO_FILTERS, null), 'folder=INBOX&limit=25');
    const params = new URLSearchParams(messageListQuery('Sent Items', { q: ' invoice ', unread: true, flagged: true }, 120, 50));
    assert.deepEqual(Object.fromEntries(params), { folder: 'Sent Items', limit: '50', q: 'invoice', unread: 'true', flagged: 'true', before: '120' });
  });
});

describe('unread counts and refresh', () => {
  test('the badge counts the inbox only and caps at 99+', () => {
    assert.equal(inboxUnread(folders), 4);
    assert.equal(badgeText(0), '');
    assert.equal(badgeText(7), '7');
    assert.equal(badgeText(120), '99+');
  });
  test('new mail is noticed outside Junk and Trash', () => {
    const after = folders.map((f) => (f.id === 'INBOX' || f.id === 'Junk' ? { ...f, unread: f.unread + 1 } : f));
    assert.deepEqual(foldersWithNewMail(folders, after), ['INBOX']);
  });
  test('the refresh backs off on failures and is capped', () => {
    assert.equal(nextSyncDelay(0), SYNC_INTERVAL_MS);
    assert.equal(nextSyncDelay(1), SYNC_INTERVAL_MS * 2);
    assert.equal(nextSyncDelay(20), SYNC_MAX_INTERVAL_MS);
  });
});

describe('keyboard', () => {
  test('single keys map to list actions, never while typing or with modifiers', () => {
    assert.equal(shortcutFor('j', {}), 'next');
    assert.equal(shortcutFor('e', {}), 'archive');
    assert.equal(shortcutFor('#', {}), 'trash');
    assert.equal(shortcutFor('e', { typing: true }), null);
    assert.equal(shortcutFor('s', { ctrl: true }), null);
    assert.equal(shortcutFor('q', {}), null);
  });
  test('j and k walk the list and stop at the ends', () => {
    assert.equal(neighbour(items, null, 1), 9);
    assert.equal(neighbour(items, 9, 1), 7);
    assert.equal(neighbour(items, 3, 1), 3);
    assert.equal(neighbour(items, 9, -1), 9);
    assert.equal(neighbour([], null, 1), null);
  });
  test('the next message opens when the open one is moved away', () => {
    assert.equal(nextAfterRemoval(items, [7], 7), 5);
    assert.equal(nextAfterRemoval(items, [3], 3), 5);
    assert.equal(nextAfterRemoval(items, [9], 7), 7);
    assert.equal(nextAfterRemoval(items, [9, 7, 5, 3], 9), null);
  });
});

describe('sidebar unread count', async () => {
  const { mailUnread, publishMailUnread, subscribeMailUnread } = await import('../src/lib/mailUnread.ts');
  test('publishing notifies only on change and never goes below zero', () => {
    let calls = 0;
    const stop = subscribeMailUnread(() => { calls += 1; });
    publishMailUnread(3); publishMailUnread(3); publishMailUnread(-4);
    assert.equal(calls, 2);
    assert.equal(mailUnread(), 0);
    stop();
    publishMailUnread(9);
    assert.equal(calls, 2);
  });
});
