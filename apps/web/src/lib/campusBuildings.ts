/**
 * The building types of the campus. The type comes from the owner's choice where there is one (the
 * icon saved with the building in the campus contract: a truck or factory is a warehouse, a chart or
 * briefcase a glass office tower, a house or person a villa) and otherwise from the building kind and
 * a stable seed, so a campus never changes between visits. Every shape is decoration; no data is drawn.
 *
 *  - executive: the tower with a crown, helipad and a large lit ANUM mark
 *  - tower: a glass office tower on a two storey lobby podium
 *  - admin: a low block with ribbon windows and deep sunshades
 *  - retail: a glazed shop pavilion (drawn by the exterior assets; this file's admin block stands in when they are not used)
 *  - warehouse: a corrugated shed with roller doors, a loading apron and a truck
 *  - workshop: a saw-tooth roofed hall with a big door and tyre stacks
 *  - villa: a house with a hip roof (the personal office)
 *  - career: two learning wings joined by a lit entrance hall, with a free-standing sign (the career building, its own kind)
 */
import { MAT, MeshBuilder, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import { signAtlasLayout, type SignCell } from './hqTextures.ts';
import {
  ASPHALT, STONE, TEAL, WARM, WINDOW_BASE, acUnit, antenna, anumMark, bush, car, flowerBed, hedge, planter, roofGarden, solarRow, truck, waterTank,
} from './hqProps.ts';
import type { CampusBuildingKind, Placement } from './campusModel.ts';

export type Archetype = 'executive' | 'tower' | 'admin' | 'retail' | 'warehouse' | 'workshop' | 'villa' | 'career';

const ICON_TYPE: Readonly<Record<string, Archetype>> = {
  workshop: 'workshop', truck: 'warehouse', factory: 'warehouse', chart: 'tower', briefcase: 'tower', store: 'retail', building: 'admin', shield: 'admin', landmark: 'admin', home: 'villa', user: 'villa',
};

/** Which type a placement is drawn as. Pure, so a unit test can pin it down. */
export function archetypeOf(p: Pick<Placement, 'kind' | 'icon' | 'workspaceId'>): Archetype {
  if (p.kind === 'executive') return 'executive';
  if (p.kind === 'personal') return 'villa';
  // Career is its own kind (owner decision O2): always the career centre, whatever icon is saved.
  if (p.kind === 'career') return 'career';
  const fromIcon = p.icon ? ICON_TYPE[p.icon] : undefined;
  if (fromIcon && fromIcon !== 'villa') return fromIcon;
  const pick = seeded('type:' + p.workspaceId)();
  if (p.kind === 'business') return pick < 0.5 ? 'tower' : 'admin';
  return pick < 0.5 ? 'workshop' : pick < 0.8 ? 'warehouse' : 'admin';
}

const ACCENT: Record<CampusBuildingKind, Rgb> = {
  executive: hex('#d9b25f'), business: hex('#45d0b5'), department: hex('#6aa7ff'), personal: hex('#b48cff'), career: hex('#e8a24a'),
};
const WALL: Record<CampusBuildingKind, Rgb> = {
  executive: hex('#cfd6de'), business: hex('#d3d9d6'), department: hex('#cfd6e0'), personal: hex('#d8d2de'), career: hex('#ddd6ca'),
};
const PODIUM = 0.5;

export type BuildingShape = Readonly<{ anchor: { x: number; y: number; z: number }; door: { x: number; y: number; z: number } }>;

/** One lit window pane on a facade. `place` maps a position along the facade to (x, z). */
function pane(b: MeshBuilder, rnd: () => number, y0: number, y1: number, q: readonly [number, number], r: readonly [number, number], lit: number): void {
  const roll = rnd();
  if (roll < 0.1) {
    b.quad([q[0], y0, q[1]], [q[0], y1, q[1]], [r[0], y1, r[1]], [r[0], y0, r[1]], hex('#9fb4c8'), WINDOW_BASE);
    return;
  }
  const cool = roll > 0.86;
  const tone = cool ? mix(hex('#cfe6ff'), hex('#8fd0ff'), rnd()) : mix(WARM, hex('#ffe9b8'), rnd() * 0.7);
  const glow = WINDOW_BASE + (roll < 0.3 ? 0.45 : 0.8 + 0.15 * lit);
  b.quadV([q[0], y0, q[1]], [q[0], y1, q[1]], [r[0], y1, r[1]], [r[0], y0, r[1]], scale(tone, 0.8), tone, tone, scale(tone, 0.8), glow);
}

type FaceFn = (len: number, place: (u: number) => readonly [number, number], at: (u: number) => readonly [number, number, number, number]) => void;

/** Runs `fn` for the four faces of a box: length along the face, a position map, and the outward fin placement. */
function faces(x: number, z: number, w: number, d: number, fn: FaceFn): void {
  fn(w, (u) => [x + u, z - 0.03], (u) => [x + u, z - 0.22, 0.16, 0.22]);
  fn(w, (u) => [x + u, z + d + 0.03], (u) => [x + u, z + d, 0.16, 0.22]);
  fn(d, (u) => [x - 0.03, z + u], (u) => [x - 0.22, z + u, 0.22, 0.16]);
  fn(d, (u) => [x + w + 0.03, z + u], (u) => [x + w, z + u, 0.22, 0.16]);
}

type Block = Readonly<{ x: number; z: number; w: number; d: number; levels: number; y: number; levelH: number }>;
type Style = Readonly<{ wall: Rgb; accent: Rgb; ribbon?: boolean; sunshade?: boolean; curtain?: boolean }>;

/** Walls, floor bands, fins or sunshades, windows and roof edge for one block. Returns the roof height. */
function block(b: MeshBuilder, rnd: () => number, k: Block, style: Style, roofKind: 'flat' | 'crown' = 'flat'): number {
  const { x, z, w, d, levels, y, levelH } = k;
  const h = levels * levelH;
  const fin = scale(style.wall, 0.92);
  b.box(x, y, z, w, h, d, hex('#27435f'), { skip: ['top', 'bottom'], grad: 0.5 });
  b.withMat(MAT.concrete, () => {
    const corner = (cx: number, cz: number) => b.box(cx - 0.45, y, cz - 0.45, 0.9, h, 0.9, style.wall, { grad: 0.25 });
    corner(x, z); corner(x + w, z); corner(x, z + d); corner(x + w, z + d);
    for (let l = 0; l < levels; l++) {
      b.box(x - 0.16, y + l * levelH, z - 0.16, w + 0.32, 0.3, d + 0.32, style.wall);
      if (style.sunshade) b.box(x - 0.9, y + l * levelH + levelH * 0.82, z - 0.9, w + 1.8, 0.14, d + 1.8, scale(style.wall, 1.05));
    }
  });
  for (let l = 0; l < levels; l++) {
    const ly = y + l * levelH;
    const lobby = l === 0 && y < 1;
    const wy = ly + (lobby ? 0.4 : style.ribbon ? levelH * 0.16 : levelH * 0.2);
    const wh = lobby ? levelH * 0.74 : style.ribbon ? levelH * 0.62 : style.curtain ? levelH * 0.7 : levelH * 0.58;
    const lit = 0.55 + 0.45 * ((l + 1) / levels);
    faces(x, z, w, d, (len, place, at) => {
      const bay = style.curtain ? 1.5 : style.ribbon ? 3.0 : 2.1;
      const cols = Math.max(2, Math.floor((len - 1.6) / bay));
      const gap = (len - 1.6) / cols;
      for (let i = 0; i <= cols; i++) {
        if (style.ribbon) continue;
        const [fx, fz, fw, fd] = at(0.8 + i * gap);
        b.withMat(MAT.concrete, () => b.box(fx - fw / 2, ly + 0.3, fz - fd / 2, fw, levelH - 0.3, fd, fin, { skip: ['top', 'bottom'] }));
      }
      for (let i = 0; i < cols; i++) {
        const u0 = 0.8 + i * gap + gap * (style.ribbon || style.curtain ? 0.03 : 0.1), u1 = 0.8 + (i + 1) * gap - gap * (style.ribbon || style.curtain ? 0.03 : 0.1);
        pane(b, rnd, wy, wy + wh, place(u0), place(u1), lit);
        const q = place(u0), r = place(u1);
        b.quad([q[0], wy - 0.08, q[1]], [q[0], wy, q[1]], [r[0], wy, r[1]], [r[0], wy - 0.08, r[1]], hex('#0f1a26'));
      }
    });
  }
  const ry = y + h;
  b.withMat(MAT.concrete, () => {
    b.box(x - 0.5, ry, z - 0.5, w + 1.0, 0.5, d + 1.0, style.wall);
    for (const [bx, bz, bw, bd] of [[x - 0.5, z - 0.5, w + 1.0, 0.3], [x - 0.5, z + d + 0.2, w + 1.0, 0.3], [x - 0.5, z - 0.5, 0.3, d + 1.0], [x + w + 0.2, z - 0.5, 0.3, d + 1.0]] as const) {
      b.box(bx, ry + 0.5, bz, bw, 0.4, bd, style.accent, { emissive: 0.55 });
    }
  });
  if (roofKind === 'flat' && w > 6 && d > 6) roofPlant(b, rnd, x, z, w, d, ry + 0.5);
  return ry + 0.9;
}

function roofPlant(b: MeshBuilder, rnd: () => number, x: number, z: number, w: number, d: number, ry: number): void {
  b.withMat(MAT.concrete, () => b.plane(x + 0.4, ry + 0.02, z + 0.4, w - 0.8, d - 0.8, hex('#8d9aa5')));
  acUnit(b, x + w * 0.12, ry, z + d * 0.16, Math.min(3.2, w * 0.24), Math.min(2.2, d * 0.2));
  acUnit(b, x + w * 0.42, ry, z + d * 0.16, Math.min(2.6, w * 0.2), Math.min(2.2, d * 0.2));
  waterTank(b, x + w * 0.84, ry, z + d * 0.22);
  antenna(b, x + w * 0.9, ry, z + d * 0.8, 3.4 + rnd() * 2);
  if (rnd() < 0.55) solarRow(b, x + w * 0.1, ry, z + d * 0.6, Math.max(2, Math.min(5, Math.floor((w * 0.8) / 2.4))), 2.0, 1.7);
  else roofGarden(b, x + w * 0.1, ry, z + d * 0.52, w * 0.55, d * 0.36, rnd);
  b.withMat(MAT.glass, () => {
    const sx = x + w * 0.62, sz = z + d * 0.5, sw = w * 0.2, sd = d * 0.2;
    b.quad([sx, ry + 0.7, sz], [sx, ry + 0.7, sz + sd], [sx + sw, ry + 0.7, sz + sd], [sx + sw, ry + 0.7, sz], hex('#7fb9d6'), WINDOW_BASE);
  });
  b.box(x + w * 0.62, ry, z + d * 0.5, w * 0.2, 0.7, d * 0.2, hex('#9db4c4'), { emissive: 0.0, skip: ['top'] });
}

function hipRoof(b: MeshBuilder, x: number, z: number, w: number, d: number, y: number, h: number, color: Rgb): void {
  const o = 0.7;
  const ax = x + w / 2, az = z + d / 2;
  const c = [[x - o, z - o], [x + w + o, z - o], [x + w + o, z + d + o], [x - o, z + d + o]];
  b.withMat(MAT.metal, () => {
    for (let i = 0; i < 4; i++) {
      const p = c[i], q = c[(i + 1) % 4];
      b.tri([p[0], y, p[1]], [q[0], y, q[1]], [ax, y + h, az], scale(color, 0.7 + 0.12 * i));
    }
  });
  b.box(ax - 0.5, y + h * 0.4, az - 0.5, 1, h * 0.6, 1, scale(color, 0.6));
}

/** Door, canopy, steps, planters and the sign board (with the lettering cell of this building). */
function entrance(b: MeshBuilder, rnd: () => number, doorX: number, doorZ: number, y0: number, style: Style, cell: SignCell, wide = 6): void {
  const { wall, accent } = style;
  b.withMat(MAT.concrete, () => {
    b.box(doorX - wide / 2, y0 + 3.2, doorZ, wide, 0.34, 2.8, scale(wall, 0.95));
    b.box(doorX - wide / 2, y0 + 3.5, doorZ + 2.7, wide, 0.14, 0.12, accent, { emissive: 0.5 });
    for (const sx of [-wide / 2 + 0.2, wide / 2 - 0.42]) b.box(doorX + sx, y0, doorZ + 2.4, 0.22, 3.2, 0.22, wall);
  });
  b.quad([doorX - 1.3, y0, doorZ + 0.05], [doorX + 1.3, y0, doorZ + 0.05], [doorX + 1.3, y0 + 3.0, doorZ + 0.05], [doorX - 1.3, y0 + 3.0, doorZ + 0.05], WARM, WINDOW_BASE + 0.95);
  for (const dx of [-1.35, -0.05, 1.25]) b.box(doorX + dx, y0, doorZ, 0.1, 3.0, 0.14, scale(wall, 0.6));
  b.withMat(MAT.tile, () => {
    for (let i = 0; i < 3; i++) b.box(doorX - wide / 2 + i * 0.35, 0.18 * (3 - i) * 0.5, doorZ + 2.8 + i * 0.35, wide - i * 0.7, 0.18, 0.5, mix(STONE, hex('#ffffff'), 0.2 + i * 0.05));
    b.box(doorX - wide / 2 - 0.4, 0, doorZ + 3.9, wide + 0.8, 0.22, 3.4, mix(STONE, hex('#ffffff'), 0.25));
  });
  signBoard(b, doorX, y0 + 3.9, doorZ + 0.02, 5.2, 1.3, cell, accent);
  planter(b, doorX - wide / 2 - 1.4, doorZ + 2.2, 1.2, rnd);
  planter(b, doorX + wide / 2 + 1.4, doorZ + 2.2, 1.2, rnd);
  flowerBed(b, doorX - 9.0, doorZ + 3.2, 3.2, 1.6, rnd);
  flowerBed(b, doorX + 5.8, doorZ + 3.2, 3.2, 1.6, rnd);
}

/** A dark sign board whose front carries the lettering texture (ANUM MINE over the building name). */
export function signBoard(b: MeshBuilder, cx: number, y: number, z: number, w: number, h: number, cell: SignCell, accent: Rgb): void {
  b.box(cx - w / 2 - 0.08, y - 0.08, z, w + 0.16, h + 0.16, 0.2, hex('#0e1b26'));
  b.withMat(MAT.sign, () => {
    b.quadUV([cx - w / 2, y, z + 0.215], [cx + w / 2, y, z + 0.215], [cx + w / 2, y + h, z + 0.215], [cx - w / 2, y + h, z + 0.215], [cell.u0, cell.v0], [cell.u1, cell.v0], [cell.u1, cell.v1], [cell.u0, cell.v1], [1, 1, 1], 0.95);
  });
  b.box(cx - w / 2 - 0.08, y + h + 0.08, z + 0.02, w + 0.16, 0.06, 0.24, accent, { emissive: 0.8, shade: false });
}

// ---------------------------------------------------------------- the types

function executive(b: MeshBuilder, rnd: () => number, p: Placement, cell: SignCell): BuildingShape {
  const style: Style = { wall: WALL.executive, accent: ACCENT.executive };
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const L = p.levels;
  const baseH = 4.4;
  let top = block(b, rnd, { x: x0, z: z0, w: W, d: D, levels: 1, y: y0, levelH: baseH }, style);
  const tw = W * 0.7, td = D * 0.7, tx = x0 + (W - tw) / 2, tz = z0 + (D - td) / 2;
  const towerLevels = Math.max(2, L - 1);
  const tLevelH = Math.max(3.4, (p.height - y0 - baseH - 4.5) / towerLevels);
  top = Math.max(top, block(b, rnd, { x: tx, z: tz, w: tw, d: td, levels: towerLevels, y: y0 + baseH + 0.6, levelH: tLevelH }, { ...style, curtain: true }, 'crown'));
  const cy = y0 + baseH + 0.6 + towerLevels * tLevelH + 0.8;
  top = Math.max(top, block(b, rnd, { x: tx + tw * 0.2, z: tz + td * 0.2, w: tw * 0.6, d: td * 0.6, levels: 2, y: cy, levelH: 2.8 }, style, 'crown'));
  const my = cy + 5.6 + 0.8;
  anumMark(b, tx + tw / 2, y0 + baseH + 0.6 + towerLevels * tLevelH * 0.62, tz + td + 0.34, Math.min(7, tLevelH * 2.2));
  b.box(p.x - 1.6, my, p.z - 1.6, 3.2, 1.0, 3.2, style.accent, { emissive: 0.7 });
  b.prism(p.x, my + 1.0, p.z, 0.2, 0.05, 7, 5, hex('#9aa5b1'), { capTop: false });
  b.box(p.x - 0.2, my + 8, p.z - 0.2, 0.4, 0.4, 0.4, hex('#ff5a5a'), { emissive: 1, shade: false });
  // Helipad on the podium roof.
  b.prism(x0 + W * 0.18, y0 + baseH + 0.5, z0 + D * 0.82, 2.4, 2.4, 0.08, 16, hex('#3b4452'));
  b.prism(x0 + W * 0.18, y0 + baseH + 0.58, z0 + D * 0.82, 1.9, 1.9, 0.02, 16, hex('#e8d36a'), { emissive: 0.25 });
  top = my + 8.6;
  const doorZ = z0 + D;
  entrance(b, rnd, p.x, doorZ, y0, style, cell);
  return { anchor: { x: p.x, y: top + 1.2, z: p.z }, door: { x: p.x, y: 0, z: doorZ + 8 } };
}

function tower(b: MeshBuilder, rnd: () => number, p: Placement, style: Style, cell: SignCell): BuildingShape {
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const podiumH = 3.4;
  block(b, rnd, { x: x0, z: z0, w: W, d: D * 0.82, levels: 2, y: y0, levelH: podiumH }, { ...style, curtain: true });
  const tw = W * 0.62, td = D * 0.5, tx = x0 + W * 0.19, tz = z0 + D * 0.08;
  const levels = Math.max(3, Math.min(8, p.levels + 1));
  const levelH = 3.2;
  let top = block(b, rnd, { x: tx, z: tz, w: tw, d: td, levels, y: y0 + podiumH * 2 + 0.3, levelH }, { ...style, curtain: true }, 'crown');
  const crownY = y0 + podiumH * 2 + 0.3 + levels * levelH + 0.9;
  b.withMat(MAT.metal, () => b.box(tx + tw * 0.15, crownY, tz + td * 0.15, tw * 0.7, 2.2, td * 0.7, hex('#8f99a4'), { grad: 0.1 }));
  for (let i = 0; i < 8; i++) b.box(tx + tw * 0.15 + i * (tw * 0.7 / 8), crownY + 0.3, tz + td * 0.15 - 0.05, 0.12, 1.6, td * 0.7 + 0.1, hex('#59656f'));
  antenna(b, tx + tw * 0.5, crownY + 2.2, tz + td * 0.5, 4);
  top = crownY + 8.6;
  // The roof terrace of the podium.
  roofGarden(b, x0 + 1, y0 + podiumH * 2 + 0.5, z0 + D * 0.5, W * 0.4, D * 0.28, rnd);
  const doorZ = z0 + D * 0.82;
  entrance(b, rnd, x0 + W / 2, doorZ, y0, style, cell);
  return { anchor: { x: p.x, y: top + 1.2, z: p.z }, door: { x: x0 + W / 2, y: 0, z: doorZ + 8 } };
}

function admin(b: MeshBuilder, rnd: () => number, p: Placement, style: Style, cell: SignCell): BuildingShape {
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const levels = Math.max(2, Math.min(3, p.levels));
  const levelH = 3.6;
  const top = block(b, rnd, { x: x0, z: z0, w: W, d: D * 0.9, levels, y: y0, levelH }, { ...style, ribbon: true, sunshade: true });
  const doorZ = z0 + D * 0.9;
  // A portico: four slim columns under a deep slab.
  b.withMat(MAT.concrete, () => {
    b.box(x0 + W / 2 - 4, y0 + 3.7, doorZ, 8, 0.4, 3.4, scale(style.wall, 1.05));
    for (const dx of [-3.7, -1.3, 1.3, 3.5]) b.box(x0 + W / 2 + dx, y0, doorZ + 3.0, 0.3, 3.7, 0.3, style.wall);
  });
  entrance(b, rnd, x0 + W / 2, doorZ, y0, style, cell, 7);
  return { anchor: { x: p.x, y: top + 1.2, z: p.z }, door: { x: x0 + W / 2, y: 0, z: doorZ + 8 } };
}

function warehouse(b: MeshBuilder, rnd: () => number, p: Placement, style: Style, cell: SignCell): BuildingShape {
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const H = 7.4, sd = D * 0.84;
  const cladding = mix(hex('#b9c4cf'), style.wall, 0.3);
  b.withMat(MAT.metal, () => b.box(x0, y0 + 1.2, z0, W, H - 1.2, sd, cladding, { skip: ['bottom'], grad: 0.12 }));
  b.withMat(MAT.concrete, () => {
    b.box(x0 - 0.1, y0, z0 - 0.1, W + 0.2, 1.3, sd + 0.2, hex('#b8b3a6'));
    b.box(x0 - 0.3, y0 + H, z0 - 0.3, W + 0.6, 0.5, sd + 0.6, scale(style.wall, 0.95));
  });
  // Clerestory band under the roof on the front, and skylights.
  for (let i = 0; i < Math.floor((W - 2) / 2.6); i++) {
    const u = 1 + i * 2.6;
    pane(b, rnd, y0 + H - 2.0, y0 + H - 0.8, [x0 + u, z0 + sd + 0.03], [x0 + u + 2.2, z0 + sd + 0.03], 0.9);
  }
  roofPlant(b, rnd, x0, z0, W, sd, y0 + H + 0.5);
  // Roller doors with frames, bumpers, lamps and a numbered plate.
  const n = Math.max(2, Math.min(4, Math.floor((W - 5) / 4.4)));
  const doorsX: number[] = [];
  for (let i = 0; i < n; i++) {
    const cx = x0 + 3.2 + i * ((W - 6.4) / Math.max(1, n - 1)) * (n > 1 ? 1 : 0);
    doorsX.push(cx);
    b.box(cx - 2.05, y0, z0 + sd - 0.05, 4.1, 4.9, 0.16, hex('#2a313b'));
    b.withMat(MAT.roller, () => b.box(cx - 1.85, y0, z0 + sd + 0.1, 3.7, 4.5, 0.1, hex('#d5dbe0')));
    b.box(cx - 1.8, y0 + 0.5, z0 + sd + 0.2, 0.3, 1.2, 0.35, hex('#1b1f24'));
    b.box(cx + 1.5, y0 + 0.5, z0 + sd + 0.2, 0.3, 1.2, 0.35, hex('#1b1f24'));
    b.box(cx - 0.4, y0 + 5.0, z0 + sd + 0.15, 0.8, 0.5, 0.06, hex('#f2c14e'), { emissive: 0.5 });
    b.box(cx - 0.12, y0 + 5.9, z0 + sd + 0.12, 0.24, 0.14, 0.2, WARM, { emissive: 1, shade: false });
  }
  // A glazed office annex on the west corner with the entrance.
  const aw = 5.2, ad = 6;
  block(b, rnd, { x: x0 - aw + 1, z: z0 + sd - ad, w: aw, d: ad, levels: 2, y: y0, levelH: 3.2 }, { ...style, curtain: true });
  const doorX = x0 - aw / 2 + 1;
  const doorZ = z0 + sd;
  entrance(b, rnd, doorX, doorZ, y0, style, cell, 4.4);
  // Loading apron, a truck backed to a door, and bollards.
  const apronZ = z0 + sd + 0.6;
  b.withMat(MAT.asphalt, () => b.plane(x0 + 1, 0.016, apronZ, W - 1, 11.5, scale(ASPHALT, 1.08)));
  const tx = doorsX[Math.min(1, doorsX.length - 1)];
  truck(b, tx, apronZ + 5.2, mix(hex('#2f6a8f'), style.accent, 0.25), 0);
  for (const cx of doorsX) for (const dx of [-2.2, 2.2]) b.box(cx + dx - 0.1, 0, apronZ + 1.2, 0.2, 0.9, 0.2, hex('#e8d36a'));
  return { anchor: { x: p.x, y: y0 + H + 4.5, z: p.z }, door: { x: doorX, y: 0, z: doorZ + 9 } };
}

function workshop(b: MeshBuilder, rnd: () => number, p: Placement, style: Style, cell: SignCell): BuildingShape {
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const H = 6.4, sd = D * 0.82;
  b.withMat(MAT.concrete, () => b.box(x0, y0, z0, W, H, sd, mix(style.wall, hex('#b9a58a'), 0.35), { skip: ['top', 'bottom'], grad: 0.1 }));
  b.box(x0 - 0.1, y0, z0 - 0.1, W + 0.2, 0.5, sd + 0.2, hex('#8d867a'));
  // Saw-tooth roof: each tooth rises to the north with a glazed face to the south.
  const teeth = Math.max(3, Math.floor(W / 4.4));
  const tw = W / teeth;
  const rise = 2.4;
  for (let i = 0; i < teeth; i++) {
    const a = x0 + i * tw, c = x0 + (i + 1) * tw;
    b.withMat(MAT.metal, () => {
      b.quad([a, y0 + H, z0 + sd], [c, y0 + H, z0 + sd], [c, y0 + H + rise, z0], [a, y0 + H + rise, z0], hex('#9aa8b6'));
      b.quad([a, y0 + H, z0], [c, y0 + H, z0], [c, y0 + H + rise, z0], [a, y0 + H + rise, z0], hex('#9aa8b6'));
    });
    // The glazed riser at the south end of each tooth.
    b.quad([a, y0 + H, z0 + sd], [a, y0 + H + 0.2, z0 + sd], [c, y0 + H + 0.2, z0 + sd], [c, y0 + H, z0 + sd], hex('#59656f'));
    b.withMat(MAT.glass, () => {
      b.quad([a + 0.1, y0 + H + rise * 0.1, z0 + 0.05], [c - 0.1, y0 + H + rise * 0.1, z0 + 0.05], [c - 0.1, y0 + H + rise, z0 + 0.05], [a + 0.1, y0 + H + rise, z0 + 0.05], hex('#ffe3a8'), WINDOW_BASE + 0.8);
    });
    b.box(a, y0 + H, z0 + sd - 0.1, 0.1, 0.4, 0.2, hex('#59656f'));
  }
  b.box(x0 + W - 1.4, y0 + H, z0 + 1, 0.8, 4.2, 0.8, hex('#b3b8bd'));
  b.prism(x0 + W - 1.0, y0 + H + 4.2, z0 + 1.4, 0.5, 0.4, 0.5, 8, hex('#7f8993'));
  // Large workshop door and a row of high windows.
  const doorX = x0 + W * 0.7;
  b.box(doorX - 2.6, y0, z0 + sd - 0.05, 5.2, 5.2, 0.16, hex('#2a313b'));
  b.withMat(MAT.roller, () => b.box(doorX - 2.4, y0, z0 + sd + 0.1, 4.8, 4.8, 0.1, hex('#cdd3d8')));
  for (let i = 0; i < Math.floor((W * 0.55) / 2.4); i++) pane(b, rnd, y0 + 3.2, y0 + 4.8, [x0 + 1 + i * 2.4, z0 + sd + 0.03], [x0 + 1 + i * 2.4 + 2.0, z0 + sd + 0.03], 0.9);
  const entX = x0 + 3.2;
  entrance(b, rnd, entX, z0 + sd, y0, style, cell, 4.4);
  // Tyre stacks, pallets and a parked van outside.
  b.withMat(MAT.asphalt, () => b.plane(x0 + 1, 0.016, z0 + sd + 0.6, W - 1, 9, scale(ASPHALT, 1.08)));
  for (let i = 0; i < 3; i++) for (let k = 0; k < 4 + (i % 2); k++) {
    b.prism(doorX + 5.6 + i * 1.1, k * 0.25, z0 + sd + 3.6, 0.46, 0.46, 0.24, 10, hex('#1b1f24'));
    b.prism(doorX + 5.6 + i * 1.1, k * 0.25 + 0.241, z0 + sd + 3.6, 0.22, 0.22, 0.004, 8, hex('#9aa5b1'));
  }
  car(b, doorX - 5, z0 + sd + 6, hex('#e8ebee'), 0);
  return { anchor: { x: p.x, y: y0 + H + rise + 4, z: p.z }, door: { x: entX, y: 0, z: z0 + sd + 9 } };
}

function villa(b: MeshBuilder, rnd: () => number, p: Placement, style: Style, cell: SignCell): BuildingShape {
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const levelH = (p.height - y0 - 1.2) / Math.max(1, p.levels);
  const levels = Math.max(1, p.levels);
  block(b, rnd, { x: x0 + 0.8, z: z0 + 0.8, w: W - 1.6, d: D - 1.6, levels, y: y0, levelH: levelH * 1.1 }, style, 'crown');
  const ry = y0 + levels * levelH * 1.1 + 0.5;
  hipRoof(b, x0 + 0.8, z0 + 0.8, W - 1.6, D - 1.6, ry, 2.6, mix(style.accent, hex('#3a2f4a'), 0.55));
  // A garden wall with a gate, a bush row and a small pool at the back.
  b.withMat(MAT.concrete, () => {
    b.box(x0 - 0.8, 0, z0 + D + 0.2, 3.2, 1.1, 0.4, hex('#d8d2c4'));
    b.box(x0 + W - 2.4, 0, z0 + D + 0.2, 3.2, 1.1, 0.4, hex('#d8d2c4'));
  });
  for (let i = 0; i < 4; i++) bush(b, x0 + 1 + i * 2.6, z0 + D + 0.9, 0.5, rnd);
  entrance(b, rnd, p.x, z0 + D - 0.8, y0, style, cell, 4.4);
  return { anchor: { x: p.x, y: ry + 3.8, z: p.z }, door: { x: p.x, y: 0, z: z0 + D - 0.8 + 8 } };
}

function career(b: MeshBuilder, rnd: () => number, p: Placement, style: Style, cell: SignCell): BuildingShape {
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2, W = p.width, D = p.depth, y0 = PODIUM;
  const levels = Math.max(2, Math.min(3, p.levels));
  const levelH = 3.6;
  const hall = Math.min(6, W * 0.3), wing = (W - hall) / 2;
  let top = block(b, rnd, { x: x0, z: z0, w: wing, d: D * 0.9, levels, y: y0, levelH }, { ...style, ribbon: true, sunshade: true });
  top = Math.max(top, block(b, rnd, { x: x0 + wing + hall, z: z0, w: wing, d: D * 0.9, levels, y: y0, levelH }, { ...style, ribbon: true, sunshade: true }));
  // The entrance hall between the wings: a lit glass box one storey proud of the roof line.
  b.withMat(MAT.glass, () => b.box(x0 + wing, y0, z0 + 0.4, hall, levels * levelH + 1.2, D * 0.9 - 0.4, mix(WARM, hex('#ffe9b8'), 0.4), { emissive: WINDOW_BASE + 0.7 }));
  b.box(x0 + wing - 0.2, y0 + levels * levelH + 1.2, z0 + 0.2, hall + 0.4, 0.3, D * 0.9, style.accent, { emissive: 0.7 });
  const doorZ = z0 + D * 0.9;
  entrance(b, rnd, x0 + W / 2, doorZ, y0, style, cell, 5);
  // A free-standing pylon on the forecourt, in the career colour.
  b.box(x0 + wing - 3.4, 0, doorZ + 5.2, 0.8, 5.2, 0.8, style.accent, { emissive: 0.4 });
  return { anchor: { x: p.x, y: Math.max(top, y0 + levels * levelH + 1.5) + 1.2, z: p.z }, door: { x: x0 + W / 2, y: 0, z: doorZ + 8 } };
}

/** Draws one building into `b` and says where its label and entrance walkway go. */
export function drawBuilding(b: MeshBuilder, p: Placement, seed: string, signIndex: number, signCount: number): BuildingShape & { archetype: Archetype } {
  const rnd = seeded(seed);
  const archetype = archetypeOf(p);
  const base = WALL[p.kind];
  const finish = Math.floor(rnd() * 3);
  const wall = finish === 1 ? mix(base, hex('#dccaa4'), 0.7) : finish === 2 ? mix(base, hex('#8a93a0'), 0.55) : base;
  const style: Style = { wall, accent: ACCENT[p.kind] };
  const cell = signAtlasLayout(signCount).cell(signIndex);
  const x0 = p.x - p.width / 2, z0 = p.z - p.depth / 2;
  b.withMat(MAT.tile, () => b.box(x0 - 1.2, 0, z0 - 1.2, p.width + 2.4, PODIUM, p.depth + 2.4, STONE));
  const shape = archetype === 'executive' ? executive(b, rnd, p, cell)
    : archetype === 'tower' ? tower(b, rnd, p, style, cell)
      : archetype === 'admin' || archetype === 'retail' ? admin(b, rnd, p, style, cell)
        : archetype === 'warehouse' ? warehouse(b, rnd, p, style, cell)
          : archetype === 'workshop' ? workshop(b, rnd, p, style, cell)
            : archetype === 'career' ? career(b, rnd, p, style, cell)
              : villa(b, rnd, p, style, cell);
  // Front hedge lines either side of the walkway.
  const dz = shape.door.z - 8 + 6.2;
  hedge(b, x0 - 0.2, dz, Math.max(2, shape.door.x - 5.6 - (x0 - 0.2)), 0.7, 0.7);
  hedge(b, shape.door.x + 5.6, dz, Math.max(2, x0 + p.width + 0.2 - (shape.door.x + 5.6)), 0.7, 0.7);
  return { ...shape, archetype };
}

export { ACCENT as BUILDING_ACCENT, TEAL as BUILDING_TEAL };
