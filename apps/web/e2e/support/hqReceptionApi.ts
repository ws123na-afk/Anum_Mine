import type { Page, Route } from '@playwright/test';

// Mock of the reception visitor register (docs/ANUM_MINE_HQ_RECEPTION.md): an in-memory store with the server's rules
// (owner-only writes on a click, revisions, status moves, keyset paging). Names are plainly fake test data.
export type ReceptionMode = 'owner' | 'viewer' | 'denied' | 'failing';
export interface MockVisit {
  id: string; visitor_name: string; company: string | null; host: { kind: 'user' | 'agent'; id: string } | null; purpose: string;
  status: 'expected' | 'checked_in' | 'checked_out' | 'cancelled'; expected_at: string; checked_in_at: string | null; checked_out_at: string | null;
  cancelled_at: string | null; building_id: string | null; room_id: string | null; revision: number; created_by: string; updated_by: string;
  created_at: string; updated_at: string;
}

export function fakeVisit(i: number, over: Partial<MockVisit> = {}): MockVisit {
  const at = new Date(Date.now() + i * 60_000).toISOString();
  return {
    id: `visit-${String(i).padStart(3, '0')}`, visitor_name: `Visitor Example ${i}`, company: i % 2 ? 'Example Trading' : null, host: null,
    purpose: 'Example meeting', status: 'expected', expected_at: at, checked_in_at: null, checked_out_at: null, cancelled_at: null,
    building_id: null, room_id: null, revision: 1, created_by: 'owner', updated_by: 'owner', created_at: at, updated_at: at, ...over,
  };
}

export interface ReceptionMock { visits: MockVisit[]; calls: Array<{ method: string; path: string; body: unknown }>; mode: ReceptionMode; failOnce: boolean }

export async function mockReception(page: Page, options: { visits?: MockVisit[]; mode?: ReceptionMode; members?: Array<{ user_id: string; email: string }> } = {}): Promise<ReceptionMock> {
  const mock: ReceptionMock = { visits: options.visits ?? [], calls: [], mode: options.mode ?? 'owner', failOnce: options.mode === 'failing' };
  const json = (route: Route, value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  const error = (route: Route, status: number, message: string) => json(route, { error: { code: status === 403 ? 'forbidden' : status === 409 ? 'conflict' : 'error', message, correlation_id: 'corr', details: [] } }, status);
  await page.route(/http:\/\/localhost:8000\/api\/v1\/workspace-members(\?.*)?$/, (route) => json(route, (options.members ?? [{ user_id: 'owner_example', email: 'owner@example.test' }]).map((m) => ({
    tenant_id: 't', workspace_id: 'w', user_id: m.user_id, role: 'owner', active: true, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', email: m.email,
  }))));
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/visitors(\/.*)?(\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    mock.calls.push({ method, path: path + url.search, body });
    if (mock.mode === 'denied') return error(route, 403, 'Workspace permission denied');
    if (mock.failOnce && method === 'GET' && path === '/api/v1/office/visitors') { mock.failOnce = false; return error(route, 500, 'An unexpected error occurred'); }
    const now = new Date().toISOString();
    if (path === '/api/v1/office/visitors/summary') {
      const inside = mock.visits.filter((v) => v.status === 'checked_in');
      const byRoom = new Map<string, number>();
      for (const v of inside) if (v.room_id) byRoom.set(v.room_id, (byRoom.get(v.room_id) ?? 0) + 1);
      return json(route, { version: 1, checked_in: inside.length, by_room: [...byRoom].map(([room_id, count]) => ({ room_id, count })), by_building: [], unplaced: inside.filter((v) => !v.room_id && !v.building_id).length, as_of: now });
    }
    if (path === '/api/v1/office/visitors' && method === 'GET') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const status = url.searchParams.get('status');
      const limit = Number(url.searchParams.get('limit') ?? 25);
      const after = url.searchParams.get('cursor');
      const base = mock.visits.filter((v) => !q || [v.visitor_name, v.company ?? '', v.purpose].some((s) => s.toLowerCase().includes(q)))
        .sort((a, b) => a.expected_at.localeCompare(b.expected_at) || a.id.localeCompare(b.id));
      const counts = { expected: 0, checked_in: 0, checked_out: 0, cancelled: 0 };
      for (const v of base) counts[v.status] += 1;
      const matching = base.filter((v) => !status || v.status === status);
      const start = after ? matching.findIndex((v) => v.id === after) + 1 : 0;
      const items = matching.slice(start, start + limit);
      const more = start + limit < matching.length;
      return json(route, { version: 1, items, next_cursor: more ? items[items.length - 1].id : null, total: matching.length, counts, can_manage: mock.mode === 'owner', as_of: now });
    }
    if (mock.mode !== 'owner') return error(route, 403, 'Workspace permission denied');
    if (path === '/api/v1/office/visitors' && method === 'POST') {
      if (body?.input_mode !== 'click') return error(route, 403, 'Confirm this on screen with a click.');
      const arrived = body.arrived === true;
      const made: MockVisit = {
        ...fakeVisit(mock.visits.length + 100), id: String(body.id), visitor_name: String(body.visitor_name), company: (body.company as string | null) ?? null,
        purpose: String(body.purpose), expected_at: String(body.expected_at), room_id: (body.room_id as string | null) ?? null,
        host: body.host_kind ? { kind: body.host_kind as 'user' | 'agent', id: String(body.host_id) } : null,
        status: arrived ? 'checked_in' : 'expected', checked_in_at: arrived ? now : null,
      };
      mock.visits.push(made);
      return json(route, made, 201);
    }
    const one = /^\/api\/v1\/office\/visitors\/([^/]+)(?:\/(check-in|check-out|cancel))?$/.exec(path);
    const visit = one ? mock.visits.find((v) => v.id === decodeURIComponent(one[1])) : undefined;
    if (!one || !visit) return error(route, 404, 'Visit not found');
    const revision = Number(body?.expected_revision ?? url.searchParams.get('expected_revision'));
    const mode = body?.input_mode ?? url.searchParams.get('input_mode');
    if (mode !== 'click') return error(route, 403, 'Confirm this on screen with a click.');
    if (revision !== visit.revision) return error(route, 409, 'The visit changed; reload and try again');
    if (method === 'DELETE') { mock.visits = mock.visits.filter((v) => v !== visit); return route.fulfill({ status: 204, body: '' }); }
    const move = one[2];
    if (move === 'check-in') { if (visit.status !== 'expected') return error(route, 409, 'Only an expected visitor can be checked in'); Object.assign(visit, { status: 'checked_in', checked_in_at: now }); }
    else if (move === 'check-out') { if (visit.status !== 'checked_in') return error(route, 409, 'Only a checked-in visitor can be checked out'); Object.assign(visit, { status: 'checked_out', checked_out_at: now }); }
    else if (move === 'cancel') { if (visit.status !== 'expected') return error(route, 409, 'Only an expected visit can be cancelled'); Object.assign(visit, { status: 'cancelled', cancelled_at: now }); }
    else if (method === 'PATCH' && body) {
      for (const k of ['visitor_name', 'company', 'purpose', 'expected_at', 'room_id'] as const) if (k in body) (visit as unknown as Record<string, unknown>)[k] = body[k];
      if ('host_kind' in body) visit.host = body.host_kind ? { kind: body.host_kind as 'user' | 'agent', id: String(body.host_id) } : null;
    }
    visit.revision += 1;
    visit.updated_at = now;
    return json(route, visit);
  });
  return mock;
}
