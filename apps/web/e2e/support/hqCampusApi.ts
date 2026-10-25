import type { Page } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';
import { agentAvatars, seedAgents } from './orgChartApi';

// A mock of what the ANUM MINE headquarters reads (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md): the
// office registry of the open workspace, the caller's workspaces, the campus endpoint, agents and
// their tasks, live agent meetings and the commerce and tyre setup. Every value is plainly fake.
const now = new Date().toISOString();
const tenant = 'tenant_local';
const workspace = 'workspace_foundation';

export interface HqMockOptions {
  /** 'api' serves the campus endpoint, 'missing' answers 404 (memberships stand in), 'denied' answers 403. */
  campus?: 'api' | 'missing' | 'denied';
  layout?: 'full' | 'empty' | 'none';
  commerce?: 'connected' | 'none' | 'denied';
  tyre?: 'connected' | 'none';
  meetings?: 'live' | 'none' | 'failing';
  /** 'api' serves GET /office/presence and the desk work record, 'missing' answers 404 so the browser reads tasks and meetings itself. */
  presence?: 'api' | 'missing';
  /** Extra desks (plainly fake) on top of the fixture, for the scene specs: a desk with an agent id is a registry assignment. */
  extraDesks?: ReadonlyArray<{ id: string; roomId: string; label: string; agentId: string | null }>;
  /** 'conflict' makes the building profile save answer 409. */
  profile?: 'ok' | 'conflict';
}

const record = (id: string, extra: Record<string, unknown>) => ({ id, revision: 1, tenant_id: tenant, workspace_id: workspace, created_at: now, updated_at: now, ...extra });

export function officeFixture(layout: 'full' | 'empty' | 'none') {
  if (layout === 'none') return { buildings: [], floors: [], rooms: [], desks: [] };
  const buildings = [record('bld_hq', { name: 'Al Noor Fleet HQ', timezone: 'Asia/Dubai', latitude: null, longitude: null })];
  const floors = [
    record('flr_0', { building_id: 'bld_hq', level: 0, name: 'Ground floor' }),
    record('flr_1', { building_id: 'bld_hq', level: 1, name: 'First floor' }),
  ];
  if (layout === 'empty') return { buildings, floors, rooms: [], desks: [] };
  const room = (id: string, floor: string, name: string, kind: string, capacity: number, geo?: [number, number, number, number]) => record(id, {
    floor_id: floor, name, kind, capacity, ...(geo ? { scene_x: geo[0], scene_z: geo[1], scene_width: geo[2], scene_depth: geo[3] } : {}),
  });
  const rooms = [
    room('room_reception', 'flr_0', 'Reception', 'reception', 3, [0, 0, 7, 5]),
    room('room_care', 'flr_0', 'Customer Care', 'office', 6, [8, 0, 9, 5]),
    room('room_ops', 'flr_0', 'Operations', 'warehouse', 6, [18, 0, 9, 5]),
    room('room_meet', 'flr_0', 'Meeting Room', 'meeting', 8, [0, 6, 9, 5]),
    room('room_tyre', 'flr_0', 'Tyre Analysis', 'workshop', 4, [10, 6, 9, 5]),
    room('room_lounge', 'flr_0', 'Breakout Lounge', 'lounge', 6, [20, 6, 7, 5]),
    room('room_sales', 'flr_1', 'Sales and Marketing', 'office', 6, [0, 0, 10, 6]),
    room('room_fin', 'flr_1', 'Finance', 'office', 4, [11, 0, 8, 6]),
    room('room_exec', 'flr_1', 'Executive Office', 'office', 3, [20, 0, 7, 6]),
  ];
  const desk = (id: string, roomId: string, label: string, agent: string | null) => record(id, { room_id: roomId, label, agent_id: agent });
  const desks = [
    desk('desk_1', 'room_care', 'Desk 1', 'agent_care'),
    desk('desk_2', 'room_care', 'Desk 2', null),
    desk('desk_3', 'room_tyre', 'Desk 1', 'agent_fleet'),
    desk('desk_4', 'room_sales', 'Desk 1', 'agent_leads'),
    desk('desk_5', 'room_sales', 'Desk 2', 'agent_outreach'),
    desk('desk_6', 'room_fin', 'Desk 1', 'agent_invoice'),
  ];
  return { buildings, floors, rooms, desks };
}

