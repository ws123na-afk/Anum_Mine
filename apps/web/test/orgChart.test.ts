// Unit tests for the AI team org chart (src/lib/orgChart.ts, src/lib/orgChartImage.ts; docs/org-chart.md).
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import type { AgentProfile, AssistantProfile, TeamPerson } from '@anum/contracts';
import {
  agentTasksPath, autonomyRules, buildOrgChart, departmentAliases, departmentKey, failedTasksPath, familyStatus, filterMembers, loadOrgSources,
  memberStatus, NO_FILTER, openTasksPath, toolChips, toolOptions, visibleMembers, type OrgInputs,
} from '../src/lib/orgChart.ts';
import { columnsFor, escapeXml, fit, imageFileName, memberHeight, orgChartSvg, wrap, type ImageModel } from '../src/lib/orgChartImage.ts';
import { ApiError } from '../src/lib/errors.ts';
import { CATALOGS } from '../src/lib/i18n.ts';

function agent(id: string, extra: Partial<AgentProfile> = {}): AgentProfile {
  return {
    id, tenant_id: 't', workspace_id: 'w', owner_user_id: 'u', status: 'active', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
    version: 1, can_edit: true, can_run: true, unavailable_tools: [], unavailable_skills: [], name: id, icon: 'bot', color: '#4f46e5', category: 'work',
    department: null, description: '', instructions: '', model_route: 'capable', allowed_skills: [], allowed_tools: [],
    autonomy: { read: 'auto', write: 'ask', external: 'ask' }, knowledge_mode: 'none', knowledge_sources: [], sharing: 'workspace', template_id: null, ...extra,
  };
}

const assistant = (id: string, department: AssistantProfile['department'], extra: Partial<AssistantProfile> = {}): AssistantProfile => ({
  id, department, name: id, instructions: '', allowed_tools: ['anum.respond'], routing_keywords: [], inbound_channels: [], autonomy: 'suggest',
  enabled: true, created_by: 'u', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', ...extra,
});

const person = (id: string, department: TeamPerson['department']): TeamPerson => ({
  user_id: id, role: 'member', display_name: id, has_profile: true, email: null, department, job_title: 'Dispatcher', capacity_hours: 40, timezone: 'Asia/Dubai', assignable: true,
});

const setup = { items: [
  { id: 'email_gmail', group: 'email', status: 'not_configured' as const, connect_view: null, connections: 0, failing: 0, missing: [], redirect_urls: [], doc: 'g', checkable: false },
  { id: 'email_outlook', group: 'email', status: 'connected' as const, connect_view: null, connections: 1, failing: 0, missing: [], redirect_urls: [], doc: 'o', checkable: false },
  { id: 'whatsapp', group: 'messaging', status: 'error' as const, connect_view: null, connections: 1, failing: 1, missing: [], redirect_urls: [], doc: 'w', checkable: false },
  { id: 'fleet_data', group: 'operations', status: 'ready' as const, connect_view: null, connections: 0, failing: 0, missing: [], redirect_urls: [], doc: 'f', checkable: false },
] };

const aliases = departmentAliases([CATALOGS.en, CATALOGS.ar] as unknown as Record<string, unknown>[]);

describe('tools to integrations', () => {
  test('one chip per connection, with the best status of a family', () => {
    const { chips, builtin } = toolChips(['send_email', 'read_email', 'send_whatsapp', 'tyres_due', 'anum.respond', 'team.assign_task', 'calendar.create'], setup);
    assert.equal(builtin, 2);
    const byKey = Object.fromEntries(chips.map((c) => [c.key, c]));
    assert.deepEqual(byKey['group:email'].tools, ['read_email', 'send_email']);
    assert.equal(byKey['group:email'].status, 'connected');
    assert.equal(byKey.whatsapp.status, 'error');
    assert.equal(byKey.fleet_data.status, 'ready');
    assert.deepEqual(byKey['raw:calendar'].label, { kind: 'raw', id: 'calendar' });
    assert.equal(byKey['raw:calendar'].status, 'unknown');
  });
  test('without connection setup every chip is unknown', () => {
    assert.equal(toolChips(['send_email'], null).chips[0].status, 'unknown');
    assert.equal(familyStatus([]), 'unknown');
    assert.equal(familyStatus([{ status: 'error' }, { status: 'not_configured' }]), 'error');
  });
});

