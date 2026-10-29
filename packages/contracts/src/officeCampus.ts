/**
 * ANUM MINE campus contract, version 1 (wire format: snake_case JSON).
 * Server: services/api/anum_api/office_campus_api.py. Docs: docs/ANUM_MINE_PRESENCE_AND_CAMPUS.md.
 * Compatible additions (new optional fields) keep `version: 1`; a breaking change bumps it.
 */
import type { OfficePresenceStateTotals } from './officePresence.js';

export const OFFICE_CAMPUS_VERSION = 1 as const;

/** `career` is its own kind (owner decision O2, 27 October 2026). */
export type CampusBuildingKind = 'executive' | 'business' | 'department' | 'personal' | 'career';
export type CampusBuildingIcon =
  | 'building' | 'briefcase' | 'store' | 'truck' | 'home' | 'user' | 'landmark' | 'factory' | 'shield' | 'chart';

export interface CampusFloorSummary {
  id: string;
  building_id: string;
  level: number;
  name: string;
  /** Arabic display name; null until one is set (the web falls back to `name`). */
  name_ar: string | null;
}

/** One building per workspace the signed-in person is an active member of. */
export interface CampusBuilding {
  workspace_id: string;
  name: string;
  /** Null until an owner classifies the workspace (PUT /office/campus/profile). Never guessed. */
  kind: CampusBuildingKind | null;
  icon: CampusBuildingIcon | null;
  profile_revision: number | null;
  /** The caller's role in that workspace (owner, member, viewer). */
  role: string;
  archived: boolean;
  /** False when that workspace could not be read right now; the numbers below are then empty/null. */
  accessible: boolean;
  floors: CampusFloorSummary[];
  /** Count of office_buildings rows (the registry's own buildings) in that workspace. */
  office_buildings: number;
  rooms: number;
  desks: number;
  /** `by_state` counts the people on shift; `off_shift` the seated people of a day or night shift not in force now (stored hours, building time zone). */
  agents: { total: number; by_state: OfficePresenceStateTotals; off_shift?: number } | null;
  /** Newest evidence time behind a non-idle agent state; null when every agent is idle or none exist. */
  presence_verified_at: string | null;
  presence_as_of: string | null;
}

export interface CampusResponse {
  version: typeof OFFICE_CAMPUS_VERSION;
  tenant_id: string;
  current_workspace_id: string;
  generated_at: string;
  /** True when more than 50 workspaces exist and the list was cut. */
  truncated: boolean;
  buildings: CampusBuilding[];
}

export interface CampusProfileInput {
  kind: CampusBuildingKind;
  icon: CampusBuildingIcon;
  /** Omit on the first save; send the current `revision` afterwards (409 when stale). */
  expected_revision?: number;
}

export interface CampusProfile {
  workspace_id: string;
  kind: CampusBuildingKind;
  icon: CampusBuildingIcon;
  revision: number;
}

/**
 * `GET /api/v1/office/campus/boards` (version 1, server: services/api/anum_api/office_campus_boards.py): the billboard facts and
 * signs of every other workspace the caller may read, each read in that workspace's own tenant and workspace context.
 * Paged (`limit` at most 16, `cursor`); the open workspace is never in it (its own live reads carry its boards).
 */
export const OFFICE_CAMPUS_BOARDS_VERSION = 1 as const;

export interface CampusBoardSign {
  style: string | null;
  /** `#rrggbb` or null. */
  color: string | null;
  /** SHA-256 of the stored logo (fetched from `GET /office-spaces/buildings/{id}/logo` in that workspace), or null. */
  logo_sha256: string | null;
}

export interface CampusBoardBuilding {
  id: string;
  name: string;
  name_ar: string | null;
  floors: number;
  rooms: number;
  sign: CampusBoardSign | null;
}

export interface CampusBoardRoom {
  name: string;
  name_ar: string | null;
  kind: string;
  team_department: string | null;
  building_id: string;
}

export interface CampusBoardWork {
  /** `connected`, or why there are no numbers (`not_connected`, `no_access`, `error`). */
  source: string;
  open: number | null;
  waiting: number | null;
  blocked: number | null;
  /** The flow read was cut: the numbers are "at least". */
  truncated: boolean;
  by_building: Record<string, { open: number; waiting: number; blocked: number }>;
}

export type CampusBoard =
  | {
    workspace_id: string;
    accessible: true;
    as_of: string;
    /** At most 16, by id. */
    buildings: CampusBoardBuilding[];
    buildings_total: number;
    /** At most 60 rooms; `total` counts them all. */
    rooms: { total: number; items: CampusBoardRoom[] };
    departments: string[];
    work: CampusBoardWork;
  }
  | { workspace_id: string; accessible: false };

export interface CampusBoardsResponse {
  version: typeof OFFICE_CAMPUS_BOARDS_VERSION;
  tenant_id: string;
  current_workspace_id: string;
  generated_at: string;
  next_cursor: string | null;
  boards: CampusBoard[];
}
