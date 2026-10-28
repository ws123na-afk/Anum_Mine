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
  agents: { total: number; by_state: OfficePresenceStateTotals } | null;
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
