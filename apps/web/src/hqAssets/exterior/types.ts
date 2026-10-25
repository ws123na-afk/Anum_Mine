/**
 * Public types of the exterior building assets.
 *
 * Frame: every builder works in a local frame whose origin is the middle of the footprint on the
 * ground (y = 0), with the front (entrance, sign) facing +z. `rotation` (quarter turns) and
 * `origin` are applied once, at the end, to the vertices, bounds and anchors.
 *
 * Geometry here is decoration only: nothing in a mesh encodes a name, count or status. The real
 * building name is an HTML overlay pinned to `sign`.
 */
import type { Rgb } from '../../lib/hqGeometry.ts';
import type { Aabb } from '../../lib/hqMath.ts';

export type ExteriorKind = 'warehouse' | 'tyreWorkshop' | 'officeTower' | 'adminBlock' | 'retailBlock' | 'executiveHq';
export const EXTERIOR_KINDS: readonly ExteriorKind[] = ['warehouse', 'tyreWorkshop', 'officeTower', 'adminBlock', 'retailBlock', 'executiveHq'];

export type ExteriorSchemeName = 'white' | 'sandstone' | 'slate' | 'graphite';

export type ExteriorParams = Readonly<{
  /** Footprint width (x) in metres. Clamped to the kind's allowed range. */
  width?: number;
  /** Footprint depth (z) in metres. Clamped to the kind's allowed range. */
  depth?: number;
  /** Storeys for the multi-storey kinds (office tower, admin block, executive HQ). Ignored by sheds. */
  levels?: number;
  /** Wall finish. 'auto' (default) picks one from the seed so a campus does not read as clones. */
  scheme?: ExteriorSchemeName | 'auto';
  /** Accent colour (fascia lines, crown band, awnings). Defaults to the scheme accent. */
  accent?: Rgb;
  /** Any string, for example the workspace id. Same seed, same building. */
  seed?: string;
  /** Share of windows that are lit at night, 0..1. Defaults per kind (see `WINDOW_RULES`). */
  litWindows?: number;
  /** Quarter turns: 0 front faces +z, 1 faces -x, 2 faces -z, 3 faces +x. */
  rotation?: 0 | 1 | 2 | 3;
  /** World position of the footprint middle. */
  origin?: Readonly<{ x: number; z: number }>;
  /** 'lean' leaves out small props (vehicles, planting, pallets) for tight triangle budgets. */
  detail?: 'full' | 'lean';
}>;

export type SceneMeshData = Readonly<{ vertices: Float32Array; bounds: Aabb; triangles: number }>;
export type Point3 = Readonly<{ x: number; y: number; z: number }>;

export type SignAnchor = Readonly<{
  /** Middle of the sign board's face, a hair in front of it. */
  x: number; y: number; z: number;
  /** Outward normal of the board (ground plane). */
  nx: number; nz: number;
  /** Board size in metres. The left `markWidth` metres hold the ANUM mark; the name goes in the rest. */
  width: number; height: number; markWidth: number;
}>;

export type ExteriorAsset = Readonly<{
  kind: ExteriorKind;
  mesh: SceneMeshData;
  triangles: number;
  /** Footprint of the building proper (without apron, yard or loading bay). */
  footprint: Readonly<{ width: number; depth: number }>;
  /** Roof height of the highest solid part in metres (antennas and spires excluded). */
  height: number;
  /** Where a label card floats: above the roof, over the middle. */
  anchor: Point3;
  /** Ground point in front of the entrance where a path should end. */
  door: Point3;
  sign: SignAnchor;
  /** Window panes drawn: `lit` of `total` are lit at night, the rest stay dark glass. */
  windows: Readonly<{ total: number; lit: number }>;
  /** Ground box of everything drawn (apron, yard, bay and baked shadow included), for placement and culling. */
  box: Aabb;
  /** Resolved scheme and seed, so a caller can show or store what was used. */
  scheme: ExteriorSchemeName;
  seed: string;
}>;

export type ExteriorBuilder = (params?: ExteriorParams) => ExteriorAsset;
