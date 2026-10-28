/**
 * ANUM MINE starter campus contract, version 1.
 * Server: services/api/anum_api/office_starter_campus.py and office_starter_campus_api.py.
 * Spec: docs/ANUM_MINE_STARTER_CAMPUS.md.
 *
 * Flow: preview (exactly what would be created and skipped, with a payload hash) -> confirm (click only,
 * `workspace:manage`). Nothing is stored by the preview. No agent is created and nothing is seated.
 */
export const OFFICE_STARTER_CAMPUS_VERSION = 1 as const;

export type StarterAction = 'create' | 'skip';
export type StarterSkipReason = 'exists' | 'name_in_use' | 'level_in_use' | 'floor_unavailable' | 'no_space';
export type StarterBuildingStatus = 'new' | 'partial' | 'complete';

export interface StarterName { en: string; ar: string }
export interface StarterCounts { buildings: number; floors: number; rooms: number; desks: number }
/** A room of the master list that already exists keeps everything; only an empty or custom department is filled. */
export interface StarterUpdateCounts { room_departments: number }
export interface StarterDepartmentSet { from: string | null; to: string }

export interface StarterDesk { id: string; label: string; pos_x: number | null; pos_z: number | null; action: 'create' }

export interface StarterRoom {
  key: string; id: string; name: StarterName; kind: string; capacity: number; width: number; depth: number;
  door_side: string; color: string; team_department: string | null; equipment: { kind: string; quantity: number }[];
  desk_count: number; action: StarterAction; reason: StarterSkipReason | null;
  /** Only on an existing room whose department is empty or custom: the department the master list names. */
  department_set?: StarterDepartmentSet;
  x?: number; z?: number; rotation?: number; desks: StarterDesk[];
}

export interface StarterFloor {
  key: string; id: string; level: number; name: StarterName; width_m: number; depth_m: number;
  action: StarterAction; reason: StarterSkipReason | null; rooms: StarterRoom[];
}

export interface StarterBuilding {
  key: string; number: number; id: string; name: StarterName; timezone: string; selected: boolean;
  /** The campus building kind it is drawn as (Career & Employment is `career`). Optional: an older server leaves it out. */
  campus_kind?: 'executive' | 'business' | 'department' | 'personal' | 'career';
  action: StarterAction; reason: StarterSkipReason | null; status: StarterBuildingStatus;
  floors: StarterFloor[]; counts: { create: StarterCounts; skip: StarterCounts; update: StarterUpdateCounts };
}

export interface StarterCampusPreview {
  version: 1; catalog_version: number; workspace_id: string; timezone: string; selected: string[];
  payload_hash: string; can_confirm: boolean; buildings: StarterBuilding[];
  totals: { create: StarterCounts; skip: StarterCounts; update: StarterUpdateCounts };
  rules: { creates_agents: false; seats_agents: false; changes_existing: false; fills_empty_departments: true };
}

export interface StarterCampusConfirm {
  payload_hash: string; input_mode: 'click'; buildings: string[]; timezone: string;
}

export type StarterItemStatus = 'created' | 'already_exists' | 'changed' | 'failed';
export interface StarterCampusResultItem {
  key: string; id: string; name: StarterName; status: StarterItemStatus; created: StarterCounts;
  skipped: StarterCounts | null; message: string; departments_set: number;
}
export type StarterCampusOutcome = 'created' | 'partial' | 'already_set_up' | 'failed';
export interface StarterCampusResult {
  version: 1; outcome: StarterCampusOutcome; payload_hash: string; timezone: string;
  buildings: StarterCampusResultItem[]; totals: StarterCounts; departments_set: number; agents_created: 0;
}

/**
 * "Add Arabic names" for campuses set up before names had an Arabic form.
 * Server: office_arabic_names_api.py. `GET /api/v1/office/starter-campus/arabic-names/preview` stores nothing;
 * `POST .../confirm` needs `workspace:manage`, the hash shown and `input_mode: "click"`. It fills ONLY an empty
 * Arabic name whose English name is a name of the master list, never overwrites, and changes no other field.
 */
export type ArabicNameKind = 'buildings' | 'floors' | 'rooms';
export interface ArabicNameCounts { buildings: number; floors: number; rooms: number }
export interface ArabicNameItem { kind: ArabicNameKind; id: string; name: string; name_ar: string }
export interface ArabicNamesPreview {
  version: 1; catalog_version: number; workspace_id: string; payload_hash: string; can_confirm: boolean;
  items: ArabicNameItem[]; counts: ArabicNameCounts; total: number;
  rules: { overwrites: false; fills_empty_only: true; matches_master_names_only: true; changes_other_fields: false };
}
export interface ArabicNamesConfirm { payload_hash: string; input_mode: 'click' }
export type ArabicNamesOutcome = 'filled' | 'nothing_to_add';
export interface ArabicNamesResult {
  version: 1; outcome: ArabicNamesOutcome; payload_hash: string; filled: number; counts: ArabicNameCounts; skipped: number;
}
