import type { Page } from '@playwright/test';

// A stateful mock of what the AI team org chart reads (docs/org-chart.md): agents, assistants,
// people, open and failed tasks, approvals, connection setup, the agent catalog and the approval
// policy, plus the drawer's per-agent tasks and runs and the task, pause and resume writes.
type Json = Record<string, unknown>;
const now = new Date().toISOString();
const hourAgo = new Date(Date.now() - 3_600_000).toISOString();

export interface OrgMockOptions {
  role?: 'owner' | 'member' | 'viewer';
  empty?: boolean;
  listFails?: boolean;
  denied?: boolean;
  setupFails?: boolean;
  approvalsDenied?: boolean;
}

function agentRow(id: string, name: string, extra: Json = {}): Json {
  return {
    id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', owner_user_id: 'user_local', status: 'active', created_at: hourAgo, updated_at: now,
    version: 1, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name, icon: 'bot', color: '#4f46e5', category: 'work',
    department: null, description: `${name} helps the team.`, instructions: 'Keep answers short and cite the source.', model_route: 'capable', allowed_skills: [], allowed_tools: [],
    autonomy: { read: 'auto', write: 'ask', external: 'ask' }, knowledge_mode: 'workspace', knowledge_sources: [], sharing: 'workspace', template_id: null, ...extra,
  };
}

export const catalog = {
  tools: [
    ['anum.respond', 'read', 'low'], ['web_search', 'read', 'low'], ['search_email', 'read', 'low'], ['read_email', 'read', 'low'], ['draft_reply', 'write', 'medium'],
    ['send_email', 'external', 'high'], ['read_whatsapp', 'read', 'low'], ['send_whatsapp', 'external', 'high'], ['tyres_due', 'read', 'low'], ['tyre_fleet_summary', 'read', 'low'],
    ['purchase_plan', 'read', 'low'], ['commerce.list_orders', 'read', 'low'], ['commerce.sales_summary', 'read', 'low'], ['marketing.draft_social_post', 'write', 'medium'],
    ['marketing.schedule_post', 'external', 'high'], ['team.assign_task', 'write', 'medium'],
  ].map(([name, category, risk_level]) => ({ name, description: `${name} tool`, risk_level, category, always_on: name === 'anum.respond', available_to_you: true, reason: null })),
  skills: [
    { id: 'anum.web-search', name: 'Web search', description: 'Looks things up on the web.', source: 'builtin', required_tools: ['web_search'] },
    { id: 'anum.email', name: 'Email', description: 'Reads, drafts and sends email.', source: 'builtin', required_tools: ['search_email', 'read_email', 'draft_reply', 'send_email'] },
    { id: 'anum.tyre-planning', name: 'Tyre planning', description: 'Plans tyre rotations and purchases.', source: 'builtin', required_tools: ['tyres_due', 'purchase_plan'] },
    { id: 'anum.document-drafting', name: 'Document drafting', description: 'Drafts and summarizes text.', source: 'builtin', required_tools: ['anum.respond'] },
    { id: 'acme.brand-voice', name: 'Brand voice', description: 'Writes in the house style.', source: 'installed', required_tools: ['anum.respond'] },
  ],
  templates: [], icons: ['bot', 'truck', 'search', 'mail'], autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' },
};

