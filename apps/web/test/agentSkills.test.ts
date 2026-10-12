// Unit tests for the skills an agent loads (src/lib/agents.ts, docs/agents.md#skills), the Skills
// page helpers that show which agents use a skill (src/lib/catalog.ts) and the voice orb's
// state model (src/lib/orb.ts). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  addableSkills, agentsApi, allowableMissingTools, allowToolsChanges, attachSkillChanges, detachSkillChanges, filterLoadout, skillNames,
  skillStateTone, sortLoadout, usageById,
  type AgentCatalog, type AgentProfile, type AgentSkillState,
} from '../src/lib/agents.ts';
import { filterBuiltins, filterSkills, usageNames } from '../src/lib/catalog.ts';
import {
  crystalGeometry, hexToRgb, ORB_MOTION, ORB_STATES, orbColors, orbDpr, orbPalette, particleCount, particleSeeds, voiceLevel,
} from '../src/lib/orb.ts';

const catalog: AgentCatalog = {
  tools: [
    { name: 'anum.respond', description: 'Text', risk_level: 'low', category: 'read', always_on: true, available_to_you: true, reason: null },
    { name: 'web_search', description: 'Search', risk_level: 'low', category: 'read', always_on: false, available_to_you: true, reason: null },
    { name: 'fleet.reset', description: 'Reset', risk_level: 'medium', category: 'write', always_on: false, available_to_you: false, reason: 'Needs the owner role.' },
  ],
  skills: [
    { id: 'anum.web-search', name: 'Web search', description: 'Looks things up', source: 'builtin', required_tools: ['web_search'] },
    { id: 'anum.document-drafting', name: 'Drafting', description: 'Drafts', source: 'builtin', required_tools: ['anum.respond'] },
    { id: 'acme.invoices', name: 'Invoices', description: 'Reads invoices', source: 'installed', required_tools: ['erp.read'] },
  ],
  templates: [], icons: ['bot'], autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' },
};

const agent: AgentProfile = {
  id: 'agent_1', tenant_id: 't', workspace_id: 'w', owner_user_id: 'u', status: 'active', created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z', version: 4, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [],
  name: 'Fleet', icon: 'truck', color: '#0f766e', category: 'work', department: null, description: '', instructions: '',
  model_route: 'capable', allowed_skills: ['acme.invoices'], allowed_tools: [], autonomy: { read: 'auto', write: 'ask', external: 'ask' },
  knowledge_mode: 'none', knowledge_sources: [], sharing: 'private', template_id: null,
};

const item = (id: string, state: AgentSkillState['state'], extra: Partial<AgentSkillState> = {}): AgentSkillState => ({
  id, name: id, description: '', source: 'builtin', version: '1.0.0', latest_version: '1.0.0', risk_level: 'low', state, reason: null,
  activation: 'on_request', triggers: [], loads_instructions: false, upgrade_available: false, tools: [], ...extra,
});

describe('skills an agent loads', () => {
  test('state tones are distinct for ready, fixable and broken skills', () => {
    assert.equal(skillStateTone('ready'), 'success');
    assert.equal(skillStateTone('needs_tools'), 'warning');
    assert.equal(skillStateTone('disabled'), 'warning');
    assert.equal(skillStateTone('unavailable'), 'dangerTone');
    assert.equal(skillStateTone('blocked'), 'dangerTone');
    assert.equal(skillStateTone('not_installed'), 'neutral');
  });

  test('ready skills sort first, then by name; search covers names, ids and tools', () => {
    const sorted = sortLoadout([item('b', 'disabled'), item('z', 'ready'), item('a', 'ready')]);
    assert.deepEqual(sorted.map((s) => s.id), ['a', 'z', 'b']);
    const withTools = [item('x', 'ready', { tools: [{ name: 'web_search', registered: true, allowed: true, granted: null }] }), item('y', 'ready')];
    assert.deepEqual(filterLoadout(withTools, 'web_search').map((s) => s.id), ['x']);
    assert.equal(filterLoadout(withTools, '').length, 2);
  });

  test('only registered tools the caller may give, and the installation granted, can be allowed in one step', () => {
    const needs = item('anum.web-search', 'needs_tools', { tools: [
      { name: 'web_search', registered: true, allowed: false, granted: null },
      { name: 'fleet.reset', registered: true, allowed: false, granted: null },
      { name: 'gone.tool', registered: false, allowed: false, granted: null },
      { name: 'erp.read', registered: true, allowed: false, granted: false },
    ] });
    assert.deepEqual(allowableMissingTools(needs, catalog), ['web_search']);
    assert.deepEqual(allowableMissingTools(needs, null), []);
  });

  test('adding a built-in skill also allows its usable tools; every change carries the version read', () => {
    const attach = attachSkillChanges(agent, catalog.skills[0], catalog);
    assert.deepEqual(attach, { allowed_skills: ['acme.invoices', 'anum.web-search'], allowed_tools: ['web_search'], version: 4 });
    assert.deepEqual(detachSkillChanges(agent, 'acme.invoices'), { allowed_skills: [], version: 4 });
    assert.deepEqual(allowToolsChanges({ ...agent, allowed_tools: ['web_search'] }, ['web_search', 'anum.x']), { allowed_tools: ['anum.x', 'web_search'], version: 4 });
  });

  test('the picker offers skills not loaded yet, installed ones first, matching the search', () => {
    assert.deepEqual(addableSkills(catalog, ['acme.invoices'], '').map((s) => s.id), ['anum.document-drafting', 'anum.web-search']);
    assert.deepEqual(addableSkills(catalog, [], '').map((s) => s.id)[0], 'acme.invoices');
    assert.deepEqual(addableSkills(catalog, [], 'web search').map((s) => s.id), ['anum.web-search']);
    assert.deepEqual(addableSkills(null, [], ''), []);
  });

  test('agent cards name the first skills and count the rest', () => {
    const many = { ...agent, allowed_skills: ['anum.web-search', 'anum.document-drafting', 'acme.invoices', 'acme.unknown'] };
    assert.deepEqual(skillNames(many, catalog), { names: ['Web search', 'Drafting', 'Invoices'], more: 1 });
    assert.deepEqual(skillNames(many, null, 2), { names: ['anum.web-search', 'anum.document-drafting'], more: 2 });
  });

  test('the API paths for the loadout and the usage', async () => {
    const calls: string[] = [];
    const request = async <T,>(path: string) => { calls.push(path); return {} as T; };
    const client = agentsApi(request, async () => undefined);
    await client.skills('agent/1');
    await client.skillUsage();
    assert.deepEqual(calls, ['/api/v1/agents/agent%2F1/skills', '/api/v1/agents/skills/usage']);
  });
});

