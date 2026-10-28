/**
 * The navigation layer of the headquarters: a floor rail, a strip of quick views, a side panel (floor plan,
 * people, quick moves, the assistant dock), drawers on small screens and a live region that says where the
 * camera is going. It wraps the existing scene and composes with its panels; it holds no data of its own.
 * Everything shown is derived from the campus, the saved office layout and the presence index (navModel.ts).
 */
import { CalendarDays, Layers, PanelLeftClose, PanelLeftOpen, PanelRight, X } from 'lucide-react';
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { changeNote, presenceLive, PRESENCE_STALE_MS, type PanelLive, type TotalsSource } from '../lib/hqPanelStatus.ts';
import type { PresenceLink } from '../lib/presenceStream.ts';
import { LiveChip } from './LiveChip.tsx';
import { useNow } from './useNow.ts';
import type { HqView } from '../HqChrome';
import { hqCommands } from '../lib/hqExtensions.ts';
import type { Campus } from '../lib/campusModel.ts';
import type { SceneBuilding, SceneDesk, SceneFloor } from '../lib/headquartersProjection.ts';
import type { HqPresenceIndex } from '../lib/hqPresence.ts';
import { t, type MessageKey } from '../lib/i18n.ts';
import { roomName, sceneName } from '../lib/registryNames.ts';
import { useLocale } from '../useLocale';
import { HqCameraBridge } from './cameraBridge.ts';
import { currentStopId, peopleAtPosts, roomStops, stripStops, type StripStop } from './navModel.ts';
import { MOVE_ICONS } from './moveIcons.tsx';
import type { Move } from './SidePanel.tsx';
import { getThumb, thumbKey, ThumbCapture } from './thumbs.ts';
import { useNavLayout } from './useNavLayout.ts';
import { stopName } from './stopName.ts';
import './hqNavLayout.css';

const FloorRail = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.FloorRail })));
const ViewStrip = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.ViewStrip })));
const FloorPlan = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.FloorPlan })));
const PeopleList = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.PeopleList })));
const QuickMoves = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.QuickMoves })));
const OfficeStatus = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.OfficeStatus })));
const AssistantPanel = lazy(() => import('./NavChrome.tsx').then((m) => ({ default: m.AssistantPanel })));

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
  /** The campus key of the open building: the workspace id, or workspace and registry building when the registry has several. */
  openKey: string;
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
  /** The floor overview switch of the floor rail. */
  floorOverview: boolean;
  onFloorOverview: (on: boolean) => void;
  onRoom: (floorId: string, roomId: string) => void;
  onAgent: (agentId: string, desk: SceneDesk) => void;
  onOverlay: (overlay: NavOverlay) => void;
  onGo: (hash: string) => void;
  /** The scene (campus, building or list) and the live-flow dock under it. */
  scene: ReactNode;
  flowDock: ReactNode;
  /** The executive assistant dock, when the shell supplies one. */
  dock: ReactNode;
  /** The panel of the selected room or person (building level): docked in the side column or under the scene, never over the scene. */
  detail?: ReactNode;
  /** The campus read failed or was refused (nothing loaded yet), and how to read it again. */
  campusIssue?: Readonly<{ denied: boolean; message: string }> | null;
  onRetry?: () => void;
  unreported?: readonly import('../lib/campusModel.ts').StatusKey[];
  /** The live links the panels report on: presence (the people) and the flow (work in progress). */
  presenceLink?: PresenceLink;
  presenceAsOf?: string | null;
  flowState?: PanelLive;
  /** Total agents: where the open workspace's numbers come from and the words for it (lib/hqPanelStatus.ts `officeLive`). */
  totalsSource?: TotalsSource;
  officeState?: PanelLive;
  /** The Executive assistant's live status (`assistantLive`) and whether its own tools read failed. */
  assistantState?: PanelLive;
  assistantToolsFailed?: boolean;
}>;

