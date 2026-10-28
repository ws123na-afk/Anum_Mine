/**
 * Reception visitor register, version 1 (wire format: snake_case JSON). Owner decision O4 (27 October 2026).
 * Server: services/api/anum_api/office_visitors_api.py. Docs: docs/ANUM_MINE_HQ_RECEPTION.md.
 * Reads need `task:read`; every write needs `workspace:manage`, `input_mode: "click"` and, on an existing visit,
 * the `expected_revision` the person was shown.
 */
export const OFFICE_VISITORS_VERSION = 1 as const;

export type VisitStatus = 'expected' | 'checked_in' | 'checked_out' | 'cancelled';
export type VisitHostKind = 'user' | 'agent';
export type VisitInputMode = 'click' | 'typed' | 'voice';

export interface VisitHost { kind: VisitHostKind; id: string }

export interface Visit {
  id: string;
  visitor_name: string;
  company: string | null;
  host: VisitHost | null;
  purpose: string;
  status: VisitStatus;
  expected_at: string;
  checked_in_at: string | null;
  checked_out_at: string | null;
  cancelled_at: string | null;
  building_id: string | null;
  room_id: string | null;
  revision: number;
  created_by: string;
  updated_by: string;
  created_at: string;
  updated_at: string;
}

export interface VisitPage {
  version: typeof OFFICE_VISITORS_VERSION;
  items: Visit[];
  /** Keyset cursor for the next page; null at the end. */
  next_cursor: string | null;
  /** Matching visits for the filters (with the status filter). */
  total: number;
  /** Matching visits per status for the window and search (without the status filter). */
  counts: Record<VisitStatus, number>;
  /** Whether the caller may add and change visits (workspace owner). */
  can_manage: boolean;
  as_of: string;
}

/** Only counts: never a name. The 3D reception room shows these. */
export interface VisitSummary {
  version: typeof OFFICE_VISITORS_VERSION;
  checked_in: number;
  by_room: { room_id: string; count: number }[];
  /** Checked-in visits with a building and no room. */
  by_building: { building_id: string; count: number }[];
  /** Checked-in visits with neither a building nor a room. */
  unplaced: number;
  as_of: string;
}

export interface VisitCreateInput {
  /** Optional client id: a retry with the same id and details answers the stored visit (200). */
  id?: string;
  visitor_name: string;
  company?: string | null;
  host_kind?: VisitHostKind | null;
  host_id?: string | null;
  purpose: string;
  expected_at: string;
  building_id?: string | null;
  room_id?: string | null;
  /** A walk-in: checked in at once. */
  arrived?: boolean;
  input_mode: VisitInputMode;
}

export type VisitUpdateInput = Partial<Omit<VisitCreateInput, 'id' | 'arrived' | 'input_mode'>> & {
  expected_revision: number;
  input_mode: VisitInputMode;
};

export interface VisitMoveInput { expected_revision: number; input_mode: VisitInputMode }
