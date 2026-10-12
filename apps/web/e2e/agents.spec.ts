import { expect, test, type Page } from '@playwright/test';
import { useSavedLocale } from './support/workspaceApi';

// Agents (docs/agents.md) against a stateful mocked API: the list with categories, search and
// paging; creating from a template through the wizard; the agent's page with Chat, Call, Edit and
// More; running a task as the agent; its memory; what a viewer sees; failures; and Arabic.
const now = '2026-10-11T09:00:00.000Z';
type Json = Record<string, unknown>;
const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const catalog = {
  tools: [
    { name: 'anum.respond', description: 'Create an internal response.', risk_level: 'low', category: 'read', always_on: true, available_to_you: true, reason: null },
    { name: 'web_search', description: 'Search the web.', risk_level: 'low', category: 'read', always_on: false, available_to_you: true, reason: null },
    { name: 'external.action', description: 'Perform an external action after approval.', risk_level: 'high', category: 'external', always_on: false, available_to_you: true, reason: null },
  ],
  skills: [
    { id: 'anum.document-drafting', name: 'Document drafting', description: 'Drafts and summarizes text.', source: 'builtin', required_tools: ['anum.respond'] },
    { id: 'anum.web-search', name: 'Web search', description: 'Looks things up on the web.', source: 'builtin', required_tools: ['web_search'] },
    { id: 'acme.release-notes', name: 'Release notes', description: 'Writes release notes in the house style.', source: 'installed', required_tools: ['anum.respond'] },
  ],
  templates: ['fleet', 'research', 'email', 'calendar', 'document', 'call'].map((id) => ({
    id, name: id, icon: id === 'fleet' ? 'truck' : id === 'research' ? 'search' : 'bot', color: '#0f766e', category: id === 'calendar' ? 'personal' : 'work',
    description: id, instructions: id === 'fleet' ? 'Plan tyre and vehicle maintenance.' : `Be a ${id} agent.`, model_route: 'capable',
    allowed_tools: id === 'research' ? ['web_search'] : [], allowed_skills: ['anum.document-drafting'], autonomy: { read: 'auto', write: 'ask', external: 'ask' },
    knowledge_mode: 'none', missing_capabilities: id === 'fleet' ? ['Tyre-planning tools (TyrePulse connector)'] : [],
  })),
  icons: ['bot', 'truck', 'search', 'mail'],
  autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' },
};

function agentRow(id: string, name: string, extra: Json = {}): Json {
  return {
    id, tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', owner_user_id: 'user_local', status: 'active', created_at: now, updated_at: now,
    version: 1, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name, icon: 'bot', color: '#4f46e5', category: 'work',
    department: null, description: `${name} helps the team.`, instructions: 'Keep it short.', model_route: 'capable', allowed_skills: [], allowed_tools: [],
    autonomy: { read: 'auto', write: 'ask', external: 'ask' }, knowledge_mode: 'none', knowledge_sources: [], sharing: 'private', template_id: null, ...extra,
  };
}

interface Options { role?: 'owner' | 'member' | 'viewer'; agents?: Json[]; listFails?: boolean; runFails?: boolean; loadoutFails?: number; disabledSkill?: string }

/** What `GET /api/v1/agents/{id}/skills` answers for an agent, from the catalog (docs/agents.md#skills). */
function loadoutOf(agent: Json, disabled?: string): Json {
  const allowed = new Set([...(agent.allowed_tools as string[]), 'anum.respond']);
  const items = (agent.allowed_skills as string[]).map((id) => {
    const skill = catalog.skills.find((s) => s.id === id);
    if (!skill) return { id, name: id, description: '', source: 'unknown', version: null, latest_version: null, risk_level: null, state: 'unavailable', reason: 'No skill with this id exists here any more. Remove it from the agent.', activation: 'always', triggers: [], loads_instructions: false, upgrade_available: false, tools: [] };
    const installed = skill.source === 'installed';
    const tools = skill.required_tools.map((name) => ({ name, registered: true, allowed: allowed.has(name), granted: installed ? true : null }));
    const missing = tools.filter((t) => !t.allowed).map((t) => t.name);
    const state = id === disabled ? 'disabled' : missing.length ? 'needs_tools' : 'ready';
    const reason = state === 'disabled' ? 'A workspace owner turned this skill off in Skills. Agents load it again once it is enabled.'
      : state === 'needs_tools' ? `Allow the tool ${missing.join(', ')} on this agent so the skill can run.` : null;
    return { id, name: skill.name, description: skill.description, source: skill.source, version: installed ? '2.0.0' : '1.0.0', latest_version: installed ? '2.0.0' : '1.0.0',
      risk_level: 'low', state, reason, activation: installed ? 'always' : 'on_request', triggers: installed ? [] : ['search', 'web'], loads_instructions: installed && state === 'ready', upgrade_available: false, tools };
  });
  const ready = items.filter((i) => i.state === 'ready').length;
  return { agent_id: agent.id, agent_version: agent.version, can_edit: agent.can_edit, items, ready, attention: items.length - ready };
}

