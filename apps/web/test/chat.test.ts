// Unit tests for the Assistant chat helpers in src/lib/chat.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  applyAskEvent, canRetry, conversationTitle, createSseParser, endAsk, fromStored, isNearBottom, PENDING_ASSISTANT, PENDING_USER,
  retryTarget, startAsk, takeSentences, type ChatTurn, type SseEvent, type UiTurn,
} from '../src/lib/chat.ts';

const turn = (id: string, role: ChatTurn['role'], content: string, status: ChatTurn['status'] = 'complete'): ChatTurn => ({
  id, conversation_id: 'c', role, content, status, created_at: '2026-10-11T09:00:00Z',
});

describe('createSseParser', () => {
  test('delivers events across arbitrary chunk boundaries', () => {
    const seen: SseEvent[] = [];
    const parser = createSseParser((e) => seen.push(e));
    const stream = 'event: turn\ndata: {"user_turn":{"id":"t1"}}\n\nevent: delta\ndata: {"text":"Hel"}\n\n: keep-alive\n\nevent: delta\r\ndata: {"text":"lo\\nthere"}\r\n\r\n';
    for (const piece of stream.match(/[\s\S]{1,7}/g) ?? []) parser.push(piece);
    parser.end();
    assert.deepEqual(seen.map((e) => e.event), ['turn', 'delta', 'delta']);
    assert.deepEqual(seen[2].data, { text: 'lo\nthere' });
  });

  test('flushes a final frame without a trailing blank line and keeps non-JSON data as text', () => {
    const seen: SseEvent[] = [];
    const parser = createSseParser((e) => seen.push(e));
    parser.push('data: plain words');
    parser.end();
    assert.deepEqual(seen, [{ event: 'message', data: 'plain words' }]);
  });
});

describe('ask reducer', () => {
  const history = fromStored([turn('u1', 'user', 'Hi'), turn('a1', 'assistant', 'Hello!')]);

  test('shows the question at once, streams the reply and settles on the stored turns', () => {
    let turns = startAsk(history, 'What is 2+2?');
    assert.deepEqual(turns.slice(-2).map((t) => [t.id, t.status]), [[PENDING_USER, 'sending'], [PENDING_ASSISTANT, 'streaming']]);
    turns = applyAskEvent(turns, { event: 'turn', data: { user_turn: turn('u2', 'user', 'What is 2+2?') } });
    turns = applyAskEvent(turns, { event: 'delta', data: { text: 'Fo' } });
    turns = applyAskEvent(turns, { event: 'delta', data: { text: 'ur.' } });
    assert.equal(turns.at(-1)?.content, 'Four.');
    turns = applyAskEvent(turns, { event: 'done', data: { assistant_turn: turn('a2', 'assistant', 'Four.') } });
    assert.deepEqual(turns.map((t) => [t.id, t.status]), [['u1', 'complete'], ['a1', 'complete'], ['u2', 'complete'], ['a2', 'complete']]);
  });

  test('an error event keeps the partial text and offers Retry of the stored question', () => {
    let turns = startAsk(history, 'Long one');
    turns = applyAskEvent(turns, { event: 'turn', data: { user_turn: turn('u2', 'user', 'Long one') } });
    turns = applyAskEvent(turns, { event: 'delta', data: { text: 'Half' } });
    turns = applyAskEvent(turns, { event: 'error', data: { code: 'model_unavailable', message: 'The model did not answer.', assistant_turn: turn('a2', 'assistant', 'Half', 'failed') } });
    const last = turns.at(-1) as UiTurn;
    assert.deepEqual([last.id, last.status, last.content, last.error], ['a2', 'failed', 'Half', 'The model did not answer.']);
    assert.equal(canRetry(turns), true);
    assert.equal(retryTarget(turns)?.id, 'u2');
  });

  test('Stop marks the reply as stopped; a retry replaces only the pending reply', () => {
    let turns = startAsk(history, 'Explain');
    turns = applyAskEvent(turns, { event: 'turn', data: { user_turn: turn('u2', 'user', 'Explain') } });
    turns = applyAskEvent(turns, { event: 'delta', data: { text: 'So' } });
    turns = endAsk(turns, { stopped: true });
    assert.equal(turns.at(-1)?.status, 'cancelled');
    assert.equal(canRetry(turns), true);
    assert.match(turns.at(-1)?.id ?? '', /^local-/);
    // Retry replaces the stopped partial (the server deletes it too) with one new pending reply.
    const retried = startAsk(turns, null);
    assert.equal(retried.length, 4);
    assert.deepEqual(retried.slice(0, 3).map((t) => t.id), ['u1', 'a1', 'u2']);
    assert.equal(retried.filter((t) => t.id === PENDING_ASSISTANT).length, 1);
    assert.equal(retried.at(-1)?.id, PENDING_ASSISTANT);
  });

  test('a request that failed before the question was stored cannot be retried by id', () => {
    let turns = startAsk([], 'Hello?');
    turns = endAsk(turns, { stopped: false, error: 'Network down' });
    assert.deepEqual(turns.map((t) => t.status), ['failed', 'failed']);
    assert.equal(turns[1].error, 'Network down');
    assert.equal(retryTarget(turns), null);
    assert.equal(canRetry(turns), false);
  });

  test('no Retry while everything succeeded', () => {
    assert.equal(canRetry(history), false);
  });
});

describe('takeSentences', () => {
  test('returns complete sentences and keeps the unfinished tail', () => {
    assert.deepEqual(takeSentences('The sky is blue because of Rayleigh scattering. At sunset the light trav'), {
      sentences: ['The sky is blue because of Rayleigh scattering.'],
      rest: 'At sunset the light trav',
    });
  });

  test('merges very short fragments and flushes the tail at the end', () => {
    const { sentences, rest } = takeSentences('Yes. It works well enough for now! And more', true);
    assert.deepEqual(sentences, ['Yes. It works well enough for now!', 'And more']);
    assert.equal(rest, '');
  });

  test('handles Arabic question marks and line breaks', () => {
    assert.deepEqual(takeSentences('كيف يمكنني مساعدتك اليوم؟ هذا سطر أول طويل\nوهذا سطر ثان', true).sentences, [
      'كيف يمكنني مساعدتك اليوم؟',
      'هذا سطر أول طويل',
      'وهذا سطر ثان',
    ]);
  });
});

describe('isNearBottom and titles', () => {
  test('auto-scroll follows only near the bottom', () => {
    assert.equal(isNearBottom({ scrollTop: 560, scrollHeight: 1000, clientHeight: 400 }), true);
    assert.equal(isNearBottom({ scrollTop: 200, scrollHeight: 1000, clientHeight: 400 }), false);
  });
  test('untitled conversations get a readable name', () => {
    assert.equal(conversationTitle({ id: 'c', title: ' ', created_at: '', updated_at: '' }), 'New conversation');
  });
});