describe('departments', () => {
  test('free text joins a known department in either language', () => {
    assert.deepEqual(departmentKey('  sales ', aliases), { key: 'sales', known: 'sales', name: null });
    assert.equal(departmentKey('المبيعات', aliases).key, 'sales');
    assert.equal(departmentKey('Customer Care', aliases).key, 'support');
    assert.deepEqual(departmentKey('Marketing & Content', aliases), { key: 'text:marketing & content', known: null, name: 'Marketing & Content' });
    assert.equal(departmentKey(null, aliases).key, 'none');
  });
});

describe('status', () => {
  test('approvals come first, then errors, then running', () => {
    assert.equal(memberStatus({ paused: false, running: 2, approvals: 1, failed: 1, unavailable: 0 }), 'approval');
    assert.equal(memberStatus({ paused: false, running: 2, approvals: 0, failed: 0, unavailable: 1 }), 'error');
    assert.equal(memberStatus({ paused: false, running: 2, approvals: 0, failed: 0, unavailable: 0 }), 'running');
    assert.equal(memberStatus({ paused: true, running: 0, approvals: 0, failed: 0, unavailable: 0 }), 'paused');
    assert.equal(memberStatus({ paused: false, off: true, running: 0, approvals: 0, failed: 0, unavailable: 0 }), 'off');
    assert.equal(memberStatus({ paused: false, running: 0, approvals: 0, failed: 0, unavailable: 0 }), 'idle');
  });
});

describe('buildOrgChart', () => {
  const input: OrgInputs = {
    agents: [
      agent('a_sales', { name: 'Outreach', department: 'Sales', allowed_tools: ['send_email'], allowed_skills: ['anum.email'] }),
      agent('a_fleet', { name: 'Fleet desk', department: 'Operations and warehouse', allowed_tools: ['tyres_due'] }),
      agent('a_mkt', { name: 'Posts', department: 'Marketing & Content', status: 'paused' }),
      agent('a_none', { name: 'Loose', unavailable_tools: ['old.tool'] }),
    ],
    assistants: [assistant('s1', 'sales', { name: 'Sales desk', enabled: false })],
    people: [person('p1', 'operations'), person('p2', null)],
    tasks: [
      { id: 't1', status: 'running', agent_id: 'a_fleet' },
      { id: 't2', status: 'waiting_approval', agent_id: 'a_sales' },
      { id: 't3', status: 'queued', agent_id: null },
      { id: 't4', status: 'failed', agent_id: 'a_fleet' },
      { id: 't5', status: 'waiting_approval', agent_id: null },
    ],
    approvals: [
      { id: 'ap1', task_id: 't2', status: 'pending' }, { id: 'ap2', task_id: 't5', status: 'pending' }, { id: 'ap3', task_id: 't2', status: 'approved' },
    ],
    setup,
    skills: [{ id: 'anum.email', name: 'Email', required_tools: ['read_email', 'send_email'] }],
    aliases,
  };
  const chart = buildOrgChart(input);
  test('groups members into departments in a fixed order, free text after, no department last', () => {
    assert.deepEqual(chart.departments.map((d) => d.key), ['sales', 'operations', 'text:marketing & content', 'none']);
    assert.deepEqual(chart.departments[0].members.map((m) => m.name), ['Outreach', 'Sales desk']);
    assert.deepEqual(chart.departments[1].members.map((m) => m.kind), ['agent', 'person']);
    assert.equal(chart.members, 5, 'agents and assistants; people are not counted');
  });
  test('counts work per agent and leaves the rest to ANUM', () => {
    const sales = chart.departments[0].members[0];
    assert.equal(sales.status, 'approval');
    assert.equal(sales.approvals, 1);
    assert.deepEqual(sales.skills, [{ id: 'anum.email', name: 'Email' }]);
    assert.deepEqual(sales.tools[0].tools, ['read_email', 'send_email'], 'tools its skills need count too');
    const fleet = chart.departments[1].members[0];
    assert.deepEqual([fleet.status, fleet.running, fleet.failed], ['error', 1, 1]);
    assert.equal(fleet.department.key, 'operations');
    assert.equal(chart.running, 2);
    assert.equal(chart.anumRunning, 1);
    assert.equal(chart.approvals, 2);
    assert.equal(chart.anumApprovals, 1);
    assert.equal(chart.departments[0].members[1].status, 'off');
    assert.equal(chart.departments[3].members[0].status, 'error');
    assert.equal(chart.attention, 3);
  });
  test('approvals the caller may not read stay unknown', () => {
    assert.equal(buildOrgChart({ ...input, approvals: null }).approvals, null);
  });
  test('the table filters by text, department, status, tool and kind', () => {
    assert.deepEqual(filterMembers(chart, { ...NO_FILTER, q: 'email' }).map((m) => m.id), ['a_sales']);
    assert.deepEqual(filterMembers(chart, { ...NO_FILTER, department: 'operations' }).map((m) => m.id), ['a_fleet', 'p1']);
    assert.deepEqual(filterMembers(chart, { ...NO_FILTER, status: 'error' }).map((m) => m.id), ['a_fleet', 'a_none']);
    assert.deepEqual(filterMembers(chart, { ...NO_FILTER, tool: 'fleet_data' }).map((m) => m.id), ['a_fleet']);
    assert.deepEqual(filterMembers(chart, { ...NO_FILTER, kind: 'person', q: 'dispatcher' }).map((m) => m.id), ['p1']);
    assert.deepEqual(toolOptions(chart).map((c) => c.key), ['fleet_data', 'group:email']);
  });
  test('large departments collapse to the first few', () => {
    const many = { members: Array.from({ length: 9 }, (_, i) => chart.departments[0].members[0] && { ...chart.departments[0].members[0], id: `m${i}` }) };
    assert.equal(visibleMembers(many, false).shown.length, 4);
    assert.equal(visibleMembers(many, false).hidden, 5);
    assert.equal(visibleMembers(many, true).hidden, 0);
  });
});

