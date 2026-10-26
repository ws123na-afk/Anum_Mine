import type { Page, Route } from '@playwright/test';

// Mock of the agents list, the assignment route, the task read and the assistant for the "assign work" and
// "add a building" specs (docs/ANUM_MINE_ASSIGN_WORK.md). Names are plainly fake test data; agent ids match
// hqRegistry(TENANT_A, { agentIds: ['agent_fleet', 'agent_leads', 'agent_care', 'agent_invoice'] }).
const stamp = '2026-10-20T08:00:00Z';
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

export const agentRow = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', owner_user_id: 'user_local', name, icon: 'bot', color: '#0f766e', category: 'work', department: 'Operations',
  description: '', instructions: '', model_route: 'fast', allowed_skills: [], allowed_tools: ['anum.respond', 'tyre_plan', 'send_email'],
  autonomy: { read: 'auto', write: 'ask', external: 'ask' }, knowledge_mode: 'none', knowledge_sources: [], sharing: 'workspace', template_id: null,
  status: 'active', created_at: stamp, updated_at: stamp, version: 3, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], ...over,
});

export interface AssignMock {
  requests: { method: string; path: string; headers: Record<string, string>; body: Record<string, unknown> | null }[];
  agents: Record<string, unknown>[];
  failNext(status: number, detail: string): void;
  onCreated(handler: (task: { id: string; title: string; agent_id: string }) => void): void;
  proposalState: { canConfirm: boolean; failConfirm: { status: number; detail: string } | null };
  posts(path: string): { headers: Record<string, string>; body: Record<string, unknown> | null }[];
}

export async function mockAssign(page: Page): Promise<AssignMock> {
  const agents = [
    agentRow('agent_fleet', 'Fleet Planner'), agentRow('agent_leads', 'Lead Finder', { department: 'Sales' }),
    agentRow('agent_paused', 'Paused Clerk', { status: 'paused', can_run: false }),
    agentRow('agent_locked', 'Locked Auditor', { can_run: false, can_edit: false }),
    agentRow('agent_care', 'WhatsApp Concierge'), agentRow('agent_invoice', 'Invoice Checker'),
  ];
  let failure: { status: number; detail: string } | null = null;
  let created: (task: { id: string; title: string; agent_id: string }) => void = () => undefined;
  const byKey = new Map<string, unknown>();
  let counter = 0;
  const mock: AssignMock = {
    requests: [], agents, proposalState: { canConfirm: true, failConfirm: null },
    failNext(status, detail) { failure = { status, detail }; }, onCreated(h) { created = h; },
    posts(path) { return mock.requests.filter((r) => r.method === 'POST' && r.path === path); },
  };
  const log = async (route: Route) => {
    const r = route.request(); const u = new URL(r.url()); const raw = r.postData();
    mock.requests.push({ method: r.method(), path: u.pathname, headers: await r.allHeaders(), body: raw ? JSON.parse(raw) : null });
    return u;
  };
  await page.route(/http:\/\/localhost:8000\/api\/v1\/agents(\/[A-Za-z0-9_-]+)?(\?.*)?$/, async (route) => {
    const u = await log(route);
    const tail = u.pathname.replace('/api/v1/agents', '');
    if (tail === '/catalog') return json(route, { tools: [
      { name: 'anum.respond', description: '', risk_level: 'low', category: 'read', always_on: true, available_to_you: true, reason: null },
      { name: 'tyre_plan', description: '', risk_level: 'low', category: 'read', always_on: false, available_to_you: true, reason: null },
      { name: 'send_email', description: '', risk_level: 'high', category: 'external', always_on: false, available_to_you: true, reason: null }],
      skills: [], templates: [], icons: [], autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' } });
    if (tail === '') {
      const q = (u.searchParams.get('q') ?? '').toLowerCase();
      const items = agents.filter((a) => !q || String(a.name).toLowerCase().includes(q));
      return json(route, { items, next_cursor: null, counts: { all: items.length, work: items.length, personal: 0, custom: 0 } });
    }
    const agent = agents.find((a) => `/${a.id}` === tail);
    if (!agent) return json(route, { detail: 'Agent not found' }, 404);
    if (route.request().method() === 'PATCH') {
      Object.assign(agent, { status: 'active', can_run: true, version: 4 });
      return json(route, agent);
    }
    return json(route, agent);
  });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/assignments$/, async (route) => {
    await log(route);
    if (failure) { const f = failure; failure = null; return json(route, { detail: f.detail }, f.status); }
    const key = (await route.request().allHeaders())['idempotency-key'];
    const body = JSON.parse(route.request().postData() ?? '{}');
    if (byKey.has(key)) return json(route, { ...(byKey.get(key) as object), replayed: true }, 200);
    counter += 1;
    const task = { id: `task_new${counter}`, title: body.title, status: 'running', agent_id: body.agent_id, priority: body.priority, due_at: body.due_at, created_at: new Date().toISOString() };
    const out = { task, replayed: false, run: body.run ? { id: `run_new${counter}`, status: 'running' } : null, approval: null, run_error: null };
    byKey.set(key, out);
    created(task);
    return json(route, out, 201);
  });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/tasks\/task_new\d+(\/runs)?(\?.*)?$/, async (route) => {
    const u = await log(route);
    const id = u.pathname.split('/')[4];
    const posted = mock.requests.filter((r) => r.path === '/api/v1/office/assignments').map((r) => r.body).filter(Boolean).at(-1) as Record<string, string> | undefined;
    if (u.pathname.endsWith('/runs')) return json(route, [{ id: 'run_new1', task_id: id, status: 'running', steps: [{ id: 's1', type: 'planning', summary: 'Planning the work', created_at: stamp }], result: null, created_at: stamp, updated_at: stamp, checkpoint: { phase: 'executing' } }]);
    return json(route, { id, title: posted?.title ?? 'Task', prompt: `${posted?.instructions ?? ''}`, status: 'running', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: stamp, updated_at: stamp, agent_id: posted?.agent_id ?? null });
  });
  return mock;
}

