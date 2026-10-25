import type { Page, Route } from '@playwright/test';

// Mock of the Staff my campus API (docs/ANUM_MINE_STAFF_DESKS.md): preview, confirm per building, keep empty, paused agents
// and turn on. The campus is plainly fake test data in the real response shape (the default 220 desk campus). The mock keeps
// the rules the UI relies on: the 200 agent limit, the run cap, one hash per building, and a hash that changes with the choices.
const WS = 'workspace_foundation';

interface RoleSeed { id: string; en: string; ar: string; limited: boolean; gap?: { id: string; en: string; ar: string } }
const ROLES: Record<string, RoleSeed> = {
  dispatcher: { id: 'operations_dispatcher', en: 'Operations Dispatcher', ar: 'مسؤول توزيع العمليات', limited: true, gap: { id: 'task_reassign', en: 'A tool that moves an existing task to another person or agent', ar: 'أداة تنقل مهمة قائمة إلى شخص أو وكيل آخر' } },
  security: { id: 'security_reviewer', en: 'Security Reviewer', ar: 'مراجع الأمن', limited: true, gap: { id: 'audit_log_read', en: 'A tool that reads the audit log', ar: 'أداة لقراءة سجل التدقيق' } },
  exec: { id: 'executive_assistant', en: 'Executive Assistant', ar: 'مساعد تنفيذي', limited: true, gap: { id: 'calendar_read', en: 'A tool that reads or books calendar events', ar: 'أداة لقراءة أحداث التقويم أو الحجز فيه' } },
  sales: { id: 'sales_followup', en: 'Sales Follow-up', ar: 'متابعة المبيعات', limited: true },
  orders: { id: 'orders_desk', en: 'Orders Desk', ar: 'مكتب الطلبات', limited: true },
  support: { id: 'support_triage', en: 'Customer Support Triage', ar: 'فرز دعم العملاء', limited: true },
  marketing: { id: 'marketing_content', en: 'Marketing Content Assistant', ar: 'مساعد محتوى التسويق', limited: true },
  fleet: { id: 'fleet_maintenance', en: 'Fleet Maintenance Coordinator', ar: 'منسق صيانة الأسطول', limited: false },
  hr: { id: 'hr_people', en: 'HR and People Assistant', ar: 'مساعد الموارد البشرية', limited: true },
  finance: { id: 'finance_reconciliation', en: 'Finance Reconciliation Clerk', ar: 'موظف مطابقة مالية', limited: true },
  proc: { id: 'procurement', en: 'Procurement Assistant', ar: 'مساعد المشتريات', limited: true },
  comp: { id: 'compliance_reviewer', en: 'Compliance Reviewer', ar: 'مراجع الامتثال', limited: true },
  eng: { id: 'engineering_devops', en: 'Engineering and DevOps Assistant', ar: 'مساعد الهندسة والعمليات التقنية', limited: true },
  research: { id: 'research_analyst', en: 'Research Analyst', ar: 'محلل أبحاث', limited: true },
};

