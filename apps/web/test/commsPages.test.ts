// Unit tests for the communication pages on the shared shell (docs/web-design-system.md#communication-pages):
// the Phone calls period, talk time, day groups and AI filters, the Meetings status counts, and the
// AI Chat day groups, plus the comms catalogs (same keys and placeholders in English and Arabic).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { aiCount, groupByDay, periodSince, startOfLocalDay, summaryPath, talkTimeText, callsPath } from '../src/lib/calls.ts';
import { needsWork, processedOn, statusCounts } from '../src/lib/meetings.ts';
import { conversationsByDay } from '../src/lib/chat.ts';
import { commsEn } from '../src/lib/messages/comms.en.ts';
import { commsAr } from '../src/lib/messages/comms.ar.ts';
import { placeholdersOf, setLocale } from '../src/lib/i18n.ts';
import { localDay } from '../src/lib/pageSummary.ts';

const now = new Date(2026, 10, 6, 15, 30);

describe('Phone calls page helpers', () => {
  test('periods start at local midnight, today or days back; all time sends no since', () => {
    assert.equal(periodSince('all', now), null);
    assert.equal(periodSince('today', now), new Date(2026, 10, 6).toISOString());
    assert.equal(periodSince('week', now), new Date(2026, 10, 0 + 6 - 6).toISOString());
    assert.equal(periodSince('month', now), startOfLocalDay(now, 29));
    assert.match(callsPath({ since: periodSince('today', now) }), /since=2026-11-0/);
    assert.equal(summaryPath(null), '/api/v1/calls/log/summary');
    assert.match(summaryPath('2026-11-06T00:00:00.000Z'), /summary\?since=2026-11-06T00%3A00%3A00.000Z$/);
  });
  test('talk time reads as seconds, minutes, then hours and minutes', () => {
    setLocale('en');
    assert.equal(talkTimeText(40), '40 s');
    assert.equal(talkTimeText(46 * 60), '46 min');
    assert.equal(talkTimeText(65 * 60), '1 h 05 min');
  });
  test('calls group into today, yesterday and earlier, keeping their order', () => {
    const at = (d: number, h: number) => ({ started_at: new Date(2026, 10, d, h).toISOString() });
    const groups = groupByDay([at(6, 10), at(6, 9), at(5, 17), at(1, 8)], now);
    assert.deepEqual(groups.map((g) => [g.day, g.items.length]), [['today', 2], ['yesterday', 1], ['earlier', 1]]);
    assert.deepEqual(groupByDay([], now), []);
  });
  test('the AI queue filter counts each state', () => {
    const items = [{ status: 'pending_approval' as const }, { status: 'completed' as const }, { status: 'pending_approval' as const }];
    assert.equal(aiCount(items, 'all'), 3);
    assert.equal(aiCount(items, 'pending_approval'), 2);
    assert.equal(aiCount(items, 'failed'), 0);
  });
});

describe('Meetings page helpers', () => {
  const items = [
    { status: 'ready' as const, updated_at: new Date(2026, 10, 6, 9).toISOString() },
    { status: 'ready' as const, updated_at: new Date(2026, 10, 2, 9).toISOString() },
    { status: 'failed' as const, updated_at: new Date(2026, 10, 6, 9).toISOString() },
    { status: 'transcribing' as const, updated_at: new Date(2026, 10, 6, 9).toISOString() },
  ];
  test('every state is counted, zero when none', () => {
    assert.deepEqual(statusCounts(items), { queued: 0, transcribing: 1, analyzing: 0, ready: 2, needs_model: 0, failed: 1 });
  });
  test('the queue holds what is not ready; processed today counts ready meetings updated today', () => {
    assert.deepEqual(needsWork(items).map((m) => m.status), ['failed', 'transcribing']);
    assert.equal(processedOn(items, localDay(now), localDay), 1);
  });
});

describe('AI Chat list', () => {
  test('conversations group by today, yesterday, this week and older', () => {
    const at = (d: number) => ({ updated_at: new Date(2026, 10, d, 9).toISOString() });
    assert.deepEqual(conversationsByDay([at(6), at(5), at(3), at(-10), at(6)], now).map((g) => [g.day, g.items.length]),
      [['today', 2], ['yesterday', 1], ['week', 1], ['older', 1]]);
  });
});

describe('comms catalogs', () => {
  test('Arabic has every English key with the same placeholders and no long dashes', () => {
    assert.deepEqual(Object.keys(commsAr).sort(), Object.keys(commsEn).sort());
    for (const key of Object.keys(commsEn) as (keyof typeof commsEn)[]) {
      assert.deepEqual(placeholdersOf(commsAr[key]).sort(), placeholdersOf(commsEn[key]).sort(), key);
      assert.doesNotMatch(JSON.stringify([commsEn[key], commsAr[key]]), /[–—]/, key);
    }
  });
});