function hash(parts: readonly string[]): string {
  let h = 5381;
  for (const part of parts) for (let i = 0; i < part.length; i++) h = ((h << 5) + h + part.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

type Drawer = null | 'floors' | 'side';

export function HqNavigation(props: HqNavigationProps) {
  const locale = useLocale();
  const { handles, level, view, campus, building, floor } = props;
  const { layout, focus, stripOpen, toggleRail, toggleSide, toggleStrip, toggleFocus } = useNavLayout();
  const [drawer, setDrawer] = useState<Drawer>(null);
  const [tab, setTab] = useState<'plan' | 'moves' | 'assistant'>('plan');
  const [liveCam, setLive] = useState('');
  const target = useRef<{ stopId: string; name: string } | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const root = useRef<HTMLDivElement>(null);
  // Inside a building the strip sits right under the scene, ahead of the module panels (the building view leaves a slot for it).
  const [slot, setSlot] = useState<Element | null>(null);

  const now = useNow(10_000);
  // The toolbar (hide the side panels, floors and side drawers) lives in the page header's tool row, so it costs the scene no row of its own.
  const [headSlot, setHeadSlot] = useState<Element | null>(null);
  useLayoutEffect(() => { setHeadSlot(root.current?.closest('.hqLive')?.querySelector('.hqHeadSlot') ?? null); });
  // Polite announcements: only when a link really changes (never per update).
  const [panelsLive, setPanelsLive] = useState('');
  const lastLive = useRef<{ office: PanelLive | null; flow: PanelLive | null }>({ office: null, flow: null });
  const peopleLive = presenceLive(props.presenceLink ?? 'connecting');
  const officeLive = props.officeState ?? peopleLive;
  useEffect(() => {
    const say = (state: PanelLive, name: string) => t(`hqPanels.say.${state}` as MessageKey, { name });
    const a = changeNote(lastLive.current.office, officeLive, t('hqPanels.name.office'), say);
    const b = props.flowState ? changeNote(lastLive.current.flow, props.flowState, t('hqPanels.name.flow'), say) : null;
    lastLive.current = { office: officeLive, flow: props.flowState ?? null };
    const note = [a, b].filter(Boolean).join(' ');
    if (note) setPanelsLive(note);
  }, [officeLive, props.flowState]);

  const navOn = view !== 'list' && !!campus;
  const stripOn = navOn && props.webglOk;
  const railOn = navOn && level === 'building' && !!building && layout.rail !== 'drawer' && layout.rail !== 'hidden';
  const sideInline = navOn && layout.side === 'open';

  useLayoutEffect(() => { setSlot(level === 'building' ? root.current?.querySelector('.hqStripSlot') ?? null : null); });

  // ---- thumbnails ---------------------------------------------------------------------------------------------
  const campusSig = useMemo(() => hash([locale, ...(campus?.buildings ?? []).map((b) => `${b.workspace_id}:${b.kind}:${b.floors ?? 0}`).sort()]), [campus, locale]);
  const buildingSig = useMemo(() => hash([locale, ...(building?.floors ?? []).flatMap((f) => f.rooms.map((r) => `${r.id}:${r.revision}:${r.x}:${r.z}:${r.width}:${r.depth}`))]), [building, locale]);
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
  const stops = useMemo(() => stripStops(campus, building, props.openKey), [campus, building, props.openKey, locale]);
  const currentId = currentStopId({ level, selectedBuildingId: props.selectedBuildingId, selectedRoomId: props.selectedRoomId, currentWorkspaceId: props.openKey });
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
      if (stop.workspaceId !== props.openKey && level === 'building') { props.onEnter(stop.workspaceId); return; }
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
    target.current = { stopId: `floor:${id}`, name: f ? sceneName(f) : '' };
    if (id === floor?.id) handles.bridge.flyHome(); else props.onFloor(id);
    if (f) setLive(t('hqNav.live.floor', { name: sceneName(f) }));
    setDrawer(null);
  }, [building, floor?.id, handles, props]);

  const pickRoom = useCallback((roomId: string) => {
    const f = floor?.rooms.find((r) => r.id === roomId);
    if (!f || !floor) return;
    target.current = { stopId: `room:${roomId}`, name: roomName(f) };
    props.onRoom(floor.id, roomId);
    handles.bridge.flyToFrame(roomId);
  }, [floor, handles, props]);

  // ---- side panel -------------------------------------------------------------------------------------------
  const people = useMemo(() => peopleAtPosts(floor, props.presence), [floor, props.presence]);
  const moves: Move[] = useMemo(() => {
    const list: Move[] = [];
    const mine = campus?.buildings.find((b) => b.workspace_id === props.openKey && b.accessible);
    if (mine) list.push({ id: 'office', label: t('hqNav.moves.myOffice'), icon: MOVE_ICONS.office, run: () => { void hqCommands.navigateTo({ building: mine.workspace_id }); } });
    if (building?.floors.some((f) => f.rooms.length > 0)) list.push({ id: 'tour', label: t('hqNav.moves.tour'), icon: MOVE_ICONS.tour, run: () => props.onOverlay('tour') });
    list.push({ id: 'meetings', label: t('hqNav.moves.meetings'), icon: MOVE_ICONS.meetings, run: () => props.onOverlay('agentMeetings') });
    list.push({ id: 'schedule', label: t('hqNav.moves.schedule'), icon: MOVE_ICONS.schedule, run: () => props.onGo('#calendar') });
    list.push({ id: 'team', label: t('hqNav.moves.team'), icon: MOVE_ICONS.team, run: () => props.onOverlay('teams') });
    return list;
  }, [campus, building, props]);
  const suggestions = useMemo(() => {
    const out: Array<{ id: string; label: string; target: { building?: string; room?: string } }> = [];
    for (const b of (campus?.buildings ?? []).filter((x) => x.accessible && !x.archived && x.workspace_id !== props.openKey).slice(0, 3)) out.push({ id: `b:${b.workspace_id}`, label: t('hqNav.moves.takeTo', { name: sceneName(b) }), target: { building: b.workspace_id } });
    if (level === 'building') for (const s of roomStops(building, 3)) if (s.kind === 'room') out.push({ id: `r:${s.roomId}`, label: t('hqNav.moves.takeTo', { name: s.label }), target: { room: s.roomId } });
    return out;
  }, [campus, building, level, props.openKey, locale]);

  const openDrawer = (which: Exclude<Drawer, null>) => (event: React.MouseEvent<HTMLElement>) => { opener.current = event.currentTarget; setDrawer(which); };
  const closeDrawer = useCallback(() => { setDrawer(null); opener.current?.focus(); }, []);
  useEffect(() => {
    if (!drawer) return undefined;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeDrawer(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawer, closeDrawer]);
  // A drawer takes focus when it opens (its close button) and gives it back to the button that opened it (closeDrawer).
  useEffect(() => {
    if (!drawer) return undefined;
    const id = window.requestAnimationFrame(() => root.current?.querySelector<HTMLElement>('.hqDrawer [data-autofocus]')?.focus());
    return () => window.cancelAnimationFrame(id);
  }, [drawer]);
  useEffect(() => { if (layout.side === 'open' && layout.rail !== 'drawer') setDrawer(null); }, [layout.side, layout.rail]);
  useEffect(() => { setTab(level === 'building' ? 'plan' : 'moves'); }, [level]);

  const planBody = (
    <Suspense fallback={null}>
      <FloorPlan floor={floor} selectedRoomId={props.selectedRoomId} view={view} canThreeD={props.webglOk} onView={props.onView} onRoom={pickRoom} />
      <PeopleList rows={people} presenceKnown={props.presence !== null} onOpen={(row) => row.agentId && props.onAgent(row.agentId, row.desk)}
        live={<LiveChip state={peopleLive} at={props.presenceAsOf ?? null} staleAfterMs={PRESENCE_STALE_MS} nowMs={now} testId="hq-people-live" />} />
    </Suspense>
  );
  const movesBody = <Suspense fallback={null}><QuickMoves moves={moves} suggestions={suggestions} /></Suspense>;
  const dockBody = props.dock ? <Suspense fallback={null}><AssistantPanel state={props.assistantState ?? 'connecting'} toolsFailed={props.assistantToolsFailed === true} nowMs={now}>{props.dock}</AssistantPanel></Suspense> : null;
  const statusNode = (variant: 'panel' | 'strip') => (
    <Suspense fallback={variant === 'strip' ? <div className="hqStatusStrip hqStatusReserve" aria-hidden="true" /> : null}>
      <OfficeStatus campus={campus} issue={props.campusIssue ?? null} unreported={props.unreported ?? []} source={props.totalsSource ?? 'campus'} state={officeLive} variant={variant} onRetry={props.onRetry ?? (() => undefined)} onGo={props.onGo} />
    </Suspense>
  );
  // The assistant dock lives in the side panel while there is one; on narrower screens it stays under the scene, where it always was.
  // Order, by what the owner needs first: the live state of the office, the quick moves, the assistant. Inside a building the floor plan and the people come first.
  const buildSide = (withDock: boolean) => (level === 'building' ? (
    <>
      <div className="hqTabs" role="tablist" aria-label={t('hqNav.side.label')}>
        <button type="button" role="tab" id="hqnav-tab-plan" aria-selected={tab === 'plan'} aria-controls="hqnav-panel-plan" onClick={() => setTab('plan')}>{t('hqNav.tab.plan')}</button>
        <button type="button" role="tab" id="hqnav-tab-moves" aria-selected={tab === 'moves'} aria-controls="hqnav-panel-moves" onClick={() => setTab('moves')}>{t('hqNav.tab.moves')}</button>
        {withDock && dockBody && <button type="button" role="tab" id="hqnav-tab-assistant" aria-selected={tab === 'assistant'} aria-controls="hqnav-panel-assistant" onClick={() => setTab('assistant')}>{t('hqNav.tab.assistant')}</button>}
      </div>
      <div role="tabpanel" id="hqnav-panel-plan" aria-labelledby="hqnav-tab-plan" hidden={tab !== 'plan'}>{planBody}</div>
      <div role="tabpanel" id="hqnav-panel-moves" aria-labelledby="hqnav-tab-moves" hidden={tab !== 'moves' && !(tab === 'assistant' && !withDock)}>{movesBody}</div>
      {withDock && dockBody && <div role="tabpanel" id="hqnav-panel-assistant" aria-labelledby="hqnav-tab-assistant" hidden={tab !== 'assistant'}>{dockBody}</div>}
    </>
  ) : <>{statusNode('panel')}{movesBody}{withDock ? dockBody : null}</>);
  const sideBody = buildSide(true);
  const detailNode = props.detail ? <div className="hqDetailDock" data-testid="hq-detail-dock">{props.detail}</div> : null;
  const live = <p className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-panels-live">{panelsLive}</p>;

  // Without the layer (list view, campus still loading) the page is the scene as before.
  if (!navOn) {
    return (
      <div className="hqNav" data-nav="off">
        <div className="hqNavGrid">
          <div className="hqMainStage">{props.scene}<div className="hqStatusRow">{props.flowDock}</div></div>
          {(dockBody || level === 'campus') && <aside className="hqNavSide" aria-label={t('hqNav.side.label')}>{level === 'campus' && statusNode('panel')}{dockBody}</aside>}
        </div>
        {live}
      </div>
    );
  }

  const stripNode = <Suspense fallback={null}><ViewStrip stops={stops} currentId={currentId} open={stripOpen} thumbKeyOf={stopKey} onToggle={toggleStrip} onGo={goStop} /></Suspense>;
  // From 900 px there are panels beside the scene to fold away; narrower, they are drawers and the scene already has the width.
  const canFocus = layout.width >= 900;
  const toolbar = canFocus || (level === 'building' && layout.rail === 'drawer') || layout.side !== 'open';
  const sideLabel = level === 'building' ? t('hqNav.drawer.side') : t('hqNav.drawer.moves');
  const toolbarNode = toolbar ? (
    <div className="hqNavToolbar" role="group" aria-label={t('hqNav.region')}>
      {level === 'building' && layout.rail === 'drawer' && <button type="button" className="hqGhost" aria-expanded={drawer === 'floors'} onClick={openDrawer('floors')}><Layers aria-hidden="true" />{t('hqNav.drawer.floors')}</button>}
      {canFocus && (
        <button type="button" className="hqGhost hqFocusToggle" aria-expanded={!focus} onClick={toggleFocus}>
          {focus ? <PanelLeftOpen aria-hidden="true" className="flip" /> : <PanelLeftClose aria-hidden="true" className="flip" />}
          {focus ? t('hqNav.focus.show') : t('hqNav.focus.hide')}
        </button>
      )}
      {layout.side === 'hidden' && !focus
        ? <button type="button" className="hqGhost" onClick={toggleSide}><PanelRight aria-hidden="true" />{t('hqNav.side.expand')}</button>
        : layout.side === 'drawer' && <button type="button" className="hqGhost" aria-expanded={drawer === 'side'} onClick={openDrawer('side')}><CalendarDays aria-hidden="true" />{sideLabel}</button>}
    </div>
  ) : null;
  const totalsStrip = level === 'campus' && !sideInline ? statusNode('strip') : null;
  return (
    <div className="hqNav" ref={root} data-nav="on" data-level={level} data-rail={railOn ? layout.rail : 'none'} data-side={sideInline ? 'open' : layout.side} data-strip={stripOn && stripOpen ? 'open' : 'closed'} data-toolbar={toolbar && !headSlot ? 'on' : 'off'}>
      {toolbarNode && (headSlot ? createPortal(toolbarNode, headSlot) : toolbarNode)}
      <div className="hqNavGrid">
        {railOn && building && <Suspense fallback={null}><FloorRail building={building} activeFloorId={floor?.id ?? null} slim={layout.rail === 'slim'} thumbKeyOf={(id) => keyFor(`floor:${id}`)} onPick={pickFloor} onToggle={toggleRail} overview={props.floorOverview} onOverview={() => props.onFloorOverview(!props.floorOverview)} /></Suspense>}
        <div className="hqMainStage">
          {props.scene}
          {stripOn && (slot ? createPortal(stripNode, slot) : stripNode)}
          <div className="hqStatusRow" data-has-totals={totalsStrip ? 'true' : 'false'}>
            {totalsStrip}
            {props.flowDock}
          </div>
          {!sideInline && detailNode}
        </div>
        {sideInline && (
          <aside className="hqNavSide" aria-label={t('hqNav.side.label')}>
            <button type="button" className="hqIconButton hqSideToggle" onClick={toggleSide} aria-label={t('hqNav.side.collapse')} title={t('hqNav.side.collapse')}><PanelRight aria-hidden="true" className="flip" /></button>
            {detailNode}
            {sideBody}
          </aside>
        )}
      </div>
      {!sideInline && dockBody && <div className="hqNavDockBelow">{dockBody}</div>}
      {drawer === 'floors' && building && (
        <div className="hqDrawer start" role="dialog" aria-modal="false" aria-label={t('hqNav.drawer.floors')}>
          <button type="button" className="hqIconButton hqDrawerClose" data-autofocus="" onClick={closeDrawer} aria-label={t('hqNav.drawer.close')}><X aria-hidden="true" /></button>
          <Suspense fallback={null}><FloorRail building={building} activeFloorId={floor?.id ?? null} slim={false} drawer thumbKeyOf={(id) => keyFor(`floor:${id}`)} onPick={pickFloor} overview={props.floorOverview} onOverview={() => { props.onFloorOverview(!props.floorOverview); closeDrawer(); }} /></Suspense>
        </div>
      )}
      {drawer === 'side' && (
        <div className="hqDrawer end" role="dialog" aria-modal="false" aria-label={sideLabel}>
          <button type="button" className="hqIconButton hqDrawerClose" data-autofocus="" onClick={closeDrawer} aria-label={t('hqNav.drawer.close')}><X aria-hidden="true" /></button>
          <div className="hqNavSideBody">{buildSide(false)}</div>
        </div>
      )}
      <p className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-nav-live">{liveCam}</p>
      {live}
    </div>
  );
}
