/**
 * ANUM MINE workstation work-panel contract, version 1 (F05).
 * Server: GET /api/v1/office/workstations/{desk_id}/work (office_presence_api.py).
 * Reuses the task and run records; `links` point at the existing task APIs for detail.
 */
import type { OfficePresenceItem } from './officePresence.js';

export const OFFICE_WORKSTATION_VERSION = 1 as const;

/** Why there is no work to show: the desk is empty, or its agent is private to someone else. */
export type OfficeWorkstationEmptyReason = 'unassigned' | 'agent_not_visible';

export interface OfficeWorkstationStep {
  id: string;
  type: string;
  summary: string | null;
  created_at: string;
}

export interface OfficeWorkstationRun {
  id: string;
  status: string;
  /** Checkpoint phase: planning, tool_ready, waiting_approval, executing, completed, failed, cancelled. */
  phase: string | null;
  checkpoint_version: number | null;
  last_step_id: string | null;
  approval_id: string | null;
  updated_at: string;
  /** Up to the 15 newest steps, oldest first. */
  steps: OfficeWorkstationStep[];
}

export interface OfficeWorkstationTask {
  id: string;
  title: string | null;
  status: string;
  priority: 'low' | 'medium' | 'high';
  due_at: string | null;
  created_at: string;
  updated_at: string;
  links: { task: string; runs: string };
  run: OfficeWorkstationRun | null;
}

export interface OfficeWorkstationApproval {
  id: string;
  task_id: string;
  action: string | null;
  risk_level: string;
  expires_at: string | null;
  expired: boolean;
  created_at: string;
}

export interface OfficeWorkstationError {
  task_id: string;
  run_id: string;
  at: string;
  message: string;
}

export interface OfficeWorkstationDocument {
  id: string;
  name: string | null;
  content_type: string;
  size_bytes: number;
  /** Existing files API path. */
  link: string;
}

export interface OfficeWorkstationWork {
  version: typeof OFFICE_WORKSTATION_VERSION;
  workspace_id: string;
  as_of: string;
  desk: { id: string; room_id: string; label: string };
  agent: { id: string; name: string; status: 'active' | 'paused' } | null;
  presence: OfficePresenceItem | null;
  reason: OfficeWorkstationEmptyReason | null;
  /** Up to 10 newest non-archived tasks of the agent. Prompts are never included. */
  tasks: OfficeWorkstationTask[];
  /** Pending approvals of those tasks; empty when the caller lacks approval:read. */
  approvals: OfficeWorkstationApproval[];
  errors: OfficeWorkstationError[];
  /** Files the agent's runs retrieved; empty when the caller lacks file:read. */
  documents: OfficeWorkstationDocument[];
}
