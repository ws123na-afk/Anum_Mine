/**
 * Interior asset modules for the headquarters building cutaway.
 *
 *   buildInteriorRoom(builder, 'warehouse', { x, y, z, width, depth }, { seed, scheme, stations })
 *
 * Every builder is a pure function that appends primitives (the shared `MeshBuilder` format) to the
 * builder it is given and returns the triangles it added. No state, no randomness except the seed.
 *
 * Truth rules: the assets draw furniture and fixtures only. Desks come from the registry and are
 * passed in as `stations`; a person appears only at a station with `occupantSeed`. Screens are lit
 * only for a station state or a `live` flag the caller derived from persisted records.
 */
import { MeshBuilder, VERTEX_FLOATS, hex, mix, seeded, type MatId, type Rgb } from '../../lib/hqGeometry.ts';
import { workstation, type StationSpec } from './furniture.ts';
import { Local } from './local.ts';
import { standingFigure } from './figures.ts';
import { executive, lounge, meetingRoom, reception } from './roomsFront.ts';
import { warehouse, workshop } from './roomsIndustrial.ts';
import { customerCare, drawStations, openOffice, operationsRoom } from './roomsWork.ts';
import { financeOffice, pantry, restroom, salesOffice, serverRoom } from './roomsSupport.ts';
import { resolveScheme } from './scheme.ts';
import { INTERIOR_KINDS, type DoorSide, type InteriorKind, type InteriorOptions, type InteriorResult, type RoomCtx, type RoomFrame } from './types.ts';

export { INTERIOR_KINDS } from './types.ts';
export type { Attendee, DoorSide, InteriorKind, InteriorOptions, InteriorResult, RoomFrame } from './types.ts';
export { ambientParts, ambientTag, ambientTagsOf, parseAmbientTag, poseAt, REST_POSE, type AmbientKind, type AmbientMotion, type AmbientPart, type AmbientPose } from './ambient.ts';
export { SCHEMES, resolveScheme, type InteriorScheme, type InteriorSchemeName } from './scheme.ts';
export { seatedFigure, standingFigure, FIGURE_TRIANGLE_CAP, type SeatedPose, type StandingPose, type SeatedOptions, type StandingOptions, type FigureOptions } from './figures.ts';
export { workstation, SCREEN_LOOK, type StationSpec, type StationState, type StationLook } from './furniture.ts';
export { upTyre } from './roomsIndustrial.ts';

/**
 * Measured ceilings per room at its reference size (see `REFERENCE_FRAMES`), fixtures only, no
 * stations, enforced by the unit test. Stations add `STATION_TRIANGLE_CAP` each and an occupied
 * station adds `FIGURE_TRIANGLE_CAP` more. `detail: 'lite'` roughly halves the warehouse and the stations.
 */
export const INTERIOR_TRIANGLE_CAP: Readonly<Record<InteriorKind, number>> = {
  reception: 2600,
  meeting: 2200,
  office: 1500,
  lounge: 2200,
  'customer-care': 1100,
  operations: 1300,
  warehouse: 6000,
  workshop: 3000,
  executive: 3600,
  server: 2100,
  pantry: 950,
  restroom: 750,
  finance: 1550,
  sales: 1550,
};

/** One empty workstation (desk, up to three monitors, chair). */
export const STATION_TRIANGLE_CAP = 520;

/** Typical sizes in metres; the builders also work for smaller and larger frames. */
export const REFERENCE_FRAMES: Readonly<Record<InteriorKind, { width: number; depth: number }>> = {
  reception: { width: 8, depth: 6 },
  meeting: { width: 6, depth: 5 },
  office: { width: 10, depth: 7 },
  lounge: { width: 7, depth: 6 },
  'customer-care': { width: 10, depth: 7 },
  operations: { width: 10, depth: 7 },
  warehouse: { width: 10, depth: 8 },
  workshop: { width: 9, depth: 7 },
  executive: { width: 8, depth: 7 },
  server: { width: 9, depth: 7 },
  pantry: { width: 7, depth: 5.5 },
  restroom: { width: 6, depth: 4.5 },
  finance: { width: 10, depth: 7 },
  sales: { width: 10, depth: 7 },
};

/**
 * Smallest frame the full dressing fits in. A smaller frame is dressed at this size and scaled down
 * uniformly (furniture and figures shrink together), so geometry never leaves the room.
 */
