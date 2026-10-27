/**
 * ANUM MINE "Add Green Concrete" contract, version 1.
 * Server: services/api/anum_api/office_green_concrete.py and office_green_concrete_api.py.
 * Spec: docs/ANUM_MINE_GREEN_CONCRETE.md.
 *
 * Flow: preview (exactly what would be created and skipped, with a payload hash) -> confirm (click only,
 * `workspace:manage`). The preview stores nothing. Every person becomes a reviewed PAUSED agent with no tool attached.
 */
export const OFFICE_GREEN_CONCRETE_VERSION = 1 as const;

export type OfficeShift = 'day' | 'night';
export type OfficeSeatKind = 'team' | 'manager';
export type OfficeFacing = 'north' | 'south' | 'east' | 'west';
export type GreenAction = 'create' | 'skip';
export type GreenSkipReason = 'exists' | 'name_in_use' | 'level_in_use' | 'parent_unavailable';
export type GreenSeatAction = 'seat' | 'skip';
export type GreenSeatReason = 'seated' | 'desk_taken' | 'agent_elsewhere' | 'parent_unavailable';

export interface GreenName { en: string; ar: string | null }
export interface GreenRecord { id: string; name: GreenName; action: GreenAction; reason: GreenSkipReason | null }
export interface GreenSeat {
  desk_id: string; label: string; person: string; pos_x: number; pos_z: number;
  shift: OfficeShift | null; seat_order: number; facing: OfficeFacing; seat_kind: OfficeSeatKind; table_group: string;
  action: GreenAction; reason: GreenSkipReason | null;
}
export interface GreenPerson {
  key: string; name: string; role: { en: string; ar: string }; shift: OfficeShift | null; seat_order: number; desk_id: string;
  agent_id: string | null; agent_action: 'create' | 'exists'; seat_action: GreenSeatAction; seat_reason: GreenSeatReason | null;
}
export interface GreenCounts {
  create: { buildings: number; floors: number; rooms: number; desks: number; agents: number };
  skip: { buildings: number; floors: number; rooms: number; desks: number; agents: number };
  seat: number;
}
export interface GreenShifts {
  day: { start: string; end: string }; night: { start: string; end: string };
  /** True while the building has no stored hours: the hours shown are defaults the owner must confirm. */
  defaults: boolean; note: string;
}
export interface GreenConcretePreview {
  version: 1; catalog_version: number; workspace_id: string; timezone: string; payload_hash: string; can_confirm: boolean;
  building: GreenRecord & { timezone: string };
  floor: GreenRecord & { level: number; width_m: number; depth_m: number };
  room: GreenRecord & { kind: 'command'; capacity: number; x: number; z: number; width: number; depth: number; door_side: string };
  seats: GreenSeat[]; people: GreenPerson[]; counts: GreenCounts; status: 'new' | 'partial' | 'complete';
  agent_slots_left: number; shifts: GreenShifts; monthly_cap_usd: number;
  rules: { creates_agents: true; agents_start_paused: true; attaches_tools: false; changes_existing: false; infers_gender_or_ethnicity: false };
}
export interface GreenConcreteConfirm { payload_hash: string; input_mode: 'click'; timezone: string }
export type GreenConcreteOutcome = 'created' | 'partial' | 'already_set_up';
export interface GreenConcreteResult {
  version: 1; outcome: GreenConcreteOutcome; payload_hash: string; timezone: string;
  created: { buildings: number; floors: number; rooms: number; desks: number; agents: number; seated: number };
  message: string; failed: string[]; agents_started: 0;
}