// [building id, name, floors: [level, rooms: [key, en, ar, desks, role]]]
const CAMPUS: [string, string, [number, [string, string, string, number, string][]][]][] = [
  ['campus-01-hq', 'ANUM HQ', [[-1, [['facilities', 'Facilities Office', 'مكتب المرافق', 3, 'dispatcher'], ['security-operations', 'Security Operations', 'عمليات الأمن', 5, 'security'], ['parking-logistics', 'Parking and Logistics', 'المواقف والخدمات اللوجستية', 2, 'dispatcher']]],
    [2, [['command-center', 'Command Center', 'مركز القيادة', 6, 'dispatcher'], ['ai-organization', 'AI Organization', 'المؤسسة الذكية', 8, 'eng'], ['operations-office', 'Operations Office', 'مكتب العمليات', 4, 'dispatcher'], ['assets', 'Assets', 'الأصول', 4, 'dispatcher'], ['suppliers', 'Suppliers', 'الموردون', 4, 'proc']]],
    [3, [['executive-office', 'Executive Office', 'المكتب التنفيذي', 3, 'exec']]]]],
  ['campus-02-commerce', 'ANUM 360 Commerce', [[0, [['ecommerce', 'E-commerce', 'التجارة الإلكترونية', 6, 'sales'], ['retail', 'Retail', 'التجزئة', 6, 'sales'], ['marketplace', 'Marketplace', 'السوق', 5, 'orders'], ['orders', 'Orders', 'الطلبات', 6, 'orders'], ['warehouse', 'Warehouse', 'المستودع', 4, 'dispatcher']]],
    [1, [['sales-office', 'Sales Office', 'مكتب المبيعات', 6, 'sales'], ['marketing-studio', 'Marketing Studio', 'استوديو التسويق', 5, 'marketing']]],
    [2, [['customer-service', 'Customer Service', 'خدمة العملاء', 6, 'support'], ['support', 'Support', 'الدعم', 6, 'support'], ['messages', 'Messages', 'الرسائل', 4, 'support'], ['tickets', 'Tickets', 'التذاكر', 4, 'support']]]]],
  ['campus-03-tyre-pulse', 'Tyre Pulse', [[0, [['workshop', 'Workshop', 'الورشة', 4, 'fleet'], ['maintenance', 'Maintenance', 'الصيانة', 4, 'fleet'], ['warehouse', 'Warehouse', 'المستودع', 4, 'dispatcher'], ['logistics', 'Logistics', 'الخدمات اللوجستية', 4, 'dispatcher']]],
    [1, [['fleet', 'Fleet', 'الأسطول', 6, 'fleet'], ['tyre-engineering', 'Tyre Engineering', 'هندسة الإطارات', 4, 'fleet'], ['inspections', 'Inspections', 'الفحص', 3, 'fleet']]]]],
  ['campus-04-corporate-services', 'Corporate Services', [[0, [['hr', 'HR', 'الموارد البشرية', 4, 'hr'], ['finance-office', 'Finance Office', 'المكتب المالي', 6, 'finance'], ['wealth-office', 'Wealth Office', 'مكتب الثروة', 3, 'finance'], ['procurement', 'Procurement', 'المشتريات', 4, 'proc']]],
    [1, [['legal-office', 'Legal Office', 'المكتب القانوني', 4, 'comp'], ['compliance-office', 'Compliance Office', 'مكتب الامتثال', 4, 'comp'], ['governance-office', 'Governance Office', 'مكتب الحوكمة', 3, 'comp']]]]],
  ['campus-05-knowledge-ai', 'Knowledge & AI', [[0, [['ai-labs', 'AI Labs', 'مختبرات الذكاء الاصطناعي', 8, 'eng'], ['data', 'Data', 'البيانات', 6, 'research'], ['automation', 'Automation', 'الأتمتة', 4, 'eng']]],
    [1, [['development', 'Development', 'التطوير', 8, 'eng'], ['deployments', 'Deployments', 'النشر', 4, 'eng'], ['incidents', 'Incidents', 'الحوادث', 4, 'eng']]],
    [2, [['research-room', 'Research Room', 'غرفة البحث', 6, 'research'], ['intelligence-desk', 'Intelligence Desk', 'مكتب الاستخبارات', 4, 'research']]]]],
  ['campus-06-new-ventures', 'New Ventures', [[0, [['investments', 'Investments', 'الاستثمارات', 4, 'finance'], ['research-development', 'R&D', 'البحث والتطوير', 6, 'research'], ['future-businesses', 'Future Businesses', 'الأعمال المستقبلية', 4, 'research']]]]],
  ['campus-07-personal', 'Personal Office', [[0, [['personal-office', 'Personal Office', 'المكتب الشخصي', 2, 'exec']]]]],
  ['campus-08-career', 'Career & Employment', [[0, [['career-office', 'Career Office', 'مكتب المسار المهني', 3, 'hr'], ['employment-desk', 'Employment Desk', 'مكتب التوظيف', 3, 'hr']]]]],
];

export interface StaffDesksMock {
  requests: { method: string; path: string; body: Record<string, any> | null }[];
  setCanConfirm(v: boolean): void;
  setPreviewFailure(status: number | null): void;
  setConfirmFailure(status: number | null, detail?: string): void;
  setExistingAgents(n: number): void;
  setBudget(v: number | null): void;
  /** Desks that fail with `seat_conflict` on confirm. */
  failRoom(roomId: string | null): void;
  state: { keepEmpty: Set<string>; staffed: Set<string>; paused: { agent_id: string; name: string; desk_id: string; template_id: string }[] };
}

