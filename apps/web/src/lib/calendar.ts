// The web Calendar page's pure helpers (docs/calendar.md#web-page): the week or day being shown,
// events grouped by local day, the "New event" form to and from the API body, attendee lines and
// the counts in the summary band. No browser or Vite APIs, so `pnpm test:unit` runs them.

export type EventKind = 'meeting' | 'call' | 'review' | 'reminder' | 'focus';
export type EventStatus = 'scheduled' | 'cancelled';
export type AttendeeResponse = 'invited' | 'accepted' | 'tentative' | 'declined';
export const EVENT_KINDS: readonly EventKind[] = ['meeting', 'call', 'review', 'reminder', 'focus'];
export const ATTENDEE_RESPONSES: readonly AttendeeResponse[] = ['invited', 'accepted', 'tentative', 'declined'];

export interface Attendee { id: string; name: string; email?: string | null; role?: string | null; response: AttendeeResponse }
/** The repeat rule of a series (docs/calendar.md#recurrence): the RFC 5545 subset the API stores. */
export type Frequency = 'daily' | 'weekly' | 'monthly' | 'yearly';
export type Weekday = 'MO' | 'TU' | 'WE' | 'TH' | 'FR' | 'SA' | 'SU';
export const WEEKDAYS: readonly Weekday[] = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
export interface Recurrence { freq: Frequency; interval?: number; by_weekday?: Weekday[]; by_monthday?: number | null; by_setpos?: number | null; until?: string | null; count?: number | null }
/** Which occurrences of a series an edit or a deletion applies to. */
export type EditScope = 'this' | 'following' | 'all';
export interface CalendarEventView {
  id: string; title: string; description?: string | null; location?: string | null; kind: EventKind; status: EventStatus;
  starts_at: string; ends_at: string; conference_url?: string | null; phone?: string | null; attendees: Attendee[];
  source?: string; created_by: string; created_at: string; updated_at: string; can_manage?: boolean;
  // Calendar sync (docs/calendar.md#sync): the connected calendar this event mirrors, if any.
  connection_id?: string | null; external_id?: string | null; synced_at?: string | null; sync_state?: SyncState | null; invitations_pending?: boolean;
  // Repeating, all-day events and reminders (docs/calendar.md#recurrence). An item of a series is one
  // occurrence: `occurrence_start` is its original start (the key of edits and deletions with a scope),
  // `occurrence_key` tells occurrences apart, `overridden` marks one changed on its own.
  all_day?: boolean; timezone?: string | null; recurrence?: Recurrence | null; reminders?: number[]; rrule?: string | null;
  start_date?: string | null; end_date?: string | null; series_id?: string | null;
  occurrence_start?: string | null; occurrence_key?: string; is_recurring?: boolean; overridden?: boolean; override_id?: string | null;
}

/** What tells one shown item from another: the occurrence key of a series item, else the event id. */
export function keyOf(event: Pick<CalendarEventView, 'id' | 'occurrence_key'>): string {
  return event.occurrence_key || event.id;
}

/** Local day-based bounds (ms) of an all-day event, instant bounds of a timed one. An all-day event covers
 * whole local days whatever zone it was made in, so it never leaks into a neighbouring day on the grid. */
