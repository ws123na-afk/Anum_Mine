// The AI team org chart (docs/org-chart.md): the owner, ANUM as chief of staff, the departments
// and their agents, each with live work counts and the tools they use. Everything here is pure
// (no DOM, no fetch of its own) so `pnpm --filter @anum/web test:unit` covers grouping, status,
// tool-to-integration mapping and the loader's partial failures without a browser.
import type { AgentAutonomyLevel, AgentCatalog, AgentCatalogSkill, AgentProfile, AgentToolCategory, AssistantProfile, Department, TeamPerson } from '@anum/contracts';
import { effectiveLevel } from './agents.ts';
import type { ApiApproval, ApiTask } from './api.ts';
import type { IntegrationSetup, SetupItem, SetupStatus } from './integrationSetup.ts';

/** A member's state, most urgent first when several apply. */
export type MemberStatus = 'approval' | 'error' | 'running' | 'paused' | 'off' | 'idle';
/** A tool chip's connection state: an integration's status, built in, or not known here. */
export type ToolStatus = SetupStatus | 'builtin' | 'unknown';

export interface ToolChip {
  /** `group:<group>` for a family of setup items (email, marketing, meetings) or a setup item id. */
  key: string;
  /** How to name it: a setup group, a setup item, or a raw tool prefix this client does not know. */
  label: { kind: 'group' | 'item' | 'raw'; id: string };
  status: ToolStatus;
  /** The agent's tools behind this chip, sorted. */
  tools: string[];
}

export interface OrgMember {
  /** An agent profile, a department assistant, or a person from the Team directory. */
  kind: 'agent' | 'assistant' | 'person';
  id: string;
  name: string;
  /** Free text: the agent's description; empty when it has none. */
  description: string;
  icon: string;
  color: string;
  status: MemberStatus;
  running: number;
  approvals: number;
  failed: number;
  /** Tools or skills this deployment no longer provides. */
  unavailable: number;
  tools: ToolChip[];
  /** Internal tools (text answers, team assignment) not tied to an integration. */
  builtin: number;
  /** Skills the agent loads, by name (built-in and installed), in the order it lists them. */
  skills: { id: string; name: string }[];
  /** Where it sits; the same for every member of a department. */
  department: { key: string; known: Department | null; name: string | null };
  /** People only: job title and workspace role. */
  jobTitle?: string | null;
  role?: string;
  /** The records behind the node, for the detail drawer and the printed profiles. */
  agent?: AgentProfile;
  assistant?: AssistantProfile;
  person?: TeamPerson;
}

export interface OrgDepartment {
  /** A known department (`sales`, `support`, ...), `text:<lowercased name>` for a free-text one, or `none`. */
  key: string;
  /** A known department's id for its translated name, else null. */
  known: Department | null;
  /** The name as typed on the agents (free-text departments only). */
  name: string | null;
  members: OrgMember[];
  running: number;
  approvals: number;
  attention: number;
}

export interface OrgChart {
  departments: OrgDepartment[];
  /** Agents and assistants (people are not counted). */
  members: number;
  /** Open tasks in the workspace: running or queued, with or without an agent. */
  running: number;
  /** Pending approvals the caller can see (null when they may not read approvals). */
  approvals: number | null;
  /** Running tasks and approvals no agent owns: ANUM's own work. */
  anumRunning: number;
  anumApprovals: number;
  /** Members that need a person: approvals, errors or missing tools. */
  attention: number;
}

export interface OrgInputs {
  agents: readonly AgentProfile[];
  assistants: readonly AssistantProfile[] | null;
  /** Open tasks (created, queued, running, waiting for approval) and recently failed ones. */
  tasks: readonly Pick<ApiTask, 'id' | 'status' | 'agent_id'>[];
  /** Approvals the caller can see; only `pending` ones count. Null: not allowed to read them. */
  approvals: readonly Pick<ApiApproval, 'id' | 'task_id' | 'status'>[] | null;
  /** Connection setup, or null when it could not be read. */
  setup: Pick<IntegrationSetup, 'items'> | null;
  /** Built-in skills' tools, so an agent's skills count toward its tools. */
  skills?: readonly Pick<AgentCatalogSkill, 'id' | 'name' | 'required_tools'>[];
  /** People from the Team directory; null when the caller may not read it. */
  people?: readonly TeamPerson[] | null;
  /** Department names in every interface language, so "Sales" or "المبيعات" on an agent joins the Sales assistants. */
  aliases?: Readonly<Record<string, Department>>;
}

