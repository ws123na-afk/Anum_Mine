import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CAMPUS_STALE_MS, FLOW_STALE_MS, PRESENCE_STALE_MS, changeNote, clockText, effectiveLive, flowLive, freshness, liveTone, presenceLive, toMs } from '../src/lib/hqPanelStatus.ts';
import { PRESENCE_TIMING } from '../src/lib/presenceStream.ts';

test('the presence window is the stream hub window, not a second number', () => {
  assert.equal(PRESENCE_STALE_MS, PRESENCE_TIMING.staleAfterMs);
  assert.ok(FLOW_STALE_MS >= 3 * 20_000 - 1);
  assert.ok(CAMPUS_STALE_MS >= 3 * 30_000);
});

test('presence and flow links map to the five words the page shows', () => {
  assert.equal(presenceLive('live'), 'live');
  assert.equal(presenceLive('missing'), 'offline');
  assert.equal(presenceLive('denied'), 'denied');
  assert.equal(flowLive('live', 'ready'), 'live');
  assert.equal(flowLive('polling', 'ready'), 'polling');
  assert.equal(flowLive('polling', 'loading'), 'connecting');
  assert.equal(flowLive('offline', 'ready'), 'offline');
  assert.equal(flowLive('live', 'error'), 'offline');
  assert.equal(flowLive('live', 'denied'), 'denied');
});

test('tones: only live is green, a dropped link is amber, no connection is red', () => {
  assert.equal(liveTone('live'), 'ok');
  assert.equal(liveTone('reconnecting'), 'warn');
  assert.equal(liveTone('polling'), 'warn');
  assert.equal(liveTone('connecting'), 'warn');
  assert.equal(liveTone('offline'), 'stop');
  assert.equal(liveTone('denied'), 'stop');
});

test('freshness: unknown time is never guessed, the window is exclusive of the edge', () => {
  const now = Date.parse('2026-10-27T10:00:00Z');
  assert.equal(freshness(null, now, 60_000), 'none');
  assert.equal(freshness('not a date', now, 60_000), 'none');
  assert.equal(freshness('2026-10-27T09:59:30Z', now, 60_000), 'fresh');
  assert.equal(freshness('2026-10-27T09:59:00Z', now, 60_000), 'fresh');
  assert.equal(freshness('2026-10-27T09:58:59Z', now, 60_000), 'stale');
  assert.equal(freshness(now - 1000, now, 60_000), 'fresh');
});

test('epoch seconds and milliseconds both read as a time', () => {
  assert.equal(toMs(1_790_000_000), 1_790_000_000_000);
  assert.equal(toMs(1_790_000_000_000), 1_790_000_000_000);
  assert.equal(toMs(''), null);
  assert.equal(toMs(undefined), null);
});

test('a stale panel never says Live', () => {
  assert.equal(effectiveLive('live', 'stale'), 'reconnecting');
  assert.equal(effectiveLive('live', 'fresh'), 'live');
  assert.equal(effectiveLive('live', 'none'), 'live');
  assert.equal(effectiveLive('offline', 'stale'), 'offline');
  assert.equal(effectiveLive('polling', 'stale'), 'polling');
});

test('the clock is hh:mm:ss in 24 hour digits in both languages, empty when unknown', () => {
  const at = new Date(2026, 9, 27, 7, 5, 9).toISOString();
  assert.match(clockText(at, 'en'), /^\d{2}:\d{2}:\d{2}$/);
  assert.match(clockText(at, 'ar'), /^\d{2}:\d{2}:\d{2}$/);
  assert.equal(clockText(null, 'en'), '');
});

test('the live region speaks only when the state changes', () => {
  const say = (s: string, n: string) => `${n}: ${s}`;
  assert.equal(changeNote(null, 'live', 'Flow', say), null);
  assert.equal(changeNote('live', 'live', 'Flow', say), null);
  assert.equal(changeNote('live', 'reconnecting', 'Flow', say), 'Flow: reconnecting');
});