async function mockAgents(page: Page, options: Options = {}) {
  let agents: Json[] = options.agents ?? [
    agentRow('agent_fleet', 'Fleet desk', { icon: 'truck', category: 'work', department: 'Logistics', sharing: 'workspace' }),
    agentRow('agent_shop', 'Groceries', { category: 'personal', description: 'Weekly shopping list.' }),
  ];
  const memories = new Map<string, Json[]>([['agent_fleet', [{ id: 'mem_1', agent_id: 'agent_fleet', content: 'Truck 12 runs on Michelin tyres.', source: 'manual', task_id: null, created_by: 'user_local', created_at: now }]]]);
  const tasks: Json[] = [];
  const calls: { method: string; path: string; body: unknown }[] = [];
  const viewer = options.role === 'viewer';
  let n = 0;
  let loadoutFailures = options.loadoutFails ?? 0;
  await page.route('http://localhost:8000/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    calls.push({ method, path: `${path}${url.search}`, body });
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    const envelope = (status: number, code: string, message: string) => json({ error: { code, message, correlation_id: 'corr', details: [] } }, status);
    const view = (a: Json) => viewer ? { ...a, can_edit: false, can_run: false } : a;
    if (path === '/api/v1/events/stream') return route.fulfill({ status: 204 });
    if (path === '/api/v1/workspace-memberships/current') return json({ tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', user_id: 'user_local', role: options.role ?? 'owner', active: true, created_at: now, updated_at: now });
    if (path === '/api/v1/agents/catalog') return json(catalog);
    if (path === '/api/v1/agents' && method === 'GET') {
      if (options.listFails) return envelope(503, 'unavailable', 'The database is not reachable.');
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      let rows = agents.filter((a) => !q || `${a.name} ${a.description} ${a.department ?? ''}`.toLowerCase().includes(q));
      const counts = { all: rows.length, work: rows.filter((a) => a.category === 'work').length, personal: rows.filter((a) => a.category === 'personal').length, custom: rows.filter((a) => a.category === 'custom').length };
      const category = url.searchParams.get('category');
      if (category) rows = rows.filter((a) => a.category === category);
      const cursor = url.searchParams.get('cursor');
      if (cursor) rows = rows.slice(rows.findIndex((a) => a.id === cursor) + 1);
      const limit = Number(url.searchParams.get('limit') ?? 24);
      const page = rows.slice(0, limit);
      return json({ items: page.map(view), next_cursor: rows.length > limit ? page[page.length - 1].id : null, counts });
    }
    if (path === '/api/v1/agents' && method === 'POST') {
      const created = agentRow(`agent_${++n}`, String((body as Json).name), body as Json);
      agents = [created, ...agents];
      return json(created, 201);
    }
    if (path === '/api/v1/agents/skills/usage') {
      const usage = new Map<string, Json[]>();
      for (const a of agents) for (const id of a.allowed_skills as string[]) usage.set(id, [...(usage.get(id) ?? []), { id: a.id, name: a.name, icon: a.icon, color: a.color, status: a.status }]);
      return json({ items: [...usage.entries()].map(([skill_id, list]) => ({ skill_id, count: list.length, agents: list })) });
    }
    const skills = /^\/api\/v1\/agents\/([^/]+)\/skills$/.exec(path);
    if (skills) {
      if (loadoutFailures > 0) { loadoutFailures -= 1; return envelope(503, 'unavailable', 'The skill registry is not reachable.'); }
      const found = agents.find((a) => a.id === skills[1]);
      if (!found) return envelope(404, 'not_found', 'Agent not found.');
      return json(loadoutOf(view(found), options.disabledSkill));
    }
    const memory = /^\/api\/v1\/agents\/([^/]+)\/memories(?:\/([^/]+))?$/.exec(path);
    if (memory) {
      const list = memories.get(memory[1]) ?? [];
      if (method === 'GET') {
        const q = (url.searchParams.get('q') ?? '').toLowerCase();
        const rows = list.filter((m) => String(m.content).toLowerCase().includes(q));
        return json({ items: rows, next_cursor: null, total: rows.length });
      }
      if (method === 'POST') {
        const created = { id: `mem_${++n}`, agent_id: memory[1], content: (body as Json).content, source: 'manual', task_id: null, created_by: 'user_local', created_at: now };
        memories.set(memory[1], [created, ...list]);
        return json(created, 201);
      }
      memories.set(memory[1], list.filter((m) => m.id !== memory[2]));
      return route.fulfill({ status: 204 });
    }
    const one = /^\/api\/v1\/agents\/([^/]+)$/.exec(path);
    if (one) {
      const found = agents.find((a) => a.id === one[1]);
      if (!found) return envelope(404, 'not_found', 'Agent not found. It may have been deleted, or it is private to someone else.');
      if (method === 'GET') return json(view(found));
      if (method === 'PATCH') {
        const { version, ...changes } = body as Json;
        if (version !== found.version) return envelope(409, 'conflict', 'Someone changed this agent since you opened it. Reload and apply your change again.');
        const updated = { ...found, ...changes, version: Number(found.version) + 1 };
        agents = agents.map((a) => a.id === found.id ? updated : a);
        return json(updated);
      }
      if (method === 'DELETE') { agents = agents.filter((a) => a.id !== found.id); return route.fulfill({ status: 204 }); }
    }
    if (path === '/api/v1/tasks' && method === 'GET') {
      const agentId = url.searchParams.get('agent_id');
      return json(tasks.filter((t) => !agentId || t.agent_id === agentId));
    }
    if (path === '/api/v1/tasks' && method === 'POST') {
      if (options.runFails) return envelope(409, 'conflict', 'Fleet desk is paused. Resume it in Agents to run tasks with it.');
      const created = { id: `task_${++n}`, title: (body as Json).title, prompt: (body as Json).prompt, status: 'created', tenant_id: 'tenant_local', workspace_id: 'workspace_foundation', created_at: now, updated_at: now, agent_id: (body as Json).agent_id ?? null };
      tasks.unshift(created);
      return json(created, 201);
    }
    const run = /^\/api\/v1\/tasks\/([^/]+)\/run$/.exec(path);
    if (run) {
      const task = tasks.find((t) => t.id === run[1])!;
      task.status = 'completed';
      return json({ task, run: { id: 'run_1', task_id: task.id, status: 'completed', result: 'Rotate the front tyres on trucks 3 and 12 on Tuesday.', steps: [], checkpoint: { agent: { id: task.agent_id } } }, approval: null });
    }
    if (path === '/api/v1/conversations' && method === 'GET') return json([]);
    if (path === '/api/v1/conversations' && method === 'POST') return json({ id: 'conv_agent', title: '', created_at: now, updated_at: now, agent_id: (body as Json).agent_id }, 201);
    if (path === '/api/v1/conversations/conv_agent/ask') {
      const user = { id: 't1', conversation_id: 'conv_agent', role: 'user', content: (body as Json).content, status: 'complete', created_at: now };
      const assistant = { id: 't2', conversation_id: 'conv_agent', role: 'assistant', content: 'Pit stop at noon.', status: 'complete', created_at: now };
      return route.fulfill({ contentType: 'text/event-stream', body: frame('turn', { user_turn: user }) + frame('delta', { text: 'Pit stop at noon.' }) + frame('done', { assistant_turn: assistant }) });
    }
    if (path === '/api/v1/retrieval/status') return json({ embedding_model: 'm', indexed: 1, failed: 0, skipped: 0, stale: 0, chunks: 1, sources: [{ source_type: 'file', source_id: 'file_notes', name: 'fleet-notes.md', status: 'indexed', error: null, chunk_count: 1, embedding_model: 'm', indexed_at: now }] });
    if (method === 'GET' && path.startsWith('/api/v1/')) return json([]);
    return route.abort();
  });
  return { calls, agents: () => agents };
}

async function openAgents(page: Page) {
  await page.goto('/#agents');
  await expect(page.getByRole('heading', { name: 'Your agents' })).toBeVisible();
}

test('the list filters by category, searches and opens an agent', async ({ page }) => {
  const api = await mockAgents(page);
  await openAgents(page);
  const list = page.getByRole('list', { name: 'Your agents' });
  await expect(list.getByRole('listitem')).toHaveCount(2);
  await expect(page.getByRole('button', { name: /Work\s*1/ })).toBeVisible();
  await page.getByRole('button', { name: /Personal\s*1/ }).click();
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('Groceries')).toBeVisible();
  await page.getByRole('button', { name: /All\s*2/ }).click();
  await page.getByLabel('Search agents', { exact: true }).fill('logistics');
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('Work · Logistics')).toBeVisible();
  expect(api.calls.some((c) => c.path.includes('q=logistics'))).toBe(true);
  await page.getByLabel('Search agents', { exact: true }).fill('nothing like this');
  await expect(page.getByRole('heading', { name: 'No agents match' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await list.getByRole('button', { name: 'Fleet desk' }).click();
  await expect(page.getByRole('heading', { name: 'Fleet desk', level: 2 })).toBeVisible();
  await expect(page.locator('.agentActions').getByRole('button', { name: 'Chat' })).toBeVisible();
  await expect(page.locator('.agentActions').getByRole('button', { name: 'Call' })).toBeVisible();
  await expect(page.locator('.agentActions').getByRole('button', { name: 'Edit' })).toBeVisible();
  await expect(page.locator('.agentActions').getByRole('button', { name: 'More' })).toBeVisible();
  for (const tab of ['Overview', 'Skills', 'Memory', 'Settings']) await expect(page.getByRole('navigation', { name: 'Agent sections' }).getByRole('button', { name: tab })).toBeVisible();
});

test('create an agent from a template, then run a task as it', async ({ page }) => {
  const api = await mockAgents(page, { agents: [] });
  await openAgents(page);
  await expect(page.getByRole('heading', { name: 'No agents yet' })).toBeVisible();
  await page.getByRole('button', { name: 'Create your first agent' }).click();
  await expect(page.getByText('Not connected yet: Tyre-planning tools (TyrePulse connector)')).toBeVisible();
  await page.getByRole('button', { name: /^Fleet Agent/ }).click();
  await expect(page.getByLabel('Name')).toHaveValue('Fleet Agent');
  await page.getByLabel('Name').fill('');
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('Give the agent a name.')).toBeVisible();
  await page.getByLabel('Name').fill('Tyre planner');
  await page.getByRole('radio', { name: 'Truck' }).click();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByLabel('Instructions')).toHaveValue('Plan tyre and vehicle maintenance.');
  await page.getByRole('button', { name: 'Next' }).click();
  // Skills and tools: turning on web search also allows its tool.
  await page.getByRole('checkbox', { name: /^Web search/ }).check();
  await expect(page.getByRole('checkbox', { name: /^web_search/ })).toBeChecked();
  await page.getByRole('button', { name: 'Next' }).click();
  // Outward actions can only ask or be off.
  const external = page.getByRole('group', { name: 'Actions outside ANUM' });
  await expect(external.getByRole('button', { name: 'Automatic' })).toHaveCount(0);
  await page.getByRole('group', { name: 'Reading' }).getByRole('button', { name: 'Ask first' }).click();
  await page.getByRole('radio', { name: /Selected memories and files/ }).check();
  await page.getByRole('checkbox', { name: /fleet-notes.md/ }).check();
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('radio', { name: /Shared with the workspace/ }).check();
  await page.getByRole('button', { name: 'Create agent' }).click();
  await expect(page.getByText('Created Tyre planner.')).toBeVisible();
  const created = api.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/agents')!.body as Json;
  expect(created).toMatchObject({
    name: 'Tyre planner', icon: 'truck', template_id: 'fleet', sharing: 'workspace', allowed_tools: ['web_search'],
    allowed_skills: ['anum.document-drafting', 'anum.web-search'], autonomy: { read: 'ask', write: 'ask', external: 'ask' },
    knowledge_mode: 'selected', knowledge_sources: [{ type: 'file', id: 'file_notes' }],
  });

  await page.getByLabel('What should Tyre planner do?').fill('Plan next week\'s rotations');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByText('Rotate the front tyres on trucks 3 and 12 on Tuesday.')).toBeVisible();
  const task = api.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/tasks')!.body as Json;
  expect(task.agent_id).toBe('agent_1');
  const recent = page.getByRole('region', { name: 'Recent tasks' });
  await expect(recent.getByText('Plan next week\'s rotations')).toBeVisible();
});

