import type { Page, Route } from '@playwright/test';
import { mockWorkspaceApi } from './workspaceApi';
import { agentAvatars, seedAgents } from './orgChartApi';
import { readFileSync } from 'node:fs';
import { logoPng } from './pngFixture';

// A mock of what the ANUM MINE headquarters reads (docs/ANUM_MINE_HQ_EXTENSION_POINTS.md): the
// office registry of the open workspace, the caller's workspaces, the campus endpoint, agents and
// their tasks, live agent meetings and the commerce and tyre setup. Every value is plainly fake.
const now = new Date().toISOString();
/**
 * The fixtures carry `now` from when this module loaded. A worker that runs specs for longer than the presence window (5 minutes)
 * would otherwise serve "verified" times that the page rightly calls stale, so every answer is stamped with the time it is sent.
 * A time a test sets on purpose (anything other than the load time) is left as it is.
 */
const stamp = (body: string) => body.split(now).join(new Date().toISOString());
const tenant = 'tenant_local';
const workspace = 'workspace_foundation';

export interface HqMockOptions {
  /** 'api' serves the campus endpoint, 'missing' answers 404 (memberships stand in), 'denied' answers 403. */
  campus?: 'api' | 'missing' | 'denied';
  layout?: 'full' | 'empty' | 'none' | 'starter' | 'campus' | 'starter-full';
  commerce?: 'connected' | 'none' | 'denied';
  tyre?: 'connected' | 'none';
  meetings?: 'live' | 'none' | 'failing';
  /** 'api' serves GET /office/presence and the desk work record, 'missing' answers 404 so the browser reads tasks and meetings itself. */
  presence?: 'api' | 'missing';
  /**
   * GET /office/presence/stream (docs/ANUM_MINE_HQ_LIVE_SCENE.md). 'unsupported' (default) answers 404, so the page polls the snapshot;
   * 'live' answers the first request with the snapshot and holds later ones until `stream.send(body)` (the stream then ends and the
   * page reconnects with Last-Event-ID, as it does when the server bounds a stream); 'blocked' answers 200 with a page that is not
   * an event stream (a proxy in the way); 'down' fails the connection; 'denied' answers 403.
   */
  stream?: 'unsupported' | 'live' | 'blocked' | 'down' | 'denied';
  /** Extra desks (plainly fake) on top of the fixture, for the scene specs: a desk with an agent id is a registry assignment. */
  extraDesks?: ReadonlyArray<{ id: string; roomId: string; label: string; agentId: string | null }>;
  /** Edits applied to room records by id when the registry is read (a spec fills it after load to move a room: the layout changed). */
  roomEdits?: Record<string, Record<string, unknown>>;
  /** 'conflict' makes the building profile save answer 409. */
  profile?: 'ok' | 'conflict';
  /** The office room the live meeting (`meeting_live`) is held in; null or omitted means a meeting with no room link. */
  meetingRoomId?: string | null;
  /**
   * GET /office/campus/boards (docs/ANUM_MINE_HQ_BILLBOARDS.md#every-building). Omitted or 'missing': 404 (an older API; other workspaces'
   * boards say Not connected). 'api' serves `boardsFixture`; 'failing' answers 503 until `boards.recover()`; 'slow' holds the answer until
   * `boards.release()`.
   */
  boards?: 'missing' | 'api' | 'failing' | 'slow';
}

const record = (id: string, extra: Record<string, unknown>) => ({ id, revision: 1, tenant_id: tenant, workspace_id: workspace, created_at: now, updated_at: now, ...extra });

