import type { Page, Route } from '@playwright/test';

// A stateful calendar API (docs/calendar.md) for the Calendar page and Home: events this week with
// attendees and replies, add / edit / cancel / delete, recorded replies and the suggested next steps.
export interface CalendarMockOptions {
  role?: 'owner' | 'viewer'; deny?: boolean; failNextSteps?: boolean; empty?: boolean;
  // Calendar sync (docs/calendar.md#sync): nothing connected, a working Google connection, one that needs
  // reconnecting, or a server without OAuth clients.
  sync?: 'none' | 'connected' | 'reauth' | 'unconfigured'; failSync?: boolean;
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
    { ...base, id: 'evt_1', title: 'Fleet review with the team', kind: 'meeting', status: 'scheduled', ...(options.sync === 'connected' ? { connection_id: 'cal_1', external_id: 'g1', sync_state: 'synced', synced_at: at(-1, 9), invitations_pending: true } : {}), starts_at: at(0, 23, 0), ends_at: at(0, 23, 30), location: 'Room 2', conference_url: 'https://meet.example.com/fleet', phone: null, description: 'Tyre spend and the winter plan.',
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
  };
  const providers = () => [
    { provider: 'google', name: 'Google Calendar', configured: options.sync !== 'unconfigured', missing: options.sync === 'unconfigured' ? ['ANUM_CALENDAR_GOOGLE_CLIENT_ID', 'ANUM_CALENDAR_GOOGLE_CLIENT_SECRET'] : [], scopes: ['openid', 'email'] },
    { provider: 'microsoft', name: 'Microsoft 365 Calendar', configured: true, missing: [], scopes: ['Calendars.ReadWrite'] },
  ];
  const view = (event: Record<string, unknown>) => ({ ...event, can_manage: owner && (event.created_by === 'user_local' || owner) });
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
        return json({ authorization_url: `${url.origin.replace('localhost:8000', '127.0.0.1:4173')}/?calendar_connect=coc_mock0001#calendar`, expires_at: at(1, 12) });
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
      const items = state.events.filter((e) => Date.parse(e.starts_at as string) < end && Date.parse(e.ends_at as string) > start && (cancelled || e.status !== 'cancelled'))
        .sort((a, b) => String(a.starts_at).localeCompare(String(b.starts_at)));
      return json({ items: items.map(view), start: new Date(start).toISOString(), end: new Date(end).toISOString(), truncated: false, can_write: owner });
    }
    if (path === '/api/v1/calendar/events' && method === 'POST') {
      if (!owner) return json({ detail: 'Adding and changing events needs the member or owner role in this workspace.' }, 403);
      const body = request.postDataJSON() as Record<string, unknown>;
      state.created.push(body);
      const attendees = ((body.attendees as Record<string, unknown>[]) ?? []).map((a, i) => ({ id: `att_new_${i}`, role: null, response: 'invited', ...a }));
      const event = { ...base, id: `evt_new_${state.created.length}`, status: 'scheduled', ...body, attendees, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      state.events.push(event);
      return json(view(event), 201);
    }
    const match = /^\/api\/v1\/calendar\/events\/([^/]+)(\/attendees\/([^/]+)\/response)?$/.exec(path);
    const event = match ? state.events.find((e) => e.id === match[1]) : undefined;
    if (!match || !event) return json({ detail: 'Event not found' }, 404);
    if (match[2] && method === 'PUT') {
      const body = request.postDataJSON() as { response: string };
      state.responses.push({ event: event.id, attendee: match[3], ...body });
      event.attendees = (event.attendees as Record<string, unknown>[]).map((a) => (a.id === match[3] ? { ...a, response: body.response } : a));
      return json(view(event));
    }
    if (method === 'GET') return json(view(event));
    if (method === 'PATCH') {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.updates.push({ id: event.id, ...body });
      Object.assign(event, body, { updated_at: new Date().toISOString() });
      if (Array.isArray(body.attendees)) event.attendees = (body.attendees as Record<string, unknown>[]).map((a, i) => ({ id: a.id ?? `att_edit_${i}`, role: null, response: 'invited', ...a }));
      return json(view(event));
    }
    if (method === 'DELETE') {
      state.deleted.push(event.id as string);
      state.events = state.events.filter((e) => e !== event);
      await route.fulfill({ status: 204 });
      return true;
    }
    return false;
  }
  return { state, handle };
}
