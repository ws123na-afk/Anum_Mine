// Unit tests for chat cards (docs/conversations.md#tools-in-chat) in src/lib/chat.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { applyAskEvent, asCard, cardAfterDecision, fromStored, PENDING_ASSISTANT, startAsk, type ChatCard } from '../src/lib/chat.ts';

const approvalCard: ChatCard = {
  state: 'approval', category: 'action', task_id: 'task_1', run_id: 'run_1', tool: 'external.action', summary: 'Needs approval',
  items: [], total: 0,
  approval: { id: 'approval_1', action: 'external.action', risk_level: 'high', status: 'pending', reason: 'r', payload_hash: 'a'.repeat(64), can_decide: true },
};

describe('chat cards', () => {
  test('tool events put the card on the streaming reply, and done keeps it', () => {
    let turns = startAsk([], 'any urgent customer emails?');
    const running = { state: 'running', category: 'email', task_id: 'task_1', summary: '', items: [], total: 0 };
    turns = applyAskEvent(turns, { event: 'tool', data: { card: running } });
    assert.equal(turns.find((t) => t.id === PENDING_ASSISTANT)?.card?.state, 'running');
    const results = { ...running, state: 'results', items: [{ kind: 'email', id: 'eml_1', title: 'Question' }], total: 1 };
    turns = applyAskEvent(turns, { event: 'tool', data: { card: results } });
    turns = applyAskEvent(turns, { event: 'done', data: { assistant_turn: { id: 'turn_2', conversation_id: 'c', role: 'assistant', content: 'One email.', status: 'complete', created_at: '', card: results } } });
    const reply = turns[turns.length - 1];
    assert.equal(reply.id, 'turn_2');
    assert.equal(reply.card?.state, 'results');
    assert.equal(reply.card?.items[0].id, 'eml_1');
  });

  test('a malformed card is ignored instead of breaking the transcript', () => {
    const turns = startAsk([], 'q');
    assert.deepEqual(applyAskEvent(turns, { event: 'tool', data: { card: { nope: true } } }), turns);
    assert.equal(asCard('text'), null);
    assert.deepEqual(asCard({ state: 'results', category: 'web' })?.items, []);
  });

  test('stored turns keep their cards', () => {
    const [stored] = fromStored([{ id: 't', conversation_id: 'c', role: 'assistant', content: 'x', status: 'complete', created_at: '', card: approvalCard }]);
    assert.equal(stored.card?.approval?.payload_hash, 'a'.repeat(64));
  });

  test('a decision moves the card to what happened', () => {
    const approved = cardAfterDecision(approvalCard, { task: { status: 'completed' }, run: { result: 'Done externally.' }, approval: { status: 'approved' } });
    assert.equal(approved.state, 'results');
    assert.equal(approved.summary, 'Done externally.');
    assert.equal(approved.approval?.status, 'approved');
    const rejected = cardAfterDecision(approvalCard, { task: { status: 'failed' }, run: { steps: [{ type: 'tool_result', summary: 'High-risk action was not approved.' }] }, approval: { status: 'rejected' } });
    assert.equal(rejected.state, 'failed');
    assert.equal(rejected.summary, 'High-risk action was not approved.');
    const partial = cardAfterDecision(approvalCard, { task: { status: 'waiting_approval' }, approval: { status: 'pending' } });
    assert.equal(partial.state, 'approval');
  });
});
