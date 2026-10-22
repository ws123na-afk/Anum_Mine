/**
 * ANUM MINE live presence contract, version 1.
 * Server: services/api/anum_api/office_presence.py and office_presence_api.py.
 *
 * Rule: a state is shown only with the record that proves it (`provenance`). `idle` means
 * "no active run, task, approval or meeting record was found", verified at `last_verified_at`.
 */
export const OFFICE_PRESENCE_VERSION = 1 as const;

export type OfficePresenceState =
  | 'working' | 'queued' | 'meeting' | 'waiting_approval' | 'blocked' | 'idle' | 'paused' | 'unknown';

export type OfficePresenceStateTotals = Record<OfficePresenceState, number>;

export type OfficePresenceProvenanceKind = 'run' | 'task' | 'approval' | 'meeting' | 'agent' | 'none';

/** Why the state holds. Only the ids that apply are set. */
export interface OfficePresenceProvenance {
  kind: OfficePresenceProvenanceKind;
  run_id: string | null;
  task_id: string | null;
  approval_id: string | null;
  meeting_id: string | null;
  /** Machine reason: agent_paused, approval_expired, approval_missing, run_failed, run_silent,
   *  agent_not_visible, no_active_record. Null when the state is plainly the record's status. */
  reason: string | null;
}

export interface OfficePresenceItem {
  /** Stable key: the agent id, or `desk:<desk_id>` for a desk whose agent is not visible to you. */
  key: string;
  agent_id: string | null;
  agent_name: string | null;
  desk_id: string | null;
  state: OfficePresenceState;
  provenance: OfficePresenceProvenance;
  task: { id: string; title: string } | null;
  /** When the backing record was last confirmed (the read time for `idle`/`unknown`). ISO 8601. */
  last_verified_at: string;
  /** True when the evidence is older than 5 minutes; render it greyed out. */
  stale: boolean;
}

export interface OfficePresenceSnapshot {
  version: typeof OFFICE_PRESENCE_VERSION;
  workspace_id: string;
  as_of: string;
  /** Id of the newest domain event at read time (Last-Event-ID for the stream), or null. */
  cursor: string | null;
  totals: OfficePresenceStateTotals;
  items: OfficePresenceItem[];
}

/** SSE `event: snapshot` frame: the full snapshot plus `sequence: 0`. Sent first, also after a reconnect. */
export interface OfficePresenceSnapshotFrame extends OfficePresenceSnapshot {
  sequence: 0;
}

/** SSE `event: delta` frame: only what changed since the previous frame of this connection. */
export interface OfficePresenceDeltaFrame {
  version: typeof OFFICE_PRESENCE_VERSION;
  /** 1, 2, 3 ... per connection. A gap or restart means: reconnect and use the new snapshot. */
  sequence: number;
  as_of: string;
  totals: OfficePresenceStateTotals;
  changed: OfficePresenceItem[];
  /** Keys that no longer exist (agent deleted, desk freed). */
  removed: string[];
}
