/**
 * ANUM MINE office history contract, version 1 (read only).
 * Server: services/api/anum_api/office_history_api.py. Spec: docs/ANUM_MINE_F09_AUDITED_HISTORY_API.md
 * and docs/ANUM_MINE_HQ_HISTORY_PANEL.md.
 *
 * Rule: every item is one stored audit record. `historical_coverage_complete` is always false: a
 * missing record never proves that nothing happened, and past presence or layout is not rebuilt.
 */
export type OfficeHistoryCategory =
  | 'all' | 'task' | 'agent_run' | 'approval' | 'meeting' | 'office' | 'team_work' | 'workspace'
  | 'membership' | 'automation' | 'autonomy' | 'other';

export interface OfficeHistoryEvidence {
  id: string;
  timestamp: string;
  actor: string;
  action: string;
  target: string;
  category: OfficeHistoryCategory;
  outcome: string;
  correlation_id: string;
  /** Sanitised audit metadata. Office records carry `before` and `after` snapshots. */
  metadata: Record<string, unknown>;
  source: string;
  verified: boolean;
}

/** `GET /api/v1/office-history` and `GET /api/v1/office-history/correlations/{id}`. */
export interface OfficeHistoryPage {
  items: OfficeHistoryEvidence[];
  /** Set while older records may exist, even when `items` is empty (category filtering happens after paging). */
  next_cursor: string | null;
  workspace_id: string;
  category: OfficeHistoryCategory;
  evidence_notice: string;
  historical_coverage_complete: boolean;
}
