// Unit tests for the Calendar page's repeating, all-day and reminder helpers
// (docs/calendar.md#recurrence, src/lib/calendar.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  allDayLength, allDayOn, conflictIds, emptyForm, eventBody, eventBounds, eventTiming, findConflicts, formFromEvent, formProblem, groupByDay, hourWindow, keyOf, layoutDay,
  moveEmailReminder, recurrenceFrom, recurrenceSummary, reminderLabel, repeatFields, type CalendarEventView, type EventForm, type Weekday,
} from '../src/lib/calendar.ts';

const at = (y: number, m: number, d: number, h = 0, min = 0) => new Date(y, m - 1, d, h, min);
let n = 0;
const event = (from: Date, to: Date, over: Partial<CalendarEventView> = {}): CalendarEventView => ({
  id: `e${++n}`, title: `Event ${n}`, kind: 'meeting', status: 'scheduled', starts_at: from.toISOString(), ends_at: to.toISOString(),
  attendees: [], created_by: 'u', created_at: '', updated_at: '', ...over,
});
const t = (key: string, vars: Record<string, string | number> = {}) => `${key.replace('calendar.', '')}(${Object.entries(vars).map(([k, v]) => `${k}=${v}`).join(',')})`;
const weekday = (day: Weekday) => day;
const date = (iso: string) => iso;
const join = (parts: string[]) => parts.join(' & ');
const form = (over: Partial<EventForm> = {}): EventForm => ({ ...emptyForm(at(2026, 11, 11), at(2026, 11, 11, 8)), title: 'Standup', ...over });

describe('repeat rules from the form', () => {
  test('weekly without a ticked day repeats on the start weekday; ticked days are kept in week order', () => {
    assert.deepEqual(recurrenceFrom(form({ repeat: 'weekly' })), { freq: 'weekly', interval: 1, by_weekday: ['WE'] });
    assert.deepEqual(recurrenceFrom(form({ repeat: 'weekly', interval: 2, weekdays: ['FR', 'MO'], endMode: 'count', count: 6 })), { freq: 'weekly', interval: 2, by_weekday: ['MO', 'FR'], count: 6 });
  });
  test('monthly by weekday takes the nth weekday of the start, the last one in the final week', () => {
    assert.deepEqual(recurrenceFrom(form({ repeat: 'monthly', monthlyMode: 'weekday' })), { freq: 'monthly', interval: 1, by_weekday: ['WE'], by_setpos: 2 });
    assert.deepEqual(recurrenceFrom(form({ date: '2026-11-25', repeat: 'monthly', monthlyMode: 'weekday' })), { freq: 'monthly', interval: 1, by_weekday: ['WE'], by_setpos: -1 });
    assert.deepEqual(recurrenceFrom(form({ repeat: 'monthly', endMode: 'until', until: '2027-05-01' })), { freq: 'monthly', interval: 1, until: '2027-05-01' });
    assert.equal(recurrenceFrom(form()), null);
  });
  test('stored rules fill the form back and the form reports bad ends', () => {
    assert.deepEqual(repeatFields({ freq: 'monthly', by_weekday: ['TU'], by_setpos: 2, count: 4 }), { repeat: 'monthly', interval: 1, weekdays: ['TU'], monthlyMode: 'weekday', endMode: 'count', until: '', count: 4 });
    assert.equal(formProblem(form({ repeat: 'daily', endMode: 'until', until: '2026-01-01' })), 'until');
    assert.equal(formProblem(form({ repeat: 'daily', endMode: 'count', count: 0 })), 'count');
    assert.equal(formProblem(form({ allDay: true, startTime: '', endTime: '' })), null);
    assert.equal(formProblem(form({ allDay: true, endDate: '2026-11-10' })), 'order');
  });
});

describe('the API body', () => {
  test('an all-day event sends its dates and zone; a scoped edit names the occurrence and drops the rule', () => {
    const body = eventBody(form({ allDay: true, endDate: '2026-11-12', timezone: 'Asia/Dubai', reminders: [1440, 10, 10] }));
    assert.equal(body.all_day, true);
    assert.deepEqual([body.start_date, body.end_date, body.timezone, body.reminders], ['2026-11-11', '2026-11-12', 'Asia/Dubai', [10, 1440]]);
    assert.equal(body.recurrence, null);
    const one = eventBody(form({ repeat: 'daily' }), [], 'this', '2026-11-11T05:00:00Z');
    assert.equal('recurrence' in one, false);
    assert.deepEqual([one.scope, one.occurrence_start], ['this', '2026-11-11T05:00:00Z']);
    const following = eventBody(form({ repeat: 'daily' }), [], 'following', '2026-11-11T05:00:00Z');
    assert.deepEqual(following.recurrence, { freq: 'daily', interval: 1 });
  });
  test('editing an occurrence of an all-day series fills the form from its dates', () => {
    const series = event(at(2026, 11, 10), at(2026, 11, 12), { all_day: true, start_date: '2026-11-10', end_date: '2026-11-11', timezone: 'Asia/Dubai', recurrence: { freq: 'weekly', by_weekday: ['TU'] }, reminders: [0] });
    const filled = formFromEvent(series);
    assert.deepEqual([filled.allDay, filled.date, filled.endDate, filled.timezone, filled.repeat, filled.weekdays, filled.reminders], [true, '2026-11-10', '2026-11-11', 'Asia/Dubai', 'weekly', ['TU'], [0]]);
  });
  test('email reminders are the reminders also emailed, and follow a changed reminder', () => {
    const body = eventBody(form({ reminders: [60, 10], emailReminders: [60, 5, 60] }));
    assert.deepEqual([body.reminders, body.email_reminders], [[10, 60], [60]]);
    const stored = event(at(2026, 11, 10, 9), at(2026, 11, 10, 10), { reminders: [10, 60], email_reminders: [60, 30] });
    assert.deepEqual(formFromEvent(stored).emailReminders, [60]);
    assert.deepEqual(emptyForm(at(2026, 11, 11), at(2026, 11, 11, 8)).emailReminders, []);
    assert.deepEqual(moveEmailReminder([60], 60, 120), [120]);
    assert.deepEqual(moveEmailReminder([60], 10, 60), []);
    assert.deepEqual(moveEmailReminder([60, 10], 30, 15), [60, 10]);
  });
});

