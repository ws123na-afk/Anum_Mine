/**
 * ANUM MINE office builder contract, version 1.
 * Server: /api/v1/office-spaces (office_space_api.py, office_layout.py).
 * Units are metres. A floor's origin is its north west corner: x grows east, z grows south.
 * A room's footprint (x, z, width, depth) is axis aligned; `rotation` and `door_side`
 * describe the room's interior only. Desk positions are metres from the room's corner.
 */

export const OFFICE_BUILDER_VERSION = 1 as const;

export const OFFICE_ROOM_KINDS = ['office', 'meeting', 'reception', 'workshop', 'warehouse', 'lounge', 'command'] as const;
export type OfficeRoomKind = (typeof OFFICE_ROOM_KINDS)[number];
/** Rooms of these kinds may hold permanent desks. */
export const OFFICE_DESK_ROOM_KINDS = ['office', 'workshop', 'warehouse', 'command'] as const;
export const OFFICE_ROTATIONS = [0, 90, 180, 270] as const;
export type OfficeRotation = (typeof OFFICE_ROTATIONS)[number];
export const OFFICE_DOOR_SIDES = ['north', 'south', 'east', 'west'] as const;
export type OfficeDoorSide = (typeof OFFICE_DOOR_SIDES)[number];
/** Palette names; the web maps each to a design token. */
export const OFFICE_ROOM_COLORS = ['slate', 'forest', 'brass', 'sapphire', 'amethyst', 'crimson', 'teal', 'sand'] as const;
export type OfficeRoomColor = (typeof OFFICE_ROOM_COLORS)[number];
export const OFFICE_EQUIPMENT_KINDS = [
  'monitor', 'display', 'whiteboard', 'projector', 'conference_phone', 'video_conference_kit',
  'printer', 'server_rack', 'network_switch', 'workbench', 'shelving', 'locker',
  'charging_station', 'first_aid_kit',
] as const;
export type OfficeEquipmentKind = (typeof OFFICE_EQUIPMENT_KINDS)[number];
/** The existing team concept (team_models.Department), plus the departments added with room modules. */
export const OFFICE_TEAM_DEPARTMENTS = [
  'sales', 'support', 'operations', 'finance', 'tyre_engineering',
  'marketing', 'technology', 'research', 'legal', 'compliance', 'security', 'hr', 'procurement', 'executive',
  'custom',
] as const;
export type OfficeTeamDepartment = (typeof OFFICE_TEAM_DEPARTMENTS)[number];
export const OFFICE_TEMPLATE_IDS = ['open_office', 'meeting_room', 'reception', 'warehouse', 'workshop', 'command_center'] as const;
export type OfficeTemplateId = (typeof OFFICE_TEMPLATE_IDS)[number];

export const OFFICE_DEFAULT_FLOOR_WIDTH_M = 60;
export const OFFICE_DEFAULT_FLOOR_DEPTH_M = 40;

export interface OfficeEquipmentItem {
  kind: OfficeEquipmentKind;
  quantity: number;
  note?: string;
}

/** Machine codes in the error envelope's `details[].type`; `details[].location` names the record. */
export type OfficeBuilderErrorCode =
  | 'ROOM_OVERLAP' | 'ROOM_OUT_OF_BOUNDS' | 'DESK_OUT_OF_ROOM' | 'ROOM_NOT_PLACED'
  | 'ROOM_NOT_ON_FLOOR' | 'DESK_NOT_ON_FLOOR' | 'STALE_REVISION' | 'NO_FREE_SPACE'
  | 'ROOM_NAME_TAKEN' | 'FLOOR_LEVEL_TAKEN' | 'DESK_LABEL_TAKEN' | 'AGENT_ALREADY_SEATED'
  | 'KIND_FORBIDS_DESKS' | 'CAPACITY_BELOW_DESKS'
  | 'BUILDING_NOT_EMPTY' | 'FLOOR_PRESENT' | 'FLOOR_NOT_EMPTY' | 'ROOM_PRESENT'
  | 'ROOM_NOT_EMPTY' | 'DESK_PRESENT';

/** Create bodies: `name_ar` is optional everywhere (absent means no Arabic name, never an empty string). */
export interface OfficeBuildingCreate {
  id: string;
  name: string;
  name_ar?: string;
  timezone: string;
  latitude?: number | null;
  longitude?: number | null;
  /** Day and night hours "HH:MM" in the building's time zone, read only by older Day or Night seats without hours of their own; `null` clears them (07:00 and 19:00 then apply). */
  shift_day_start?: string | null;
  shift_night_start?: string | null;
}
export interface OfficeFloorCreate {
  id: string;
  building_id: string;
  level: number;
  name: string;
  name_ar?: string;
  width_m?: number;
  depth_m?: number;
}
export interface OfficeRoomCreate {
  id: string;
  floor_id: string;
  name: string;
  name_ar?: string;
  kind: OfficeRoomKind;
  capacity: number;
}