/** Departments in the order the chart shows them; free-text ones follow alphabetically, then "no department". */
export const DEPARTMENT_ORDER: readonly Department[] = [
  'sales', 'support', 'operations', 'tyre_engineering', 'finance',
  'marketing', 'technology', 'research', 'legal', 'compliance', 'security', 'hr', 'procurement', 'executive',
  'custom',
];
/** How many members a department shows before "Show all". */
export const COLLAPSED_MEMBERS = 4;
export const OPEN_STATUSES = ['created', 'queued', 'running', 'waiting_approval'] as const;
export const RUNNING_STATUSES: ReadonlySet<string> = new Set(['created', 'queued', 'running']);
/** Failed tasks this recent mark the agent as needing a look. */
export const FAILED_WINDOW_MS = 24 * 60 * 60 * 1000;
/** The chart refreshes this often while the page is visible. */
export const REFRESH_MS = 30_000;

// ---------------------------------------------------------------- tools to integrations

const EMAIL_TOOLS = new Set(['search_email', 'read_email', 'mark_email_read', 'archive_email', 'unarchive_email', 'draft_reply', 'send_email']);
const TYRE_TOOLS = new Set(['tyre_fleet_summary', 'tyres_due', 'purchase_plan', 'tyre_alerts']);
/** Tools that run inside ANUM and need no outside connection. */
export const BUILTIN_TOOLS: ReadonlySet<string> = new Set(['anum.respond', 'team.assign_task']);

interface IntegrationRule { key: string; label: ToolChip['label']; items: (setup: readonly SetupItem[]) => SetupItem[]; match: (tool: string) => boolean }

const byGroup = (group: string) => (items: readonly SetupItem[]) => items.filter((item) => item.group === group);
const byId = (...ids: string[]) => (items: readonly SetupItem[]) => items.filter((item) => ids.includes(item.id));

/** Which connection each tool family needs (docs/org-chart.md#tools). The first rule that matches wins. */
export const INTEGRATION_RULES: readonly IntegrationRule[] = [
  { key: 'group:email', label: { kind: 'group', id: 'email' }, items: byGroup('email'), match: (tool) => EMAIL_TOOLS.has(tool) || tool.startsWith('email.') },
  { key: 'whatsapp', label: { kind: 'item', id: 'whatsapp' }, items: byId('whatsapp'), match: (tool) => tool.includes('whatsapp') },
  { key: 'commerce', label: { kind: 'item', id: 'commerce' }, items: byId('commerce'), match: (tool) => tool.startsWith('commerce.') },
  { key: 'group:marketing', label: { kind: 'group', id: 'marketing' }, items: byGroup('marketing'), match: (tool) => tool.startsWith('marketing.') },
  { key: 'fleet_data', label: { kind: 'item', id: 'fleet_data' }, items: byId('fleet_data'), match: (tool) => TYRE_TOOLS.has(tool) || /^(tyre|fleet|vehicle|maintenance)[._]/.test(tool) },
  { key: 'group:meetings', label: { kind: 'group', id: 'meetings' }, items: byGroup('meetings'), match: (tool) => /^meetings?[._]/.test(tool) },
  { key: 'developer', label: { kind: 'item', id: 'developer' }, items: byId('developer'), match: (tool) => tool.startsWith('developer.') },
  { key: 'web_search', label: { kind: 'item', id: 'web_search' }, items: byId('web_search'), match: (tool) => tool === 'web_search' || tool.startsWith('web.') },
  { key: 'external_webhook', label: { kind: 'item', id: 'external_webhook' }, items: byId('external_webhook'), match: (tool) => tool === 'external.action' },
  { key: 'files', label: { kind: 'item', id: 'files' }, items: byId('files'), match: (tool) => tool.startsWith('files.') },
];

const STATUS_RANK: Record<SetupStatus, number> = { connected: 3, ready: 2, error: 1, not_configured: 0 };

/** A family's status: connected when any of its items is; otherwise the most useful of the rest. */
export function familyStatus(items: readonly Pick<SetupItem, 'status'>[]): SetupStatus | 'unknown' {
  if (items.length === 0) return 'unknown';
  return items.reduce<SetupStatus>((best, item) => STATUS_RANK[item.status] > STATUS_RANK[best] ? item.status : best, 'not_configured');
}

