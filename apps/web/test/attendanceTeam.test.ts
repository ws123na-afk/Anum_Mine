// Unit tests for the team attendance helpers (src/lib/attendanceTeam.ts, docs/attendance.md#team-attendance).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AttendanceDailyReport, AttendanceTeamRow, TeamPerson } from '@anum/contracts';
import {
  breakText, filterBySite, filterReports, initials, isPresent, mergeReports, percent, personName, quickFrom, reasonText, rosterCsv, rowState, shiftShare,
  siteOptions, summaryOf, teamFigures, toReview,
} from '../src/lib/attendanceTeam.ts';
import { setLocale } from '../src/lib/i18n.ts';

const row = (userId: string, extra: Partial<AttendanceTeamRow> = {}): AttendanceTeamRow => ({
  user_id: userId, me: false, day: '2026-11-05', time_zone: 'Asia/Riyadh', role_title: null,
  status: { at_site: false, site_id: null, on_break: false, remote: false, since: null }, site_names: [], first_check_in: null, first_check_in_source: null,
  breaks: [], break_seconds: 0, worked_seconds: 0, presence_seconds: 0, remote_seconds: 0, scheduled_seconds: 0, open: false, flags: [], reports_shared: false, report: null, ...extra,
});
const report = (day: string, extra: Partial<AttendanceDailyReport> = {}): AttendanceDailyReport => ({
  id: `r-${day}`, day, project: null, tasks_done: [], tasks_pending: [], site_visits: [], issues: null, time_spent_minutes: null, priority: 'normal', follow_up_on: null,
  notes: null, file_ids: [], drafted_with_ai: false, version: 1, created_at: '', updated_at: '', submitted_at: null, ...extra,
});
const people: TeamPerson[] = [{ user_id: 'u_aimal', role: 'member', display_name: 'Aimal Khan', has_profile: false, email: null, department: null, job_title: 'Fleet supervisor', capacity_hours: null, timezone: 'Asia/Riyadh', assignable: true }];