const byState = (agents: number, working: number, meeting: number, idle: number, queued = 0) => ({
  total: agents, by_state: { working, queued, meeting, waiting_approval: 0, blocked: 0, idle, paused: 0, unknown: 0 },
});
const floorsOf = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `flr_${i}`, building_id: 'bld', level: i, name: `Floor ${i}` }));
const bld = (id: string, name: string, kind: string | null, floors: number, agents: ReturnType<typeof byState> | null, extra: Record<string, unknown> = {}) => ({
  workspace_id: id, name, kind, icon: null, profile_revision: kind ? 1 : null, role: 'owner', archived: false, accessible: true, floors: floorsOf(floors), office_buildings: 1,
  rooms: floors * 4, desks: floors * 6, agents, presence_verified_at: agents ? now : null, presence_as_of: now, ...extra,
});

/** GET /api/v1/office/campus, contract version 1 (packages/contracts/src/officeCampus.ts). */
export const campusFixture = {
  version: 1, tenant_id: tenant, current_workspace_id: workspace, generated_at: now, truncated: false,
  buildings: [
    bld(workspace, 'Al Noor Fleet HQ', 'business', 2, byState(9, 4, 1, 4)),
    bld('workspace_exec', 'Executive HQ', 'executive', 3, byState(5, 2, 0, 3), { role: 'member' }),
    bld('workspace_tyre', 'Tyre Pulse HQ', 'business', 2, null, { agents: null, presence_verified_at: null }),
    bld('workspace_commerce', 'ANUM360 Commerce', 'business', 2, byState(7, 3, 2, 1, 1), { role: 'member' }),
    bld('workspace_personal', 'Personal Office', null, 1, byState(1, 0, 0, 1), { role: 'member' }),
    bld('workspace_legal', 'Legal and Compliance', 'department', 2, null, { accessible: false, agents: null, role: 'viewer', floors: [], rooms: 0, desks: 0 }),
  ],
};

/** GET /api/v1/office/presence, contract version 1 (packages/contracts/src/officePresence.ts). */
export function presenceFixture() {
  const item = (agent: string, name: string, state: string, extra: Record<string, unknown> = {}) => ({
    key: agent, agent_id: agent, agent_name: name, desk_id: null, state, provenance: { kind: state === 'idle' ? 'none' : 'task', run_id: null, task_id: null, approval_id: null, meeting_id: null, reason: null },
    task: null, last_verified_at: now, stale: false, ...extra,
  });
  const items = [
    item('agent_fleet', 'Fleet Planner', 'working', { desk_id: 'desk_3', task: { id: 'task_plan', title: 'Plan October tyre rotation' } }),
    item('agent_outreach', 'Outreach Writer', 'waiting_approval', { desk_id: 'desk_5', task: { id: 'task_mail', title: 'Email Al Noor Transport about the pilot' } }),
    item('agent_care', 'WhatsApp Concierge', 'meeting', { desk_id: 'desk_1' }),
    item('agent_leads', 'Lead Finder', 'idle', { desk_id: 'desk_4', stale: true }),
    item('agent_invoice', 'Invoice Checker', 'queued', { desk_id: 'desk_6' }),
    item('agent_maint', 'Maintenance Scheduler', 'paused'),
    { key: 'desk:desk_2', agent_id: null, agent_name: null, desk_id: 'desk_2', state: 'unknown', provenance: { kind: 'none', run_id: null, task_id: null, approval_id: null, meeting_id: null, reason: 'agent_not_visible' }, task: null, last_verified_at: now, stale: false },
  ];
  const totals: Record<string, number> = { working: 0, queued: 0, meeting: 0, waiting_approval: 0, blocked: 0, idle: 0, paused: 0, unknown: 0 };
  for (const i of items) totals[i.state] += 1;
  return { version: 1, workspace_id: workspace, as_of: now, cursor: null, totals, items };
}

