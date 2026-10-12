// User-built agents (docs/agents.md): API calls and the pure helpers behind the Agents view,
// its create/edit wizard and detail tabs. The helpers take no DOM, so `pnpm test:unit` runs them.
import type {
  AgentAutonomy, AgentAutonomyLevel, AgentCatalog, AgentCatalogSkill, AgentCatalogTool, AgentCategory, AgentKnowledgeMode,
  AgentKnowledgeSource, AgentMemory, AgentMemoryPage, AgentModelRoute, AgentPage, AgentProfile, AgentProfileFields,
  AgentProfileUpdate, AgentSharing, AgentSkillLoadout, AgentSkillState, AgentSkillStateName, AgentSkillTool, AgentStatus,
  AgentTemplate, AgentToolCategory, SkillUsage, SkillUsagePage,
} from '@anum/contracts';
import type { MessageKey } from './i18n.ts';

export type {
  AgentAutonomy, AgentAutonomyLevel, AgentCatalog, AgentCatalogSkill, AgentCatalogTool, AgentCategory, AgentKnowledgeMode,
  AgentKnowledgeSource, AgentMemory, AgentMemoryPage, AgentModelRoute, AgentPage, AgentProfile, AgentProfileFields,
  AgentProfileUpdate, AgentSharing, AgentSkillLoadout, AgentSkillState, AgentSkillStateName, AgentSkillTool, AgentStatus,
  AgentTemplate, AgentToolCategory, SkillUsage, SkillUsagePage,
};

export const AGENT_PAGE_SIZE = 24;
export const MEMORY_PAGE_SIZE = 20;
export const NAME_MAX = 80;
export const DESCRIPTION_MAX = 500;
export const INSTRUCTIONS_MAX = 8000;
export const MEMORY_MAX = 2000;
export const CATEGORIES: readonly AgentCategory[] = ['work', 'personal', 'custom'];
export const TOOL_CATEGORIES: readonly AgentToolCategory[] = ['read', 'write', 'external'];
export const COLORS = ['#4f46e5', '#2563eb', '#0f766e', '#16a34a', '#ea580c', '#db2777', '#7c3aed', '#475569'] as const;
export const ALWAYS_ON_TOOL = 'anum.respond';

export type CategoryFilter = 'all' | AgentCategory;
export type WizardStep = 'start' | 'identity' | 'instructions' | 'capabilities' | 'autonomy' | 'review';
export const WIZARD_STEPS: readonly WizardStep[] = ['start', 'identity', 'instructions', 'capabilities', 'autonomy', 'review'];
export const EDIT_STEPS: readonly WizardStep[] = ['identity', 'instructions', 'capabilities', 'autonomy', 'review'];
export type DetailTab = 'overview' | 'skills' | 'memory' | 'settings';
export const DETAIL_TABS: readonly DetailTab[] = ['overview', 'skills', 'memory', 'settings'];

/** What the wizard edits: the profile fields, as form values. */
export interface AgentDraft {
  name: string;
  icon: string;
  color: string;
  category: AgentCategory;
  department: string;
  description: string;
  instructions: string;
  model_route: AgentModelRoute;
  allowed_skills: string[];
  allowed_tools: string[];
  autonomy: AgentAutonomy;
  knowledge_mode: AgentKnowledgeMode;
  knowledge_sources: AgentKnowledgeSource[];
  sharing: AgentSharing;
  template_id: string | null;
}

export const DEFAULT_AUTONOMY: AgentAutonomy = { read: 'auto', write: 'ask', external: 'ask' };

export function emptyDraft(): AgentDraft {
  return {
    name: '', icon: 'bot', color: COLORS[0], category: 'custom', department: '', description: '', instructions: '',
    model_route: 'capable', allowed_skills: [], allowed_tools: [], autonomy: { ...DEFAULT_AUTONOMY },
    knowledge_mode: 'none', knowledge_sources: [], sharing: 'private', template_id: null,
  };
}

/** A template as a draft; `name` is the localized template name the person can change. */
export function draftFromTemplate(template: AgentTemplate, name: string, description: string): AgentDraft {
  return {
    ...emptyDraft(), name, description, icon: template.icon, color: template.color, category: template.category,
    instructions: template.instructions, model_route: template.model_route, allowed_skills: [...template.allowed_skills],
    allowed_tools: [...template.allowed_tools], autonomy: { ...template.autonomy }, knowledge_mode: template.knowledge_mode,
    template_id: template.id,
  };
}

