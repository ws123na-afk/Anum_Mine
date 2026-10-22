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
 * moves up by 60% of its height (down once the top edge is reached), and is hidden when no free spot is found unless it has the top priority.
 */
export function layoutLabels(
  labels: readonly LabelInput[], vp: Mat4, width: number, height: number, sizeOf: (id: string) => Size,
): LabelPlacement[] {
  type Item = { input: LabelInput; x: number; y: number; depth: number; size: Size };
  const items: Item[] = [];
  const out = new Map<string, LabelPlacement>();
  for (const input of labels) {
    const p = projectToNdc(vp, input.anchor);
    const px = ndcToPixels(p, width, height);
    const away = input.maxDistance !== undefined && p.depth > input.maxDistance;
    const inView = p.visible && !away && px.x > -24 && px.x < width + 24 && px.y > -24 && px.y < height + 120;
    if (!inView) { out.set(input.id, { id: input.id, x: 0, y: 0, depth: p.depth, shown: false, shifted: 0 }); continue; }
    items.push({ input, x: px.x, y: px.y, depth: p.depth, size: sizeOf(input.id) });
  }
  items.sort((a, b) => (a.input.priority ?? 1) - (b.input.priority ?? 1) || a.depth - b.depth);
  const placed: Array<{ x0: number; y0: number; x1: number; y1: number }> = [];
  for (const item of items) {
    const { w, h } = item.size;
    const minY = h + 6;
    // A card never leaves the stage sideways: near an edge it slides in, so it is always whole and clickable.
    const x = Math.max(w / 2 + 4, Math.min(width - w / 2 - 4, item.x));
    let y = Math.max(item.y, minY);
    let shifts = 0;
    const box = () => ({ x0: x - w / 2, x1: x + w / 2, y0: y - h, y1: y });
    const clash = () => { const b = box(); return placed.some((q) => b.x0 < q.x1 - 2 && b.x1 > q.x0 + 2 && b.y0 < q.y1 - 2 && b.y1 > q.y0 + 2); };
    // Up first (stays near the building), then down when the top edge of the stage is reached.
    while (clash() && shifts < MAX_SHIFTS && y - h * 0.6 >= minY) { y -= h * 0.6; shifts += 1; }
    let down = 0;
    while (clash() && down < MAX_SHIFTS) { y += h * 0.6; down += 1; shifts += 1; }
    const keep = !clash() || (item.input.priority ?? 1) === 0;
    if (!keep) { out.set(item.input.id, { id: item.input.id, x: item.x, y: item.y, depth: item.depth, shown: false, shifted: shifts }); continue; }
    placed.push(box());
    out.set(item.input.id, { id: item.input.id, x, y, depth: item.depth, shown: true, shifted: shifts });
  }
  return labels.map((l) => out.get(l.id)!);
}

/** Writes placements to the label wrappers (display, transform, z-index) and caches card sizes. */
export function placeLabels(
  labels: readonly LabelInput[], nodes: ReadonlyMap<string, HTMLElement>, sizes: Map<string, Size>,
  vp: Mat4, width: number, height: number,
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
  const placements = layoutLabels(labels, vp, width, height, sizeOf);
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
