/**
 * Screen-space placement of label cards: project each anchor, then push overlapping cards upward in
 * priority order so every card stays readable. Pure maths (`layoutLabels`) plus a thin DOM writer.
 */
import { ndcToPixels, projectToNdc, type Mat4 } from './hqMath.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type LabelInput = Readonly<{ id: string; anchor: Vec3; maxDistance?: number; priority?: number }>;
export type LabelPlacement = Readonly<{ id: string; x: number; y: number; depth: number; shown: boolean; shifted: number }>;
export type Size = Readonly<{ w: number; h: number }>;

const MAX_SHIFTS = 7;

/**
 * Places labels with bottom-centre at the projected anchor. A label that collides with a placed one
 * takes the nearest free spot (up first, then down or sideways), and is hidden when none is found unless it has the top priority.
 */
export function layoutLabels(
  labels: readonly LabelInput[], vp: Mat4, width: number, height: number, sizeOf: (id: string) => Size, bottomInset = 0,
): LabelPlacement[] {
  type Item = { input: LabelInput; x: number; y: number; depth: number; size: Size };
  const items: Item[] = [];
  const out = new Map<string, LabelPlacement>();
  for (const input of labels) {
    const p = projectToNdc(vp, input.anchor);
    const px = ndcToPixels(p, width, height);
    const away = input.maxDistance !== undefined && p.depth > input.maxDistance;
    const inView = p.visible && !away && px.x > -60 && px.x < width + 60 && px.y > -90 && px.y < height + 120;
    if (!inView) { out.set(input.id, { id: input.id, x: 0, y: 0, depth: p.depth, shown: false, shifted: 0 }); continue; }
    items.push({ input, x: px.x, y: px.y, depth: p.depth, size: sizeOf(input.id) });
  }
  items.sort((a, b) => (a.input.priority ?? 1) - (b.input.priority ?? 1) || a.depth - b.depth);
  const placed: Array<{ x0: number; y0: number; x1: number; y1: number }> = [];
  for (const item of items) {
    const { w, h } = item.size;
    const overlaps = (x: number, y: number) => placed.some((q) => x - w / 2 < q.x1 - 2 && x + w / 2 > q.x0 + 2 && y - h < q.y1 - 2 && y > q.y0 + 2);
    // `bottomInset` keeps cards clear of the toolbar along the bottom edge of the stage, so a card is never under a control.
    const limit = height - bottomInset;
    const inside = (x: number, y: number) => x - w / 2 >= 4 && x + w / 2 <= width - 4 && y - h >= 4 && y <= limit;
    // Candidate spots around the anchor, nearest first: straight up (the usual case), then down and sideways.
    const spots: Array<{ x: number; y: number; cost: number }> = [];
    for (let iy = -MAX_SHIFTS; iy <= MAX_SHIFTS; iy++) {
      for (let ix = -2; ix <= 2; ix++) {
        const x = item.x + ix * w * 1.05, y = item.y + iy * h * 1.05;
        spots.push({ x, y, cost: Math.abs(iy) * (iy < 0 ? 1 : 1.3) + Math.abs(ix) * 3 });
      }
    }
    spots.sort((p, q) => p.cost - q.cost);
    // The first spot is the anchor itself, pulled into the stage when the anchor sits just outside it.
    const home = { x: Math.max(w / 2 + 4, Math.min(width - w / 2 - 4, item.x)), y: Math.min(Math.max(item.y, h + 6), limit) };
    const pick = !overlaps(home.x, home.y) && inside(home.x, home.y) ? home : spots.find((c) => inside(c.x, c.y) && !overlaps(c.x, c.y));
    if (!pick) {
      // No free spot. Building and room cards are the way into the scene, so they stay (on the least covered spot);
      // only the secondary agent cards give way.
      if ((item.input.priority ?? 1) >= 2) { out.set(item.input.id, { id: item.input.id, x: home.x, y: home.y, depth: item.depth, shown: false, shifted: 0 }); continue; }
      const area = (x: number, y: number) => placed.reduce((sum, q) => sum + Math.max(0, Math.min(x + w / 2, q.x1) - Math.max(x - w / 2, q.x0)) * Math.max(0, Math.min(y, q.y1) - Math.max(y - h, q.y0)), 0);
      const best = spots.filter((c) => inside(c.x, c.y)).sort((p, q) => area(p.x, p.y) - area(q.x, q.y) || p.cost - q.cost)[0] ?? home;
      placed.push({ x0: best.x - w / 2, x1: best.x + w / 2, y0: best.y - h, y1: best.y });
      out.set(item.input.id, { id: item.input.id, x: best.x, y: best.y, depth: item.depth, shown: true, shifted: 0 });
      continue;
    }
    placed.push({ x0: pick.x - w / 2, x1: pick.x + w / 2, y0: pick.y - h, y1: pick.y });
    out.set(item.input.id, { id: item.input.id, x: pick.x, y: pick.y, depth: item.depth, shown: true, shifted: Math.round(Math.abs(pick.y - item.y) / (h * 1.05)) });
  }
  return labels.map((l) => out.get(l.id)!);
}

/** Writes placements to the label wrappers (display, transform, z-index) and caches card sizes. */
export function placeLabels(
  labels: readonly LabelInput[], nodes: ReadonlyMap<string, HTMLElement>, sizes: Map<string, Size>,
  vp: Mat4, width: number, height: number, bottomInset = 0,
): void {
  const sizeOf = (id: string): Size => {
    const cached = sizes.get(id);
    if (cached) return cached;
    const node = nodes.get(id);
    if (!node) return { w: 160, h: 44 };
    const prev = node.style.display;
    node.style.display = '';
    node.style.transform = 'translate3d(-9999px, 0, 0)';
    const size = { w: node.offsetWidth || 160, h: node.offsetHeight || 44 };
    node.style.display = prev;
    sizes.set(id, size);
    return size;
  };
  const placements = layoutLabels(labels, vp, width, height, sizeOf, bottomInset);
  for (let i = 0; i < labels.length; i++) {
    const node = nodes.get(labels[i].id);
    const p = placements[i];
    if (!node) continue;
    if (!p.shown) { if (node.style.display !== 'none') node.style.display = 'none'; continue; }
    node.style.display = '';
    node.style.transform = `translate3d(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px, 0) translate(-50%, -100%)`;
    node.style.zIndex = String(Math.max(1, Math.round(5000 - p.depth * 10) - (labels[i].priority ?? 0) * 1000));
  }
}
