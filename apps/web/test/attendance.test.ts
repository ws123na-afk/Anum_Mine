// Unit tests for the attendance helpers (src/lib/attendance.ts, docs/attendance.md#web).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AttendanceEvent, AttendanceSite, AttendanceToday } from '@anum/contracts';
import {
  checkReason, checkShift, clock, dayIn, dayTotals, decimalHours, hashForTab, hours, isOvernight, localParts, localToIso, newKey, nextActions, reportBody,
  emptyReportDraft, shiftDay, statusText, tabFromHash, timeline,
} from '../src/lib/attendance.ts';
import { setLocale } from '../src/lib/i18n.ts';
import { attendanceAr } from '../src/lib/messages/attendance.ar.ts';
import { attendanceEn } from '../src/lib/messages/attendance.en.ts';
import { viewFromHash } from '../src/lib/shell.ts';

const site = (id: string, name: string): AttendanceSite => ({ id, name, kind: 'workplace', address_label: null, radius_m: 150, archived: false, created_at: '', updated_at: '' });
const today = (status: Partial<AttendanceToday['status']>): AttendanceToday => ({
  day: '2026-11-04', time_zone: 'Asia/Dubai', status: { at_site: false, site_id: null, on_break: false, remote: false, since: null, ...status },
  totals: null, events: [], tracking_paused: false, location_consent: false, profile_saved: true,
});
const event = (id: string, kind: AttendanceEvent['kind'], at: string, extra: Partial<AttendanceEvent> = {}): AttendanceEvent => ({
  id, kind, occurred_at: at, received_at: at, time_zone: 'Asia/Dubai', site_id: null, source: 'web', quality: 'ok', status: 'active', note: null, ...extra,
});