describe('summaries and labels', () => {
  test('rules read as sentences', () => {
    assert.equal(recurrenceSummary(null, t, weekday, date, join), 'repeat.none()');
    assert.equal(recurrenceSummary({ freq: 'daily' }, t, weekday, date, join), 'repeat.daily(count=1)');
    assert.equal(recurrenceSummary({ freq: 'weekly', interval: 2, by_weekday: ['MO', 'WE'], until: '2026-12-31' }, t, weekday, date, join), 'repeat.until(rule=repeat.everyWeeksOn(count=2,days=MO & WE),date=2026-12-31)');
    assert.equal(recurrenceSummary({ freq: 'monthly', by_weekday: ['FR'], by_setpos: -1, count: 3 }, t, weekday, date, join), 'repeat.times(rule=repeat.monthlyOnWeekday(count=1,position=repeat.position.last(),day=FR),count=3)');
    assert.equal(recurrenceSummary({ freq: 'monthly', by_monthday: -1 }, t, weekday, date, join), 'repeat.monthlyOnDay(count=1,day=repeat.lastDay())');
    assert.equal(recurrenceSummary({ freq: 'yearly', interval: 3 }, t, weekday, date, join), 'repeat.every_yearly(count=3)');
  });
  test('reminder minutes become the largest whole unit', () => {
    assert.equal(reminderLabel(0, t), 'reminder.atStart()');
    assert.equal(reminderLabel(45, t), 'reminder.minutes(count=45)');
    assert.equal(reminderLabel(120, t), 'reminder.hours(count=2)');
    assert.equal(reminderLabel(1440, t), 'reminder.days(count=1)');
    assert.equal(reminderLabel(20160, t), 'reminder.weeks(count=2)');
  });
});

describe('occurrences and all-day events on the grid', () => {
  test('occurrences are told apart by their key and an all-day event covers whole local days', () => {
    const a = event(at(2026, 11, 11, 9), at(2026, 11, 11, 10), { id: 'series', occurrence_key: 'series@2026-11-11T05:00:00Z' });
    const b = { ...a, occurrence_key: 'series@2026-11-18T05:00:00Z', starts_at: at(2026, 11, 18, 9).toISOString(), ends_at: at(2026, 11, 18, 10).toISOString() };
    assert.notEqual(keyOf(a), keyOf(b));
    const allDay = event(at(2026, 11, 10, 20), at(2026, 11, 12, 20), { all_day: true, start_date: '2026-11-11', end_date: '2026-11-12' });
    assert.deepEqual(eventBounds(allDay), { from: at(2026, 11, 11).getTime(), to: at(2026, 11, 13).getTime() });
    assert.equal(allDayLength(allDay), 2);
    const days = [at(2026, 11, 10), at(2026, 11, 11), at(2026, 11, 12), at(2026, 11, 13)];
    assert.deepEqual(groupByDay([allDay, a], days).map((g) => g.events.length), [0, 2, 1, 0]);
    assert.equal(groupByDay([allDay, a], days)[1].events[0], allDay, 'all-day events lead the day');
    assert.deepEqual(allDayOn([allDay, a], at(2026, 11, 12)).map((e) => e.id), [allDay.id]);
    assert.equal(eventTiming(allDay, at(2026, 11, 12, 23)), 'now');
  });
  test('all-day events stay out of the hours, the lanes and the double bookings', () => {
    const allDay = event(at(2026, 11, 11), at(2026, 11, 12), { all_day: true, start_date: '2026-11-11', end_date: '2026-11-11' });
    const timed = event(at(2026, 11, 11, 9), at(2026, 11, 11, 10));
    assert.deepEqual(hourWindow([allDay, timed], [at(2026, 11, 11)]), { from: 8, to: 18 });
    assert.equal(layoutDay([allDay, timed], at(2026, 11, 11), 8, 18).length, 1);
    assert.equal(findConflicts([allDay, timed]).length, 0);
    const twin = event(at(2026, 11, 11, 9, 30), at(2026, 11, 11, 10, 30), { id: 'twin', occurrence_key: 'twin@x' });
    assert.deepEqual([...conflictIds([timed, twin])].sort(), [timed.id, 'twin@x'].sort());
  });
});
