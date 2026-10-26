/**
 * The shell of one room on a cut-away floor: the floor finish for its kind, skirting, the walls with a glass
 * partition above a low solid base, the door opening in the wall the registry names (`door_side`), a door frame
 * and a parked sliding door, a name plate over the door, and the ceiling light panels that hang from the wall
 * tops. Scenery only: nothing here is data, and the plate carries the room name only through the sign atlas cell
 * the caller gives it (the real name, drawn by the renderer).
 */
import { MAT, hex, mix, scale, type MatId, type MeshBuilder, type Rgb } from '../../lib/hqGeometry.ts';
import type { SignCell } from '../../lib/hqTextures.ts';
import { WARM } from '../../lib/hqProps.ts';
import { ambientTag } from './ambient.ts';
import type { DoorSide, InteriorKind } from './types.ts';

export const WALL_HEIGHT = 3.0;
const WALL_T = 0.1;
const SOLID = 1.0;
const SKIRT = 0.1;
const HEADER = 2.15;
export const MAX_HALOS_PER_ROOM = 4;

type Pattern = 'tile' | 'plank' | 'slab' | 'parquet' | 'check';
export type Finish = Readonly<{ mat: MatId; base: Rgb; joint: Rgb; pattern: Pattern; step: number; border?: Rgb }>;

const BRASS = hex('#c9a24a');
/** Floor finish per interior kind (warm, like the concept renders: timber, stone and carpet, concrete only where work is rough). */
export const FINISH: Readonly<Record<InteriorKind | 'default', Finish>> = {
  default: { mat: MAT.tile, base: hex('#d0bd9c'), joint: hex('#b8a380'), pattern: 'tile', step: 1.5 },
  office: { mat: MAT.tile, base: hex('#cdb999'), joint: hex('#b5a07f'), pattern: 'tile', step: 1.5 },
  finance: { mat: MAT.tile, base: hex('#c9b595'), joint: hex('#b19c7b'), pattern: 'tile', step: 1.5 },
  sales: { mat: MAT.tile, base: hex('#d1bc97'), joint: hex('#b8a27c'), pattern: 'tile', step: 1.5 },
  'customer-care': { mat: MAT.tile, base: hex('#c8bba3'), joint: hex('#b0a285'), pattern: 'tile', step: 1.5 },
  operations: { mat: MAT.tile, base: hex('#b9bec2'), joint: hex('#a2a8ad'), pattern: 'tile', step: 1.5 },
  meeting: { mat: MAT.wood, base: hex('#b98d60'), joint: hex('#9c7449'), pattern: 'plank', step: 0.42 },
  lounge: { mat: MAT.wood, base: hex('#b6905f'), joint: hex('#9a7647'), pattern: 'plank', step: 0.42 },
  reception: { mat: MAT.tile, base: hex('#e6d6b8'), joint: hex('#c9b690'), pattern: 'slab', step: 2.0, border: BRASS },
  executive: { mat: MAT.wood, base: hex('#7a5233'), joint: hex('#5a3a22'), pattern: 'parquet', step: 0.6 },
  warehouse: { mat: MAT.concrete, base: hex('#a9aaa5'), joint: hex('#8f908b'), pattern: 'slab', step: 3.0 },
  workshop: { mat: MAT.concrete, base: hex('#9fa2a2'), joint: hex('#868a8b'), pattern: 'slab', step: 3.0 },
  server: { mat: MAT.tile, base: hex('#7f8a93'), joint: hex('#626c75'), pattern: 'tile', step: 0.6 },
  pantry: { mat: MAT.tile, base: hex('#d9cfbd'), joint: hex('#c4b79f'), pattern: 'check', step: 0.6 },
  restroom: { mat: MAT.tile, base: hex('#cfd9dd'), joint: hex('#b3c1c7'), pattern: 'tile', step: 0.6 },
};