export function eventBounds(event: Pick<CalendarEventView, 'starts_at' | 'ends_at' | 'all_day' | 'start_date' | 'end_date'>): { from: number; to: number } {
  if (event.all_day && event.start_date && event.end_date) {
    const [y, m, d] = event.start_date.split('-').map(Number);
    const [y2, m2, d2] = event.end_date.split('-').map(Number);
    return { from: new Date(y, m - 1, d).getTime(), to: new Date(y2, m2 - 1, d2 + 1).getTime() };
  }
  return { from: Date.parse(event.starts_at), to: Date.parse(event.ends_at) };
}
export type SyncState = 'synced' | 'pending_push' | 'local';
export type SyncProvider = 'google' | 'microsoft';
export type ConnectionStatus = 'connected' | 'error' | 'reauth_required';
export interface SyncProviderSetup { provider: SyncProvider; name: string; configured: boolean; missing: string[]; scopes: string[] }
export interface SyncSummary { created?: number; updated?: number; deleted?: number; skipped?: number; pushed?: number; push_failed?: number; conflicts_remote_won?: number; conflicts_local_won?: number; full?: boolean }
export interface SyncConnection {
  id: string; provider: SyncProvider; provider_name: string; email: string; display_name: string | null; connected_by: string; status: ConnectionStatus; fix: string;
  last_sync_at: string | null; last_sync_status: string | null; last_error: string | null; last_summary: SyncSummary; events_synced: number; created_at: string;
}
export interface SyncSetup { providers: SyncProviderSetup[]; redirect_uri: string; can_manage: boolean; connections: SyncConnection[] }
export interface InvitationRequest { status: 'waiting_approval'; summary: string; attendees: string[]; approval: { id: string } }
export interface EventPage { items: CalendarEventView[]; start: string; end: string; truncated: boolean; can_write: boolean }
export interface NextStepAction { kind: 'task' | 'open'; title?: string | null; prompt?: string | null; destination?: 'email_urgent' | 'approvals' | 'event' | null; event_id?: string | null }
export interface NextStep { id: string; title: string; detail: string; icon: 'agenda' | 'follow_up' | 'summary' | 'email' | 'approval'; action: NextStepAction }

/** Week and Day draw a time grid, Month a month of days, Agenda the week as a list. */
export type CalendarMode = 'week' | 'day' | 'month' | 'agenda';
export const CALENDAR_MODES: readonly CalendarMode[] = ['day', 'week', 'month', 'agenda'];

const pad = (n: number) => String(n).padStart(2, '0');

/** Local midnight of `date`. */
export function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/** Local midnight `days` later (calendar days, so a daylight-saving change never shifts it). */
export function addDays(date: Date, days: number): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

/** Monday of the week that holds `date` (weeks run Monday to Sunday, as in the UAE and ISO 8601). */
export function startOfWeek(date: Date): Date {
  const day = startOfDay(date);
  return addDays(day, -((day.getDay() + 6) % 7));
}

/** The local day of `date` as YYYY-MM-DD. */
export function dayKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * The range a mode shows around `anchor`: seven days from Monday (Week and Agenda), the one day, or
 * the six weeks that hold the anchor's month (Month, from the Monday on or before the 1st).
 */
export function visibleRange(mode: CalendarMode, anchor: Date): { start: Date; end: Date; days: Date[] } {
  const start = mode === 'day' ? startOfDay(anchor) : mode === 'month' ? startOfWeek(new Date(anchor.getFullYear(), anchor.getMonth(), 1)) : startOfWeek(anchor);
  const count = mode === 'day' ? 1 : mode === 'month' ? 42 : 7;
  const days = Array.from({ length: count }, (_, index) => addDays(start, index));
  return { start, end: addDays(start, count), days };
}

/** The anchor one step back or forward: a day, a week, or a month (kept on a day the month has). */
export function shiftAnchor(mode: CalendarMode, anchor: Date, step: -1 | 1): Date {
  if (mode === 'month') {
    const target = new Date(anchor.getFullYear(), anchor.getMonth() + step, 1);
    const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
    return new Date(target.getFullYear(), target.getMonth(), Math.min(anchor.getDate(), last));
  }
  return addDays(anchor, step * (mode === 'day' ? 1 : 7));
}

/** Query string for `GET /calendar/events` over a local range. */
export function eventsQuery(start: Date, end: Date, includeCancelled: boolean, limit = 500): string {
  const query = new URLSearchParams({ start: start.toISOString(), end: end.toISOString(), limit: String(limit) });
  if (includeCancelled) query.set('include_cancelled', 'true');
  return query.toString();
}

/**
 * Events on each shown day, earliest first. An event that runs over midnight is listed on every
 * local day it touches inside the range.
 */
export function groupByDay(events: readonly CalendarEventView[], days: readonly Date[]): { day: Date; key: string; events: CalendarEventView[] }[] {
  return days.map((day) => {
    const from = day.getTime();
    const to = addDays(day, 1).getTime();
    const list = events
      .filter((event) => { const b = eventBounds(event); return b.from < to && b.to > from; })
      .sort((a, b) => Number(Boolean(b.all_day)) - Number(Boolean(a.all_day)) || a.starts_at.localeCompare(b.starts_at) || a.title.localeCompare(b.title));
    return { day, key: dayKey(day), events: list };
  });
}

