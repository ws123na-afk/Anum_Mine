/**
 * Screen-space placement of label cards (docs/ANUM_MINE_HQ_LABELS.md): project each anchor, hide the ones a solid box
 * hides, give every label the richest density that fits (full card, compact icon and name, dot) and push the rest
 * apart in priority order so no two cards overlap. Pure maths (`layoutLabels`) plus a thin DOM writer that touches
 * the DOM only when a value changed and skips a frame whose inputs did not change.
 */
import type { Aabb, Mat4 } from './hqMath.ts';
import type { Vec3, SceneFloor } from './headquartersProjection.ts';

// This module is a lazy chunk of its own and imports nothing at run time: with imports from the page's chunk the bundler splits the
// shared chunks differently and the renderer's preload list grows. The constants and the two projection helpers below are copies of
// FLOOR_PITCH / SLAB (buildingScene.ts) and projectToNdc / ndcToPixels (hqMath.ts); test/hqLabelModel.test.ts keeps them equal.
export const FLOOR_PITCH_M = 4.2, SLAB_M = 0.3;
export function projectToNdc(vp: Mat4, p: Vec3) {
  const x = vp[0] * p.x + vp[4] * p.y + vp[8] * p.z + vp[12], y = vp[1] * p.x + vp[5] * p.y + vp[9] * p.z + vp[13];
  const z = vp[2] * p.x + vp[6] * p.y + vp[10] * p.z + vp[14], w = vp[3] * p.x + vp[7] * p.y + vp[11] * p.z + vp[15];
  if (w <= 1e-6) return { x: 0, y: 0, depth: w, visible: false };
  const nz = z / w;
  return { x: x / w, y: y / w, depth: w, visible: nz >= -1 && nz <= 1 };
}
export const ndcToPixels = (p: { x: number; y: number }, width: number, height: number) => ({ x: (p.x * 0.5 + 0.5) * width, y: (0.5 - p.y * 0.5) * height });

export type Density = 'full' | 'compact' | 'dot';

export type LabelInput = Readonly<{
  id: string; anchor: Vec3; maxDistance?: number; priority?: number;
  /** The densities this label may take, richest first. Without it the label is always a full card that may be pushed aside. */
  tiers?: readonly Density[];
  /** Solid boxes (floor slabs above the label) that hide the label while they stand between the eye and its anchor. */
  occluders?: readonly Aabb[];
  /** Other anchors for the same label (a floor tag sits on the edge of the floor nearest the eye). */
  alternatives?: readonly Vec3[];
  /** A large card (a billboard): search for a free spot in small steps around the anchor instead of whole card heights. */
  fine?: boolean;
}>;
/** `crowded`: no free spot at any density, so the card stays at its anchor unseen and unclickable but in the tab order, and a focus brings it forward. */
export type LabelPlacement = Readonly<{ id: string; x: number; y: number; depth: number; shown: boolean; shifted: number; density: Density; occluded: boolean; crowded?: boolean }>;
export type Size = Readonly<{ w: number; h: number }>;
export type Rect = Readonly<{ x0: number; y0: number; x1: number; y1: number }>;
/** `keepOut`: screen rectangles of the controls drawn over the scene; a card is never put under them. */
export type LayoutOptions = Readonly<{ eye?: Vec3 | null; keepOut?: readonly Rect[]; /** A phone-sized scene: no card is left out; one with no free spot sits on its anchor in its smallest density, even over another. */ squeeze?: boolean }>;

const MAX_SHIFTS = 7;

/** True when the straight line from the eye to the anchor passes through the box (slab test). */
export function segmentHitsBox(a: Vec3, b: Vec3, box: Aabb): boolean {
  let t0 = 0, t1 = 1;
  const axes = ['x', 'y', 'z'] as const;
  for (const k of axes) {
    const d = b[k] - a[k];
    if (Math.abs(d) < 1e-9) { if (a[k] < box.min[k] || a[k] > box.max[k]) return false; continue; }
    let u = (box.min[k] - a[k]) / d, v = (box.max[k] - a[k]) / d;
    if (u > v) [u, v] = [v, u];
    t0 = Math.max(t0, u); t1 = Math.min(t1, v);
    if (t0 > t1) return false;
  }
  return true;
}