/** An agent's tools (its own plus those its skills need) as one chip per connection. */
export function toolChips(tools: readonly string[], setup: OrgInputs['setup']): { chips: ToolChip[]; builtin: number } {
  const chips = new Map<string, ToolChip>();
  let builtin = 0;
  for (const tool of [...new Set(tools)].sort()) {
    if (BUILTIN_TOOLS.has(tool)) { builtin += 1; continue; }
    const rule = INTEGRATION_RULES.find((r) => r.match(tool));
    const key = rule?.key ?? `raw:${tool.split(/[._]/)[0]}`;
    const chip = chips.get(key);
    if (chip) { chip.tools.push(tool); continue; }
    const status: ToolStatus = !rule ? 'unknown' : setup ? familyStatus(rule.items(setup.items)) : 'unknown';
    chips.set(key, { key, label: rule?.label ?? { kind: 'raw', id: tool.split(/[._]/)[0] }, status, tools: [tool] });
  }
  return { chips: [...chips.values()], builtin };
}

// ---------------------------------------------------------------- departments

const normal = (text: string) => text.normalize('NFKC').trim().replace(/\s+/g, ' ').toLocaleLowerCase();

/** The department an agent's free-text department belongs to. */
export function departmentKey(department: string | null | undefined, aliases: OrgInputs['aliases'] = {}): { key: string; known: Department | null; name: string | null } {
  const text = (department ?? '').trim();
  if (!text) return { key: 'none', known: null, name: null };
  const value = normal(text);
  for (const [alias, known] of Object.entries(aliases)) if (normal(alias) === value) return { key: known, known, name: null };
  if ((DEPARTMENT_ORDER as readonly string[]).includes(value)) return { key: value, known: value as Department, name: null };
  return { key: `text:${value}`, known: null, name: text };
}

const STATUS_ORDER: Record<MemberStatus, number> = { approval: 0, error: 1, running: 2, paused: 3, off: 4, idle: 5 };

export function memberStatus(input: { paused: boolean; off?: boolean; running: number; approvals: number; failed: number; unavailable: number }): MemberStatus {
  if (input.approvals > 0) return 'approval';
  if (input.unavailable > 0 || input.failed > 0) return 'error';
  if (input.off) return 'off';
  if (input.running > 0) return 'running';
  if (input.paused) return 'paused';
  return 'idle';
}

const NO_DEPT: OrgMember['department'] = { key: 'none', known: null, name: null };
const KIND_ORDER: Record<OrgMember['kind'], number> = { agent: 0, assistant: 1, person: 2 };

/** Agents first (those needing a person at the top, then busy ones), then assistants, then people; by name within. */
export function compareMembers(a: OrgMember, b: OrgMember): number {
  return KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || b.running - a.running || a.name.localeCompare(b.name);
}

function departmentRank(d: OrgDepartment): number {
  if (d.known) return DEPARTMENT_ORDER.indexOf(d.known);
  return d.key === 'none' ? 1000 : 100;
}