/** Matches a typed filter against the title, location, kind and attendees (case and accents folded). */
export function matchesEvent(event: CalendarEventView, query: string): boolean {
  const fold = (text: string) => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  const q = fold(query.trim());
  if (!q) return true;
  const hay = [event.title, event.location ?? '', event.description ?? '', ...event.attendees.flatMap((a) => [a.name, a.email ?? ''])].join(' ');
  return fold(hay).includes(q);
}

export interface CalendarCounts { today: number; range: number; awaiting: number; cancelled: number }

/** The band's figures over the loaded events: today, the shown range, replies still missing, cancelled. */
export function calendarCounts(events: readonly CalendarEventView[], now: Date): CalendarCounts {
  const from = startOfDay(now).getTime();
  const to = addDays(startOfDay(now), 1).getTime();
  const live = events.filter((event) => event.status !== 'cancelled');
  return {
    today: live.filter((event) => { const b = eventBounds(event); return b.from < to && b.to > from; }).length,
    range: live.length,
    awaiting: live.filter((event) => Date.parse(event.ends_at) > now.getTime()).reduce((sum, event) => sum + event.attendees.filter((a) => a.response === 'invited').length, 0),
    cancelled: events.length - live.length,
  };
}

/** Replies of one event, by response. */
export function replyCounts(event: CalendarEventView): Record<AttendeeResponse, number> {
  const counts: Record<AttendeeResponse, number> = { invited: 0, accepted: 0, tentative: 0, declined: 0 };
  for (const attendee of event.attendees) counts[attendee.response] += 1;
  return counts;
}

// ---- The event form ----------------------------------------------------------------------------

export type RepeatChoice = 'none' | Frequency;
export type RepeatEnd = 'never' | 'until' | 'count';
export interface EventForm {
  title: string; kind: EventKind; date: string; startTime: string; endDate: string; endTime: string;
  location: string; conferenceUrl: string; phone: string; description: string; attendees: string;
  // Repeating, all-day and reminders (docs/calendar.md#recurrence).
  allDay: boolean; timezone: string;
  repeat: RepeatChoice; interval: number; weekdays: Weekday[]; monthlyMode: 'day' | 'weekday'; endMode: RepeatEnd; until: string; count: number;
  reminders: number[];
}

/** The browser's IANA zone (UTC when it cannot be read). */
export function browserZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}

/** The zones the form offers: the browser's, the event's, the Gulf and UTC, then every zone the browser knows. */
export function zoneChoices(...extra: (string | null | undefined)[]): string[] {
  let known: string[] = [];
  try { known = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.('timeZone') ?? []; } catch { known = []; }
  const first = [browserZone(), ...extra.filter((z): z is string => Boolean(z)), 'Asia/Dubai', 'Asia/Riyadh', 'UTC'];
  return [...new Set([...first, ...known])];
}

/** Minutes-before choices for reminders: at the start, minutes, hours, a day, two days, a week. */
export const REMINDER_PRESETS: readonly number[] = [0, 5, 10, 15, 30, 60, 120, 1440, 2880, 10080];
export const MAX_REMINDERS = 5;

const WEEKDAY_OF_DATE = (date: string): Weekday => { const [y, m, d] = date.split('-').map(Number); return WEEKDAYS[(new Date(y, m - 1, d).getDay() + 6) % 7]; };

