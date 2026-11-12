import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AppCall, AppCallRoom } from '@anum/contracts';
import { activeOthers, canShareScreen, freeSlots, groupHistory, groupOutcome, parsePeerState } from '../src/lib/appCalls.ts';

const call = (over: Partial<AppCall> = {}): AppCall => ({
  id: 'call_1', direction: 'outgoing', peer_user_id: 'bob', peer_name: 'Bob', media: 'audio', status: 'ended', outcome: 'completed',
  created_at: '2026-11-12T10:00:00Z', answered_at: '2026-11-12T10:00:05Z', ended_at: '2026-11-12T10:02:00Z', duration_seconds: 115,
  ring_expires_at: '2026-11-12T10:00:45Z', answered_elsewhere: false, room_id: null, ...over,
});

const room = (states: [string, AppCallRoom['members'][number]['state'], boolean?][]): AppCallRoom => ({
  id: 'room_1', media: 'audio', status: 'live', host_user_id: 'me', max_people: 4, created_at: '2026-11-12T10:00:00Z', ended_at: null,
  members: states.map(([user_id, state, is_you]) => ({ user_id, name: user_id, state, is_you: Boolean(is_you) })),
});

describe('group call helpers', () => {
  test('the people still in the call or being rung, without you', () => {
    const r = room([['me', 'joined', true], ['bob', 'joined'], ['sara', 'invited'], ['omar', 'declined']]);
    assert.deepEqual(activeOthers(r).map((m) => m.user_id), ['bob', 'sara']);
    assert.deepEqual(activeOthers(null), []);
  });

  test('a group holds four people at once, you included', () => {
    assert.equal(freeSlots(room([['me', 'joined', true], ['bob', 'joined']])), 2);
    assert.equal(freeSlots(room([['me', 'joined', true], ['bob', 'joined'], ['sara', 'invited'], ['omar', 'left']])), 1);
    assert.equal(freeSlots(room([['me', 'joined', true], ['a', 'joined'], ['b', 'joined'], ['c', 'invited']])), 0);
    // A call between two people can take two more.
    assert.equal(freeSlots(null, 1), 2);
  });

  test('history rows of one group call become one entry; other calls stay apart', () => {
    const items = [
      call({ id: 'c4' }),
      call({ id: 'c3', room_id: 'room_1', peer_name: 'Sara', outcome: 'declined' }),
      call({ id: 'c2', room_id: 'room_1', peer_name: 'Bob' }),
      call({ id: 'c1', room_id: 'room_2' }),
    ];
    const entries = groupHistory(items);
    assert.deepEqual(entries.map((e) => [e.roomId, e.calls.map((c) => c.id)]), [[null, ['c4']], ['room_1', ['c3', 'c2']], ['room_2', ['c1']]]);
  });

  test('a group call reads as ended if any connection connected, else as its first outcome', () => {
    assert.equal(groupOutcome([call({ outcome: 'declined' }), call({ outcome: 'completed' })]), 'completed');
    assert.equal(groupOutcome([call({ direction: 'incoming', outcome: 'cancelled' })]), 'missed');
  });

  test('screen sharing is offered only where the browser has getDisplayMedia', () => {
    assert.equal(canShareScreen({ getDisplayMedia: async () => ({}) as MediaStream }), true);
    assert.equal(canShareScreen({}), false);
    assert.equal(canShareScreen(undefined), false);
  });

  test('the data channel state is parsed strictly', () => {
    assert.deepEqual(parsePeerState('{"camera":true,"screen":false,"muted":true}'), { camera: true, screen: false, muted: true });
    assert.equal(parsePeerState('{"camera":"yes","screen":false,"muted":true}'), null);
    assert.equal(parsePeerState('not json'), null);
    assert.equal(parsePeerState(42), null);
    assert.equal(parsePeerState(`{"camera":true,"screen":false,"muted":true,"pad":"${'x'.repeat(300)}"}`), null);
  });
});