export const MIN_FRAMES: Readonly<Record<InteriorKind, { width: number; depth: number }>> = {
  reception: { width: 7, depth: 6 },
  meeting: { width: 5, depth: 4.5 },
  office: { width: 8, depth: 6 },
  lounge: { width: 6, depth: 5 },
  'customer-care': { width: 8, depth: 6 },
  operations: { width: 8, depth: 6 },
  warehouse: { width: 8, depth: 7 },
  workshop: { width: 7.5, depth: 6 },
  executive: { width: 7, depth: 6 },
  server: { width: 7, depth: 5 },
  pantry: { width: 5.5, depth: 4.5 },
  restroom: { width: 4.5, depth: 3.6 },
  finance: { width: 8, depth: 6 },
  sales: { width: 8, depth: 6 },
};

const BUILDERS: Readonly<Record<InteriorKind, (c: RoomCtx) => number>> = {
  reception, meeting: meetingRoom, office: openOffice, lounge, 'customer-care': customerCare, operations: operationsRoom, warehouse, workshop, executive,
  server: serverRoom, pantry, restroom, finance: financeOffice, sales: salesOffice,
};

const DEFAULT_FLOOR = hex('#e4dccb');

/** Quarter turns a model slot gains when the room is turned from a south door to this side (0 faces +z, 1 faces -x, 2 faces -z, 3 faces +x). */
const SIDE_TURNS: Readonly<Record<DoorSide, number>> = { south: 0, west: 1, north: 2, east: 3 };

/** Where a point of the room as dressed with a south door lands when the room is turned to face `side`; offsets are from the room's middle. */
export function turnOffset(side: DoorSide, dx: number, dz: number): [number, number] {
  if (side === 'north') return [-dx, -dz];
  if (side === 'east') return [dz, -dx];
  if (side === 'west') return [-dz, dx];
  return [dx, dz];
}

/** The inverse of `turnOffset`: a point of the turned room back in the south-door frame. */
export function unturnOffset(side: DoorSide, dx: number, dz: number): [number, number] {
  if (side === 'north') return [-dx, -dz];
  if (side === 'east') return [-dz, dx];
  if (side === 'west') return [dz, -dx];
  return [dx, dz];
}

/**
 * Append one furnished room to `b`. Returns the kind and the triangles added. The room is dressed with the door in the
 * south wall and turned to the wall of `options.doorSide` (a quarter or half turn about the room's middle, so the
 * footprint stays the frame). Stations are given in world coordinates and are mapped into the turned frame and back.
 */
export function buildInteriorRoom(b: MeshBuilder, kind: InteriorKind, frame: RoomFrame, options: InteriorOptions): InteriorResult {
  const before = b.triangleCount;
  const side = options.doorSide ?? 'south';
  if (side === 'south') {
    dress(b, kind, frame, options);
  } else {
    const swap = side === 'east' || side === 'west';
    const cw = swap ? frame.depth : frame.width, cd = swap ? frame.width : frame.depth;
    const cx = frame.x + frame.width / 2, cz = frame.z + frame.depth / 2;
    const canon: RoomFrame = { x: cx - cw / 2, y: frame.y, z: cz - cd / 2, width: cw, depth: cd };
    const stations = options.stations?.map((st) => {
      const [qx, qz] = unturnOffset(side, st.x - cx, st.z - cz);
      return { ...st, x: cx + qx, z: cz + qz, rot: (((st.rot ?? 0) - SIDE_TURNS[side]) % 4 + 4) % 4 };
    });
    const temp = new MeshBuilder();
    dress(temp, kind, canon, { ...options, stations, doorSide: undefined });
    appendTurned(b, temp, cx, cz, side);
  }
  return { kind, triangles: b.triangleCount - before };
}

