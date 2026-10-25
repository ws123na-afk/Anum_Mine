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
import { signAtlasLayout } from './hqTextures.ts';

const EXTERIOR_OF: Readonly<Partial<Record<Archetype, ExteriorKind>>> = {
  executive: 'executiveHq', tower: 'officeTower', admin: 'adminBlock', retail: 'retailBlock', warehouse: 'warehouse', workshop: 'tyreWorkshop',
};

export type ExteriorShape = Readonly<{
  vertices: Float32Array; bounds: Aabb; triangles: number; anchor: Vec3; door: Vec3; box: Aabb;
}>;

/** The asset for this building, or null for a type the asset set does not cover (the villa). */
export function drawExterior(p: Placement, archetype: Archetype, signIndex: number, signCount: number): ExteriorShape | null {
  const kind = EXTERIOR_OF[archetype];
  if (!kind) return null;
  // The assets have their own size ranges (a tower is at least 8 storeys, the executive building at least 30 m wide):
  // ask for the smallest storey count that keeps the executive building the tallest on the campus.
  const levels = archetype === 'executive' ? Math.min(26, Math.max(14, p.levels * 2)) : archetype === 'tower' ? 8 : p.levels;
  const asset: ExteriorAsset = buildExterior(kind, { seed: 'bld:' + p.workspaceId, width: p.width, depth: p.depth, levels, origin: { x: p.x, z: p.z } });
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
  return { vertices, bounds, triangles: vertices.length / VERTEX_FLOATS / 3, anchor: asset.anchor, door: asset.door, box: asset.box };
}