export type ShellOptions = Readonly<{
  x: number; z: number; width: number; depth: number; y: number;
  kind: InteriorKind | null;
  /** The kind of the registry room, for the finish of a room no kit dresses. */
  registryKind: string;
  accent: Rgb;
  selected: boolean;
  /** A floor below the active one: the slab and the walls only, no finish, no lights. */
  ghost: boolean;
  doorSide: DoorSide;
  /** The sign atlas cell with this room's name; null draws no plate. */
  plate: SignCell | null;
}>;

export type ShellResult = Readonly<{ floor: Rgb; finish: Finish; doorWidth: number }>;

/** The door width for a wall of this length: 1.2 m where it fits, narrower in a tiny room. */
export function doorWidthFor(length: number): number {
  return Math.max(0.9, Math.min(1.2, length * 0.3));
}

/** Corners and direction of the door wall: its centre, the outward normal and the along-wall axis (unit). */
export function doorWall(o: Pick<ShellOptions, 'x' | 'z' | 'width' | 'depth' | 'doorSide'>): Readonly<{ cx: number; cz: number; nx: number; nz: number; ax: number; az: number; length: number }> {
  switch (o.doorSide) {
    case 'north': return { cx: o.x + o.width / 2, cz: o.z, nx: 0, nz: -1, ax: 1, az: 0, length: o.width };
    case 'east': return { cx: o.x + o.width, cz: o.z + o.depth / 2, nx: 1, nz: 0, ax: 0, az: 1, length: o.depth };
    case 'west': return { cx: o.x, cz: o.z + o.depth / 2, nx: -1, nz: 0, ax: 0, az: 1, length: o.depth };
    default: return { cx: o.x + o.width / 2, cz: o.z + o.depth, nx: 0, nz: 1, ax: 1, az: 0, length: o.width };
  }
}

export function drawRoomShell(opaque: MeshBuilder, glass: MeshBuilder, amb: MeshBuilder, o: ShellOptions): ShellResult {
  const { x, z, width: w, depth: d, y, ghost, selected, accent } = o;
  const finish = FINISH[o.kind ?? (o.registryKind === 'warehouse' || o.registryKind === 'workshop' || o.registryKind === 'lounge' || o.registryKind === 'meeting' || o.registryKind === 'reception' ? (o.registryKind as InteriorKind) : 'default')] ?? FINISH.default;
  const base = selected ? mix(finish.base, accent, 0.4) : ghost ? scale(finish.base, 0.8) : finish.base;
  // The floor: one slab with the finish, lit a little so it reads at night.
  opaque.withMat(finish.mat, () => opaque.box(x, y, z, w, 0.06, d, base, { skip: ['bottom'], shade: false, emissive: ghost ? 0.05 : selected ? 0.9 : 0.55 }));
  if (!ghost && w > 3 && d > 3) drawFinishLines(opaque, o, finish, base);
  // The front strip of the zone colour along the door wall's inner edge keeps each zone readable from far away.
  const dw = doorWall(o);
  const door = doorWidthFor(dw.length);
  drawWalls(opaque, glass, o, door);
  drawDoorFrame(opaque, o, dw, door);
  if (!ghost) {
    if (o.plate) drawPlate(opaque, o, dw, o.plate);
    drawCeilingLights(opaque, amb, o);
  }
  return { floor: base, finish, doorWidth: door };
}

