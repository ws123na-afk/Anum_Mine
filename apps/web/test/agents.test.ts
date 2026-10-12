// Unit tests for the Agents view helpers in src/lib/agents.ts (docs/agents.md).
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  agentListPath, agentsApi, callSummaryPrompt, draftBody, draftChanges, draftFromAgent, draftFromTemplate, effectiveLevel, emptyDraft,
  levelsFor, memoryListPath, mergeById, recentTasksPath, skillsMissingTools, templateKey, toggleSkill, toggleTool, toolGroups, validateDraft,
  type AgentCatalog, type AgentProfile,
} from '../src/lib/agents.ts';
import { CATALOGS } from '../src/lib/i18n.ts';

const catalog: AgentCatalog = {
  tools: [
    { name: 'anum.respond', description: 'Text', risk_level: 'low', category: 'read', always_on: true, available_to_you: true, reason: null },
    { name: 'web_search', description: 'Search', risk_level: 'low', category: 'read', always_on: false, available_to_you: true, reason: null },
    { name: 'fleet.reset', description: 'Reset', risk_level: 'medium', category: 'write', always_on: false, available_to_you: false, reason: 'Needs the owner role, which you do not have.' },
    { name: 'external.action', description: 'Act', risk_level: 'high', category: 'external', always_on: false, available_to_you: true, reason: null },
  ],
  skills: [
    { id: 'anum.web-search', name: 'Web search', description: 'Search', source: 'builtin', required_tools: ['web_search'] },
    { id: 'anum.document-drafting', name: 'Drafting', description: 'Drafts', source: 'builtin', required_tools: ['anum.respond'] },
    { id: 'acme.invoices', name: 'Invoices', description: 'Reads', source: 'installed', required_tools: ['erp.read'] },
  ],
  templates: [{
    id: 'research', name: 'Research Agent', icon: 'search', color: '#2563eb', category: 'work', description: 'Looks things up',
    instructions: 'Research it.', model_route: 'capable', allowed_tools: ['web_search'], allowed_skills: ['anum.web-search'],
    autonomy: { read: 'auto', write: 'ask', external: 'ask' }, knowledge_mode: 'none', missing_capabilities: [],
  }],
  icons: ['bot', 'search'],
  autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' },
};

const agent: AgentProfile = {
  id: 'agent_1', tenant_id: 't', workspace_id: 'w', owner_user_id: 'u', status: 'active', created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z', version: 3, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [],
  name: 'Fleet', icon: 'truck', color: '#0f766e', category: 'work', department: null, description: 'Tyres', instructions: 'Plan.',
  model_route: 'capable', allowed_skills: [], allowed_tools: ['web_search'], autonomy: { read: 'auto', write: 'ask', external: 'ask' },
  knowledge_mode: 'none', knowledge_sources: [], sharing: 'private', template_id: 'fleet',
};

describe('paths', () => {
  test('list path carries only the filters in use', () => {
    assert.equal(agentListPath({ category: 'all', q: '  ', status: 'all' }), '/api/v1/agents?limit=24');
    assert.equal(agentListPath({ category: 'work', q: ' tyre ', status: 'paused', cursor: 'agent_9' }), '/api/v1/agents?limit=24&category=work&status=paused&q=tyre&cursor=agent_9');
    assert.equal(memoryListPath('agent/1', 'michelin', 'm1'), '/api/v1/agents/agent%2F1/memories?limit=20&q=michelin&cursor=m1');
    assert.equal(recentTasksPath('agent_1'), '/api/v1/tasks?agent_id=agent_1&limit=10');
  });
});

describe('drafts', () => {
  test('a template becomes a draft with the localized name', () => {
    const draft = draftFromTemplate(catalog.templates[0], 'وكيل البحث', 'يبحث');
    assert.equal(draft.name, 'وكيل البحث');
    assert.deepEqual(draft.allowed_tools, ['web_search']);
    assert.equal(draft.template_id, 'research');
  });

  test('the body trims, sorts and drops sources unless selected', () => {
    const draft = { ...emptyDraft(), name: '  Ops  ', department: '  ', allowed_tools: ['b', 'a'], knowledge_sources: [{ type: 'file' as const, id: 'f' }] };
    const body = draftBody(draft);
    assert.equal(body.name, 'Ops');
    assert.equal(body.department, null);
    assert.deepEqual(body.allowed_tools, ['a', 'b']);
    assert.deepEqual(body.knowledge_sources, []);
  });

  test('an edit sends only what changed, with the version it started from', () => {
    const draft = { ...draftFromAgent(agent), name: 'Fleet planner', autonomy: { ...agent.autonomy, read: 'ask' as const } };
    assert.deepEqual(draftChanges(agent, draft), { name: 'Fleet planner', autonomy: { read: 'ask', write: 'ask', external: 'ask' }, version: 3 });
    assert.deepEqual(draftChanges(agent, draftFromAgent(agent)), { version: 3 });
  });
});