/** The open workspace's registry after the starter campus: eight buildings, the first with the full fixture, the others with a floor and two rooms. */
function starterFixture() {
  const base = officeFixture('full');
  const names = ['ANUM HQ', 'ANUM 360 Commerce', 'Tyre Pulse Workshop', 'Corporate Services', 'Knowledge and AI', 'New Ventures', 'Founder Personal Office', 'Career and Employment'];
  const buildings = names.map((name, i) => record(i === 0 ? 'bld_hq' : `bld_s${i}`, { name, timezone: 'Asia/Dubai', latitude: null, longitude: null }));
  const floors = [...base.floors];
  const rooms = [...base.rooms];
  for (let i = 1; i < names.length; i++) {
    floors.push(record(`flr_s${i}`, { building_id: `bld_s${i}`, level: 0, name: 'Ground floor' }));
    rooms.push(record(`room_s${i}a`, { floor_id: `flr_s${i}`, name: `${names[i]} Lobby`, kind: 'reception', capacity: 3, scene_x: 0, scene_z: 0, scene_width: 7, scene_depth: 5 }));
    rooms.push(record(`room_s${i}b`, { floor_id: `flr_s${i}`, name: `${names[i]} Office`, kind: 'office', capacity: 4, scene_x: 8, scene_z: 0, scene_width: 8, scene_depth: 5 }));
  }
  return { buildings, floors, rooms, desks: base.desks };
}

/** The real starter campus (8 buildings, 18 floors, 62 rooms, 220 desks): the API planner's own output, positions included. */
function starterCampusFixture() {
  const fx = JSON.parse(readFileSync(new URL('../../test/fixtures/starterCampus.json', import.meta.url), 'utf8')) as { buildings: Record<string, unknown>[]; floors: Record<string, unknown>[]; rooms: Record<string, unknown>[]; desks: Record<string, unknown>[] };
  return {
    buildings: fx.buildings.map((b) => record(String(b.id), { name: b.name, name_ar: b.name_ar ?? null, timezone: 'Asia/Dubai', latitude: null, longitude: null })),
    floors: fx.floors.map((f) => record(String(f.id), { building_id: f.building_id, level: f.level, name: f.name, name_ar: f.name_ar ?? null })),
    rooms: fx.rooms.map((r) => record(String(r.id), r)),
    desks: fx.desks.map((d) => record(String(d.id), d)),
  };
}

/**
 * Four buildings with the stable ids the starter campus gives its buildings (`campus-01-hq`...): ANUM HQ over three floors
 * with a command centre, ANUM 360 Commerce, Tyre Pulse and the personal office. Rooms carry the departments the starter campus sets.
 */
