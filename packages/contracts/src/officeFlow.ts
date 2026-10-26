import type { AvatarAppearance } from './avatar.js';
/**
 * ANUM MINE live flow, stuck work and office team contract, version 1.
 * Server: services/api/anum_api/office_flow.py and office_flow_api.py.
 * Spec: docs/ANUM_MINE_HQ_LIVE_FLOW.md.
 *
 * Rule: an item exists only because a stored record backs it; it sits in an office only because a
 * record places it there (`location`); it is stuck only because a stated rule matched (`stuck`).
 */
export const OFFICE_FLOW_VERSION = 1 as const;

export type OfficeFlowKind = 'task' | 'lead' | 'order';
export type OfficeFlowStage = 'arrived' | 'in_office' | 'waiting_human' | 'blocked' | 'done';
export type OfficeFlowSourceState = 'connected' | 'no_access' | 'not_connected' | 'error';
export type OfficeFlowSeverity = 'critical' | 'warning';

export type OfficeFlowStuckCode =
  | 'approval_waiting' | 'approval_expired' | 'approval_missing' | 'run_failed' | 'run_silent'
  | 'queued_too_long' | 'agent_paused' | 'lead_unassigned' | 'lead_idle' | 'order_ship_overdue';

export type OfficeFlowAction =
  | 'decide_approval' | 'retry_task' | 'open_task' | 'resume_agent' | 'assign_owner' | 'open_lead' | 'open_order';

/** Why an item has no office: `plain_runtime` (task without an agent), `agent_has_no_desk`,
 *  `no_owner`, `owner_is_person`, `no_order_office`. */
export type OfficeFlowLocationReason =
  | 'plain_runtime' | 'agent_has_no_desk' | 'no_owner' | 'owner_is_person' | 'no_order_office';

export interface OfficeFlowLocation {
  building_id: string; floor_id: string; room_id: string; room_name: string; desk_id: string;
  agent_id: string | null; agent_name: string | null;
  /** `handler`: no record names an owner, so the owner confirmed handler of this kind of work holds the item. */
  via?: 'handler';
}

export interface OfficeFlowStuck {
  code: OfficeFlowStuckCode;
  severity: OfficeFlowSeverity;
  since: string;
  age_seconds: number;
  threshold_seconds: number | null;
  facts: Record<string, string | number | boolean>;
  action: OfficeFlowAction;
}

export interface OfficeFlowWaiting {
  code: 'approval_pending'; since: string; approval_id: string; action: string | null; expires_at: string | null;
}

export interface OfficeFlowItem {
  key: string;
  kind: OfficeFlowKind;
  id: string;
  title: string;
  stage: OfficeFlowStage;
  status: string;
  since: string;
  created_at: string;
  location: OfficeFlowLocation | null;
  location_reason: OfficeFlowLocationReason | null;
  stuck: OfficeFlowStuck | null;
  waiting: OfficeFlowWaiting | null;
}

export interface OfficeFlowRoomRollup {
  room_id: string; floor_id: string; building_id: string;
  items: number; waiting: number; stuck: number; worst: OfficeFlowSeverity | null;
}

export interface OfficeFlowTotals {
  items: number;
  by_stage: Record<OfficeFlowStage, number>;
  by_kind: Partial<Record<OfficeFlowKind, number>>;
  waiting_human: number; stuck: number; stuck_by_code: Partial<Record<OfficeFlowStuckCode, number>>;
  critical: number; unlocated: number;
}

export interface OfficeFlowSnapshot {
  version: 1;
  workspace_id: string;
  as_of: string;
  cursor: string | null;
  thresholds: Record<string, number>;
  sources: { tasks: OfficeFlowSourceState; approvals: OfficeFlowSourceState; leads: OfficeFlowSourceState; orders: OfficeFlowSourceState };
  items: OfficeFlowItem[];
  rooms: Record<string, OfficeFlowRoomRollup>;
  totals: OfficeFlowTotals;
  truncated: boolean;
}

export interface OfficeFlowTrailStep { id: string; type: string; at: string; data: Record<string, string> }
export interface OfficeFlowTrail {
  version: 1; kind: OfficeFlowKind; item_id: string;
  source: 'connected' | 'not_connected' | 'not_recorded'; steps: OfficeFlowTrailStep[];
}

export interface OfficeTeamSkill { id: string; name: string; state: string; reason: string | null; source: string }
export interface OfficeTeamMember {
  agent_id: string; name: string; icon: string; color: string;
  category: 'work' | 'personal' | 'custom'; department: string | null; description: string;
  agent_status: 'active' | 'paused';
  /** The avatar style the owner chose for this agent, or null for the default drawn from the agent id. */
  avatar: AvatarAppearance | null;
  state: string; state_reason: string | null; last_verified_at: string | null; stale: boolean;
  task: { id: string; title: string } | null;
  stuck: (OfficeFlowStuck & { item_key: string; title: string }) | null;
  desk: { id: string; room_id: string; room_name: string; floor_id: string; building_id: string };
  skills: OfficeTeamSkill[] | null; skills_total: number; tools_total: number;
}
export interface OfficeTeam {
  version: 1; workspace_id: string; as_of: string;
  scope: { building_id: string | null; floor_id: string | null; room_id: string | null };
  skills_included: boolean;
  members: OfficeTeamMember[];
  vacant_desks: { desk_id: string; room_id: string; room_name: string }[];
  hidden_assignments: number;
  counts: { agents: number; vacant_desks: number; hidden: number; working: number; waiting_approval: number; blocked: number; stuck: number };
  unassigned_agents: { agent_id: string; name: string; department: string | null; status: string }[];
  unassigned_total: number;
}