function drawFinishLines(b: MeshBuilder, o: ShellOptions, f: Finish, base: Rgb): void {
  const { x, z, width: w, depth: d, y } = o;
  const top = y + 0.062;
  const joint = f.joint;
  b.withMat(f.mat, () => {
    if (f.pattern === 'plank') {
      for (let gx = x + f.step; gx < x + w - 0.1; gx += f.step) b.plane(gx, top, z, 0.02, d, joint, 0.4);
      for (let gz = z + 2.1; gz < z + d - 0.1; gz += 2.1) b.plane(x, top, gz, w, 0.02, scale(joint, 0.9), 0.4);
    } else if (f.pattern === 'parquet') {
      for (let gx = x + f.step; gx < x + w - 0.1; gx += f.step) b.plane(gx, top, z, 0.03, d, joint, 0.4);
      for (let gz = z + f.step; gz < z + d - 0.1; gz += f.step) b.plane(x, top, gz, w, 0.03, joint, 0.4);
      // A darker border band round the room.
      b.plane(x + 0.35, top + 0.002, z + 0.35, w - 0.7, 0.05, scale(base, 0.7), 0.4);
      b.plane(x + 0.35, top + 0.002, z + d - 0.4, w - 0.7, 0.05, scale(base, 0.7), 0.4);
    } else if (f.pattern === 'check') {
      for (let gx = x; gx < x + w - 0.05; gx += f.step) for (let gz = z; gz < z + d - 0.05; gz += f.step) {
        if ((Math.round((gx - x) / f.step) + Math.round((gz - z) / f.step)) % 2 === 0) b.plane(gx, top, gz, Math.min(f.step, x + w - gx), Math.min(f.step, z + d - gz), scale(base, 0.9), 0.45);
      }
    } else {
      for (let gx = x + f.step; gx < x + w - 0.1; gx += f.step) b.plane(gx, top, z, 0.03, d, joint, 0.4);
      for (let gz = z + f.step; gz < z + d - 0.1; gz += f.step) b.plane(x, top, gz, w, 0.03, joint, 0.4);
    }
    if (f.border) {
      const t = 0.05;
      b.plane(x + 0.3, top + 0.003, z + 0.3, w - 0.6, t, f.border, 0.7);
      b.plane(x + 0.3, top + 0.003, z + d - 0.3 - t, w - 0.6, t, f.border, 0.7);
      b.plane(x + 0.3, top + 0.003, z + 0.3, t, d - 0.6, f.border, 0.7);
      b.plane(x + w - 0.3 - t, top + 0.003, z + 0.3, t, d - 0.6, f.border, 0.7);
    }
    if (o.kind === 'warehouse' || o.kind === 'workshop') {
      // Rough floors get a yellow safety edge.
      const yel = hex('#e8d36a');
      b.plane(x + 0.15, top + 0.003, z + 0.15, w - 0.3, 0.08, yel, 0.35);
      b.plane(x + 0.15, top + 0.003, z + d - 0.23, w - 0.3, 0.08, yel, 0.35);
    }
  });
}

/** One wall segment: skirting and a solid base to 1.0 m, glass above with mullions, and a top rail in the zone colour. */
function wallSegment(opaque: MeshBuilder, glass: MeshBuilder, o: ShellOptions, sx: number, sz: number, sw: number, sd: number): void {
  const { y, ghost, selected, accent } = o;
  opaque.withMat(MAT.concrete, () => opaque.box(sx, y + 0.06, sz, sw, SOLID, sd, hex('#f0e9da'), { emissive: 0.3, grad: 0.2 }));
  // Skirting proud of the wall on both faces.
  opaque.box(sx - 0.012, y + 0.06, sz - 0.012, sw + 0.024, SKIRT, sd + 0.024, hex('#9aa5ad'));
  glass.box(sx, y + 0.06 + SOLID, sz, sw, WALL_HEIGHT - 0.06 - SOLID, sd, hex('#9fd4e6'), { shade: false });
  opaque.box(sx, y + WALL_HEIGHT, sz, sw, 0.06, sd, accent, { emissive: selected ? 0.9 : 0.4, shade: false });
  if (!ghost) {
    const long = Math.max(sw, sd);
    for (let m = 0.9; m < long - 0.3; m += 1.4) {
      if (sw > sd) opaque.box(sx + m, y + 0.06 + SOLID, sz - 0.01, 0.05, WALL_HEIGHT - 0.06 - SOLID, sd + 0.02, hex('#59656f'));
      else opaque.box(sx - 0.01, y + 0.06 + SOLID, sz + m, sw + 0.02, WALL_HEIGHT - 0.06 - SOLID, 0.05, hex('#59656f'));
    }
  }
}