describe('autonomy rules', () => {
  test('never looser than the workspace floor, with the tools in each category', () => {
    const rules = autonomyRules(agent('x', { allowed_tools: ['send_email', 'read_email'], autonomy: { read: 'auto', write: 'auto', external: 'ask' } }), {
      tools: [
        { name: 'read_email', description: '', risk_level: 'low', category: 'read', always_on: false, available_to_you: true, reason: null },
        { name: 'send_email', description: '', risk_level: 'high', category: 'external', always_on: false, available_to_you: true, reason: null },
      ],
      autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' },
    });
    assert.deepEqual(rules.map((r) => [r.category, r.level, r.floored, r.tools]), [
      ['read', 'auto', false, ['read_email']], ['write', 'ask', true, []], ['external', 'ask', false, ['send_email']],
    ]);
  });
});

describe('loadOrgSources', () => {
  test('pages agents, reads the rest softly and reports what failed', async () => {
    const calls: string[] = [];
    const request = async <T>(path: string): Promise<T> => {
      calls.push(path);
      if (path.startsWith('/api/v1/agents?')) {
        return (path.includes('cursor=') ? { items: [agent('b')], next_cursor: null, counts: {} } : { items: [agent('a')], next_cursor: 'a', counts: {} }) as T;
      }
      if (path === '/api/v1/approvals') throw new ApiError(403, 'forbidden', 'Only approvers can see this.');
      if (path === '/api/v1/integrations/setup') return setup as T;
      if (path === '/api/v1/agents/catalog') return { tools: [], skills: [{ id: 's', name: 'S', description: '', source: 'builtin', required_tools: [] }], templates: [], icons: [], autonomy_floor: { read: 'auto', write: 'ask', external: 'ask' } } as T;
      if (path.startsWith('/api/v1/tasks')) return (path.includes('failed') ? [{ id: 't1', status: 'failed', agent_id: 'a' }] : [{ id: 't1', status: 'failed', agent_id: 'a' }, { id: 't2', status: 'running', agent_id: 'b' }]) as T;
      if (path === '/api/v1/team/people') throw new Error('offline');
      return [] as T;
    };
    const sources = await loadOrgSources(request, Date.parse('2026-10-12T12:00:00Z'));
    assert.deepEqual(sources.agents.map((a) => a.id), ['a', 'b']);
    assert.equal(sources.tasks.length, 2, 'tasks in both lists count once');
    assert.equal(sources.approvals, null);
    assert.equal(sources.people, null);
    assert.equal(sources.skills[0].name, 'S');
    assert.deepEqual(sources.partial.map((p) => p.source).sort(), ['approvals', 'people']);
    assert.ok(calls.includes(openTasksPath()));
    assert.ok(calls.some((c) => c.includes('created_after=2026-10-11T12%3A00%3A00.000Z')));
  });
  test('an agents failure is the caller’s error', async () => {
    await assert.rejects(loadOrgSources(async () => { throw new ApiError(403, 'forbidden', 'No.'); }), (e: unknown) => e instanceof ApiError && e.status === 403);
  });
  test('paths', () => {
    assert.equal(openTasksPath(), '/api/v1/tasks?limit=100&status=created&status=queued&status=running&status=waiting_approval');
    assert.match(failedTasksPath(0), /status=failed/);
    assert.equal(agentTasksPath('a 1', 't9', 10), '/api/v1/tasks?agent_id=a+1&limit=10&cursor=t9');
  });
});

