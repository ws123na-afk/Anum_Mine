/**
 * Where the live screens are and which cell of the shared atlas each one owns (docs/ANUM_MINE_HQ_LIVE_SCREENS.md). Pure: no DOM.
 *
 * One small canvas holds every screen of the open floor: 256 x 128 cells for desk monitors and 512 x 256 cells for walls. The cap keeps the
 * texture at most 2048 x 1024 (8 MB). A face is laid over the real monitor or video wall as one textured quad a few millimetres in front of
 * it, so the geometry the room was built from never changes when a face does.
 */
import { MAT, MeshBuilder, hex } from '../lib/hqGeometry.ts';
import type { SceneScreen } from '../lib/buildingScene.ts';
import type { RenderItem } from '../lib/hqRenderTypes.ts';

export const MON_W = 256, MON_H = 128, WALL_W = 512, WALL_H = 256;
export const ATLAS_W = 2048;
export const MON_COLS = ATLAS_W / MON_W;
export const MON_CAP = 48;
export const WALL_CAP = 4;

const nextPow2 = (n: number) => { let p = 1; while (p < n) p *= 2; return p; };

export type AtlasCell = Readonly<{ x: number; y: number; w: number; h: number; u0: number; u1: number; v0: number; v1: number }>;
export type ScreenLayout = Readonly<{ width: number; height: number; cell: (kind: 'monitor' | 'wall', index: number) => AtlasCell }>;

/** The atlas for `monitors` monitor cells then `walls` wall cells. `v1` is the top edge in texture space (the canvas is flipped on upload). */
export function screenLayout(monitors: number, walls: number): ScreenLayout {
  const m = Math.min(MON_CAP, Math.max(0, monitors)), w = Math.min(WALL_CAP, Math.max(0, walls));
  const monRows = Math.ceil(m / MON_COLS);
  const height = nextPow2(Math.max(MON_H, monRows * MON_H + (w ? WALL_H : 0)));
  const cell = (kind: 'monitor' | 'wall', i: number): AtlasCell => {
    const cw = kind === 'monitor' ? MON_W : WALL_W, ch = kind === 'monitor' ? MON_H : WALL_H;
    const x = kind === 'monitor' ? (i % MON_COLS) * MON_W : i * WALL_W;
    const y = kind === 'monitor' ? Math.floor(i / MON_COLS) * MON_H : monRows * MON_H;
    // Half a texel inside the cell, so neighbours never bleed in.
    return { x, y, w: cw, h: ch, u0: (x + 0.5) / ATLAS_W, u1: (x + cw - 0.5) / ATLAS_W, v1: 1 - (y + 0.5) / height, v0: 1 - (y + ch - 0.5) / height };
  };
  return { width: ATLAS_W, height, cell };
}

export type ScreenSite = Readonly<{
  kind: 'monitor' | 'wall';
  /** The desk id of a monitor, the room id of a wall. Never printed. */
  id: string;
  roomId: string;
  /** Index among the sites of its kind: the atlas cell. */
  cell: number;
  /** The four world corners: bottom left, bottom right, top right, top left, as the viewer sees them. */
  p: readonly number[];
  center: readonly [number, number, number];
  /** Width over height of the glass, so a face can be drawn without stretching. */
  aspect: number;
}>;

const dist3 = (p: readonly number[], a: number, b: number) => Math.hypot(p[a] - p[b], p[a + 1] - p[b + 1], p[a + 2] - p[b + 2]);

function siteOf(kind: 'monitor' | 'wall', id: string, roomId: string, cell: number, p: readonly number[]): ScreenSite {
  const center: [number, number, number] = [(p[0] + p[6]) / 2, (p[1] + p[7]) / 2, (p[2] + p[8]) / 2];
  return { kind, id, roomId, cell, p, center, aspect: Math.max(0.3, dist3(p, 0, 3)) / Math.max(0.05, dist3(p, 0, 9)) };
}

export type FlatRoom = Readonly<{ id: string; x: number; z: number; width: number; depth: number; doorSide?: 'north' | 'south' | 'east' | 'west' | null }>;

/**
 * A wall screen for a room that shows the operations wall but whose furnishing has no video wall (an office called Logistics): a panel
 * on the wall opposite the door, centred, a metre high. Returns the four corners facing into the room.
 */
