/**
 * Subtle bevels for the exterior kit (docs/ANUM_MINE_HQ_BUILDING_LOOK.md, "Edges"): a roof line or slab band whose top edge and
 * vertical corners are chamfered, and slim chamfered corner beads that soften a wall block's vertical corners. Each chamfer is a
 * flat face at 45 degrees, so it catches the light differently from the faces beside it and reads as a rounded edge at campus
 * distance, for a few dozen triangles. Pure geometry into a MeshBuilder, no WebGL.
 */
import { scale, type MeshBuilder, type Rgb } from '../../lib/hqGeometry.ts';

type P = readonly [number, number];

/**
 * The outline of a rectangle with its four corners cut at 45 degrees, in the order the kit's walls use (north edge first, then east,
 * south, west), so the outward side of each edge is on its right. `inset` moves every edge in; the corner cut shrinks so the cut
 * edges stay parallel to the outer ones.
 */
export function chamferedRing(x: number, z: number, w: number, d: number, cut: number, inset = 0): P[] {
  const x0 = x + inset, x1 = x + w - inset, z0 = z + inset, z1 = z + d - inset;
  const c = Math.max(0.005, Math.min(cut - inset * (2 - Math.SQRT2), (x1 - x0) / 2 - 0.005, (z1 - z0) / 2 - 0.005));
  return [[x0 + c, z0], [x1 - c, z0], [x1, z0 + c], [x1, z1 - c], [x1 - c, z1], [x0 + c, z1], [x0, z1 - c], [x0, z0 + c]];
}

/** An upright quad from P to Q between two heights whose normal points to the given outward side. */
function wall(b: MeshBuilder, p: P, q: P, y0: number, y1: number, out: P, color: Rgb, emissive = 0): void {
  const dx = q[0] - p[0], dz = q[1] - p[1];
  const [a, c] = dz * out[0] - dx * out[1] >= 0 ? [p, q] : [q, p];
  b.quad([a[0], y0, a[1]], [a[0], y1, a[1]], [c[0], y1, c[1]], [c[0], y0, c[1]], color, emissive);
}

/**
 * A band round a block (a slab edge, a parapet or a coping) from `y` to `y + h`, with chamfered vertical corners and a chamfered top
 * edge of size `bevel`. 38 triangles (a plain box band is 10).
 */
export function bevelBand(b: MeshBuilder, x: number, z: number, w: number, d: number, y: number, h: number, color: Rgb, bevel = 0.16): void {
  const c = Math.max(0.02, Math.min(bevel, h * 0.6));
  const outer = chamferedRing(x, z, w, d, c * 1.6);
  const inner = chamferedRing(x, z, w, d, c * 1.6, c);
  const ya = y + h - c, yb = y + h;
  const n = outer.length;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const p = outer[i], q = outer[j];
    // Outward on the right of the edge direction.
    const out: P = [q[1] - p[1], -(q[0] - p[0])];
    wall(b, p, q, y, ya, out, scale(color, i % 2 === 0 ? 0.84 : 0.92));
    // The chamfer: from the outer edge at ya up and in to the inner edge at yb.
    const pi = inner[i], qi = inner[j];
    b.quad([p[0], ya, p[1]], [pi[0], yb, pi[1]], [qi[0], yb, qi[1]], [q[0], ya, q[1]], scale(color, 1.06));
  }
  // The top, a fan over the inner outline, facing up.
  for (let k = 1; k + 1 < n; k++) b.tri([inner[0][0], yb, inner[0][1]], [inner[k + 1][0], yb, inner[k + 1][1]], [inner[k][0], yb, inner[k][1]], color);
}

/**
 * Slim chamfered beads on the four vertical corners of a wall block from y0 to y1: each stands `out` proud of both faces and runs
 * `along` back along them, with a 45 degree face across the corner. 24 triangles.
 */
export function cornerBevels(b: MeshBuilder, x: number, z: number, w: number, d: number, y0: number, y1: number, color: Rgb, out = 0.2, along = 0.07): void {
  for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]] as const) {
    const cx = sx < 0 ? x : x + w, cz = sz < 0 ? z : z + d;
    const a: P = [cx + sx * out, cz - sz * along], a0: P = [cx, cz - sz * along];
    const bb: P = [cx - sx * along, cz + sz * out], b0: P = [cx - sx * along, cz];
    wall(b, a, bb, y0, y1, [sx, sz], scale(color, 1.08));
    wall(b, a0, a, y0, y1, [0, -sz], scale(color, 0.86));
    wall(b, b0, bb, y0, y1, [-sx, 0], scale(color, 0.86));
  }
}