/** The look of a building's sign (docs/ANUM_MINE_HQ_LOGO_SIGNS.md). `null` means the default lettered sign. */
export type OfficeSignStyle = 'plain' | 'plaque' | 'illuminated' | 'banner';
export const OFFICE_SIGN_STYLES: readonly OfficeSignStyle[] = ['plain', 'plaque', 'illuminated', 'banner'];

/** PATCH bodies: send only what changes. `null` clears color, name_ar, team_department and coordinates. */
export interface OfficeBuildingUpdate {
  expected_revision: number;
  name?: string;
  /** One line, 1 to 100 characters; `null` clears it. */
  name_ar?: string | null;
  timezone?: string;
  latitude?: number | null;
  longitude?: number | null;
  shift_day_start?: string | null;
  shift_night_start?: string | null;
  /** The sign style; `null` clears it (the lettered sign). */
  sign_style?: OfficeSignStyle | null;
  /** The brand colour of the sign, `#rrggbb` (the server lower-cases it); `null` clears it. */
  brand_color?: string | null;
}
export interface OfficeFloorUpdate {
  expected_revision: number;
  name?: string;
  name_ar?: string | null;
  level?: number;
  /** Send both or neither. */
  width_m?: number;
  depth_m?: number;
}
export interface OfficeRoomUpdate {
  expected_revision: number;
  name?: string;
  kind?: OfficeRoomKind;
  capacity?: number;
  rotation?: OfficeRotation;
  door_side?: OfficeDoorSide;
  description?: string;
  notes?: string;
  color?: OfficeRoomColor | null;
  name_ar?: string | null;
  /** Replaces the list; each kind once. */
  equipment?: OfficeEquipmentItem[];
  team_department?: OfficeTeamDepartment | null;
}
export interface OfficeDeskUpdate {
  expected_revision: number;
  label?: string;
  /** Both or neither; both null clears the position. */
  pos_x?: number | null;
  pos_z?: number | null;
  /** Seat fields (docs/ANUM_MINE_GREEN_CONCRETE.md); `null` clears one. `shift` is the older day or night seat. */
  shift?: 'day' | 'night' | null;
  seat_order?: number | null;
  facing?: 'north' | 'south' | 'east' | 'west' | null;
  seat_kind?: 'team' | 'manager' | null;
  table_group?: string | null;
  /** The working hours of the person at this seat, "HH:MM" in the building's time zone (an end before the start runs over
   * midnight). Send both together; both `null` clears them. */
  work_start?: string | null;
  work_end?: string | null;
}

/** PUT /floors/{id}/layout: all or nothing. */
export interface OfficeLayoutRoom {
  id: string;
  expected_revision: number;
  x: number;
  z: number;
  width: number;
  depth: number;
  rotation?: OfficeRotation;
  door_side?: OfficeDoorSide;
}
export interface OfficeLayoutDesk {
  id: string;
  expected_revision: number;
  pos_x: number;
  pos_z: number;
}
export interface OfficeFloorLayoutRequest {
  rooms?: OfficeLayoutRoom[];
  desks?: OfficeLayoutDesk[];
}

export interface OfficeRoomCopyRequest {
  id: string;
  name?: string;
  floor_id?: string;
  x?: number;
  z?: number;
  copy_desks?: boolean;
}
export interface OfficeRoomFromTemplateRequest {
  id: string;
  floor_id: string;
  template_id: OfficeTemplateId;
  name?: string;
  x?: number;
  z?: number;
  with_desks?: boolean;
}

export interface OfficeRoomTemplate {
  id: OfficeTemplateId;
  name: string;
  name_ar: string;
  kind: OfficeRoomKind;
  capacity: number;
  width: number;
  depth: number;
  door_side: OfficeDoorSide;
  color: OfficeRoomColor;
  equipment: OfficeEquipmentItem[];
  desks: number;
}
export interface OfficeTemplateCatalogue {
  items: OfficeRoomTemplate[];
  equipment_kinds: OfficeEquipmentKind[];
  colors: OfficeRoomColor[];
  departments: OfficeTeamDepartment[];
  units: 'metres';
}

/** GET /rooms/{id}/team: counts are real records for the linked department, null when none is linked. */
export interface OfficeRoomTeam {
  room_id: string;
  department: OfficeTeamDepartment | null;
  member_profiles: number | null;
  assistants: number | null;
}

/** Realtime event `office.layout_changed` (ids and revisions only; re-read what you show). */
export interface OfficeLayoutChangedPayload {
  action: string;
  resource_kind: 'buildings' | 'floors' | 'rooms' | 'desks' | null;
  resource_ids: string[];
  resource_count: number;
  floor_id?: string;
}
