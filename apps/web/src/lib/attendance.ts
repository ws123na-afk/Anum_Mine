// Work attendance helpers (docs/attendance.md#web): pure, so `pnpm test:unit` covers them without a browser.
// Hours, clock times in the record's own time zone, the timeline rows, form checks and the tabs.
import type {
  AttendanceDailyReport, AttendanceDailyReportInput, AttendanceDay, AttendanceEvent, AttendanceEventKind, AttendanceFlag,
  AttendanceShift, AttendanceSite, AttendanceToday,
} from '@anum/contracts';
import { formatNumber, t, type MessageKey } from './i18n.ts';

// `workspace` (the manager view, docs/attendance.md#manager-view) is shown to workspace owners and organisation administrators only.
export const ATTENDANCE_TABS = ['team', 'timesheet', 'daily', 'corrections', 'settings', 'workspace'] as const;
export type AttendanceTab = typeof ATTENDANCE_TABS[number];

/** Links made before the redesign keep working: Today and Reports are both in My timesheet now. */
const LEGACY_TABS: Record<string, AttendanceTab> = { today: 'timesheet', reports: 'timesheet' };

export function tabFromHash(hash: string): AttendanceTab {
  const id = hash.replace(/^#attendance-?/, '');
  if ((ATTENDANCE_TABS as readonly string[]).includes(id)) return id as AttendanceTab;
  return LEGACY_TABS[id] ?? 'team';
}
export const hashForTab = (tab: AttendanceTab): string => (tab === 'team' ? '#attendance' : `#attendance-${tab}`);

/** "7 h 30 min", "45 min", "0 min"; "-" for no value. Never rounds a part day up to an hour. */
export function hours(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '-';
  const minutes = Math.floor(Math.max(0, seconds) / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return t('att.minutes', { m: formatNumber(m) });
  return m ? t('att.hoursMinutes', { h: formatNumber(h), m: formatNumber(m) }) : t('att.hours', { h: formatNumber(h) });
}

/** Decimal hours for figures ("7.50"). */
export const decimalHours = (seconds: number): string => (Math.max(0, seconds) / 3600).toFixed(2);

/** A clock time in the record's own zone (an overnight shift or a trip abroad shows where it happened). */
export function clock(iso: string | null | undefined, timeZone: string, locale = 'en-GB'): string {
  if (!iso) return '-';
  try {
    return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hour12: false, timeZone }).format(new Date(iso));
  } catch {
    return new Date(iso).toISOString().slice(11, 16);
  }
}

/** Today in a time zone, as YYYY-MM-DD. */
export function dayIn(timeZone: string, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone }).formatToParts(now);
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

export function shiftDay(day: string, delta: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + delta);
  return date.toISOString().slice(0, 10);
}

export const flagLabel = (flag: AttendanceFlag): string => t(`att.flag.${flag}` as MessageKey);
export const kindLabel = (kind: AttendanceEventKind): string => t(`att.kind.${kind}` as MessageKey);
export const weekdayLabel = (weekday: number): string => t(`att.weekday.${weekday}` as MessageKey);

/** Flags that need the person's attention (a correction may fix them). */
export const ATTENTION: ReadonlySet<AttendanceFlag> = new Set(['missing_checkout', 'no_arrival', 'poor_location', 'leave_without_arrival']);

export function siteName(sites: readonly AttendanceSite[], id: string | null | undefined): string {
  if (!id) return '-';
  return sites.find((s) => s.id === id)?.name ?? t('att.site.unknown');
}

/** What the person is doing now, for the status line. */
export function statusText(today: AttendanceToday, sites: readonly AttendanceSite[]): string {
  const s = today.status;
  if (s.on_break) return t('att.status.break');
  if (s.at_site) return t('att.status.atSite', { site: siteName(sites, s.site_id) });
  if (s.remote) return t('att.status.remote');
  return t('att.status.out');
}

/** The buttons the today view offers, from the current state (never a contradiction such as "check out" when out). */
export function nextActions(today: AttendanceToday): AttendanceEventKind[] {
  const s = today.status;
  if (s.on_break) return ['break_end'];
  const working = s.at_site || s.remote;
  const out: AttendanceEventKind[] = [];
  if (s.at_site) out.push('leave');
  else out.push('arrive');
  if (s.remote) out.push('remote_end');
  else if (!s.at_site) out.push('remote_start');
  if (working) out.push('break_start');
  return out;
}