export function seedAgents(): Json[] {
  return [
    agentRow('agent_leads', 'Lead Finder', { icon: 'search', color: '#2563eb', department: 'Sales & Outreach', description: 'Finds fleet operators in the UAE that match our customer profile.', allowed_tools: ['web_search'], allowed_skills: ['anum.web-search', 'anum.document-drafting'] }),
    agentRow('agent_outreach', 'Outreach Writer', { icon: 'mail', color: '#7c3aed', department: 'Sales & Outreach', description: 'Drafts first emails and follow-ups to new leads.', allowed_tools: ['search_email', 'read_email', 'draft_reply', 'send_email'], allowed_skills: ['anum.email', 'acme.brand-voice'] }),
    agentRow('agent_fleet', 'Fleet Planner', { icon: 'truck', color: '#0f766e', department: 'Operations and warehouse', description: 'Plans tyre rotations and the monthly purchase order.', allowed_tools: ['tyres_due', 'tyre_fleet_summary', 'purchase_plan'], allowed_skills: ['anum.tyre-planning'], autonomy: { read: 'auto', write: 'auto', external: 'off' } }),
    agentRow('agent_maint', 'Maintenance Scheduler', { icon: 'wrench', color: '#ea580c', department: 'Operations and warehouse', status: 'paused', description: 'Books workshop slots for vehicles that are due.', allowed_tools: ['tyre_fleet_summary', 'team.assign_task'] }),
    agentRow('agent_care', 'WhatsApp Concierge', { icon: 'phone', color: '#16a34a', department: 'Customer Care', description: 'Answers customer questions on WhatsApp and hands off to a person.', allowed_tools: ['read_whatsapp', 'send_whatsapp'], unavailable_skills: ['acme.old-faq'] }),
    agentRow('agent_invoice', 'Invoice Checker', { icon: 'chart', color: '#475569', department: 'Finance', description: 'Checks marketplace orders against invoices and flags gaps.', allowed_tools: ['commerce.list_orders', 'commerce.sales_summary'] }),
    ...['Social Posts', 'Content Writer', 'SEO Researcher', 'Newsletter', 'Video Scripts'].map((name, i) => agentRow(`agent_mkt_${i}`, name, {
      icon: 'sparkles', color: '#db2777', department: 'Marketing & Content', description: `${name} for LinkedIn and Instagram.`,
      allowed_tools: i === 2 ? ['web_search'] : ['marketing.draft_social_post', 'marketing.schedule_post'], allowed_skills: i === 2 ? ['anum.web-search'] : ['acme.brand-voice'],
    })),
    agentRow('agent_personal', 'Personal Planner', { category: 'personal', icon: 'calendar', color: '#4f46e5', description: 'Keeps my week in order.', sharing: 'private' }),
  ];
}

const task = (id: string, status: string, agent_id: string | null, title: string, created_at = now): Json => ({
  id, title, prompt: title, status, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at, updated_at: created_at, agent_id,
});