/** The whole chart from what the APIs returned. */
export function buildOrgChart(input: OrgInputs, now: number = Date.now()): OrgChart {
  const agentOfTask = new Map<string, string | null>();
  const running = new Map<string, number>();
  const failed = new Map<string, number>();
  let anyRunning = 0, anumRunning = 0;
  for (const task of input.tasks) {
    agentOfTask.set(task.id, task.agent_id ?? null);
    if (RUNNING_STATUSES.has(task.status)) {
      anyRunning += 1;
      if (task.agent_id) running.set(task.agent_id, (running.get(task.agent_id) ?? 0) + 1); else anumRunning += 1;
    }
    if (task.status === 'failed' && task.agent_id) failed.set(task.agent_id, (failed.get(task.agent_id) ?? 0) + 1);
  }
  const pending = input.approvals?.filter((a) => a.status === 'pending') ?? null;
  const approvals = new Map<string, number>();
  let anumApprovals = 0;
  for (const approval of pending ?? []) {
    const agentId = agentOfTask.get(approval.task_id);
    if (agentId) approvals.set(agentId, (approvals.get(agentId) ?? 0) + 1); else anumApprovals += 1;
  }
  const skillTools = new Map((input.skills ?? []).map((s) => [s.id, s.required_tools]));
  const skillNames = new Map((input.skills ?? []).map((s) => [s.id, s.name]));
  const departments = new Map<string, OrgDepartment>();
  const place = (where: ReturnType<typeof departmentKey>, member: OrgMember) => {
    const dept = departments.get(where.key) ?? { key: where.key, known: where.known, name: where.name, members: [], running: 0, approvals: 0, attention: 0 };
    member.department = { key: where.key, known: where.known, name: dept.name };
    dept.members.push(member);
    dept.running += member.running;
    dept.approvals += member.approvals;
    if (member.status === 'approval' || member.status === 'error') dept.attention += 1;
    departments.set(where.key, dept);
  };
  for (const agent of input.agents) {
    // Every agent answers in text (`anum.respond`), so it is always counted, once.
    const tools = ['anum.respond', ...agent.allowed_tools, ...agent.allowed_skills.flatMap((id) => skillTools.get(id) ?? [])];
    const { chips, builtin } = toolChips(tools, input.setup);
    const counts = { running: running.get(agent.id) ?? 0, approvals: approvals.get(agent.id) ?? 0, failed: failed.get(agent.id) ?? 0, unavailable: agent.unavailable_tools.length + agent.unavailable_skills.length };
    place(departmentKey(agent.department, input.aliases), {
      kind: 'agent', id: agent.id, name: agent.name, description: agent.description, icon: agent.icon, color: agent.color,
      status: memberStatus({ paused: agent.status === 'paused', ...counts }), ...counts, tools: chips, builtin,
      skills: agent.allowed_skills.map((id) => ({ id, name: skillNames.get(id) ?? id })), department: NO_DEPT, agent,
    });
  }
  for (const assistant of input.assistants ?? []) {
    const { chips, builtin } = toolChips(assistant.allowed_tools, input.setup);
    const where = assistant.department === 'custom' ? departmentKey(assistant.name, input.aliases) : { key: assistant.department, known: assistant.department, name: null };
    place(where.key === 'none' ? { key: 'custom', known: 'custom', name: null } : where, {
      kind: 'assistant', id: assistant.id, name: assistant.name, description: '', icon: 'briefcase', color: '#475569',
      status: memberStatus({ paused: false, off: !assistant.enabled, running: 0, approvals: 0, failed: 0, unavailable: 0 }),
      running: 0, approvals: 0, failed: 0, unavailable: 0, tools: chips, builtin, skills: [], department: NO_DEPT, assistant,
    });
  }
  for (const person of input.people ?? []) {
    if (!person.department) continue;
    place({ key: person.department, known: person.department, name: null }, {
      kind: 'person', id: person.user_id, name: person.display_name, description: '', icon: 'user', color: '#64748b', status: 'idle',
      running: 0, approvals: 0, failed: 0, unavailable: 0, tools: [], builtin: 0, skills: [], department: NO_DEPT,
      jobTitle: person.job_title, role: person.role, person,
    });
  }
  const list = [...departments.values()];
  for (const dept of list) dept.members.sort(compareMembers);
  list.sort((a, b) => departmentRank(a) - departmentRank(b) || (a.name ?? '').localeCompare(b.name ?? ''));
  const members = list.reduce((sum, d) => sum + d.members.filter((m) => m.kind !== 'person').length, 0);
  return {
    departments: list, members, running: anyRunning, approvals: pending ? pending.length : null, anumRunning, anumApprovals,
    attention: list.reduce((sum, d) => sum + d.attention, 0),
  };
}

/** The members a department shows: all when expanded, else the first few. */
export function visibleMembers(dept: Pick<OrgDepartment, 'members'>, expanded: boolean, limit = COLLAPSED_MEMBERS): { shown: OrgMember[]; hidden: number } {
  if (expanded || dept.members.length <= limit) return { shown: dept.members, hidden: 0 };
  return { shown: dept.members.slice(0, limit), hidden: dept.members.length - limit };
}

// ---------------------------------------------------------------- loading

export type Requester = <T>(path: string, init: RequestInit) => Promise<T>;

export interface OrgSources {
  agents: AgentProfile[];
  assistants: AssistantProfile[] | null;
  tasks: ApiTask[];
  approvals: ApiApproval[] | null;
  setup: IntegrationSetup | null;
  skills: AgentCatalogSkill[];
  catalog: AgentCatalog | null;
  people: TeamPerson[] | null;
  policy: { two_person_rule: boolean; medium_risk_requires_approval: boolean } | null;
  /** When the rarely changing sources (catalog, setup, people, assistants, policy) were read. */
  staticAt: number;
  /** What could not be read, as API errors, for one quiet note (the chart still shows). */
  partial: { source: 'assistants' | 'tasks' | 'approvals' | 'setup' | 'catalog' | 'people' | 'policy'; error: unknown }[];
}

