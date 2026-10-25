// Unit tests for the live meeting room state (src/lib/liveMeeting.ts): replaying a stored event log,
// the speaking rule, SSE frames and the deep link. No browser, no network.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  SPEAKING_WINDOW_MS, activeSpeakers, applyEvent, budgetUsed, draftFromMessage, initialState, meetingHash, meetingIdFromHash, parseSse, presentActors,
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
