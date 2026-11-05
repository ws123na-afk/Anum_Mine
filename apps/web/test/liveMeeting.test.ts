// Unit tests for the live meeting room state (src/lib/liveMeeting.ts): replaying a stored event log,
// the speaking rule, SSE frames and the deep link. No browser, no network.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  SPEAKING_WINDOW_MS, activeSpeakers, applyEvent, budgetUsed, draftFromMessage, initialState, meetingHash, meetingIdFromHash, parseSse, presentActors, presentAgentIds,
  type RoomBundle, type RoomEvent, type RoomMeeting,
} from '../src/lib/liveMeeting.ts';

const meeting: RoomMeeting = {
  id: 'm1', title: 'Plan', status: 'active', revision: 3, created_by: 'owner', max_turns: 8, max_duration_seconds: 600, max_tokens: 5000, max_cost_usd: '1.000000',
  max_participants: 6, total_input_tokens: 10, total_output_tokens: 20, total_cost_usd: '0.001000', started_at: '2026-10-22T10:00:00Z', ended_at: null, ended_reason: null,
  hand_raised_by: null, transcript_deleted_at: null,
};
const bundle: RoomBundle = {
  meeting, participants: [{ id: 'p1', actor_id: 'agent_a', kind: 'agent' }], attendance: [], agenda: [], outputs: [], files: [], speaking: [], last_event_seq: 2,
  transcript_deleted: false, viewer: { user_id: 'owner', is_owner: true },
};
let seq = 2;
const ev = (kind: string, payload: Record<string, unknown>, at = '2026-10-22T10:00:10Z'): RoomEvent => ({ seq: ++seq, kind, actor_id: 'x', payload, created_at: at });
const fresh = () => { seq = 2; return initialState(bundle, []); };

describe('replaying events', () => {
  test('a message becomes a transcript row once, even when replayed', () => {
    const message = ev('message', { sequence: 1, speaker_id: 'agent_a', role: 'agent', body: 'Hello', request_key: 'k1' });
    let state = applyEvent(fresh(), message);
    state = applyEvent(state, message);
    assert.equal(state.messages.length, 1);
    assert.equal(state.lastSeq, 3);
  });

  test('events at or below the loaded cursor are ignored (reconnect replay)', () => {
    const state = applyEvent(fresh(), { seq: 2, kind: 'message', actor_id: null, payload: { sequence: 9, body: 'old' }, created_at: '' });
    assert.equal(state.messages.length, 0);
  });

  test('speaking starts at turn_started and stops at the message or the failure, never on its own', () => {
    let state = applyEvent(fresh(), ev('turn_started', { agent_id: 'agent_a', sequence: 1, request_key: 'k1' }));
    const t0 = Date.parse('2026-10-22T10:00:11Z');
    assert.deepEqual(activeSpeakers(state, t0), ['agent_a']);
    assert.deepEqual(activeSpeakers(state, t0 + SPEAKING_WINDOW_MS + 1000), []); // a crashed worker never shows as speaking forever
    state = applyEvent(state, ev('message', { sequence: 1, speaker_id: 'agent_a', role: 'agent', body: 'Hi', request_key: 'k1' }));
    assert.deepEqual(activeSpeakers(state, t0), []);
    state = applyEvent(state, ev('turn_started', { agent_id: 'agent_a', sequence: 2, request_key: 'k2' }));
    state = applyEvent(state, ev('turn_failed', { sequence: 2, code: 'conflict', reason: 'Meeting cost cap exceeded' }));
    assert.deepEqual(activeSpeakers(state, t0), []);
    assert.equal(state.notices[0].reason, 'Meeting cost cap exceeded');
  });

  test('pause, end and a hand', () => {
    let state = applyEvent(fresh(), ev('turn_started', { agent_id: 'agent_a', sequence: 1, request_key: 'k1' }));
    state = applyEvent(state, ev('hand_raised', { actor_id: 'guest' }));
    assert.equal(state.meeting.hand_raised_by, 'guest');
    state = applyEvent(state, ev('state', { status: 'paused', revision: 5 }));
    assert.equal(state.meeting.status, 'paused');
    assert.equal(state.meeting.revision, 5);
    assert.deepEqual(activeSpeakers(state, Date.parse('2026-10-22T10:00:11Z')), []);
    state = applyEvent(state, ev('state', { status: 'finished', reason: 'limit_reached', revision: 6 }));
    assert.equal(state.meeting.ended_reason, 'limit_reached');
    assert.equal(state.meeting.hand_raised_by, null);
  });

  test('attendance, agenda, outputs and files follow their events', () => {
    let state = applyEvent(fresh(), ev('joined', { actor_id: 'guest' }));
    assert.deepEqual(presentActors(state), ['guest']);
    state = applyEvent(state, ev('left', { actor_id: 'guest' }));
    assert.deepEqual(presentActors(state), []);
    state = applyEvent(state, ev('agenda', { op: 'add', item: { id: 'a1', position: 1, title: 'Budget', state: 'open' } }));
    state = applyEvent(state, ev('agenda', { op: 'add', item: { id: 'a2', position: 2, title: 'Hiring', state: 'open' } }));
    state = applyEvent(state, ev('agenda', { op: 'update', item: { id: 'a1', position: 1, title: 'Budget', state: 'current' } }));
    state = applyEvent(state, ev('agenda', { op: 'update', item: { id: 'a2', position: 2, title: 'Hiring', state: 'current' } }));
    assert.deepEqual(state.agenda.map((a) => a.state), ['open', 'current']);
    state = applyEvent(state, ev('agenda', { op: 'delete', item: { id: 'a1' } }));
    assert.equal(state.agenda.length, 1);
    const output = { id: 'o1', kind: 'decision', body: 'Ship', state: 'draft', owner_label: null, source_sequences: [1], task_id: null, created_by: 'owner', decided_by: null };
    state = applyEvent(state, ev('output', { op: 'create', output }));
    state = applyEvent(state, ev('output', { op: 'update', output: { ...output, state: 'confirmed' } }));
    assert.deepEqual(state.outputs.map((o) => o.state), ['confirmed']);
    state = applyEvent(state, ev('file', { op: 'add', file_id: 'f1', name: 'plan.pdf' }));
    state = applyEvent(state, ev('file', { op: 'remove', file_id: 'f1' }));
    assert.equal(state.files.length, 0);
  });
});