function drawWalls(opaque: MeshBuilder, glass: MeshBuilder, o: ShellOptions, door: number): void {
  const { x, z, width: w, depth: d } = o;
  const t = WALL_T;
  // Each of the four walls; the one the door is in leaves a gap.
  const walls: Array<{ side: DoorSide; x: number; z: number; w: number; d: number }> = [
    { side: 'north', x, z, w, d: t },
    { side: 'south', x, z: z + d - t, w, d: t },
    { side: 'west', x, z, w: t, d },
    { side: 'east', x: x + w - t, z, w: t, d },
  ];
  for (const wall of walls) {
    if (wall.side !== o.doorSide) { wallSegment(opaque, glass, o, wall.x, wall.z, wall.w, wall.d); continue; }
    if (wall.side === 'north' || wall.side === 'south') {
      const left = (wall.w - door) / 2;
      wallSegment(opaque, glass, o, wall.x, wall.z, left, wall.d);
      wallSegment(opaque, glass, o, wall.x + left + door, wall.z, wall.w - left - door, wall.d);
    } else {
      const left = (wall.d - door) / 2;
      wallSegment(opaque, glass, o, wall.x, wall.z, wall.w, left);
      wallSegment(opaque, glass, o, wall.x, wall.z + left + door, wall.w, wall.d - left - door);
    }
  }
}

/** Door frame (two posts and a header), a threshold in brass and a sliding door parked beside the opening. */
function drawDoorFrame(opaque: MeshBuilder, o: ShellOptions, dw: ReturnType<typeof doorWall>, door: number): void {
  const { y, accent, ghost } = o;
  const frame = hex('#3c4852');
  const horizontal = dw.ax === 1;
  const inwardX = -dw.nx, inwardZ = -dw.nz;
  // Wall centre line sits half a wall thickness inside the outer face.
  const wx = dw.cx + inwardX * (WALL_T / 2), wz = dw.cz + inwardZ * (WALL_T / 2);
  const hx = door / 2;
  if (horizontal) {
    for (const s of [-1, 1]) opaque.box(wx + s * hx - 0.03, y + 0.06, wz - WALL_T / 2 - 0.01, 0.06, HEADER - 0.06, WALL_T + 0.02, frame);
    opaque.box(wx - hx - 0.03, y + HEADER, wz - WALL_T / 2 - 0.01, door + 0.06, WALL_HEIGHT - HEADER, WALL_T + 0.02, hex('#eef1f3'), { emissive: 0.2 });
    opaque.box(wx - hx, y + 0.06, wz - WALL_T / 2, door, 0.012, WALL_T, BRASS, { emissive: ghost ? 0 : 0.5 });
  } else {
    for (const s of [-1, 1]) opaque.box(wx - WALL_T / 2 - 0.01, y + 0.06, wz + s * hx - 0.03, WALL_T + 0.02, HEADER - 0.06, 0.06, frame);
    opaque.box(wx - WALL_T / 2 - 0.01, y + HEADER, wz - hx - 0.03, WALL_T + 0.02, WALL_HEIGHT - HEADER, door + 0.06, hex('#eef1f3'), { emissive: 0.2 });
    opaque.box(wx - WALL_T / 2, y + 0.06, wz - hx, WALL_T, 0.012, door, BRASS, { emissive: ghost ? 0 : 0.5 });
  }
  if (ghost) return;
  // Header accent strip, then the sliding leaf parked on the inside to one side of the opening.
  if (horizontal) {
    opaque.box(wx - hx, y + HEADER - 0.03, wz - WALL_T / 2, door, 0.03, WALL_T, accent, { emissive: 0.7, shade: false });
    const leaf = Math.min(door * 0.9, 1.0);
    opaque.box(wx - hx, y + 0.1, wz + inwardZ * 0.12 - 0.015, leaf, 2.0, 0.03, hex('#9fd4e6'), { emissive: 0.15, shade: false });
  } else {
    opaque.box(wx - WALL_T / 2, y + HEADER - 0.03, wz - hx, WALL_T, 0.03, door, accent, { emissive: 0.7, shade: false });
    const leaf = Math.min(door * 0.9, 1.0);
    opaque.box(wx + inwardX * 0.12 - 0.015, y + 0.1, wz - hx, 0.03, 2.0, leaf, hex('#9fd4e6'), { emissive: 0.15, shade: false });
  }
}

