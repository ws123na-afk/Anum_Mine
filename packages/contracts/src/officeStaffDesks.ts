/**
 * ANUM MINE "Staff my campus" contract, version 1.
 * Server: services/api/anum_api/office_staff_desks.py, office_staff_templates.py, office_staff_desks_api.py.
 * Spec: docs/ANUM_MINE_STAFF_DESKS.md.
 *
 * Flow: preview (nothing is stored, one `payload_hash` per building) -> confirm one building at a time (click only,
 * `workspace:manage`) -> agents stay PAUSED until the owner turns them on (single agent, or the bulk turn-on that
 * shows the monthly cost cap total first).
 */
export const OFFICE_STAFF_DESKS_VERSION = 1 as const;

export type StaffDeskState =
  | 'plan' | 'resume' | 'occupied' | 'keep_empty' | 'over_limit' | 'over_cap' | 'no_role' | 'excluded' | 'agent_elsewhere';

export type StaffDeskCounts = Record<StaffDeskState, number> & { total: number };

export interface StaffDeskAgent {
  name: string; template_id: string; monthly_cost_limit_usd: number; existing_agent_id: string | null;
}

export interface StaffDesk {
  id: string; label: string; number: number; state: StaffDeskState;
  reason: 'no_matching_role' | 'agent_limit' | 'run_cap' | 'agent_seated_elsewhere' | null;
  agent: StaffDeskAgent | null;
}

export interface StaffRoom {
  id: string; name: string; name_ar: string | null; kind: string; team_department: string | null;
  floor_id: string; floor_level: number; floor_name: string | null;
  template_id: string | null; role_source: 'kind' | 'name' | 'department' | 'none';
  desks: StaffDesk[]; counts: StaffDeskCounts;
}

export interface StaffBuilding {
  id: string; name: string; selected: boolean; rooms: StaffRoom[]; counts: StaffDeskCounts;
}

export interface StaffRole {
  id: string; department: string; name: { en: string; ar: string }; count: number; monthly_cost_limit_usd: number;
  limited: boolean; tools: number; skills: number;
  missing: { kind: string; id: string; en: string; ar: string }[];
  autonomy: { read: string; write: string; external: string };
}

export interface StaffCapabilityGap {
  template_id: string; id: string; kind: string; en: string; ar: string; agents: number;
}

export interface StaffBatch {
  building_id: string; count: number; new_agents: number; resumed: number; quota: number;
  max_monthly_usd: number; payload_hash: string;
}

export interface StaffWorkspaceBudget { visible: boolean; monthly_cost_limit_usd: number | null }

export interface StaffDesksPreview {
  version: 1; catalog_version: number; workspace_id: string; as_of: string; lang: 'en' | 'ar';
  limits: { agents: number; max_agents: number; free_slots: number; run_cap: number | null };
  totals: StaffDeskCounts;
  cost: {
    per_template: { template_id: string; count: number; cap_usd: number; subtotal_usd: number }[];
    max_monthly_usd: number; agents: number; workspace_budget: StaffWorkspaceBudget; within_budget: boolean | null;
  };
  roles: StaffRole[]; capability_gaps: StaffCapabilityGap[];
  buildings: StaffBuilding[]; batches: StaffBatch[];
  can_confirm: boolean;
  rules: { created_paused: true; touches_occupied_desks: false; click_only: true; least_privilege: true };
}

export interface StaffDesksPreviewRequest {
  buildings?: string[]; exclude_rooms: string[]; max_agents?: number | null; lang: 'en' | 'ar';
}

export interface StaffDesksConfirm {
  payload_hash: string; input_mode: 'click'; building_id: string; exclude_rooms: string[]; quota: number; lang: 'en' | 'ar';
}

export type StaffDeskResultStatus = 'created' | 'seated_existing' | 'skipped' | 'failed';
export type StaffDeskReason =
  | 'agent_limit' | 'agent_refused' | 'agent_failed' | 'seat_conflict' | 'cap_failed' | 'room_failed'
  | 'kept_empty' | 'already_staffed' | 'being_built' | null;

export interface StaffDeskResult {
  desk_id: string; room_id: string; status: StaffDeskResultStatus; agent_id: string | null; agent_name: string; reason: StaffDeskReason;
}

export interface StaffDesksResult {
  version: 1; outcome: 'created' | 'partial' | 'failed' | 'already_staffed' | 'in_progress';
  building_id: string; payload_hash: string; paused: true;
  counts: Record<StaffDeskResultStatus, number>; results: StaffDeskResult[];
}

export interface StaffKeepEmptyResult { version: 1; keep_empty: boolean; changed: string[]; unchanged: string[] }

export interface StaffPausedAgent {
  agent_id: string; name: string; desk_id: string | null; room_id: string | null; template_id: string; monthly_cost_limit_usd: number | null;
}
export interface StaffPausedPage {
  version: 1; total: number; items: StaffPausedAgent[]; offset: number; limit: number; all_ids: string[]; can_confirm: boolean;
}
export interface StaffTurnOnPreview {
  version: 1; items: StaffPausedAgent[]; not_paused_or_unknown: string[]; max_monthly_usd: number; agents_without_cap: string[];
  workspace_budget: StaffWorkspaceBudget; within_budget: boolean | null; payload_hash: string; can_confirm: boolean;
}
export interface StaffTurnOnResult {
  version: 1; outcome: 'on' | 'partial' | 'failed' | 'nothing_to_turn_on';
  results: { agent_id: string; status: 'on' | 'failed'; reason: 'not_paused' | 'not_allowed' | 'not_found' | 'failed' | null }[];
  counts: { on: number; failed: number }; max_monthly_usd?: number;
}
