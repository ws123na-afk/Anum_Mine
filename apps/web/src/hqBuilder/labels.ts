// The fixed vocabularies of the office builder (docs/ANUM_MINE_OFFICE_REGISTRY_BACKEND.md), typed
// from the shared contract so a new server value is a compile error here, not a blank label.
import type {
  OfficeDoorSide, OfficeEquipmentKind, OfficeRoomColor, OfficeRoomKind, OfficeTeamDepartment,
} from '@anum/contracts';

export const ROOM_KINDS: readonly OfficeRoomKind[] = ['office', 'meeting', 'reception', 'workshop', 'warehouse', 'lounge', 'command'];
export const DESK_KINDS: readonly OfficeRoomKind[] = ['office', 'workshop', 'warehouse', 'command'];
export const ROOM_COLORS: readonly OfficeRoomColor[] = ['slate', 'forest', 'brass', 'sapphire', 'amethyst', 'crimson', 'teal', 'sand'];
export const DOOR_SIDES: readonly OfficeDoorSide[] = ['north', 'south', 'east', 'west'];
export const DEPARTMENTS: readonly OfficeTeamDepartment[] = [
  'sales', 'support', 'operations', 'finance', 'tyre_engineering',
  'marketing', 'technology', 'research', 'legal', 'compliance', 'security', 'hr', 'procurement', 'executive',
  'custom',
];
export const EQUIPMENT_KINDS: readonly OfficeEquipmentKind[] = [
  'monitor', 'display', 'whiteboard', 'projector', 'conference_phone', 'video_conference_kit',
  'printer', 'server_rack', 'network_switch', 'workbench', 'shelving', 'locker', 'charging_station', 'first_aid_kit',
];
export const MAX_EQUIPMENT_KINDS = 20;
export const SNAP_STEPS: readonly number[] = [0.5, 1, 2, 0];