export function draftFromAgent(agent: AgentProfile): AgentDraft {
  return {
    name: agent.name, icon: agent.icon, color: agent.color, category: agent.category, department: agent.department ?? '',
    description: agent.description, instructions: agent.instructions, model_route: agent.model_route,
    allowed_skills: [...agent.allowed_skills], allowed_tools: [...agent.allowed_tools], autonomy: { ...agent.autonomy },
    knowledge_mode: agent.knowledge_mode, knowledge_sources: agent.knowledge_sources.map((s) => ({ ...s })),
    sharing: agent.sharing, template_id: agent.template_id ?? null,
  };
}

/** The request body for `POST /api/v1/agents`. */
export function draftBody(draft: AgentDraft): AgentProfileFields {
  return {
    name: draft.name.trim(), icon: draft.icon, color: draft.color, category: draft.category,
    department: draft.department.trim() || null, description: draft.description.trim(), instructions: draft.instructions.trim(),
    model_route: draft.model_route, allowed_skills: [...draft.allowed_skills].sort(), allowed_tools: [...draft.allowed_tools].sort(),
    autonomy: { ...draft.autonomy }, knowledge_mode: draft.knowledge_mode,
    knowledge_sources: draft.knowledge_mode === 'selected' ? draft.knowledge_sources : [], sharing: draft.sharing,
    template_id: draft.template_id,
  };
}

/** Only what changed, for `PATCH /api/v1/agents/{id}`, with the version the edit started from. */
export function draftChanges(agent: AgentProfile, draft: AgentDraft): AgentProfileUpdate {
  const before = draftBody(draftFromAgent(agent)) as unknown as Record<string, unknown>;
  const after = draftBody(draft) as unknown as Record<string, unknown>;
  const changes: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(after)) {
    if (key === 'template_id') continue;
    if (JSON.stringify(value) !== JSON.stringify(before[key])) changes[key] = value;
  }
  return { ...(changes as AgentProfileUpdate), version: agent.version };
}

export type DraftErrors = Partial<Record<'name' | 'description' | 'instructions' | 'department' | 'skills' | 'knowledge', MessageKey>>;

/** Problems on one wizard step (or every step for `review`), as message keys. */
export function validateDraft(draft: AgentDraft, step: WizardStep, catalog: AgentCatalog | null): DraftErrors {
  const errors: DraftErrors = {};
  const all = step === 'review';
  if (all || step === 'identity') {
    if (!draft.name.trim()) errors.name = 'agents.err.nameRequired';
    else if (draft.name.trim().length > NAME_MAX) errors.name = 'agents.err.nameLong';
    if (draft.description.length > DESCRIPTION_MAX) errors.description = 'agents.err.descriptionLong';
    if (draft.department.length > 80) errors.department = 'agents.err.departmentLong';
  }
  if ((all || step === 'instructions') && draft.instructions.length > INSTRUCTIONS_MAX) errors.instructions = 'agents.err.instructionsLong';
  if ((all || step === 'capabilities') && catalog && skillsMissingTools(draft, catalog).length) errors.skills = 'agents.err.skillNeedsTool';
  if ((all || step === 'autonomy') && draft.knowledge_mode === 'selected' && !draft.knowledge_sources.length) errors.knowledge = 'agents.err.knowledgeEmpty';
  return errors;
}

/** Built-in skills in the draft whose tools are not allowed (the API refuses them). */
export function skillsMissingTools(draft: AgentDraft, catalog: AgentCatalog): { skill: AgentCatalogSkill; tools: string[] }[] {
  const allowed = new Set([...draft.allowed_tools, ALWAYS_ON_TOOL]);
  return catalog.skills
    .filter((skill) => skill.source === 'builtin' && draft.allowed_skills.includes(skill.id))
    .map((skill) => ({ skill, tools: skill.required_tools.filter((tool) => !allowed.has(tool)) }))
    .filter((entry) => entry.tools.length > 0);
}

/** Turning a built-in skill on also allows the tools it needs (when the caller may use them). */
export function toggleSkill(draft: AgentDraft, skill: AgentCatalogSkill, on: boolean, catalog: AgentCatalog): AgentDraft {
  const skills = on ? [...new Set([...draft.allowed_skills, skill.id])] : draft.allowed_skills.filter((id) => id !== skill.id);
  let tools = draft.allowed_tools;
  if (on && skill.source === 'builtin') {
    const usable = new Set(catalog.tools.filter((tool) => tool.available_to_you && !tool.always_on).map((tool) => tool.name));
    tools = [...new Set([...tools, ...skill.required_tools.filter((tool) => usable.has(tool))])];
  }
  return { ...draft, allowed_skills: skills, allowed_tools: tools };
}

