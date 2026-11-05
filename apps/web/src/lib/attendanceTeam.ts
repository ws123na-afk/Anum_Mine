// Team attendance helpers (docs/attendance.md#team-attendance): pure, so `pnpm test:unit` covers them.
// Every row comes from GET /attendance/team: the signed-in person and the colleagues who share their record
// with them. Nothing here invents a person, a status or an hour; a value the API did not send shows "-".
import type { AttendanceDailyReport, AttendanceFlag, AttendanceTeamRow, TeamPerson } from '@anum/contracts';
import { ATTENTION, clock } from './attendance.ts';
import { t, type MessageKey } from './i18n.ts';

/** What a roster row says about the person on the chosen day. */
export type RowState = 'on_site' | 'remote' | 'on_break' | 'left' | 'not_checked_in';

export function rowState(row: AttendanceTeamRow): RowState {
  const s = row.status;
  if (s?.on_break) return 'on_break';
  if (s?.at_site) return 'on_site';
  if (s?.remote) return 'remote';
  return row.first_check_in ? 'left' : 'not_checked_in';
}

/** Present: on site, remote or on a break now; for a past day, anyone who worked that day. */
export function isPresent(row: AttendanceTeamRow): boolean {
  if (row.status) return row.status.at_site || row.status.remote || row.status.on_break;
  return row.worked_seconds > 0 || row.presence_seconds > 0;
}

export interface TeamFigures {
  people: number;
  present: number;
  remote: number;
  notIn: number;
  checkedIn: number;
  latest: string | null;
  latestZone: string | null;
  reportsVisible: number;
  reportsSubmitted: number;
  myReportOpen: boolean;
  flagged: number;
}

/** The KPI row and "Today at a glance" from the roster rows the API returned (never more). */
export function teamFigures(rows: readonly AttendanceTeamRow[]): TeamFigures {
  const checked = rows.filter((r) => r.first_check_in);
  const latestRow = checked.reduce<AttendanceTeamRow | null>((best, r) => (!best || (r.first_check_in ?? '') > (best.first_check_in ?? '') ? r : best), null);
  const visible = rows.filter((r) => r.report !== null);
  const me = rows.find((r) => r.me);
  return {
    people: rows.length,
    present: rows.filter(isPresent).length,
    remote: rows.filter((r) => r.status?.remote).length,
    notIn: rows.filter((r) => !r.first_check_in).length,
    checkedIn: checked.length,
    latest: latestRow?.first_check_in ?? null,
    latestZone: latestRow?.time_zone ?? null,
    reportsVisible: visible.length,
    reportsSubmitted: visible.filter((r) => r.report?.status === 'submitted').length,
    myReportOpen: Boolean(me && me.report && me.report.status !== 'submitted'),
    flagged: rows.filter((r) => r.flags.some((f) => ATTENTION.has(f as AttendanceFlag))).length,
  };
}

export const toReview = (f: TeamFigures): number => (f.myReportOpen ? 1 : 0) + f.flagged;

export const percent = (part: number, whole: number): number => (whole > 0 ? Math.round((part / whole) * 100) : 0);

/** Worked time as a share of the person's scheduled shift for the day, 0 to 100; null without a schedule. */
export function shiftShare(row: AttendanceTeamRow): number | null {
  if (!row.scheduled_seconds) return null;
  return Math.min(100, Math.round((row.worked_seconds / row.scheduled_seconds) * 100));
}

/** The person's name from Team people, then their role title, never an invented name. */
export function personName(userId: string, people: readonly TeamPerson[], me: boolean): string {
  const found = people.find((p) => p.user_id === userId)?.display_name;
  if (found) return me ? t('attTeam.you', { name: found }) : found;
  return me ? t('attTeam.me') : userId;
}

export function personRole(row: AttendanceTeamRow, people: readonly TeamPerson[]): string {
  return people.find((p) => p.user_id === row.user_id)?.job_title || row.role_title || '';
}

/** Two letters for the avatar: first and last word's first letter ("Aimal Khan" is "AK"). */
export function initials(name: string): string {
  // Letters only: a translated "(you)" and the bidi isolation marks around names never become an initial.
  const words = name.replace(/\(.*?\)/g, '').split(/\s+/).map((w) => Array.from(w).filter((c) => /\p{L}/u.test(c))).filter((w) => w.length);
  if (!words.length) return '?';
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : words[0][1] ?? '';
  return (first + last).toUpperCase();
}

