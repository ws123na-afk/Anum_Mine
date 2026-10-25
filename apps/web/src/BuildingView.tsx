/**
 * Building cutaway: floors, rooms as zones, desks and agent cards from the saved office layout.
 * Presence comes in as props (workstation evidence plus live meeting participants); this view
 * never invents a status. Module panels (commerce, tyre) show only for connected modules.
 */
import type { AvatarAppearance } from '@anum/contracts';
import type { OfficeFlowSnapshot } from '@anum/contracts';
import type { ChangeBatch } from './useOfficeFlow';
import { useFlowLabels } from './HqFlowStage';
import { ArrowLeft, Building2, ChevronDown, ChevronRight, DoorOpen, HardDrive, Layers, Users } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { BottomToolbar, CameraTools, TotalsPanel, statusLabel } from './HqChrome';
import { AvatarChip } from './AvatarStyleEditor';
import { HqStage, useAspect, type StageLabel } from './HqStage';
import { buildBuildingScene, frameBuilding, type DeskVisual } from './lib/buildingScene.ts';
import { buildingCamera, clampTarget, faceNorth, rotateBy, threeDView, topView, zoomBy } from './lib/hqCamera.ts';
import { dolly, type CameraState } from './lib/headquartersSpatial.ts';
import type { RoomGeometry, SceneBuilding, SceneDesk, SceneFloor } from './lib/headquartersProjection.ts';
import { presenceForDesk, totalsOf, type HqPresenceIndex } from './lib/hqPresence.ts';
import { STATUS_TONE, type StatusKey } from './lib/campusModel.ts';
import type { HqView } from './HqChrome';
import type { CommerceLive, ModuleState, TyreLive } from './lib/hqModules.ts';
import { commerceFlow } from './lib/hqModules.ts';
import type { PickTarget } from './lib/hqMath.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import type { HqNavHandles } from './hq-nav/HqNavigation';

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
  /** The navigation layer's camera bridge and thumbnail capture (flights, snapshots). */
  nav?: HqNavHandles;
  /** The open room or workstation panel, drawn over the scene (stacked under it on narrow screens). */
  side: ReactNode;
  /** Stored work per room and the last move the server reported, for the live-flow badges and markers. */
  flow?: Readonly<{ snapshot: OfficeFlowSnapshot | null; batch: ChangeBatch; onPickRoom: (roomId: string) => void }>;
  /** Avatar style and department per agent id, from the agent profiles; an agent with no entry is drawn with the default style. */
  agentStyles?: ReadonlyMap<string, AgentStyleInfo>;
}>;

export type AgentStyleInfo = Readonly<{ avatar: AvatarAppearance | null; department: string | null }>;

const ROOM_ICON = { meeting: Users, reception: DoorOpen, warehouse: HardDrive } as const;

const DESK_VISUAL: Record<StatusKey, DeskVisual> = {
  working: 'working', queued: 'idle', meeting: 'idle', waiting_approval: 'waiting', blocked: 'waiting', idle: 'idle', paused: 'unknown', unknown: 'unknown',
};