/** The repeat rule the form describes, or null for a one-off event. Weekly with no day ticked repeats on the start's weekday. */
export function recurrenceFrom(form: EventForm): Recurrence | null {
  if (form.repeat === 'none') return null;
  const rule: Recurrence = { freq: form.repeat, interval: Math.min(99, Math.max(1, Math.round(form.interval) || 1)) };
  if (form.repeat === 'weekly') rule.by_weekday = form.weekdays.length ? WEEKDAYS.filter((d) => form.weekdays.includes(d)) : [WEEKDAY_OF_DATE(form.date)];
  if (form.repeat === 'monthly' && form.monthlyMode === 'weekday') {
    const [y, m, d] = form.date.split('-').map(Number);
    const position = Math.ceil(d / 7);
    const lastOfMonth = new Date(y, m, 0).getDate();
    rule.by_weekday = [WEEKDAY_OF_DATE(form.date)];
    rule.by_setpos = position > 4 || lastOfMonth - d < 7 && position === 4 ? -1 : position;
  }
  if (form.endMode === 'until' && form.until) rule.until = form.until;
  if (form.endMode === 'count') rule.count = Math.min(730, Math.max(1, Math.round(form.count) || 1));
  return rule;
}

/** The form fields of a stored rule. */
export function repeatFields(rule: Recurrence | null | undefined): Pick<EventForm, 'repeat' | 'interval' | 'weekdays' | 'monthlyMode' | 'endMode' | 'until' | 'count'> {
  if (!rule) return { repeat: 'none', interval: 1, weekdays: [], monthlyMode: 'day', endMode: 'never', until: '', count: 10 };
  return {
    repeat: rule.freq, interval: rule.interval ?? 1, weekdays: rule.by_weekday ?? [], monthlyMode: rule.by_setpos != null ? 'weekday' : 'day',
    endMode: rule.until ? 'until' : rule.count ? 'count' : 'never', until: rule.until ?? '', count: rule.count ?? 10,
  };
}

export type Translate = (key: string, vars?: Record<string, string | number>) => string;

/**
 * A readable sentence for a rule: "Every 2 weeks on Monday and Wednesday, until 31 Dec 2026". Weekday and
 * date names come from `weekdayName` / `dateName` so the caller formats them in the page's language.
 */
export function recurrenceSummary(rule: Recurrence | null | undefined, t: Translate, weekdayName: (day: Weekday) => string, dateName: (iso: string) => string, listJoin: (parts: string[]) => string): string {
  if (!rule) return t('calendar.repeat.none');
  const interval = rule.interval ?? 1;
  let base: string;
  if (rule.freq === 'weekly' && rule.by_weekday?.length) base = t(interval === 1 ? 'calendar.repeat.weeklyOn' : 'calendar.repeat.everyWeeksOn', { count: interval, days: listJoin(rule.by_weekday.map(weekdayName)) });
  else if (rule.freq === 'monthly' && rule.by_weekday?.length && rule.by_setpos != null) {
    const position = t(`calendar.repeat.position.${rule.by_setpos === -1 ? 'last' : rule.by_setpos}`);
    base = t(interval === 1 ? 'calendar.repeat.monthlyOnWeekday' : 'calendar.repeat.everyMonthsOnWeekday', { count: interval, position, day: weekdayName(rule.by_weekday[0]) });
  } else if (rule.freq === 'monthly' && rule.by_monthday != null) {
    base = t(interval === 1 ? 'calendar.repeat.monthlyOnDay' : 'calendar.repeat.everyMonthsOnDay', { count: interval, day: rule.by_monthday === -1 ? t('calendar.repeat.lastDay') : String(rule.by_monthday) });
  } else base = t(`calendar.repeat.${interval === 1 ? rule.freq : `every_${rule.freq}`}`, { count: interval });
  if (rule.until) return t('calendar.repeat.until', { rule: base, date: dateName(rule.until) });
  if (rule.count) return t('calendar.repeat.times', { rule: base, count: rule.count });
  return base;
}

/** "10 minutes before", "1 day before", "At the start". */
export function reminderLabel(minutes: number, t: Translate): string {
  if (minutes === 0) return t('calendar.reminder.atStart');
  if (minutes % 10080 === 0) return t('calendar.reminder.weeks', { count: minutes / 10080 });
  if (minutes % 1440 === 0) return t('calendar.reminder.days', { count: minutes / 1440 });
  if (minutes % 60 === 0) return t('calendar.reminder.hours', { count: minutes / 60 });
  return t('calendar.reminder.minutes', { count: minutes });
}

/** A `datetime-local` style value (YYYY-MM-DDTHH:MM) for a date, in local time. */
export function localInput(date: Date): { date: string; time: string } {
  return { date: dayKey(date), time: `${pad(date.getHours())}:${pad(date.getMinutes())}` };
}