/** Site names seen in the roster, for the site picker, sorted, case-insensitively unique. */
export function siteOptions(rows: readonly AttendanceTeamRow[]): string[] {
  const seen = new Map<string, string>();
  for (const name of rows.flatMap((r) => r.site_names)) if (!seen.has(name.toLowerCase())) seen.set(name.toLowerCase(), name);
  return [...seen.values()].sort((a, b) => a.localeCompare(b));
}

export function filterBySite(rows: readonly AttendanceTeamRow[], site: string): AttendanceTeamRow[] {
  if (!site) return [...rows];
  const wanted = site.toLowerCase();
  return rows.filter((r) => r.site_names.some((n) => n.toLowerCase() === wanted));
}

/** "12:31 to 13:02 (31 min)" for one break, in the person's zone. */
export function breakText(row: AttendanceTeamRow): string {
  if (!row.breaks.length) return '-';
  const first = row.breaks[0];
  const range = first.end ? t('attTeam.breakRange', { start: clock(first.start, row.time_zone), end: clock(first.end, row.time_zone) }) : t('attTeam.breakOpen', { start: clock(first.start, row.time_zone) });
  return row.breaks.length > 1 ? t('attTeam.breakMore', { range, count: row.breaks.length - 1 }) : range;
}

export const sourceLabel = (source: AttendanceTeamRow['first_check_in_source']): string => (source ? t(`attTeam.source.${source}` as MessageKey) : '');

// ---- reports -----------------------------------------------------------------------------------------

export type ReportStatus = 'draft' | 'submitted';
export const reportStatus = (report: AttendanceDailyReport): ReportStatus => (report.submitted_at ? 'submitted' : 'draft');

export interface ContributorReport { report: AttendanceDailyReport; userId: string; me: boolean }

/** One list of everyone's readable reports, newest day first, then by name. */
export function mergeReports(lists: readonly { userId: string; me: boolean; items: readonly AttendanceDailyReport[] }[]): ContributorReport[] {
  return lists.flatMap((l) => l.items.map((report) => ({ report, userId: l.userId, me: l.me })))
    .sort((a, b) => b.report.day.localeCompare(a.report.day) || Number(b.me) - Number(a.me) || a.userId.localeCompare(b.userId));
}

export function filterReports(rows: readonly ContributorReport[], status: '' | ReportStatus): ContributorReport[] {
  return status ? rows.filter((r) => reportStatus(r.report) === status) : [...rows];
}

/** The short "completed work" line a table shows. */
export function summaryOf(report: AttendanceDailyReport): string {
  const text = report.tasks_done.join('; ') || report.project || report.notes || '';
  return text.length > 160 ? `${text.slice(0, 157)}...` : text;
}

/** The quick report form's three fields mapped onto the report's own fields, keeping everything else. */
export interface QuickReport { done: string; blocker: string; next: string }

export function quickFrom(report: AttendanceDailyReport | null): QuickReport {
  return { done: report?.tasks_done.join('\n') ?? '', blocker: report?.issues ?? '', next: report?.tasks_pending.join('\n') ?? '' };
}

// ---- corrections -------------------------------------------------------------------------------------

export const REASON_PRESETS = ['forgot_in', 'forgot_out', 'wrong_time', 'offline', 'worked_late', 'other'] as const;
export type ReasonPreset = typeof REASON_PRESETS[number];

/** The reason the API stores: the chosen reason, then the details when given. */
export function reasonText(preset: ReasonPreset | '', details: string): string {
  const extra = details.trim();
  if (!preset) return extra;
  const base = t(`attTeam.reason.${preset}` as MessageKey);
  if (preset === 'other') return extra;
  return extra ? `${base}. ${extra}` : base;
}

// ---- export -----------------------------------------------------------------------------------------

function cell(raw: unknown): string {
  let text = raw === null || raw === undefined ? '' : String(raw);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The roster as shown, for a spreadsheet: formula-safe, one row per person, hours as decimals. */
export function rosterCsv(rows: readonly AttendanceTeamRow[], people: readonly TeamPerson[]): string {
  const head = ['person', 'day', 'time_zone', 'sites', 'first_check_in', 'source', 'break_minutes', 'worked_hours', 'scheduled_hours', 'state', 'daily_report'];
  const lines = rows.map((r) => [
    personName(r.user_id, people, r.me), r.day, r.time_zone, r.site_names.join('; '), r.first_check_in ? clock(r.first_check_in, r.time_zone) : '',
    r.first_check_in_source ?? '', Math.round(r.break_seconds / 60), (r.worked_seconds / 3600).toFixed(2), (r.scheduled_seconds / 3600).toFixed(2),
    rowState(r), r.report ? r.report.status : 'not_shared',
  ].map(cell).join(','));
  return [head.join(','), ...lines].join('\r\n') + '\r\n';
}