export function BuildingView(props: BuildingViewProps) {
  const { building, floor, view, night, presence } = props;
  const floors = building.floors;
  const activeIndex = Math.max(0, floors.findIndex((f) => f.id === floor.id));
  const [showAll, setShowAll] = useState(false);

  const deskState = useMemo(() => {
    const map = new Map<string, DeskVisual>();
    for (const f of floors) {
      for (const d of f.desks) {
        const p = presenceForDesk(presence, d);
        if (!d.agentId && !p) { map.set(d.id, 'vacant'); continue; }
        map.set(d.id, p ? (p.stale ? 'unknown' : DESK_VISUAL[p.status]) : 'assigned');
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
  const scene = useMemo(
    () => buildBuildingScene(floors, { activeIndex, showAll, selectedRoomId: props.selectedRoomId, deskState, meetingRoomIds: props.liveMeetingRoomIds, people, looks }),
    [floors, activeIndex, showAll, props.selectedRoomId, deskState, props.liveMeetingRoomIds, people, looks],
  );
  const frame = useMemo(() => frameBuilding(scene, activeIndex), [scene, activeIndex]);
  const [wrap, aspect] = useAspect();
  const home = useMemo(() => buildingCamera(frame.target, frame.distance, aspect), [frame.target.x, frame.target.z, frame.distance, activeIndex, aspect]); // eslint-disable-line react-hooks/exhaustive-deps
  const [camera, setCamera] = useState<CameraState>(home);
  const homeRef = useRef(home);
  useEffect(() => {
    if (homeRef.current === home) return;
    homeRef.current = home;
    setCamera(view === 'top' ? topView(home) : home);
  }, [home, view]);
  useEffect(() => { setCamera((c) => (view === 'top' ? topView(c) : threeDView(c))); }, [view]);

  const latest = useRef(props);
  latest.current = props;
  const roomById = useMemo(() => new Map(floor.rooms.map((r) => [r.id, r])), [floor.rooms]);

  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  // The camera that frames one room of the open floor, or null when the room is not on it.
  const frameOf = useCallback((id: string): CameraState | null => {
    const room = roomById.get(id);
    if (!room) return null;
    const c = cameraRef.current;
    return { ...c, target: { x: room.x + room.width / 2, y: activeIndex * 4.2, z: room.z + room.depth / 2 }, distance: Math.max(c.minDistance, Math.min(c.maxDistance, Math.max(room.width, room.depth) * 2.8 + 6)) };
  }, [roomById, activeIndex]);
  const focusRoom = useCallback((room: RoomGeometry) => {
    const next = frameOf(room.id);
    if (!next) return;
    if (latest.current.nav && !latest.current.reducedMotion) latest.current.nav.bridge.flyTo(next);
    else setCamera(next);
  }, [frameOf]);
  const homeNow = useRef(home);
  homeNow.current = view === 'top' ? topView(home) : home;
  const frameRef = useRef(frameOf);
  frameRef.current = frameOf;
  useEffect(() => props.nav?.bridge.attach({
    get: () => cameraRef.current, set: (c) => setCamera(clampTarget(c, 400)), obstacles: () => [], frame: (id) => frameRef.current(id), home: () => homeNow.current,
  }), [props.nav]);

  const pickRoom = useCallback((id: string | null) => {
    const room = id ? roomById.get(id) ?? null : null;
    latest.current.onSelectRoom(room);
    if (room) focusRoom(room);
  }, [roomById, focusRoom]);

  const targets: PickTarget[] = useMemo(() => scene.rooms.filter((r) => r.floorIndex === activeIndex).map((r) => ({ id: r.id, box: r.box })), [scene, activeIndex]);
  const renderScene = useMemo(() => ({
    opaque: [
      { id: 'building', vertices: scene.opaque.vertices, bounds: scene.opaque.bounds, cull: false },
      // The procedural sofas and people stay in the scene and are hidden only while a loaded model replaces them.
      ...(['sofa', 'figure'] as const).filter((g) => scene.fallbacks[g].vertices.length).map((g) => ({ id: `fallback-${g}`, vertices: scene.fallbacks[g].vertices, bounds: scene.fallbacks[g].bounds, cull: false, fallbackOf: g })),
    ],
    glass: scene.glass.vertices.length ? [{ id: 'glass', vertices: scene.glass.vertices, bounds: scene.glass.bounds, cull: false }] : [],
    glassAlpha: 0.2,
    // The entrance sign on the front lawn is lettered with the real building name.
    signs: [{ title: 'ANUM MINE', subtitle: props.workspaceName }],
    shadow: scene.shadow,
    models: scene.models,
    halos: scene.halos,
  }), [scene, props.workspaceName]);

  const flowAnchor = useCallback((id: string) => scene.rooms.find((r) => r.id === id)?.anchor, [scene]);
  const flowLabels = useFlowLabels({
    rooms: floor.rooms, anchorOf: flowAnchor, snapshot: props.flow?.snapshot, batch: props.flow?.batch,
    selectedRoomId: props.selectedRoomId, reducedMotion: props.reducedMotion, onPickRoom: (id) => { pickRoom(id); props.flow?.onPickRoom(id); },
  });

  const labels: StageLabel[] = useMemo(() => {
    const list: StageLabel[] = [];
    const roomHandle = new Map(scene.rooms.map((r) => [r.id, r]));
    for (const room of floor.rooms) {
      const handle = roomHandle.get(room.id);
      if (!handle) continue;
      const desks = floor.desks.filter((d) => d.roomId === room.id);
      const assigned = desks.filter((d) => d.agentId || presenceForDesk(presence, d)).length;
      const working = desks.filter((d) => presenceForDesk(presence, d)?.status === 'working').length;
      list.push({
        id: 'room:' + room.id, anchor: handle.anchor, priority: room.id === props.selectedRoomId ? 0 : 1, maxDistance: 160,
        node: <RoomCard room={room} assigned={assigned} working={working} known={presence !== null} live={props.liveMeetingRoomIds.has(room.id)} selected={room.id === props.selectedRoomId} onPick={() => pickRoom(room.id)} />,
      });
    }
    for (const dh of scene.desks) {
      if (dh.floorIndex !== activeIndex) continue;
      const desk = floor.desks.find((d) => d.id === dh.id);
      if (!desk) continue;
      const p = presenceForDesk(presence, desk);
      const key = desk.agentId ?? (p ? p.agentId : null);
      if (!key) continue;
      const hidden = p?.reason === 'agent_not_visible' || key.startsWith('desk:');
      list.push({
        id: 'desk:' + dh.id, anchor: dh.anchor, priority: key === props.selectedAgentId ? 0 : 2, maxDistance: 70,
        node: <AgentCard chip={hidden ? null : { id: key, avatar: props.agentStyles?.get(key)?.avatar ?? null, department: props.agentStyles?.get(key)?.department ?? p?.role ?? null }} name={hidden ? t('hq.agent.private') : (p?.name || desk.agent?.name || t('hq.agent.assigned'))} role={p?.role ?? null} status={p?.status ?? null} stale={p?.stale === true} selected={key === props.selectedAgentId} onPick={() => latest.current.onSelectAgent(key, desk)} />,
      });
    }
    return [...list, ...flowLabels];
  }, [scene, floor, presence, props.selectedRoomId, props.selectedAgentId, props.liveMeetingRoomIds, props.agentStyles, activeIndex, pickRoom, flowLabels]);

  const agents = useMemo(() => (presence ? totalsOf([...presence.byAgent.values()]) : null), [presence]);
  const change = (fn: (c: CameraState) => CameraState) => setCamera((c) => clampTarget(fn(c), 400));
  const stepFloor = (d: 1 | -1) => { const f = floors[activeIndex + d]; if (f) props.onSelectFloor(f.id); };
  const roomsTotal = floors.reduce((n, f) => n + f.rooms.length, 0);

  return (
    <div className="hqBuilding">
      <div className="hqBuildingBar">
        <nav className="hqCrumbs" aria-label={t('hq.crumb.label')}>
          <ol>
            <li><button type="button" className="hqBack" onClick={props.onBack}><ArrowLeft aria-hidden="true" className="flip" />{t('hq.back')}</button></li>
            <li aria-hidden="true"><ChevronRight className="flip" /></li>
            <li className="hqBuildingTitle" aria-current="page">
              <Building2 aria-hidden="true" />
              <div>
                <h2>{props.workspaceName}</h2>
                <small>{props.kindLabel}</small>
              </div>
            </li>
          </ol>
        </nav>
        <ul className="hqCounters" aria-label={t('hq.counters.label')}>
          <li><strong>{agents ? agents.agents : '-'}</strong><span>{t('hq.counters.people')}</span></li>
          <li><strong>{floors.length}</strong><span>{t('hq.counters.floors')}</span></li>
          <li><strong>{roomsTotal}</strong><span>{t('hq.counters.rooms')}</span></li>
          {props.liveMeetings !== null && <li><strong>{props.liveMeetings}</strong><span>{t('hq.counters.meetings')}</span></li>}
        </ul>
        {props.buildings.length > 1 && (
          <label className="hqSelect">
            <span className="hqSrOnly">{t('hq.building.pick')}</span>
            <select value={building.id} onChange={(e) => props.onSelectBuilding(e.target.value)}>
              {props.buildings.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
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
              {floors.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </label>
          <button type="button" onClick={() => stepFloor(1)} disabled={activeIndex >= floors.length - 1} aria-label={t('hq.floor.up')}><ArrowLeft aria-hidden="true" className="flipOpposite" /></button>
          <label className="hqCheck"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /><span>{t('hq.floor.showAll')}</span></label>
        </div>
      </div>

      <div className="hqSceneWrap" ref={wrap}>
        <HqStage
          label={t('hq.building.stageLabel', { name: props.workspaceName, floor: floor.name })} description={t('hq.building.stageHelp')}
          scene={renderScene} pickTargets={targets} camera={camera} homeCamera={view === 'top' ? topView(home) : home}
          onCamera={(c) => setCamera(clampTarget(c, 400))} night={night} reducedMotion={props.reducedMotion}
          hoverId={null} selectedId={null} onHover={() => undefined}
          onPick={pickRoom} labels={labels} onUnavailable={props.onUnavailable} onCanvasFrame={props.nav?.capture.onFrame}
        />
        {floor.rooms.length === 0 && (
          <div className="hqEmptyFloor" role="status">
            <DoorOpen aria-hidden="true" />
            <strong>{t('hq.floor.empty')}</strong>
            <span>{t('hq.floor.emptyHint')}</span>
            <button type="button" className="hqPrimary" onClick={props.onManageSpaces}>{t('hq.manageSpaces')}</button>
          </div>
        )}
        <div className="hqOverlay hqOverlayRight">
          <CameraTools
            yaw={camera.yaw} night={night} onNight={props.onNight}
            onZoomIn={() => change((c) => zoomBy(c, 0.8))} onZoomOut={() => change((c) => zoomBy(c, 1.25))}
            onReset={() => setCamera(view === 'top' ? topView(home) : home)} onNorth={() => change(faceNorth)}
          />
        </div>
        {props.side && <div className="hqOverlay hqSideOverlay">{props.side}</div>}
        <div className="hqOverlay hqOverlayBottom">
          <BottomToolbar
            onRotateLeft={() => change((c) => rotateBy(c, -20))} onRotateRight={() => change((c) => rotateBy(c, 20))}
            onPrev={() => stepFloor(-1)} onNext={() => stepFloor(1)} prevLabel={t('hq.floor.down')} nextLabel={t('hq.floor.up')}
            onZoomIn={() => change((c) => dolly(c, -250))} onZoomOut={() => change((c) => dolly(c, 250))}
            fullscreen={props.fullscreen} onFullscreen={props.onToggleFullscreen}
          />
        </div>
      </div>

      <div className="hqStripSlot" />

      <div className="hqBottomPanels">
        <TotalsPanel
          title={t('hq.team.title')} totals={agents} unreported={props.unreported} compact verifiedAt={props.verifiedAt}
          note={presence && agents && agents.agents === 0 ? t('hq.team.none') : null}
        />
        <CommercePanels state={props.commerce} />
        <TyrePanel state={props.tyre} />
      </div>
    </div>
  );
}

function RoomCard(props: { room: RoomGeometry; assigned: number; working: number; known: boolean; live: boolean; selected: boolean; onPick: () => void }) {
  const Icon = (ROOM_ICON as Record<string, typeof Users>)[props.room.kind] ?? Building2;
  const sub = props.known
    ? t('hq.room.assignedWorking', { assigned: props.assigned, working: props.working })
    : t('hq.room.assigned', { count: props.assigned });
  return (
    <button type="button" className="hqLabelCard hqRoomCard" data-selected={props.selected} data-live={props.live} data-kind={props.room.kind} aria-pressed={props.selected} onClick={props.onPick}>
      <span className="hqLabelIcon"><Icon aria-hidden="true" /></span>
      <span className="hqLabelText"><strong>{props.room.label}</strong><small>{props.live ? t('hq.room.inMeeting') : sub}</small></span>
    </button>
  );
}

function AgentCard(props: { chip: { id: string; avatar: AvatarAppearance | null; department: string | null } | null; name: string; role: string | null; status: StatusKey | null; stale: boolean; selected: boolean; onPick: () => void }) {
  const tone = props.status ? STATUS_TONE[props.status] : 'faint';
  return (
    <button type="button" className={`hqAgentCard tone-${tone}`} data-selected={props.selected} data-stale={props.stale} aria-pressed={props.selected} onClick={props.onPick}>
      {props.chip
        ? <AvatarChip id={props.chip.id} name={props.name} avatar={props.chip.avatar} department={props.chip.department} size={30} decorative />
        : <span className="hqAvatar" aria-hidden="true">{props.name.slice(0, 1).toUpperCase()}</span>}
      <span className="hqLabelText">
        <strong>{props.name}</strong>
        {props.role && <small>{props.role}</small>}
        <span className={`hqPill tone-${tone}`}>{props.status ? statusLabel(props.status) : t('hq.status.unavailable')}{props.stale ? ` (${t('hq.agent.stale')})` : ''}</span>
      </span>
    </button>
  );
}

function NotConnected(props: { title: string; state: ModuleState<unknown>; setupHash: string }) {
  const s = props.state;
  return (
    <section className="hqPanel hqModule" aria-label={props.title}>
      <h2>{props.title}</h2>
      {s.state === 'loading' && <p className="hqMuted" role="status">{t('hq.module.loading')}</p>}
      {s.state === 'not_connected' && <div className="hqUnavailable"><strong>{t('hq.module.notConnected')}</strong><a href={props.setupHash}>{t('hq.module.setup')}</a></div>}
      {s.state === 'forbidden' && <div className="hqUnavailable"><strong>{t('hq.module.forbidden')}</strong><span>{t('hq.module.forbiddenHint')}</span></div>}
      {s.state === 'error' && <div className="hqUnavailable" role="alert"><strong>{t('hq.module.error')}</strong><span>{s.message}</span></div>}
    </section>
  );
}

function CommercePanels({ state }: { state: ModuleState<CommerceLive> }) {
  if (state.state !== 'ready') return <NotConnected title={t('hq.commerce.title')} state={state} setupHash="#commerce" />;
  const c = state.data;
  return (
    <>
      <section className="hqPanel hqModule" aria-label={t('hq.commerce.title')}>
        <h2>{t('hq.commerce.title')}</h2>
        <ul className="hqStats">
          <li><strong>{c.orders}</strong><span>{t('hq.commerce.orders')}</span></li>
          <li><strong>{c.waiting}</strong><span>{t('hq.commerce.waiting')}</span></li>
          <li><strong>{c.late}</strong><span>{t('hq.commerce.late')}</span></li>
          <li><strong>{c.openAlerts}</strong><span>{t('hq.commerce.alerts')}</span></li>
        </ul>
      </section>
      <section className="hqPanel hqModule" aria-label={t('hq.flow.title')}>
        <h2>{t('hq.flow.title')}</h2>
        <ol className="hqFlow">
          {commerceFlow(c).map((step) => (
            <li key={step.key}><strong>{step.count}</strong><span>{t(`hq.flow.${step.key}` as MessageKey)}</span></li>
          ))}
        </ol>
      </section>
    </>
  );
}

function TyrePanel({ state }: { state: ModuleState<TyreLive> }) {
  if (state.state !== 'ready') return <NotConnected title={t('hq.tyre.title')} state={state} setupHash="#tyre" />;
  const d = state.data;
  return (
    <section className="hqPanel hqModule" aria-label={t('hq.tyre.title')}>
      <h2>{t('hq.tyre.title')}</h2>
      <ul className="hqStats">
        <li><strong>{d.vehicles}</strong><span>{t('hq.tyre.vehicles')}</span></li>
        <li><strong>{d.belowMinimum}</strong><span>{t('hq.tyre.below')}</span></li>
        <li><strong>{d.overdueInspections}</strong><span>{t('hq.tyre.overdue')}</span></li>
        <li><strong>{d.dueNext30}</strong><span>{t('hq.tyre.due')}</span></li>
      </ul>
    </section>
  );
}