/** At most this many agent pages (a workspace holds at most 200 agents). */
const MAX_AGENT_PAGES = 3;
export const AGENT_CHART_PAGE = 100;

/** Open tasks are read at most this many pages of 100. */
export const MAX_OPEN_TASK_PAGES = 5;
/** Catalog, setup, people, assistants and policy are re-read after this long. */
export const STATIC_TTL_MS = 5 * 60_000;

export function openTasksPath(cursor: string | null = null): string {
  const query = new URLSearchParams({ limit: '100' });
  if (cursor) query.set('cursor', cursor);
  for (const status of OPEN_STATUSES) query.append('status', status);
  return `/api/v1/tasks?${query}`;
}

export function failedTasksPath(now: number): string {
  return `/api/v1/tasks?${new URLSearchParams({ limit: '100', status: 'failed', created_after: new Date(now - FAILED_WINDOW_MS).toISOString() })}`;
}

const isPage = (value: unknown): value is { items: AgentProfile[]; next_cursor: string | null } =>
  Boolean(value) && typeof value === 'object' && Array.isArray((value as { items?: unknown }).items);

/**
 * Everything the chart reads. The agents list is required (its failure, including 403, is the
 * caller's error state); every other source fails soft and is listed in `partial`.
 */
export async function loadOrgSources(request: Requester, now: number = Date.now(), previous: OrgSources | null = null): Promise<OrgSources> {
  const agents: AgentProfile[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_AGENT_PAGES; page += 1) {
    const query = new URLSearchParams({ limit: String(AGENT_CHART_PAGE) });
    if (cursor) query.set('cursor', cursor);
    const result: unknown = await request(`/api/v1/agents?${query}`, { method: 'GET' });
    if (!isPage(result)) break;
    agents.push(...result.items.filter((a) => !agents.some((b) => b.id === a.id)));
    cursor = result.next_cursor;
    if (!cursor) break;
  }
  const partial: OrgSources['partial'] = [];
  const soft = async <T>(source: OrgSources['partial'][number]['source'], run: () => Promise<T>): Promise<T | null> => {
    try { return await run(); } catch (error) { partial.push({ source, error }); return null; }
  };
  const list = <T>(value: unknown): T[] | null => Array.isArray(value) ? value as T[] : null;
  // Sources that rarely change are reused from the last load for a few minutes (live refreshes
  // then read only agents, tasks and approvals).
  const reuse = previous && now - previous.staticAt < STATIC_TTL_MS && !previous.partial.length ? previous : null;
  const fixed = <T>(value: T | undefined, run: () => Promise<T | null>): Promise<T | null> => reuse ? Promise.resolve(value ?? null) : run();
  const [assistants, open, failed, approvals, setup, catalog, people, policy] = await Promise.all([
    fixed(reuse?.assistants ?? undefined, () => soft('assistants', async () => list<AssistantProfile>(await request('/api/v1/assistants', { method: 'GET' })))),
    soft('tasks', async () => {
      // Open tasks page by page (newest first), so counts and approval owners stay right in busy workspaces.
      const rows: ApiTask[] = [];
      let after: string | null = null;
      for (let page = 0; page < MAX_OPEN_TASK_PAGES; page += 1) {
        const batch: ApiTask[] = list<ApiTask>(await request(openTasksPath(after), { method: 'GET' })) ?? [];
        rows.push(...batch);
        if (batch.length < 100) break;
        after = batch[batch.length - 1].id;
      }
      return rows;
    }),
    soft('tasks', async () => list<ApiTask>(await request(failedTasksPath(now), { method: 'GET' })) ?? []),
    soft('approvals', async () => list<ApiApproval>(await request('/api/v1/approvals', { method: 'GET' }))),
    fixed(reuse?.setup ?? undefined, () => soft('setup', async () => {
      const value = await request<IntegrationSetup>('/api/v1/integrations/setup', { method: 'GET' });
      return value && Array.isArray(value.items) ? value : null;
    })),
    fixed<AgentCatalog>(reuse?.catalog ?? undefined, () => soft('catalog', async () => request<AgentCatalog>('/api/v1/agents/catalog', { method: 'GET' }))),
    fixed(reuse?.people ?? undefined, () => soft('people', async () => list<TeamPerson>(await request('/api/v1/team/people', { method: 'GET' })))),
    fixed(reuse?.policy ?? undefined, () => soft('policy', async () => request<OrgSources['policy']>('/api/v1/approval-policy', { method: 'GET' }))),
  ]);
  const tasks = new Map<string, ApiTask>();
  for (const task of [...(open ?? []), ...(failed ?? [])]) tasks.set(task.id, task);
  return {
    agents, assistants, tasks: [...tasks.values()], approvals, setup,
    skills: Array.isArray(catalog?.skills) ? catalog.skills : [], catalog: catalog && Array.isArray(catalog.tools) ? catalog : null,
    people, policy: policy && typeof policy === 'object' ? policy : null, partial, staticAt: reuse ? reuse.staticAt : now,
  };
}

