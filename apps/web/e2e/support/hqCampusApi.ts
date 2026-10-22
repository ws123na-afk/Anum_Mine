import type { Page } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';
import { seedAgents } from './orgChartApi';

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

const totals = (agents: number, working: number, meeting: number, idle: number) => ({ agents, working, meeting, waiting_approval: 0, blocked: 0, idle, paused: 0, unknown: 0 });

export const campusFixture = {
  generated_at: now,
  buildings: [
    { workspace_id: workspace, tenant_id: tenant, name: 'Al Noor Fleet HQ', kind: 'business', icon: null, floors: 2, accessible: true, totals: totals(9, 4, 1, 4), presence_verified_at: now },
    { workspace_id: 'workspace_exec', tenant_id: tenant, name: 'Executive HQ', kind: 'executive', icon: null, floors: 3, accessible: true, totals: totals(5, 2, 0, 3), presence_verified_at: now },
    { workspace_id: 'workspace_tyre', tenant_id: tenant, name: 'Tyre Pulse HQ', kind: 'business', icon: null, floors: 2, accessible: true, totals: null, presence_verified_at: null },
    { workspace_id: 'workspace_commerce', tenant_id: tenant, name: 'ANUM360 Commerce', kind: 'business', icon: null, floors: 2, accessible: true, totals: totals(7, 3, 2, 2), presence_verified_at: now },
    { workspace_id: 'workspace_personal', tenant_id: tenant, name: 'Personal Office', kind: 'personal', icon: null, floors: 1, accessible: true, totals: totals(1, 0, 0, 1), presence_verified_at: now },
    { workspace_id: 'workspace_legal', tenant_id: tenant, name: 'Legal and Compliance', kind: 'department', icon: null, floors: 2, accessible: false, totals: null, presence_verified_at: null },
  ],
};

const memberships = [
  { workspace_id: workspace, workspace_name: 'Al Noor Fleet HQ', role: 'owner', active: true },
  { workspace_id: 'workspace_tyre', workspace_name: 'Tyre Pulse HQ', role: 'owner', active: true },
  { workspace_id: 'workspace_commerce', workspace_name: 'ANUM360 Commerce', role: 'member', active: true },
  { workspace_id: 'workspace_personal', workspace_name: 'Personal Office', role: 'owner', active: true },
];

export async function mockHq(page: Page, options: HqMockOptions = {}) {
  await mockWorkspaceApi(page);
  const office = officeFixture(options.layout ?? 'full');
  const agents = seedAgents();
  const task = (id: string, status: string, agent_id: string, title: string) => ({ id, title, prompt: title, status, tenant_id: tenant, workspace_id: workspace, created_at: now, updated_at: now, agent_id });
  const tasks = [
    task('task_plan', 'running', 'agent_fleet', 'Plan October tyre rotation'),
    task('task_mail', 'waiting_approval', 'agent_outreach', 'Email Al Noor Transport about the pilot'),
  ];
  const calls: string[] = [];
  await page.route('http://localhost:8000/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    calls.push(`${method} ${path}`);
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const error = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    const listed = /^\/api\/v1\/office-spaces\/(buildings|floors|rooms|desks)$/.exec(path);
    if (listed && method === 'GET') return json({ items: office[listed[1] as keyof typeof office], next_cursor: null });
    if (path === '/api/v1/me/workspace-memberships') return json(memberships);
    if (path === '/api/v1/office/campus') {
      if (options.campus === 'api') return json(campusFixture);
      if (options.campus === 'denied') return error(403, 'forbidden', 'You cannot read the campus.');
      return error(404, 'not_found', 'Not found.');
    }
    if (path === '/api/v1/agents' && method === 'GET') return json({ items: agents, next_cursor: null, counts: { all: agents.length, work: agents.length, personal: 0, custom: 0 } });
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
  return { calls };
}
