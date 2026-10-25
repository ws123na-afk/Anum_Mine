/**
 * ANUM MINE executive command centre contract, version 1 (F10).
 * Server: services/api/anum_api/executive_api.py.
 *
 * Only summaries leave a workspace, and only the scopes an owner granted. Personal, employer
 * and business workspaces are separate items; nothing is pooled across them.
 */
import type { OfficePresenceStateTotals } from './officePresence.js';

export const EXECUTIVE_VERSION = 1 as const;

export type ExecutiveScope = 'counts' | 'tasks' | 'approvals' | 'cost' | 'meetings';
export const EXECUTIVE_SCOPES: readonly ExecutiveScope[] = ['counts', 'tasks', 'approvals', 'cost', 'meetings'];

/** `expired` is derived from `expires_at` / `request_expires_at`; `stored_status` is the database value. */
export type ExecutiveGrantStatus = 'pending' | 'active' | 'rejected' | 'revoked' | 'superseded' | 'expired';

export interface ExecutiveGrant {
  id: string;
  workspace_id: string;
  grantee_user_id: string;
  scopes: ExecutiveScope[];
  status: ExecutiveGrantStatus;
  stored_status: Exclude<ExecutiveGrantStatus, 'expired'>;
  /** SHA-256 hex of the canonical grant. Show it to the approving owner; send it back to approve. */
  payload_hash: string;
  revision: number;
  requested_by: string;
  requested_at: string;
  request_expires_at: string;
  approved_by: string | null;
  approved_at: string | null;
  expires_at: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  decision_reason: string | null;
}

export interface ExecutiveGrantCreated extends ExecutiveGrant {
  /** Always true: a new grant gives no access until an owner approves the hash. */
  approval_required: true;
}

export interface ExecutiveGrantCreateInput {
  grantee_user_id: string;
  scopes: ExecutiveScope[];
  /** Optional ISO 8601, in the future and within one year. */
  expires_at?: string;
}

export interface ExecutiveGrantApproveInput {
  payload_hash: string;
  reason?: string;
}

/** Narrowing only (subset of scopes and/or earlier expiry); widening needs a new request. */
export interface ExecutiveGrantNarrowInput {
  expected_revision: number;
  scopes?: ExecutiveScope[];
  expires_at?: string;
}

export interface ExecutiveGrantList {
  version: typeof EXECUTIVE_VERSION;
  items: ExecutiveGrant[];
}

export interface ExecutiveCountsSummary {
  buildings: number;
  floors: number;
  rooms: number;
  desks: number;
  agents: number;
  agents_by_state: OfficePresenceStateTotals;
  note: string;
}

export interface ExecutiveSummary {
  counts?: ExecutiveCountsSummary;
  tasks?: { by_status: Record<string, number>; blocked_agents: number };
  approvals?: { pending: number; oldest_pending_at: string | null };
  cost?: { month: string; estimated_cost_usd: string; calls: number; unpriced_calls: number; currency: 'USD' };
  meetings?: { active: number; scheduled: number; paused: number };
}

export interface ExecutiveGrantedWorkspace {
  workspace_id: string;
  name: string;
  access: 'granted';
  grant_id: string;
  scopes: ExecutiveScope[];
  expires_at: string | null;
  summary: ExecutiveSummary;
  as_of: string;
}

export interface ExecutiveNotGrantedWorkspace {
  workspace_id: string;
  name?: string;
  access: 'not_granted';
  reason: 'no_grant' | 'grant_ended';
  /** Present for workspaces where the caller is a member: open them with an explicit workspace switch. */
  open_with?: 'workspace_switch';
}

export interface ExecutiveUnavailableWorkspace {
  workspace_id: string;
  access: 'unavailable';
  reason: string;
}

export type ExecutiveWorkspaceItem =
  | ExecutiveGrantedWorkspace
  | ExecutiveNotGrantedWorkspace
  | ExecutiveUnavailableWorkspace;

export interface ExecutiveOverview {
  version: typeof EXECUTIVE_VERSION;
  tenant_id: string;
  home_workspace_id: string;
  generated_at: string;
  truncated: boolean;
  workspaces: ExecutiveWorkspaceItem[];
}