/** GET /api/v1/office/workstations/{desk}/work (packages/contracts/src/officeWorkstation.ts). */
export function workFixture(deskId: string) {
  const base = { version: 1, workspace_id: workspace, as_of: now, desk: { id: deskId, room_id: 'room_tyre', label: 'Desk 1' } };
  if (deskId === 'desk_2') return { ...base, agent: null, presence: null, reason: 'agent_not_visible', tasks: [], approvals: [], errors: [], documents: [] };
  return {
    ...base, agent: { id: 'agent_fleet', name: 'Fleet Planner', status: 'active' }, presence: null, reason: null,
    tasks: [{
      id: 'task_plan', title: 'Plan October tyre rotation', status: 'running', priority: 'high', due_at: null, created_at: now, updated_at: now,
      links: { task: '/api/v1/tasks/task_plan', runs: '/api/v1/tasks/task_plan/runs' },
      run: { id: 'run_1', status: 'running', phase: 'executing', checkpoint_version: 3, last_step_id: 's2', approval_id: null, updated_at: now, steps: [
        { id: 's1', type: 'retrieval', summary: 'Read the fleet tyre data', created_at: now }, { id: 's2', type: 'tool_call', summary: 'Prepared the rotation plan', created_at: now } ] },
    }],
    approvals: [{ id: 'ap_1', task_id: 'task_plan', action: 'send_email', risk_level: 'high', expires_at: null, expired: false, created_at: now }],
    errors: [{ task_id: 'task_old', run_id: 'run_old', at: now, message: 'The tyre data source timed out.' }],
    documents: [{ id: 'file_1', name: 'Fleet tyre register', content_type: 'text/csv', size_bytes: 2048, link: '/api/v1/files/file_1' }],
  };
}

const memberships = [
  { workspace_id: workspace, workspace_name: 'Al Noor Fleet HQ', role: 'owner', active: true },
  { workspace_id: 'workspace_tyre', workspace_name: 'Tyre Pulse HQ', role: 'owner', active: true },
  { workspace_id: 'workspace_commerce', workspace_name: 'ANUM360 Commerce', role: 'member', active: true },
  { workspace_id: 'workspace_personal', workspace_name: 'Personal Office', role: 'owner', active: true },
];