describe('attendance helpers', () => {
  test('hours never round up and show "-" for no value', () => {
    setLocale('en');
    assert.equal(hours(null), '-');
    assert.equal(hours(59), '0 min');
    assert.equal(hours(3600), '1 h');
    assert.equal(hours(27_000 + 59), '7 h 30 min');
    assert.equal(decimalHours(27_000), '7.50');
  });

  test('clock times are shown in the zone the event was recorded in', () => {
    assert.equal(clock('2026-11-04T04:00:00Z', 'Asia/Dubai'), '08:00');
    assert.equal(clock('2026-11-04T04:00:00Z', 'Asia/Riyadh'), '07:00');
    assert.equal(clock(null, 'Asia/Dubai'), '-');
    assert.equal(dayIn('Asia/Dubai', new Date('2026-11-04T21:30:00Z')), '2026-11-05');
    assert.equal(shiftDay('2026-03-01', -1), '2026-02-28');
  });

  test('a typed local time becomes the right instant across a DST change', () => {
    assert.equal(localToIso('2026-11-04', '08:00', 'Asia/Dubai'), '2026-11-04T04:00:00.000Z');
    assert.equal(localToIso('2026-07-01', '09:00', 'Europe/London'), '2026-07-01T08:00:00.000Z');
    assert.equal(localToIso('2026-12-01', '09:00', 'Europe/London'), '2026-12-01T09:00:00.000Z');
    assert.equal(localToIso('2026-11-04', '25:00', 'Asia/Dubai'), null);
  });

  test('the buttons follow the state, never contradicting it', () => {
    assert.deepEqual(nextActions(today({})), ['arrive', 'remote_start']);
    assert.deepEqual(nextActions(today({ at_site: true, site_id: 's' })), ['leave', 'break_start']);
    assert.deepEqual(nextActions(today({ remote: true })), ['arrive', 'remote_end', 'break_start']);
    assert.deepEqual(nextActions(today({ at_site: true, on_break: true })), ['break_end']);
    assert.equal(statusText(today({ at_site: true, site_id: 's1' }), [site('s1', 'Head office')]), 'At Head office');
  });

  test('the timeline sorts by time and marks phone, corrected, weak and late events', () => {
    const rows = timeline([
      event('b', 'leave', '2026-11-04T12:00:00Z', { source: 'manual', site_id: 's1' }),
      event('a', 'arrive', '2026-11-04T04:00:00Z', { source: 'device', quality: 'poor', received_at: '2026-11-04T05:00:00Z', site_id: 's1' }),
    ], [site('s1', 'Head office')]);
    assert.deepEqual(rows.map((r) => [r.time, r.label, r.site]), [['08:00', 'Arrived', 'Head office'], ['16:00', 'Left', 'Head office']]);
    assert.deepEqual(rows[0].tags, ['iPhone', 'Weak location signal', 'Arrived late from the phone']);
    assert.deepEqual(rows[1].tags, ['Corrected']);
  });

  test('forms: shifts, overnight, reasons and reports', () => {
    assert.equal(checkShift({ weekday: 0, start: '09:00', end: '17:00' }), null);
    assert.ok(checkShift({ weekday: 0, start: '9', end: '17:00' }));
    assert.ok(checkShift({ weekday: 0, start: '09:00', end: '09:00' }));
    assert.equal(isOvernight({ weekday: 0, start: '22:00', end: '06:00' }), true);
    assert.equal(checkReason(' x ').ok, false);
    assert.deepEqual(checkReason(' Forgot '), { ok: true, value: 'Forgot' });
    assert.equal(reportBody(emptyReportDraft(), [], null).ok, false);
    const body = reportBody({ ...emptyReportDraft(), done: 'Pumps\n\n Valves ', visits: 'head office', minutes: '90' }, [site('s1', 'Head office')], 2);
    assert.ok(body.ok);
    if (body.ok) {
      assert.deepEqual(body.value.tasks_done, ['Pumps', 'Valves']);
      assert.equal(body.value.site_visits[0].site_id, 's1');
      assert.equal(body.value.expected_version, 2);
    }
    assert.equal(reportBody({ ...emptyReportDraft(), notes: 'x', minutes: '1.5' }, [], null).ok, false);
  });

  test('totals count days that need attention', () => {
    const day = { worked_seconds: 3600, verified_seconds: 0, presence_seconds: 3600, flags: ['missing_checkout'] } as never;
    assert.deepEqual(dayTotals([day, day]), { worked: 7200, verified: 0, presence: 7200, flagged: 2 });
  });

  test('tabs are deep-linkable and keys are fresh per press', () => {
    assert.equal(tabFromHash('#attendance-reports'), 'reports');
    assert.equal(tabFromHash('#attendance'), 'today');
    assert.equal(tabFromHash('#attendance-nope'), 'today');
    assert.equal(hashForTab('daily'), '#attendance-daily');
    assert.equal(viewFromHash('#attendance-settings'), 'attendance');
    assert.notEqual(newKey(), newKey());
    assert.match(newKey(), /^web-[A-Za-z0-9-]{8,}$/);
  });

  test('English and Arabic catalogs have the same keys and the same placeholders', () => {
    assert.deepEqual(Object.keys(attendanceAr).sort(), Object.keys(attendanceEn).sort());
    for (const [key, value] of Object.entries(attendanceEn)) {
      const names = (text: unknown) => [...JSON.stringify(text).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).filter((n) => n !== 'count').sort();
      assert.deepEqual([...new Set(names((attendanceAr as Record<string, unknown>)[key]))], [...new Set(names(value))], key);
    }
    setLocale('ar');
    assert.equal(hours(3600), '1 س');
    setLocale('en');
  });
});

describe('correction form times', () => {
  test('date and time of an event come from the same zone, so saving does not move it a day', () => {
    // 02:00 on 3 November in Dubai is 22:00 on 2 November in UTC.
    const parts = localParts('2026-11-02T22:00:00Z', 'Asia/Dubai');
    assert.deepEqual(parts, { day: '2026-11-03', time: '02:00' });
    assert.equal(localToIso(parts.day, parts.time, 'Asia/Dubai'), '2026-11-02T22:00:00.000Z');
  });
});