/** Department names in each catalog, keyed by name, for {@link departmentKey}. */
export function departmentAliases(catalogs: readonly Record<string, unknown>[]): Record<string, Department> {
  const aliases: Record<string, Department> = {};
  for (const catalog of catalogs) {
    for (const dept of DEPARTMENT_ORDER) {
      if (dept === 'custom') continue;
      const name = catalog[`team.dept.${dept}`];
      if (typeof name === 'string') aliases[name] = dept;
    }
  }
  // Common names people type for these departments (docs/org-chart.md#departments).
  Object.assign(aliases, { 'customer care': 'support', 'customer support': 'support', operations: 'operations', ops: 'operations', 'finance & approvals': 'finance', 'finance and approvals': 'finance', tyres: 'tyre_engineering', 'tyre engineering': 'tyre_engineering', 'sales & outreach': 'sales', 'sales and outreach': 'sales' });
  return aliases;
}

// ---------------------------------------------------------------- detail, table and filters

export interface AutonomyRule { category: AgentToolCategory; level: AgentAutonomyLevel; floored: boolean; tools: string[] }

/**
 * What the agent may do alone and what waits for a person, per tool category: its own level,
 * never looser than the workspace floor, with the agent's tools in each category.
 */
export function autonomyRules(agent: Pick<AgentProfile, 'autonomy' | 'allowed_tools'>, catalog: Pick<AgentCatalog, 'tools' | 'autonomy_floor'> | null): AutonomyRule[] {
  const categories: AgentToolCategory[] = ['read', 'write', 'external'];
  const byName = new Map((catalog?.tools ?? []).map((tool) => [tool.name, tool]));
  return categories.map((category) => {
    const own = agent.autonomy[category];
    const level = catalog ? effectiveLevel(own, catalog.autonomy_floor[category]) : own;
    const tools = agent.allowed_tools.filter((name) => byName.get(name)?.category === category).sort();
    return { category, level, floored: level !== own, tools };
  });
}

export type StatusFilter = 'all' | MemberStatus;
export interface MemberFilter { q: string; department: string; status: StatusFilter; tool: string; kind: 'all' | OrgMember['kind'] }
export const NO_FILTER: MemberFilter = { q: '', department: 'all', status: 'all', tool: 'all', kind: 'all' };

/** Every member, flat, filtered for the table view. People never match a status or tool filter. */
export function filterMembers(chart: Pick<OrgChart, 'departments'>, filter: MemberFilter): OrgMember[] {
  const q = normal(filter.q);
  return chart.departments.flatMap((d) => d.members).filter((m) => {
    if (filter.kind !== 'all' && m.kind !== filter.kind) return false;
    if (filter.department !== 'all' && m.department.key !== filter.department) return false;
    if (filter.status !== 'all' && (m.kind === 'person' || m.status !== filter.status)) return false;
    if (filter.tool !== 'all' && !m.tools.some((chip) => chip.key === filter.tool)) return false;
    if (!q) return true;
    const haystack = [m.name, m.description, m.jobTitle ?? '', m.department.name ?? '', ...m.skills.map((s) => s.name), ...m.tools.flatMap((c) => c.tools)].join(' ');
    return normal(haystack).includes(q);
  });
}

/** The tool families any member uses, for the table's tool filter. */
export function toolOptions(chart: Pick<OrgChart, 'departments'>): ToolChip[] {
  const seen = new Map<string, ToolChip>();
  for (const d of chart.departments) for (const m of d.members) for (const chip of m.tools) if (!seen.has(chip.key)) seen.set(chip.key, chip);
  return [...seen.values()].sort((a, b) => a.key.localeCompare(b.key));
}