export async function mockHq(page: Page, options: HqMockOptions = {}) {
  await mockWorkspaceApi(page);
  const office = officeFixture(options.layout ?? 'full');
  for (const d of options.extraDesks ?? []) office.desks.push(record(d.id, { room_id: d.roomId, label: d.label, agent_id: d.agentId }) as (typeof office.desks)[number]);
  const agents = seedAgents();
  const task = (id: string, status: string, agent_id: string, title: string) => ({ id, title, prompt: title, status, tenant_id: tenant, workspace_id: workspace, created_at: now, updated_at: now, agent_id });
  const tasks = [
    task('task_plan', 'running', 'agent_fleet', 'Plan October tyre rotation'),
    task('task_mail', 'waiting_approval', 'agent_outreach', 'Email Al Noor Transport about the pilot'),
  ];
  const calls: string[] = [];
  const profileCalls: Array<{ kind: string; icon: string; expected_revision?: number }> = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    calls.push(`${method} ${path}`);
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const error = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    // The executive PA dock beside the scene: quiet by default (no tools, no tour); specs that test it register their own routes.
    if (path === '/api/v1/executive-pa/tools') return json({ tools: [] });
    if (path === '/api/v1/executive-pa/tour') return json({ status: 'empty', stops: [] });
    const listed = /^\/api\/v1\/office-spaces\/(buildings|floors|rooms|desks)$/.exec(path);
    if (listed && method === 'GET') return json({ items: office[listed[1] as keyof typeof office], next_cursor: null });
    if (path === '/api/v1/me/workspace-memberships') return json(memberships);
    if (path === '/api/v1/office/campus') {
      if (options.campus === 'api') return json(campusFixture);
      if (options.campus === 'denied') return error(403, 'forbidden', 'You cannot read the campus.');
      return error(404, 'not_found', 'Not found.');
    }
    if (path === '/api/v1/office/presence') return options.presence === 'missing' ? error(404, 'not_found', 'Not found.') : json(presenceFixture());
    const work = /^\/api\/v1\/office\/workstations\/([^/]+)\/work$/.exec(path);
    if (work) return options.presence === 'missing' ? error(404, 'not_found', 'Not found.') : json(workFixture(work[1]));
    if (path === '/api/v1/office/campus/profile' && method === 'PUT') {
      const body = route.request().postDataJSON() as { kind: string; icon: string; expected_revision?: number };
      profileCalls.push(body);
      if (options.profile === 'conflict') return error(409, 'conflict', 'The profile changed.');
      return json({ workspace_id: workspace, kind: body.kind, icon: body.icon, revision: (body.expected_revision ?? 0) + 1 });
    }
    if (path === '/api/v1/agents' && method === 'GET') return json({ items: agents.map((a) => (a.id as string) in agentAvatars ? { ...a, avatar: agentAvatars[a.id as string] } : a), next_cursor: null, counts: { all: agents.length, work: agents.length, personal: 0, custom: 0 } });
    if (path === '/api/v1/tasks' && method === 'GET') return json(tasks);
    if (path === '/api/v1/agent-meetings') {
      if (options.meetings === 'failing') return error(503, 'unavailable', 'Meetings are not reachable.');
      return json({ items: options.meetings === 'live' ? [{ id: 'meeting_live', title: 'Weekly sync', status: 'active', revision: 1 }] : [], next_cursor: null });
    }
    if (path === '/api/v1/agent-meetings/meeting_live') return json({ id: 'meeting_live', title: 'Weekly sync', status: 'active', revision: 1, participants: [{ id: 'p1', session_id: 'meeting_live', actor_id: 'agent_care', kind: 'agent' }] });
    if (path === '/api/v1/commerce/setup') {
      if (options.commerce === 'denied') return error(403, 'forbidden', 'You need commerce access.');
      return json({ permissions: {}, connectors: options.commerce === 'connected' ? 1 : 0, channels: [], own_store_modes: [], sync_scheduler: false });
    }
    if (path === '/api/v1/commerce/dashboard') {
      return json({ start: '2026-10-22', end: '2026-10-22', tz: 'Asia/Dubai', currency: 'AED', currencies: [], by_channel: [{ channel: 'amazon', label: 'Amazon', marketplace_id: 'x', marketplace: 'Amazon.ae', currency: 'AED', revenue: 100, orders: 12, units: 14 }, { channel: 'noon', label: 'noon', marketplace_id: 'y', marketplace: 'noon UAE', currency: 'AED', revenue: 50, orders: 5, units: 6 }],
        by_day: [], top_products: [], pending_orders: [], pending_total: 7, late_total: 2, low_stock: [], returns: { count: 3, units: 3, amount: {}, rate: 0.1 }, price_alerts: [], open_alerts: 1, fees: {}, truncated: false, connectors: 1 });
    }
    if (path === '/api/v1/tyre/setup') return json({ connection: options.tyre === 'connected' ? { kind: 'supabase' } : null, mapping: null, mapping_problems: [], assumptions: {}, discovery: null, sync: null, fields: {}, presets: [], can_manage: true, can_operate: true, delivery_channels: [] });
    if (path === '/api/v1/tyre/dashboard') return json({ summary: { as_of: now, currency: 'AED', vehicles: 42, tyres_fitted: 250, tyres_in_stock: 30, tyres_removed_last_30_days: 4, tyres_with_readings: 200, average_tread_mm: 8, below_minimum: 3, in_warning_band: 5, pressure_checked: 100, pressure_compliant_pct: 90, under_inflated: 2, over_inflated: 1, overdue_inspections: 6, due_next_30_days: 11, due_next_90_days: 20, cost_per_1000_km: null, cost_per_hour: null, min_tread_mm: 3 }, synced_at: now, alert_counts: { critical: 1, warning: 2 }, top_alerts: [], due_soon: [], due_by_month: {} });
    return route.fallback();
  });
  return { calls, profileCalls };
}