/** A new event's form: on `day`, at the next whole hour today (or 09:00 on another day), for an hour. */
export function emptyForm(day: Date, now: Date): EventForm {
  const sameDay = dayKey(day) === dayKey(now);
  const startHour = sameDay ? Math.min(23, now.getHours() + 1) : 9;
  const endHour = Math.min(23, startHour + 1);
  return {
    title: '', kind: 'meeting', date: dayKey(day), startTime: `${pad(startHour)}:00`, endDate: dayKey(day), endTime: startHour === 23 ? '23:59' : `${pad(endHour)}:00`,
    location: '', conferenceUrl: '', phone: '', description: '', attendees: '',
    allDay: false, timezone: browserZone(), ...repeatFields(null), reminders: [],
  };
}

/** The form for editing an existing event. */
export function formFromEvent(event: CalendarEventView): EventForm {
  const start = localInput(new Date(event.starts_at));
  const end = localInput(new Date(event.ends_at));
  const allDay = Boolean(event.all_day && event.start_date && event.end_date);
  return {
    title: event.title, kind: event.kind, date: allDay ? event.start_date! : start.date, startTime: allDay ? '09:00' : start.time, endDate: allDay ? event.end_date! : end.date, endTime: allDay ? '10:00' : end.time,
    location: event.location ?? '', conferenceUrl: event.conference_url ?? '', phone: event.phone ?? '', description: event.description ?? '',
    attendees: formatAttendees(event.attendees),
    allDay, timezone: event.timezone || browserZone(), ...repeatFields(event.recurrence), reminders: [...(event.reminders ?? [])],
  };
}

/** One attendee per line: "Name <email>", "Name, email" or just a name. */
type AttendeeRow = { name: string; email: string | null; id?: string; role?: string | null; response?: AttendeeResponse };

export function parseAttendees(text: string, existing: readonly Attendee[] = []): AttendeeRow[] {
  const rows: AttendeeRow[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const angle = /^(.*?)<\s*([^<>\s]+@[^<>\s]+)\s*>\s*$/.exec(line);
    const comma = /^(.*?),\s*([^,\s]+@[^,\s]+)\s*$/.exec(line);
    const bare = /^([^\s@]+@[^\s@]+\.[^\s@]+)$/.exec(line);
    const match = angle ?? comma;
    const email = match ? match[2].toLowerCase() : bare ? bare[1].toLowerCase() : null;
    const name = (match ? match[1] : bare ? bare[1].split('@')[0] : line).replace(/\s+/g, ' ').trim().replace(/^"|"$/g, '') || (email ?? line);
    const known = existing.find((attendee) => (email && attendee.email?.toLowerCase() === email) || (!email && attendee.name === name));
    rows.push(known ? { name, email, id: known.id, role: known.role ?? null, response: known.response } : { name, email });
  }
  return rows;
}

export function formatAttendees(attendees: readonly Attendee[]): string {
  return attendees.map((attendee) => (attendee.email ? `${attendee.name} <${attendee.email}>` : attendee.name)).join('\n');
}

export type FormProblem = 'title' | 'date' | 'order' | 'link' | 'until' | 'count' | null;

/** What is wrong with the form before it is sent (the API checks again). */
export function formProblem(form: EventForm): FormProblem {
  if (!form.title.trim()) return 'title';
  const day = /^\d{4}-\d{2}-\d{2}$/, time = /^\d{2}:\d{2}$/;
  if (!day.test(form.date) || !day.test(form.endDate)) return 'date';
  if (form.allDay) {
    if (form.endDate < form.date) return 'order';
  } else {
    if (!time.test(form.startTime) || !time.test(form.endTime)) return 'date';
    if (`${form.endDate}T${form.endTime}` <= `${form.date}T${form.startTime}`) return 'order';
  }
  if (form.conferenceUrl.trim() && !/^https:\/\/\S+$/.test(form.conferenceUrl.trim())) return 'link';
  if (form.repeat !== 'none' && form.endMode === 'until' && (!day.test(form.until) || form.until < form.date)) return 'until';
  if (form.repeat !== 'none' && form.endMode === 'count' && !(form.count >= 1 && form.count <= 730)) return 'count';
  return null;
}