export function toggleTool(draft: AgentDraft, name: string, on: boolean): AgentDraft {
  return { ...draft, allowed_tools: on ? [...new Set([...draft.allowed_tools, name])] : draft.allowed_tools.filter((tool) => tool !== name) };
}

/** Tools grouped by what they do; the always-on text tool is shown apart. */
export function toolGroups(catalog: AgentCatalog): Record<AgentToolCategory, AgentCatalogTool[]> {
  const groups: Record<AgentToolCategory, AgentCatalogTool[]> = { read: [], write: [], external: [] };
  for (const tool of catalog.tools) if (!tool.always_on) groups[tool.category].push(tool);
  return groups;
}

const LEVEL_ORDER: Record<AgentAutonomyLevel, number> = { off: 0, ask: 1, auto: 2 };

/** What a category really does: the agent's level, never looser than the workspace floor. */
export function effectiveLevel(level: AgentAutonomyLevel, floor: AgentAutonomyLevel): AgentAutonomyLevel {
  return LEVEL_ORDER[level] <= LEVEL_ORDER[floor] ? level : floor;
}

/** Levels a category may be set to (`external` is never `auto`: outward actions always need approval). */
export function levelsFor(category: AgentToolCategory): AgentAutonomyLevel[] {
  return category === 'external' ? ['off', 'ask'] : ['off', 'ask', 'auto'];
}

export function agentListPath(filter: { category: CategoryFilter; q: string; status: AgentStatus | 'all'; cursor?: string | null; limit?: number }): string {
  const query = new URLSearchParams({ limit: String(filter.limit ?? AGENT_PAGE_SIZE) });
  if (filter.category !== 'all') query.set('category', filter.category);
  if (filter.status !== 'all') query.set('status', filter.status);
  if (filter.q.trim()) query.set('q', filter.q.trim());
  if (filter.cursor) query.set('cursor', filter.cursor);
  return `/api/v1/agents?${query}`;
}

export function memoryListPath(agentId: string, q: string, cursor?: string | null): string {
  const query = new URLSearchParams({ limit: String(MEMORY_PAGE_SIZE) });
  if (q.trim()) query.set('q', q.trim());
  if (cursor) query.set('cursor', cursor);
  return `/api/v1/agents/${encodeURIComponent(agentId)}/memories?${query}`;
}

export function recentTasksPath(agentId: string, limit = 10): string {
  return `/api/v1/tasks?${new URLSearchParams({ agent_id: agentId, limit: String(limit) })}`;
}

/** The prompt a call summary runs with (the Call Agent summarizes; telephony is not set up). */
export function callSummaryPrompt(notes: string, intro: string): string {
  return `${intro}\n\n${notes.trim()}`;
}

/** Template names and descriptions in the interface language, by template id. */
export function templateKey(id: string, part: 'name' | 'description'): MessageKey {
  return `agents.template.${id}.${part}` as MessageKey;
}

/** Pages joined without duplicates (a row that moved keeps its first place). */
export function mergeById<T extends { id: string }>(first: readonly T[], more: readonly T[]): T[] {
  const seen = new Set(first.map((item) => item.id));
  return [...first, ...more.filter((item) => !seen.has(item.id))];
}

// ---------------------------------------------------------------------------- skills an agent loads

/** Badge tone for a loadout state (always shown with its text label). */
export function skillStateTone(state: AgentSkillStateName): 'success' | 'warning' | 'dangerTone' | 'neutral' {
  if (state === 'ready') return 'success';
  if (state === 'needs_tools' || state === 'disabled') return 'warning';
  if (state === 'blocked' || state === 'unavailable') return 'dangerTone';
  return 'neutral';
}

