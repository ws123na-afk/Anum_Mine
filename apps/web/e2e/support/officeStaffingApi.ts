import type { Page, Route } from '@playwright/test';

// Mocked office staffing API (docs/ANUM_MINE_OFFICE_STAFFING.md). Two offices; the detail of the first has one
// missing role (create), one agent missing skills, tools and rules (update) and one data source not connected.
const H = (c: string) => c.repeat(64);
const now = '2026-11-02T08:00:00Z';
type Json = Record<string, unknown>;

const rules = (write: 'ask' | 'off', cap: number, extra: { id: string; en: string; ar: string }[] = []) => ({
  autonomy: { read: 'auto', write, external: 'off' }, max_risk: write === 'off' ? 'low' : 'medium',
  approvals: [{ id: 'read_auto', en: 'Reading runs on its own', ar: 'القراءة تعمل تلقائيا' }],
  monthly_cost_limit_usd: cap,
  never: [{ id: 'no_outward_without_approval', en: 'Send, publish or pay anything without a person\'s approval', ar: 'يرسل أو ينشر أو يدفع أي شيء دون موافقة شخص' }, ...extra],
});
const tool = (name: string, risk = 'low') => ({ name, state: 'present', reason: null, risk, category: 'read' });
const skill = (id: string) => ({ id, name: id, state: 'present', reason: null, source: 'builtin' });

