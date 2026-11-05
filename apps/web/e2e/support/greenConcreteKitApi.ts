import type { Page, Route } from '@playwright/test';

// Mocked Green Concrete office and agents kit (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md). The office has one role (Tyre
// planning engineer) with Shahzeb's agent; the kit has two skills (one with its data, one Not connected), one gap, no
// knowledge collection yet and one routine that can be added. Memories of the agent live in this mock.
const H = (c: string) => c.repeat(64);
const now = '2026-11-02T08:00:00Z';
type Json = Record<string, unknown>;

export interface KitMockOptions { kit?: 'ok' | 'denied' | 'missing' | 'error'; role?: 'owner' | 'viewer' }
export interface KitMock { setups: Json[]; switches: Json[]; memoryWrites: Json[]; failOnce: boolean }

const officeSummary = {
  building_id: 'gc-green-concrete', name: { en: 'Green Concrete', ar: null }, kind: 'green_concrete', kind_name: { en: 'Green Concrete operations', ar: 'عمليات جرين كونكريت' },
  units: [{ id: 'green_concrete', name: { en: 'Green Concrete operations', ar: 'عمليات جرين كونكريت' } }], roles_total: 1, roles_staffed: 1, missing_roles: [],
  agents_total: 1, agents_active: 0, agents_needing_update: 1, sources_not_connected: 1, sources: [{ id: 'tyre_pulse', state: 'not_connected' }],
};

function office(lang: string, viewer: boolean): Json {
  const agent = { id: 'agent_shahzeb', name: 'Shahzeb', status: 'paused', desk_id: 'gc-d4', can_edit: !viewer, skills: [], tools: [],
    missing_tools: ['gc.tyre_plan'], missing_skills: ['gc.tyre-plan'], missing_rules: [], over_risk_tools: [], missing_knowledge: false, complete: false };
  return {
    ...officeSummary, version: 1, blueprint_version: 1, as_of: now, lang,
    roles: [{ key: 'gc_tyre_planner', template_id: 'fleet_maintenance', unit: 'green_concrete', name: { en: 'Fleet Maintenance Planner', ar: 'مخطط صيانة الأسطول' },
      purpose: { en: 'Tyre planning engineer: plans rotations, replacements and purchases from Tyre Pulse data.', ar: 'مهندس تخطيط الإطارات.' }, department: 'tyre_engineering',
      rules: { autonomy: { read: 'auto', write: 'off', external: 'off' }, max_risk: 'low', approvals: [], monthly_cost_limit_usd: 6, never: [] },
      tools: [{ name: 'gc.tyre_plan', state: 'present', reason: null, risk: 'low', category: 'read' }], skills: [{ id: 'gc.tyre-plan', name: 'Tyre planning', state: 'present', reason: null, source: 'builtin' }],
      attach_tools: ['gc.tyre_plan'], attach_skills: ['gc.tyre-plan'], missing: [], limited: false, sources: ['tyre_pulse'], agents: [agent], state: 'needs_update' }],
    sources: [{ id: 'tyre_pulse', state: 'not_connected', kind: 'connector', name: { en: 'Tyre Pulse fleet sync', ar: 'مزامنة أسطول تاير بلس' }, view: 'tyre', roles: ['gc_tyre_planner'] }],
    other_agents: [], actions: viewer ? [] : [{ id: 'update:agent_shahzeb', kind: 'update', role: 'gc_tyre_planner', agent_id: 'agent_shahzeb', hash: H('c'),
      content: { kind: 'update', role: 'gc_tyre_planner', agent_id: 'agent_shahzeb', version: 1, add_tools: ['gc.tyre_plan'], add_skills: ['gc.tyre-plan'], autonomy: null, add_rules_text: false, monthly_cost_limit_usd: null, add_collection: null } }],
    can_apply: !viewer, limits: { agents: 10, max_agents: 200 },
  };
}

const text = (en: string, ar: string) => ({ en, ar });

