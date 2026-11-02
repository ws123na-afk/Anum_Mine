/**
 * ANUM MINE office staffing contract, version 1.
 * Server: services/api/anum_api/office_staffing_blueprint.py and office_staffing_api.py.
 * Spec: docs/ANUM_MINE_OFFICE_STAFFING.md.
 *
 * Each office (building) has a blueprint of roles. The office view compares its real agents with it; the owner
 * applies chosen actions with a click (`workspace:manage`): a missing role becomes a PAUSED agent, an existing
 * agent gets missing skills, tools and rules. Every action carries its own hash.
 */
export const OFFICE_STAFFING_VERSION = 1 as const;

export interface StaffingText { en: string; ar: string }
export type StaffingSourceState = 'connected' | 'not_connected' | 'available' | 'no_access';
export type StaffingRoleState = 'staffed' | 'needs_update' | 'missing';
export type StaffingRisk = 'low' | 'medium' | 'high' | 'blocked';

export interface StaffingRules {
  autonomy: { read: 'auto' | 'ask' | 'off'; write: 'ask' | 'off'; external: 'off' };
  max_risk: 'low' | 'medium';
  approvals: ({ id: string } & StaffingText)[];
  monthly_cost_limit_usd: number;
  never: ({ id: string } & StaffingText)[];
}

export interface StaffingTool { name: string; state: 'present' | 'missing' | 'over_risk'; reason: string | null; risk: StaffingRisk | null; category: string | null }
export interface StaffingSkill { id: string; name: string; state: 'present' | 'missing'; reason: string | null; source: string | null }

export interface StaffingRoleView {
  key: string; template_id: string; unit: string; name: StaffingText; purpose: StaffingText; department: string;
  rules: StaffingRules; tools: StaffingTool[]; skills: StaffingSkill[];
  attach_tools: string[]; attach_skills: string[];
  missing: ({ kind: 'tool' | 'skill' | 'capability'; id: string } & StaffingText)[];
  limited: boolean; sources: string[];
}

export interface StaffingAgent {
  id: string; name: string; status: 'active' | 'paused'; desk_id: string | null; can_edit: boolean;
  skills: string[]; tools: string[];
  missing_tools: string[]; missing_skills: string[]; missing_rules: ('autonomy' | 'cost_cap' | 'never_list')[];
  over_risk_tools: string[]; complete: boolean;
}

export interface StaffingRole extends StaffingRoleView { agents: StaffingAgent[]; state: StaffingRoleState }

export interface StaffingSourceSummary { id: string; state: StaffingSourceState }
export interface StaffingSource extends StaffingSourceSummary {
  kind: 'connector' | 'module' | 'setting'; name: StaffingText; view: string | null; roles: string[];
}

export interface StaffingOfficeSummary {
  building_id: string; name: { en: string; ar: string | null };
  kind: string; kind_name: StaffingText; units: { id: string; name: StaffingText }[];
  roles_total: number; roles_staffed: number; missing_roles: string[];
  agents_total: number; agents_active: number; agents_needing_update: number;
  sources_not_connected: number; sources: StaffingSourceSummary[];
}

export interface StaffingOfficePage {
  version: 1; blueprint_version: number; workspace_id: string; as_of: string;
  total: number; limit: number; offset: number; buildings_total: number;
  items: StaffingOfficeSummary[]; can_apply: boolean;
}

export interface StaffingCreateContent {
  kind: 'create'; role: string; lang: 'en' | 'ar'; status: 'paused';
  agent: { name: string; department: string; allowed_tools: string[]; allowed_skills: string[];
    autonomy: StaffingRules['autonomy']; template_id: string; [key: string]: unknown };
  monthly_cost_limit_usd: number;
  placement: { desk_id: string; desk_label: string; room_id: string; room_name: string } | null;
}
export interface StaffingUpdateContent {
  kind: 'update'; role: string; agent_id: string; version: number;
  add_tools: string[]; add_skills: string[];
  autonomy: StaffingRules['autonomy'] | null; add_rules_text: boolean; monthly_cost_limit_usd: number | null;
}
export interface StaffingAction {
  id: string; kind: 'create' | 'update'; role: string; agent_id?: string; hash: string;
  content: StaffingCreateContent | StaffingUpdateContent;
}

export interface StaffingOffice extends Omit<StaffingOfficeSummary, 'sources'> {
  version: 1; blueprint_version: number; as_of: string; lang: 'en' | 'ar';
  roles: StaffingRole[]; sources: StaffingSource[];
  other_agents: { id: string; name: string; status: 'active' | 'paused'; desk_id: string | null; skills: string[]; tools: string[] }[];
  actions: StaffingAction[]; can_apply: boolean; limits: { agents: number; max_agents: number };
}

export type StaffingOutcome = 'created' | 'resumed' | 'updated' | 'already_done' | 'being_built' | 'stale' | 'failed';
export interface StaffingApplyResult {
  version: 1; building_id: string; created: number; updated: number;
  results: { id: string; outcome: StaffingOutcome; agent_id?: string; desk_id?: string | null; seated?: boolean; detail?: string }[];
}
export const STAFFING_MAX_ACTIONS = 20 as const;