describe('an agent removed from the live meeting', () => {
  test('agent_left marks only that agent as left, stops its speaking dot, and it no longer takes part', () => {
    seq = 2;
    const two = initialState({ ...bundle, participants: [{ id: 'p1', actor_id: 'agent_a', kind: 'agent' }, { id: 'p2', actor_id: 'agent_b', kind: 'agent' }, { id: 'p3', actor_id: 'owner', kind: 'human' }] }, []);
    assert.deepEqual(presentAgentIds(two.participants), ['agent_a', 'agent_b']);
    let state = applyEvent(two, ev('turn_started', { agent_id: 'agent_b', request_key: 'k9', sequence: 4 }));
    state = applyEvent(state, ev('agent_left', { agent_id: 'agent_b' }, '2026-10-22T10:05:00Z'));
    assert.equal(state.participants.find((p) => p.actor_id === 'agent_b')?.left_at, '2026-10-22T10:05:00Z');
    assert.equal(state.participants.find((p) => p.actor_id === 'agent_a')?.left_at, undefined);
    assert.deepEqual(presentAgentIds(state.participants), ['agent_a']);
    assert.deepEqual(Object.values(state.speaking), []);
    // A replay of the same leave keeps the first time; a stored left_at from the room read counts as left too.
    const again = applyEvent(state, { ...ev('agent_left', { agent_id: 'agent_b' }, '2026-10-22T11:00:00Z') });
    assert.equal(again.participants.find((p) => p.actor_id === 'agent_b')?.left_at, '2026-10-22T10:05:00Z');
    assert.deepEqual(presentAgentIds([{ id: 'p1', actor_id: 'agent_a', kind: 'agent', left_at: '2026-10-22T10:05:00Z' }]), []);
  });
});