describe('Skills page: built-in skills and the agents that load a skill', () => {
  const builtins = [
    { id: 'anum.web-search', name: 'Web search', description: 'Looks things up', source: 'builtin', required_tools: ['web_search'] },
    { id: 'acme.invoices', name: 'Invoices', description: 'Reads', source: 'installed', required_tools: [] },
  ];
  test('built-in skills show under All and Built in, never Installed', () => {
    assert.deepEqual(filterBuiltins(builtins, '', 'all').map((s) => s.id), ['anum.web-search']);
    assert.deepEqual(filterBuiltins(builtins, 'web_search', 'builtin').map((s) => s.id), ['anum.web-search']);
    assert.deepEqual(filterBuiltins(builtins, '', 'installed'), []);
    assert.deepEqual(filterSkills([], '', 'builtin'), []);
  });
  test('usage names the first agents and counts the rest', () => {
    assert.deepEqual(usageNames(['A', 'B', 'C', 'D']), { shown: ['A', 'B'], more: 2 });
    const map = usageById({ items: [{ skill_id: 's', count: 1, agents: [{ id: 'a', name: 'A', icon: 'bot', color: '#000000', status: 'active' }] }] });
    assert.equal(map.get('s')?.count, 1);
    assert.equal(usageById(null).size, 0);
  });
});

describe('voice orb', () => {
  test('colours come from the design tokens, with the shipped values as fallback', () => {
    assert.deepEqual(hexToRgb('#ff0000'), [1, 0, 0]);
    assert.deepEqual(hexToRgb('#0f0'), [0, 1, 0]);
    assert.equal(hexToRgb('rgb(1,2,3)'), null);
    const fallback = orbColors();
    assert.deepEqual(fallback.alert, hexToRgb('#ffb547'));
    const themed = orbColors((name) => (name === '--d-sky' ? ' #000000 ' : ''));
    assert.deepEqual(themed.sky, [0, 0, 0]);
  });

  test('every state has its own core and heart colour, not one blue', () => {
    const colors = orbColors();
    const cores = new Set(ORB_STATES.map((state) => { const p = orbPalette(state, colors); return JSON.stringify([p.core, p.heart]); }));
    assert.equal(cores.size, ORB_STATES.length);
    assert.notDeepEqual(orbPalette('speaking', colors).heart, orbPalette('listening', colors).heart);
  });

  test('thinking turns fastest, speaking glows brightest, idle is calm', () => {
    assert.ok(ORB_MOTION.thinking.ringSpeed > ORB_MOTION.listening.ringSpeed && ORB_MOTION.thinking.ringSpeed > ORB_MOTION.idle.ringSpeed);
    assert.ok(ORB_MOTION.speaking.glow >= Math.max(...ORB_STATES.map((s) => ORB_MOTION[s].glow)));
    assert.ok(ORB_MOTION.idle.spin <= Math.min(...ORB_STATES.map((s) => ORB_MOTION[s].spin)));
  });

  test('the microphone level only drives listening and is clamped', () => {
    assert.equal(voiceLevel('listening', 2), 1);
    assert.equal(voiceLevel('listening', -1), 0);
    assert.equal(voiceLevel('listening', Number.NaN), 0);
    assert.equal(voiceLevel('speaking', 0.8), 0);
  });

  test('the canvas stays cheap on phones', () => {
    assert.equal(orbDpr(3), 1.5);
    assert.equal(orbDpr(0), 1);
    assert.ok(particleCount(208) < particleCount(400) && particleCount(10) >= 70 && particleCount(10_000) <= 220);
  });

  test('the crystal is a closed faceted solid with outward normals', () => {
    const g = crystalGeometry(1);
    assert.equal(g.faces, 80);
    assert.equal(g.position.length, 80 * 9);
    for (let f = 0; f < g.faces; f += 1) {
      const c = [0, 1, 2].map((axis) => (g.position[f * 9 + axis] + g.position[f * 9 + 3 + axis] + g.position[f * 9 + 6 + axis]) / 3);
      const n = [g.normal[f * 9], g.normal[f * 9 + 1], g.normal[f * 9 + 2]];
      assert.ok(c[0] * n[0] + c[1] * n[1] + c[2] * n[2] > 0, `face ${f} faces inward`);
    }
    assert.deepEqual(crystalGeometry(1).position, g.position, 'the same stone every time');
    assert.equal(particleSeeds(10).length, 40);
  });
});
