import assert from 'node:assert/strict';
import { test } from 'node:test';
import { CAMPUS_STALE_MS, FLOW_STALE_MS, HISTORY_CHECK_MS, HISTORY_STALE_MS, PRESENCE_STALE_MS, assistantLive, changeNote, historyLive, officeLive, teamLive, totalsSource, clockText, effectiveLive, flowLive, freshness, liveTone, presenceLive, toMs } from '../src/lib/hqPanelStatus.ts';
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

test('Total agents follows the presence stream while it is live, else the campus read', () => {
  assert.equal(totalsSource('live', true), 'stream');
  assert.equal(totalsSource('live', false), 'campus');
  for (const link of ['connecting', 'reconnecting', 'polling', 'offline', 'denied', 'missing'] as const) assert.equal(totalsSource(link, true), 'campus');
  const campus = (loaded: boolean, failed = false, denied = false) => ({ loaded, failed, denied });
  assert.equal(officeLive('stream', campus(true)), 'live');
  assert.equal(officeLive('stream', campus(true, true)), 'live');
  assert.equal(officeLive('stream', campus(false)), 'connecting');
  assert.equal(officeLive('campus', campus(true)), 'polling');
  assert.equal(officeLive('campus', campus(false)), 'connecting');
  assert.equal(officeLive('campus', campus(true, true)), 'reconnecting');
  assert.equal(officeLive('campus', campus(false, true)), 'offline');
  assert.equal(officeLive('campus', campus(false, true, true)), 'denied');
});

test('the Team panel is as live as the flow it re-reads with, and never Live before it answered', () => {
  assert.equal(teamLive('loading', false, false, 'live'), 'connecting');
  assert.equal(teamLive('error', false, true, 'live'), 'offline');
  assert.equal(teamLive('denied', false, false, 'live'), 'denied');
  assert.equal(teamLive('ready', true, false, 'live'), 'live');
  assert.equal(teamLive('ready', true, false, 'polling'), 'polling');
  assert.equal(teamLive('ready', true, true, 'live'), 'reconnecting');
});

test('the History panel checks on a timer, so it says Checking, never Live', () => {
  assert.equal(historyLive('loading', false), 'connecting');
  assert.equal(historyLive('ready', false), 'polling');
  assert.equal(historyLive('ready', true), 'reconnecting');
  assert.equal(historyLive('error', false), 'offline');
  assert.equal(historyLive('invalid', false), 'offline');
  assert.equal(historyLive('denied', false), 'denied');
  assert.ok(HISTORY_STALE_MS >= 3 * HISTORY_CHECK_MS);
});

test('the Executive assistant needs its tools first, then follows the workspace event link', () => {
  assert.equal(assistantLive(null, 'live'), 'connecting');
  assert.equal(assistantLive('loading', 'live'), 'connecting');
  assert.equal(assistantLive('unavailable', 'live'), 'offline');
  assert.equal(assistantLive('ready', 'live'), 'live');
  assert.equal(assistantLive('ready', 'offline'), 'offline');
  assert.equal(assistantLive('ready', null), 'connecting');
});
