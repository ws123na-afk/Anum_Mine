import type { Page, Route } from '@playwright/test';

// Mock of the office history API (docs/ANUM_MINE_F09_AUDITED_HISTORY_API.md) and of the source reads the
// panel makes (task, approval, audit export). It repeats the server's rules: records newest first, filters
// on actor, text and dates, an opaque cursor, and the category applied AFTER paging (so a page can be empty
// while a cursor is set). Names and ids are plainly fake. Room and desk ids match hqRegistry(TENANT_A, ...).
const WS = 'workspace_foundation';
const BASE = Date.parse('2026-10-20T12:00:00Z');

export interface HistoryRecord {
  id: string; timestamp: string; actor: string; action: string; target: string; category: string; outcome: string;
  correlation_id: string; metadata: Record<string, unknown>; source: string; verified: boolean;
}

const PREFIX: [string, string][] = [
  ['agent_meeting.', 'meeting'], ['office.', 'office'], ['work_item.', 'team_work'], ['task.', 'task'], ['agent_run.', 'agent_run'],
  ['approval.', 'approval'], ['workspace.', 'workspace'], ['workspace_member.', 'membership'], ['automation.', 'automation'], ['autonomy.', 'autonomy'],
];
export const categoryOf = (action: string) => PREFIX.find(([p]) => action.startsWith(p))?.[1] ?? 'other';

let counter = 0;
export function record(action: string, target: string, minutesAgo: number, metadata: Record<string, unknown> = {}, extra: Partial<HistoryRecord> = {}): HistoryRecord {
  counter += 1;
  return {
    id: `audit_${String(counter).padStart(4, '0')}`, timestamp: new Date(BASE - minutesAgo * 60_000).toISOString(), actor: 'owner_example', action, target,
    category: categoryOf(action), outcome: 'success', correlation_id: `corr_${counter}`, metadata, source: 'workspace_audit', verified: true, ...extra,
  };
}

const roomSnap = (name: string, extra: Record<string, unknown> = {}) => ({
  id: 'Alpha-room-0', revision: 1, floor_id: 'Alpha-floor-0', name, kind: 'office', capacity: 6, scene_x: 0, scene_z: 0, scene_width: 6, scene_depth: 6, ...extra,
});
const deskSnap = (agent: string | null) => ({ id: 'Alpha-desk-0', revision: 1, room_id: 'Alpha-room-0', label: 'Alpha Desk 1', agent_id: agent, pos_x: 0, pos_z: 0 });

/** The fixture: Alpha Room 1 (Alpha-room-0) has a recorded life with one unexplained change; 130 filler task records follow for paging. */
export function historyFixture(): HistoryRecord[] {
  counter = 0;
  const out: HistoryRecord[] = [
    record('office.room.create', 'Alpha-room-0', 6000, { resource_kind: 'rooms', after: roomSnap('Alpha Room 1') }),
    record('office.room.rename', 'Alpha-room-0', 5000, { resource_kind: 'rooms', before: roomSnap('Alpha Room 1'), after: roomSnap('Front Desk') }),
    record('office.desk.assignment', 'Alpha-desk-0', 4000, { resource_kind: 'desks', before: deskSnap(null), after: deskSnap('agent_fleet') }),
    record('office.room.geometry', 'Alpha-room-0', 3000, { resource_kind: 'rooms', before: roomSnap('Front Desk'), after: roomSnap('Front Desk', { scene_x: 9 }) }),
    // Something changed the room without a record: this record starts from a name the previous one did not end with.
    record('office.room.rename', 'Alpha-room-0', 2000, { resource_kind: 'rooms', before: roomSnap('Lobby', { scene_x: 9 }), after: roomSnap('Welcome Hall', { scene_x: 9 }) }),
    record('office.room.rename', 'Alpha-room-1', 1900, { resource_kind: 'rooms', before: { ...roomSnap('Alpha Room 2'), id: 'Alpha-room-1' }, after: { ...roomSnap('Sales'), id: 'Alpha-room-1' } }),
    record('approval.approved', 'ap_1', 1500, { task_id: 'task_1' }),
    record('agent_meeting.create', 'meeting_1', 1400, {}),
    record('task.updated', 'task_1', 1300, { fields: ['title'] }),
  ];
  for (let i = 0; i < 130; i += 1) out.push(record('task.updated', `task_filler_${i}`, 7000 + i * 5, { fields: ['status'] }, { actor: i % 2 ? 'owner_example' : 'agent_fleet' }));
  // Newest first, like the server.
  return out.sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp) || b.id.localeCompare(a.id));
}

