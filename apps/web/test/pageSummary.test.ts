// Unit tests for the shared page layout helpers in src/lib/pageSummary.ts (docs/design/anum360.md#page-layout).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  actionItemCounts, chatContext, figure, fillHeight, filterFiles, firstUnread, groupBy, indexCounts, localDay, memoriesFromChat, waitingDrafts,
} from '../src/lib/pageSummary.ts';

describe('figure', () => {
  test('a loading or failed load never shows a number', () => {
    assert.deepEqual(figure('a', 'Files', { state: 'loading' }, () => ({ value: '0' })), { id: 'a', label: 'Files', state: 'loading' });
    assert.deepEqual(figure('a', 'Files', { state: 'error' }, () => ({ value: '0' })), { id: 'a', label: 'Files', state: 'error' });
  });
  test('a ready load takes its value, hint and tone', () => {
    assert.deepEqual(figure('a', 'Files', { state: 'ready' }, () => ({ value: '3', hint: '1 failed', tone: 'danger' })), { id: 'a', label: 'Files', state: 'ready', value: '3', hint: '1 failed', tone: 'danger' });
  });
});

describe('fillHeight', () => {
  test('fills the window below the top edge, less the gutter', () => assert.equal(fillHeight(900, 300), 576));
  test('never goes below the minimum', () => assert.equal(fillHeight(700, 500, { min: 480 }), 480));
  test('a bad measurement falls back to the minimum', () => assert.equal(fillHeight(Number.NaN, 10, { min: 400 }), 400));
});

describe('firstUnread', () => {
  const rows = [{ id: 'a', unread: false }, { id: 'b', unread: true }, { id: 'c', unread: true }];
  test('picks the first unread', () => assert.equal(firstUnread(rows, (r) => r.unread)?.id, 'b'));
  test('falls back to the first row', () => assert.equal(firstUnread(rows.map((r) => ({ ...r, unread: false })), (r) => r.unread)?.id, 'a'));
  test('nothing for an empty list', () => assert.equal(firstUnread([], () => true), null));
});

describe('indexCounts', () => {
  const sources = [
    { source_type: 'file', status: 'indexed' }, { source_type: 'file', status: 'failed' }, { source_type: 'file', status: 'skipped' },
    { source_type: 'memory', status: 'indexed' }, { source_type: 'memory', status: 'indexed' },
  ];
  test('counts one source type only', () => {
    assert.deepEqual(indexCounts(sources, 'file'), { indexed: 1, failed: 1, skipped: 1, total: 3 });
    assert.deepEqual(indexCounts(sources, 'memory'), { indexed: 2, failed: 0, skipped: 0, total: 2 });
    assert.deepEqual(indexCounts([], 'file'), { indexed: 0, failed: 0, skipped: 0, total: 0 });
    assert.deepEqual(indexCounts(undefined, 'file'), { indexed: 0, failed: 0, skipped: 0, total: 0 });
  });
});

describe('filterFiles', () => {
  const files = [
    { id: '1', kind: 'pdf', index: { status: 'indexed' } }, { id: '2', kind: 'image', index: { status: 'skipped' } },
    { id: '3', kind: 'pdf', index: { status: 'failed' } }, { id: '4', kind: 'text', index: null },
  ];
  const ids = (rows: { id: string }[]) => rows.map((r) => r.id);
  test('no filter keeps everything', () => assert.deepEqual(ids(filterFiles(files, { type: '', index: '' })), ['1', '2', '3', '4']));
  test('by type', () => assert.deepEqual(ids(filterFiles(files, { type: 'pdf', index: '' })), ['1', '3']));
  test('by index state; a file without an index entry is pending', () => {
    assert.deepEqual(ids(filterFiles(files, { type: '', index: 'failed' })), ['3']);
    assert.deepEqual(ids(filterFiles(files, { type: '', index: 'pending' })), ['4']);
  });
  test('both together', () => assert.deepEqual(ids(filterFiles(files, { type: 'pdf', index: 'indexed' })), ['1']));
});

