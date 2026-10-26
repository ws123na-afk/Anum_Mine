import type { Page, Route } from '@playwright/test';

// Mock of the governed provisioning API (docs/ANUM_MINE_AGENT_PROVISIONING.md): gaps, proposals,
// confirm and the agent PATCH used by "Turn it on". Names are plainly fake test data.
const WS = 'workspace_foundation';
const HASH = 'ab'.repeat(32);
const asOf = () => new Date().toISOString();

const tool = (name: string, state = 'present', reason: string | null = null) => ({ name, state, reason, risk: 'low', category: 'read', detail: null });
export const ordersTemplate = {
  id: 'orders_desk', department: 'operations', room_department: 'operations',
  name: { en: 'Orders Desk', ar: 'مكتب الطلبات' }, description: { en: 'Order intake, status and shipping follow-up.', ar: 'استلام الطلبات ومتابعتها.' },
  work_kinds: ['orders'], icon: 'shopping-cart', color: '#b45309', model_route: 'fast',
  autonomy: { read: 'auto', write: 'ask', external: 'off' }, monthly_cost_limit_usd: 5, knowledge_mode: 'none',
  tools: [tool('commerce.list_orders'), tool('commerce.get_order'), tool('commerce.sales_summary'), tool('draft_reply', 'missing', 'not_registered')],
  skills: [{ id: 'anum.commerce', name: 'Commerce', state: 'present', reason: null, source: 'builtin' }],
  limited: true,
  missing: [
    { kind: 'tool', id: 'draft_reply', reason: 'not_registered', en: 'The tool draft_reply is not in this deployment', ar: 'الأداة draft_reply غير متوفرة في هذا النشر' },
    { kind: 'capability', id: 'carrier_tracking', reason: 'no_tool_exists', en: 'A carrier tracking lookup for shipping follow-up', ar: 'أداة لتتبع الشحنات لدى شركة النقل' },
  ],
};

const candidates = [
  { room_id: 'Alpha-room-0', room_name: 'Alpha Room 1', floor_id: 'Alpha-floor-0', building_id: 'Alpha-building', team_department: 'operations', linked: true, fits: true, mode: 'existing_desk', vacant: 1, desks: 2, capacity: 4 },
  { room_id: 'Alpha-room-3', room_name: 'Alpha Room 4', floor_id: 'Alpha-floor-0', building_id: 'Alpha-building', team_department: null, linked: false, fits: false, mode: 'none', vacant: 0, desks: 4, capacity: 4 },
];

export function proposalFor(id: string, lang: string, canConfirm: boolean, mode = 'existing_desk') {
  const ar = lang === 'ar';
  const template = id === 'orders_desk' ? ordersTemplate : { ...ordersTemplate, id, name: { en: 'Sales Follow-up', ar: 'متابعة المبيعات' }, work_kinds: ['leads'], missing: [], limited: false };
  const name = ar ? template.name.ar : template.name.en;
  return {
    version: 1, payload_hash: HASH,
    content: {
      template_id: id, catalog_version: 1, lang, initial_status: 'paused', work_kinds: template.work_kinds,
      agent: { name, icon: 'bot', color: '#b45309', category: 'work', department: 'operations', description: ar ? template.description.ar : template.description.en,
        instructions: 'x', model_route: 'fast', allowed_skills: ['anum.commerce'], allowed_tools: ['commerce.list_orders'], autonomy: template.autonomy,
        knowledge_mode: 'none', sharing: 'workspace', template_id: `hq:${id}` },
      missing: template.missing.map((m) => ({ kind: m.kind, id: m.id, reason: m.reason })), limited: template.limited,
      budget: { monthly_cost_limit_usd: 5 }, placement: { mode, room_id: mode === 'none' ? null : 'Alpha-room-0', desk_id: 'Alpha-desk-9', desk_label: 'Orders Desk', reason: 'linked_room' },
    },
    template, names: template.name,
    placement: { mode, room_id: mode === 'none' ? null : 'Alpha-room-0', desk_id: 'Alpha-desk-9', desk_label: 'Orders Desk', reason: mode === 'none' ? 'no_linked_room' : 'linked_room',
      room_name: mode === 'none' ? null : 'Alpha Room 1', floor_id: 'Alpha-floor-0', building_id: 'Alpha-building', candidates },
    gap: { key: `gap:${id}`, template_id: id, severity: 'critical', flow_based: true, reasons: [{ code: 'orders_without_office', count: 3, stuck: 1, critical: 1 }] },
    coverage: { template_id: id, work_kinds: template.work_kinds, state: 'gap', handler: null },
    can_confirm: canConfirm,
    creates: { agent: 1, desk: mode === 'new_desk' ? 1 : 0, seat: mode === 'none' ? 0 : 1, budget_cap: 1, ledger_row: 1 },
  };
}