export function isOccluded(eye: Vec3 | null | undefined, anchor: Vec3, boxes: readonly Aabb[] | undefined): boolean {
  if (!eye || !boxes || boxes.length === 0) return false;
  for (const box of boxes) if (segmentHitsBox(eye, anchor, box)) return true;
  return false;
}

const dist2 = (a: Vec3, b: Vec3) => (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2;

/**
 * Places labels with bottom-centre at the projected anchor. A label with `tiers` takes the richest density that is free at
 * its anchor (or one step away) and is hidden when even the last one collides, unless it has the top priority. A label
 * without `tiers` that collides takes the nearest free spot (up first, then down or sideways).
 */
export function layoutLabels(
  labels: readonly LabelInput[], vp: Mat4, width: number, height: number, sizeOf: (id: string, density: Density) => Size, bottomInset = 0, options: LayoutOptions = {},
): LabelPlacement[] {
  type Item = { input: LabelInput; x: number; y: number; depth: number };
  const items: Item[] = [];
  const out = new Map<string, LabelPlacement>();
  const eye = options.eye ?? null;
  const hide = (input: LabelInput, depth: number, occluded = false) => out.set(input.id, { id: input.id, x: 0, y: 0, depth, shown: false, shifted: 0, density: 'full', occluded });
  for (const input of labels) {
    let anchor = input.anchor;
    if (input.alternatives?.length && eye) {
      let best = Infinity;
      for (const a of [input.anchor, ...input.alternatives]) { const d = dist2(a, eye); if (d < best) { best = d; anchor = a; } }
    }
    if (isOccluded(eye, anchor, input.occluders)) { hide(input, 0, true); continue; }
    const p = projectToNdc(vp, anchor);
    const px = ndcToPixels(p, width, height);
    const away = input.maxDistance !== undefined && p.depth > input.maxDistance;
    const inView = p.visible && !away && px.x > -60 && px.x < width + 60 && px.y > -90 && px.y < height + 120;
    if (!inView) {
      // A building or room that is only off the edge of the stage stays in the page for keyboard and screen reader users (invisible,
      // shown where it can be reached when it takes focus); an agent card, or a card beyond its distance, is hidden.
      if (away || (input.priority ?? 1) >= 2) { hide(input, p.depth); continue; }
      const { w, h } = sizeOf(input.id, input.tiers?.[input.tiers.length - 1] ?? 'full');
      const x = Math.max(w / 2 + 4, Math.min(width - w / 2 - 4, Number.isFinite(px.x) ? px.x : width / 2));
      const y = Math.min(Math.max(Number.isFinite(px.y) ? px.y : height / 2, h + 6), height - bottomInset);
      out.set(input.id, { id: input.id, x, y, depth: p.depth, shown: false, shifted: 0, density: 'full', occluded: false, crowded: true });
      continue;
    }
    items.push({ input, x: px.x, y: px.y, depth: p.depth });
  }
  items.sort((a, b) => (a.input.priority ?? 1) - (b.input.priority ?? 1) || a.depth - b.depth);
  const placed: Array<{ x0: number; y0: number; x1: number; y1: number }> = [...(options.keepOut ?? [])];
  // `bottomInset` keeps cards clear of the toolbar along the bottom edge of the stage, so a card is never under a control.
  const limit = height - bottomInset;
  const set = (item: Item, x: number, y: number, size: Size, density: Density, shifted: number) => {
    placed.push({ x0: x - size.w / 2, x1: x + size.w / 2, y0: y - size.h, y1: y });
    out.set(item.input.id, { id: item.input.id, x, y, depth: item.depth, shown: true, shifted, density, occluded: false });
  };
  // A seated agent shows where it sits or gives way, so its smallest card is reserved first: a room card gives up detail
  // around it instead of hiding it, and the agent then takes the richest density that still fits.
  const reserved = new Map<string, { x0: number; y0: number; x1: number; y1: number }>();
  const clampHome = (item: Item, w: number, h: number) => ({ x: Math.max(w / 2 + 4, Math.min(width - w / 2 - 4, item.x)), y: Math.min(Math.max(item.y, h + 6), limit) });
  for (const item of items) {
    const tiers = item.input.tiers;
    if (!tiers || tiers.length < 2 || (item.input.priority ?? 1) < 2) continue;
    const { w, h } = sizeOf(item.input.id, tiers[tiers.length - 1]);
    const home = clampHome(item, w, h);
    reserved.set(item.input.id, { x0: home.x - w / 2, x1: home.x + w / 2, y0: home.y - h, y1: home.y });
  }
  for (const item of items) {
    const tiers: readonly Density[] = item.input.tiers?.length ? item.input.tiers : ['full'];
    const adaptive = tiers.length > 1;
    const lastIndex = tiers.length - 1;
    let done = false;
    for (let ti = 0; ti < tiers.length && !done; ti++) {
      const density = tiers[ti];
      const { w, h } = sizeOf(item.input.id, density);
      const hits = (q: { x0: number; y0: number; x1: number; y1: number }, x: number, y: number) => x - w / 2 < q.x1 - 2 && x + w / 2 > q.x0 + 2 && y - h < q.y1 - 2 && y > q.y0 + 2;
      const keepReserved = (item.input.priority ?? 1) > 0;
      const overlaps = (x: number, y: number) => placed.some((q) => hits(q, x, y)) || (keepReserved && [...reserved].some(([id, q]) => id !== item.input.id && hits(q, x, y)));
      const inside = (x: number, y: number) => x - w / 2 >= 4 && x + w / 2 <= width - 4 && y - h >= 4 && y <= limit;
      // The first spot is the anchor itself, pulled into the stage when the anchor sits just outside it.
      const home = clampHome(item, w, h);
      if (!overlaps(home.x, home.y) && inside(home.x, home.y)) { set(item, home.x, home.y, { w, h }, density, 0); done = true; break; }
      // A room card keeps its detail by stepping up (twice at most) before it gives any up; a seated agent never steps.
      if (adaptive && ti < lastIndex && (item.input.priority ?? 1) < 2) {
        const up = [1, 2].map((k) => ({ x: home.x, y: home.y - k * h * 1.05 })).find((c) => inside(c.x, c.y) && !overlaps(c.x, c.y));
        if (up) { set(item, up.x, up.y, { w, h }, density, Math.round((home.y - up.y) / (h * 1.05))); done = true; break; }
      }
      // An adaptive label first gives up detail (the next tier); only its last tier looks for another spot.
      // (A `fine` label, a large card, first looks for a free spot at this density and gives up detail only when there is none.)
      if (adaptive && ti < lastIndex && !item.input.fine) continue;
      // Candidate spots around the anchor, nearest first: straight up (the usual case), then down and sideways.
      const spots: Array<{ x: number; y: number; cost: number }> = [];
      // A secondary card (a seated agent) never moves off its anchor: it shows where it sits or gives way.
      const secondary = adaptive && (item.input.priority ?? 1) >= 2;
      const reach = secondary ? 0 : adaptive ? 4 : MAX_SHIFTS, sideways = secondary ? 0 : 2;
      if (item.input.fine) {
        for (let dy = -h * 1.5; dy <= h * 1.5; dy += 20) {
          for (let dx = -w * 1.6; dx <= w * 1.6; dx += 20) spots.push({ x: item.x + dx, y: item.y + dy, cost: (Math.abs(dy) + Math.abs(dx) * 0.7) / 20 });
        }
      } else {
        for (let iy = -reach; iy <= reach; iy++) {
          for (let ix = -sideways; ix <= sideways; ix++) {
            const x = item.x + ix * w * 1.05, y = item.y + iy * h * 1.05;
            spots.push({ x, y, cost: Math.abs(iy) * (iy < 0 ? 1 : 1.3) + Math.abs(ix) * 3 });
          }
        }
      }
      spots.sort((p, q) => p.cost - q.cost);
      const pick = spots.find((c) => inside(c.x, c.y) && !overlaps(c.x, c.y));
      if (pick) { set(item, pick.x, pick.y, { w, h }, density, Math.round(Math.abs(pick.y - item.y) / (h * 1.05))); done = true; break; }
      if (adaptive && ti < lastIndex) continue;
      // No free spot. Building and room cards are the way into the scene, so they stay (on the least covered spot);
      // only the secondary agent cards give way, and so does a crowded label that has a dot to fall back on.
      if (options.squeeze && adaptive) { set(item, home.x, home.y, { w, h }, density, 0); done = true; break; }
      if ((item.input.priority ?? 1) >= 2 || (adaptive && (item.input.priority ?? 1) >= 1)) { out.set(item.input.id, { id: item.input.id, x: home.x, y: home.y, depth: item.depth, shown: false, shifted: 0, density, occluded: false, crowded: true }); done = true; break; }
      const area = (x: number, y: number) => placed.reduce((sum, q) => sum + Math.max(0, Math.min(x + w / 2, q.x1) - Math.max(x - w / 2, q.x0)) * Math.max(0, Math.min(y, q.y1) - Math.max(y - h, q.y0)), 0);
      const best = spots.filter((c) => inside(c.x, c.y)).sort((p, q) => area(p.x, p.y) - area(q.x, q.y) || p.cost - q.cost)[0] ?? home;
      set(item, best.x, best.y, { w, h }, adaptive ? tiers[0] : density, 0);
      done = true;
    }
  }
  return labels.map((l) => out.get(l.id)!);
}

type Memo = { vp: Float32Array; width: number; height: number; inset: number; labels: readonly LabelInput[]; eye: string; keep: string };
const memos = new WeakMap<object, Memo>();

/** Writes placements to the label wrappers (display, transform, z-index, density) and caches card sizes. */
export function placeLabels(
  labels: readonly LabelInput[], nodes: ReadonlyMap<string, HTMLElement>, sizes: Map<string, Size>,
  vp: Mat4, width: number, height: number, bottomInset = 0, eye: Vec3 | null = null, keepOut: readonly Rect[] = [],
): void {
  const eyeKey = eye ? `${eye.x.toFixed(2)},${eye.y.toFixed(2)},${eye.z.toFixed(2)}` : '';
  const keep = keepOut.map((r) => `${Math.round(r.x0)},${Math.round(r.y0)},${Math.round(r.x1)},${Math.round(r.y1)}`).join(';');
  const prev = memos.get(sizes);
  // A frame with the same camera, size and labels would write the same values: skip it.
  if (prev && prev.labels === labels && prev.width === width && prev.height === height && prev.inset === bottomInset && prev.eye === eyeKey && prev.keep === keep && prev.vp.every((v, i) => v === vp[i])) return;
  memos.set(sizes, { vp: Float32Array.from(vp), width, height, inset: bottomInset, labels, eye: eyeKey, keep });
  const sizeOf = (id: string, density: Density): Size => {
    const key = `${id}|${density}`;
    const cached = sizes.get(key);
    if (cached) return cached;
    const node = nodes.get(id);
    if (!node) return { w: 160, h: 44 };
    const prevDisplay = node.style.display, prevDensity = node.dataset.density;
    node.style.display = '';
    node.style.transform = 'translate3d(-9999px, 0, 0)';
    node.dataset.density = density;
    const size = { w: node.offsetWidth || 160, h: node.offsetHeight || 44 };
    node.style.display = prevDisplay;
    if (prevDensity === undefined) delete node.dataset.density; else node.dataset.density = prevDensity;
    sizes.set(key, size);
    return size;
  };
  const placements = layoutLabels(labels, vp, width, height, sizeOf, bottomInset, { eye, keepOut, squeeze: width < 641 });
  for (let i = 0; i < labels.length; i++) {
    const node = nodes.get(labels[i].id);
    const p = placements[i];
    if (!node) continue;
    node.dataset.occluded = p.occluded ? 'true' : 'false';
    const crowded = !p.shown && p.crowded === true;
    const hidden = crowded ? 'crowded' : 'no';
    if (node.dataset.hidden !== hidden) node.dataset.hidden = hidden;
    if (!p.shown && !crowded) { if (node.style.display !== 'none') node.style.display = 'none'; continue; }
    if (node.dataset.density !== p.density) node.dataset.density = p.density;
    node.style.display = '';
    node.style.transform = `translate3d(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px, 0) translate(-50%, -100%)`;
    node.style.zIndex = String(Math.max(1, Math.round(5000 - p.depth * 10) - (labels[i].priority ?? 0) * 1000));
  }
}

/**
 * Reads the rectangles (relative to the stage) of the elements matching `selector` next to the stage, and again whenever one of them or the
 * stage changes size. Returns the function that stops it.
 */
export function bindKeepOut(stage: HTMLElement, selector: string, onChange: (rects: readonly Rect[]) => void): () => void {
  const scope = stage.parentElement;
  if (!scope) return () => undefined;
  const read = () => {
    const box = stage.getBoundingClientRect();
    // The scene's width steps for the chrome's CSS (a CSS size container would change how the page's columns are sized).
    for (const w of [641, 700, 1340, 1600]) { const v = box.width >= w ? '1' : '0'; if (scope.dataset['s' + w] !== v) scope.dataset['s' + w] = v; }
    onChange([...scope.querySelectorAll<HTMLElement>(selector)].map((el) => {
      const r = el.getBoundingClientRect();
      return { x0: r.left - box.left - 6, y0: r.top - box.top - 6, x1: r.right - box.left + 6, y1: r.bottom - box.top + 6 };
    }).filter((r) => r.x1 - r.x0 > 14 && r.y1 - r.y0 > 14));
  };
  read();
  const observer = new ResizeObserver(read);
  observer.observe(stage);
  scope.querySelectorAll<HTMLElement>(selector).forEach((el) => observer.observe(el));
  return () => observer.disconnect();
}

/* The stacked view's extras: the slab of each floor as a solid box, the slabs over a floor, and one tag per floor on its edge nearest the eye. */

export type RoomRect = Readonly<{ x0: number; x1: number; z0: number; z1: number }>;

/** The ground extent of a floor: its rooms' rectangles. Null for an empty floor. */
export function floorExtent(floor: Pick<SceneFloor, 'rooms'>): RoomRect | null {
  if (!floor.rooms.length) return null;
  let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (const r of floor.rooms) { x0 = Math.min(x0, r.x); x1 = Math.max(x1, r.x + r.width); z0 = Math.min(z0, r.z); z1 = Math.max(z1, r.z + r.depth); }
  return { x0, x1, z0, z1 };
}

/** The slab under floor `index` as a solid box, a little wider than its rooms (the floor plate overhangs). */
export function slabBox(extent: RoomRect, index: number, pitch = FLOOR_PITCH_M): Aabb {
  const y = index * pitch;
  return { min: { x: extent.x0 - 0.6, y: y - SLAB_M, z: extent.z0 - 0.6 }, max: { x: extent.x1 + 0.6, y: y + 0.02, z: extent.z1 + 0.6 } };
}

/** For each floor, the slabs of the floors above it: what can stand between the eye and a label hung inside that floor. */
export function slabsAbove(floors: readonly Pick<SceneFloor, 'rooms'>[], pitch = FLOOR_PITCH_M): Aabb[][] {
  const slabs = floors.map((f, i) => { const e = floorExtent(f); return e ? slabBox(e, i, pitch) : null; });
  return floors.map((_, i) => slabs.slice(i + 1).filter((b): b is Aabb => b !== null));
}


export type FloorTagSpec = Readonly<{
  id: string; kind: 'floor'; floor: number; floorId: string; anchor: Vec3; alternatives: readonly Vec3[]; priority: number; tiers: readonly Density[]; maxDistance: number;
}>;


/** One tag per floor for the stacked view: it sits on the floor's edge nearest the eye, so no slab can cover it. */
export function floorTagSpecs(args: Readonly<{ floors: readonly Pick<SceneFloor, 'id' | 'rooms'>[]; activeIndex: number; pitch?: number }>): FloorTagSpec[] {
  const pitch = args.pitch ?? FLOOR_PITCH_M;
  const out: FloorTagSpec[] = [];
  args.floors.forEach((floor, fi) => {
    const e = floorExtent(floor);
    if (!e) return;
    const y = fi * pitch + 1.1, cx = (e.x0 + e.x1) / 2, cz = (e.z0 + e.z1) / 2, out1 = 1.6;
    const sides: Vec3[] = [{ x: cx, y, z: e.z0 - out1 }, { x: cx, y, z: e.z1 + out1 }, { x: e.x0 - out1, y, z: cz }, { x: e.x1 + out1, y, z: cz }];
    out.push({ id: `floor:${floor.id}`, kind: 'floor', floor: fi, floorId: floor.id, anchor: sides[0], alternatives: sides.slice(1), priority: fi === args.activeIndex ? 0 : 1, tiers: ['full', 'compact'], maxDistance: 200 });
  });
  return out;
}
