// Unit tests for message actions and the conversation list (docs/conversations.md#message-actions)
// in src/lib/chat.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  canRegenerate, conversationMarkdown, deletionSpan, exportFileName, fromStored, isStored, laterTurns, nextCursor, orderConversations, PENDING_ASSISTANT,
  startAsk, type ChatTurn, type Conversation, type UiTurn,
} from '../src/lib/chat.ts';

const turn = (id: string, role: ChatTurn['role'], content: string, extra: Partial<ChatTurn> = {}): ChatTurn => ({
  id, conversation_id: 'c', role, content, status: 'complete', created_at: '2026-10-12T09:00:00Z', ...extra,
});
const history: UiTurn[] = fromStored([
  turn('u1', 'user', 'First'), turn('a1', 'assistant', 'One', { feedback: 'up' }),
  turn('u2', 'user', 'Second'), turn('a2', 'assistant', 'Two'),
]);

describe('regenerate and edit', () => {
  test('stored ratings come back with the turns', () => {
    assert.deepEqual(history.map((t) => t.feedback ?? null), [null, 'up', null, null]);
  });

  test('Regenerate is offered on the latest complete answer only', () => {
    assert.equal(canRegenerate(history, history[3]), true);
    assert.equal(canRegenerate(history, history[1]), false);
    const pending = startAsk(history, 'Third');
    assert.equal(canRegenerate(pending, pending.at(-1)!), false);
  });

  test('a regenerate replaces the latest answer with one pending reply', () => {
    const next = startAsk(history, null);
    assert.deepEqual(next.map((t) => t.id), ['u1', 'a1', 'u2', PENDING_ASSISTANT]);
  });

  test('an edit replaces the question and everything after it', () => {
    const next = startAsk(history, 'First, better', 'u1');
    assert.deepEqual(next.map((t) => [t.role, t.content]), [['user', 'First, better'], ['assistant', '']]);
    assert.equal(laterTurns(history, 'u1'), 3);
    assert.equal(laterTurns(history, 'u2'), 1);
  });

  test('deleting a question takes its answers; an answer goes alone', () => {
    assert.deepEqual(deletionSpan(history, 'u1'), ['u1', 'a1']);
    assert.deepEqual(deletionSpan(history, 'a2'), ['a2']);
    assert.deepEqual(deletionSpan(history, 'missing'), []);
  });

  test('placeholders and screen-only turns are not stored', () => {
    assert.equal(isStored(history[0]), true);
    assert.equal(isStored({ id: 'pending-user', role: 'user', content: '', status: 'sending' }), false);
    assert.equal(isStored({ id: 'local-3', role: 'assistant', content: '', status: 'failed' }), false);
  });
});

describe('conversation list', () => {
  const c = (id: string, updated: string, pinned_at: string | null = null): Conversation => ({ id, title: id, created_at: updated, updated_at: updated, pinned: Boolean(pinned_at), pinned_at });

  test('pinned first (newest pin first), then latest activity, without duplicates', () => {
    const ordered = orderConversations([c('old', '2026-10-01'), c('new', '2026-10-10'), c('pin', '2026-09-01', '2026-10-05'), c('new', '2026-10-10'), c('pin2', '2026-09-02', '2026-10-06')]);
    assert.deepEqual(ordered.map((x) => x.id), ['pin2', 'pin', 'new', 'old']);
  });

  test('the next cursor is the last unpinned row of a full page', () => {
    assert.deepEqual(nextCursor([c('p', '2026-10-01', '2026-10-02'), c('a', '2026-10-09'), c('b', '2026-10-08')], 2), { before_updated_at: '2026-10-08', before_id: 'b' });
    assert.equal(nextCursor([c('a', '2026-10-09')], 2), null);
  });
});

describe('export', () => {
  test('the conversation as Markdown quotes questions and keeps answers as written', () => {
    const md = conversationMarkdown('  Plan\n for Q4 ', fromStored([turn('u', 'user', '# not a heading\nline two'), turn('a', 'assistant', '- point\n\n```js\nx()\n```'), turn('e', 'assistant', '  ')]), { you: 'You', assistant: 'Assistant' }, 'Oct 12');
    assert.equal(md, '# Plan for Q4\n\n_Oct 12_\n\n## You\n\n> # not a heading\n> line two\n\n## Assistant\n\n- point\n\n```js\nx()\n```\n');
  });

  test('file names are safe in any script', () => {
    assert.equal(exportFileName('Q4 plan: "final"/v2'), 'q4-plan-final-v2.md');
    assert.equal(exportFileName('خطة الربع الرابع'), 'خطة-الربع-الرابع.md');
    assert.equal(exportFileName('../../'), 'conversation.md');
  });
});