/** A fresh idempotency key per press (a retry of the same press reuses it). */
export function newKey(prefix = 'web'): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`;
}

export interface TimelineRow { id: string; time: string; label: string; site: string; tags: string[] }

/** The events of a day as timeline rows, oldest first, in each event's own zone. */
export function timeline(events: readonly AttendanceEvent[], sites: readonly AttendanceSite[]): TimelineRow[] {
  return [...events].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)).map((e) => {
    const tags: string[] = [];
    if (e.source === 'device') tags.push(t('att.source.device'));
    if (e.source === 'manual') tags.push(t('att.source.manual'));
    if (e.quality === 'poor') tags.push(t('att.flag.poor_location'));
    if (e.status !== 'active') tags.push(t(`att.status.${e.status}` as MessageKey));
    if (Date.parse(e.received_at) - Date.parse(e.occurred_at) > 15 * 60_000) tags.push(t('att.flag.late_event'));
    return { id: e.id, time: clock(e.occurred_at, e.time_zone), label: kindLabel(e.kind), site: e.site_id ? siteName(sites, e.site_id) : '', tags };
  });
}

// ---- forms ---------------------------------------------------------------------------------------

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

export function checkShift(shift: AttendanceShift): string | null {
  if (!CLOCK.test(shift.start) || !CLOCK.test(shift.end)) return t('att.form.clock');
  if (shift.start === shift.end) return t('att.form.sameTimes');
  return null;
}

export const isOvernight = (shift: AttendanceShift): boolean => shift.end <= shift.start;

export function checkReason(reason: string): Checked<string> {
  const value = reason.trim();
  return value.length >= 3 ? { ok: true, value } : { ok: false, error: t('att.form.reason') };
}

/** A local date and time typed in the form, as an ISO instant with the offset of `timeZone`. */
export function localToIso(day: string, time: string, timeZone: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !CLOCK.test(time)) return null;
  const guess = new Date(`${day}T${time}:00Z`);
  const offset = (instant: Date) => {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).formatToParts(instant);
    const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
    const asUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second'));
    return asUtc - instant.getTime();
  };
  try {
    const first = new Date(guess.getTime() - offset(guess));
    const second = new Date(guess.getTime() - offset(first));
    return second.toISOString();
  } catch {
    return null;
  }
}

/** An instant's local date and clock time in one zone: the inverse of `localToIso` for the correction form. */
export function localParts(iso: string, timeZone: string): { day: string; time: string } {
  return { day: dayIn(timeZone, new Date(iso)), time: clock(iso, timeZone) };
}

export function lines(text: string): string[] {
  return text.split('\n').map((line) => line.trim()).filter(Boolean);
}

export interface ReportDraft {
  project: string; done: string; pending: string; visits: string; issues: string; minutes: string;
  priority: AttendanceDailyReport['priority']; followUp: string; notes: string; fileIds: string[]; ai: boolean;
}

export const emptyReportDraft = (): ReportDraft => ({ project: '', done: '', pending: '', visits: '', issues: '', minutes: '', priority: 'normal', followUp: '', notes: '', fileIds: [], ai: false });

export function draftFromReport(report: AttendanceDailyReport): ReportDraft {
  return {
    project: report.project ?? '', done: report.tasks_done.join('\n'), pending: report.tasks_pending.join('\n'),
    visits: report.site_visits.map((v) => v.name).join('\n'), issues: report.issues ?? '',
    minutes: report.time_spent_minutes === null ? '' : String(report.time_spent_minutes), priority: report.priority,
    followUp: report.follow_up_on ?? '', notes: report.notes ?? '', fileIds: [...report.file_ids], ai: report.drafted_with_ai,
  };
}

export function reportBody(draft: ReportDraft, sites: readonly AttendanceSite[], version: number | null): Checked<AttendanceDailyReportInput> {
  const minutesText = draft.minutes.trim();
  const minutes = minutesText === '' ? null : Number(minutesText);
  if (minutes !== null && (!Number.isInteger(minutes) || minutes < 0 || minutes > 1440)) return { ok: false, error: t('att.form.minutes') };
  const done = lines(draft.done);
  const pending = lines(draft.pending);
  if (!done.length && !pending.length && !draft.notes.trim() && !draft.issues.trim()) return { ok: false, error: t('att.form.reportEmpty') };
  return {
    ok: true,
    value: {
      project: draft.project.trim() || null, tasks_done: done, tasks_pending: pending,
      site_visits: lines(draft.visits).map((name) => ({ name, site_id: sites.find((s) => s.name.toLowerCase() === name.toLowerCase())?.id ?? null, note: null })),
      issues: draft.issues.trim() || null, time_spent_minutes: minutes, priority: draft.priority, follow_up_on: draft.followUp || null,
      notes: draft.notes.trim() || null, file_ids: draft.fileIds, drafted_with_ai: draft.ai, expected_version: version,
    },
  };
}

/** The summary band's numbers for a list of days. */
export function dayTotals(days: readonly AttendanceDay[]): { worked: number; verified: number; presence: number; flagged: number } {
  return days.reduce((acc, d) => ({
    worked: acc.worked + d.worked_seconds, verified: acc.verified + d.verified_seconds, presence: acc.presence + d.presence_seconds,
    flagged: acc.flagged + (d.flags.some((f) => ATTENTION.has(f)) ? 1 : 0),
  }), { worked: 0, verified: 0, presence: 0, flagged: 0 });
}

/** The Shortcut request body for an arrive or leave automation (docs/attendance.md#iphone-shortcuts). */
export function shortcutBody(event: 'arrive' | 'leave', site: string): string {
  return JSON.stringify({ event, site, idempotency_key: '<Shortcut: Current Date as ISO 8601>-' + event }, null, 2);
}

export const ZONES = ['Asia/Dubai', 'Asia/Riyadh', 'Asia/Qatar', 'Asia/Kuwait', 'Asia/Bahrain', 'Asia/Muscat', 'Asia/Karachi', 'Asia/Kolkata', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'UTC'];
