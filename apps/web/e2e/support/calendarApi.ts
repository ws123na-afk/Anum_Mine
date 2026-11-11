import type { Page, Route } from '@playwright/test';

// A stateful calendar API (docs/calendar.md) for the Calendar page and Home: events this week with
// attendees and replies, add / edit / cancel / delete, recorded replies and the suggested next steps.
export interface CalendarMockOptions {
  role?: 'owner' | 'viewer'; deny?: boolean; failNextSteps?: boolean; empty?: boolean;
  // Calendar sync (docs/calendar.md#sync): nothing connected, a working Google connection, one that needs
  // reconnecting, or a server without OAuth clients.
  sync?: 'none' | 'connected' | 'reauth' | 'unconfigured'; failSync?: boolean;
  // With `sync: 'connected'`: the first event's sends to Google failed five times and ANUM stopped retrying.
  failedPush?: boolean;
}

// The fixture's "today" is the run day, taken once at midday. A spec that shows the calendar pins the page's clock to the same
// instant (`pinCalendarClock`), so an event at 23:00 is still "today, later" when a CI run crosses midnight (UTC or local) and the
// agenda's week cannot roll over under it. Nothing sleeps or waits for the clock.
const RUN_MIDDAY = (() => { const d = new Date(); d.setHours(12, 0, 0, 0); return d; })();
const at = (dayOffset: number, hour: number, minute = 0) => { const d = new Date(RUN_MIDDAY); d.setDate(d.getDate() + dayOffset); d.setHours(hour, minute, 0, 0); return d.toISOString(); };

/** Makes `Date` in the page read the fixture's midday (timers keep running), so the fixture's days match the page's "today". */
export async function pinCalendarClock(page: Page): Promise<void> {
  await page.clock.setFixedTime(RUN_MIDDAY);
}

