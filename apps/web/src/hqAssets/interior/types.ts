import type { MeshBuilder, Rgb } from '../../lib/hqGeometry.ts';
import type { Local } from './local.ts';
import type { InteriorScheme, InteriorSchemeName } from './scheme.ts';
import type { StationSpec } from './furniture.ts';
import type { FigureLook } from '../../lib/hqAvatar.ts';

/** The room kinds this folder dresses. The first six match the registry's `RoomGeometry.kind`. */
export type InteriorKind =
  | 'reception' | 'meeting' | 'office' | 'lounge' | 'warehouse' | 'workshop'
  | 'customer-care' | 'operations' | 'executive'
  | 'server' | 'pantry' | 'restroom' | 'finance' | 'sales';

export const INTERIOR_KINDS: readonly InteriorKind[] = [
  'reception', 'meeting', 'office', 'lounge', 'customer-care', 'operations', 'warehouse', 'workshop', 'executive',
  'server', 'pantry', 'restroom', 'finance', 'sales',
];

/** The wall the door is in. The kits are dressed with the door in the south wall (local +z) and turned for the others. */
export type DoorSide = 'north' | 'south' | 'east' | 'west';

/** Where the room is: minimum corner on the plan, the height of its floor surface, and its size. */
export type RoomFrame = Readonly<{ x: number; y: number; z: number; width: number; depth: number }>;

export type InteriorOptions = Readonly<{
  /** Any stable string (a room id). The same seed always gives the same room. */
  seed: string;
  scheme?: InteriorSchemeName | Partial<InteriorScheme>;
  /** The room's zone colour (accent strips, brand mark, sofa piping). Defaults to the scheme accent. */
  accent?: Rgb;
  /** The floor colour of the room, so contact patches can darken it. */
  floor?: Rgb;
  /** Meeting rooms and the operations wall light up only when this is true (a persisted live state). */
  live?: boolean;
  /** `lite` drops the smallest props for far views or large campuses. Default `full`. */
  detail?: 'full' | 'lite';
  /**
   * Workstations to draw, in world coordinates. The asset invents none: no stations, no desks. A
   * figure is drawn only for a station that carries `occupantSeed`.
   */
  stations?: readonly StationSpec[];
  /** The wall the door is in (the registry's `door_side`). The room is dressed to face it. Default `south`. */
  doorSide?: DoorSide;
  /**
   * The stored participants of the live meeting held in this room (meeting rooms only), in seating order. The asset seats
   * exactly these on the chairs of the table and invents nobody; none (or no meeting) leaves every chair empty.
   */
  attendees?: readonly Attendee[];
}>;

/** One stored meeting participant: a seed (the record id) and the look to draw. */
export type Attendee = Readonly<{ seed: string; look?: FigureLook }>;

export type InteriorResult = Readonly<{ kind: InteriorKind; triangles: number }>;

export type RoomCtx = Readonly<{
  b: MeshBuilder;
  /** Local frame at the room's minimum corner, floor level. Local x east, z south, +z is the front where the door is. */
  R: Local;
  w: number;
  d: number;
  y: number;
  S: InteriorScheme;
  rnd: () => number;
  accent: Rgb;
  floor: Rgb;
  live: boolean;
  /** Fewer small props (no side racking, single carton rows); roughly half the triangles. */
  lite: boolean;
  stations: readonly StationSpec[];
  attendees: readonly Attendee[];
  frame: RoomFrame;
}>;