export interface HistoryMock { requests: URL[]; exports: URL[]; setRecords(r: HistoryRecord[]): void; failNext(): void }
export interface HistoryMockOptions { mode?: 'ok' | 'denied' | 'error' | 'empty'; /** The audit export answers 403 (a viewer without audit:export). */ exportDenied?: boolean; /** When set, answers the workspace permissions read with this export flag. */ canExport?: boolean; records?: HistoryRecord[]; hold?: () => Promise<void> }

function page(records: HistoryRecord[], url: URL) {
  const limit = Math.min(200, Number(url.searchParams.get('limit') ?? 60));
  const cursor = url.searchParams.get('cursor');
  const start = cursor ? Number(Buffer.from(cursor, 'base64url').toString()) : 0;
  const since = url.searchParams.get('since');
  const until = url.searchParams.get('until');
  const q = url.searchParams.get('q')?.toLowerCase();
  const actor = url.searchParams.get('actor');
  const corr = url.pathname.split('/correlations/')[1];
  const matching = records.filter((r) => (!actor || r.actor === actor)
    && (!corr || r.correlation_id === decodeURIComponent(corr))
    && (!since || Date.parse(r.timestamp) >= Date.parse(since))
    && (!until || Date.parse(r.timestamp) < Date.parse(until))
    && (!q || `${r.actor} ${r.action} ${r.target}`.toLowerCase().includes(q)));
  const slice = matching.slice(start, start + limit);
  const category = url.searchParams.get('category') ?? 'all';
  const next = start + limit < matching.length ? Buffer.from(String(start + limit)).toString('base64url') : null;
  return {
    items: slice.filter((r) => category === 'all' || r.category === category), next_cursor: next, workspace_id: WS, category,
    evidence_notice: 'x', historical_coverage_complete: false,
  };
}

export async function mockHistory(p: Page, options: HistoryMockOptions = {}): Promise<HistoryMock> {
  let records = options.records ?? historyFixture();
  let failures = 0;
  const mock: HistoryMock = { requests: [], exports: [], setRecords: (r) => { records = r; }, failNext: () => { failures += 1; } };
  const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await p.route(/http:\/\/localhost:8000\/api\/v1\/office-history(\/.*)?(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    mock.requests.push(url);
    await options.hold?.();
    if (options.mode === 'denied') return json(route, 403, { detail: 'Work-history replay is restricted to workspace owners and authorized organization administrators.' });
    if (failures > 0 || options.mode === 'error') { failures = Math.max(0, failures - 1); return json(route, 500, { detail: 'The audit store is busy.' }); }
    return json(route, 200, page(options.mode === 'empty' ? [] : records, url));
  });
  if (options.canExport !== undefined) {
    await p.route(/http:\/\/localhost:8000\/api\/v1\/workspaces\/current\/permissions(\?.*)?$/, (route) => json(route, 200, {
      workspace_id: WS, role: 'owner', organization_admin: false, can_manage_workspace: true, can_create_workspace: true, can_manage_members: true, can_read_audit: true, can_export_audit: options.canExport,
    }));
  }
  await p.route(/http:\/\/localhost:8000\/api\/v1\/tasks\/task_1(\?.*)?$/, (route) => json(route, 200, {
    id: 'task_1', title: 'Plan October tyre rotation', prompt: 'private words that must not show', status: 'running', tenant_id: 'tenant_local', workspace_id: WS,
    created_at: '2026-10-20T09:00:00Z', updated_at: '2026-10-20T10:00:00Z', agent_id: 'agent_fleet',
  }));
  await p.route(/http:\/\/localhost:8000\/api\/v1\/approvals\/ap_1(\?.*)?$/, (route) => json(route, 404, { detail: 'Approval not found' }));
  await p.route(/http:\/\/localhost:8000\/api\/v1\/audit\/export(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    mock.exports.push(url);
    if (options.exportDenied) return json(route, 403, { detail: 'Permission audit:export is required' });
    if (url.searchParams.get('format') === 'json') {
      const action = url.searchParams.get('action');
      const q = url.searchParams.get('q')?.toLowerCase();
      const actor = url.searchParams.get('actor');
      const rows = records
        .filter((r) => (!actor || r.actor === actor) && (!q || `${r.actor} ${r.action} ${r.target}`.toLowerCase().includes(q)))
        .filter((r) => !action || (action.endsWith('*') ? r.action.startsWith(action.slice(0, -1)) : r.action === action))
        .map((r) => ({ id: r.id, created_at: r.timestamp, actor: r.actor, action: r.action, target: r.target, outcome: r.outcome, correlation_id: r.correlation_id, tenant_id: 'tenant_local', workspace_id: WS, metadata: r.metadata }));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) });
    }
    await route.fulfill({ status: 200, contentType: 'text/csv; charset=utf-8', headers: { 'content-disposition': 'attachment; filename="audit-example.csv"', 'access-control-expose-headers': 'content-disposition' }, body: 'id,action\naudit_0001,office.room.create\n' });
  });
  return mock;
}
