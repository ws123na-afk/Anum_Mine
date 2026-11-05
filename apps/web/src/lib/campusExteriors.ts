/**
 * Bridge between the campus and the exterior building assets (`hqAssets/exterior`): picks the
 * builder for a building type, places it, lays the lettered sign over the asset's blank board, and
 * gives its flat surfaces their classes. Pure; the lettering text itself is drawn at render time.
 */
import { buildExterior, type ExteriorAsset, type ExteriorKind } from '../hqAssets/exterior/index.ts';
import type { Archetype } from './campusBuildings.ts';
import type { Placement } from './campusModel.ts';
import type { Vec3 } from './headquartersProjection.ts';
import { MAT, MeshBuilder, VERTEX_FLOATS } from './hqGeometry.ts';
import type { Aabb } from './hqMath.ts';
import { classifySurfaces } from './hqSurfaces.ts';
import { leanPoint, leanVertices } from './campusFacing.ts';
import { signAtlasLayout } from './hqTextures.ts';

export const EXTERIOR_OF: Readonly<Record<Archetype, ExteriorKind>> = {
  executive: 'executiveHq', tower: 'officeTower', admin: 'adminBlock', retail: 'retailBlock', warehouse: 'warehouse', workshop: 'tyreWorkshop', villa: 'personalVilla',
  // The career centre is a lazy drawer in the plan kit (`hqPlanKit.ts` `careerDrawer`); until it is loaded, and inside the plan shell, a career building is the admin block family.
  career: 'adminBlock',
};

export type ExteriorShape = Readonly<{
  vertices: Float32Array; bounds: Aabb; triangles: number; anchor: Vec3; door: Vec3; box: Aabb; halos: Float32Array;
}>;

/** The asset for this building: every campus type has one. */
export function drawExterior(p: Placement, archetype: Archetype, signIndex: number, signCount: number): ExteriorShape {
  const kind = EXTERIOR_OF[archetype];
  // The assets have their own size ranges (a tower is at least 8 storeys, the executive building at least 30 m wide):
  // ask for the smallest storey count that keeps the executive building the tallest on the campus.
  const levels = archetype === 'executive' ? Math.min(10, Math.max(8, p.levels + 3)) : archetype === 'tower' ? 6 : p.levels;
  const asset: ExteriorAsset = buildExterior(kind, { seed: 'bld:' + p.workspaceId, width: p.width, depth: p.depth, levels, origin: { x: p.x, z: p.z }, rotation: p.turn });
  return leanShape(shapeOf(asset, signIndex, signCount), p);
}

/**
 * Leans a placed shape by its building's lean (`Placement.yaw`) round the building's middle, so its front points straight at the plaza:
 * the vertices (in place), the door, the label anchor and the halos; the bounds and the box become the boxes round the leaned shape.
 */
export function leanShape(shape: ExteriorShape, p: Placement): ExteriorShape {
  const yaw = p.yaw ?? 0;
  if (!yaw) return shape;
  leanVertices(shape.vertices, yaw, p.x, p.z);
  const halos = new Float32Array(shape.halos);
  for (let i = 0; i < halos.length; i += 8) { const q = leanPoint({ x: halos[i], z: halos[i + 2] }, yaw, p.x, p.z); halos[i] = q.x; halos[i + 2] = q.z; }
  const around = (box: Aabb): Aabb => {
    let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
    for (const [x, z] of [[box.min.x, box.min.z], [box.max.x, box.min.z], [box.max.x, box.max.z], [box.min.x, box.max.z]] as const) {
      const q = leanPoint({ x, z }, yaw, p.x, p.z);
      x0 = Math.min(x0, q.x); z0 = Math.min(z0, q.z); x1 = Math.max(x1, q.x); z1 = Math.max(z1, q.z);
    }
    return { min: { x: x0, y: box.min.y, z: z0 }, max: { x: x1, y: box.max.y, z: z1 } };
  };
  return { ...shape, halos, bounds: around(shape.bounds), box: around(shape.box), door: leanPoint(shape.door, yaw, p.x, p.z), anchor: leanPoint(shape.anchor, yaw, p.x, p.z) };
}

/** Letters the sign board and classifies the surfaces of any exterior asset (generic or generated from floor plans). */
export function shapeOf(asset: ExteriorAsset, signIndex: number, signCount: number): ExteriorShape {
  // The lettered sign lies a hair in front of the asset's blank board and covers it (the board's own mark is in the texture).
  const cell = signAtlasLayout(signCount).cell(signIndex);
  const s = asset.sign;
  const rx = s.nz, rz = -s.nx; // the board's right-hand direction seen from the front
  const hw = s.width / 2, hh = s.height / 2, off = 0.05;
  const cx = s.x + s.nx * off, cz = s.z + s.nz * off;
  const sb = new MeshBuilder();
  sb.mat = MAT.sign;
  sb.quadUV(
    [cx - rx * hw, s.y - hh, cz - rz * hw], [cx + rx * hw, s.y - hh, cz + rz * hw], [cx + rx * hw, s.y + hh, cz + rz * hw], [cx - rx * hw, s.y + hh, cz - rz * hw],
    [cell.u0, cell.v0], [cell.u1, cell.v0], [cell.u1, cell.v1], [cell.u0, cell.v1], [1, 1, 1], 0.95,
  );
  const board = sb.toFloat32();
  const body = classifySurfaces(asset.mesh.vertices);
  const vertices = new Float32Array(body.length + board.length);
  vertices.set(body, 0);
  vertices.set(board, body.length);
  const bb = sb.bounds(), ab = asset.mesh.bounds;
  const bounds: Aabb = {
    min: { x: Math.min(ab.min.x, bb.min.x), y: Math.min(ab.min.y, bb.min.y), z: Math.min(ab.min.z, bb.min.z) },
    max: { x: Math.max(ab.max.x, bb.max.x), y: Math.max(ab.max.y, bb.max.y), z: Math.max(ab.max.z, bb.max.z) },
  };
  return { vertices, bounds, triangles: vertices.length / VERTEX_FLOATS / 3, anchor: asset.anchor, door: asset.door, box: asset.box, halos: asset.halos };
}