test('memory, chat, call summary, pause and delete on an agent\'s page', async ({ page }) => {
  const api = await mockAgents(page);
  await openAgents(page);
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  const tabs = page.getByRole('navigation', { name: 'Agent sections' });
  await tabs.getByRole('button', { name: 'Memory' }).click();
  await expect(page.getByText('Truck 12 runs on Michelin tyres.')).toBeVisible();
  await page.getByLabel('Add a memory').fill('Depot is in Jeddah');
  await page.getByRole('button', { name: 'Save memory' }).click();
  await expect(page.getByText('Depot is in Jeddah')).toBeVisible();
  await page.getByRole('button', { name: 'Delete memory: Truck 12 runs on Michelin tyres.' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByText('Truck 12 runs on Michelin tyres.')).toHaveCount(0);

  await page.locator('.agentActions').getByRole('button', { name: 'Chat' }).click();
  await page.getByRole('textbox', { name: /message|ask/i }).first().fill('When is the pit stop?');
  await page.keyboard.press('Enter');
  await expect(page.getByText('Pit stop at noon.')).toBeVisible();
  expect(api.calls.find((c) => c.method === 'POST' && c.path === '/api/v1/conversations')!.body).toEqual({ agent_id: 'agent_fleet' });

  await page.locator('.agentActions').getByRole('button', { name: 'Call' }).click();
  await expect(page.getByText(/needs telephony, which is not set up/)).toBeVisible();
  await page.getByLabel('Call notes or transcript').fill('Ali: tyres arrive Monday. Sara: book fitting Tuesday.');
  await page.getByRole('button', { name: 'Summarize call' }).click();
  await expect(page.getByText('Rotate the front tyres on trucks 3 and 12 on Tuesday.')).toBeVisible();
  const callTask = api.calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/tasks').pop()!.body as Json;
  expect(String(callTask.prompt)).toContain('Ali: tyres arrive Monday.');

  await page.locator('.agentActions').getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Pause' }).click();
  await expect(page.getByText(/This agent is paused/)).toBeVisible();
  await expect(page.locator('.agentActions').getByRole('button', { name: 'Chat' })).toBeDisabled();
  await page.locator('.agentActions').getByRole('button', { name: 'More' }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
  await page.locator('.agentActions').getByRole('button', { name: 'More' }).click();
  await page.getByRole('menuitem', { name: 'Delete agent' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete agent' }).click();
  await expect(page.getByText('Deleted Fleet desk. Its tasks stay in the task history.')).toBeVisible();
  expect(api.agents().map((a) => a.id)).toEqual(['agent_shop']);
});

test('a viewer can open shared agents but not create, change or run them', async ({ page }) => {
  await mockAgents(page, { role: 'viewer' });
  await openAgents(page);
  await expect(page.getByText('You can use agents shared with this workspace. Members and owners can create agents.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create agent' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  await expect(page.getByText('You can use this agent. Only its creator, or a workspace owner, can change it.')).toBeVisible();
  await expect(page.locator('.agentActions').getByRole('button', { name: 'Edit' })).toHaveCount(0);
  await expect(page.getByText('Viewers cannot run tasks. Ask a member or owner.')).toBeVisible();
});

test('failures say what went wrong and offer a retry', async ({ page }) => {
  await mockAgents(page, { listFails: true });
  await openAgents(page);
  await expect(page.getByRole('alert').getByText('Could not load agents: The database is not reachable.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('a refused task explains why', async ({ page }) => {
  await mockAgents(page, { runFails: true });
  await openAgents(page);
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  await page.getByLabel('What should Fleet desk do?').fill('Check tyre wear');
  await page.getByRole('button', { name: 'Run task' }).click();
  await expect(page.getByText('The task did not start: Fleet desk is paused. Resume it in Agents to run tasks with it.')).toBeVisible();
});

test('Arabic: the list, the wizard and the agent page are translated and right to left', async ({ page }) => {
  await mockAgents(page);
  await useSavedLocale(page, 'ar');
  await page.goto('/#agents');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.getByRole('heading', { name: 'وكلاؤك' })).toBeVisible();
  await expect(page.getByRole('button', { name: /العمل\s*1/ })).toBeVisible();
  await page.getByRole('button', { name: 'إنشاء وكيل' }).click();
  await expect(page.getByRole('button', { name: /^وكيل الأسطول/ })).toBeVisible();
  await page.getByRole('button', { name: 'إلغاء' }).click();
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  for (const label of ['محادثة', 'مكالمة', 'تعديل', 'المزيد']) await expect(page.locator('.agentActions').getByRole('button', { name: label, exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'أقسام الوكيل' }).getByRole('button', { name: 'الذاكرة' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('the Skills tab shows what the agent loads, fixes a missing tool, adds and removes skills', async ({ page }) => {
  const api = await mockAgents(page, { agents: [
    agentRow('agent_fleet', 'Fleet desk', { allowed_skills: ['acme.release-notes', 'anum.web-search'], allowed_tools: [], unavailable_skills: [] }),
  ] });
  await openAgents(page);
  const card = page.getByRole('list', { name: 'Your agents' }).getByRole('listitem').first();
  await expect(card.getByText('Release notes')).toBeVisible();
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  await page.getByRole('navigation', { name: 'Agent sections' }).getByRole('button', { name: 'Skills' }).click();
  const loaded = page.getByRole('list', { name: 'Skills this agent loads' });
  await expect(page.getByText('Ready: 1. Needs attention: 1.')).toBeVisible();
  const notes = loaded.getByRole('listitem').filter({ hasText: 'Release notes' }).first();
  await expect(notes.getByText('Ready', { exact: true })).toBeVisible();
  await expect(notes.getByText('Loads its instructions into every task and chat with this agent.')).toBeVisible();
  const search = loaded.getByRole('listitem').filter({ hasText: 'Web search' }).first();
  await expect(search.getByText('Needs tools', { exact: true })).toBeVisible();
  await expect(search.getByText('Joins a task when you ask about: search, web')).toBeVisible();

  // One step fixes the missing tool: a PATCH with the version the page read.
  await search.getByRole('button', { name: 'Allow web_search' }).click();
  await expect(page.getByText('Allowed web_search on this agent.')).toBeVisible();
  await expect(page.getByText('Ready: 2. Needs attention: 0.')).toBeVisible();
  const allow = api.calls.filter((c) => c.method === 'PATCH').pop()!.body as Json;
  expect(allow).toEqual({ allowed_tools: ['web_search'], version: 1 });

  // Add a skill from the searchable picker.
  await page.getByRole('button', { name: 'Add a skill' }).first().click();
  await page.getByLabel('Search available skills').fill('draft');
  await page.getByRole('button', { name: 'Add Document drafting' }).click();
  await expect(page.getByText('Document drafting added. The next task and chat load it.')).toBeVisible();
  const add = api.calls.filter((c) => c.method === 'PATCH').pop()!.body as Json;
  expect(add).toEqual({ allowed_skills: ['acme.release-notes', 'anum.document-drafting', 'anum.web-search'], allowed_tools: ['web_search'], version: 2 });
  await expect(page.getByText('This agent already loads every available skill.')).toBeVisible();
  await page.getByRole('button', { name: 'Done' }).click();

  await page.getByRole('button', { name: 'Remove Release notes from this agent' }).click();
  await expect(page.getByText('Release notes removed. Tasks that already started keep it.')).toBeVisible();
  await expect(loaded.getByText('Release notes')).toHaveCount(0);
  expect(api.agents()[0].allowed_skills).toEqual(['anum.document-drafting', 'anum.web-search']);
});

test('a skill turned off in the workspace says who can fix it; a viewer sees the skills read only', async ({ page }) => {
  await mockAgents(page, { role: 'viewer', disabledSkill: 'acme.release-notes', agents: [agentRow('agent_fleet', 'Fleet desk', { sharing: 'workspace', allowed_skills: ['acme.release-notes'] })] });
  await openAgents(page);
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  await page.getByRole('navigation', { name: 'Agent sections' }).getByRole('button', { name: 'Skills' }).click();
  await expect(page.getByText('Turned off', { exact: true })).toBeVisible();
  await expect(page.getByText(/A workspace owner turned this skill off in Skills/)).toBeVisible();
  await expect(page.getByText('Only this agent’s creator, or a workspace owner for a shared agent, can change its skills.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add a skill' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Remove / })).toHaveCount(0);
});

test('an agent without skills offers to add one, and a failed load can be retried', async ({ page }) => {
  await mockAgents(page, { loadoutFails: 1, agents: [agentRow('agent_fleet', 'Fleet desk')] });
  await openAgents(page);
  await page.getByRole('button', { name: 'Fleet desk' }).click();
  await page.getByRole('navigation', { name: 'Agent sections' }).getByRole('button', { name: 'Skills' }).click();
  await expect(page.getByRole('alert').getByText(/Could not load this agent’s skills: The skill registry is not reachable./)).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('heading', { name: 'No skills loaded' })).toBeVisible();
  await page.locator('.compactEmpty').getByRole('button', { name: 'Add a skill' }).click();
  await expect(page.getByRole('button', { name: 'Add Release notes' })).toBeVisible();
});