export function createCalendarMock(options: CalendarMockOptions = {}) {
  const owner = options.role !== 'viewer';
  const base = { tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', source: 'manual', source_ref: null, created_by: 'user_local', created_at: at(-3, 9), updated_by: null, updated_at: at(-3, 9) };
  const events: Record<string, unknown>[] = options.empty ? [] : [
    { ...base, id: 'evt_1', title: 'Fleet review with the team', kind: 'meeting', status: 'scheduled', ...(options.sync === 'connected' ? { connection_id: 'cal_1', external_id: 'g1', sync_state: 'synced', synced_at: at(-1, 9), invitations_pending: true } : {}),
      ...(options.sync === 'connected' && options.failedPush ? { sync_state: 'pending_push', sync_failed: true, sync_error: 'Google Calendar answered 400: Invalid recurrence rule.' } : {}), starts_at: at(0, 23, 0), ends_at: at(0, 23, 30), location: 'Room 2', conference_url: 'https://meet.example.com/fleet', phone: null, description: 'Tyre spend and the winter plan.',
      attendees: [{ id: 'att_1', name: 'Sara Ali', email: 'sara@example.com', role: 'Fleet manager', response: 'accepted' }, { id: 'att_2', name: 'Omar Haddad', email: 'omar@example.com', role: null, response: 'invited' }] },
    { ...base, id: 'evt_2', title: 'Supplier call: Michelin order', kind: 'call', status: 'scheduled', starts_at: at(0, 1, 0), ends_at: at(0, 1, 45), location: null, conference_url: null, phone: '+971 50 123 4567', description: null, attendees: [] },
    { ...base, id: 'evt_3', title: 'Quarterly budget review', kind: 'review', status: 'cancelled', starts_at: at(0, 2, 0), ends_at: at(0, 3, 0), location: 'Board room', conference_url: null, phone: null, description: null, attendees: [], created_by: 'user_owner' },
  ];
  const connection = (over: Record<string, unknown> = {}) => ({
    id: 'cal_1', provider: 'google', provider_name: 'Google Calendar', email: 'owner@example.com', display_name: 'Owner', connected_by: 'user_local', status: 'connected', fix: '',
    last_sync_at: new Date(RUN_MIDDAY.getTime() - 5 * 60_000).toISOString(), last_sync_status: 'ok', last_error: null,
    last_summary: { created: 2, updated: 1, deleted: 0, pushed: 1, conflicts_remote_won: 1, conflicts_local_won: 0 }, events_synced: 3, created_at: at(-1, 9), ...over,
  });
  const initial = options.sync === 'connected' ? [connection()]
    : options.sync === 'reauth' ? [connection({ status: 'reauth_required', last_sync_status: 'error', last_error: 'Google Calendar answered 400: Token has been expired or revoked.' })] : [];
  const state = {
    events, created: [] as unknown[], updates: [] as unknown[], deleted: [] as string[], responses: [] as unknown[],
    connections: initial as Record<string, unknown>[], syncs: [] as string[], disconnected: [] as string[], invitations: [] as string[], connectStarts: [] as string[],
    retries: [] as string[],
  };
  const providers = () => [
    { provider: 'google', name: 'Google Calendar', configured: options.sync !== 'unconfigured', missing: options.sync === 'unconfigured' ? ['ANUM_CALENDAR_GOOGLE_CLIENT_ID', 'ANUM_CALENDAR_GOOGLE_CLIENT_SECRET'] : [], scopes: ['openid', 'email'] },
    { provider: 'microsoft', name: 'Microsoft 365 Calendar', configured: true, missing: [], scopes: ['Calendars.ReadWrite'] },
  ];
  const view = (event: Record<string, unknown>) => ({ ...event, can_manage: owner && (event.created_by === 'user_local' || owner) });
  // Repeating events (docs/calendar.md#recurrence): daily and weekly rules with a count or an end date, the series'
  // skipped occurrences and child rows that replace one occurrence, expanded the way the API does.
  const DAY = 86_400_000;
  function occurrencesOf(event: Record<string, unknown>, start: number, end: number): Record<string, unknown>[] {
    const rule = event.recurrence as { freq: string; interval?: number; count?: number | null; until?: string | null; by_weekday?: string[] } | null | undefined;
    if (!rule) return [event];
    const first = Date.parse(event.starts_at as string), length = Date.parse(event.ends_at as string) - first;
    const skipped = new Set((event.skipped as string[] | undefined) ?? []);
    const overrides = new Set(state.events.filter((e) => e.series_id === event.id).map((e) => e.recurrence_start as string));
    const out: Record<string, unknown>[] = [];
    const step = (rule.freq === 'weekly' ? 7 : 1) * (rule.interval ?? 1) * DAY;
    for (let n = 0, at = first; n < (rule.count ?? 400) && at < end; n += 1, at += step) {
      if (rule.until && at > Date.parse(`${rule.until}T23:59:59`)) break;
      const iso = new Date(at).toISOString();
      if (at + length <= start || skipped.has(iso) || overrides.has(iso)) continue;
      // Replies to this occurrence alone replace the attendee's series reply (docs/calendar.md#replies).
      const own = ((event.occurrence_responses as { attendee_id: string; occurrence_start: string; response: string }[] | undefined) ?? []).filter((r) => r.occurrence_start === iso);
      const attendees = (event.attendees as Record<string, unknown>[] | undefined ?? []).map((a) => { const mine = own.find((r) => r.attendee_id === a.id); return mine ? { ...a, response: mine.response } : a; });
      out.push({ ...event, attendees, occurrence_replies: own.map((r) => r.attendee_id), starts_at: iso, ends_at: new Date(at + length).toISOString(), occurrence_start: iso, occurrence_key: `${event.id}@${iso}`, is_recurring: true, rrule: `FREQ=${rule.freq.toUpperCase()}` });
    }
    return out;
  }
  const childView = (child: Record<string, unknown>) => {
    const parent = state.events.find((e) => e.id === child.series_id);
    return { ...child, id: parent?.id ?? child.id, series_id: child.series_id, recurrence: parent?.recurrence ?? null, occurrence_start: child.recurrence_start, occurrence_key: `${child.series_id}@${child.recurrence_start}`, is_recurring: true, overridden: true, override_id: child.id };
  };
  async function handle(route: Route): Promise<boolean> {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (!path.startsWith('/api/v1/calendar/')) return false;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).then(() => true);
    if (options.deny) return json({ detail: 'Seeing the calendar needs a viewer, member or owner role in this workspace.' }, 403);
    if (path.startsWith('/api/v1/calendar/sync')) {
      if (options.failSync) return json({ detail: 'Database unavailable' }, 503);
      if (path === '/api/v1/calendar/sync' && method === 'GET') return json({ providers: providers(), redirect_uri: 'https://api.example.com/api/v1/calendar/sync/oauth/callback', can_manage: owner, connections: state.connections });
      if (!owner) return json({ detail: 'Only workspace owners can connect, disconnect or sync calendars.' }, 403);
      const start = /^\/api\/v1\/calendar\/sync\/oauth\/(google|microsoft)\/start$/.exec(path);
      if (start && method === 'POST') {
        state.connectStarts.push(start[1]);
        // The provider sends the browser back to the app that started the flow: the page's own origin
        // (the configured baseURL), never a fixed port.
        const appOrigin = new URL(request.headers().referer ?? request.frame().url()).origin;
        return json({ authorization_url: `${appOrigin}/?calendar_connect=coc_mock0001#calendar`, expires_at: at(1, 12) });
      }
      if (path === '/api/v1/calendar/sync/oauth/complete' && method === 'POST') {
        state.connections = [connection()];
        return json(state.connections[0], 201);
      }
      const act = /^\/api\/v1\/calendar\/sync\/connections\/([^/]+)(\/sync)?$/.exec(path);
      if (act && act[2] && method === 'POST') {
        state.syncs.push(act[1]);
        state.connections = state.connections.map((c) => (c.id === act[1] ? { ...c, status: 'connected', fix: '', last_sync_at: RUN_MIDDAY.toISOString(), last_sync_status: 'ok', last_error: null } : c));
        return json({ connection: state.connections[0], summary: { created: 0 } });
      }
      if (act && !act[2] && method === 'DELETE') {
        state.disconnected.push(act[1]);
        state.connections = [];
        return json({ revoked_at_provider: true, events_kept: 3, detail: 'Google Calendar is disconnected and ANUM\'s access was revoked. The events stay in ANUM.' });
      }
      return json({ detail: 'Not found' }, 404);
    }
    const retry = /^\/api\/v1\/calendar\/events\/([^/]+)\/sync\/retry$/.exec(path);
    if (retry && method === 'POST') {
      const target = state.events.find((e) => e.id === retry[1]);
      if (!target) return json({ detail: 'Event not found' }, 404);
      if (!owner) return json({ detail: 'Only the person who created this event or a workspace owner can change or delete it.' }, 403);
      state.retries.push(retry[1]);
      Object.assign(target, { sync_state: 'synced', sync_failed: false, sync_error: null, synced_at: RUN_MIDDAY.toISOString() });
      return json(view(target));
    }
    const invite = /^\/api\/v1\/calendar\/events\/([^/]+)\/invitations$/.exec(path);
    if (invite && method === 'POST') {
      state.invitations.push(invite[1]);
      return json({ status: 'waiting_approval', summary: 'Waiting for an owner\'s approval. Nothing has been sent.', approval: { id: 'approval_inv' }, attendees: ['sara@example.com', 'omar@example.com'] }, 202);
    }
    if (path === '/api/v1/calendar/next-steps') {
      if (options.failNextSteps) return json({ detail: 'Database unavailable' }, 503);
      return json({ items: [
        { id: 'agenda:evt_1', title: 'Prepare for Fleet review with the team', detail: 'Today 23:00', icon: 'agenda', action: { kind: 'task', title: 'Prepare: Fleet review with the team', prompt: 'Prepare a short briefing for the fleet review.' } },
        { id: 'approvals:pending', title: 'Review 1 approval', detail: 'Waiting for a decision', icon: 'approval', action: { kind: 'open', destination: 'approvals' } },
      ] });
    }
    if (path === '/api/v1/calendar/events' && method === 'GET') {
      const start = Date.parse(url.searchParams.get('start') ?? at(0, 0));
      const end = Date.parse(url.searchParams.get('end') ?? at(8, 0));
      const cancelled = url.searchParams.get('include_cancelled') === 'true';
      const items = state.events.filter((e) => cancelled || e.status !== 'cancelled')
        .flatMap((e) => (e.series_id ? [childView(e)] : occurrencesOf(e, start, end)))
        .filter((e) => Date.parse(e.starts_at as string) < end && Date.parse(e.ends_at as string) > start)
        .sort((a, b) => String(a.starts_at).localeCompare(String(b.starts_at)));
      return json({ items: items.map(view), start: new Date(start).toISOString(), end: new Date(end).toISOString(), truncated: false, can_write: owner, offset: 0 });
    }
    if (path === '/api/v1/calendar/events' && method === 'POST') {
      if (!owner) return json({ detail: 'Adding and changing events needs the member or owner role in this workspace.' }, 403);
      const body = request.postDataJSON() as Record<string, unknown>;
      state.created.push(body);
      const attendees = ((body.attendees as Record<string, unknown>[]) ?? []).map((a, i) => ({ id: `att_new_${i}`, role: null, response: 'invited', ...a }));
      const event: Record<string, unknown> = { ...base, id: `evt_new_${state.created.length}`, status: 'scheduled', reminders: [], ...body, attendees, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      if (event.all_day && event.start_date) { event.end_date = event.end_date ?? event.start_date; event.starts_at = `${event.start_date}T00:00:00.000Z`; event.ends_at = new Date(Date.parse(`${event.end_date}T00:00:00.000Z`) + DAY).toISOString(); }
      if (event.recurrence) { event.is_recurring = true; event.rrule = `FREQ=${String((event.recurrence as { freq: string }).freq).toUpperCase()}`; event.occurrence_start = event.starts_at; event.occurrence_key = `${event.id}@${event.starts_at}`; }
      state.events.push(event);
      return json(view(event), 201);
    }
    const match = /^\/api\/v1\/calendar\/events\/([^/]+)(\/attendees\/([^/]+)\/response)?$/.exec(path);
    const event = match ? state.events.find((e) => e.id === match[1]) : undefined;
    if (!match || !event) return json({ detail: 'Event not found' }, 404);
    const scoped = url.searchParams.get('scope') ?? undefined;
    if (match[2] && method === 'PUT') {
      const body = request.postDataJSON() as { response: string; scope?: string; occurrence_start?: string };
      state.responses.push({ event: event.id, attendee: match[3], ...body });
      if (body.scope === 'this' && body.occurrence_start) {
        const replies = ((event.occurrence_responses as Record<string, unknown>[] | undefined) ?? []).filter((r) => !(r.attendee_id === match[3] && r.occurrence_start === body.occurrence_start));
        event.occurrence_responses = [...replies, { attendee_id: match[3], occurrence_start: body.occurrence_start, response: body.response }];
        const shown = occurrencesOf(event, Date.parse(body.occurrence_start), Date.parse(body.occurrence_start) + 1).find((o) => o.occurrence_start === body.occurrence_start) ?? event;
        return json(view(shown));
      }
      event.attendees = (event.attendees as Record<string, unknown>[]).map((a) => (a.id === match[3] ? { ...a, response: body.response } : a));
      event.occurrence_responses = ((event.occurrence_responses as Record<string, unknown>[] | undefined) ?? []).filter((r) => r.attendee_id !== match[3]);
      return json(view(event));
    }
    if (method === 'GET') return json(view(event));
    if (method === 'PATCH') {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.updates.push({ id: event.id, ...body });
      const { scope, occurrence_start: occurrence, ...fields } = body;
      if (event.recurrence && scope === 'this' && typeof occurrence === 'string') {
        // One occurrence changed on its own: a child row keyed by the occurrence's original start.
        let child = state.events.find((e) => e.series_id === event.id && e.recurrence_start === occurrence);
        if (!child) {
          const length = Date.parse(event.ends_at as string) - Date.parse(event.starts_at as string);
          child = { ...event, id: `evt_child_${state.updates.length}`, series_id: event.id, recurrence_start: occurrence, recurrence: null, starts_at: occurrence, ends_at: new Date(Date.parse(occurrence) + length).toISOString() };
          state.events.push(child);
        }
        Object.assign(child, fields, { updated_at: new Date().toISOString() });
        return json(view(childView(child)));
      }
      if (event.recurrence && scope === 'following' && typeof occurrence === 'string') {
        const rule = event.recurrence as { count?: number | null; until?: string | null };
        const before = occurrencesOf(event, 0, Date.parse(occurrence)).length;
        const fresh = { ...event, ...fields, id: `evt_split_${state.updates.length}`, starts_at: (fields.starts_at as string) ?? occurrence, recurrence: { ...(fields.recurrence as object ?? event.recurrence as object), ...(rule.count ? { count: rule.count - before } : {}) } };
        event.recurrence = { ...rule, ...(rule.count ? { count: before } : { until: new Date(Date.parse(occurrence) - DAY).toISOString().slice(0, 10) }) };
        state.events.push(fresh);
        return json(view(fresh));
      }
      Object.assign(event, fields, { updated_at: new Date().toISOString() });
      if (Array.isArray(body.attendees)) event.attendees = (body.attendees as Record<string, unknown>[]).map((a, i) => ({ id: a.id ?? `att_edit_${i}`, role: null, response: 'invited', ...a }));
      return json(view(event));
    }
    if (method === 'DELETE') {
      state.deleted.push(scoped ? `${event.id}:${scoped}` : (event.id as string));
      const occurrence = url.searchParams.get('occurrence_start');
      if (scoped === 'this' && occurrence) {
        event.skipped = [...((event.skipped as string[] | undefined) ?? []), occurrence];
        state.events = state.events.filter((e) => !(e.series_id === event.id && e.recurrence_start === occurrence));
      } else if (scoped === 'following' && occurrence) {
        event.recurrence = { ...(event.recurrence as object), count: occurrencesOf(event, 0, Date.parse(occurrence)).length, until: null };
      } else state.events = state.events.filter((e) => e !== event && e.series_id !== event.id);
      await route.fulfill({ status: 204 });
      return true;
    }
    return false;
  }
  return { state, handle };
}
