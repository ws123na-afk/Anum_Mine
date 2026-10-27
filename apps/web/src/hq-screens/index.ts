/**
 * Entry of the live screens chunk, loaded by `useScreens` only when a building scene with screens opens (docs/ANUM_MINE_HQ_LIVE_SCREENS.md):
 * the screen sites and their quads, the atlas, and the feed that keeps it current. Nothing here reads anything but the whitelisted fields of
 * `screenModel.ts`; the main renderer only receives a canvas and a mesh class.
 */
import { monitorFace, wallFace, type MonitorSource, type WallInput } from './screenModel.ts';
import { ScreenAtlas, type CanvasMaker, type ScreenFace } from './screenDraw.ts';
import type { ScreenLayer } from './screenSites.ts';

export { screenLayer, type ScreenLayer, type ScreenSite, type FlatRoom } from './screenSites.ts';
export { deskRecords, SCREEN_FIELDS, type MonitorSource, type WallInput, type DeskRecord } from './screenModel.ts';
export { ScreenAtlas } from './screenDraw.ts';

/** How near (metres from the eye) a monitor shows its text, and a wall its full face. Hysteresis of 15 percent keeps a face from flickering at the edge. */
export const NEAR_MONITOR = 7;
export const NEAR_WALL = 45;

export type ScreenInputs = Readonly<{
  monitors: ReadonlyMap<string, MonitorSource>;
  /** The wall data, or null while the page has none (the wall then says Not connected). */
  wall: WallInput | null;
  eye: readonly [number, number, number];
  reducedMotion: boolean;
  now: number;
}>;

/** The face of every site of the layer for these inputs. */
export function screenFaces(layer: ScreenLayer, input: ScreenInputs, wasNear: Map<string, boolean>): Map<string, ScreenFace> {
  const out = new Map<string, ScreenFace>();
  for (const site of layer.sites) {
    const id = `${site.kind}:${site.cell}`;
    const d = Math.hypot(site.center[0] - input.eye[0], site.center[1] - input.eye[1], site.center[2] - input.eye[2]);
    const limit = site.kind === 'monitor' ? NEAR_MONITOR : NEAR_WALL;
    const near = wasNear.get(id) ? d < limit * 1.15 : d < limit;
    wasNear.set(id, near);
    if (site.kind === 'monitor') {
      const source = input.monitors.get(site.id);
      if (!source) continue;
      const face = monitorFace(source, input.now);
      out.set(id, { kind: 'monitor', face, near, aspect: site.aspect, animate: !input.reducedMotion, phase: Math.floor(input.now / 1000) });
    } else if (input.wall) {
      out.set(id, { kind: 'wall', face: wallFace(input.wall, input.now), near, aspect: site.aspect });
    }
  }
  return out;
}

export type ScreenFeed = Readonly<{ atlas: ScreenAtlas; touch: () => void; stop: () => void }>;

/**
 * Keeps an atlas current: `touch` reads the inputs now and draws what changed; a cell held back by the one second rule is drawn when it is due;
 * and a one second tick reads again so a record that stops being confirmed turns "Stale" and an elapsed time counts, without any new data.
 * The tick stops while the tab is hidden.
 */
export function startScreenFeed(args: Readonly<{ layer: ScreenLayer; read: () => ScreenInputs; onDraw: (atlas: ScreenAtlas) => void; tickMs?: number; make?: CanvasMaker; now?: () => number }>): ScreenFeed {
  const atlas = new ScreenAtlas(args.layer.layout, args.make);
  const clock = args.now ?? Date.now;
  const wasNear = new Map<string, boolean>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const touch = (): void => {
    if (stopped) return;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    const input = args.read();
    const result = atlas.update(screenFaces(args.layer, { ...input, now: clock() }, wasNear), clock());
    if (result.drawn > 0) args.onDraw(atlas);
    if (result.nextDueMs !== null) timer = setTimeout(touch, result.nextDueMs + 5);
  };
  const tick = setInterval(() => { if (typeof document === 'undefined' || document.visibilityState === 'visible') touch(); }, args.tickMs ?? 1000);
  touch();
  return { atlas, touch, stop: () => { stopped = true; clearInterval(tick); if (timer !== null) clearTimeout(timer); } };
}

/** What is on the screens right now, one line per screen (the stage's `data-screen-texts`, for QA: never an input). */
export function screenLines(atlas: ScreenAtlas): string {
  return [...atlas.drawn.entries()].map(([id, f]) => `${id}|${f.near ? 'near' : 'far'}|${f.kind === 'monitor' ? [f.face.headline, f.face.record, f.face.elapsed, f.face.foot, f.face.scan ? 'scan' : ''].filter(Boolean).join(' / ') : [f.face.tiles.map((x) => `${x.label} ${x.text}`).join(', '), f.face.agents.text, f.face.flowRows.join(', '), f.face.tyre ? f.face.tyre.items.map((x) => `${x.label} ${x.text}`).join(', ') + (f.face.tyre.note ?? '') : '', f.face.updated ?? '', f.face.statusText ?? ''].filter(Boolean).join(' / ')}`).join('\n');
}
