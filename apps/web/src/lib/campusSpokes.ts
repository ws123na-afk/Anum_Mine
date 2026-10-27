/**
 * Walkways from the roundabout at the centre of the campus to each building's door ("spokes").
 *
 * A building's door stands at the end of its front garden on the +z side, so a building south of the plaza has its door
 * on the side away from it. A spoke therefore cannot always be a straight line: it is routed on a coarse ground grid
 * round every footprint (A*, then straightened where the line of sight is clear), so it always reaches the door and never
 * crosses a building. Pure and deterministic: the same footprints and doors give the same spokes.
 */
import { PLAZA } from './campusModel.ts';

export type SpokeFootprint = Readonly<{ id: string; x0: number; x1: number; z0: number; z1: number }>;
export type SpokeDoor = Readonly<{ id: string; x: number; z: number }>;
export type Spoke = Readonly<{ id: string; points: ReadonlyArray<readonly [number, number]> }>;

/** Walkway width, and how far a walkway keeps from a footprint (half the width plus a margin). */
export const SPOKE_WIDTH = 4.6;
export const SPOKE_KEEP = 3.6;
/** The spoke starts on this circle: the outer edge of the roundabout's pavement. */
export const SPOKE_START = PLAZA.outer + 0.2;

const CELL = 2.5;

type Cell = { i: number; j: number };

class Heap {
  private a: Array<[number, number]> = [];
  get size(): number { return this.a.length; }
  push(k: number, v: number): void {
    const a = this.a; a.push([k, v]);
    let i = a.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (a[p][0] <= a[i][0]) break; [a[p], a[i]] = [a[i], a[p]]; i = p; }
  }
  pop(): [number, number] {
    const a = this.a, top = a[0], last = a.pop()!;
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1, r = l + 1; let m = i;
        if (l < a.length && a[l][0] < a[m][0]) m = l;
        if (r < a.length && a[r][0] < a[m][0]) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]]; i = m;
      }
    }
    return top;
  }
}

const hits = (f: SpokeFootprint, x: number, z: number, pad: number): boolean => x > f.x0 - pad && x < f.x1 + pad && z > f.z0 - pad && z < f.z1 + pad;

export function routeSpokes(doors: readonly SpokeDoor[], footprints: readonly SpokeFootprint[], extent: number): Spoke[] {
  const half = Math.ceil((extent + 4) / CELL) * CELL;
  const n = Math.round((half * 2) / CELL) + 1;
  const cx = (i: number) => -half + i * CELL, cz = (j: number) => -half + j * CELL;
  const blocked = new Uint8Array(n * n), near = new Uint8Array(n * n);
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const x = cx(i), z = cz(j);
    for (const f of footprints) {
      if (hits(f, x, z, SPOKE_KEEP)) { blocked[j * n + i] = 1; break; }
      if (hits(f, x, z, SPOKE_KEEP + 5)) near[j * n + i] = 1;
    }
  }
  const clear = (x0: number, z0: number, x1: number, z1: number): boolean => {
    const len = Math.hypot(x1 - x0, z1 - z0), steps = Math.max(1, Math.ceil(len / 1.2));
    for (let s = 0; s <= steps; s++) {
      const t = s / steps, x = x0 + (x1 - x0) * t, z = z0 + (z1 - z0) * t;
      if (footprints.some((f) => hits(f, x, z, SPOKE_KEEP - 0.4))) return false;
    }
    return true;
  };
  const out: Spoke[] = [];
  for (const door of doors) {
    const start: Cell = { i: Math.max(0, Math.min(n - 1, Math.round((door.x + half) / CELL))), j: Math.max(0, Math.min(n - 1, Math.round((door.z + half) / CELL))) };
    const key = (c: Cell) => c.j * n + c.i;
    const heuristic = (c: Cell) => Math.max(0, Math.hypot(cx(c.i), cz(c.j)) - SPOKE_START);
    const g = new Float32Array(n * n).fill(Infinity), from = new Int32Array(n * n).fill(-1);
    const heap = new Heap();
    g[key(start)] = 0; heap.push(heuristic(start), key(start));
    let goal = -1;
    while (heap.size) {
      const [, k] = heap.pop();
      const i = k % n, j = (k - i) / n;
      if (Math.hypot(cx(i), cz(j)) <= SPOKE_START) { goal = k; break; }
      for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) {
        if (!di && !dj) continue;
        const ni = i + di, nj = j + dj;
        if (ni < 0 || nj < 0 || ni >= n || nj >= n) continue;
        const nk = nj * n + ni;
        if (blocked[nk]) continue;
        if (di && dj && (blocked[j * n + ni] || blocked[nj * n + i])) continue;
        const step = (di && dj ? Math.SQRT2 : 1) * CELL * (1 + (near[nk] ? 0.9 : 0));
        if (g[k] + step < g[nk]) { g[nk] = g[k] + step; from[nk] = k; heap.push(g[nk] + heuristic({ i: ni, j: nj }), nk); }
      }
    }
    // The door first, then the cells of the route, then (when the search failed) a straight line.
    let pts: Array<[number, number]> = [[door.x, door.z]];
    if (goal >= 0) {
      const cells: Array<[number, number]> = [];
      for (let k = goal; k >= 0; k = from[k]) { const i = k % n, j = (k - i) / n; if (k !== key(start)) cells.push([cx(i), cz(j)]); }
      pts.push(...cells.reverse());
    }
    const last = pts[pts.length - 1];
    const d = Math.hypot(last[0], last[1]) || 1;
    if (d > SPOKE_START) pts.push([(last[0] / d) * SPOKE_START, (last[1] / d) * SPOKE_START]);
    // Straighten: from each point jump to the farthest later point it can see.
    const cut: Array<[number, number]> = [pts[0]];
    let at = 0;
    while (at < pts.length - 1) {
      let to = at + 1;
      for (let k = pts.length - 1; k > at + 1; k--) if (clear(pts[at][0], pts[at][1], pts[k][0], pts[k][1])) { to = k; break; }
      cut.push(pts[to]); at = to;
    }
    pts = cut;
    // The walk ends exactly on the start circle: cut the last stretch where it crosses it.
    const radius = (q: readonly [number, number]) => Math.hypot(q[0], q[1]);
    let keep = pts.length - 1;
    while (keep > 0 && radius(pts[keep - 1]) <= SPOKE_START) keep -= 1;
    pts = pts.slice(0, keep + 1);
    if (pts.length >= 2 && radius(pts[pts.length - 1]) < SPOKE_START) {
      const a = pts[pts.length - 2], c = pts[pts.length - 1];
      let lo = 0, hi = 1;
      for (let it = 0; it < 40; it++) { const mid = (lo + hi) / 2; if (radius([a[0] + (c[0] - a[0]) * mid, a[1] + (c[1] - a[1]) * mid]) > SPOKE_START) lo = mid; else hi = mid; }
      pts[pts.length - 1] = [a[0] + (c[0] - a[0]) * hi, a[1] + (c[1] - a[1]) * hi];
    }
    out.push({ id: door.id, points: pts.reverse() });
  }
  return out;
}