describe('groupBy', () => {
  test('fixed order and no empty groups', () => {
    const groups = groupBy([{ g: 'task' }, { g: 'chat' }, { g: 'task' }] as const, (x) => x.g);
    assert.deepEqual(groups.map((g) => [g.group, g.items.length]), [['chat', 1], ['task', 2]]);
  });
});

describe('actionItemCounts', () => {
  test('open, overdue and done across meetings', () => {
    const meetings = [
      { action_items: [{ done: false, due_date: '2026-10-01' }, { done: false, due_date: '2026-10-20' }, { done: true, due_date: '2026-09-01' }] },
      { action_items: [{ done: false, due_date: null }] },
      { action_items: [] },
    ];
    assert.deepEqual(actionItemCounts(meetings, '2026-10-12'), { open: 3, overdue: 1, done: 1 });
  });
  test('due today is not overdue', () => assert.deepEqual(actionItemCounts([{ action_items: [{ done: false, due_date: '2026-10-12' }] }], '2026-10-12'), { open: 1, overdue: 0, done: 0 }));
  test('localDay pads months and days', () => assert.equal(localDay(new Date(2026, 0, 5, 12)), '2026-01-05'));
});

describe('waitingDrafts', () => {
  test('keeps drafts a person still has to act on', () => {
    const drafts = [{ id: 'a', status: 'draft' }, { id: 'b', status: 'sent' }, { id: 'c', status: 'pending_approval' }, { id: 'd', status: 'failed' }, { id: 'e', status: 'discarded' }];
    assert.deepEqual(waitingDrafts(drafts).map((d) => d.id), ['a', 'c', 'd']);
  });
});

describe('chatContext', () => {
  const card = (extra: Record<string, unknown>) => ({ state: 'results', category: 'files', summary: 'Found 2 files', items: [], total: 0, ...extra }) as never;
  test('counts turns and collects sources, tasks and approvals once each', () => {
    const turns = [
      { id: 'u1', role: 'user' as const },
      { id: 'a1', role: 'assistant' as const, card: card({ task_id: 'task_1', items: [{ kind: 'file', id: 'f1', title: 'notes.md' }, { kind: 'file', id: 'f1', title: 'notes.md' }, { kind: 'web', id: null, title: 'Site', url: 'https://example.com' }] }) },
      { id: 'u2', role: 'user' as const },
      { id: 'a2', role: 'assistant' as const, card: card({ state: 'approval', task_id: 'task_2', summary: 'Send the email', approval: { id: 'ap1', action: 'send_email', risk_level: 'high', status: 'pending', reason: '', can_decide: true } }) },
      { id: 'a3', role: 'assistant' as const, card: null },
    ];
    const context = chatContext(turns);
    assert.equal(context.questions, 2);
    assert.equal(context.answers, 3);
    assert.deepEqual(context.sources.map((s) => `${s.kind}:${s.id ?? s.url}`), ['file:f1', 'web:https://example.com']);
    assert.deepEqual(context.tasks.map((t) => [t.taskId, t.state]), [['task_1', 'results'], ['task_2', 'approval']]);
    assert.deepEqual(context.approvals, [{ id: 'ap1', action: 'send_email', status: 'pending', risk: 'high', summary: 'Send the email' }]);
  });
  test('an empty conversation has nothing', () => assert.deepEqual(chatContext([]), { questions: 0, answers: 0, sources: [], tasks: [], approvals: [] }));
});

describe('memoriesFromChat', () => {
  const memory = (id: string, type: string, source: string | null, at: string) => ({ id, content: id, created_at: at, provenance: { source_type: type, source_id: source } });
  test('only this conversation\'s memories, newest first', () => {
    const memories = [memory('a', 'conversation', 'conv_1', '2026-10-01'), memory('b', 'conversation', 'conv_2', '2026-10-02'), memory('c', 'user', 'conv_1', '2026-10-03'), memory('d', 'conversation', 'conv_1', '2026-10-04')];
    assert.deepEqual(memoriesFromChat(memories, 'conv_1').map((m) => m.id), ['d', 'a']);
    assert.deepEqual(memoriesFromChat(memories, null), []);
  });
});