/** The assistant: tools, an empty tour, and `propose_add_building` / confirm like the API (proposal only; a click confirms). */
export async function mockBuildingAssistant(page: Page, mock: AssignMock): Promise<void> {
  const proposal = (name: string, floors: number, lang: string) => {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const bid = `bld-${slug}`;
    const fl = Array.from({ length: floors }, (_, level) => ({ id: `${bid}-f${level}`, level, name: level === 0 ? 'Ground floor' : `Floor ${level}`, width_m: 60, depth_m: 40,
      rooms: (level === 0 ? ['reception', 'open_office'] : ['open_office', 'meeting_room']).map((t, i) => ({ id: `${bid}-f${level}-${t}`, template_id: t, name: t === 'reception' ? 'Reception' : t === 'open_office' ? 'Open office' : 'Meeting room', name_ar: t === 'reception' ? 'الاستقبال' : t === 'open_office' ? 'مكتب مفتوح' : 'غرفة اجتماعات', kind: t, capacity: 4, x: i * 8, z: 0, width: 6, depth: 4 })) }));
    return { content: { lang, building: { id: bid, name, timezone: 'Asia/Dubai' }, floors: fl, totals: { buildings: 1, floors, rooms: floors * 2, desks: 0, agents: 0 } },
      payload_hash: 'cd'.repeat(32), bounds: { width_m: 60, depth_m: 40 }, creates: { buildings: 1, floors, rooms: floors * 2, desks: 0, agents: 0 },
      can_confirm: mock.proposalState.canConfirm, governed_by: 'builder API', note: '', version: 1 };
  };
  await page.route(/http:\/\/localhost:8000\/api\/v1\/executive-pa\/invoke$/, async (route) => {
    const r = route.request(); const body = JSON.parse(r.postData() ?? '{}');
    mock.requests.push({ method: 'POST', path: '/api/v1/executive-pa/invoke', headers: await r.allHeaders(), body });
    if (body.tool === 'propose_add_building') {
      const a = body.arguments as { name?: string; floors?: number; lang?: string };
      if (!a.name) return json(route, { tool: body.tool, status: 'needs_info', missing: 'name', question: 'A building needs a name.' });
      if (!a.floors) return json(route, { tool: body.tool, status: 'needs_info', missing: 'floors', question: 'How many floors?' });
      return json(route, { tool: body.tool, status: 'proposal', ...proposal(a.name, a.floors, a.lang ?? 'en') });
    }
    return json(route, { tool: body.tool, status: 'denied' });
  });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/building-proposals\/confirm$/, async (route) => {
    const r = route.request(); const body = JSON.parse(r.postData() ?? '{}');
    mock.requests.push({ method: 'POST', path: '/api/v1/office/building-proposals/confirm', headers: await r.allHeaders(), body });
    const f = mock.proposalState.failConfirm;
    if (f) return json(route, { detail: f.detail }, f.status);
    return json(route, { version: 1, replayed: false, building: { id: `bld-${String(body.name).toLowerCase().replace(/[^a-z0-9]+/g, '-')}`, name: body.name }, floor_ids: [], room_ids: [] }, 201);
  });
}
