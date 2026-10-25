import { agentAvatars as teamAvatars } from './orgChartApi';
export { teamAvatars };
import type { Page, Route } from '@playwright/test';

// Mock of the office live flow API (docs/ANUM_MINE_HQ_LIVE_FLOW.md): /office/flow, the item trail,
// /office/team and the event stream. Names are plainly fake test data. Room and agent ids match
// hqRegistry(TENANT_A, { rooms: 4, desks: 4, agentIds: [fleet, leads, care, invoice] }).
const WS = 'workspace_foundation';
const asOf = () => new Date().toISOString();
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const loc = (n: number, agent: string, name: string) => ({
  building_id: 'Alpha-building', floor_id: 'Alpha-floor-0', room_id: `Alpha-room-${n}`, room_name: `Alpha Room ${n + 1}`,
  desk_id: `Alpha-desk-${n}`, agent_id: agent, agent_name: name,
});

export function flowItems() {
  return [
    { key: 'task:t_fail', kind: 'task', id: 't_fail', title: 'Reorder wiper blades', stage: 'blocked', status: 'failed', since: minutesAgo(50), created_at: minutesAgo(120),
      location: loc(3, 'agent_invoice', 'Invoice Checker'), location_reason: null, waiting: null,
      stuck: { code: 'run_failed', severity: 'critical', since: minutesAgo(50), age_seconds: 3000, threshold_seconds: 86400, facts: { error: 'Supplier API returned 500' }, action: 'retry_task' } },
    { key: 'task:t_send', kind: 'task', id: 't_send', title: 'Send fleet quote to customer', stage: 'waiting_human', status: 'waiting_approval', since: minutesAgo(380), created_at: minutesAgo(400),
      location: loc(2, 'agent_care', 'WhatsApp Concierge'), location_reason: null,
      waiting: { code: 'approval_pending', since: minutesAgo(380), approval_id: 'ap_1', action: 'send_whatsapp', expires_at: null },
      stuck: { code: 'approval_waiting', severity: 'warning', since: minutesAgo(380), age_seconds: 22800, threshold_seconds: 14400, facts: { approval_id: 'ap_1' }, action: 'decide_approval' } },
    { key: 'lead:l_acme', kind: 'lead', id: 'l_acme', title: 'Acme Fleet', stage: 'arrived', status: 'new', since: minutesAgo(300), created_at: minutesAgo(300),
      location: null, location_reason: 'no_owner', waiting: null,
      stuck: { code: 'lead_unassigned', severity: 'warning', since: minutesAgo(300), age_seconds: 18000, threshold_seconds: 14400, facts: {}, action: 'assign_owner' } },
    { key: 'task:t_plan', kind: 'task', id: 't_plan', title: 'Plan October tyre rotation', stage: 'in_office', status: 'running', since: minutesAgo(4), created_at: minutesAgo(30),
      location: loc(0, 'agent_fleet', 'Fleet Planner'), location_reason: null, waiting: null, stuck: null },
  ];
}

export function rollupOf(items: ReturnType<typeof flowItems>) {
  const rooms: Record<string, unknown> = {};
  for (const i of items) {
    if (!i.location || i.stage === 'done') continue;
    const r = (rooms[i.location.room_id] ??= { room_id: i.location.room_id, floor_id: i.location.floor_id, building_id: i.location.building_id, items: 0, waiting: 0, stuck: 0, worst: null }) as
      { items: number; waiting: number; stuck: number; worst: string | null };
    r.items += 1;
    if (i.stage === 'waiting_human') r.waiting += 1;
    if (i.stuck) { r.stuck += 1; r.worst = i.stuck.severity === 'critical' || r.worst === 'critical' ? 'critical' : 'warning'; }
  }
  return rooms;
}

export function flowSnapshot(items = flowItems(), sources: Record<string, string> = {}) {
  const live = items.filter((i) => i.stage !== 'done');
  return {
    version: 1, workspace_id: WS, as_of: asOf(), cursor: 'ev_1',
    thresholds: { approval_wait_minutes: 240, queue_wait_minutes: 30, run_silent_minutes: 60, lead_unassigned_minutes: 240, lead_idle_hours: 72, order_ship_grace_minutes: 0, recent_done_minutes: 30, failed_window_hours: 24 },
    sources: { tasks: 'connected', approvals: 'connected', leads: 'connected', orders: 'not_connected', ...sources },
    items, rooms: rollupOf(items), truncated: false,
    totals: {
      items: items.length, by_stage: { arrived: 1, in_office: 1, waiting_human: 1, blocked: 1, done: 0 }, by_kind: { task: 3, lead: 1 },
      waiting_human: items.filter((i) => i.stage === 'waiting_human').length, stuck: live.filter((i) => i.stuck).length, stuck_by_code: {},
      critical: live.filter((i) => i.stuck?.severity === 'critical').length, unlocated: live.filter((i) => !i.location).length,
    },
  };
}