describe('server-sent frames', () => {
  const frame = (e: RoomEvent, name = e.kind) => `id: ${e.seq}\nevent: ${name}\ndata: ${JSON.stringify(e)}\n\n`;

  test('whole frames parse and a torn frame waits for the next chunk', () => {
    const a = ev('message', { sequence: 1, body: 'x' });
    const text = frame(a) + 'id: 9\nevent: mess';
    const first = parseSse(text);
    assert.equal(first.events.length, 1);
    assert.equal(first.events[0].seq, a.seq);
    assert.equal(first.rest, 'id: 9\nevent: mess');
  });

  test('end and reconnect frames are flags, comments and retry lines are skipped', () => {
    const out = parseSse('retry: 3000\n\n: keep-alive\n\nevent: end\ndata: {"seq":4}\n\n');
    assert.deepEqual([out.events.length, out.end, out.reconnect], [0, true, false]);
    assert.equal(parseSse('event: reconnect\ndata: {"seq":4}\n\n').reconnect, true);
  });

  test('a bad frame is dropped without throwing', () => {
    assert.equal(parseSse('event: message\ndata: {not json\n\n').events.length, 0);
  });
});

describe('helpers', () => {
  test('budget numbers come from the stored totals', () => {
    const used = budgetUsed(meeting, 3);
    assert.deepEqual([used.turns.used, used.tokens.used, used.cost.used, used.cost.max], [3, 30, 0.001, 1]);
  });
  test('draft text is collapsed and capped', () => {
    assert.equal(draftFromMessage('a\n\n b   c'), 'a b c');
    assert.equal(draftFromMessage('x'.repeat(400)).length, 300);
  });
  test('deep link', () => {
    assert.equal(meetingIdFromHash('#office/meeting/agent_meeting_1'), 'agent_meeting_1');
    assert.equal(meetingIdFromHash('#office/meeting/../etc'), null);
    assert.equal(meetingIdFromHash('#office-preview'), null);
    assert.equal(meetingHash('a b'), '#office/meeting/a%20b');
  });
});

describe('owner-only transcript and the monthly budget (S-02, S-03)', () => {
  const hiddenBundle: RoomBundle = { ...bundle, outputs: [], files: [], transcript_hidden: true, outputs_count: 3, files_count: 2, viewer: { user_id: 'member', is_owner: false, can_read_transcript: false } };

  test('a bundle without text keeps only the counts and the flag', () => {
    const state = initialState(hiddenBundle, []);
    assert.equal(state.transcriptHidden, true);
    assert.equal(state.outputsCount, 3);
    assert.equal(state.filesCount, 2);
    assert.equal(state.messages.length, 0);
    assert.equal(state.viewer.can_read_transcript, false);
  });

  test('an owner bundle is not hidden and counts what it holds', () => {
    const state = initialState({ ...bundle, outputs: [{ id: 'o', kind: 'decision', body: 'x', state: 'draft', owner_label: null, source_sequences: [], task_id: null, created_by: 'u', decided_by: null }] }, []);
    assert.equal(state.transcriptHidden, false);
    assert.equal(state.outputsCount, 1);
  });

  test('a message and the budget event both ask the room to refetch the monthly budget', () => {
    let state = fresh();
    assert.equal(state.budgetTick, 0);
    state = applyEvent(state, ev('message', { sequence: 1, speaker_id: 'agent_a', role: 'agent', body: 'Hi', request_key: 'k1' }));
    assert.equal(state.budgetTick, 1);
    state = applyEvent(state, ev('budget_reached', { limit_usd: 50, used_usd: 50.01 }));
    assert.equal(state.budgetTick, 2);
    assert.equal(state.messages.length, 1);
  });

  test('the budget pause arrives as a state event and stops the speaking dot', () => {
    let state = applyEvent(fresh(), ev('turn_started', { agent_id: 'agent_a', sequence: 1, request_key: 'k1' }));
    state = applyEvent(state, ev('state', { status: 'paused', reason: 'budget', revision: 5 }));
    assert.equal(state.meeting.status, 'paused');
    assert.deepEqual(activeSpeakers(state, Date.parse('2026-10-22T10:00:11Z')), []);
    assert.equal(state.meeting.ended_reason, null);
  });
});