function kit(state: { knowledge: boolean; routine: 'missing' | 'off' | 'on' }, viewer: boolean, memories: number): Json {
  const items = [] as Json[];
  if (!state.knowledge) items.push({ id: 'knowledge', kind: 'knowledge', content: { kind: 'knowledge' }, hash: H('d') });
  if (state.routine === 'missing') items.push({ id: 'routine:daily_ops_brief', kind: 'routine', content: { kind: 'routine' }, hash: H('e') });
  return {
    version: 1, kit_version: 1, as_of: now, lang: 'en', building: { id: 'gc-green-concrete', name: 'Green Concrete' },
    skills: [
      { id: 'gc.daily-ops-brief', version: '1.0.0', name: text('Daily operations brief', 'الموجز اليومي للعمليات'), description: text('Prepares the manager\'s brief for the start of the day.', 'يعد موجز المدير لبداية اليوم.'),
        tool: 'gc.ops_brief', inputs: [text('none', 'لا شيء')], output: text('A brief in a fixed order.', 'موجز بترتيب ثابت.'), guardrails: [text('Never invent a figure.', 'لا تختلق أي رقم.')],
        sources: [{ id: 'tasks_approvals', state: 'available' }], state: 'ready', roles: ['gc_pmv_manager'] },
      { id: 'gc.tyre-plan', version: '1.0.0', name: text('Tyre planning', 'تخطيط الإطارات'), description: text('Plans tyre rotations, replacements and purchases.', 'يخطط لتدوير الإطارات.'),
        tool: 'gc.tyre_plan', inputs: [text('months to plan', 'عدد الأشهر')], output: text('A tyre plan.', 'خطة إطارات.'), guardrails: [text('A purchase is a proposal.', 'الشراء اقتراح.')],
        sources: [{ id: 'tyre_pulse', state: 'not_connected' }], state: 'not_connected', roles: ['gc_tyre_planner'] },
    ],
    tools: [
      { name: 'gc.ops_brief', label: text('Daily operations brief', 'الموجز اليومي للعمليات'), description: 'read-only', risk: 'low', registered: true, read_only: true, permissions: ['task:read'], sources: ['tasks_approvals'] },
      { name: 'gc.tyre_plan', label: text('Tyre plan', 'خطة الإطارات'), description: 'read-only', risk: 'low', registered: true, read_only: true, permissions: ['tyre:read'], sources: ['tyre_pulse'] },
    ],
    roles: [
      { key: 'gc_pmv_manager', name: text('Operations Dispatcher', 'منسق العمليات'), purpose: text('PMV manager', 'مدير PMV'), max_risk: 'medium', skills: ['gc.daily-ops-brief'], tools: ['gc.ops_brief'] },
      { key: 'gc_tyre_planner', name: text('Fleet Maintenance Planner', 'مخطط صيانة الأسطول'), purpose: text('Tyre planning engineer', 'مهندس تخطيط الإطارات'), max_risk: 'low', skills: ['gc.tyre-plan'], tools: ['gc.tyre_plan'] },
    ],
    agents: [{ id: 'agent_shahzeb', name: 'Shahzeb', status: 'paused', role: 'gc_tyre_planner', can_edit: !viewer, skills: [], tools: [], reads_collection: false, memories }],
    sources: [{ id: 'tasks_approvals', state: 'available', name: text('Tasks and approvals', 'المهام والموافقات'), view: 'tasks' },
      { id: 'tyre_pulse', state: 'not_connected', name: text('Tyre Pulse fleet sync', 'مزامنة أسطول تاير بلس'), view: 'tyre' }],
    gaps: [{ id: 'fuel', name: text('Fuel use and fuel cost', 'استهلاك الوقود وتكلفته'), detail: text('ANUM has no fuel data source.', 'لا يوجد مصدر لبيانات الوقود.') }],
    knowledge: state.knowledge ? { state: 'empty', collection: { id: 'kbc_gc', name: 'Green Concrete knowledge', item_count: 0, description: '' } } : { state: 'missing', collection: null },
    routines: [{ key: 'daily_ops_brief', name: text('Green Concrete: daily operations brief', 'جرين كونكريت: الموجز اليومي للعمليات'),
      description: text('Every day at 06:00, the start of the morning shift.', 'كل يوم الساعة 06:00.'), role: 'gc_pmv_manager', skill: 'gc.daily-ops-brief', cron: '0 6 * * *',
      timezone: 'Asia/Dubai', state: state.routine, workflow_id: state.routine === 'missing' ? null : 'wf_1', schedule_id: state.routine === 'missing' ? null : 'sc_1',
      next_run_at: state.routine === 'on' ? '2026-11-03T02:00:00Z' : null, agent: { id: 'agent_pmv', name: 'Ahmed Elbendari', status: 'paused' } }],
    items: viewer ? [] : items, can_manage: !viewer, can_manage_automations: !viewer,
  };
}