export async function mockStaffDesks(page: Page): Promise<StaffDesksMock> {
  let canConfirm = true; let previewFail: number | null = null; let confirmFail: { status: number; detail: string } | null = null;
  let existing = 0; let budget: number | null = null; let failRoom: string | null = null;
  const mock: StaffDesksMock = {
    requests: [], state: { keepEmpty: new Set(), staffed: new Set(), paused: [] },
    setCanConfirm(v) { canConfirm = v; }, setPreviewFailure(s) { previewFail = s; }, setExistingAgents(n) { existing = n; }, setBudget(v) { budget = v; },
    setConfirmFailure(status, detail = 'failed') { confirmFail = status === null ? null : { status, detail }; }, failRoom(r) { failRoom = r; },
  };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const hash = (seed: string) => { let h = 7; for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h.toString(16).padStart(8, 'a').repeat(8).slice(0, 64); };
  const zero = () => ({ plan: 0, resume: 0, occupied: 0, keep_empty: 0, over_limit: 0, over_cap: 0, no_role: 0, excluded: 0, agent_elsewhere: 0, total: 0 });

  function plan(selected: string[] | null, exclude: string[], cap: number | null, lang: 'en' | 'ar') {
    let slots = Math.max(0, 200 - existing - mock.state.staffed.size); let left = cap;
    const totals = zero(); const batches: any[] = []; const roleCount: Record<string, number> = {};
    const buildings = CAMPUS.map(([bid, bname, floors]) => {
      const inRun = selected === null || selected.includes(bid); const bc = zero(); let newAgents = 0; const items: string[] = [];
      const rooms = floors.flatMap(([level, rs]) => rs.map(([key, en, ar, n, role]) => {
        const rid = `${bid}-${key}`; const rc = zero();
        const desks = Array.from({ length: n }, (_, i) => {
          const id = `${rid}-d${i + 1}`; let state = 'plan'; let reason: string | null = null;
          if (mock.state.staffed.has(id)) state = 'occupied';
          else if (mock.state.keepEmpty.has(id)) state = 'keep_empty';
          else if (!inRun || exclude.includes(rid)) state = 'excluded';
          else if (slots <= 0) { state = 'over_limit'; reason = 'agent_limit'; }
          else if (left !== null && left <= 0) { state = 'over_cap'; reason = 'run_cap'; }
          else { slots -= 1; if (left !== null) left -= 1; }
          rc[state as keyof typeof rc] += 1; rc.total += 1;
          const r = ROLES[role];
          if (state === 'plan') { roleCount[r.id] = (roleCount[r.id] ?? 0) + 1; newAgents += 1; items.push(id); }
          return { id, label: `Desk ${i + 1}`, number: i + 1, state, reason,
            agent: state === 'plan' ? { name: lang === 'ar' ? `${r.ar}، ${ar} ${i + 1}` : `${r.en}, ${en} ${i + 1}`, template_id: r.id, monthly_cost_limit_usd: 5, existing_agent_id: null } : null };
        });
        for (const k of Object.keys(rc) as (keyof typeof rc)[]) bc[k] += rc[k];
        return { id: rid, name: en, name_ar: ar, kind: 'office', team_department: null, floor_id: `${bid}-l${level}`, floor_level: level, floor_name: `Level ${level}`,
          template_id: ROLES[role].id, role_source: 'name', desks, counts: rc };
      }));
      for (const k of Object.keys(bc) as (keyof typeof bc)[]) totals[k] += bc[k];
      if (newAgents) batches.push({ building_id: bid, count: newAgents, new_agents: newAgents, resumed: 0, quota: newAgents, max_monthly_usd: newAgents * 5,
        payload_hash: hash(`${bid}:${items.join(',')}:${lang}`) });
      return { id: bid, name: bname, selected: inRun, rooms, counts: bc };
    });
    return { buildings, batches, totals, roleCount, freeSlots: Math.max(0, 200 - existing - mock.state.staffed.size) };
  }

  await page.route(/http:\/\/localhost:8000\/api\/v1\/office\/staff-desks\/.*/, async (route: Route) => {
    const request = route.request(); const url = new URL(request.url());
    const path = url.pathname.replace('/api/v1/office/staff-desks', '');
    const raw = request.postData(); const body = raw ? JSON.parse(raw) : null;
    mock.requests.push({ method: request.method(), path: path + url.search, body });
    if (path === '/preview') {
      if (previewFail !== null) return json(route, { detail: previewFail === 403 ? 'Forbidden' : 'The service failed.' }, previewFail);
      const lang = body.lang === 'ar' ? 'ar' : 'en';
      const p = plan(body.buildings ?? null, body.exclude_rooms ?? [], body.max_agents ?? null, lang);
      const roles = Object.values(ROLES).filter((r) => p.roleCount[r.id]).map((r) => ({ id: r.id, department: 'x', name: { en: r.en, ar: r.ar }, count: p.roleCount[r.id],
        monthly_cost_limit_usd: 5, limited: r.limited, tools: 4, skills: 2, missing: r.gap ? [{ kind: 'capability', ...r.gap }] : [],
        autonomy: { read: 'auto', write: 'ask', external: 'off' } }));
      const gaps = Object.values(ROLES).filter((r) => p.roleCount[r.id] && r.gap).map((r) => ({ template_id: r.id, kind: 'capability', ...r.gap!, agents: p.roleCount[r.id] }));
      const agents = Object.values(p.roleCount).reduce((a, b) => a + b, 0);
      const max = agents * 5;
      return json(route, { version: 1, catalog_version: 1, workspace_id: WS, as_of: 'now', lang,
        limits: { agents: existing + mock.state.staffed.size, max_agents: 200, free_slots: p.freeSlots, run_cap: body.max_agents ?? null }, totals: p.totals,
        cost: { per_template: roles.map((r) => ({ template_id: r.id, count: r.count, cap_usd: 5, subtotal_usd: r.count * 5 })), max_monthly_usd: max, agents,
          workspace_budget: { visible: true, monthly_cost_limit_usd: budget }, within_budget: budget === null ? null : max <= budget },
        roles, capability_gaps: gaps, buildings: p.buildings, batches: p.batches, can_confirm: canConfirm,
        rules: { created_paused: true, touches_occupied_desks: false, click_only: true, least_privilege: true } });
    }
    if (path === '/confirm') {
      if (confirmFail) return json(route, { detail: confirmFail.detail }, confirmFail.status);
      const p = plan([body.building_id], body.exclude_rooms ?? [], body.quota, body.lang);
      const b = p.buildings.find((x) => x.id === body.building_id)!; const results: any[] = [];
      for (const room of b.rooms) for (const d of room.desks) if (d.state === 'plan') {
        if (failRoom && room.id === failRoom) { results.push({ desk_id: d.id, room_id: room.id, status: 'failed', agent_id: 'agent_x', agent_name: d.agent!.name, reason: 'seat_conflict' }); continue; }
        mock.state.staffed.add(d.id);
        mock.state.paused.push({ agent_id: `agent_${mock.state.paused.length + 1}`, name: d.agent!.name, desk_id: d.id, template_id: d.agent!.template_id });
        results.push({ desk_id: d.id, room_id: room.id, status: 'created', agent_id: `agent_${mock.state.paused.length}`, agent_name: d.agent!.name, reason: null });
      }
      const failed = results.some((r) => r.status === 'failed'); const made = results.some((r) => r.status === 'created');
      return json(route, { version: 1, outcome: !results.length ? 'already_staffed' : failed ? (made ? 'partial' : 'failed') : 'created', building_id: body.building_id,
        payload_hash: body.payload_hash, paused: true, counts: { created: results.filter((r) => r.status === 'created').length, seated_existing: 0, skipped: 0, failed: results.filter((r) => r.status === 'failed').length }, results });
    }
    if (path === '/keep-empty') {
      for (const id of body.desk_ids) { if (body.keep_empty) mock.state.keepEmpty.add(id); else mock.state.keepEmpty.delete(id); }
      return json(route, { version: 1, keep_empty: body.keep_empty, changed: body.desk_ids, unchanged: [] });
    }
    if (path === '/paused') {
      const offset = Number(url.searchParams.get('offset') ?? 0); const limit = Number(url.searchParams.get('limit') ?? 50);
      const all = mock.state.paused;
      return json(route, { version: 1, total: all.length, offset, limit, can_confirm: canConfirm, all_ids: all.map((a) => a.agent_id),
        items: all.slice(offset, offset + limit).map((a) => ({ ...a, room_id: null, monthly_cost_limit_usd: 5 })) });
    }
    if (path === '/turn-on/preview') {
      const items = mock.state.paused.filter((a) => body.agent_ids.includes(a.agent_id)).map((a) => ({ ...a, room_id: null, monthly_cost_limit_usd: 5 }));
      const total = items.length * 5;
      return json(route, { version: 1, items, not_paused_or_unknown: [], max_monthly_usd: total, agents_without_cap: [], workspace_budget: { visible: true, monthly_cost_limit_usd: budget },
        within_budget: budget === null ? null : total <= budget, payload_hash: hash(`on:${body.agent_ids.join(',')}`), can_confirm: canConfirm });
    }
    if (path === '/turn-on') {
      mock.state.paused = mock.state.paused.filter((a) => !body.agent_ids.includes(a.agent_id));
      return json(route, { version: 1, outcome: 'on', results: body.agent_ids.map((id: string) => ({ agent_id: id, status: 'on', reason: null })), counts: { on: body.agent_ids.length, failed: 0 }, max_monthly_usd: body.agent_ids.length * 5 });
    }
    return json(route, { detail: 'Not found' }, 404);
  });
  return mock;
}