export function corporateOffice(lang: 'en' | 'ar' = 'en', applied: Set<string> = new Set()): Json {
  const finAgent = { id: 'agent_fin', name: 'Ledger Desk', status: 'active', desk_id: 'd1', can_edit: true, skills: [], tools: ['finance.summary', 'send_email'],
    missing_tools: applied.has('update:agent_fin') ? [] : ['finance.transactions.search'], missing_skills: applied.has('update:agent_fin') ? [] : ['anum.finance'],
    missing_rules: applied.has('update:agent_fin') ? [] : ['autonomy', 'cost_cap', 'never_list'], over_risk_tools: ['send_email'], complete: applied.has('update:agent_fin') };
  const hrAgents = applied.has('create:hr_people') ? [{ id: 'agent_hr_new', name: lang === 'ar' ? 'مساعد الموارد البشرية، الخدمات المؤسسية' : 'HR and People Assistant, Corporate Services',
    status: 'paused', desk_id: 'd2', can_edit: true, skills: [], tools: [], missing_tools: [], missing_skills: [], missing_rules: [], over_risk_tools: [], complete: true }] : [];
  const role = (key: string, unit: string, en: string, ar: string, agents: unknown[], write: 'ask' | 'off', cap: number, tools: string[], skills: string[], extra: { id: string; en: string; ar: string }[] = []) => ({
    key, template_id: key, unit, name: { en, ar }, purpose: { en: `${en} purpose for this office.`, ar: `غرض ${ar} في هذا المكتب.` }, department: unit,
    rules: rules(write, cap, extra), tools: tools.map((n) => tool(n)), skills: skills.map(skill), attach_tools: tools, attach_skills: skills,
    missing: key === 'legal_reviewer' ? [{ kind: 'capability', id: 'contract_register', en: 'A contract register with parties, dates and renewals', ar: 'سجل عقود بالأطراف والتواريخ والتجديدات' }] : [],
    limited: key === 'legal_reviewer', sources: unit === 'finance' ? ['finance_ledger'] : ['email_center', 'files'], agents,
    state: (agents as { complete: boolean }[]).length === 0 ? 'missing' : (agents as { complete: boolean }[]).every((a) => a.complete) ? 'staffed' : 'needs_update',
  });
  const roles = [
    role('finance_clerk', 'finance', 'Finance Reconciliation Clerk', 'موظف مطابقة مالية', [finAgent], 'off', 4, ['finance.summary', 'finance.transactions.search'], ['anum.finance'],
      [{ id: 'no_live_trading', en: 'Place a live trade; live trading stays off', ar: 'ينفذ صفقة حقيقية؛ التداول الحقيقي يبقى متوقفا' }]),
    role('hr_people', 'hr', 'HR and People Assistant', 'مساعد الموارد البشرية', hrAgents, 'ask', 5, ['search_email', 'read_email'], ['anum.email']),
    role('legal_reviewer', 'legal_compliance', 'Legal Reviewer', 'المراجع القانوني', [{ ...finAgent, id: 'agent_legal', name: 'Contracts Desk', tools: [], over_risk_tools: [], missing_tools: [], missing_skills: [], missing_rules: [], complete: true }],
      'ask', 5, ['search_email'], ['anum.email']),
  ];
  const actions = [
    !applied.has('create:hr_people') && { id: 'create:hr_people', kind: 'create', role: 'hr_people', hash: H('a'), content: {
      kind: 'create', role: 'hr_people', lang, status: 'paused', monthly_cost_limit_usd: 5,
      agent: { name: lang === 'ar' ? 'مساعد الموارد البشرية، الخدمات المؤسسية' : 'HR and People Assistant, Corporate Services', department: 'hr', allowed_tools: ['search_email', 'read_email'], allowed_skills: ['anum.email'], autonomy: { read: 'auto', write: 'ask', external: 'off' }, template_id: 'bp:abc:hr_people' },
      placement: { desk_id: 'd2', desk_label: 'Desk 2', room_id: 'r_hr', room_name: 'HR' } } },
    !applied.has('update:agent_fin') && { id: 'update:agent_fin', kind: 'update', role: 'finance_clerk', agent_id: 'agent_fin', hash: H('b'), content: {
      kind: 'update', role: 'finance_clerk', agent_id: 'agent_fin', version: 3, add_tools: ['finance.transactions.search'], add_skills: ['anum.finance'],
      autonomy: { read: 'auto', write: 'off', external: 'off' }, add_rules_text: true, monthly_cost_limit_usd: 4 } },
  ].filter(Boolean);
  return {
    version: 1, blueprint_version: 1, as_of: now, lang, building_id: 'campus-04-corporate-services', name: { en: 'Corporate Services', ar: 'الخدمات المؤسسية' },
    kind: 'corporate_services', kind_name: { en: 'Corporate services', ar: 'الخدمات المؤسسية' },
    units: [{ id: 'finance', name: { en: 'Finance', ar: 'المالية' } }, { id: 'hr', name: { en: 'Human resources', ar: 'الموارد البشرية' } }, { id: 'legal_compliance', name: { en: 'Legal and compliance', ar: 'القانونية والامتثال' } }],
    roles_total: 3, roles_staffed: roles.filter((r) => r.agents.length).length, missing_roles: roles.filter((r) => !r.agents.length).map((r) => r.key),
    agents_total: 2 + hrAgents.length, agents_active: 2, agents_needing_update: applied.has('update:agent_fin') ? 0 : 1, sources_not_connected: 1,
    roles, other_agents: [{ id: 'agent_other', name: 'Old Helper', status: 'paused', desk_id: null, skills: [], tools: [] }],
    sources: [
      { id: 'email_center', state: 'connected', kind: 'connector', name: { en: 'Email mailboxes', ar: 'صناديق البريد' }, view: 'email', roles: ['hr_people'] },
      { id: 'finance_ledger', state: 'not_connected', kind: 'connector', name: { en: 'Finance ledger', ar: 'السجل المالي' }, view: 'finance', roles: ['finance_clerk'] },
      { id: 'web_search', state: 'not_connected', kind: 'setting', name: { en: 'Web search service', ar: 'خدمة البحث في الويب' }, view: null, roles: [] },
      { id: 'files', state: 'available', kind: 'module', name: { en: 'Documents library', ar: 'مكتبة المستندات' }, view: 'files', roles: ['legal_reviewer'] },
    ],
    actions, can_apply: true, limits: { agents: 3, max_agents: 200 },
  };
}

const summary = (o: Json) => {
  const { roles: _r, other_agents: _o, actions: _a, can_apply: _c, limits: _l, version: _v, blueprint_version: _b, as_of: _t, lang: _g, ...rest } = o;
  return { ...rest, sources: (o.sources as { id: string; state: string }[]).map((s) => ({ id: s.id, state: s.state })) };
};

