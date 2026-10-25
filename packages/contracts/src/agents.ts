// User-built agents (docs/agents.md), as the API returns them (services/api/anum_api/agent_profiles.py).
// Wire shapes are snake_case, as the API sends them. One agent-profile model serves custom
// agents and department assistants (`category: 'work'` with a `department`).

import type { AvatarAppearance } from './avatar.js';

export type AgentCategory = 'work' | 'personal' | 'custom';
export type AgentStatus = 'active' | 'paused';
export type AgentSharing = 'private' | 'workspace';
export type AgentModelRoute = 'fast' | 'capable';
export type AgentAutonomyLevel = 'off' | 'ask' | 'auto';
export type AgentToolCategory = 'read' | 'write' | 'external';
export type AgentKnowledgeMode = 'none' | 'workspace' | 'selected';

/** Per tool category: `off` blocks, `ask` needs approval, `auto` follows workspace policy. `external` is never `auto`. */
export interface AgentAutonomy {
  read: AgentAutonomyLevel;
  write: AgentAutonomyLevel;
  external: Exclude<AgentAutonomyLevel, 'auto'>;
}

export interface AgentKnowledgeSource {
  type: 'memory' | 'file';
  id: string;
}

/** What a person sets on an agent: the body of `POST /api/v1/agents`. */
export interface AgentProfileFields {
  name: string;
  icon: string;
  color: string;
  category: AgentCategory;
  department?: string | null;
  description: string;
  instructions: string;
  model_route: AgentModelRoute;
  allowed_skills: string[];
  allowed_tools: string[];
  autonomy: AgentAutonomy;
  knowledge_mode: AgentKnowledgeMode;
  knowledge_sources: AgentKnowledgeSource[];
  sharing: AgentSharing;
  template_id?: string | null;
  /** The style the headquarters draws this agent in; null or absent = the neutral default from the agent id. */
  avatar?: AvatarAppearance | null;
}

/** `PATCH /api/v1/agents/{id}`: only the fields sent change; `version` refuses a stale edit with 409. */
export type AgentProfileUpdate = Partial<AgentProfileFields> & { status?: AgentStatus; version?: number };

export interface AgentProfile extends AgentProfileFields {
  id: string;
  tenant_id: string;
  workspace_id: string;
  owner_user_id: string;
  status: AgentStatus;
  created_at: string;
  updated_at: string;
  version: number;
  can_edit: boolean;
  can_run: boolean;
  /** Allowed tools or skills this deployment or workspace no longer provides. */
  unavailable_tools: string[];
  unavailable_skills: string[];
}

/** `GET /api/v1/agents?category=&status=&q=&limit=&cursor=` */
export interface AgentPage {
  items: AgentProfile[];
  next_cursor: string | null;
  /** Per category plus `all`, after search and status filters, before the category filter. */
  counts: Record<'all' | AgentCategory, number>;
}

export interface AgentMemory {
  id: string;
  agent_id: string;
  content: string;
  source: 'manual' | 'task';
  task_id: string | null;
  created_by: string;
  created_at: string;
}

/** `GET /api/v1/agents/{id}/memories?q=&limit=&cursor=` */
export interface AgentMemoryPage {
  items: AgentMemory[];
  next_cursor: string | null;
  total: number;
}

export interface AgentCatalogTool {
  name: string;
  description: string;
  risk_level: 'low' | 'medium' | 'high';
  category: AgentToolCategory;
  /** The internal text-response tool every agent has. */
  always_on: boolean;
  available_to_you: boolean;
  reason: string | null;
}

export interface AgentCatalogSkill {
  id: string;
  name: string;
  description: string;
  source: 'builtin' | 'installed';
  required_tools: string[];
}

export interface AgentTemplate extends Omit<AgentProfileFields, 'sharing' | 'knowledge_sources' | 'department' | 'template_id'> {
  id: string;
  /** What the template would use that this deployment does not have yet. */
  missing_capabilities: string[];
}

/** `GET /api/v1/agents/catalog` */
export interface AgentCatalog {
  tools: AgentCatalogTool[];
  skills: AgentCatalogSkill[];
  templates: AgentTemplate[];
  icons: string[];
  /** The least restrictive level each category may run at in this workspace. */
  autonomy_floor: AgentAutonomy;
}

/** How a skill an agent allows is treated now: `ready` loads; every other state says why not. */
export type AgentSkillStateName = 'ready' | 'needs_tools' | 'disabled' | 'not_installed' | 'blocked' | 'unavailable';

export interface AgentSkillTool {
  name: string;
  /** The tool exists in this deployment. */
  registered: boolean;
  /** The agent may use it (its allowed tools, or the always-on text tool). */
  allowed: boolean;
  /** Granted by the workspace installation; null for built-in skills. */
  granted: boolean | null;
}

export interface AgentSkillState {
  id: string;
  name: string;
  description: string;
  source: 'builtin' | 'installed' | 'unknown';
  version: string | null;
  latest_version: string | null;
  risk_level: 'low' | 'medium' | 'high' | 'blocked' | null;
  state: AgentSkillStateName;
  /** Why it does not load and who can change that; null when ready. */
  reason: string | null;
  /** `on_request`: selected when a task's words call for it. `always`: loaded into every run and chat. */
  activation: 'on_request' | 'always';
  /** Words that select a built-in skill (a sample, English first). */
  triggers: string[];
  /** An installed skill whose instructions the agent loads. */
  loads_instructions: boolean;
  upgrade_available: boolean;
  tools: AgentSkillTool[];
}

/** `GET /api/v1/agents/{id}/skills` (docs/agents.md#skills). */
export interface AgentSkillLoadout {
  agent_id: string;
  agent_version: number;
  can_edit: boolean;
  items: AgentSkillState[];
  ready: number;
  attention: number;
}

export interface SkillUsageAgent {
  id: string;
  name: string;
  icon: string;
  color: string;
  status: AgentStatus;
}

export interface SkillUsage {
  skill_id: string;
  count: number;
  agents: SkillUsageAgent[];
}

/** `GET /api/v1/agents/skills/usage`: the agents the caller can see that load each skill. */
export interface SkillUsagePage {
  items: SkillUsage[];
}
