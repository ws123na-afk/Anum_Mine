/**
 * The navigation layer of the headquarters: a floor rail, a strip of quick views, a side panel (floor plan,
 * people, quick moves, the assistant dock), drawers on small screens and a live region that says where the
 * camera is going. It wraps the existing scene and composes with its panels; it holds no data of its own.
 * Everything shown is derived from the campus, the saved office layout and the presence index (navModel.ts).
 */
import { CalendarDays, Layers, PanelRight, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { HqView } from '../HqChrome';
import { hqCommands } from '../lib/hqExtensions.ts';
import type { Campus } from '../lib/campusModel.ts';
import type { SceneBuilding, SceneDesk, SceneFloor } from '../lib/headquartersProjection.ts';
import type { HqPresenceIndex } from '../lib/hqPresence.ts';
import { t } from '../lib/i18n.ts';
import { HqCameraBridge } from './cameraBridge.ts';
import { FloorRail } from './FloorRail.tsx';
import { currentStopId, peopleAtPosts, roomStops, stripStops, type StripStop } from './navModel.ts';
import { FloorPlan, MOVE_ICONS, PeopleList, QuickMoves, type Move } from './SidePanel.tsx';
import { getThumb, thumbKey, ThumbCapture } from './thumbs.ts';
import { useNavLayout } from './useNavLayout.ts';
import { stopName, ViewStrip } from './ViewStrip.tsx';
import './hqNav.css';

export type HqNavHandles = Readonly<{ bridge: HqCameraBridge; capture: ThumbCapture }>;
export type NavOverlay = 'tour' | 'agentMeetings' | 'teams';

export type HqNavigationProps = Readonly<{
  handles: HqNavHandles;
  level: 'campus' | 'building';
  view: HqView;
  webglOk: boolean;
  night: boolean;
  campus: Campus | null;
  tenantId: string;
  workspaceId: string;
  selectedBuildingId: string | null;
  building: SceneBuilding | null;
  floor: SceneFloor | null;
  selectedRoomId: string | null;
  presence: HqPresenceIndex | null;
  onView: (view: HqView) => void;
  onShowCampus: () => void;
  onSelectBuilding: (workspaceId: string | null) => void;
  onEnter: (workspaceId: string) => void;
  onFloor: (floorId: string) => void;
  onRoom: (floorId: string, roomId: string) => void;
  onAgent: (agentId: string, desk: SceneDesk) => void;
  onOverlay: (overlay: NavOverlay) => void;
  onGo: (hash: string) => void;
  /** The scene (campus, building or list) and the live-flow dock under it. */
  scene: ReactNode;
  flowDock: ReactNode;
  /** The executive assistant dock, when the shell supplies one. */
  dock: ReactNode;
}>;

function hash(parts: readonly string[]): string {
  let h = 5381;
  for (const part of parts) for (let i = 0; i < part.length; i++) h = ((h << 5) + h + part.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

type Drawer = null | 'floors' | 'side';

export function HqNavigation(props: HqNavigationProps) {
  const { handles, level, view, campus, building, floor } = props;
  const { layout, stripOpen, toggleRail, toggleSide, toggleStrip } = useNavLayout();
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [tab, setTab] = useState<'plan' | 'moves' | 'assistant'>('plan');
  const [live, setLive] = useState('');
  const target = useRef<{ stopId: string; name: string } | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const root = useRef<HTMLDivElement>(null);
  // Inside a building the strip sits right under the scene, ahead of the module panels (the building view leaves a slot for it).
  const [slot, setSlot] = useState<Element | null>(null);

  const navOn = view !== 'list' && !!campus;
  const stripOn = navOn && props.webglOk;
  const railOn = navOn && level === 'building' && !!building && layout.rail !== 'drawer';
  const sideInline = navOn && layout.side === 'open';

  useLayoutEffect(() => { setSlot(level === 'building' ? root.current?.querySelector('.hqStripSlot') ?? null : null); });

  // ---- thumbnails ---------------------------------------------------------------------------------------------
  const campusSig = useMemo(() => hash((campus?.buildings ?? []).map((b) => `${b.workspace_id}:${b.kind}:${b.floors ?? 0}`).sort()), [campus]);
  const buildingSig = useMemo(() => hash((building?.floors ?? []).flatMap((f) => f.rooms.map((r) => `${r.id}:${r.revision}:${r.x}:${r.z}:${r.width}:${r.depth}`))), [building]);
  const keyFor = useCallback((place: string) => thumbKey({
    tenantId: props.tenantId, workspaceId: props.workspaceId, signature: place.startsWith('floor:') || place.startsWith('room:') ? buildingSig : campusSig, place, night: props.night,
  }), [props.tenantId, props.workspaceId, buildingSig, campusSig, props.night]);

  // ---- camera events: live region and snapshots ----------------------------------------------------------------
  const keyRef = useRef(keyFor);
  keyRef.current = keyFor;
  useEffect(() => handles.bridge.subscribe((event) => {
    const where = target.current;
    if (event.phase === 'missing') { setLive(t('hqNav.live.notShown')); return; }
    if (!where) return;
    if (event.phase === 'start') setLive(t('hqNav.live.flying', { place: where.name }));
    else {
      setLive(event.phase === 'instant' ? t('hqNav.live.instant', { place: where.name }) : t('hqNav.live.arrived', { place: where.name }));
      const key = keyRef.current(where.stopId);
      window.setTimeout(() => { if (!handles.bridge.isFlying && handles.capture.waiting === false) { handles.capture.request(key); handles.bridge.nudge(); } }, 500);
    }
  }), [handles]);

  // The first calm view of a place (the campus aerial, a floor's overview) is photographed once, if the person has not touched the camera.
  const placeNow = level === 'campus' ? (props.selectedBuildingId ? null : 'campus') : props.selectedRoomId ? null : floor ? `floor:${floor.id}` : null;
  useEffect(() => {
    if (!navOn || !props.webglOk || view !== '3d' || !placeNow) return undefined;
    const key = keyRef.current(placeNow);
    if (getThumb(key)) return undefined;
    let touched = false;
    const stage = document.querySelector('.hqStage');
    const touch = () => { touched = true; };
    stage?.addEventListener('pointerdown', touch, { passive: true });
    stage?.addEventListener('wheel', touch, { passive: true });
    const id = window.setTimeout(() => { if (!touched && !handles.bridge.isFlying) { handles.capture.request(key); handles.bridge.nudge(); } }, 2400);
    return () => { window.clearTimeout(id); stage?.removeEventListener('pointerdown', touch); stage?.removeEventListener('wheel', touch); };
  }, [navOn, props.webglOk, view, placeNow, props.night, handles]);

  // ---- quick views ------------------------------------------------------------------------------------------
  const stops = useMemo(() => stripStops(campus, building, props.workspaceId), [campus, building, props.workspaceId]);
  const currentId = currentStopId({ level, selectedBuildingId: props.selectedBuildingId, selectedRoomId: props.selectedRoomId, currentWorkspaceId: props.workspaceId });
  const stopKey = useCallback((stop: StripStop) => keyFor(stop.id), [keyFor]);

  const goStop = useCallback((stop: StripStop) => {
    handles.bridge.setReducedMotion(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true);
    target.current = { stopId: stop.id, name: stopName(stop) };
    if (stop.kind === 'campus') {
      if (level !== 'campus') props.onShowCampus();
      props.onSelectBuilding(null);
      handles.bridge.flyHome();
    } else if (stop.kind === 'building') {
      if (!stop.accessible) return;
      if (level === 'campus' && currentId === stop.id) { props.onEnter(stop.workspaceId); return; }
      if (stop.workspaceId !== props.workspaceId && level === 'building') { props.onEnter(stop.workspaceId); return; }
      if (level === 'building') props.onShowCampus();
      props.onSelectBuilding(stop.workspaceId);
      handles.bridge.flyToFrame(stop.workspaceId);
    } else {
      props.onRoom(stop.floorId, stop.roomId);
      handles.bridge.flyToFrame(stop.roomId);
    }
  }, [handles, level, currentId, props]);

  const pickFloor = useCallback((id: string) => {
    const f = building?.floors.find((x) => x.id === id);
    target.current = { stopId: `floor:${id}`, name: f?.name ?? '' };
    if (id === floor?.id) handles.bridge.flyHome(); else props.onFloor(id);
    if (f) setLive(t('hqNav.live.floor', { name: f.name }));
    setDrawer(null);
  }, [building, floor?.id, handles, props]);

  const pickRoom = useCallback((roomId: string) => {
    const f = floor?.rooms.find((r) => r.id === roomId);
    if (!f || !floor) return;
    target.current = { stopId: `room:${roomId}`, name: f.label };
    props.onRoom(floor.id, roomId);
    handles.bridge.flyToFrame(roomId);
  }, [floor, handles, props]);

  // ---- side panel -------------------------------------------------------------------------------------------
  const people = useMemo(() => peopleAtPosts(floor, props.presence), [floor, props.presence]);
  const moves: Move[] = useMemo(() => {
    const list: Move[] = [];
    const mine = campus?.buildings.find((b) => b.workspace_id === props.workspaceId && b.accessible);
    if (mine) list.push({ id: 'office', label: t('hqNav.moves.myOffice'), icon: MOVE_ICONS.office, run: () => { void hqCommands.navigateTo({ building: mine.workspace_id }); } });
    if (building?.floors.some((f) => f.rooms.length > 0)) list.push({ id: 'tour', label: t('hqNav.moves.tour'), icon: MOVE_ICONS.tour, run: () => props.onOverlay('tour') });
    list.push({ id: 'meetings', label: t('hqNav.moves.meetings'), icon: MOVE_ICONS.meetings, run: () => props.onOverlay('agentMeetings') });
    list.push({ id: 'schedule', label: t('hqNav.moves.schedule'), icon: MOVE_ICONS.schedule, run: () => props.onGo('#calendar') });
    list.push({ id: 'team', label: t('hqNav.moves.team'), icon: MOVE_ICONS.team, run: () => props.onOverlay('teams') });
    return list;
  }, [campus, building, props]);
  const suggestions = useMemo(() => {
    const out: Array<{ id: string; label: string; target: { building?: string; room?: string } }> = [];
    for (const b of (campus?.buildings ?? []).filter((x) => x.accessible && !x.archived && x.workspace_id !== props.workspaceId).slice(0, 3)) out.push({ id: `b:${b.workspace_id}`, label: t('hqNav.moves.takeTo', { name: b.name }), target: { building: b.workspace_id } });
    if (level === 'building') for (const s of roomStops(building, 3)) if (s.kind === 'room') out.push({ id: `r:${s.roomId}`, label: t('hqNav.moves.takeTo', { name: s.label }), target: { room: s.roomId } });
    return out;
  }, [campus, building, level, props.workspaceId]);

  const openDrawer = (which: Exclude<Drawer, null>) => (event: React.MouseEvent<HTMLElement>) => { opener.current = event.currentTarget; setDrawer(which); };
  const closeDrawer = useCallback(() => { setDrawer(null); opener.current?.focus(); }, []);
  useEffect(() => {
    if (!drawer) return undefined;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeDrawer(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawer, closeDrawer]);
  useEffect(() => { if (layout.side === 'open' && layout.rail !== 'drawer') setDrawer(null); }, [layout.side, layout.rail]);
  useEffect(() => { setTab(level === 'building' ? 'plan' : 'moves'); }, [level]);

  const planBody = (
    <>
      <FloorPlan floor={floor} selectedRoomId={props.selectedRoomId} view={view} canThreeD={props.webglOk} onView={props.onView} onRoom={pickRoom} />
      <PeopleList rows={people} presenceKnown={props.presence !== null} onOpen={(row) => row.agentId && props.onAgent(row.agentId, row.desk)} />
    </>
  );
  const movesBody = <QuickMoves moves={moves} suggestions={suggestions} />;
  const dockBody = props.dock ? <section className="hqPaDock" aria-label={t('hq.paDock')}>{props.dock}</section> : null;
  const sideBody = level === 'building' ? (
    <>
      <div className="hqTabs" role="tablist" aria-label={t('hqNav.side.label')}>
        <button type="button" role="tab" id="hqnav-tab-plan" aria-selected={tab === 'plan'} aria-controls="hqnav-panel-plan" onClick={() => setTab('plan')}>{t('hqNav.tab.plan')}</button>
        <button type="button" role="tab" id="hqnav-tab-moves" aria-selected={tab === 'moves'} aria-controls="hqnav-panel-moves" onClick={() => setTab('moves')}>{t('hqNav.tab.moves')}</button>
        {dockBody && <button type="button" role="tab" id="hqnav-tab-assistant" aria-selected={tab === 'assistant'} aria-controls="hqnav-panel-assistant" onClick={() => setTab('assistant')}>{t('hqNav.tab.assistant')}</button>}
      </div>
      <div role="tabpanel" id="hqnav-panel-plan" aria-labelledby="hqnav-tab-plan" hidden={tab !== 'plan'}>{planBody}</div>
      <div role="tabpanel" id="hqnav-panel-moves" aria-labelledby="hqnav-tab-moves" hidden={tab !== 'moves'}>{movesBody}</div>
      {dockBody && <div role="tabpanel" id="hqnav-panel-assistant" aria-labelledby="hqnav-tab-assistant" hidden={tab !== 'assistant'}>{dockBody}</div>}
    </>
  ) : <>{movesBody}{dockBody}</>;

  // Without the layer (list view, campus still loading) the page is the scene as before.
  if (!navOn) {
    return (
      <div className="hqNav" data-nav="off">
        <div className="hqNavGrid">
          <div className="hqMainStage">{props.scene}{props.flowDock}</div>
          {dockBody && <aside className="hqNavSide" aria-label={t('hqNav.side.label')}>{dockBody}</aside>}
        </div>
      </div>
    );
  }

  const stripNode = <ViewStrip stops={stops} currentId={currentId} open={stripOpen} thumbKeyOf={stopKey} onToggle={toggleStrip} onGo={goStop} />;
  const toolbar = (level === 'building' && layout.rail === 'drawer') || layout.side !== 'open';
  const sideLabel = level === 'building' ? t('hqNav.drawer.side') : t('hqNav.drawer.moves');
  return (
    <div className="hqNav" ref={root} data-nav="on" data-level={level} data-rail={railOn ? layout.rail : 'none'} data-side={sideInline ? 'open' : layout.side} data-strip={stripOn && stripOpen ? 'open' : 'closed'}>
      {toolbar && (
        <div className="hqNavToolbar" role="group" aria-label={t('hqNav.region')}>
          {level === 'building' && layout.rail === 'drawer' && <button type="button" className="hqGhost" aria-expanded={drawer === 'floors'} onClick={openDrawer('floors')}><Layers aria-hidden="true" />{t('hqNav.drawer.floors')}</button>}
          {layout.side === 'hidden'
            ? <button type="button" className="hqGhost" onClick={toggleSide}><PanelRight aria-hidden="true" />{t('hqNav.side.expand')}</button>
            : layout.side === 'drawer' && <button type="button" className="hqGhost" aria-expanded={drawer === 'side'} onClick={openDrawer('side')}><CalendarDays aria-hidden="true" />{sideLabel}</button>}
        </div>
      )}
      <div className="hqNavGrid">
        {railOn && building && <FloorRail building={building} activeFloorId={floor?.id ?? null} slim={layout.rail === 'slim'} thumbKeyOf={(id) => keyFor(`floor:${id}`)} onPick={pickFloor} onToggle={toggleRail} />}
        <div className="hqMainStage">
          {props.scene}
          {stripOn && (slot ? createPortal(stripNode, slot) : stripNode)}
          {props.flowDock}
        </div>
        {sideInline && (
          <aside className="hqNavSide" aria-label={t('hqNav.side.label')}>
            <button type="button" className="hqIconButton hqSideToggle" onClick={toggleSide} aria-label={t('hqNav.side.collapse')} title={t('hqNav.side.collapse')}><PanelRight aria-hidden="true" className="flip" /></button>
            {sideBody}
          </aside>
        )}
      </div>
      {drawer === 'floors' && building && (
        <div className="hqDrawer start" role="dialog" aria-modal="false" aria-label={t('hqNav.drawer.floors')}>
          <button type="button" className="hqIconButton hqDrawerClose" onClick={closeDrawer} aria-label={t('hqNav.drawer.close')}><X aria-hidden="true" /></button>
          <FloorRail building={building} activeFloorId={floor?.id ?? null} slim={false} drawer thumbKeyOf={(id) => keyFor(`floor:${id}`)} onPick={pickFloor} />
        </div>
      )}
      {drawer === 'side' && (
        <div className="hqDrawer end" role="dialog" aria-modal="false" aria-label={sideLabel}>
          <button type="button" className="hqIconButton hqDrawerClose" onClick={closeDrawer} aria-label={t('hqNav.drawer.close')}><X aria-hidden="true" /></button>
          <div className="hqNavSideBody">{sideBody}</div>
        </div>
      )}
      <p className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-nav-live">{live}</p>
    </div>
  );
}
