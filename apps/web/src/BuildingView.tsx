/**
 * Building cutaway: floors, rooms as zones, desks and agent cards from the saved office layout.
 * Presence comes in as props (workstation evidence plus live meeting participants); this view
 * never invents a status. Module panels (commerce, tyre) show only for connected modules.
 */
import type { AvatarAppearance } from '@anum/contracts';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { ChangeBatch } from './useOfficeFlow';
import { useFlowLabels } from './HqFlowStage';
import { ArrowLeft, ChevronDown, DoorOpen, Layers } from 'lucide-react';
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

const SceneBackCard = lazy(() => import('./HqSceneStrips').then((m) => ({ default: m.SceneBackCard })));
const FloorSelector = lazy(() => import('./HqSceneStrips').then((m) => ({ default: m.FloorSelector })));
const LiveOpsStrip = lazy(() => import('./HqSceneStrips').then((m) => ({ default: m.LiveOpsStrip })));
const FlowStrip = lazy(() => import('./HqSceneStrips').then((m) => ({ default: m.FlowStrip })));
const CommercePanels = lazy(() => import('./BuildingPanels').then((m) => ({ default: m.CommercePanels })));
const FloorRooms = lazy(() => import('./BuildingPanels').then((m) => ({ default: m.FloorRooms })));
const TyrePanel = lazy(() => import('./BuildingPanels').then((m) => ({ default: m.TyrePanel })));
const HqCommandWall = lazy(() => import('./HqCommandWall').then((m) => ({ default: m.HqCommandWall })));
import { BottomToolbar, CameraTools, TotalsPanel } from './HqChrome';
import { AgentLabelCard, FloorTagCard, RoomLabelCard } from './HqLabelCards';
import { roomLabelSpecs, roomSubtitle } from './lib/hqLabelModel.ts';
import { HqStage, useAspect, type StageLabel } from './HqStage';
import { visualOfStatus } from './hqAssets/interior/stateVisual.ts';
import { buildBuildingScene, FLOOR_PITCH, WALL_HEIGHT, type DeskVisual } from './lib/buildingScene.ts';
import { CUT_PITCH, STACK_AUTO_MAX, cutLifts, facingSides, snapYaw, type CutawayInfo, type Facade, type Side } from './lib/hqCutaway.ts';
import { HOME_YAW, buildingFitCamera, floorOverviewCamera, clampInside, clampTarget, insideCamera, isInsideView, faceNorth, rotateBy, threeDView, topView, zoomBy } from './lib/hqCamera.ts';
import { dolly, type CameraState } from './lib/headquartersSpatial.ts';
import type { RoomGeometry, SceneBuilding, SceneDesk, SceneFloor } from './lib/headquartersProjection.ts';
import { deskShiftLine, WORK_STATUSES } from './lib/hqShifts.ts';
import { deskVisual } from './lib/hqDeskState.ts';
import { presenceForDesk, totalsOf, type HqPresenceIndex } from './lib/hqPresence.ts';
import type { StatusKey } from './lib/campusModel.ts';
import type { HqView } from './HqChrome';
import type { CommerceLive, ModuleState, TyreLive } from './lib/hqModules.ts';
import { buildingModules } from './lib/hqBuildingModules.ts';
import { floorLifts } from './lib/hqFloorLift.ts';
import { LIFT_MS, easeLift } from './lib/hqRenderTypes.ts';
import type { PickTarget } from './lib/hqMath.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { roomName, sceneName } from './lib/registryNames.ts';
import { useLocale } from './useLocale';
import { useArabicSignFont } from './lib/arabicFont.ts';
import type { HqNavHandles } from './hq-nav/HqNavigation';
import type { WallModel } from './lib/hqCommandWall.ts';