function campusFixture4() {
  const buildings = [
    record('campus-01-hq', { name: 'ANUM HQ', timezone: 'Asia/Dubai', latitude: null, longitude: null }),
    record('campus-02-commerce', { name: 'ANUM 360 Commerce', timezone: 'Asia/Dubai', latitude: null, longitude: null }),
    record('campus-03-tyre-pulse', { name: 'Tyre Pulse', timezone: 'Asia/Dubai', latitude: null, longitude: null }),
    record('campus-07-personal', { name: 'Founder Personal Office', timezone: 'Asia/Dubai', latitude: null, longitude: null }),
  ];
  const floor = (id: string, building: string, level: number, name: string) => record(id, { building_id: building, level, name });
  const floors = [
    floor('campus-01-hq-l1', 'campus-01-hq', 1, 'Floor 1: Reception and Meeting Rooms'),
    floor('campus-01-hq-l2', 'campus-01-hq', 2, 'Floor 2: Operations and Management'),
    floor('campus-01-hq-l3', 'campus-01-hq', 3, 'Floor 3: Executive and Strategy'),
    floor('campus-02-commerce-l0', 'campus-02-commerce', 0, 'Ground floor'),
    floor('campus-03-tyre-pulse-l0', 'campus-03-tyre-pulse', 0, 'Ground floor'),
    floor('campus-07-personal-l0', 'campus-07-personal', 0, 'Ground floor'),
  ];
  const room = (id: string, floorId: string, name: string, kind: string, capacity: number, geo: [number, number, number, number], department: string | null = null) => record(id, {
    floor_id: floorId, name, kind, capacity, scene_x: geo[0], scene_z: geo[1], scene_width: geo[2], scene_depth: geo[3], team_department: department,
  });
  const rooms = [
    room('campus-01-hq-reception', 'campus-01-hq-l1', 'Reception', 'reception', 4, [0, 0, 10, 6]),
    room('campus-01-hq-meeting-a', 'campus-01-hq-l1', 'Meeting Room A', 'meeting', 8, [12, 0, 8, 6]),
    room('campus-01-hq-command-center', 'campus-01-hq-l2', 'Command Center', 'command', 10, [0, 0, 14, 10]),
    room('campus-01-hq-marketing', 'campus-01-hq-l2', 'Marketing Studio', 'office', 6, [16, 0, 12, 8], 'marketing'),
    room('campus-01-hq-facilities', 'campus-01-hq-l2', 'Facilities Office', 'office', 4, [0, 12, 10, 8]),
    room('campus-01-hq-executive', 'campus-01-hq-l3', 'Executive Office', 'office', 5, [0, 0, 14, 9]),
    room('campus-01-hq-boardroom', 'campus-01-hq-l3', 'Boardroom', 'meeting', 16, [16, 0, 16, 9]),
    room('campus-02-commerce-sales', 'campus-02-commerce-l0', 'Sales Office', 'office', 8, [0, 0, 12, 8], 'sales'),
    room('campus-02-commerce-orders', 'campus-02-commerce-l0', 'Orders', 'office', 8, [14, 0, 12, 8], 'operations'),
    room('campus-02-commerce-warehouse', 'campus-02-commerce-l0', 'Warehouse', 'warehouse', 8, [0, 10, 16, 12], 'operations'),
    room('campus-03-tyre-pulse-workshop', 'campus-03-tyre-pulse-l0', 'Workshop', 'workshop', 8, [0, 0, 14, 10], 'tyre_engineering'),
    room('campus-03-tyre-pulse-fleet', 'campus-03-tyre-pulse-l0', 'Fleet', 'office', 6, [16, 0, 12, 8], 'operations'),
    room('campus-07-personal-office', 'campus-07-personal-l0', 'Personal Office', 'office', 4, [0, 0, 10, 8]),
    room('campus-07-personal-lounge', 'campus-07-personal-l0', 'Lounge', 'lounge', 6, [12, 0, 8, 6]),
  ];
  const desk = (id: string, roomId: string, label: string, agent: string | null) => record(id, { room_id: roomId, label, agent_id: agent });
  const desks = [
    desk('desk_c1', 'campus-01-hq-command-center', 'Desk 1', 'agent_fleet'),
    desk('desk_c2', 'campus-01-hq-command-center', 'Desk 2', null),
    desk('desk_m1', 'campus-01-hq-marketing', 'Desk 1', 'agent_leads'),
    desk('desk_s1', 'campus-02-commerce-sales', 'Desk 1', 'agent_care'),
    desk('desk_t1', 'campus-03-tyre-pulse-workshop', 'Desk 1', 'agent_invoice'),
  ];
  return { buildings, floors, rooms, desks };
}