describe('validation', () => {
  test('identity needs a name of at most 80 characters', () => {
    assert.equal(validateDraft(emptyDraft(), 'identity', catalog).name, 'agents.err.nameRequired');
    assert.equal(validateDraft({ ...emptyDraft(), name: 'x'.repeat(81) }, 'identity', catalog).name, 'agents.err.nameLong');
    assert.deepEqual(validateDraft({ ...emptyDraft(), name: 'Ok' }, 'identity', catalog), {});
  });

  test('a built-in skill needs its tools; selected knowledge needs a source', () => {
    const draft = { ...emptyDraft(), name: 'x', allowed_skills: ['anum.web-search'] };
    assert.equal(validateDraft(draft, 'capabilities', catalog).skills, 'agents.err.skillNeedsTool');
    assert.deepEqual(skillsMissingTools(draft, catalog).map((m) => m.tools), [['web_search']]);
    assert.equal(validateDraft({ ...draft, allowed_tools: ['web_search'] }, 'capabilities', catalog).skills, undefined);
    assert.equal(validateDraft({ ...emptyDraft(), name: 'x', knowledge_mode: 'selected' }, 'review', catalog).knowledge, 'agents.err.knowledgeEmpty');
  });
});

describe('capabilities', () => {
  test('turning a built-in skill on allows its usable tools; installed skills grant nothing', () => {
    const on = toggleSkill(emptyDraft(), catalog.skills[0], true, catalog);
    assert.deepEqual(on.allowed_tools, ['web_search']);
    const installed = toggleSkill(emptyDraft(), catalog.skills[2], true, catalog);
    assert.deepEqual(installed.allowed_tools, []);
    assert.deepEqual(toggleSkill(on, catalog.skills[0], false, catalog).allowed_skills, []);
    assert.deepEqual(toggleTool(on, 'web_search', false).allowed_tools, []);
  });

  test('tools are grouped by category without the always-on text tool', () => {
    const groups = toolGroups(catalog);
    assert.deepEqual(groups.read.map((tool) => tool.name), ['web_search']);
    assert.deepEqual(groups.write.map((tool) => tool.name), ['fleet.reset']);
    assert.deepEqual(groups.external.map((tool) => tool.name), ['external.action']);
  });

  test('external actions can never be automatic, and the workspace floor wins', () => {
    assert.deepEqual(levelsFor('external'), ['off', 'ask']);
    assert.deepEqual(levelsFor('write'), ['off', 'ask', 'auto']);
    assert.equal(effectiveLevel('auto', 'ask'), 'ask');
    assert.equal(effectiveLevel('off', 'auto'), 'off');
    assert.equal(effectiveLevel('ask', 'auto'), 'ask');
  });
});

test('pages merge without duplicates', () => {
  assert.deepEqual(mergeById([{ id: 'a' }, { id: 'b' }], [{ id: 'b' }, { id: 'c' }]).map((x) => x.id), ['a', 'b', 'c']);
});

test('the call summary prompt puts the instruction before the notes', () => {
  assert.equal(callSummaryPrompt('  hello  ', 'Summarize:'), 'Summarize:\n\nhello');
});

test('every template id has a name and description in both languages', () => {
  for (const id of ['fleet', 'research', 'email', 'calendar', 'document', 'call']) {
    for (const part of ['name', 'description'] as const) {
      assert.ok(CATALOGS.en[templateKey(id, part)], `${id} ${part} en`);
      assert.ok(CATALOGS.ar[templateKey(id, part)], `${id} ${part} ar`);
    }
  }
});

test('the API client sends the right requests', async () => {
  const calls: [string, RequestInit][] = [];
  const client = agentsApi(async <T,>(path: string, init: RequestInit) => { calls.push([path, init]); return {} as T; }, async (path, init) => { calls.push([path, init]); });
  await client.update('agent_1', { status: 'paused', version: 2 });
  await client.remove('agent_1');
  await client.addMemory('agent_1', 'Depot in Riyadh');
  assert.deepEqual(calls.map(([path, init]) => [path, init.method, init.body ?? null]), [
    ['/api/v1/agents/agent_1', 'PATCH', '{"status":"paused","version":2}'],
    ['/api/v1/agents/agent_1', 'DELETE', null],
    ['/api/v1/agents/agent_1/memories', 'POST', '{"content":"Depot in Riyadh"}'],
  ]);
});
