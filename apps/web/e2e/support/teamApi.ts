import type { Route } from '@playwright/test';

// A stateful mock of the team work, assistants, notifications and organisation routes
// (docs/team-work.md, docs/customer-organisations.md). `handleTeamRoute` answers the request
// and returns true, or returns false for anything it does not know.
const now = () => new Date().toISOString();
const later = new Date(Date.now() + 2 * 86_400_000).toISOString();
const earlier = new Date(Date.now() - 3 * 3_600_000).toISOString();

export interface TeamMockOptions { denyEdit?: boolean; emptyWork?: boolean; denyOrganisations?: boolean }

type Item = Record<string, unknown> & { id: string; title: string; status: string; version: number; comments: Record<string, unknown>[]; checklist: Record<string, unknown>[]; attachments: Record<string, unknown>[] };

export function createTeamMock(options: TeamMockOptions = {}) {
  const people = [
    { user_id: 'user_local', role: 'owner', display_name: 'Omar Owner', has_profile: true, email: null, department: 'operations', job_title: 'Owner', capacity_hours: 40, timezone: 'Asia/Riyadh', assignable: true },
    { user_id: 'user_ahmed', role: 'member', display_name: 'Ahmed Saleh', has_profile: true, email: 'ahmed@example.com', department: 'sales', job_title: 'Sales lead', capacity_hours: 40, timezone: 'Asia/Riyadh', assignable: true },
    { user_id: 'user_vera', role: 'viewer', display_name: 'user_vera', has_profile: false, email: null, department: null, job_title: null, capacity_hours: null, timezone: 'UTC', assignable: false },
  ];
  const assistants: Record<string, unknown>[] = [{ id: 'asst_sales', department: 'sales', name: 'Sales assistant', instructions: 'Draft quotes.', allowed_tools: ['anum.respond', 'team.assign_task'], routing_keywords: ['price', 'quote'], inbound_channels: ['whatsapp'], autonomy: 'suggest', enabled: true, created_by: 'user_local', created_at: now(), updated_at: now() }];
  const templates = ['sales', 'support', 'operations', 'finance', 'tyre_engineering'].map((department) => ({ department, name: `${department} assistant`, instructions: 'Help.', allowed_tools: ['anum.respond'], routing_keywords: [department], autonomy: department === 'tyre_engineering' ? 'observe' : 'suggest' }));
  const name = (type: string | null, id: string | null) => !type || !id ? null : type === 'anum' ? 'ANUM' : type === 'assistant' ? String(assistants.find((a) => a.id === id)?.name ?? id) : people.find((p) => p.user_id === id)?.display_name ?? id;
  let seq = 0;
  const item = (patch: Partial<Item> & { title: string }): Item => {
    seq += 1;
    return {
      id: `work_${seq}`, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', description: '', status: 'todo', priority: 'normal',
      assignee_type: 'user', assignee_id: 'user_local', created_by: 'user_ahmed', due_at: later, follow_up_at: null, completed_at: null, reminder_sent_at: null,
      estimate_hours: 4, source: 'manual', source_detail: {}, agent_task_id: null, version: 1, created_at: now(), updated_at: now(), comments: [], checklist: [], attachments: [], ...patch,
    } as Item;
  };
  const items: Item[] = options.emptyWork ? [] : [
    item({ title: 'Count winter tyre stock', priority: 'high', due_at: earlier, checklist: [{ id: 'chk_1', label: 'Warehouse A', done: true, position: 0, done_by: 'user_local', done_at: now(), created_at: now() }, { id: 'chk_2', label: 'Warehouse B', done: false, position: 1, done_by: null, done_at: null, created_at: now() }] }),
    item({ title: 'Call the supplier about the Michelin order', status: 'in_progress' }),
    item({ title: 'Draft a quote for Al Noor fleet', assignee_type: 'assistant', assignee_id: 'asst_sales', status: 'todo' }),
  ];
  const activity: Record<string, unknown>[] = items.map((i, n) => ({ id: `act_${n}`, work_item_id: i.id, actor_type: 'user', actor_id: 'user_ahmed', actor_name: 'Ahmed Saleh', action: 'work_item.create', summary: `Ahmed Saleh created "${i.title}"`, created_at: now() }));
  const notifications = [
    { id: 'ntf_1', user_id: 'user_local', kind: 'work_assigned', title: 'New work: Count winter tyre stock', body: 'Ahmed Saleh assigned you "Count winter tyre stock" (high priority).', work_item_id: 'work_1', read_at: null, email_status: 'not_requested', email_attempts: 0, email_error: null, push_status: 'not_configured', created_at: now() },
  ];
  const orgs: Record<string, unknown>[] = [{ tenant_id: 'gulf_tyres', display_name: 'Gulf Tyres', plan: 'standard', first_workspace_id: 'gulf_tyres_main', created_at: now(), workspaces: 1, active_members: 4, pending_invitations: 0, open_agent_tasks: 2, open_work_items: 7, overdue_work_items: 1, last_activity_at: now(), health: 'attention' }];
  const view = (i: Item) => {
    const { comments, checklist, attachments, ...rest } = i;
    return {
      ...rest, assignee_name: name(i.assignee_type as string, i.assignee_id as string), created_by_name: name('user', i.created_by as string),
      overdue: ['todo', 'in_progress', 'blocked'].includes(i.status) && Boolean(i.due_at) && new Date(String(i.due_at)) < new Date(),
      checklist_total: checklist.length, checklist_done: checklist.filter((c) => c.done).length, comment_count: comments.length, attachment_count: attachments.length,
      can_edit: !options.denyEdit, can_delete: !options.denyEdit,
    };
  };
  const detail = (i: Item) => ({ ...view(i), checklist: i.checklist, comments: i.comments, attachments: i.attachments });

  return async function handleTeamRoute(route: Route): Promise<boolean> {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = async (body: unknown, status = 200) => { await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }); return true; };
    const body = () => (request.postData() ? request.postDataJSON() : {}) as Record<string, unknown>;
    if (path === '/api/v1/notifications' && method === 'GET') return json({ items: notifications, unread: notifications.filter((n) => !n.read_at).length, next_cursor: null });
    if (/^\/api\/v1\/notifications\/[^/]+\/read$/.test(path)) { const n = notifications.find((x) => path.includes(x.id)); if (n) n.read_at = now(); return json(n ?? {}); }
    if (path === '/api/v1/notifications/read-all') { notifications.forEach((n) => { n.read_at = now(); }); return json({ updated: 1 }); }
    if (path === '/api/v1/organization/branding' && method === 'GET') return json({ tenant_id: 'tenant_local', display_name: 'Tyre House', has_logo: false, logo_content_type: null, updated_at: null });
    if (path === '/api/v1/organization/branding' && method === 'PUT') return json({ tenant_id: 'tenant_local', display_name: body().display_name, has_logo: false, logo_content_type: null, updated_at: now() });
    if (path === '/api/v1/me/organizations') return json([{ tenant_id: 'tenant_local', name: 'ANUM', display_name: 'Tyre House', current: true, workspaces: [{ workspace_id: 'workspace_foundation', workspace_name: 'Foundation', role: 'owner' }] }, { tenant_id: 'gulf_tyres', name: 'Gulf Tyres', display_name: 'Gulf Tyres', current: false, workspaces: [{ workspace_id: 'gulf_tyres_main', workspace_name: 'Main', role: 'member' }] }]);
    if (path === '/api/v1/team/people') return json(people);
    if (path === '/api/v1/assistants' && method === 'GET') return json(assistants);
    if (path === '/api/v1/assistants/templates') return json(templates);
    if (path === '/api/v1/assistants' && method === 'POST') { const a = { ...body(), id: `asst_${assistants.length + 1}`, created_by: 'user_local', created_at: now(), updated_at: now() }; assistants.push(a); return json(a, 201); }
    if (path === '/api/v1/assistants/route') {
      const b = body(); const text = `${b.subject ?? ''} ${b.body}`.toLowerCase(); const hit = /price|quote/.test(text) ? assistants[0] : null;
      const created = item({ title: `${String(b.channel)} from ${String(b.sender)}: ${String(b.subject || b.body)}`, assignee_type: hit ? 'assistant' : null, assignee_id: hit ? String(hit.id) : null, source: 'inbound' });
      items.push(created); return json({ assistant: hit, matched_keywords: hit ? ['price'] : [], work_item: view(created) }, 201);
    }
    if (path === '/api/v1/work-items/workload') return json({ rows: [{ assignee_type: 'user', assignee_id: 'user_local', name: 'Omar Owner', open: 2, in_progress: 1, blocked: 0, overdue: 1, due_this_week: 1, estimate_hours: 34, capacity_hours: 40, load_percent: 85 }, { assignee_type: 'assistant', assignee_id: 'asst_sales', name: 'Sales assistant', open: 1, in_progress: 0, blocked: 0, overdue: 0, due_this_week: 1, estimate_hours: 4, capacity_hours: null, load_percent: null }], unassigned_open: 0, generated_at: now() });
    if (path === '/api/v1/team/activity') return json({ items: [...activity].reverse(), next_cursor: null });
    if (path === '/api/v1/team/commands/parse') {
      const text = String(body().text);
      if (!/ahmed/i.test(text)) return json({ understood: false, problem: 'Nobody in this workspace is called Khalid. Add their name to their team profile under Team, People, or pick the person below.', person: 'Khalid', title: 'Count tyres', due_at: null, due_text: null, candidates: [], assignee: null });
      return json({ understood: true, problem: null, person: 'Ahmed', title: 'Call the supplier', due_at: later, due_text: 'Thursday', candidates: [{ type: 'user', id: 'user_ahmed', name: 'Ahmed Saleh' }], assignee: { type: 'user', id: 'user_ahmed', name: 'Ahmed Saleh' } });
    }
    if (path === '/api/v1/team/commands') { const created = item({ title: 'Call the supplier', assignee_id: 'user_ahmed', created_by: 'user_local', source: 'command' }); items.push(created); activity.push({ id: `act_${seq}`, work_item_id: created.id, actor_type: 'anum', actor_id: 'anum', actor_name: 'ANUM', action: 'work_item.create', summary: 'ANUM (for Omar Owner) created "Call the supplier" for Ahmed Saleh', created_at: now() }); return json(detail(created), 201); }
    if (path === '/api/v1/work-items' && method === 'GET') {
      const scope = url.searchParams.get('scope'); const status = url.searchParams.get('status') ?? 'open'; const q = url.searchParams.get('q')?.toLowerCase();
      const limit = Number(url.searchParams.get('limit') ?? 50); const offset = Number(url.searchParams.get('offset') ?? 0);
      const assigneeId = url.searchParams.get('assignee_id');
      const found = items.filter((i) => (status === 'open' ? ['todo', 'in_progress', 'blocked'].includes(i.status) : status === 'all' ? true : i.status === status))
        .filter((i) => scope !== 'mine' || (i.assignee_type === 'user' && i.assignee_id === 'user_local'))
        .filter((i) => !assigneeId || i.assignee_id === assigneeId)
        .filter((i) => !q || i.title.toLowerCase().includes(q));
      const page = found.slice(offset, offset + limit);
      return json({ items: page.map(view), total: found.length, limit, offset, next_offset: offset + page.length < found.length ? offset + page.length : null });
    }
    if (path === '/api/v1/work-items' && method === 'POST') {
      const b = body(); const assignee = b.assignee as { type: string; id: string } | null;
      const created = item({ title: String(b.title), description: String(b.description ?? ''), priority: String(b.priority ?? 'normal'), assignee_type: assignee?.type ?? null, assignee_id: assignee?.id ?? null, due_at: (b.due_at as string) ?? null, created_by: 'user_local', checklist: ((b.checklist as string[]) ?? []).map((label, n) => ({ id: `chk_new_${n}`, label, done: false, position: n, done_by: null, done_at: null, created_at: now() })) });
      items.push(created); return json(detail(created), 201);
    }
    const match = /^\/api\/v1\/work-items\/(work_\d+)(\/.*)?$/.exec(path);
    if (match) {
      const found = items.find((i) => i.id === match[1]);
      if (!found) return json({ error: { code: 'not_found', message: 'Work item not found' } }, 404);
      const rest = match[2] ?? '';
      if (rest === '' && method === 'GET') return json(detail(found));
      if (options.denyEdit && method !== 'GET') return json({ error: { code: 'forbidden', message: 'Only the person who created this work item, the person it is assigned to, or a workspace owner can change it.' } }, 403);
      if (rest === '' && method === 'PATCH') {
        const b = body();
        if (b.version !== found.version) return json({ error: { code: 'conflict', message: 'Someone changed this work item after you opened it. Reload it to see their change, then try again.' } }, 409);
        Object.assign(found, b.status ? { status: b.status } : {}, b.priority ? { priority: b.priority } : {}); found.version += 1; return json(detail(found));
      }
      if (rest === '' && method === 'DELETE') { items.splice(items.indexOf(found), 1); await route.fulfill({ status: 204 }); return true; }
      if (rest === '/comments' && method === 'POST') { const c = { id: `cmt_${found.comments.length + 1}`, author_type: 'user', author_id: 'user_local', author_name: 'Omar Owner', body: body().body, created_at: now() }; found.comments.push(c); found.version += 1; return json(c, 201); }
      if (rest === '/checklist' && method === 'POST') { const c = { id: `chk_${found.checklist.length + 10}`, label: body().label, done: false, position: found.checklist.length, done_by: null, done_at: null, created_at: now() }; found.checklist.push(c); return json(c, 201); }
      const check = /^\/checklist\/([^/]+)$/.exec(rest);
      if (check && method === 'PATCH') { const c = found.checklist.find((x) => x.id === check[1]); if (c) c.done = body().done; return json(c ?? {}); }
    }
    if (path === '/api/v1/platform/organizations' && method === 'GET') {
      if (options.denyOrganisations) return json({ error: { code: 'forbidden', message: 'Platform operator role required: onboarding customer organisations and the platform console are for the person who runs this ANUM deployment.' } }, 403);
      return json(orgs);
    }
    if (path === '/api/v1/platform/organizations' && method === 'POST') {
      const b = body();
      const summary = { tenant_id: b.tenant_id, display_name: b.display_name, plan: b.plan, first_workspace_id: `${String(b.tenant_id)}_main`, created_at: now(), workspaces: 1, active_members: 0, pending_invitations: 1, open_agent_tasks: 0, open_work_items: 0, overdue_work_items: 0, last_activity_at: null, health: 'setting_up' };
      orgs.push(summary);
      return json({ organisation: summary, invitation_id: 'invitation_1', invitation_token: 'anum_inv_example', invitation_expires_at: later, workspace_id: `${String(b.tenant_id)}_main` }, 201);
    }
    return false;
  };
}