const skill = (id: string, name: string) => ({ id, name, state: 'ready', reason: null, source: 'builtin' });
const member = (n: number, agent: string, name: string, dept: string, state: string, extra: object = {}) => ({
  agent_id: agent, name, icon: 'bot', color: '#0f766e', category: 'work', department: dept, description: `${name} helps the team.`, agent_status: 'active', avatar: teamAvatars[agent] ?? null,
  state, state_reason: null, last_verified_at: asOf(), stale: false, task: null, stuck: null,
  desk: { id: `Alpha-desk-${n}`, room_id: `Alpha-room-${n}`, room_name: `Alpha Room ${n + 1}`, floor_id: 'Alpha-floor-0', building_id: 'Alpha-building' },
  skills: [skill('anum.tyre-planning', 'Tyre planning')], skills_total: 1, tools_total: 3, ...extra,
});

export function teamFor(roomId: string | null) {
  const all = [
    member(0, 'agent_fleet', 'Fleet Planner', 'Operations and warehouse', 'working', { task: { id: 't_plan', title: 'Plan October tyre rotation' } }),
    member(1, 'agent_leads', 'Lead Finder', 'Sales & Outreach', 'idle', { skills: [skill('anum.web-search', 'Web search'), skill('anum.document-drafting', 'Document drafting')], skills_total: 2 }),
    member(2, 'agent_care', 'WhatsApp Concierge', 'Customer Care', 'waiting_approval', { task: { id: 't_send', title: 'Send fleet quote to customer' } }),
    member(3, 'agent_invoice', 'Invoice Checker', 'Finance', 'blocked', {
      stuck: { code: 'run_failed', severity: 'critical', since: minutesAgo(50), age_seconds: 3000, threshold_seconds: 86400, facts: { error: 'Supplier API returned 500' }, action: 'retry_task', item_key: 'task:t_fail', title: 'Reorder wiper blades' } }),
  ];
  const members = roomId ? all.filter((m) => m.desk.room_id === roomId) : all;
  return {
    version: 1, workspace_id: WS, as_of: asOf(), scope: { building_id: null, floor_id: null, room_id: roomId }, skills_included: true,
    members, vacant_desks: [], hidden_assignments: 0,
    counts: { agents: members.length, vacant_desks: 0, hidden: 0, working: 0, waiting_approval: 0, blocked: 0, stuck: members.filter((m) => m.stuck).length },
    unassigned_agents: [{ agent_id: 'agent_personal', name: 'Personal Planner', department: null, status: 'active' }], unassigned_total: 1,
  };
}

export interface FlowMock {
  /** Replace what /office/flow answers; the next stream event makes the page read it. */
  set(items: ReturnType<typeof flowItems>, sources?: Record<string, string>): void;
  /** Queue one stream event; the open stream request delivers it. */
  emit(type: string): void;
  failWith(status: number | null): void;
  requests: { path: string }[];
}

export async function mockFlow(page: Page, initial = flowItems()): Promise<FlowMock> {
  let items = initial;
  let sources: Record<string, string> = {};
  let fail: number | null = null;
  const events: string[] = [];
  const mock: FlowMock = {
    requests: [],
    set(next, s = {}) { items = next; sources = s; },
    emit(type) { events.push(type); },
    failWith(status) { fail = status; },
  };
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/(flow|team)(\/.*)?(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url());
    mock.requests.push({ path: url.pathname + url.search });
    if (fail !== null) {
      if (fail === 0) return route.abort('failed');
      return route.fulfill({ status: fail, contentType: 'application/json', body: JSON.stringify({ detail: fail === 403 ? 'Forbidden' : 'The service failed.' }) });
    }
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/v1/office/flow') return json(flowSnapshot(items, sources));
    if (url.pathname === '/api/v1/office/team') return json(teamFor(url.searchParams.get('room_id')));
    const trail = url.pathname.match(/\/flow\/items\/(task|lead|order)\/([^/]+)\/trail$/);
    if (trail) {
      if (trail[1] === 'order') return json({ version: 1, kind: 'order', item_id: trail[2], source: 'not_recorded', steps: [] });
      return json({ version: 1, kind: trail[1], item_id: trail[2], source: 'connected', steps: [
        { id: 'ev_a', type: 'task.created', at: minutesAgo(120), data: {} }, { id: 'ev_b', type: 'agent_run.failed', at: minutesAgo(50), data: { run_id: 'run_1' } }] });
    }
    return route.fulfill({ status: 404, body: '{}' });
  });
  await page.route('http://localhost:8000/api/v1/events/stream', async (route: Route) => {
    const pending = events.splice(0);
    const body = pending.map((type, i) => `id: e${i}\ndata: ${JSON.stringify({ id: `e${i}`, type, version: 1, tenant_id: 'tenant_local', workspace_id: WS, subject: 'x', correlation_id: 'c', created_at: asOf(), payload: {} })}\n\n`).join('') || ': keep-alive\n\n';
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body });
  });
  return mock;
}
