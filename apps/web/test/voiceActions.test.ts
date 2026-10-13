// Unit tests for voice action cards (docs/voice.md#actions) in src/lib/voiceActions.ts.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { actionLinks, actionTone, asVoiceActionCard, latestCard } from '../src/lib/voiceActions.ts';
import { translate } from '../src/lib/i18n.ts';

const approval = {
  kind: 'assign', state: 'approval', title: 'Send the price list to Omar', task_id: 'task_1', task_status: 'waiting_approval',
  agent: { id: 'agent_1', name: 'Sales agent' }, command_id: null, steps: 0, approvals_pending: 1,
  approval: { id: 'approval_1', action: 'external.action', risk_level: 'high', status: 'pending', reason: 'High risk', payload_hash: 'a'.repeat(64), arguments: {}, target: null, expires_at: null, can_decide: true },
  summary: '', candidates: [], error_code: null,
};

describe('voice action cards', () => {
  test('a card from the stream keeps its task, agent and approval', () => {
    const card = asVoiceActionCard(approval);
    assert.ok(card);
    assert.equal(card.agent?.name, 'Sales agent');
    assert.equal(card.approval?.payload_hash, 'a'.repeat(64));
    assert.equal(actionTone(card), 'warning');
    // Open the task, and Approvals while something waits there: voice never decides it.
    assert.deepEqual(actionLinks(card), [{ kind: 'task', id: 'task_1' }, { kind: 'approvals' }]);
  });

  test('a finished task links to the task only', () => {
    const card = asVoiceActionCard({ ...approval, state: 'completed', approvals_pending: 0, approval: null });
    assert.ok(card);
    assert.equal(actionTone(card), 'success');
    assert.deepEqual(actionLinks(card), [{ kind: 'task', id: 'task_1' }]);
  });

  test('refusals and misses have no links and read as problems', () => {
    const denied = asVoiceActionCard({ kind: 'cancel_task', state: 'denied', summary: 'Only workspace owners can cancel tasks.', error_code: 'forbidden' });
    assert.ok(denied);
    assert.deepEqual(actionLinks(denied), []);
    assert.equal(actionTone(denied), 'danger');
    const ambiguous = asVoiceActionCard({ kind: 'task_status', state: 'ambiguous', candidates: ['Weekly report', 7, 'Weekly report for sales'] });
    assert.deepEqual(ambiguous?.candidates, ['Weekly report', 'Weekly report for sales']);
  });

  test('a malformed card is ignored and the last good one stays', () => {
    assert.equal(asVoiceActionCard(null), null);
    assert.equal(asVoiceActionCard({ kind: 'delete_everything', state: 'completed' }), null);
    assert.equal(asVoiceActionCard({ kind: 'assign', state: 'teleported' }), null);
    const first = asVoiceActionCard({ ...approval, state: 'running' });
    assert.equal(latestCard(first, { nope: true }), first);
    assert.equal(latestCard(first, approval)?.state, 'approval');
    assert.equal(asVoiceActionCard({ ...approval, steps: -3, approvals_pending: 'many' })?.steps, 0);
  });

  test('every kind and state has English and Arabic words', () => {
    for (const key of ['create_task', 'assign', 'run_task', 'cancel_task', 'task_status', 'run_quick_command']) {
      for (const locale of ['en', 'ar'] as const) assert.notEqual(translate(locale, `voiceAction.kind.${key}` as never), `voiceAction.kind.${key}`);
    }
    for (const state of ['running', 'completed', 'approval', 'failed', 'cancelled', 'created', 'status', 'not_found', 'ambiguous', 'denied', 'needs_detail']) {
      for (const locale of ['en', 'ar'] as const) assert.notEqual(translate(locale, `voiceAction.state.${state}` as never), `voiceAction.state.${state}`);
    }
    assert.equal(translate('en', 'voiceAction.steps', { count: 3 }, { isolate: false }), '3 steps');
  });
});
