/**
 * ANUM MINE office layout history and templates, contract version 1.
 * Server: /api/v1/office-layout (office_layout_history_api.py, office_layout_history.py, office_layout_templates.py).
 * A revision is an append-only snapshot of one floor's layout written in the same transaction as the change.
 * Restore makes a NEW revision. It is a click only action: the body carries `input_mode: "click"`.
 */

export const OFFICE_LAYOUT_HISTORY_VERSION = 1 as const;

export interface OfficeLayoutDiffSummary {
  rooms_added: number;
  rooms_removed: number;
  rooms_moved: number;
  rooms_changed: number;
  desks_added: number;
  desks_removed: number;
  desks_moved: number;
  /** Desks whose seat fields (shift, place at the table, facing, seat kind, table group) changed and nothing else. Absent on older revisions. */
  seats_changed?: number;
  /** True when the building's shift hours differ. Absent on older revisions. */
  shift_hours_changed?: boolean;
  floor_resized: boolean;
  /** Totals in the revision itself. */
  rooms: number;
  desks: number;
  /** True on the starting point written before the first tracked change. */
  baseline?: boolean;
}

export interface OfficeLayoutRevision {
  revision_no: number;
  /** Audit action name, for example office.floor.layout, office.room.update, office.floor.restore, baseline. */
  action: string;
  actor: string;
  created_at: string;
  summary: OfficeLayoutDiffSummary;
  snapshot_hash: string;
  restored_from: number | null;
  is_current: boolean;
}

export interface OfficeLayoutRevisionList {
  items: OfficeLayoutRevision[];
  head_revision: number;
  /** Revisions kept per floor; older ones are pruned. */
  cap: number;
  next_before: number | null;
  can_restore: boolean;
}

export interface OfficeLayoutBox { x: number; z: number; width: number; depth: number; rotation: number }
export type OfficeLayoutRoomStatus = 'same' | 'added' | 'removed' | 'moved' | 'changed';

export interface OfficeLayoutDiffRoom {
  id: string;
  name: string;
  kind: string;
  color: string | null;
  status: OfficeLayoutRoomStatus;
  box: OfficeLayoutBox | null;
  from_box: OfficeLayoutBox | null;
  from_name: string | null;
}

export interface OfficeLayoutDiffDesk { id: string; room_id: string; label: string; x: number | null; z: number | null }

export interface OfficeLayoutDiff {
  rooms: OfficeLayoutDiffRoom[];
  desks: OfficeLayoutDiffDesk[];
  summary: OfficeLayoutDiffSummary;
}

/** A desk a restore would remove while an agent sits at it. `agent_hidden` when the agent is private to someone else. */
export interface OfficeLayoutUnassign { desk_id: string; label: string; room: string; agent_id: string | null; agent_hidden: boolean }

export interface OfficeLayoutRevisionDetail {
  revision: OfficeLayoutRevision;
  head_revision: number;
  against: 'current' | 'previous';
  current_hash: string;
  /** Send back to restore: bound to who confirms, where and the exact revision. */
  payload_hash: string;
  matches_current: boolean;
  floor: { width_m: number | null; depth_m: number | null };
  diff: OfficeLayoutDiff;
  unassign: OfficeLayoutUnassign[];
  can_restore: boolean;
}

export interface OfficeLayoutRestoreRequest {
  payload_hash: string;
  expected_head: number;
  confirm_unassign: string[];
  input_mode: 'click';
}

export interface OfficeLayoutRestoreResult {
  floor_id: string;
  restored: boolean;
  replayed: boolean;
  /** The floor already looked like that revision: nothing was written. */
  noop: boolean;
  revision_no: number;
  restored_from: number | null;
  unassigned: { desk_id: string; label: string; room: string }[];
  summary: OfficeLayoutDiffSummary;
}

export type OfficeLayoutTemplateKind = 'floor' | 'building';

export interface OfficeLayoutTemplate {
  id: string;
  kind: OfficeLayoutTemplateKind;
  name: string;
  revision: number;
  created_by: string;
  created_at: string;
  summary: { floors: number; rooms: number; desks: number };
}

export interface OfficeLayoutTemplateList { items: OfficeLayoutTemplate[]; can_manage: boolean }
export interface OfficeLayoutTemplateSaved extends OfficeLayoutTemplate { skipped_unplaced_rooms: number }

/** `POST /floors/from-template`: the template's Arabic name goes with its own English name; send `name_ar` with a new `name`. */
export interface OfficeFloorFromTemplateRequest {
  template_id: string; id: string; building_id: string; level: number; name?: string; name_ar?: string;
}
/** `POST /buildings/from-template`: the floors keep their Arabic names; the building takes `name_ar` if sent. */
export interface OfficeBuildingFromTemplateRequest {
  template_id: string; id: string; name: string; name_ar?: string; timezone: string;
}
