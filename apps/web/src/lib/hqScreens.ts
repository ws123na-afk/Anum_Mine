/**
 * Loads the live screens chunk when a building scene with screens opens, and keeps its atlas fed (docs/ANUM_MINE_HQ_LIVE_SCREENS.md).
 * This file is the only screens code in the headquarters chunk: the drawing, the model and the layout stay in `hq-screens/`, a lazy chunk.
 */
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { SceneScreen } from './buildingScene.ts';
import type { RenderItem } from './hqRenderTypes.ts';
import type { CameraState } from './headquartersSpatial.ts';
import { cameraEye } from './headquartersSpatial.ts';
import type { RoomGeometry, SceneFloor } from './headquartersProjection.ts';
import { presenceForDesk, type HqPresenceIndex } from './hqPresence.ts';
import type { DeskVisual } from './buildingScene.ts';
import type { WallModel } from './hqCommandWall.ts';
import type { ModuleState, TyreLive } from './hqModules.ts';

type Mod = typeof import('../hq-screens/index.ts');
type Feed = ReturnType<Mod['startScreenFeed']>;

export type ScreensArgs = Readonly<{
  screens: readonly SceneScreen[];
  floor: SceneFloor;
  floorIndex: number;
  floorY: number;
  presence: HqPresenceIndex | null;
  deskState: ReadonlyMap<string, DeskVisual>;
  flow: OfficeFlowSnapshot | null;
  wall: Readonly<{ model: WallModel; isOperations: (room: RoomGeometry) => boolean }> | null;
  tyre: ModuleState<TyreLive> | null;
  buildingName: string;
  live: boolean;
  reducedMotion: boolean;
  camera: RefObject<CameraState>;
}>;

export type ScreensState = Readonly<{ items: readonly RenderItem[]; atlas: Readonly<{ canvas: HTMLCanvasElement | null; version: number }> | null; texts: string }>;
const NONE: ScreensState = { items: [], atlas: null, texts: '' };

export function useScreens(a: ScreensArgs): ScreensState {
  const [mod, setMod] = useState<Mod | null>(null);
  const mounted = useMemo(() => (a.wall ? a.floor.rooms.filter((r) => a.wall!.isOperations(r)).map((r) => ({ id: r.id, x: r.x, z: r.z, width: r.width, depth: r.depth, doorSide: r.doorSide ?? null })) : []), [a.floor, a.wall]);
  const want = a.screens.some((s) => s.floorIndex === a.floorIndex) || mounted.length > 0;
  useEffect(() => {
    if (!want || mod) return undefined;
    let dead = false;
    void import('../hq-screens/index.ts').then((m) => { if (!dead) setMod(m); });
    return () => { dead = true; };
  }, [want, mod]);

  // The layer depends on where the screens are, never on what they say: a presence change rebuilds the scene but not the screens.
  const signature = useMemo(() => [a.floorIndex, a.floorY, ...a.screens.map((s) => `${s.kind}${s.roomId}${s.id}${s.floorIndex}${s.p.map((v) => v.toFixed(3)).join(',')}`), ...mounted.map((m) => `${m.id}${m.x}${m.z}${m.width}${m.depth}${m.doorSide}`)].join(';'), [a.screens, a.floorIndex, a.floorY, mounted]);
  const keep = useRef<{ sig: string; layer: ReturnType<Mod['screenLayer']> | null }>({ sig: '', layer: null });
  const layer = useMemo(() => {
    if (!mod) return null;
    if (keep.current.sig !== signature || !keep.current.layer) keep.current = { sig: signature, layer: mod.screenLayer(a.screens, a.floorIndex, mounted, a.floorY) };
    return keep.current.layer;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mod, signature]);

  const latest = useRef(a);
  latest.current = a;
  const records = useMemo(() => (mod ? mod.deskRecords(a.flow) : null), [mod, a.flow]);
  const recordsRef = useRef(records);
  recordsRef.current = records;

  const [state, setState] = useState<{ canvas: HTMLCanvasElement | null; version: number; texts: string }>({ canvas: null, version: 0, texts: '' });
  const feed = useRef<Feed | null>(null);
  useEffect(() => {
    if (!mod || !layer || layer.sites.length === 0) { feed.current = null; return undefined; }
    const f = mod.startScreenFeed({
      layer,
      read: () => {
        const cur = latest.current;
        const monitors = new Map<string, import('../hq-screens/screenModel.ts').MonitorSource>();
        for (const desk of cur.floor.desks) {
          const p = presenceForDesk(cur.presence, desk);
          monitors.set(desk.id, {
            visual: cur.deskState.get(desk.id) ?? 'vacant',
            presence: p ? { status: p.status, stale: p.stale, observedAt: p.observedAt } : null,
            record: recordsRef.current?.get(desk.id) ?? null,
          });
        }
        const eye = cameraEye(cur.camera.current);
        return {
          monitors, eye: [eye.x, eye.y, eye.z] as const, reducedMotion: cur.reducedMotion, now: Date.now(),
          wall: cur.wall ? { wall: cur.wall.model, flow: { status: 'ready', snapshot: cur.flow }, tyre: cur.tyre, buildingName: cur.buildingName, live: cur.live } : null,
        };
      },
      onDraw: (atlas) => setState({ canvas: atlas.canvas, version: atlas.version, texts: mod.screenLines(atlas) }),
    });
    feed.current = f;
    return () => { f.stop(); feed.current = null; };
  }, [mod, layer]);
  // New data is drawn at once (the one second rule per cell still holds).
  useEffect(() => { feed.current?.touch(); }, [a.presence, a.deskState, a.flow, a.wall?.model, a.tyre, a.live, a.buildingName, records, a.reducedMotion]);

  return useMemo(() => (layer && layer.sites.length && state.canvas ? { items: layer.items, atlas: { canvas: state.canvas, version: state.version }, texts: state.texts } : NONE), [layer, state]);
}