/** Ready skills first, then the ones that need attention; by name within each group. */
export function sortLoadout(items: readonly AgentSkillState[]): AgentSkillState[] {
  const rank = (s: AgentSkillState) => (s.state === 'ready' ? 0 : 1);
  return [...items].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

/** Registered tools a skill needs that the agent does not allow yet but the caller may give it. */
export function allowableMissingTools(item: AgentSkillState, catalog: AgentCatalog | null): string[] {
  if (!catalog) return [];
  const usable = new Set(catalog.tools.filter((tool) => tool.available_to_you && !tool.always_on).map((tool) => tool.name));
  return item.tools.filter((tool) => tool.registered && !tool.allowed && tool.granted !== false && usable.has(tool.name)).map((tool) => tool.name);
}

/** The PATCH that adds a skill (and, for a built-in one, the tools it needs that the caller may give). */
export function attachSkillChanges(agent: AgentProfile, skill: AgentCatalogSkill, catalog: AgentCatalog): AgentProfileUpdate {
  const next = toggleSkill(draftFromAgent(agent), skill, true, catalog);
  return { allowed_skills: [...next.allowed_skills].sort(), allowed_tools: [...next.allowed_tools].sort(), version: agent.version };
}

/** The PATCH that removes a skill; the tools stay (other skills or the agent may use them). */
export function detachSkillChanges(agent: AgentProfile, skillId: string): AgentProfileUpdate {
  return { allowed_skills: agent.allowed_skills.filter((id) => id !== skillId), version: agent.version };
}

/** The PATCH that allows more tools on the agent. */
export function allowToolsChanges(agent: AgentProfile, tools: readonly string[]): AgentProfileUpdate {
  return { allowed_tools: [...new Set([...agent.allowed_tools, ...tools])].sort(), version: agent.version };
}

/** Catalog skills the agent does not load yet, matching a search over name, id, description and tools. */
export function addableSkills(catalog: AgentCatalog | null, loaded: readonly string[], query: string): AgentCatalogSkill[] {
  if (!catalog) return [];
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const have = new Set(loaded);
  return catalog.skills
    .filter((skill) => !have.has(skill.id))
    .filter((skill) => words.every((word) => `${skill.name} ${skill.id} ${skill.description} ${skill.required_tools.join(' ')}`.toLowerCase().includes(word)))
    .sort((a, b) => (a.source === b.source ? a.name.localeCompare(b.name) : a.source === 'installed' ? -1 : 1));
}

/** Loaded skills matching a search (shown once the list is long). */
export function filterLoadout(items: readonly AgentSkillState[], query: string): AgentSkillState[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter((item) => words.every((word) => `${item.name} ${item.id} ${item.description} ${item.tools.map((t) => t.name).join(' ')}`.toLowerCase().includes(word)));
}

/** Skill names for an agent card: the first few by catalog name, and how many more. */
export function skillNames(agent: AgentProfile, catalog: AgentCatalog | null, max = 3): { names: string[]; more: number } {
  const byId = new Map((catalog?.skills ?? []).map((skill) => [skill.id, skill.name]));
  const names = agent.allowed_skills.map((id) => byId.get(id) ?? id);
  return { names: names.slice(0, max), more: Math.max(0, names.length - max) };
}

/** Usage by skill id, for the Skills page. */
export function usageById(page: SkillUsagePage | null): Map<string, SkillUsage> {
  return new Map((page?.items ?? []).map((item) => [item.skill_id, item]));
}

// ---------------------------------------------------------------------------- API

type Requester = <T>(path: string, init: RequestInit) => Promise<T>;
type VoidRequester = (path: string, init: RequestInit) => Promise<void>;

export function agentsApi(request: Requester, requestVoid: VoidRequester) {
  return {
    catalog: () => request<AgentCatalog>('/api/v1/agents/catalog', { method: 'GET' }),
    list: (filter: Parameters<typeof agentListPath>[0]) => request<AgentPage>(agentListPath(filter), { method: 'GET' }),
    get: (id: string) => request<AgentProfile>(`/api/v1/agents/${encodeURIComponent(id)}`, { method: 'GET' }),
    create: (body: AgentProfileFields) => request<AgentProfile>('/api/v1/agents', { method: 'POST', body: JSON.stringify(body) }),
    update: (id: string, body: AgentProfileUpdate) => request<AgentProfile>(`/api/v1/agents/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) }),
    remove: (id: string) => requestVoid(`/api/v1/agents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    memories: (id: string, q: string, cursor?: string | null) => request<AgentMemoryPage>(memoryListPath(id, q, cursor), { method: 'GET' }),
    addMemory: (id: string, content: string) => request<AgentMemory>(`/api/v1/agents/${encodeURIComponent(id)}/memories`, { method: 'POST', body: JSON.stringify({ content }) }),
    skills: (id: string) => request<AgentSkillLoadout>(`/api/v1/agents/${encodeURIComponent(id)}/skills`, { method: 'GET' }),
    skillUsage: () => request<SkillUsagePage>('/api/v1/agents/skills/usage', { method: 'GET' }),
    removeMemory: (id: string, memoryId: string) => requestVoid(`/api/v1/agents/${encodeURIComponent(id)}/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' }),
  };
}