export interface ProvisioningMock {
  requests: { method: string; path: string; body: Record<string, unknown> | null }[];
  setCanConfirm(v: boolean): void;
  setGapsFailure(status: number | null): void;
  setConfirmFailure(status: number | null, detail?: string): void;
  setPlacement(mode: string): void;
  setGapsEmpty(v: boolean): void;
}

export async function mockProvisioning(page: Page): Promise<ProvisioningMock> {
  let canConfirm = true; let gapsFail: number | null = null; let confirmFail: { status: number; detail: string } | null = null;
  let created = false; let mode = 'existing_desk'; let empty = false;
  const mock: ProvisioningMock = {
    requests: [],
    setCanConfirm(v) { canConfirm = v; }, setGapsFailure(s) { gapsFail = s; }, setPlacement(m) { mode = m; },
    setConfirmFailure(status, detail = 'failed') { confirmFail = status === null ? null : { status, detail }; },
    setGapsEmpty(v) { empty = v; },
  };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/provisioning\/.*/, async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/office/provisioning', '');
    const raw = request.postData();
    mock.requests.push({ method: request.method(), path: path + url.search, body: raw ? JSON.parse(raw) : null });
    if (path === '/gaps') {
      if (gapsFail !== null) return json(route, { detail: gapsFail === 403 ? 'Forbidden' : 'The service failed.' }, gapsFail);
      const coverage = [
        { template_id: 'orders_desk', work_kinds: ['orders'], state: created ? 'covered' : 'gap', handler: created ? { agent_id: 'agent_new', agent_name: 'Orders Desk', agent_status: 'paused', desk_id: 'Alpha-desk-9' } : null },
        { template_id: 'sales_followup', work_kinds: ['leads'], state: 'no_signal', handler: null },
        { template_id: 'support_triage', work_kinds: [], state: 'source_not_connected', handler: null },
        { template_id: 'finance_reconciliation', work_kinds: [], state: 'no_signal', handler: null },
      ];
      const all = [
        { key: 'gap:orders_desk', template_id: 'orders_desk', severity: 'critical', flow_based: true, reasons: [{ code: 'orders_without_office', count: 3, stuck: 1, critical: 1 }] },
        { key: 'gap:finance_reconciliation', template_id: 'finance_reconciliation', severity: 'warning', flow_based: false, reasons: [{ code: 'empty_department', department: 'finance', rooms: [{ id: 'Alpha-room-2', name: 'Alpha Room 3' }] }] },
      ];
      const gaps = empty ? [] : created ? all.slice(1) : all;
      return json(route, { version: 1, workspace_id: WS, as_of: asOf(), sources: { tasks: 'connected', orders: 'connected' }, gaps, coverage,
        limits: { agents: 4, max_agents: 200 }, can_confirm: canConfirm, templates: [] });
    }
    const proposal = path.match(/^\/proposals\/([a-z_]+)(\/confirm)?$/);
    if (proposal && !proposal[2]) return json(route, proposalFor(proposal[1], url.searchParams.get('lang') ?? 'en', canConfirm, mode));
    if (proposal && proposal[2]) {
      if (confirmFail) return json(route, { detail: confirmFail.detail }, confirmFail.status);
      created = true;
      const lang = (JSON.parse(raw ?? '{}') as { lang?: string }).lang ?? 'en';
      return json(route, { version: 1, outcome: 'created', template_id: proposal[1], desk_id: 'Alpha-desk-9', limited: true, paused: true,
        agent: { id: 'agent_new', name: lang === 'ar' ? 'مكتب الطلبات' : 'Orders Desk', status: 'paused', version: 1, department: 'operations', allowed_tools: [], allowed_skills: [] },
        placement: proposalFor(proposal[1], lang, true).placement, missing: ordersTemplate.missing, budget: { monthly_cost_limit_usd: 5 }, work_kinds: ['orders'] }, 200);
    }
    return route.fulfill({ status: 404, body: '{}' });
  });
  await page.route(/http:\/\/localhost:8000\/api\/v1\/agents\/agent_new$/, async (route: Route) => {
    mock.requests.push({ method: route.request().method(), path: '/agents/agent_new', body: JSON.parse(route.request().postData() ?? 'null') });
    return json(route, { id: 'agent_new', status: 'active', version: 2 });
  });
  return mock;
}
