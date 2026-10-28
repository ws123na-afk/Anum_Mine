/**
 * ANUM MINE "Add Green Concrete" contract, version 2 (each person has their own working hours; there is no night shift).
 * Server: services/api/anum_api/office_green_concrete.py and office_green_concrete_api.py.
 * Spec: docs/ANUM_MINE_GREEN_CONCRETE.md.
 *
 * Flow: preview (exactly what would be created and skipped, with a payload hash) -> confirm (click only,
 * `workspace:manage`). The preview stores nothing. Every person becomes a reviewed PAUSED agent with no tool attached.
 */
import type { AvatarStyle } from './avatar.js';

export const OFFICE_GREEN_CONCRETE_VERSION = 2 as const;

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
  /** Always null for a new seat: older Day or Night seats are only read, never written. */
  shift: OfficeShift | null; seat_order: number; facing: OfficeFacing; seat_kind: OfficeSeatKind; table_group: string;
  /** The person's working hours, "HH:MM" in the building's time zone. */
  work_start: string; work_end: string;
  action: GreenAction; reason: GreenSkipReason | null;
}
export interface GreenPerson {
  key: string; name: string; role: { en: string; ar: string }; seat_kind: OfficeSeatKind; work_start: string; work_end: string;
  seat_order: number; desk_id: string;
  agent_id: string | null; agent_action: 'create' | 'exists'; seat_action: GreenSeatAction; seat_reason: GreenSeatReason | null;
  /** The look (masculine, feminine, neutral) a NEW agent is created with: only what the owner chose, never read from a name. Null when none is sent or the agent already exists (an existing agent is never changed). */
  appearance?: AvatarStyle | null;
}
export interface GreenCounts {
  create: { buildings: number; floors: number; rooms: number; desks: number; agents: number };
  skip: { buildings: number; floors: number; rooms: number; desks: number; agents: number };
  seat: number;
}
/** The working hours of the plan, grouped by window (the owner's answer, not defaults). */
export interface GreenHours {
  timezone: string; night_shift: false;
  windows: { start: string; end: string; people: string[] }[];
}
export interface GreenConcretePreview {
  version: 2; catalog_version: number; workspace_id: string; timezone: string; payload_hash: string; can_confirm: boolean;
  building: GreenRecord & { timezone: string };
  floor: GreenRecord & { level: number; width_m: number; depth_m: number };
  room: GreenRecord & { kind: 'command'; capacity: number; x: number; z: number; width: number; depth: number; door_side: string };
  seats: GreenSeat[]; people: GreenPerson[]; counts: GreenCounts; status: 'new' | 'partial' | 'complete';
  agent_slots_left: number; hours: GreenHours; monthly_cap_usd: number;
  /** Desks of an earlier build that still have no working hours of their own. The action never changes them. */
  older_seats: string[];
  rules: { creates_agents: true; agents_start_paused: true; attaches_tools: false; changes_existing: false; infers_gender_or_ethnicity: false };
}
export interface GreenConcreteConfirm {
  payload_hash: string; input_mode: 'click'; timezone: string;
  /** The look for every person not listed in `appearance` (the dialog sends neutral). Part of the hash. */
  default_look?: AvatarStyle;
  /** The owner's choice per person key. Part of the hash. */
  appearance?: Record<string, AvatarStyle>;
}
/** The looks the preview and the confirm are made for. */
export interface GreenLooks { default_look: AvatarStyle; appearance: Record<string, AvatarStyle> }
export type GreenConcreteOutcome = 'created' | 'partial' | 'already_set_up';
export interface GreenConcreteResult {
  version: 2; outcome: GreenConcreteOutcome; payload_hash: string; timezone: string;
  created: { buildings: number; floors: number; rooms: number; desks: number; agents: number; seated: number };
  message: string; failed: string[]; agents_started: 0;
}