/** A name plate over the door, on the outside of the wall, lettered through the sign atlas cell. */
function drawPlate(b: MeshBuilder, o: ShellOptions, dw: ReturnType<typeof doorWall>, cell: SignCell): void {
  const w = 1.6, h = 0.4;
  const y0 = o.y + 2.2;
  // Right-hand vector as seen from outside: (nz, 0, -nx).
  const rx = dw.nz, rz = -dw.nx;
  const px = dw.cx + dw.nx * 0.045, pz = dw.cz + dw.nz * 0.045;
  const hw = w / 2;
  const corner = (s: number, yy: number): number[] => [px + rx * hw * s, yy, pz + rz * hw * s];
  // Plate body: a dark backing box.
  const back = hex('#0e1b26');
  const bx = Math.abs(dw.nx) === 1 ? 0.07 : w + 0.08, bz = Math.abs(dw.nz) === 1 ? 0.07 : w + 0.08;
  b.box(dw.cx + dw.nx * 0.035 - bx / 2, y0 - 0.04, dw.cz + dw.nz * 0.035 - bz / 2, bx, h + 0.08, bz, back);
  b.withMat(MAT.sign, () => {
    const q0 = corner(-1, y0), q1 = corner(1, y0), q2 = corner(1, y0 + h), q3 = corner(-1, y0 + h);
    const off = 0.04;
    const lift = (p: number[]): number[] => [p[0] + dw.nx * off, p[1], p[2] + dw.nz * off];
    b.quadUV(lift(q0), lift(q1), lift(q2), lift(q3), [cell.u0, cell.v0], [cell.u1, cell.v0], [cell.u1, cell.v1], [cell.u0, cell.v1], [1, 1, 1], 0.9);
  });
}

/** Ceiling light panels hung under the cut: a grid per room, a few warm halos, and a glint that sweeps along each panel. */
function drawCeilingLights(opaque: MeshBuilder, amb: MeshBuilder, o: ShellOptions): void {
  const { x, z, width: w, depth: d, y } = o;
  const cols = Math.max(1, Math.floor(w / 2.7)), rows = Math.max(1, Math.floor(d / 2.7));
  const pw = Math.min(0.9, (w / cols) * 0.4), pd = Math.min(0.42, (d / rows) * 0.24);
  const py = y + 2.82;
  const lamp = hex('#fff1d0');
  let halos = 0;
  for (let i = 0; i < cols; i++) {
    for (let j = 0; j < rows; j++) {
      const cx = x + ((i + 0.5) * w) / cols, cz = z + ((j + 0.5) * d) / rows;
      opaque.box(cx - pw / 2, py, cz - pd / 2, pw, 0.05, pd, lamp, { emissive: 0.95, shade: false, skip: ['bottom'] });
      // A pale diffuser frame: from the cut-away camera the panel reads as a lit fixture, not a dark lid.
      opaque.box(cx - pw / 2 - 0.03, py + 0.05, cz - pd / 2 - 0.03, pw + 0.06, 0.015, pd + 0.06, hex('#f4ead0'), { skip: ['bottom'], emissive: 0.8, shade: false });
      for (const sx of [-1, 1]) opaque.box(cx + sx * pw * 0.4 - 0.006, py + 0.07, cz - 0.006, 0.012, WALL_HEIGHT - 2.82 - 0.07 + 0.0, 0.012, hex('#8d98a3'));
      // The glint: a brighter strip that slides along the panel (scenery: a gentle shimmer of the light).
      amb.tagged(ambientTag('slide', 7 + ((i + j) % 3), pw * 0.7, 'x', ((i * 3 + j) % 5) / 5), () => {
        amb.box(cx - 0.12, py + 0.052, cz - pd / 2 + 0.04, 0.24, 0.004, pd - 0.08, hex('#ffffff'), { emissive: 1, shade: false, skip: ['bottom'] });
      });
      if (halos < MAX_HALOS_PER_ROOM && (i + j) % 2 === 0) { opaque.halo(cx, py - 0.3, cz, Math.min(3.2, Math.max(w, d) * 0.45), WARM, 0.55); halos++; }
    }
  }
}