export function mountedWall(room: FlatRoom, floorY: number): number[] {
  const side = room.doorSide ?? 'south';
  const inset = 0.16;
  const alongX = side === 'south' || side === 'north';
  const span = alongX ? room.width : room.depth;
  const w = Math.min(3.6, Math.max(1.6, span - 1.4));
  const y0 = floorY + 1.15, y1 = y0 + w / 2.7;
  const mid = alongX ? room.x + room.width / 2 : room.z + room.depth / 2;
  // The wall opposite the door; n points into the room, `right` is the viewer's right while looking at the wall.
  const wall = side === 'south' ? room.z + inset : side === 'north' ? room.z + room.depth - inset : side === 'east' ? room.x + inset : room.x + room.width - inset;
  const n = side === 'south' ? [0, 1] : side === 'north' ? [0, -1] : side === 'east' ? [1, 0] : [-1, 0];
  const right = [n[1], -n[0]];
  const at = (s: number, y: number): number[] => (alongX ? [mid + right[0] * s, y, wall] : [wall, y, mid + right[1] * s]);
  return [...at(-w / 2, y0), ...at(w / 2, y0), ...at(w / 2, y1), ...at(-w / 2, y1)];
}

export type ScreenLayer = Readonly<{ sites: readonly ScreenSite[]; layout: ScreenLayout; items: readonly RenderItem[] }>;

/**
 * The screens of the open floor. `screens` are the ones the furnishing noted (monitors and video walls); `mounted` lists rooms that show the
 * operations wall without having a video wall, which get a framed panel. Sites beyond the caps are left out (their monitor keeps the plain look).
 */
export function screenLayer(screens: readonly SceneScreen[], floorIndex: number, mounted: readonly FlatRoom[], floorY: number): ScreenLayer {
  const sites: ScreenSite[] = [];
  let mon = 0, wall = 0;
  const hasWall = new Set(screens.filter((s) => s.kind === 'wall').map((s) => s.roomId));
  const frame = new MeshBuilder();
  const rects: Array<{ p: readonly number[]; site: ScreenSite }> = [];
  for (const s of screens) {
    if (s.floorIndex !== floorIndex) continue;
    if (s.kind === 'monitor' && mon < MON_CAP) { const site = siteOf('monitor', s.id, s.roomId, mon++, s.p); sites.push(site); rects.push({ p: s.p, site }); }
    if (s.kind === 'wall' && wall < WALL_CAP) { const site = siteOf('wall', s.roomId, s.roomId, wall++, s.p); sites.push(site); rects.push({ p: s.p, site }); }
  }
  for (const room of mounted) {
    if (hasWall.has(room.id) || wall >= WALL_CAP) continue;
    const p = mountedWall(room, floorY);
    const site = siteOf('wall', room.id, room.id, wall++, p);
    sites.push(site);
    rects.push({ p, site });
    // A dark frame behind the panel, so it reads as a mounted display.
    const side = room.doorSide ?? 'south';
    const alongX = side === 'south' || side === 'north';
    const lo = [Math.min(p[0], p[3], p[6], p[9]), Math.min(p[1], p[7]), Math.min(p[2], p[5], p[8], p[11])];
    const hi = [Math.max(p[0], p[3], p[6], p[9]), Math.max(p[1], p[7]), Math.max(p[2], p[5], p[8], p[11])];
    const pad = 0.08;
    frame.mat = MAT.flat;
    const sign = side === 'south' || side === 'east' ? 1 : -1;
    // Behind the panel (away from the room): 6 cm deep, its front 5 mm behind the face.
    const back = (v: number) => (sign > 0 ? v - 0.065 : v + 0.005);
    if (alongX) frame.box(lo[0] - pad, lo[1] - pad, back(lo[2]), hi[0] - lo[0] + 2 * pad, hi[1] - lo[1] + 2 * pad, 0.06, hex('#11161c'));
    else frame.box(back(lo[0]), lo[1] - pad, lo[2] - pad, 0.06, hi[1] - lo[1] + 2 * pad, hi[2] - lo[2] + 2 * pad, hex('#11161c'));
  }
  const layout = screenLayout(mon, wall);
  const b = new MeshBuilder();
  b.mat = MAT.screen;
  for (const { p, site } of rects) {
    const c = layout.cell(site.kind, site.cell);
    b.quadUV([p[0], p[1], p[2]], [p[3], p[4], p[5]], [p[6], p[7], p[8]], [p[9], p[10], p[11]], [c.u0, c.v0], [c.u1, c.v0], [c.u1, c.v1], [c.u0, c.v1], [1, 1, 1], 0);
  }
  const items: RenderItem[] = [];
  if (rects.length) items.push({ id: 'screens', vertices: b.toFloat32(), bounds: b.bounds(), cull: false });
  if (frame.vertexCount) items.push({ id: 'screen-frames', vertices: frame.toFloat32(), bounds: frame.bounds(), cull: false });
  return { sites, layout, items };
}