export type BuildingViewProps = Readonly<{
  workspaceName: string;
  kindLabel: string;
  buildings: readonly SceneBuilding[];
  building: SceneBuilding;
  floor: SceneFloor;
  presence: HqPresenceIndex | null;
  unreported: readonly StatusKey[];
  verifiedAt: string | null;
  liveMeetings: number | null;
  liveMeetingRoomIds: ReadonlySet<string>;
  commerce: ModuleState<CommerceLive>;
  tyre: ModuleState<TyreLive>;
  view: Exclude<HqView, 'list'>;
  night: boolean;
  reducedMotion: boolean;
  fullscreen: boolean;
  selectedRoomId: string | null;
  selectedAgentId: string | null;
  onBack: () => void;
  onSelectBuilding: (id: string) => void;
  onSelectFloor: (id: string) => void;
  onSelectRoom: (room: RoomGeometry | null) => void;
  onSelectAgent: (agentId: string, desk: SceneDesk) => void;
  onManageSpaces: () => void;
  onToggleFullscreen: () => void;
  onUnavailable: (reason: string) => void;
  onNight: (night: boolean) => void;
  /** Floor overview: the whole active floor from above with every room labelled (state lives in the page so the floor rail can switch it too). */
  overview: boolean;
  onOverview: (on: boolean) => void;
  /** The navigation layer's camera bridge and thumbnail capture (flights, snapshots). */
  nav?: HqNavHandles;
  /** The open room or workstation panel, drawn over the scene (stacked under it on narrow screens). */
  side: ReactNode;
  /** Stored work per room and the last move the server reported, for the live-flow badges and markers. */
  flow?: Readonly<{ snapshot: OfficeFlowSnapshot | null; batch: ChangeBatch; onPickRoom: (roomId: string) => void }>;
  /** Avatar style and department per agent id, from the agent profiles; an agent with no entry is drawn with the default style. */
  agentStyles?: ReadonlyMap<string, AgentStyleInfo>;
  /** The operations wall: real numbers (or "Not connected") drawn over an operations room while it is selected. */
  wall?: Readonly<{ model: WallModel; isOperations: (room: RoomGeometry) => boolean; onReload: () => void }>;
}>;

export type AgentStyleInfo = Readonly<{ avatar: AvatarAppearance | null; department: string | null }>;


