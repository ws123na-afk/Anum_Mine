// The web Calendar page's pure helpers (docs/calendar.md#web-page): the week or day being shown,
// events grouped by local day, the "New event" form to and from the API body, attendee lines and
// the counts in the summary band. No browser or Vite APIs, so `pnpm test:unit` runs them.

export type EventKind = 'meeting' | 'call' | 'review' | 'reminder' | 'focus';
export type EventStatus = 'scheduled' | 'cancelled';
export type AttendeeResponse = 'invited' | 'accepted' | 'tentative' | 'declined';
export const EVENT_KINDS: readonly EventKind[] = ['meeting', 'call', 'review', 'reminder', 'focus'];
export const ATTENDEE_RESPONSES: readonly AttendeeResponse[] = ['invited', 'accepted', 'tentative', 'declined'];

export interface Attendee { id: string; name: string; email?: string | null; role?: string | null; response: AttendeeResponse }
export interface CalendarEventView {
  id: string; title: string; description?: string | null; location?: string | null; kind: EventKind; status: EventStatus;
  starts_at: string; ends_at: string; conference_url?: string | null; phone?: string | null; attendees: Attendee[];
  source?: string; created_by: string; created_at: string; updated_at: string; can_manage?: boolean;
  // Calendar sync (docs/calendar.md#sync): the connected calendar this event mirrors, if any.
  connection_id?: string | null; external_id?: string | null; synced_at?: string | null; sync_state?: SyncState | null; invitations_pending?: boolean;
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

export type CalendarMode = 'week' | 'day';

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

/** The range a mode shows around `anchor`: seven days from Monday, or the one day. */
export function visibleRange(mode: CalendarMode, anchor: Date): { start: Date; end: Date; days: Date[] } {
  const start = mode === 'week' ? startOfWeek(anchor) : startOfDay(anchor);
  const count = mode === 'week' ? 7 : 1;
  const days = Array.from({ length: count }, (_, index) => addDays(start, index));
  return { start, end: addDays(start, count), days };
}

/** The anchor one step back or forward (a week or a day). */
export function shiftAnchor(mode: CalendarMode, anchor: Date, step: -1 | 1): Date {
  return addDays(anchor, step * (mode === 'week' ? 7 : 1));
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
      .filter((event) => Date.parse(event.starts_at) < to && Date.parse(event.ends_at) > from)
      .sort((a, b) => a.starts_at.localeCompare(b.starts_at) || a.title.localeCompare(b.title));
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
    today: live.filter((event) => Date.parse(event.starts_at) < to && Date.parse(event.ends_at) > from).length,
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

export interface EventForm {
  title: string; kind: EventKind; date: string; startTime: string; endDate: string; endTime: string;
  location: string; conferenceUrl: string; phone: string; description: string; attendees: string;
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
  };
}

/** The form for editing an existing event. */
export function formFromEvent(event: CalendarEventView): EventForm {
  const start = localInput(new Date(event.starts_at));
  const end = localInput(new Date(event.ends_at));
  return {
    title: event.title, kind: event.kind, date: start.date, startTime: start.time, endDate: end.date, endTime: end.time,
    location: event.location ?? '', conferenceUrl: event.conference_url ?? '', phone: event.phone ?? '', description: event.description ?? '',
    attendees: formatAttendees(event.attendees),
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

export type FormProblem = 'title' | 'date' | 'order' | 'link' | null;

/** What is wrong with the form before it is sent (the API checks again). */
export function formProblem(form: EventForm): FormProblem {
  if (!form.title.trim()) return 'title';
  const day = /^\d{4}-\d{2}-\d{2}$/, time = /^\d{2}:\d{2}$/;
  if (!day.test(form.date) || !day.test(form.endDate) || !time.test(form.startTime) || !time.test(form.endTime)) return 'date';
  if (`${form.endDate}T${form.endTime}` <= `${form.date}T${form.startTime}`) return 'order';
  if (form.conferenceUrl.trim() && !/^https:\/\/\S+$/.test(form.conferenceUrl.trim())) return 'link';
  return null;
}

/** The local date and time as an instant (ISO with offset Z). */
export function toInstant(date: string, time: string): string {
  const [y, m, d] = date.split('-').map(Number);
  const [h, min] = time.split(':').map(Number);
  return new Date(y, m - 1, d, h, min).toISOString();
}

/** The API body for `POST /calendar/events` or `PATCH /calendar/events/{id}`. */
export function eventBody(form: EventForm, existing: readonly Attendee[] = []): Record<string, unknown> {
  const text = (value: string) => value.trim() || null;
  return {
    title: form.title.trim(), kind: form.kind, starts_at: toInstant(form.date, form.startTime), ends_at: toInstant(form.endDate, form.endTime),
    location: text(form.location), conference_url: text(form.conferenceUrl), phone: text(form.phone), description: text(form.description),
    attendees: parseAttendees(form.attendees, existing).map((row) => ({ ...(row.id ? { id: row.id } : {}), name: row.name, email: row.email, ...(row.id ? { role: row.role ?? null } : {}), ...(row.response ? { response: row.response } : {}) })),
  };
}

/** Minutes between two instants, for "1 h 30 min". */
export function durationMinutes(event: Pick<CalendarEventView, 'starts_at' | 'ends_at'>): number {
  return Math.max(0, Math.round((Date.parse(event.ends_at) - Date.parse(event.starts_at)) / 60_000));
}

/** Whether the event is on now, still to come, or over. */
export function eventTiming(event: Pick<CalendarEventView, 'starts_at' | 'ends_at'>, now: Date): 'now' | 'later' | 'past' {
  const t = now.getTime();
  if (Date.parse(event.ends_at) <= t) return 'past';
  return Date.parse(event.starts_at) <= t ? 'now' : 'later';
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