export function tyreOffice(): Json {
  return { building_id: 'campus-03-tyre-pulse', name: { en: 'Tyre Pulse', ar: 'تاير بلس' }, kind: 'tyre_pulse', kind_name: { en: 'Tyre Pulse', ar: 'تاير بلس' },
    units: [{ id: 'tyre_pulse', name: { en: 'Tyre Pulse fleet', ar: 'أسطول تاير بلس' } }], roles_total: 2, roles_staffed: 0, missing_roles: ['fleet_maintenance', 'fleet_logistics'],
    agents_total: 0, agents_active: 0, agents_needing_update: 0, sources_not_connected: 1, sources: [{ id: 'tyre_pulse', state: 'not_connected' }] };
}

export interface StaffingMockOptions { mode?: 'ok' | 'empty' | 'denied' | 'missing' | 'error'; role?: 'owner' | 'viewer'; offices?: number }
export interface StaffingMock { applies: Json[]; reads: string[]; failOnce: boolean }

export async function mockStaffing(page: Page, options: StaffingMockOptions = {}): Promise<StaffingMock> {
  const mock: StaffingMock = { applies: [], reads: [], failOnce: options.mode === 'error' };
  const applied = new Set<string>();
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/staffing\/.*/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const fail = (status: number, message: string) => json({ error: { code: status === 403 ? 'forbidden' : 'unavailable', message, correlation_id: 'c', details: [] } }, status);
    mock.reads.push(`${request.method()} ${url.pathname}${url.search}`);
    if (options.mode === 'denied') return fail(403, 'Forbidden');
    if (options.mode === 'missing') return fail(503, 'Office persistence needs the PostgreSQL backend');
    if (mock.failOnce) { mock.failOnce = false; return fail(500, 'Database timeout'); }
    const viewer = options.role === 'viewer';
    if (url.pathname === '/api/v1/office/staffing/offices') {
      const extra = Array.from({ length: Math.max(0, (options.offices ?? 2) - 2) }, (_, i) => ({ ...tyreOffice(), building_id: `extra-${i}`, name: { en: `Branch office ${i + 1}`, ar: null } }));
      let all = options.mode === 'empty' ? [] : [summary(corporateOffice('en', applied)), tyreOffice(), ...extra];
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const total = all.length;
      if (q) all = all.filter((o) => JSON.stringify((o as Json).name).toLowerCase().includes(q));
      const limit = Number(url.searchParams.get('limit') ?? '12'), offset = Number(url.searchParams.get('offset') ?? '0');
      return json({ version: 1, blueprint_version: 1, workspace_id: 'workspace_foundation', as_of: now, total: all.length, limit, offset, buildings_total: total, items: all.slice(offset, offset + limit), can_apply: !viewer });
    }
    const detail = /^\/api\/v1\/office\/staffing\/offices\/([^/]+)$/.exec(url.pathname);
    if (detail && request.method() === 'GET') {
      const lang = (url.searchParams.get('lang') as 'en' | 'ar') ?? 'en';
      if (detail[1] === 'campus-04-corporate-services' || detail[1] === 'Alpha-building') {
        const office = corporateOffice(lang, applied);
        return json({ ...office, building_id: detail[1], ...(viewer ? { can_apply: false } : {}) });
      }
      return json({ ...tyreOffice(), version: 1, blueprint_version: 1, as_of: now, lang, roles: [], other_agents: [], actions: [], can_apply: !viewer, limits: { agents: 3, max_agents: 200 },
        sources: [{ id: 'tyre_pulse', state: 'not_connected', kind: 'connector', name: { en: 'Tyre Pulse fleet sync', ar: 'مزامنة أسطول تاير بلس' }, view: 'tyre', roles: [] }] });
    }
    const apply = /^\/api\/v1\/office\/staffing\/offices\/([^/]+)\/apply$/.exec(url.pathname);
    if (apply && request.method() === 'POST') {
      if (viewer) return fail(403, 'Only a workspace owner can do this');
      const body = request.postDataJSON() as { actions: { id: string; hash: string }[] };
      mock.applies.push(body as unknown as Json);
      const results = body.actions.map((a) => { applied.add(a.id); return { id: a.id, outcome: a.id.startsWith('create:') ? 'created' : 'updated', agent_id: 'agent_x' }; });
      return json({ version: 1, building_id: apply[1], created: 1, updated: 1, results });
    }
    return fail(404, `unmocked ${request.method()} ${url.pathname}`);
  });
  return mock;
}