/** Copy `src` into `dst`, turned about the vertical axis through (cx, cz) to face `side`. Tags, classes and light halos come along. */
function appendTurned(dst: MeshBuilder, src: MeshBuilder, cx: number, cz: number, side: DoorSide): void {
  const v = src.toFloat32();
  const tags = src.triangleTags();
  const at = (i: number): number[] => { const [x, z] = turnOffset(side, v[i] - cx, v[i + 2] - cz); return [cx + x, v[i + 1], cz + z]; };
  for (let i = 0; i < v.length; i += VERTEX_FLOATS * 3) {
    const j = i + VERTEX_FLOATS, k = i + 2 * VERTEX_FLOATS;
    dst.mat = v[i + 10] as MatId;
    dst.tag = tags[i / (VERTEX_FLOATS * 3)] ?? null;
    dst.tri(at(i), at(j), at(k), [v[i + 6], v[i + 7], v[i + 8]], v[i + 9]);
  }
  dst.tag = null;
  for (const slot of src.slots) {
    const [x, z] = turnOffset(side, slot.x - cx, slot.z - cz);
    dst.slots.push({ ...slot, x: cx + x, z: cz + z, rot: (slot.rot + SIDE_TURNS[side]) % 4 });
  }
  for (let h = 0; h < src.halos.length; h += 8) {
    const [x, z] = turnOffset(side, src.halos[h] - cx, src.halos[h + 2] - cz);
    dst.halos.push(cx + x, src.halos[h + 1], cz + z, ...src.halos.slice(h + 3, h + 8));
  }
  for (const sc of src.screens) {
    const p: number[] = [];
    for (let i = 0; i < 12; i += 3) { const [x, z] = turnOffset(side, sc.p[i] - cx, sc.p[i + 2] - cz); p.push(cx + x, sc.p[i + 1], cz + z); }
    dst.screens.push({ ...sc, p });
  }
}

function dress(b: MeshBuilder, kind: InteriorKind, frame: RoomFrame, options: InteriorOptions): void {
  const min = MIN_FRAMES[kind];
  if (frame.width < min.width || frame.depth < min.depth) {
    // Dress the room at its minimum size, then shrink it uniformly into the real frame.
    const w2 = Math.max(frame.width, min.width), d2 = Math.max(frame.depth, min.depth);
    const s = Math.min(frame.width / w2, frame.depth / d2);
    const big: RoomFrame = { x: frame.x, y: frame.y, z: frame.z, width: w2, depth: d2 };
    const stations = options.stations?.map((st) => ({ ...st, x: frame.x + (st.x - frame.x) / s, z: frame.z + (st.z - frame.z) / s }));
    const temp = new MeshBuilder();
    drawRoom(temp, kind, big, { ...options, stations });
    appendScaled(b, temp, frame, s);
  } else {
    drawRoom(b, kind, frame, options);
  }
}

function drawRoom(b: MeshBuilder, kind: InteriorKind, frame: RoomFrame, options: InteriorOptions): void {
  const S = resolveScheme(options.scheme);
  const accent: Rgb = options.accent ?? S.accent;
  const R = new Local(b, frame.x, frame.z, 0, frame.y);
  const ctx: RoomCtx = {
    b, R, w: frame.width, d: frame.depth, y: frame.y, S, rnd: seeded(`interior:${kind}:${options.seed}`),
    accent, floor: options.floor ?? DEFAULT_FLOOR, live: options.live === true, lite: options.detail === 'lite',
    stations: options.stations ?? [], attendees: kind === 'meeting' ? options.attendees ?? [] : [], frame,
  };
  BUILDERS[kind](ctx);
  // A desk is a registry record: a room whose kit does not place desks itself still shows each one it is given.
  if (kind === 'warehouse' || kind === 'workshop') drawStations(ctx, { plant: false, chairTone: mix(ctx.S.fabric, hex('#14181d'), 0.6) });
  // A person assigned to a post in a room whose kit draws no workstation stands at the stored post (the reception kit stands its own behind the counter).
  const own = STATIONS_DRAWN[kind] ?? 0;
  ctx.stations.forEach((st, i) => {
    if (i < own || !st.occupantSeed) return;
    standingFigure(b, st.x, frame.y, st.z, { seed: st.occupantSeed, rot: ((st.rot ?? 0) + 2) % 4, pose: 'neutral', headset: st.headset, look: st.look });
  });
}

/** How many of the given stations each kit draws itself (with a person seated or standing at it); the rest stand at their post. */
const STATIONS_DRAWN: Readonly<Partial<Record<InteriorKind, number>>> = {
  office: Infinity, 'customer-care': Infinity, operations: Infinity, warehouse: Infinity, workshop: Infinity, server: Infinity, finance: Infinity, sales: Infinity,
  reception: Infinity, executive: 1,
};