describe('exported image', () => {
  const model: ImageModel = {
    title: 'Meet my AI team', subtitle: 'Fleet & Co · 12 October 2026', owner: { name: 'Sara <owner>', role: 'Owner', initials: 'S' },
    anum: { name: 'ANUM', role: 'Chief of Staff, one center', stats: '3 agents' },
    departments: [
      { name: 'Sales', stats: '1 agent', members: [{ name: 'Outreach', role: 'Writes to leads', status: 'approval', statusLabel: 'Needs approval', counts: '1 waiting', skills: 'Skills: Email', tools: 'Tools: Email (Connected)' }] },
      { name: 'Ops', stats: '2 agents', members: Array.from({ length: 12 }, (_, i) => ({ name: `Agent ${i}`, role: '', status: 'idle' as const, statusLabel: 'Idle', counts: '', skills: '', tools: '' })) },
    ],
    strip: 'You approve risky actions.', footer: 'Made with ANUM', rtl: false,
  };
  test('draws every member and escapes text', () => {
    const { svg, width, height } = orgChartSvg(model);
    assert.ok(svg.startsWith('<svg xmlns="http://www.w3.org/2000/svg"'));
    assert.ok(svg.includes('Sara &lt;owner&gt;'));
    assert.ok(svg.includes('>Agent 11<'), 'no member is cut');
    assert.ok(svg.includes('Tools: Email (Connected)'));
    assert.equal(width, 960);
    assert.ok(height > 12 * 44);
    assert.ok(!svg.includes('href'), 'nothing loads from elsewhere');
  });
  test('Arabic text runs right to left', () => {
    assert.ok(orgChartSvg({ ...model, rtl: true }).svg.includes('direction="rtl"'));
  });
  test('helpers', () => {
    assert.equal(escapeXml(`a&b"'`), 'a&amp;b&quot;&apos;');
    assert.equal(fit('x'.repeat(100), 56, 10), `${'x'.repeat(8)}…`);
    assert.equal(fit('short', 300, 12), 'short');
    assert.deepEqual(wrap('one two three four five', 45, 10), ['one two', 'three', 'four', 'five']);
    assert.deepEqual(wrap('a b c d e f g h', 29, 10, 2), ['a b c', 'd e…']);
    assert.deepEqual(wrap('ab cd ef', 40, 10, 1), ['ab cd…']);
    assert.deepEqual([1, 2, 3, 4, 7].map(columnsFor), [1, 2, 3, 4, 4]);
    assert.equal(memberHeight(model.departments[0].members[0]), 44 + 4 * 17);
    assert.equal(imageFileName(new Date('2026-10-12T08:00:00Z')), 'anum-ai-team-2026-10-12.png');
  });
});