/** The local date and time as an instant (ISO with offset Z). */
export function toInstant(date: string, time: string): string {
  const [y, m, d] = date.split('-').map(Number);
  const [h, min] = time.split(':').map(Number);
  return new Date(y, m - 1, d, h, min).toISOString();
}

/**
 * The API body for `POST /calendar/events` or `PATCH /calendar/events/{id}`. An all-day event sends its
 * dates and zone; a one-off timed event sends instants. `scope` and `occurrenceStart` address one
 * occurrence of a series (an edit of the whole series from an occurrence moves it by the same amount).
 */
export function eventBody(form: EventForm, existing: readonly Attendee[] = [], scope?: EditScope, occurrenceStart?: string | null): Record<string, unknown> {
  const text = (value: string) => value.trim() || null;
  const times = form.allDay
    ? { all_day: true, start_date: form.date, end_date: form.endDate, timezone: form.timezone, starts_at: toInstant(form.date, '00:00'), ends_at: toInstant(form.endDate, '23:59') }
    : { all_day: false, starts_at: toInstant(form.date, form.startTime), ends_at: toInstant(form.endDate, form.endTime), timezone: form.timezone };
  const body: Record<string, unknown> = {
    title: form.title.trim(), kind: form.kind, ...times,
    location: text(form.location), conference_url: text(form.conferenceUrl), phone: text(form.phone), description: text(form.description),
    attendees: parseAttendees(form.attendees, existing).map((row) => ({ ...(row.id ? { id: row.id } : {}), name: row.name, email: row.email, ...(row.id ? { role: row.role ?? null } : {}), ...(row.response ? { response: row.response } : {}) })),
    reminders: [...new Set(form.reminders)].sort((a, b) => a - b),
  };
  // One occurrence cannot carry a rule of its own; the whole series and "following" can.
  if (scope !== 'this') body.recurrence = recurrenceFrom(form);
  if (scope) body.scope = scope;
  if (occurrenceStart) body.occurrence_start = occurrenceStart;
  return body;
}

/** Minutes between two instants, for "1 h 30 min". */
export function durationMinutes(event: Pick<CalendarEventView, 'starts_at' | 'ends_at'>): number {
  return Math.max(0, Math.round((Date.parse(event.ends_at) - Date.parse(event.starts_at)) / 60_000));
}

/** Whole days an all-day event covers. */
export function allDayLength(event: Pick<CalendarEventView, 'start_date' | 'end_date'>): number {
  if (!event.start_date || !event.end_date) return 1;
  const [y, m, d] = event.start_date.split('-').map(Number);
  const [y2, m2, d2] = event.end_date.split('-').map(Number);
  return Math.max(1, Math.round((new Date(y2, m2 - 1, d2).getTime() - new Date(y, m - 1, d).getTime()) / 86_400_000) + 1);
}

/** Whether the event is on now, still to come, or over. */
export function eventTiming(event: Pick<CalendarEventView, 'starts_at' | 'ends_at' | 'all_day' | 'start_date' | 'end_date'>, now: Date): 'now' | 'later' | 'past' {
  const t = now.getTime();
  const bounds = eventBounds(event);
  if (bounds.to <= t) return 'past';
  return bounds.from <= t ? 'now' : 'later';
}


/** Where the browser lands after a provider consent: `/?calendar_connect=<id>#calendar` or `calendar_connect_error=<code>`. */
export const CALENDAR_CONNECT_KEY = 'anum.calendar.connect';
export function readCalendarConnectReturn(href: string): { connection: string | null; error: string | null; cleanUrl: string } | null {
  const url = new URL(href);
  const connection = url.searchParams.get('calendar_connect');
  const error = url.searchParams.get('calendar_connect_error');
  if (!connection && !error) return null;
  url.searchParams.delete('calendar_connect');
  url.searchParams.delete('calendar_connect_error');
  const search = url.searchParams.toString();
  return { connection, error, cleanUrl: `${url.pathname}${search ? `?${search}` : ''}${url.hash || '#calendar'}` };
}

