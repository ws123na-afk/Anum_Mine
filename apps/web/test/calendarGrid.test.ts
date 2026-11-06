// Unit tests for the Calendar page's time grid, month, double bookings and week strip
// (docs/calendar.md#web-page, src/lib/calendar.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { conflictIds, dayKey, findConflicts, hourWindow, inRange, layoutDay, monthDays, nextEvent, rangeStats, shiftAnchor, visibleRange, type CalendarEventView } from '../src/lib/calendar.ts';

const at = (y: number, m: number, d: number, h = 0, min = 0) => new Date(y, m - 1, d, h, min);
let n = 0;
const event = (from: Date, to: Date, over: Partial<CalendarEventView> = {}): CalendarEventView => ({
  id: `e${++n}`, title: `Event ${n}`, kind: 'meeting', status: 'scheduled', starts_at: from.toISOString(), ends_at: to.toISOString(),
  attendees: [], created_by: 'u', created_at: '', updated_at: '', ...over,
});

describe('calendar month and modes', () => {
  test('month shows six weeks from the Monday on or before the 1st and steps by month', () => {
    const range = visibleRange('month', at(2026, 11, 18));
    assert.equal(range.days.length, 42);
    assert.equal(dayKey(range.start), '2026-10-26');
    assert.equal(dayKey(shiftAnchor('month', at(2026, 1, 31), 1)), '2026-02-28');
    assert.equal(dayKey(shiftAnchor('month', at(2026, 3, 15), -1)), '2026-02-15');
    assert.equal(monthDays(at(2026, 11, 1)).length, 42);
  });
  test('agenda shows the week; inRange follows the mode', () => {
    assert.equal(visibleRange('agenda', at(2026, 11, 5)).days.length, 7);
    assert.equal(inRange('week', at(2026, 11, 5), at(2026, 11, 8)), true);
    assert.equal(inRange('week', at(2026, 11, 5), at(2026, 11, 9)), false);
    assert.equal(inRange('day', at(2026, 11, 5), at(2026, 11, 5)), true);
    assert.equal(inRange('month', at(2026, 11, 5), at(2026, 10, 31)), false);
  });
});

describe('time grid', () => {
  test('the hour window is 8 to 18, widened to hold early and late events', () => {
    const day = at(2026, 11, 5);
    assert.deepEqual(hourWindow([], [day]), { from: 8, to: 18 });
    assert.deepEqual(hourWindow([event(at(2026, 11, 5, 6, 30), at(2026, 11, 5, 7)), event(at(2026, 11, 5, 20), at(2026, 11, 5, 21, 15))], [day]), { from: 6, to: 22 });
  });
  test('overlapping events share the width in lanes; separate events get the full width', () => {
    const day = at(2026, 11, 5);
    const a = event(at(2026, 11, 5, 9), at(2026, 11, 5, 10));
    const b = event(at(2026, 11, 5, 9, 30), at(2026, 11, 5, 10, 30));
    const c = event(at(2026, 11, 5, 14), at(2026, 11, 5, 15));
    const blocks = layoutDay([a, b, c], day, 8, 18);
    const byId = Object.fromEntries(blocks.map((block) => [block.event.id, block]));
    assert.equal(byId[a.id].lanes, 2);
    assert.equal(byId[b.id].lane, 1);
    assert.equal(byId[c.id].lanes, 1);
    assert.ok(Math.abs(byId[a.id].top - 0.1) < 1e-9);
    assert.ok(Math.abs(byId[a.id].height - 0.1) < 1e-9);
  });
});

describe('double bookings, next event and the strip', () => {
  const a = event(at(2026, 11, 5, 9), at(2026, 11, 5, 10), { attendees: [{ id: 'p1', name: 'Sara', email: 'sara@example.com', response: 'invited' }] });
  const b = event(at(2026, 11, 5, 9, 30), at(2026, 11, 5, 10, 30), { attendees: [{ id: 'p2', name: 'Sara', email: 'SARA@example.com', response: 'accepted' }] });
  const c = event(at(2026, 11, 5, 10, 30), at(2026, 11, 5, 11));
  const gone = event(at(2026, 11, 5, 9), at(2026, 11, 5, 11), { status: 'cancelled' });
  test('only scheduled events that overlap are double bookings (touching is not)', () => {
    assert.deepEqual(findConflicts([a, b, c, gone]).map(([x, y]) => [x.id, y.id]), [[a.id, b.id]]);
    assert.deepEqual([...conflictIds([a, b, c])].sort(), [a.id, b.id].sort());
  });
  test('the next event is the first scheduled one still to start', () => {
    assert.equal(nextEvent([a, b, c, gone], at(2026, 11, 5, 9, 45))?.id, c.id);
    assert.equal(nextEvent([a], at(2026, 11, 5, 12)), null);
  });
  test('the strip counts events, distinct people, double bookings and missing replies', () => {
    assert.deepEqual(rangeStats([a, b, c, gone], at(2026, 11, 5, 8)), { events: 3, people: 1, conflicts: 1, awaiting: 1, cancelled: 1 });
  });
});