/** Copy `src` into `dst`, scaled by `s` about the frame's minimum corner at floor level. */
function appendScaled(dst: MeshBuilder, src: MeshBuilder, frame: RoomFrame, s: number): void {
  const v = src.toFloat32();
  const tags = src.triangleTags();
  const at = (i: number): number[] => [frame.x + (v[i] - frame.x) * s, frame.y + (v[i + 1] - frame.y) * s, frame.z + (v[i + 2] - frame.z) * s];
  for (let i = 0; i < v.length; i += VERTEX_FLOATS * 3) {
    const j = i + VERTEX_FLOATS, k = i + 2 * VERTEX_FLOATS;
    // Keep the class of each triangle (wood, tile and so on) and the tag, so scaled rooms split like the others.
    dst.mat = v[i + 10] as MatId;
    dst.tag = tags[i / (VERTEX_FLOATS * 3)] ?? null;
    dst.tri(at(i), at(j), at(k), [v[i + 6], v[i + 7], v[i + 8]], v[i + 9]);
  }
  dst.tag = null;
  for (const slot of src.slots) dst.slots.push({ ...slot, x: frame.x + (slot.x - frame.x) * s, y: frame.y + (slot.y - frame.y) * s, z: frame.z + (slot.z - frame.z) * s, width: slot.width * s });
  for (let h = 0; h < src.halos.length; h += 8) {
    dst.halos.push(frame.x + (src.halos[h] - frame.x) * s, frame.y + (src.halos[h + 1] - frame.y) * s, frame.z + (src.halos[h + 2] - frame.z) * s, src.halos[h + 3] * s, ...src.halos.slice(h + 4, h + 8));
  }
  for (const sc of src.screens) dst.screens.push({ ...sc, p: sc.p.map((v, i) => [frame.x, frame.y, frame.z][i % 3] + (v - [frame.x, frame.y, frame.z][i % 3]) * s) });
}

/** A standalone mesh in the same shape as `SceneMesh`, for tests, previews and caching. */
export function buildInteriorMesh(kind: InteriorKind, frame: RoomFrame, options: InteriorOptions): { vertices: Float32Array; bounds: ReturnType<MeshBuilder['bounds']>; triangles: number } {
  const b = new MeshBuilder();
  buildInteriorRoom(b, kind, frame, options);
  return { vertices: b.toFloat32(), bounds: b.bounds(), triangles: b.triangleCount };
}

/**
 * Map a registry room kind (and optionally its name) to an interior. Names only choose the
 * decoration, they never produce data. Returns null for a kind this folder does not dress.
 */
export function interiorKindFor(kind: string, name = ''): InteriorKind | null {
  const n = name.toLowerCase();
  if (kind === 'office') {
    if (/(server|data cent|datacent|network|infrastructure|engineering|automation|ai lab|deployment|incident|it room)/.test(n)) return 'server';
    if (/(customer|care|support|help|service|ticket|message)/.test(n)) return 'customer-care';
    if (/(operation|dispatch|control|command|security)/.test(n)) return 'operations';
    if (/(exec|ceo|director|board|owner|personal)/.test(n)) return 'executive';
    if (/(financ|wealth|account|audit|treasury|invest|procure)/.test(n)) return 'finance';
    if (/(sales|marketing|commerce|retail|marketplace|order|e-?com)/.test(n)) return 'sales';
    if (/(pantry|kitchen|cafe|canteen|break room)/.test(n)) return 'pantry';
    if (/(restroom|toilet|wc\b|washroom|bathroom)/.test(n)) return 'restroom';
    return 'office';
  }
  if (kind === 'lounge' && /(pantry|kitchen|cafe|canteen)/.test(n)) return 'pantry';
  // The command centre is a kind of its own and always gets the operations room with its video wall, whatever it is called.
  if (kind === 'command') return 'operations';
  if (kind === 'reception' || kind === 'meeting' || kind === 'lounge' || kind === 'warehouse' || kind === 'workshop') return kind;
  return (INTERIOR_KINDS as readonly string[]).includes(kind) ? (kind as InteriorKind) : null;
}

/**
 * A neat default grid of station positions for a frame, for previews and for rooms that have desks
 * without saved geometry. `ids` are the registry desk ids; one station per id, none invented.
 */
export function layoutStations(frame: RoomFrame, ids: readonly string[], base: Partial<StationSpec> = {}): StationSpec[] {
  const cols = Math.max(1, Math.floor((frame.width - 4.1) / 1.9) + 1);
  const rows = Math.max(1, Math.floor((frame.depth - 3.75) / 2.6) + 1);
  const out: StationSpec[] = [];
  const x0 = frame.x + 1.1 + (frame.width - 2.5 - 1.5 - (cols - 1) * 1.9) / 2 + 0.75;
  for (let i = 0; i < ids.length && i < cols * rows; i++) {
    out.push({ ...base, id: ids[i], x: x0 + (i % cols) * 1.9, z: frame.z + 2.3 + Math.floor(i / cols) * 2.6 });
  }
  return out;
}

export { workstation as drawWorkstation };