export async function mockGreenConcreteKit(page: Page, options: KitMockOptions = {}): Promise<KitMock> {
  const mock: KitMock = { setups: [], switches: [], memoryWrites: [], failOnce: options.kit === 'error' };
  const viewer = options.role === 'viewer';
  const state = { knowledge: false, routine: 'missing' as 'missing' | 'off' | 'on' };
  let memories: Json[] = [{ id: 'mem_1', agent_id: 'agent_shahzeb', content: 'Rotate steer tyres at 6 mm.', source: 'manual', task_id: null, created_by: 'user_local',
    created_at: now, kind: 'decision', source_label: 'Tyre SOP, version 3', source_date: '2026-10-30', updated_at: null, updated_by: null, expires_at: null }];
  let n = 1;
  await page.route(/http:\/\/localhost:8000\/api\/v1\/(office\/staffing|office\/green-concrete\/kit|agents\/agent_shahzeb\/memories).*/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const fail = (status: number, message: string) => json({ error: { code: status === 403 ? 'forbidden' : 'unavailable', message, correlation_id: 'c', details: [] } }, status);
    const path = url.pathname;
    if (path === '/api/v1/office/staffing/offices') {
      return json({ version: 1, blueprint_version: 1, workspace_id: 'workspace_foundation', as_of: now, total: 1, limit: 12, offset: 0, buildings_total: 1, items: [officeSummary], can_apply: !viewer });
    }
    if (path === '/api/v1/office/staffing/offices/gc-green-concrete') return json(office(url.searchParams.get('lang') ?? 'en', viewer));
    if (path.startsWith('/api/v1/office/green-concrete/kit')) {
      if (options.kit === 'denied') return fail(403, 'Your role cannot read this.');
      if (options.kit === 'missing') return fail(503, 'Office persistence needs the PostgreSQL backend');
      if (mock.failOnce) { mock.failOnce = false; return fail(500, 'Database timeout'); }
      if (path === '/api/v1/office/green-concrete/kit' && request.method() === 'GET') {
        return json(kit(state, viewer, memories.length));
      }
      if (path === '/api/v1/office/green-concrete/kit/setup' && request.method() === 'POST') {
        if (viewer) return fail(403, 'Only a workspace owner can do this');
        const body = request.postDataJSON() as { items: { id: string }[] };
        mock.setups.push(body as unknown as Json);
        for (const item of body.items) { if (item.id === 'knowledge') state.knowledge = true; else state.routine = 'off'; }
        return json({ version: 1, created: body.items.length, results: body.items.map((i) => ({ id: i.id, outcome: 'created', target_id: 'x' })) });
      }
      const sw = /^\/api\/v1\/office\/green-concrete\/kit\/routines\/([a-z_]+)\/state$/.exec(path);
      if (sw && request.method() === 'POST') {
        const body = request.postDataJSON() as { enabled: boolean };
        mock.switches.push(body as unknown as Json);
        state.routine = body.enabled ? 'on' : 'off';
        return json({ key: sw[1], enabled: body.enabled, workflow_id: 'wf_1' });
      }
    }
    const memory = /^\/api\/v1\/agents\/agent_shahzeb\/memories(?:\/([^/]+))?$/.exec(path);
    if (memory) {
      if (request.method() === 'GET') {
        const kind = url.searchParams.get('kind');
        const items = memories.filter((m) => !kind || m.kind === kind);
        return json({ items, next_cursor: null, total: items.length });
      }
      if (viewer) return fail(403, 'Only the agent\'s creator or a workspace owner can change its memories.');
      if (request.method() === 'POST') {
        const body = request.postDataJSON() as Json;
        mock.memoryWrites.push({ method: 'POST', ...body });
        const created = { id: `mem_${++n}`, agent_id: 'agent_shahzeb', source: 'manual', task_id: null, created_by: 'user_local', created_at: now,
          updated_at: null, updated_by: null, expires_at: null, source_file_id: null, ...body };
        memories = [created, ...memories];
        return json(created, 201);
      }
      if (request.method() === 'PATCH') {
        const body = request.postDataJSON() as Json;
        mock.memoryWrites.push({ method: 'PATCH', id: memory[1], ...body });
        memories = memories.map((m) => (m.id === memory[1] ? { ...m, ...body, updated_at: now, updated_by: 'user_local' } : m));
        return json(memories.find((m) => m.id === memory[1]));
      }
      if (request.method() === 'DELETE') {
        mock.memoryWrites.push({ method: 'DELETE', id: memory[1] });
        memories = memories.filter((m) => m.id !== memory[1]);
        return route.fulfill({ status: 204, body: '' });
      }
    }
    return fail(404, `unmocked ${request.method()} ${path}`);
  });
  return mock;
}