/** `GET /api/v1/tasks?agent_id=` one page at a time, newest first, for the drawer. */
export function agentTasksPath(agentId: string, cursor: string | null = null, limit = 10): string {
  const query = new URLSearchParams({ agent_id: agentId, limit: String(limit) });
  if (cursor) query.set('cursor', cursor);
  return `/api/v1/tasks?${query}`;
}

// ---------------------------------------------------------------- header KPIs and department health

export interface OrgKpis { agents: number; running: number; approvals: number | null; failedToday: number; toolsNotConnected: number; withoutAgent: number }

/** The header strip: one number each, from the same data as the chart. */
export function orgKpis(chart: OrgChart): OrgKpis {
  const members = chart.departments.flatMap((d) => d.members);
  const notConnected = new Set<string>();
  for (const m of members) for (const chip of m.tools) if (chip.status === 'not_configured' || chip.status === 'error' || chip.status === 'ready') notConnected.add(chip.key);
  return {
    agents: members.filter((m) => m.kind === 'agent').length,
    running: chart.running,
    approvals: chart.approvals,
    failedToday: members.reduce((sum, m) => sum + m.failed, 0),
    toolsNotConnected: notConnected.size,
    withoutAgent: chart.anumRunning + chart.anumApprovals,
  };
}

export type DepartmentHealth = { tone: 'ok' | 'warn' | 'stop'; approvals: number; attention: number };

/** Healthy, waiting for you, or needing attention (failed work or missing tools). */
export function departmentHealth(dept: Pick<OrgDepartment, 'members'>): DepartmentHealth {
  const approvals = dept.members.reduce((sum, m) => sum + m.approvals, 0);
  const attention = dept.members.filter((m) => m.status === 'error').length;
  return { tone: attention ? 'stop' : approvals ? 'warn' : 'ok', approvals, attention };
}

/** Who leads a department: its first enabled assistant, else its first person, else nobody. */
export function departmentLead(dept: Pick<OrgDepartment, 'members'>): OrgMember | null {
  return dept.members.find((m) => m.kind === 'assistant' && m.status !== 'off')
    ?? dept.members.find((m) => m.kind === 'assistant')
    ?? dept.members.find((m) => m.kind === 'person') ?? null;
}

export function departmentCounts(dept: Pick<OrgDepartment, 'members'>): { agents: number; assistants: number; people: number } {
  const count = (kind: OrgMember['kind']) => dept.members.filter((m) => m.kind === kind).length;
  return { agents: count('agent'), assistants: count('assistant'), people: count('person') };
}

/** How tall a department draws, in agent cards (its header and the compact people rows are smaller). */
export function departmentWeight(dept: Pick<OrgDepartment, 'members'>, limit = COLLAPSED_MEMBERS): number {
  const agents = dept.members.filter((m) => m.kind === 'agent').length;
  const compact = dept.members.length - agents;
  return 0.75 + Math.min(agents, limit) + (agents > limit ? 0.3 : 0) + compact * 0.3;
}

/**
 * Departments in `columns` stacks of similar height: the tallest are placed first, each on the
 * shortest stack, then every stack keeps the chart's department order top to bottom.
 */
export function balanceColumns<T extends Pick<OrgDepartment, 'members'>>(depts: readonly T[], columns: number): T[][] {
  const n = Math.max(1, Math.min(columns, depts.length || 1));
  const stacks: { dept: T; at: number }[][] = Array.from({ length: n }, () => []);
  const heights = Array.from({ length: n }, () => 0);
  const order = depts.map((dept, at) => ({ dept, at, weight: departmentWeight(dept) })).sort((a, b) => b.weight - a.weight || a.at - b.at);
  for (const item of order) {
    const column = heights.indexOf(Math.min(...heights));
    stacks[column].push(item);
    heights[column] += item.weight + 0.15;
  }
  // The column holding the first department comes first, so Sales stays top left.
  return stacks.map((stack) => stack.sort((a, b) => a.at - b.at))
    .sort((a, b) => (a[0]?.at ?? Infinity) - (b[0]?.at ?? Infinity)).map((stack) => stack.map((item) => item.dept));
}

/** Columns that fit a width: cards stay at least ~320px wide, never more than four. */
export function columnsForWidth(width: number): number {
  return Math.max(1, Math.min(4, Math.floor((width + 16) / 336)));
}