/** The connection new events are sent to: the oldest one that works (as the API chooses it). */
export function syncTarget(connections: readonly SyncConnection[]): SyncConnection | null {
  return connections.find((connection) => connection.status === 'connected') ?? null;
}

/** "Synced 5 min ago" in minutes, hours or days; null when it never ran. */
export function minutesSince(value: string | null, now: Date = new Date()): number | null {
  return value ? Math.max(0, Math.round((now.getTime() - Date.parse(value)) / 60_000)) : null;
}

/** The sync state an event shows: where it lives, and whether something waits to be sent. */
export function eventSyncKind(event: Pick<CalendarEventView, 'sync_state' | 'connection_id'>): 'synced' | 'waiting' | 'local' {
  if (event.connection_id && event.sync_state === 'synced') return 'synced';
  if (event.connection_id && event.sync_state === 'pending_push') return 'waiting';
  return 'local';
}

/** Attendees with an email address: the people an invitation would reach. */
export function invitees(event: Pick<CalendarEventView, 'attendees'>): string[] {
  return event.attendees.map((attendee) => attendee.email).filter((email): email is string => Boolean(email));
}

// ---- Time grid, conflicts and the week strip (docs/calendar.md#web-page) ----

/**
 * The wall-clock hour (0 to 24, with fractions) of `ms` on the local `day`: 0 before the day starts,
 * 24 after it ends. Read from the local clock, never from elapsed time since midnight, so a
 * daylight-saving day (23 or 25 hours long) still places 23:30 at 23:30.
 */
export function wallHour(ms: number, day: Date): number {
  if (ms < day.getTime()) return 0;
  if (ms >= addDays(day, 1).getTime()) return 24;
  const at = new Date(ms);
  return at.getHours() + at.getMinutes() / 60 + at.getSeconds() / 3600;
}

/** The hours a time grid draws: 08:00 to 18:00, widened to hold every shown event (whole hours). */
export function hourWindow(events: readonly CalendarEventView[], days: readonly Date[]): { from: number; to: number } {
  let from = 8, to = 18;
  for (const day of days) {
    const dayStart = day.getTime(), dayEnd = addDays(day, 1).getTime();
    for (const event of events) {
      if (event.all_day) continue;  // all-day events sit in their own row above the hours
      const starts = Date.parse(event.starts_at), ends = Date.parse(event.ends_at);
      if (!(starts < dayEnd && (ends > dayStart || starts >= dayStart))) continue;
      const startHour = wallHour(starts, day), endHour = Math.max(wallHour(ends, day), startHour);
      from = Math.min(from, Math.floor(startHour));
      to = Math.max(to, Math.ceil(Math.max(endHour, Math.min(24, startHour + 0.5))));
    }
  }
  return { from: Math.max(0, from), to: Math.min(24, Math.max(to, from + 1)) };
}

export interface GridBlock { event: CalendarEventView; top: number; height: number; lane: number; lanes: number }

/**
 * Events of one day placed on a grid from `from` to `to` o'clock (wall-clock hours): `top` and
 * `height` are fractions of the grid, and events that overlap share the width in lanes (each
 * cluster of overlapping events gets as many lanes as it needs). A very short event keeps a
 * readable minimum height.
 */