export function officeFixture(layout: 'full' | 'empty' | 'none' | 'starter' | 'campus' | 'starter-full'): { buildings: unknown[]; floors: unknown[]; rooms: unknown[]; desks: unknown[] } {
  if (layout === 'starter-full') return starterCampusFixture();
  if (layout === 'starter') return starterFixture();
  if (layout === 'campus') return campusFixture4();
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

export const BOARD_LOGO_SHA = 'cd'.repeat(32);
const room = (name: string, kind: string, team_department: string | null, building_id: string, name_ar: string | null = null) => ({ name, name_ar, kind, team_department, building_id });
const work = (open: number, waiting: number, blocked: number) => ({ source: 'connected', open, waiting, blocked, truncated: false, by_building: {} });
const boardOf = (id: string, buildings: unknown[], rooms: unknown[], departments: string[], w: unknown) => ({
  workspace_id: id, accessible: true, as_of: now, buildings, buildings_total: buildings.length, rooms: { total: rooms.length, items: rooms }, departments, work: w,
});
/**
 * GET /api/v1/office/campus/boards, version 1 (packages/contracts/src/officeCampus.ts): the other workspaces of `campusFixture`. Executive HQ has a
 * logo and a brand colour, Tyre Pulse HQ a colour only and no open work at all (zeros), ANUM360 Commerce could not be read, and Personal Office
 * has no registry yet. Legal and Compliance is not in it (the campus says it cannot be opened).
 */
export const boardsFixture = {
  version: 1, tenant_id: tenant, current_workspace_id: workspace, generated_at: now, next_cursor: null,
  boards: [
    boardOf('workspace_exec', [{ id: 'exec-tower', name: 'Executive Tower', name_ar: 'البرج التنفيذي', floors: 3, rooms: 3, sign: { style: 'plaque', color: '#1b3a6b', logo_sha256: BOARD_LOGO_SHA } }],
      [room('Boardroom', 'meeting', null, 'exec-tower', 'قاعة المجلس'), room('Treasury', 'office', 'finance', 'exec-tower', 'الخزينة'), room('Counsel', 'office', 'legal', 'exec-tower', 'المستشار')], ['finance', 'legal'], work(4, 1, 1)),
    boardOf('workspace_tyre', [{ id: 'tyre-shop', name: 'Tyre Shop', name_ar: null, floors: 2, rooms: 2, sign: { style: null, color: '#0f5b45', logo_sha256: null } }],
      [room('Workshop', 'workshop', 'tyre_engineering', 'tyre-shop', 'الورشة'), room('Fleet Desk', 'office', 'operations', 'tyre-shop', 'مكتب الأسطول')], ['operations', 'tyre_engineering'], work(0, 0, 0)),
    { workspace_id: 'workspace_commerce', accessible: false },
    boardOf('workspace_personal', [], [], [], work(0, 0, 0)),
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

/** One SSE frame of the presence stream. */
export const sseFrame = (event: 'snapshot' | 'delta', data: unknown, id: string) => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
export const presenceItem = (agent: string, name: string, state: string, extra: Record<string, unknown> = {}) => ({
  key: agent, agent_id: agent, agent_name: name, desk_id: null, state,
  provenance: { kind: state === 'idle' ? 'none' : 'task', run_id: null, task_id: null, approval_id: null, meeting_id: null, reason: null },
  task: null, last_verified_at: new Date().toISOString(), stale: false, ...extra,
});
export const sseDelta = (sequence: number, changed: unknown[], id: string, removed: string[] = []) =>
  sseFrame('delta', { version: 1, sequence, as_of: new Date().toISOString(), totals: {}, changed, removed }, id);

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
  /** Specs flip `active` to finish the live meeting; `created` records meetings started from the UI (with their room_id);
   *  `left` adds participants whose record says they left (`left_at`). */
  const meeting = { active: options.meetings === 'live', roomId: options.meetingRoomId ?? null, created: [] as Array<Record<string, unknown>>, left: [] as Array<Record<string, unknown>> };
  const streamState = { requests: [] as Array<{ lastEventId: string | null }>, held: [] as Array<{ route: Route; timer: ReturnType<typeof setTimeout> }>, queued: [] as string[], items: presenceFixture().items as Array<Record<string, unknown>>, event: 1 };
  const sse = (route: Route, body: string) => route.fulfill({ status: 200, contentType: 'text/event-stream', headers: { 'cache-control': 'no-cache' }, body: stamp(body) });
  const snapshotFrame = () => sseFrame('snapshot', { ...presenceFixture(), as_of: new Date().toISOString(), items: streamState.items, cursor: `evt_${streamState.event}`, sequence: 0 }, `evt_${streamState.event}`);
  const stream = {
    requests: streamState.requests,
    /** How many requests wait for the server's next frame right now (a page keeps one stream open, never several). */
    open: () => streamState.held.length,
    /**
     * What the server does next on the open stream: like the real one, a connection starts with a snapshot of the current state and
     * then sends the delta, after which the stream ends (a bounded lifetime) and the page reconnects with Last-Event-ID.
     * `sequence` overrides the delta's number (a skipped number is a lost frame); `raw` sends these frames instead of a delta.
     */
    async deliver(change: { changed?: Array<Record<string, unknown>>; removed?: string[]; sequence?: number; raw?: string } = {}): Promise<void> {
      const before = snapshotFrame();
      streamState.event += 1;
      const body = change.raw ?? sseDelta(change.sequence ?? 1, change.changed ?? [], `evt_${streamState.event}`, change.removed ?? []);
      for (const item of change.changed ?? []) {
        const at = streamState.items.findIndex((i) => i.key === item.key);
        if (at >= 0) streamState.items[at] = item; else streamState.items.push(item);
      }
      const held = streamState.held.shift();
      if (held) { clearTimeout(held.timer); await sse(held.route, before + body); } else streamState.queued.push(before + body);
    },
  };
  const profileCalls: Array<{ kind: string; icon: string; expected_revision?: number }> = [];
  /** The boards read: `requests` counts reads, `logos` the other workspaces' logo reads (building id and the workspace named in the request). */
  const boardsState = { mode: options.boards ?? 'missing', requests: 0, logos: [] as Array<{ building: string; workspace: string | null }>, held: [] as Route[] };
  const boards = {
    get requests() { return boardsState.requests; },
    logos: boardsState.logos,
    /** A failing boards read answers again from now on. */
    recover() { boardsState.mode = 'api'; },
    /** Lets the held (slow) reads answer. */
    async release() { boardsState.mode = 'api'; for (const r of boardsState.held.splice(0)) await r.fulfill({ status: 200, contentType: 'application/json', body: stamp(JSON.stringify(boardsFixture)) }); },
  };
  await page.route('http://localhost:8000/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    calls.push(`${method} ${path}`);
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: stamp(JSON.stringify(value)) });
    const error = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    // The executive PA dock beside the scene: quiet by default (no tools, no tour); specs that test it register their own routes.
    if (path === '/api/v1/executive-pa/tools') return json({ tools: [] });
    if (path === '/api/v1/executive-pa/tour') return json({ status: 'empty', stops: [] });
    const listed = /^\/api\/v1\/office-spaces\/(buildings|floors|rooms|desks)$/.exec(path);
    if (listed && method === 'GET') {
      const items = office[listed[1] as keyof typeof office] as Array<Record<string, unknown>>;
      return json({ items: listed[1] === 'rooms' && options.roomEdits ? items.map((r) => ({ ...r, ...(options.roomEdits![String(r.id)] ?? {}) })) : items, next_cursor: null });
    }
    if (path === '/api/v1/me/workspace-memberships') return json(memberships);
    if (path === '/api/v1/office/campus') {
      if (options.campus === 'api') return json(campusFixture);
      if (options.campus === 'denied') return error(403, 'forbidden', 'You cannot read the campus.');
      return error(404, 'not_found', 'Not found.');
    }
    if (path === '/api/v1/office/campus/boards') {
      boardsState.requests += 1;
      if (boardsState.mode === 'missing') return error(404, 'not_found', 'Not found.');
      if (boardsState.mode === 'failing') return error(503, 'unavailable', 'The campus boards could not be read.');
      if (boardsState.mode === 'slow') { boardsState.held.push(route); return undefined; }
      return json(boardsFixture);
    }
    const otherLogo = /^\/api\/v1\/office-spaces\/buildings\/([^/]+)\/logo$/.exec(path);
    if (otherLogo && method === 'GET' && boardsState.mode !== 'missing') {
      const named = route.request().headers()['x-workspace-id'] ?? null;
      boardsState.logos.push({ building: otherLogo[1], workspace: named });
      // Only Executive HQ's building has a logo, and only when the request names Executive HQ's workspace (the API reads it in that workspace).
      if (otherLogo[1] === 'exec-tower' && named === 'workspace_exec') return route.fulfill({ status: 200, contentType: 'image/png', headers: { 'cache-control': 'private, no-cache', etag: `"${BOARD_LOGO_SHA}"` }, body: logoPng(160, 80, [27, 58, 107]) });
      return error(404, 'not_found', 'This building has no logo.');
    }
    if (path === '/api/v1/office/presence/stream') {
      const mode = options.presence === 'missing' ? 'unsupported' : options.stream ?? 'unsupported';
      streamState.requests.push({ lastEventId: route.request().headers()['last-event-id'] ?? null });
      if (mode === 'unsupported') return error(404, 'not_found', 'Not found.');
      if (mode === 'denied') return error(403, 'forbidden', 'You cannot read presence.');
      if (mode === 'down') return route.abort('connectionrefused');
      if (mode === 'blocked') return route.fulfill({ status: 200, contentType: 'text/html', body: '<html>proxy</html>' });
      const queued = streamState.queued.shift();
      if (streamState.requests.length === 1) return sse(route, snapshotFrame());
      if (queued !== undefined) return sse(route, queued);
      // A quiet stream: the snapshot comes at once on a real server; here the answer waits a moment so the page does not reconnect in a tight loop.
      const timer = setTimeout(() => { const at = streamState.held.findIndex((h) => h.route === route); if (at >= 0) streamState.held.splice(at, 1); void sse(route, snapshotFrame()).catch(() => undefined); }, 1500);
      streamState.held.push({ route, timer });
      return undefined;
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
    if (path === '/api/v1/agent-meetings' && method === 'POST') {
      const body = route.request().postDataJSON() as { title: string; room_id?: string };
      const made = { id: `meeting_new_${meeting.created.length + 1}`, title: body.title, status: 'scheduled', revision: 1, room_id: body.room_id ?? null };
      meeting.created.push(made);
      return json(made, 201);
    }
    if (path === '/api/v1/agent-meetings') {
      if (options.meetings === 'failing') return error(503, 'unavailable', 'Meetings are not reachable.');
      const wantsActive = url.searchParams.get('status') === 'active';
      const all = [...(meeting.active ? [{ id: 'meeting_live', title: 'Weekly sync', status: 'active', revision: 1, room_id: meeting.roomId }] : []),
        ...(wantsActive ? [] : meeting.created)];
      return json({ items: all, next_cursor: null });
    }
    if (path === '/api/v1/agent-meetings/meeting_live') return json({ id: 'meeting_live', title: 'Weekly sync', status: 'active', revision: 1, participants: [{ id: 'p1', session_id: 'meeting_live', actor_id: 'agent_care', kind: 'agent', left_at: null }, ...meeting.left] });
    if (path === '/api/v1/commerce/setup') {
      if (options.commerce === 'denied') return error(403, 'forbidden', 'You need commerce access.');
      return json({ permissions: {}, connectors: options.commerce === 'connected' ? 1 : 0, channels: [], own_store_modes: [], sync_scheduler: false });
    }
    if (path === '/api/v1/commerce/dashboard') {
      return json({ start: '2026-10-22', end: '2026-10-22', tz: 'Asia/Dubai', currency: 'AED', currencies: [], by_channel: [{ channel: 'amazon', label: 'Amazon', marketplace_id: 'x', marketplace: 'Amazon.ae', currency: 'AED', revenue: 100, orders: 12, units: 14 }, { channel: 'noon', label: 'noon', marketplace_id: 'y', marketplace: 'noon UAE', currency: 'AED', revenue: 50, orders: 5, units: 6 }],
        by_day: [], top_products: [], pending_orders: [], pending_total: 7, late_total: 2, low_stock: [{ listing_id: 'l1', sku: 'SKU-1', title: 'Sample tyre', channel: 'amazon', marketplace: 'Amazon.ae', quantity: 1, threshold: 5 }, { listing_id: 'l2', sku: 'SKU-2', title: 'Sample rim', channel: 'noon', marketplace: 'noon UAE', quantity: 0, threshold: 3 }], returns: { count: 3, units: 3, amount: {}, rate: 0.1 }, price_alerts: [], open_alerts: 1, fees: {}, truncated: false, connectors: 1 });
    }
    if (path === '/api/v1/tyre/setup') return json({ connection: options.tyre === 'connected' ? { kind: 'supabase' } : null, mapping: null, mapping_problems: [], assumptions: {}, discovery: null, sync: null, fields: {}, presets: [], can_manage: true, can_operate: true, delivery_channels: [] });
    if (path === '/api/v1/tyre/dashboard') return json({ summary: { as_of: now, currency: 'AED', vehicles: 42, tyres_fitted: 250, tyres_in_stock: 30, tyres_removed_last_30_days: 4, tyres_with_readings: 200, average_tread_mm: 8, below_minimum: 3, in_warning_band: 5, pressure_checked: 100, pressure_compliant_pct: 90, under_inflated: 2, over_inflated: 1, overdue_inspections: 6, due_next_30_days: 11, due_next_90_days: 20, cost_per_1000_km: null, cost_per_hour: null, min_tread_mm: 3 }, synced_at: now, alert_counts: { critical: 1, warning: 2 }, top_alerts: [], due_soon: [], due_by_month: {} });
    return route.fallback();
  });
  return { calls, profileCalls, meeting, stream, boards };
}