// ---------------------------------------------------------------- profile page

export type ProfileKind = OrgMember['kind'];
export interface ProfileRef { kind: ProfileKind; id: string }
export const PROFILE_TABS = {
  agent: ['overview', 'work', 'skills', 'approvals', 'activity', 'performance'],
  assistant: ['overview', 'work', 'skills'],
  person: ['overview', 'work'],
} as const;
export type ProfileTab = (typeof PROFILE_TABS)[ProfileKind][number];

/** `#agents/team/agent/<id>` and friends: a profile anyone can link to. */
export function profileHash(ref: ProfileRef, tab?: ProfileTab): string {
  return `#agents/team/${ref.kind}/${encodeURIComponent(ref.id)}${tab && tab !== 'overview' ? `/${tab}` : ''}`;
}

export function parseProfileHash(hash: string): (ProfileRef & { tab: ProfileTab }) | null {
  const match = /^#?agents\/team\/(agent|assistant|person)\/([^/]+)(?:\/([a-z]+))?$/.exec(hash);
  if (!match) return null;
  const kind = match[1] as ProfileKind;
  let id: string;
  try { id = decodeURIComponent(match[2]); } catch { return null; }
  const tabs = PROFILE_TABS[kind] as readonly string[];
  const tab = (match[3] && tabs.includes(match[3]) ? match[3] : 'overview') as ProfileTab;
  return { kind, id, tab };
}

/** `#agents/team`: the org chart tab itself. */
export const TEAM_HASH = '#agents/team';

// ---------------------------------------------------------------- performance

export interface TaskStats { total: number; completed: number; failed: number; cancelled: number; open: number; successRate: number | null }

/** Counts from `GET /api/v1/tasks/summary`; success rate is completed of finished (completed or failed). */
export function taskStats(byStatus: Readonly<Record<string, number>>): TaskStats {
  const get = (key: string) => Number(byStatus[key] ?? 0);
  const completed = get('completed'), failed = get('failed'), cancelled = get('cancelled');
  const open = get('created') + get('queued') + get('running') + get('waiting_approval');
  const finished = completed + failed;
  return { total: completed + failed + cancelled + open, completed, failed, cancelled, open, successRate: finished ? completed / finished : null };
}

/**
 * Average time from a task's creation to its last update, for finished tasks: the API stores no
 * separate start and end, so this is an upper bound of the run time (docs/org-chart.md#performance).
 */
export function averageDurationMs(tasks: readonly Pick<ApiTask, 'status' | 'created_at' | 'updated_at'>[]): number | null {
  const spans = tasks.filter((t) => t.status === 'completed')
    .map((t) => Date.parse(t.updated_at) - Date.parse(t.created_at)).filter((ms) => Number.isFinite(ms) && ms >= 0);
  return spans.length ? spans.reduce((a, b) => a + b, 0) / spans.length : null;
}

export function summaryPath(agentId: string, days: number, now: number): string {
  return `/api/v1/tasks/summary?${new URLSearchParams({ agent_id: agentId, created_after: new Date(now - days * 86_400_000).toISOString() })}`;
}

export function agentTasksByStatusPath(agentId: string, statuses: readonly string[], limit = 20, extra: Record<string, string> = {}): string {
  const query = new URLSearchParams({ agent_id: agentId, limit: String(limit), ...extra });
  for (const status of statuses) query.append('status', status);
  return `/api/v1/tasks?${query}`;
}

/** Approvals on this agent's tasks: pending first, then newest. */
export function approvalsForTasks<T extends Pick<ApiApproval, 'task_id' | 'status' | 'created_at'>>(approvals: readonly T[], taskIds: ReadonlySet<string>): T[] {
  return approvals.filter((a) => taskIds.has(a.task_id))
    .sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending') || b.created_at.localeCompare(a.created_at));
}

/** A duration in the largest sensible unit, for `Intl.NumberFormat` with `style: "unit"`. */
export function durationParts(ms: number): { value: number; unit: 'second' | 'minute' | 'hour' | 'day' } {
  const s = ms / 1000;
  if (s < 90) return { value: Math.round(s), unit: 'second' };
  const m = s / 60;
  if (m < 90) return { value: Math.round(m), unit: 'minute' };
  const h = m / 60;
  if (h < 36) return { value: Math.round(h * 10) / 10, unit: 'hour' };
  return { value: Math.round((h / 24) * 10) / 10, unit: 'day' };
}