export async function mockOrgChart(page: Page, options: OrgMockOptions = {}) {
  let agents = options.empty ? [] : seedAgents();
  const tasks: Json[] = options.empty ? [] : [
    task('task_plan', 'running', 'agent_fleet', 'Plan October tyre rotation'),
    task('task_mail1', 'waiting_approval', 'agent_outreach', 'Email Al Noor Transport about the pilot'),
    task('task_mail2', 'waiting_approval', 'agent_outreach', 'Follow up with Desert Logistics'),
    task('task_wa', 'failed', 'agent_care', 'Reply to a customer about delivery times', hourAgo),
    { ...task('task_done', 'completed', 'agent_fleet', 'September purchase plan', new Date(Date.now() - 3 * 3_600_000).toISOString()), updated_at: hourAgo },
    task('task_anum', 'queued', null, 'Weekly summary for the owner'),
    task('task_anum_ap', 'waiting_approval', null, 'Share the board pack'),
  ];
  const approvals = options.empty ? [] : [
    { id: 'ap_1', task_id: 'task_mail1', action: 'send_email', risk_level: 'high', status: 'pending', reason: 'Sends an email', created_at: now },
    { id: 'ap_2', task_id: 'task_mail2', action: 'send_email', risk_level: 'high', status: 'pending', reason: 'Sends an email', created_at: now },
    { id: 'ap_3', task_id: 'task_anum_ap', action: 'external.action', risk_level: 'high', status: 'pending', reason: 'Shares a file', created_at: now },
  ];
  const calls: { method: string; path: string; body: unknown }[] = [];
  let n = 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path: `${path}${url.search}`, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const envelope = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true });
    if (path === '/api/v1/workspaces/current') return json({ id: 'workspace_foundation', tenant_id: 'tenant_local', name: 'Al Noor Fleet', created_at: now, updated_at: now });
    if (path === '/api/v1/agents/catalog') return json(catalog);
    if (path === '/api/v1/agents' && method === 'GET') {
      if (options.denied) return envelope(403, 'forbidden', 'You need the task:read permission in this workspace.');
      if (options.listFails) return envelope(503, 'unavailable', 'The database is not reachable.');
      const limit = Number(url.searchParams.get('limit') ?? 24);
      const cursor = url.searchParams.get('cursor');
      const rows = cursor ? agents.slice(agents.findIndex((a) => a.id === cursor) + 1) : agents;
      const pageRows = rows.slice(0, limit);
      return json({ items: pageRows, next_cursor: rows.length > limit ? pageRows[pageRows.length - 1].id : null, counts: { all: agents.length, work: agents.length, personal: 0, custom: 0 } });
    }
    const one = /^\/api\/v1\/agents\/([^/]+)$/.exec(path);
    if (one) {
      const found = agents.find((a) => a.id === one[1]);
      if (!found) return envelope(404, 'not_found', 'Agent not found.');
      if (method === 'PATCH') {
        const { version, ...changes } = body as Json;
        if (version !== found.version) return envelope(409, 'conflict', 'Someone changed this agent since you opened it.');
        const updated = { ...found, ...changes, version: Number(found.version) + 1 };
        agents = agents.map((a) => a.id === found.id ? updated : a);
        return json(updated);
      }
      return json(found);
    }
    const loadout = /^\/api\/v1\/agents\/([^/]+)\/skills$/.exec(path);
    if (loadout) {
      const found = agents.find((a) => a.id === loadout[1]);
      if (!found) return envelope(404, 'not_found', 'Agent not found.');
      const allowed = new Set([...(found.allowed_tools as string[]), 'anum.respond']);
      const items = (found.allowed_skills as string[]).map((id) => {
        const skill = catalog.skills.find((k) => k.id === id);
        const tools = (skill?.required_tools ?? []).map((name) => ({ name, registered: true, allowed: allowed.has(name), granted: null }));
        const missing = tools.filter((tool) => !tool.allowed).length;
        return { id, name: skill?.name ?? id, description: skill?.description ?? '', source: skill?.source ?? 'unknown', version: '1.0.0', latest_version: '1.0.0', risk_level: 'low',
          state: missing ? 'needs_tools' : 'ready', reason: missing ? 'Allow the missing tools on this agent so the skill can run.' : null, activation: skill?.source === 'installed' ? 'always' : 'on_request',
          triggers: ['plan', 'tyres'], loads_instructions: false, upgrade_available: false, tools };
      });
      return json({ agent_id: found.id, agent_version: found.version, can_edit: true, items, ready: items.filter((i) => i.state === 'ready').length, attention: items.filter((i) => i.state !== 'ready').length });
    }
    if (/^\/api\/v1\/agents\/[^/]+\/memories$/.test(path)) return json({ items: [], next_cursor: null, total: 0 });
    if (path === '/api/v1/tasks/summary') {
      const agentId = url.searchParams.get('agent_id');
      const by: Record<string, number> = {};
      for (const row of tasks.filter((t) => !agentId || t.agent_id === agentId)) by[String(row.status)] = (by[String(row.status)] ?? 0) + 1;
      // Fleet Planner's history: 4 finished in the last week, 11 in the last month, plus one older failure.
      if (agentId === 'agent_fleet') { const days = (Date.now() - Date.parse(url.searchParams.get('created_after') ?? now)) / 86_400_000; by.completed = (by.completed ?? 0) + (days > 8 ? 11 : 4); by.failed = (by.failed ?? 0) + 1; }
      return json({ total: Object.values(by).reduce((a, b) => a + b, 0), by_status: by, archived: 0, creators: ['user_local'] });
    }
    if (path === '/api/v1/work-items/workload') return json({ generated_at: now, unassigned_open: 1, rows: [
      { assignee_type: 'user', assignee_id: 'user_aisha', name: 'Aisha Rahman', open: 3, in_progress: 1, blocked: 0, overdue: 1, due_this_week: 2, estimate_hours: 30, capacity_hours: 40, load_percent: 75 },
    ] });
    if (path === '/api/v1/work-items') {
      const id = url.searchParams.get('assignee_id');
      const items = id === 'user_aisha' || id === 'asst_sales' ? [1, 2].map((n) => ({
        id: `work_${id}_${n}`, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', title: n === 1 ? 'Book tyre fitting for truck 7' : 'Quote for Desert Logistics', description: '', status: n === 1 ? 'in_progress' : 'todo',
        priority: 'high', assignee_type: url.searchParams.get('assignee_type'), assignee_id: id, assignee_name: null, created_by: 'user_local', created_by_name: 'Sara Haddad', due_at: now, follow_up_at: null,
        completed_at: null, reminder_sent_at: null, estimate_hours: 2, source: 'manual', source_detail: {}, agent_task_id: null, version: 1, created_at: hourAgo, updated_at: now, overdue: n === 1,
      })) : [];
      return json({ items, total: items.length, limit: 20, offset: 0, next_offset: null });
    }
    if (path === '/api/v1/assistants') return json(options.empty ? [] : [
      { id: 'asst_sales', department: 'sales', name: 'Sales desk', instructions: 'Answer price questions and draft quotes.', allowed_tools: ['anum.respond', 'team.assign_task'], routing_keywords: ['price', 'quote'], inbound_channels: ['whatsapp'], autonomy: 'suggest', enabled: true, created_by: 'user_local', created_at: now, updated_at: now },
      { id: 'asst_support', department: 'support', name: 'Support desk', instructions: 'Triage complaints.', allowed_tools: ['anum.respond', 'read_whatsapp'], routing_keywords: ['late', 'broken'], inbound_channels: ['email', 'whatsapp'], autonomy: 'observe', enabled: false, created_by: 'user_local', created_at: now, updated_at: now },
    ]);
    if (path === '/api/v1/team/people') return json(options.empty ? [] : [
      { user_id: 'user_local', role: 'owner', display_name: 'Sara Haddad', has_profile: true, email: null, department: null, job_title: 'Managing director', capacity_hours: 40, timezone: 'Asia/Dubai', assignable: true },
      { user_id: 'user_aisha', role: 'member', display_name: 'Aisha Rahman', has_profile: true, email: 'aisha@example.com', department: 'operations', job_title: 'Fleet dispatcher', capacity_hours: 40, timezone: 'Asia/Dubai', assignable: true },
      { user_id: 'user_omar', role: 'member', display_name: 'Omar Saleh', has_profile: true, email: null, department: 'finance', job_title: 'Accountant', capacity_hours: 30, timezone: 'Asia/Dubai', assignable: true },
    ]);
    if (path === '/api/v1/approvals') return options.approvalsDenied ? envelope(403, 'forbidden', 'Only approvers can see approvals.') : json(approvals);
    if (path === '/api/v1/approval-policy') return json({ two_person_rule: false, medium_risk_requires_approval: true });
    if (path === '/api/v1/integrations/setup') {
      if (options.setupFails) return envelope(503, 'unavailable', 'Setup status is not reachable.');
      const item = (id: string, group: string, status: string) => ({ id, group, status, connect_view: null, connections: status === 'connected' ? 1 : 0, failing: status === 'error' ? 1 : 0, missing: [], redirect_urls: [], doc: id, checkable: false });
      return json({ can_view_setup: true, can_check: true, items: [
        item('email_gmail', 'email', 'connected'), item('email_outlook', 'email', 'not_configured'), item('whatsapp', 'messaging', 'error'),
        item('fleet_data', 'operations', 'connected'), item('commerce', 'commerce', 'ready'), item('marketing_meta', 'marketing', 'not_configured'),
        item('marketing_linkedin', 'marketing', 'not_configured'), item('web_search', 'ai', 'connected'),
      ] });
    }
    if (path === '/api/v1/tasks' && method === 'GET') {
      const agentId = url.searchParams.get('agent_id');
      const statuses = url.searchParams.getAll('status');
      let rows = tasks.filter((t) => (!agentId || t.agent_id === agentId) && (!statuses.length || statuses.includes(String(t.status))));
      const cursor = url.searchParams.get('cursor');
      if (cursor) rows = rows.slice(rows.findIndex((t) => t.id === cursor) + 1);
      return json(rows.slice(0, Number(url.searchParams.get('limit') ?? 50)));
    }
    if (path === '/api/v1/tasks' && method === 'POST') {
      const created = task(`task_new_${++n}`, 'created', ((body as Json).agent_id as string) ?? null, String((body as Json).title));
      tasks.unshift(created);
      return json(created, 201);
    }
    const run = /^\/api\/v1\/tasks\/([^/]+)\/run$/.exec(path);
    if (run) {
      const found = tasks.find((t) => t.id === run[1])!;
      found.status = 'completed';
      return json({ task: found, run: { id: `run_${n}`, task_id: found.id, status: 'completed', result: 'Rotation booked for trucks 3 and 12 on Tuesday.', steps: [] }, approval: null });
    }
    const latest = /^\/api\/v1\/tasks\/([^/]+)\/latest-run$/.exec(path);
    if (latest) {
      if (latest[1] === 'task_done') return json({ id: 'run_done', task_id: 'task_done', status: 'completed', result: 'Buy 24 tyres (315/80R22.5) from Michelin by 5 October; estimated AED 38,400.', steps: [] });
      if (latest[1] === 'task_wa') return json({ id: 'run_wa', task_id: 'task_wa', status: 'failed', result: null, steps: [{ id: 's1', type: 'tool_result', summary: 'WhatsApp refused the message: the access token has expired.', created_at: hourAgo }] });
      return envelope(404, 'not_found', 'This task never ran.');
    }
    if (method === 'GET' && path.startsWith('/api/v1/')) return json([]);
    return route.abort();
  });
  return { calls, agents: () => agents };
}