describe('team attendance helpers', () => {
  test('state and presence come only from what the API sent', () => {
    setLocale('en');
    assert.equal(rowState(row('a', { status: { at_site: true, site_id: 's', on_break: false, remote: false, since: null } })), 'on_site');
    assert.equal(rowState(row('a', { status: { at_site: true, site_id: 's', on_break: true, remote: false, since: null } })), 'on_break');
    assert.equal(rowState(row('a', { status: { at_site: false, site_id: null, on_break: false, remote: true, since: null } })), 'remote');
    assert.equal(rowState(row('a', { first_check_in: '2026-11-05T05:07:00Z' })), 'left');
    assert.equal(rowState(row('a')), 'not_checked_in');
    // A past day has no "now": present means worked that day.
    assert.equal(isPresent(row('a', { status: null, worked_seconds: 60 })), true);
    assert.equal(isPresent(row('a', { status: null })), false);
  });

  test('figures count the rows shown, reports only when shared, and my unsubmitted report', () => {
    const rows = [
      row('me', { me: true, reports_shared: true, first_check_in: '2026-11-05T05:07:00Z', report: { status: 'draft', updated_at: null, submitted_at: null, summary: null },
        status: { at_site: true, site_id: 's', on_break: false, remote: false, since: null } }),
      row('u_aimal', { reports_shared: true, first_check_in: '2026-11-05T05:22:00Z', report: { status: 'submitted', updated_at: null, submitted_at: '2026-11-05T10:27:00Z', summary: 'x' },
        status: { at_site: false, site_id: null, on_break: false, remote: true, since: null }, flags: ['missing_checkout'] }),
      row('u_hours_only'),
    ];
    const f = teamFigures(rows);
    assert.deepEqual([f.people, f.present, f.remote, f.notIn, f.checkedIn], [3, 2, 1, 1, 2]);
    assert.equal(f.latest, '2026-11-05T05:22:00Z');
    assert.deepEqual([f.reportsVisible, f.reportsSubmitted, f.myReportOpen, f.flagged], [2, 1, true, 1]);
    assert.equal(toReview(f), 2);
    assert.equal(percent(2, 3), 67);
    assert.equal(percent(1, 0), 0);
  });

  test('shift share, breaks, names, initials and site filter', () => {
    setLocale('en');
    assert.equal(shiftShare(row('a', { worked_seconds: 4 * 3600, scheduled_seconds: 8 * 3600 })), 50);
    assert.equal(shiftShare(row('a', { worked_seconds: 10 * 3600, scheduled_seconds: 8 * 3600 })), 100);
    assert.equal(shiftShare(row('a', { worked_seconds: 3600 })), null);
    assert.equal(breakText(row('a')), '-');
    assert.equal(breakText(row('a', { breaks: [{ start: '2026-11-05T09:31:00Z', end: '2026-11-05T10:02:00Z' }] })), '12:31 to 13:02');
    assert.equal(personName('u_aimal', people, false), 'Aimal Khan');
    assert.equal(personName('u_aimal', people, true), 'Aimal Khan (you)');
    assert.equal(personName('u_unknown', people, false), 'u_unknown');
    assert.equal(initials('Aimal Khan'), 'AK');
    assert.equal(initials('Aimal Khan (you)'), 'AK');
    assert.equal(initials('Sana'), 'SA');
    assert.equal(initials('\u2068Shahzeb Rahman\u2069 (\u0623\u0646\u062a)'), 'SR');
    assert.equal(initials('\u2069'), '?');
    const rows = [row('a', { site_names: ['Riyadh Office'] }), row('b', { site_names: ['riyadh office', 'Depot'] }), row('c')];
    assert.deepEqual(siteOptions(rows), ['Depot', 'Riyadh Office']);
    assert.deepEqual(filterBySite(rows, 'RIYADH OFFICE').map((r) => r.user_id), ['a', 'b']);
    assert.equal(filterBySite(rows, '').length, 3);
  });

  test('reports merge newest first, filter by status and summarise', () => {
    const merged = mergeReports([
      { userId: 'u_b', me: false, items: [report('2026-11-04'), report('2026-11-05', { submitted_at: '2026-11-05T10:00:00Z' })] },
      { userId: 'me', me: true, items: [report('2026-11-05')] },
    ]);
    assert.deepEqual(merged.map((m) => `${m.report.day}:${m.userId}`), ['2026-11-05:me', '2026-11-05:u_b', '2026-11-04:u_b']);
    assert.equal(filterReports(merged, 'submitted').length, 1);
    assert.equal(filterReports(merged, 'draft').length, 2);
    assert.equal(summaryOf(report('d', { tasks_done: ['One', 'Two'] })), 'One; Two');
    assert.equal(summaryOf(report('d', { tasks_done: ['x'.repeat(200)] })).length, 160);
    assert.deepEqual(quickFrom(report('d', { tasks_done: ['a', 'b'], issues: 'Waiting', tasks_pending: ['c'] })), { done: 'a\nb', blocker: 'Waiting', next: 'c' });
    assert.deepEqual(quickFrom(null), { done: '', blocker: '', next: '' });
  });

  test('a correction reason is the chosen reason plus details; Other needs the details', () => {
    setLocale('en');
    assert.equal(reasonText('forgot_out', ''), 'Forgot to check out');
    assert.equal(reasonText('forgot_out', ' Left at 17:00 '), 'Forgot to check out. Left at 17:00');
    assert.equal(reasonText('other', ''), '');
    assert.equal(reasonText('other', 'Site gate was closed'), 'Site gate was closed');
    assert.equal(reasonText('', 'typed'), 'typed');
  });

  test('the roster CSV is formula-safe and says when reports are not shared', () => {
    const csv = rosterCsv([row('u_aimal', { site_names: ['=HYPERLINK("x")'], worked_seconds: 5400, report: null })], people);
    const [head, line] = csv.trim().split('\r\n');
    assert.match(head, /^person,day,time_zone,sites/);
    assert.match(line, /^Aimal Khan,2026-11-05,Asia\/Riyadh,"'=HYPERLINK\(""x""\)"/);
    assert.match(line, /,1\.50,0\.00,not_checked_in,not_shared$/);
  });
});