export function layoutDay(events: readonly CalendarEventView[], day: Date, from: number, to: number, minHeight = 0.03): GridBlock[] {
  const span = to - from, dayStart = day.getTime(), dayEnd = addDays(day, 1).getTime();
  const placed = events
    .filter((event) => !event.all_day)
    .filter((event) => { const starts = Date.parse(event.starts_at), ends = Date.parse(event.ends_at); return starts < dayEnd && (ends > dayStart || starts >= dayStart); })
    .map((event) => {
      const startHour = wallHour(Date.parse(event.starts_at), day), endHour = Math.max(wallHour(Date.parse(event.ends_at), day), startHour);
      return { event, start: Math.min(Math.max(startHour, from), to), end: Math.min(Math.max(endHour, from), to) };
    })
    .filter((row) => row.end > row.start || (row.start >= from && row.start < to))
    .sort((a, b) => a.start - b.start || b.end - a.end || a.event.title.localeCompare(b.event.title));
  const blocks: GridBlock[] = [];
  let cluster: { block: GridBlock; end: number }[] = [];
  let clusterEnd = -Infinity;
  const close = () => { const lanes = Math.max(1, ...cluster.map((c) => c.block.lane + 1)); for (const c of cluster) c.block.lanes = lanes; cluster = []; };
  for (const row of placed) {
    const end = Math.max(row.end, row.start + span * minHeight);
    if (row.start >= clusterEnd) { close(); clusterEnd = -Infinity; }
    const used = new Set(cluster.filter((c) => c.end > row.start).map((c) => c.block.lane));
    let lane = 0;
    while (used.has(lane)) lane += 1;
    const top = (row.start - from) / span;
    const block: GridBlock = { event: row.event, top, height: Math.min(1 - top, (end - row.start) / span), lane, lanes: 1 };
    blocks.push(block);
    cluster.push({ block, end });
    clusterEnd = Math.max(clusterEnd, end);
  }
  close();
  return blocks;
}

/** Pairs of scheduled events whose times overlap (double bookings), earliest first. */
export function findConflicts(events: readonly CalendarEventView[]): [CalendarEventView, CalendarEventView][] {
  // All-day events never double-book: they hold no hour.
  const live = events.filter((event) => event.status !== 'cancelled' && !event.all_day).slice().sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  const pairs: [CalendarEventView, CalendarEventView][] = [];
  for (let i = 0; i < live.length; i += 1) {
    for (let j = i + 1; j < live.length; j += 1) {
      if (Date.parse(live[j].starts_at) >= Date.parse(live[i].ends_at)) break;
      if (Date.parse(live[j].ends_at) > Date.parse(live[i].starts_at)) pairs.push([live[i], live[j]]);
    }
  }
  return pairs;
}

/** The ids of events in a double booking. */
export function conflictIds(events: readonly CalendarEventView[]): Set<string> {
  return new Set(findConflicts(events).flat().map((event) => keyOf(event)));
}

/** All-day events touching a local day, earliest first. */
export function allDayOn(events: readonly CalendarEventView[], day: Date): CalendarEventView[] {
  const from = day.getTime(), to = addDays(day, 1).getTime();
  return events.filter((event) => event.all_day && eventBounds(event).from < to && eventBounds(event).to > from)
    .sort((a, b) => (a.start_date ?? '').localeCompare(b.start_date ?? '') || a.title.localeCompare(b.title));
}

/** The next scheduled event still to start after `now`, or null. */
export function nextEvent(events: readonly CalendarEventView[], now: Date): CalendarEventView | null {
  return events.filter((event) => event.status !== 'cancelled' && Date.parse(event.starts_at) > now.getTime())
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at))[0] ?? null;
}

/** The figures for the strip under the calendar: events, distinct people invited, double bookings, replies missing. */
export function rangeStats(events: readonly CalendarEventView[], now: Date): { events: number; people: number; conflicts: number; awaiting: number; cancelled: number } {
  const live = events.filter((event) => event.status !== 'cancelled');
  const people = new Set(live.flatMap((event) => event.attendees.map((a) => (a.email ?? a.name).trim().toLowerCase())).filter(Boolean));
  const counts = calendarCounts(events, now);
  return { events: live.length, people: people.size, conflicts: findConflicts(live).length, awaiting: counts.awaiting, cancelled: counts.cancelled };
}

/** The 42 days of the small month calendar that holds `month` (Monday first). */
export function monthDays(month: Date): Date[] {
  const start = startOfWeek(new Date(month.getFullYear(), month.getMonth(), 1));
  return Array.from({ length: 42 }, (_, index) => addDays(start, index));
}

/** Whether `day` falls inside the range a mode shows around `anchor`. */
export function inRange(mode: CalendarMode, anchor: Date, day: Date): boolean {
  const range = visibleRange(mode === 'month' ? 'week' : mode, anchor);
  if (mode === 'month') return day.getMonth() === anchor.getMonth() && day.getFullYear() === anchor.getFullYear();
  return day.getTime() >= range.start.getTime() && day.getTime() < range.end.getTime();
}