export function BuildingView(props: BuildingViewProps) {
  const { building, floor, view, night, presence } = props;
  const locale = useLocale();
  const fontsReady = useArabicSignFont(locale === 'ar');
  const floorTitle = sceneName(floor, locale);
  const floors = building.floors;
  const activeIndex = Math.max(0, floors.findIndex((f) => f.id === floor.id));
  // The section view: every floor at once, the walls facing the camera and the roof taken away (docs/ANUM_MINE_HQ_CUTAWAY.md).
  const [showAll, setShowAll] = useState(() => floors.length <= STACK_AUTO_MAX);
  const [facade, setFacade] = useState<Facade>('cutaway');
  const [cutSides, setCutSides] = useState<readonly Side[]>(() => facingSides(35));
  const [facadeSaid, setFacadeSaid] = useState(false);
  const [panMode, setPanMode] = useState(false);
  const deskState = useMemo(() => {
    const map = new Map<string, DeskVisual>();
    for (const f of floors) {
      for (const d of f.desks) {
        const p = presenceForDesk(presence, d);
        map.set(d.id, deskVisual(d, p));
      }
    }
    return map;
  }, [floors, presence]);

  const people = useMemo(() => {
    const map = new Map<string, string>();
    for (const f of floors) for (const d of f.desks) {
      const p = presenceForDesk(presence, d);
      if (d.agentId || p) map.set(d.id, d.agentId ?? p?.agentId ?? d.id);
    }
    return map;
  }, [floors, presence]);
  const looks = useMemo(() => {
    const map = new Map<string, AgentStyleInfo>();
    for (const [deskId, agentId] of people) { const info = props.agentStyles?.get(agentId); if (info) map.set(deskId, info); }
    return map;
  }, [people, props.agentStyles]);
  // The overview and the top view look straight down at the open floor: the floors above would hide it, so they stay out of those views.
  const stackOn = showAll && !props.overview && view !== 'top';
  const scene = useMemo(
    () => buildBuildingScene(floors, { activeIndex, showAll: stackOn, cutaway: true, selectedRoomId: props.selectedRoomId, deskState, meetingRoomIds: props.liveMeetingRoomIds, people, looks, locale }),
    [floors, activeIndex, stackOn, props.selectedRoomId, deskState, props.liveMeetingRoomIds, people, looks, locale],
  );
  const [wrap, aspect] = useAspect();
  const { overview } = props;
  // The home camera fits the footprint, the turn of the isometric view and the shape of the stage; the overview camera fits the active floor from above.
  const fp = scene.footprint;
  const topY = (stackOn ? floors.length - 1 : activeIndex) * FLOOR_PITCH;
  // The azimuth the home camera looks from: a diagonal view (HOME_YAW plus a multiple of 90) the rotate buttons step through.
  const [homeYaw, setHomeYaw] = useState(HOME_YAW);
  const fitBox = useMemo(
    () => ({ x0: fp.x0 - 1, x1: fp.x1 + 1, z0: fp.z0 - 1, z1: fp.z1 + 1, y0: (stackOn ? 0 : activeIndex * FLOOR_PITCH) - 0.4, y1: topY + WALL_HEIGHT + 0.4 }),
    [fp.x0, fp.x1, fp.z0, fp.z1, activeIndex, stackOn, topY]);
  const buildingHome = useMemo(() => buildingFitCamera(fitBox, aspect, CUT_PITCH, homeYaw), [fitBox, aspect, homeYaw]);
  const overviewHome = useMemo(
    () => floorOverviewCamera({ x0: fp.x0 - 0.6, x1: fp.x1 + 0.6, z0: fp.z0 - 0.6, z1: fp.z1 + 0.6, y0: activeIndex * FLOOR_PITCH, y1: activeIndex * FLOOR_PITCH + WALL_HEIGHT }, aspect),
    [fp.x0, fp.x1, fp.z0, fp.z1, activeIndex, aspect]);
  const home = overview ? overviewHome : buildingHome;
  const [camera, setCamera] = useState<CameraState>(home);
  const homeRef = useRef(home);
  const turning = useRef(false);
  const shownFloor = useRef(activeIndex);
  // A short eased glide of the camera to the new floor's framing. It makes no announcement of its own (the floor note does) and a
  // person's own camera input or the next change of floor ends it.
  const glideRaf = useRef<number | null>(null);
  const stopGlide = () => { if (glideRaf.current !== null) { cancelAnimationFrame(glideRaf.current); glideRaf.current = null; } };
  const glide = (to: CameraState) => {
    if (glideRaf.current !== null) cancelAnimationFrame(glideRaf.current);
    const from = cameraRef.current;
    const t0 = performance.now();
    const step = (now: number) => {
      const k = easeLift((now - t0) / LIFT_MS);
      const mix = (a: number, b: number) => a + (b - a) * k;
      setCamera({ ...to, target: { x: mix(from.target.x, to.target.x), y: mix(from.target.y, to.target.y), z: mix(from.target.z, to.target.z) }, distance: mix(from.distance, to.distance), yaw: k >= 1 ? ((to.yaw % 360) + 360) % 360 : mix(from.yaw, to.yaw), pitch: mix(from.pitch, to.pitch) });
      glideRaf.current = k < 1 ? requestAnimationFrame(step) : null;
    };
    glideRaf.current = requestAnimationFrame(step);
  };
  useEffect(() => () => { if (glideRaf.current !== null) cancelAnimationFrame(glideRaf.current); }, []);
  // Inside holds its own room: closing the room panel (to see the room) must not throw the camera back out.
  const [insideRoomId, setInsideRoomId] = useState<string | null>(null);
  const inside = insideRoomId !== null;
  const insideRef = useRef<string | null>(null);
  insideRef.current = insideRoomId;
  const setInside = useCallback((on: boolean) => { if (!on) setInsideRoomId(null); }, []);
  useEffect(() => {
    if (homeRef.current === home) return;
    homeRef.current = home;
    if (turning.current) { turning.current = false; return; } // a rotate snap already flies there
    if (insideRef.current) return; // the Inside view keeps its own camera
    const next = view === 'top' ? topView(home) : home;
    // A change of floor glides the camera to the new floor (reduced motion jumps); any other new framing is applied at once.
    const floorChanged = shownFloor.current !== activeIndex;
    shownFloor.current = activeIndex;
    if (floorChanged && !latest.current.reducedMotion) glide(next);
    else { stopGlide(); setCamera(next); } // a glide still running toward an older framing must not overwrite the new one
  }, [home, view]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setInside(false); setCamera((c) => (view === 'top' ? topView(isInsideView(c) ? homeRef.current : c) : threeDView(isInsideView(c) ? homeRef.current : c))); }, [view]);

  const latest = useRef(props);
  latest.current = props;
  const roomById = useMemo(() => new Map(floor.rooms.map((r) => [r.id, r])), [floor.rooms]);

  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  // Which outer walls face the camera, with a dead band (hqCutaway.ts); frozen while the camera is inside a room.
  useEffect(() => {
    if (isInsideView(camera)) return;
    setCutSides((p) => { const n = facingSides(camera.yaw, p); return n[0] === p[0] && n[1] === p[1] ? p : n; });
  }, [camera]);
  const effectiveFacade: Facade = facade === 'cutaway' || props.overview || view === 'top' ? 'cutaway' : 'closed';
  const cut = useMemo((): CutawayInfo => ({ facade: effectiveFacade, cutSides: effectiveFacade === 'cutaway' ? cutSides : [], roofOpen: effectiveFacade === 'cutaway', floors: floors.length, activeIndex }), [effectiveFacade, cutSides, floors.length, activeIndex]);
  // A turn of 90 degrees to the next diagonal view, flown there (reduced motion jumps); free turns (keys, drag) keep the camera as it is.
  const turn = (dir: 1 | -1) => {
    const c = cameraRef.current;
    if (isInsideView(c) || view === 'top' || latest.current.overview) { change((x) => rotateBy(x, dir * 20)); return; }
    const yaw = snapYaw(c.yaw, dir);
    const fit = buildingFitCamera(fitBox, aspect, CUT_PITCH, yaw);
    turning.current = true;
    setHomeYaw(((yaw % 360) + 360) % 360);
    if (latest.current.reducedMotion) setCamera({ ...fit, yaw: ((yaw % 360) + 360) % 360 }); else glide(fit);
  };
  // The camera that frames one room of the open floor, or null when the room is not on it.
  const frameOf = useCallback((id: string): CameraState | null => {
    const room = roomById.get(id);
    if (!room) return null;
    const c = cameraRef.current;
    return { ...c, target: { x: room.x + room.width / 2, y: activeIndex * FLOOR_PITCH, z: room.z + room.depth / 2 }, distance: Math.max(c.minDistance, Math.min(c.maxDistance, Math.max(room.width, room.depth) * 2.8 + 6)) };
  }, [roomById, activeIndex]);
  const focusRoom = useCallback((room: RoomGeometry) => {
    const next = frameOf(room.id);
    if (!next) return;
    if (latest.current.nav && !latest.current.reducedMotion) latest.current.nav.bridge.flyTo(next);
    else setCamera(next);
  }, [frameOf]);
  // Inside: a low camera in the selected room. It follows the selection, and leaves with the room, the floor or the view.
  const selectedRoom = props.selectedRoomId ? roomById.get(props.selectedRoomId) ?? null : null;
  const insideOf = useCallback((room: RoomGeometry) => insideCamera(room, activeIndex * FLOOR_PITCH + 0.06), [activeIndex]);
  const goTo = useCallback((next: CameraState) => {
    if (latest.current.nav && !latest.current.reducedMotion) latest.current.nav.bridge.flyTo(next);
    else setCamera(next);
  }, []);
  const toggleInside = useCallback(() => {
    if (insideRef.current) {
      setInside(false);
      goTo(latest.current.view === 'top' ? topView(homeRef.current) : homeRef.current);
    } else if (selectedRoom) {
      if (latest.current.overview) latest.current.onOverview(false);
      setInsideRoomId(selectedRoom.id);
      goTo(insideOf(selectedRoom));
    }
  }, [goTo, insideOf, selectedRoom, setInside]);
  const toggleOverview = useCallback(() => {
    setInside(false);
    latest.current.onOverview(!latest.current.overview);
  }, [setInside]);
  useEffect(() => {
    const current = insideRef.current;
    if (!current) return;
    if (selectedRoom) { setInsideRoomId(selectedRoom.id); goTo(insideOf(selectedRoom)); return; }
    // The selection went away: stay in the room, unless the floor changed and the room is no longer on it.
    if (!roomById.has(current)) { setInsideRoomId(null); setCamera(homeNowRef.current); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRoom?.id, activeIndex]);
  const homeNow = useRef(home);
  homeNow.current = view === 'top' ? topView(home) : home;
  const homeNowRef = homeNow;
  const frameRef = useRef(frameOf);
  frameRef.current = frameOf;
  useEffect(() => props.nav?.bridge.attach({
    get: () => cameraRef.current, set: (c) => setCamera(clampTarget(c, 400)), obstacles: () => [], frame: (id) => frameRef.current(id), home: () => homeNow.current,
  }), [props.nav]);

  const pickRoom = useCallback((id: string | null) => {
    const room = id ? roomById.get(id) ?? null : null;
    latest.current.onSelectRoom(room);
    // In the floor overview a pick only selects: the whole floor stays in view.
    if (room && !latest.current.overview) focusRoom(room);
  }, [roomById, focusRoom]);

  const targets: PickTarget[] = useMemo(() => scene.rooms.filter((r) => r.floorIndex === activeIndex).map((r) => ({ id: r.id, box: r.box })), [scene, activeIndex]);
  // The floor the person was on before this one, so the floor they just left can ease away instead of vanishing.
  const lastActive = useRef(activeIndex);
  useEffect(() => { lastActive.current = activeIndex; }, [activeIndex]);
  const lifts = useMemo(
    () => ({ ...floorLifts(scene.ghosts.map((g) => g.floorIndex), activeIndex, stackOn, lastActive.current !== activeIndex ? lastActive.current : null), ...cutLifts(cut) }),
    [scene, activeIndex, stackOn, cut],
  );
  const renderScene = useMemo(() => ({
    opaque: [
      { id: 'building', vertices: scene.opaque.vertices, bounds: scene.opaque.bounds, cull: false },
      ...scene.walls.map((w) => ({ id: `cut-o:${w.side}:${w.floorIndex}`, vertices: w.opaque.vertices, bounds: w.opaque.bounds, cull: false, lift: { group: `cut:${w.side}`, y: w.y } })),
      ...scene.ghosts.map((g) => ({ id: `floor-o:${g.floorIndex}`, vertices: g.opaque.vertices, bounds: g.opaque.bounds, cull: false, lift: { group: `floor:${g.floorIndex}`, y: g.y } })),
      // The procedural sofas and people stay in the scene and are hidden only while a loaded model replaces them.
      ...(['sofa', 'figure'] as const).filter((g) => scene.fallbacks[g].vertices.length).map((g) => ({ id: `fallback-${g}`, vertices: scene.fallbacks[g].vertices, bounds: scene.fallbacks[g].bounds, cull: false, fallbackOf: g })),
    ],
    glass: [
      ...(scene.glass.vertices.length ? [{ id: 'glass', vertices: scene.glass.vertices, bounds: scene.glass.bounds, cull: false }] : []),
      ...scene.walls.filter((w) => w.glass.vertices.length).map((w) => ({ id: `cut-g:${w.side}:${w.floorIndex}`, vertices: w.glass.vertices, bounds: w.glass.bounds, cull: false, lift: { group: `cut:${w.side}`, y: w.y } })),
      ...scene.ghosts.filter((g) => g.glass.vertices.length).map((g) => ({ id: `floor-g:${g.floorIndex}`, vertices: g.glass.vertices, bounds: g.glass.bounds, cull: false, lift: { group: `floor:${g.floorIndex}`, y: g.y } })),
    ],
    glassAlpha: 0.2,
    // The entrance sign on the front lawn is lettered with the real building name.
    // Cell 0 is the entrance sign; the cells after it are the real room names and the floor sign (`scene.signs`).
    signs: [{ title: 'ANUM MINE', subtitle: props.workspaceName }, ...scene.signs].map((s) => ({ ...s, fontsReady })),
    ambient: scene.ambient.map((a) => ({ id: a.id, vertices: a.vertices, bounds: a.bounds, cull: false, motion: a.motion })),
    shadow: scene.shadow,
    models: scene.models,
    halos: scene.halos,
    lifts,
  }), [scene, props.workspaceName, lifts, fontsReady]);

  const flowAnchor = useCallback((id: string) => scene.rooms.find((r) => r.id === id)?.anchor, [scene]);
  const flowLabels = useFlowLabels({
    rooms: floor.rooms, anchorOf: flowAnchor, snapshot: props.flow?.snapshot, batch: props.flow?.batch,
    selectedRoomId: props.selectedRoomId, reducedMotion: props.reducedMotion, onPickRoom: (id) => { pickRoom(id); props.flow?.onPickRoom(id); },
  });

  const wantsStack = stackOn && floors.length > 1;
  // The stacked view's helpers load the first time it is asked for; until then the labels are the single floor's.
  const [stackLib, setStackLib] = useState<typeof import('./lib/hqLabels.ts') | null>(null);
  useEffect(() => { if (wantsStack && !stackLib) void import('./lib/hqLabels.ts').then(setStackLib); }, [wantsStack, stackLib]);
  const stacked = wantsStack && stackLib !== null;
  const labels: StageLabel[] = useMemo(() => {
    const list: StageLabel[] = [];
    const roomHandle = new Map(scene.rooms.map((r) => [r.id, r]));
    const roomsOf = (fi: number) => floors[fi]?.rooms ?? [];
    const countsOf = (fi: number, roomId: string) => {
      const desks = floors[fi].desks.filter((d) => d.roomId === roomId);
      // Observed activity only: a desk counts as working when a presence record says so (nothing is counted without presence).
      return { desks: desks.length, assigned: desks.filter((d) => d.agentId || presenceForDesk(presence, d)).length, working: presence ? desks.filter((d) => presenceForDesk(presence, d)?.status === 'working' && (d.shiftInfo?.onShift ?? true)).length : null };
    };
    if (overview) {
      for (const room of floor.rooms) {
        const handle = roomHandle.get(room.id);
        if (!handle) continue;
        const c = countsOf(activeIndex, room.id);
        list.push({
          id: 'room:' + room.id, anchor: handle.anchor, priority: room.id === props.selectedRoomId ? 0 : 1, maxDistance: 160,
          node: <OverviewCard room={room} desks={c.desks} assigned={c.assigned} selected={room.id === props.selectedRoomId} onPick={() => pickRoom(room.id)} />,
        });
      }
      return [...list, ...flowLabels];
    }
    // Room cards: one per room of the open floor, hung above the room at that floor's height; the other floors of the stack carry only their floor tag (docs/ANUM_MINE_HQ_LABELS.md).
    for (const spec of roomLabelSpecs({ floors, activeIndex, mode: 'single', selectedRoomId: props.selectedRoomId, wallHeight: WALL_HEIGHT })) {
      const room = roomsOf(spec.floor).find((r) => r.id === spec.roomId);
      if (!room) continue;
      const c = countsOf(spec.floor, room.id);
      const onFloor = spec.floor === activeIndex;
      list.push({
        id: spec.id, anchor: spec.anchor, priority: spec.priority, maxDistance: spec.maxDistance, tiers: spec.tiers, occluders: spec.occluders,
        node: <RoomLabelCard name={roomName(room)} kind={room.kind} sub={roomSubtitle(room, c.desks)} working={c.working} live={props.liveMeetingRoomIds.has(room.id)} selected={room.id === props.selectedRoomId}
          onPick={() => (onFloor ? pickRoom(room.id) : latest.current.onSelectFloor(floors[spec.floor].id))} />,
      });
    }
    if (stacked && stackLib) {
      for (const tag of stackLib.floorTagSpecs({ floors, activeIndex })) {
        const f = floors[tag.floor];
        list.push({ id: tag.id, anchor: tag.anchor, alternatives: tag.alternatives, priority: tag.priority, maxDistance: tag.maxDistance, tiers: tag.tiers,
          node: <FloorTagCard name={sceneName(f)} rooms={f.rooms.length} active={tag.floor === activeIndex} onPick={() => latest.current.onSelectFloor(f.id)} /> });
      }
    }
    // Agent cards: only for a desk with a real agent record (registry assignment or presence), on the active floor.
    // The open floor's agents are never hidden by the floors above: it is the floor the person works on, and the cutaway keeps those floors drawn.
    for (const dh of scene.desks) {
      if (dh.floorIndex !== activeIndex) continue;
      const desk = floor.desks.find((d) => d.id === dh.id);
      if (!desk) continue;
      const p = presenceForDesk(presence, desk);
      const key = desk.agentId ?? (p ? p.agentId : null);
      if (!key) continue;
      const hidden = p?.reason === 'agent_not_visible' || key.startsWith('desk:');
      list.push({
        id: 'desk:' + dh.id, anchor: dh.anchor, priority: key === props.selectedAgentId ? 0 : 2, maxDistance: 140, tiers: ['full', 'compact', 'dot'],
        node: <AgentLabelCard offShift={deskShiftLine(desk.shiftInfo, p ? WORK_STATUSES.has(p.status) : false)} chip={hidden ? null : { id: key, avatar: props.agentStyles?.get(key)?.avatar ?? null, department: props.agentStyles?.get(key)?.department ?? p?.role ?? null }} name={hidden ? t('hq.agent.private') : (p?.name || desk.agent?.name || t('hq.agent.assigned'))} role={p?.role ?? null} status={p?.status ?? null} stale={p?.stale === true} selected={key === props.selectedAgentId} onPick={() => latest.current.onSelectAgent(key, desk)} />,
      });
    }
    const wallRoomId = props.selectedRoomId ?? insideRoomId;
    if (props.wall && wallRoomId) {
      const room = roomById.get(wallRoomId);
      const handle = roomHandle.get(wallRoomId);
      if (room && handle && props.wall.isOperations(room)) {
        // The board hangs where the room's video wall is: on the back wall, so the Inside view looks straight at it.
        list.push({ id: 'wall:' + room.id, anchor: { x: (handle.box.min.x + handle.box.max.x) / 2, y: handle.box.min.y + 1.7, z: handle.box.min.z + 0.5 }, priority: 0, maxDistance: 220, node: <Suspense fallback={null}><HqCommandWall model={props.wall.model} variant="scene" onReload={props.wall.onReload} /></Suspense> });
      }
    }
    return [...list, ...flowLabels];
  }, [roomById, insideRoomId, props.wall, scene, floor, floors, presence, props.selectedRoomId, props.selectedAgentId, props.liveMeetingRoomIds, props.agentStyles, activeIndex, pickRoom, flowLabels, overview, stacked, stackLib]);

  const agents = useMemo(() => (presence ? totalsOf([...presence.byAgent.values()]) : null), [presence]);
  const insideRoom = insideRoomId ? roomById.get(insideRoomId) ?? null : null;
  const keep = (c: CameraState): CameraState => (insideRoom && isInsideView(c) ? clampInside(c, insideRoom) : clampTarget(c, 400));
  const change = (fn: (c: CameraState) => CameraState) => { stopGlide(); setCamera((c) => keep(fn(c))); };
  const stepFloor = (d: 1 | -1) => { const f = floors[activeIndex + d]; if (f) props.onSelectFloor(f.id); };
  const roomsTotal = floors.reduce((n, f) => n + f.rooms.length, 0);
  const modules = useMemo(() => buildingModules(building), [building]);

  return (
    <div className="hqBuilding" data-inside={inside} data-overview={overview} data-fullscreen={props.fullscreen} onKeyDown={(e) => {
      if (e.key === 'Escape') {
        if (insideRef.current) { e.stopPropagation(); toggleInside(); } else if (latest.current.overview) { e.stopPropagation(); latest.current.onOverview(false); }
        return;
      }
      // Page Up and Page Down move between floors (never while typing in a field or holding a modifier).
      if ((e.key === 'PageUp' || e.key === 'PageDown') && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        const tag = (e.target as HTMLElement).tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        e.preventDefault();
        stepFloor(e.key === 'PageUp' ? 1 : -1);
      }
    }}>
      <div className="hqBuildingBar">
        {props.buildings.length > 1 && (
          <label className="hqSelect">
            <span className="hqSrOnly">{t('hq.building.pick')}</span>
            <select value={building.id} onChange={(e) => props.onSelectBuilding(e.target.value)}>
              {props.buildings.map((b) => <option key={b.id} value={b.id}>{sceneName(b, locale)}</option>)}
            </select>
            <ChevronDown aria-hidden="true" />
          </label>
        )}
        <div className="hqFloorControl" role="group" aria-label={t('hq.floor.label')}>
          <button type="button" onClick={() => stepFloor(-1)} disabled={activeIndex <= 0} aria-label={t('hq.floor.down')}><ArrowLeft aria-hidden="true" className="flip" /></button>
          <label className="hqSelect compactSelect">
            <span className="hqSrOnly">{t('hq.floor.pick')}</span>
            <Layers aria-hidden="true" />
            <select value={floor.id} onChange={(e) => props.onSelectFloor(e.target.value)}>
              {floors.map((f) => <option key={f.id} value={f.id}>{sceneName(f, locale)}</option>)}
            </select>
          </label>
          <button type="button" onClick={() => stepFloor(1)} disabled={activeIndex >= floors.length - 1} aria-label={t('hq.floor.up')}><ArrowLeft aria-hidden="true" className="flipOpposite" /></button>
          <span className="hqFloorCount" data-testid="hq-floor-count">{t('hq.floor.count', { index: activeIndex + 1, count: floors.length })}</span>
          <div className="hqSegment" role="group" aria-label={t('hq.facade.label')}>
            {(['cutaway', 'closed'] as const).map((f) => (
              <button key={f} type="button" aria-pressed={facade === f} data-testid={`hq-facade-${f}`} onClick={() => { setFacade(f); setFacadeSaid(true); }}>{t(`hq.facade.${f}` as MessageKey)}</button>
            ))}
          </div>
          <label className="hqCheck"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /><span>{t('hq.floor.showAll')}</span></label>
        </div>
      </div>

      <p className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-facade-live">{facadeSaid ? t(`hq.facade.${facade}Now` as MessageKey) : ''}</p>
      <p className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-floor-live">{t('hq.floor.now', { name: floorTitle, index: activeIndex + 1, count: floors.length })}</p>
      <div className="hqSceneWrap" ref={wrap} data-facade={cut.facade} data-cut-sides={cut.cutSides.join(' ')} data-roof-open={cut.roofOpen} data-stack={stackOn} data-camera-yaw={camera.yaw.toFixed(1)} data-camera-distance={camera.distance.toFixed(2)} data-camera-eye-y={(camera.target.y + Math.sin((camera.pitch * Math.PI) / 180) * camera.distance).toFixed(2)}>
        <HqStage
          label={t('hq.building.stageLabel', { name: props.workspaceName, floor: floorTitle })} description={t('hq.building.stageHelp')}
          scene={renderScene} pickTargets={targets} camera={camera} homeCamera={view === 'top' ? topView(home) : home}
          onCamera={(c) => { stopGlide(); setCamera(keep(c)); }} night={night} reducedMotion={props.reducedMotion}
          hoverId={null} selectedId={null} onHover={() => undefined}
          onPick={pickRoom} labels={labels} panMode={panMode} labelKeepOut="[data-keepout]" onUnavailable={props.onUnavailable} onCanvasFrame={props.nav?.capture.onFrame}
        />
        {floor.rooms.length === 0 && (
          <div className="hqEmptyFloor" role="status">
            <DoorOpen aria-hidden="true" />
            <strong>{t('hq.floor.empty')}</strong>
            <span>{t('hq.floor.emptyHint')}</span>
            <button type="button" className="hqPrimary" onClick={props.onManageSpaces}>{t('hq.manageSpaces')}</button>
          </div>
        )}
        <div className="hqOverlay hqOverlayTopLeft" data-keepout="">
          <Suspense fallback={<h2 className="hqSrOnly">{props.workspaceName}</h2>}>
            <SceneBackCard
              name={props.workspaceName} kindLabel={props.kindLabel} totals={agents} floors={floors.length} rooms={roomsTotal} meetings={props.liveMeetings} onBack={props.onBack}
              orders={modules.panels.includes('commerce') && props.commerce.state === 'ready' ? props.commerce.data.orders : null}
            />
          </Suspense>
        </div>
        <div className="hqOverlay hqOverlayFlow" data-keepout="">
          <Suspense fallback={null}><FlowStrip snapshot={props.flow?.snapshot} /></Suspense>
        </div>
        <div className="hqOverlay hqOverlayRight" data-keepout="">
          <CameraTools
            yaw={camera.yaw} night={night} onNight={props.onNight}
            onZoomIn={() => change((c) => zoomBy(c, 0.8))} onZoomOut={() => change((c) => zoomBy(c, 1.25))}
            onReset={() => { setInside(false); setHomeYaw(HOME_YAW); setCamera(view === 'top' ? topView(home) : buildingFitCamera(fitBox, aspect, CUT_PITCH, HOME_YAW)); }} onNorth={() => change(faceNorth)}
          />
        </div>
        {props.side && <div className="hqOverlay hqSideOverlay" data-keepout="">{props.side}</div>}
        <div className="hqOverlay hqOverlayBottom hqDock" data-keepout="">
          <div className="hqDockOps">
            <Suspense fallback={null}><LiveOpsStrip totals={agents} unreported={props.unreported} snapshot={props.flow?.snapshot} /></Suspense>
          </div>
          <div className="hqDockTools">
          <BottomToolbar
            inside={{ active: inside, disabled: !selectedRoom || view === 'top', onToggle: toggleInside }}
            overview={{ active: overview, onToggle: toggleOverview }}
            onRotateLeft={() => turn(-1)} onRotateRight={() => turn(1)}
            onPrev={() => stepFloor(-1)} onNext={() => stepFloor(1)} prevDisabled={activeIndex <= 0} nextDisabled={activeIndex >= floors.length - 1} prevLabel={t('hq.floor.down')} nextLabel={t('hq.floor.up')}
            onZoomIn={() => change((c) => dolly(c, -250))} onZoomOut={() => change((c) => dolly(c, 250))}
            pan={{ active: panMode, onToggle: () => setPanMode((v) => !v) }}
            fullscreen={props.fullscreen} onFullscreen={props.onToggleFullscreen}
          />
          </div>
          <div className="hqDockFloors">
            <Suspense fallback={null}><FloorSelector floors={floors.map((f) => ({ id: f.id, name: sceneName(f) }))} activeId={floor.id} stacked={showAll && floors.length > 1} onPick={props.onSelectFloor} onStack={setShowAll} /></Suspense>
          </div>
        </div>
      </div>

      <div className="hqStripSlot" />

      {overview && floor.rooms.length > 0 && <Suspense fallback={null}><FloorRooms floor={floor} has={(d) => !!presenceForDesk(presence, d)} selectedRoomId={props.selectedRoomId} onPick={pickRoom} onLeave={() => props.onOverview(false)} /></Suspense>}

      <div className="hqBottomPanels">
        <TotalsPanel
          title={t('hq.team.title')} totals={agents} unreported={props.unreported} compact verifiedAt={props.verifiedAt}
          note={presence && agents && agents.agents === 0 ? t('hq.team.none') : null}
        />
        {modules.panels.includes('os') && props.wall && <Suspense fallback={null}><HqCommandWall model={props.wall.model} variant="panel" onReload={props.wall.onReload} /></Suspense>}
        {modules.panels.includes('commerce') && <Suspense fallback={null}><CommercePanels state={props.commerce} /></Suspense>}
        {modules.panels.includes('tyre') && <Suspense fallback={null}><TyrePanel state={props.tyre} /></Suspense>}
      </div>
    </div>
  );
}

/** The room card of the floor overview: the name, the kind, the desks and the people at them; a button that selects the room. */
function OverviewCard(props: { room: RoomGeometry; desks: number; assigned: number; selected: boolean; onPick: () => void }) {
  return (
    <button type="button" className="hqLabelCard hqOverviewCard" data-selected={props.selected} data-kind={props.room.kind} aria-pressed={props.selected} onClick={props.onPick}>
      <span className="hqLabelIcon" data-kind={props.room.kind} aria-hidden="true" />
      <span className="hqLabelText"><strong>{roomName(props.room)}</strong><small>{t(`hq.roomKind.${props.room.kind}` as MessageKey)}</small><small>{t('hqFloor.stats', { desks: props.desks, agents: props.assigned })}</small></span>
    </button>
  );
}
