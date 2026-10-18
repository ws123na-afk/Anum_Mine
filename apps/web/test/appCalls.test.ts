import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AppCall } from '@anum/contracts';
import {
  elapsedSeconds, formatDuration, initialsOf, isMissed, mediaProblem, newClientId, outcomeWord, parseSse, phaseFor, reconnectDelay,
} from '../src/lib/appCalls.ts';

const call = (over: Partial<AppCall> = {}): AppCall => ({
  id: 'call_1', direction: 'outgoing', peer_user_id: 'bob', peer_name: 'Bob', media: 'audio', status: 'ringing', outcome: null,
  created_at: '2026-10-18T10:00:00Z', answered_at: null, ended_at: null, duration_seconds: null, ring_expires_at: '2026-10-18T10:00:45Z',
  answered_elsewhere: false, ...over,
});

describe('in-app call helpers', () => {
  test('device ids are url-safe, 22 characters and random', () => {
    const id = newClientId();
    assert.match(id, /^[A-Za-z0-9_-]{22}$/);
    assert.notEqual(id, newClientId());
    assert.equal(newClientId((bytes) => bytes.fill(255)), '_____________________w');
  });

  test('durations read like a phone', () => {
    assert.equal(formatDuration(7), '0:07');
    assert.equal(formatDuration(750), '12:30');
    assert.equal(formatDuration(3723), '1:02:03');
    assert.equal(formatDuration(-4), '0:00');
    assert.equal(elapsedSeconds({ answered_at: '2026-10-18T10:00:00Z' }, Date.parse('2026-10-18T10:01:05Z')), 65);
    assert.equal(elapsedSeconds({ answered_at: null }), 0);
  });

  test('each participant reads the outcome from their side', () => {
    assert.equal(outcomeWord(call({ direction: 'incoming', outcome: 'cancelled' })), 'missed');
    assert.equal(outcomeWord(call({ outcome: 'cancelled' })), 'cancelled');
    assert.equal(outcomeWord(call({ outcome: 'missed' })), 'noAnswer');
    assert.equal(outcomeWord(call({ direction: 'incoming', outcome: 'busy' })), 'missed');
    assert.equal(outcomeWord(call({ outcome: 'busy' })), 'busyPeer');
    assert.equal(outcomeWord(call({ outcome: 'declined' })), 'declinedByPeer');
    assert.equal(outcomeWord(call({ direction: 'incoming', outcome: 'declined' })), 'declinedByYou');
    assert.equal(outcomeWord(call({ outcome: 'completed' })), 'completed');
    assert.equal(outcomeWord(call()), null);
    assert.ok(isMissed(call({ direction: 'incoming', outcome: 'missed' })));
    assert.ok(!isMissed(call({ outcome: 'missed' })));
  });

  test('the call screen phase follows the call and the media path', () => {
    const idle = { connected: false, reconnecting: false };
    assert.equal(phaseFor(null, idle), 'idle');
    assert.equal(phaseFor(call(), idle), 'ringing');
    assert.equal(phaseFor(call({ direction: 'incoming' }), idle), 'incoming');
    assert.equal(phaseFor(call({ status: 'accepted' }), idle), 'connecting');
    assert.equal(phaseFor(call({ status: 'accepted' }), { connected: true, reconnecting: false }), 'connected');
    assert.equal(phaseFor(call({ status: 'accepted' }), { connected: true, reconnecting: true }), 'reconnecting');
    assert.equal(phaseFor(call({ status: 'ended', outcome: 'completed' }), idle), 'ended');
  });

  test('microphone and camera errors map to what the screen explains', () => {
    assert.equal(mediaProblem({ name: 'NotAllowedError' }), 'denied');
    assert.equal(mediaProblem({ name: 'NotFoundError' }), 'noDevice');
    assert.equal(mediaProblem({ name: 'NotReadableError' }), 'inUse');
    assert.equal(mediaProblem({ name: 'SecurityError' }), 'insecure');
    assert.equal(mediaProblem(new Error('x')), 'other');
    assert.equal(mediaProblem(null), 'other');
  });

  test('the SSE parser keeps unfinished frames and skips comments', () => {
    const { frames, rest } = parseSse('retry: 1000\n\n: keep-alive\n\nid: a\nevent: call\ndata: {"id":"c"}\n\nevent: signal\ndata: {"x"');
    assert.deepEqual(frames, [{ event: 'call', id: 'a', data: '{"id":"c"}' }]);
    assert.equal(rest, 'event: signal\ndata: {"x"');
    assert.deepEqual(parseSse('data: one\ndata: two\r\n\r\n').frames, [{ event: 'message', id: null, data: 'one\ntwo' }]);
  });

  test('reconnects back off and initials fit the avatar', () => {
    assert.deepEqual([0, 1, 2, 3, 9].map(reconnectDelay), [500, 1000, 2000, 4000, 15000]);
    assert.equal(initialsOf('Sara Ali Khan'), 'SK');
    assert.equal(initialsOf('bob'), 'BO');
    assert.equal(initialsOf('  '), '?');
    assert.equal(initialsOf('سارة علي'), 'سع');
  });
});
