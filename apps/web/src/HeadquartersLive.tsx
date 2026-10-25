/**
 * ANUM MINE headquarters: a navigable campus of business buildings and a cutaway of each building.
 * It lives inside the app's own shell (sidebar and top bar), so it adds only scene controls.
 *
 * Data truth: buildings are the caller's authorised workspaces; floors, rooms and desks are the saved
 * office layout; agent status comes from task evidence and live meeting participants. Anything without
 * a record behind it reads "Presence unavailable" or "Not connected".
 * Extension points are documented in docs/ANUM_MINE_HQ_EXTENSION_POINTS.md.
 */
import { AlertTriangle, Building2, Compass, DoorOpen, Layers, LayoutGrid, Lock, RefreshCcw, Users, Wrench } from 'lucide-react';
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AgentMeetingSessions } from './AgentMeetingSessions';
import { BuildingView } from './BuildingView';
import { CampusListView } from './CampusListView';
import { CampusView } from './CampusView';
import { GuidedExecutiveTour } from './GuidedExecutiveTour';
import { HqPlanFallback } from './HqPlanFallback';
import { ViewSwitch, type HqView } from './HqChrome';
import { AgentPanel, RoomPanel } from './HqPanels';
import { OfficeSpaceLiveManager } from './OfficeSpaceLiveManager';
import { OfficeWorkstations } from './OfficeWorkstations';
import * as api from './lib/api.ts';
import { campusTotals, workspaceOf, type Campus, type CampusBuildingKind } from './lib/campusModel.ts';
import { expandCampus } from './lib/campusRegistry.ts';
import { HeadquartersController, adaptOfficeRegistry, type OfficeState } from './lib/headquartersController.ts';
import type { AgentEvidence, RoomGeometry, SceneDesk } from './lib/headquartersProjection.ts';
import { loadCampus, type CampusSource } from './lib/hqCampusLoader.ts';
import { hqCommands, resolveNavTarget, type HqAgentRef, type HqCallbacks, type HqNavResult, type HqNavTarget, type HqRoomRef, type HqSlots } from './lib/hqExtensions.ts';
import { loadCommerceState, loadTyreState } from './lib/hqModulesApi.ts';
import { loadPresence, saveCampusProfile, type LoadedPresence } from './lib/hqPresenceApi.ts';
import type { CommerceLive, ModuleState, TyreLive } from './lib/hqModules.ts';
import { agentPresence, indexPresence, loadMeetingEvidence, presenceFromItems, totalsOf, unreportedStatuses, type HqAgentPresence, type MeetingEvidence } from './lib/hqPresence.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { createOfficeSpaceClient } from './lib/officeSpaceClient.ts';
import { loadWorkspaceWorkstations, type WorkstationSnapshot } from './lib/officeWorkstations.ts';
import { HqFlowDock } from './HqFlowDock';
import { HqTeamPanel } from './HqTeamPanel';
import { useOfficeFlow } from './useOfficeFlow';
import { HqCameraBridge } from './hq-nav/cameraBridge.ts';
import { HqNavigation, type HqNavHandles } from './hq-nav/HqNavigation';
import { departmentsOf, initialsOf } from './hq-nav/navModel.ts';
import { ThumbCapture } from './hq-nav/thumbs.ts';
import { OrgGapsPanel } from './hq-provisioning/OrgGapsPanel';
import { AssignHost } from './hq-assign/AssignHost';
// Loaded only when the owner opens it, so its code and styles stay out of the headquarters chunk (e2e/hq-performance.spec.ts budget).
const StarterCampusDialog = lazy(() => import('./hq-starter-campus/StarterCampusDialog').then((m) => ({ default: m.StarterCampusDialog })));
import { useProvisioningGaps } from './hq-provisioning/useProvisioning';
import './hqFlow.css';
import './headquartersLive.css';

const officeApi = createOfficeSpaceClient(api.apiRequest, api.apiRequestVoid, api.currentScope);
const LIGHT_KEY = 'anum.hq.light';

export type HeadquartersProps = HqCallbacks & HqSlots & Readonly<{
  onClose?: () => void;
  /** Opens another workspace of the same organisation (the app remounts under it). */
  onSwitchWorkspace?: (workspaceId: string) => Promise<void>;
  /** Opens a workspace of another organisation. */
  onSwitchOrganisation?: (tenantId: string, workspaceId: string) => Promise<void>;
  /** Rooms with a live meeting, supplied by the meetings workstream. Empty until a room binding exists. */
  liveMeetingRoomIds?: ReadonlySet<string>;
  /** Counter of workspace events from the shell's event stream; the scene refreshes when it moves. */
  live?: number;
  /** Type of the shell's latest event and whether its one event stream is up (the flow reads again on a flow event). */
  liveEvent?: string | null;
  streamLive?: boolean;
  /** Extra controls in the header row (the entrypoint puts its presentation switch here). */
  headerExtra?: ReactNode;
}>;

type Overlay = 'none' | 'spaces' | 'workstations' | 'tour' | 'agentMeetings' | 'teams' | 'gaps';
const NO_ROOMS: ReadonlySet<string> = new Set();

function readLight(): boolean {
  try {
    const saved = window.localStorage.getItem(LIGHT_KEY);
    if (saved === 'night') return true;
    if (saved === 'day') return false;
  } catch { /* storage blocked: follow the device */ }
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function HeadquartersLive(props: HeadquartersProps) {
  const [state, setState] = useState<OfficeState | null>(null);
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [toolsOpen, setToolsOpen] = useState(false);
  const [starterOpen, setStarterOpen] = useState(false);
  const flow = useOfficeFlow(true, { tick: props.live ?? 0, type: props.liveEvent ?? null, streaming: props.streamLive === true });
  const gaps = useProvisioningGaps(flow.snapshot?.as_of ?? 0);
  const [flowOpen, setFlowOpen] = useState(false);
  const [roomFilter, setRoomFilter] = useState<string | null>(null);
  // Owner of this workspace (workspace:manage): only they are told to set a department; the server checks every write.
  const [canManage, setCanManage] = useState(false);
  const [view, setView] = useState<HqView>('3d');
  const [night, setNight] = useState(readLight);
  const [fullscreen, setFullscreen] = useState(false);
  const [webglOk, setWebglOk] = useState(true);
  const [webglNote, setWebglNote] = useState<string | null>(null);
  const [level, setLevel] = useState<'campus' | 'building'>('campus');
  const [campusPick, setCampusPick] = useState<string | null>(null);
  const [roomId, setRoomId] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [agentDesk, setAgentDesk] = useState<SceneDesk | null>(null);
  const [snapshot, setSnapshot] = useState<WorkstationSnapshot | null>(null);
  const [meetings, setMeetings] = useState<MeetingEvidence | null>(null);
  const [presenceApi, setPresenceApi] = useState<LoadedPresence | null>(null);
  const [loaded, setLoaded] = useState<{ campus: Campus; source: CampusSource } | null>(null);
  const [campusError, setCampusError] = useState<{ denied: boolean; message: string } | null>(null);
  const [commerce, setCommerce] = useState<ModuleState<CommerceLive>>({ state: 'loading' });
  const [tyre, setTyre] = useState<ModuleState<TyreLive>>({ state: 'loading' });
  const [moduleStamp, setModuleStamp] = useState(0);
  const host = useRef<HTMLElement>(null);
  const campusLoadedAt = useRef(0);
  const want = useRef<HqNavTarget | null>(null);
  const reducedMotion = useMemo(() => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches, []);
  const scope = api.currentScope();
  useEffect(() => {
    let active = true;
    api.getCurrentMembership().then((mine) => { if (active) setCanManage(mine.active && mine.role === 'owner'); }).catch(() => { if (active) setCanManage(false); });
    return () => { active = false; };
  }, [scope.tenantId, scope.workspaceId]);

  const navHandles: HqNavHandles = useMemo(() => ({ bridge: new HqCameraBridge(), capture: new ThumbCapture() }), []);
  navHandles.bridge.setReducedMotion(reducedMotion);

  const latestProps = useRef(props);
  latestProps.current = props;

  const controller = useMemo(() => new HeadquartersController({
    scope: () => api.currentScope(),
    registry: async () => adaptOfficeRegistry(await officeApi.load()),
    agents: async () => {
      const [result, meet, live] = await Promise.all([
        loadWorkspaceWorkstations(),
        loadMeetingEvidence((path) => api.apiRequest(path, { method: 'GET' })),
        loadPresence().catch(() => null),
      ]);
      const current = api.currentScope();
      if (result.snapshot.scope.tenantId !== current.tenantId || result.snapshot.scope.workspaceId !== current.workspaceId) {
        throw new Error('Workspace changed during agent evidence load');
      }
      setSnapshot(result.snapshot);
      setMeetings(meet);
      setPresenceApi(live);
      return result.snapshot.records.map((agent): AgentEvidence => ({
        id: agent.agentId, name: agent.name,
        status: agent.status === 'working' ? 'running'
          : ['queued', 'waiting_approval', 'blocked'].includes(agent.status) ? 'waiting'
            : agent.status === 'idle' && result.snapshot.complete ? 'idle' : 'unknown',
        observedAt: result.snapshot.observedAt, taskIds: agent.taskIds, source: 'authenticated-agent-snapshot',
      }));
    },
  }, 10_000), []);

  // The shell owns the one workspace event stream and hands every event over as a counter: the scene
  // re-reads (at most every 300 ms) when it moves, and the scoped periodic refresh is the fallback.
  const lastLive = useRef(props.live ?? 0);
  useEffect(() => {
    if (props.live === undefined || props.live === lastLive.current) return undefined;
    lastLive.current = props.live;
    const id = window.setTimeout(() => { void controller.refresh(); }, 300);
    return () => window.clearTimeout(id);
  }, [props.live, controller]);
  useEffect(() => {
    const refreshVisible = () => { if (document.visibilityState === 'visible') void controller.refresh(); };
    const refreshFocused = () => { void controller.refresh(); };
    window.addEventListener('focus', refreshFocused);
    document.addEventListener('visibilitychange', refreshVisible);
    return () => { window.removeEventListener('focus', refreshFocused); document.removeEventListener('visibilitychange', refreshVisible); };
  }, [controller]);
  useEffect(() => {
    const unsubscribe = controller.subscribe(setState);
    void controller.initialize();
    controller.scheduleRefresh();
    return () => { unsubscribe(); controller.dispose(); };
  }, [controller]);
  useEffect(() => {
    const handler = () => setFullscreen(document.fullscreenElement === host.current);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);
  useEffect(() => { try { window.localStorage.setItem(LIGHT_KEY, night ? 'night' : 'day'); } catch { /* remembered for this page only */ } }, [night]);

  // Every embedded panel has its own Close button; Escape closes whichever is open.
  useEffect(() => {
    if (overlay === 'none') return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // A dialog inside the panel (for example a review dialog) closes first and returns focus to its opener.
      const inner = (event.target as Element | null)?.closest?.('[role="dialog"]');
      if (inner && !inner.classList.contains('hqTeamModal') && !inner.classList.contains('hqModalHost')) return;
      setOverlay('none');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [overlay]);

  const projection = state?.projection ?? null;
  const registryBuilding = projection?.buildings.find((b) => b.id === state?.selected.buildingId) ?? projection?.buildings[0] ?? null;
  const floor = registryBuilding?.floors.find((f) => f.id === state?.selected.floorId) ?? registryBuilding?.floors[0] ?? null;

  // The presence endpoint is the source when the server has it; otherwise the browser reads tasks and meetings itself.
  const presenceList: HqAgentPresence[] | null = useMemo(() => {
    if (presenceApi) {
      const departments = new Map((snapshot?.records ?? []).map((r) => [r.agentId, r.department?.trim() || (r.suggestedDepartment !== 'Unassigned' ? r.suggestedDepartment : null)]));
      return presenceFromItems(presenceApi.items, (id) => departments.get(id) ?? null);
    }
    return snapshot ? agentPresence(snapshot.records, meetings) : null;
  }, [presenceApi, snapshot, meetings]);
  const presenceIndex = useMemo(() => (presenceList ? indexPresence(presenceList) : null), [presenceList]);
  const presenceOf = useCallback((id: string, desk: SceneDesk | null) => presenceIndex?.byAgent.get(id) ?? (desk ? presenceIndex?.byDesk.get(desk.id) : undefined), [presenceIndex]);
  const localTotals = useMemo(() => (presenceList ? totalsOf(presenceList.filter((p) => !p.agentId.startsWith('desk:'))) : null), [presenceList]);
  const unreported = useMemo(() => (presenceApi ? [] : unreportedStatuses(meetings)), [presenceApi, meetings]);
  const verifiedAt = presenceApi?.asOf ?? snapshot?.observedAt ?? null;
  const floorCount = registryBuilding ? registryBuilding.floors.length : null;

  // Campus: the endpoint when present, else memberships; the open workspace's counts come from local presence.
  const localRef = useRef({ totals: localTotals, floors: floorCount, verifiedAt });
  localRef.current = { totals: localTotals, floors: floorCount, verifiedAt };
  const refreshCampus = useCallback(async () => {
    try {
      const result = await loadCampus(localRef.current);
      setLoaded(result);
      setCampusError(null);
    } catch (error) {
      const status = (error as { status?: number }).status;
      setCampusError({ denied: status === 403 || status === 401, message: error instanceof Error ? error.message : t('hq.error.generic') });
    }
  }, []);
  // The campus does not wait for the office registry: it loads at once, then at most every 30 seconds with the scene.
  useEffect(() => {
    const now = Date.now();
    if (loaded && now - campusLoadedAt.current < 30_000) return;
    campusLoadedAt.current = now;
    void refreshCampus();
  }, [state?.updatedAt, refreshCampus, loaded]);

  const campus: Campus | null = useMemo(() => {
    if (!loaded) return null;
    const buildings = loaded.campus.buildings.map((b) => (b.workspace_id === scope.workspaceId && (loaded.source === 'memberships' || !b.totals)
      ? { ...b, floors: b.floors ?? floorCount, totals: b.totals ?? localTotals, presence_verified_at: b.presence_verified_at ?? verifiedAt }
      : b));
    const merged: Campus = { ...loaded.campus, buildings, totals: campusTotals(buildings).totals };
    // A workspace whose office registry holds several buildings shows each of them on the campus (never another workspace's registry).
    return expandCampus(merged, scope.workspaceId, projection?.buildings ?? [], presenceIndex);
  }, [loaded, localTotals, floorCount, verifiedAt, scope.workspaceId, projection, presenceIndex]);

  // The campus building that is open: the registry building when the workspace has several, else the workspace's own building.
  const currentBuilding = campus?.buildings.find((b) => workspaceOf(b) === scope.workspaceId && (!b.registryBuildingId || b.registryBuildingId === registryBuilding?.id)) ?? null;
  const openKey = currentBuilding?.workspace_id ?? scope.workspaceId;
  const avatars = useMemo(() => (presenceList ?? []).filter((p) => p.name && !p.agentId.startsWith('desk:')).slice(0, 4).map((p) => initialsOf(p.name)), [presenceList]);
  const departments = useMemo(() => departmentsOf((presenceList ?? []).filter((p) => !p.agentId.startsWith('desk:')).map((p) => p.role)), [presenceList]);

  // Module panels follow the open workspace; they load when a building opens and refresh with the scene.
  useEffect(() => {
    if (level !== 'building') return;
    let live = true;
    void loadCommerceState().then((s) => { if (live) setCommerce(s); });
    void loadTyreState().then((s) => { if (live) setTyre(s); });
    return () => { live = false; };
  }, [level, moduleStamp]);
  useEffect(() => { if (level === 'building' && state?.updatedAt) { const id = window.setTimeout(() => setModuleStamp((n) => n + 1), 45_000); return () => window.clearTimeout(id); } return undefined; }, [level, state?.updatedAt]);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (host.current) await host.current.requestFullscreen();
    } catch { /* the browser refused fullscreen: the view stays as it is */ }
  }, []);

  const onUnavailable = useCallback((reason: string) => {
    setWebglOk(false);
    setWebglNote(reason);
    setView(level === 'building' ? 'top' : 'list');
  }, [level]);

  // Entering buildings, rooms and agents.
  const switchTo = useCallback(async (workspaceId: string, tenantId: string | null, pending: HqNavTarget) => {
    const p = latestProps.current;
    hqCommands.keepPending(pending);
    try {
      if (tenantId && tenantId !== api.currentScope().tenantId) {
        if (!p.onSwitchOrganisation) throw new Error(t('hq.error.cannotSwitch'));
        await p.onSwitchOrganisation(tenantId, workspaceId);
      } else {
        if (!p.onSwitchWorkspace) throw new Error(t('hq.error.cannotSwitch'));
        await p.onSwitchWorkspace(workspaceId);
      }
    } catch (error) {
      hqCommands.takePending();
      setCampusError({ denied: false, message: error instanceof Error ? error.message : t('hq.error.generic') });
    }
  }, []);

  const enterBuilding = useCallback((key: string) => {
    const b = campus?.buildings.find((x) => x.workspace_id === key);
    const workspaceId = b ? workspaceOf(b) : key;
    if (workspaceId === api.currentScope().workspaceId) {
      if (b?.registryBuildingId && b.registryBuildingId !== registryBuilding?.id) controller.selectBuilding(b.registryBuildingId);
      setLevel('building'); setRoomId(null); setAgentId(null); setAgentDesk(null);
      setView((v) => (v === 'list' && webglOk ? '3d' : v));
      return;
    }
    void switchTo(workspaceId, b?.tenant_id ?? null, { building: workspaceId });
  }, [campus, switchTo, webglOk, controller, registryBuilding?.id]);

  const roomRef = useCallback((room: RoomGeometry): HqRoomRef => {
    const f = registryBuilding?.floors.find((x) => x.id === room.floorId);
    return { workspaceId: scope.workspaceId, buildingId: registryBuilding?.id ?? null, floorId: room.floorId, floorLevel: f?.level ?? 0, roomId: room.id, label: room.label, kind: room.kind, capacity: room.capacity };
  }, [registryBuilding, scope.workspaceId]);

  const selectRoom = useCallback((room: RoomGeometry | null) => {
    setAgentId(null); setAgentDesk(null);
    if (!room) { setRoomId(null); return; }
    setRoomId(room.id);
    latestProps.current.onSelectRoom?.(roomRef(room));
  }, [roomRef]);

  const selectAgent = useCallback((id: string, desk: SceneDesk | null) => {
    setAgentId(id); setAgentDesk(desk);
    const p = presenceOf(id, desk);
    latestProps.current.onSelectAgent?.({
      agentId: id, name: p?.name ?? '', role: p?.role ?? null, status: p?.status ?? 'unknown',
      deskId: desk?.id ?? null, roomId: desk?.roomId ?? null, workspaceId: scope.workspaceId,
    });
  }, [presenceOf, scope.workspaceId]);

  const goFloor = useCallback((id: string) => { controller.selectFloor(id); setRoomId(null); setAgentId(null); }, [controller]);
  const openRoomById = useCallback((fid: string, rid: string) => {
    const fl = registryBuilding?.floors.find((f) => f.id === fid);
    const room = fl?.rooms.find((r) => r.id === rid);
    if (!fl || !room) return;
    if (floor?.id !== fid) controller.selectFloor(fid);
    setLevel('building'); setView((v) => (v === 'list' ? '3d' : v));
    setRoomId(rid); setAgentId(null);
    latestProps.current.onSelectRoom?.(roomRef(room));
  }, [registryBuilding, floor?.id, controller, roomRef]);

  const roomLabels = useMemo(() => {
    const map = new Map<string, { label: string }>();
    for (const b of projection?.buildings ?? []) for (const f of b.floors) for (const r of f.rooms) map.set(r.id, { label: r.label });
    return map;
  }, [projection]);
  const focusRoomById = useCallback((rid: string) => {
    for (const f of registryBuilding?.floors ?? []) if (f.rooms.some((r) => r.id === rid)) { openRoomById(f.id, rid); return; }
  }, [registryBuilding, openRoomById]);
  const goTo = useCallback((hash: string) => { window.location.hash = hash; }, []);

  // Navigation commands from the executive assistant.
  const navScene = useMemo(() => {
    if (!registryBuilding) return null;
    return {
      workspaceId: scope.workspaceId, buildingId: registryBuilding.id,
      floors: registryBuilding.floors.map((f) => ({ id: f.id, level: f.level, name: f.name, rooms: f.rooms.map((r) => ({ id: r.id, label: r.label })) })),
      agents: (presenceList ?? []).filter((p) => !p.agentId.startsWith('desk:')).map((p) => {
        const desk = registryBuilding.floors.flatMap((f) => f.desks).find((d) => d.agentId === p.agentId);
        return { id: p.agentId, name: p.name, roomId: desk?.roomId ?? null };
      }),
    };
  }, [registryBuilding, presenceList, scope.workspaceId]);

  const navigate = useCallback(async (target: HqNavTarget): Promise<HqNavResult> => {
    if (target.view) setView(target.view === '3d' && !webglOk ? 'list' : target.view);
    if (!campus || !navScene) { want.current = target; return { ok: true, applied: { workspaceId: scope.workspaceId, floorId: null, roomId: null, agentId: null } }; }
    const res = resolveNavTarget(target, campus, navScene);
    if (res.kind === 'error') return { ok: false, code: res.code, message: res.message };
    if (res.kind === 'switch') {
      const b = campus.buildings.find((x) => x.workspace_id === res.workspaceId);
      await switchTo(res.workspaceId, b?.tenant_id ?? null, target);
      return { ok: true, applied: { workspaceId: res.workspaceId, floorId: null, roomId: null, agentId: null } };
    }
    setLevel('building');
    if (res.buildingId && res.buildingId !== registryBuilding?.id) controller.selectBuilding(res.buildingId);
    if (res.floorId && res.floorId !== floor?.id) controller.selectFloor(res.floorId);
    setRoomId(res.roomId); setAgentId(res.agentId);
    if (res.roomId) {
      const room = registryBuilding?.floors.flatMap((f) => f.rooms).find((r) => r.id === res.roomId);
      if (room) latestProps.current.onSelectRoom?.(roomRef(room));
    }
    return { ok: true, applied: { workspaceId: res.workspaceId, floorId: res.floorId, roomId: res.roomId, agentId: res.agentId } };
  }, [campus, navScene, scope.workspaceId, floor?.id, controller, registryBuilding, roomRef, switchTo, webglOk]);
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  useEffect(() => hqCommands.attach((target) => navigateRef.current(target)), []);
  useEffect(() => {
    if (want.current && campus && navScene) { const target = want.current; want.current = null; void navigateRef.current(target); }
  }, [campus, navScene]);

  const saveProfile = useCallback(async (kind: CampusBuildingKind, icon: string, revision: number | null): Promise<string | null> => {
    try {
      await saveCampusProfile({ kind, icon, ...(revision !== null ? { expected_revision: revision } : {}) } as Parameters<typeof saveCampusProfile>[0]);
      campusLoadedAt.current = 0;
      await refreshCampus();
      return null;
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (status === 409) return t('hq.profile.conflict');
      if (status === 403) return t('hq.profile.ownerOnly');
      return error instanceof Error ? error.message : t('hq.error.generic');
    }
  }, [refreshCampus]);

  const retry = () => { campusLoadedAt.current = 0; void controller.refresh(); void refreshCampus(); };
  const roomsForRoom = floor && roomId ? floor.rooms.find((r) => r.id === roomId) ?? null : null;
  const roomDesks = floor && roomsForRoom ? floor.desks.filter((d) => d.roomId === roomsForRoom.id) : [];
  const agentRoom = agentDesk ? floor?.rooms.find((r) => r.id === agentDesk.roomId) ?? null : null;
  const tourStops = (floor?.rooms ?? []).map((r) => ({ id: r.id, name: r.label, floor: floor?.level ?? 0, x: r.x, y: r.z, description: r.kind, staff: [] }));
  const effectiveView: HqView = webglOk ? view : (level === 'building' && view !== 'list' ? 'top' : 'list');
  const closePanels = () => { setRoomId(null); setAgentId(null); setAgentDesk(null); };
  const ctx = { close: closePanels };
  const roomPanelRoom = roomsForRoom;

  const agentRef: HqAgentRef | null = agentId
    ? { agentId, name: presenceOf(agentId, agentDesk)?.name ?? '', role: presenceOf(agentId, agentDesk)?.role ?? null, status: presenceOf(agentId, agentDesk)?.status ?? 'unknown', deskId: agentDesk?.id ?? null, roomId: agentDesk?.roomId ?? null, workspaceId: scope.workspaceId }
    : null;
  const sidePanel = agentId && agentRef
    ? (props.renderWorkstationPanel
      ? props.renderWorkstationPanel(agentRef, ctx)
      : <AgentPanel agentId={agentId} deskId={agentDesk?.id ?? null} refreshKey={state?.updatedAt ?? null} presence={presenceOf(agentId, agentDesk)} roomLabel={agentRoom?.label ?? null} deskLabel={agentDesk?.label ?? null} onClose={closePanels} />)
    : roomPanelRoom
      ? (props.renderRoomPanel
        ? props.renderRoomPanel(roomRef(roomPanelRoom), ctx)
        : <><RoomPanel room={roomPanelRoom} desks={roomDesks} presence={presenceIndex} onClose={closePanels} onOpenAgent={(id, desk) => selectAgent(id, desk)} buildingKind={currentBuilding?.kind ?? null} canManage={canManage} onOpenFlow={() => setFlowOpen(true)} onOpenGaps={() => setOverlay('gaps')} /><HqTeamPanel roomId={roomPanelRoom.id} refreshKey={flow.snapshot?.as_of ?? 0} onNavigate={goTo} onOpenSpaces={() => setOverlay('spaces')} /></>)
      : null;

  const status = state?.status;
  const connection = status === 'ready' ? t('hq.conn.ready') : status === 'refreshing' ? t('hq.conn.updating') : status === 'stale' ? t('hq.conn.stale') : status === 'error' ? t('hq.conn.error') : t('hq.conn.connecting');
  const loading = !campus && !campusError;

  const buildingKind: CampusBuildingKind = currentBuilding?.kind ?? 'business';

  return (
    <section className={`hqLive ${night ? 'hqNight' : 'hqDay'}`} ref={host} aria-label={t('hq.region')}>
      <header className="hqHead">
        <div className="hqHeadTitle">
          <h1 id="view-title">ANUM <em>MINE</em></h1>
          <p>{t('hq.tagline')}</p>
        </div>
        <div className="hqHeadTools">
          {props.headerExtra}
          <span className={`hqConn tone-${status === 'ready' ? 'ok' : status === 'error' ? 'stop' : 'warn'}`} role="status">{connection}</span>
          <button type="button" className="hqIconButton" onClick={retry} disabled={state?.busy} aria-label={t('hq.refresh')} title={t('hq.refresh')}><RefreshCcw aria-hidden="true" /></button>
          <ViewSwitch view={effectiveView} onChange={(v) => { if (v === "3d" && !webglOk) return; setView(v); }} />
          <div className="hqMenuWrap">
            <button type="button" className="hqGhost" aria-expanded={toolsOpen} onClick={() => setToolsOpen((v) => !v)}><Wrench aria-hidden="true" />{t('hq.tools')}</button>
            {toolsOpen && (
              <ul className="hqMenu">
                <li><button type="button" aria-pressed={flowOpen} onClick={() => { setFlowOpen((v) => !v); setToolsOpen(false); }}><Layers aria-hidden="true" />{t('hqFlow.open')}</button></li>
                <li><button type="button" onClick={() => { setStarterOpen(true); setToolsOpen(false); }}><Building2 aria-hidden="true" />{t('hqSc.open')}</button></li>
                <li><button type="button" onClick={() => { setOverlay('gaps'); setToolsOpen(false); }}><Users aria-hidden="true" />{gaps.gaps && gaps.gaps.gaps.length > 0 ? t('hqProv.openCount', { count: gaps.gaps.gaps.length }) : t('hqProv.open')}</button></li>
                <li><button type="button" onClick={() => { setOverlay('teams'); setToolsOpen(false); }}><Users aria-hidden="true" />{t('hqTeam.title')}</button></li>
                {([
                  ['spaces', LayoutGrid, 'hq.tools.spaces'], ['workstations', Users, 'hq.tools.workstations'], ['agentMeetings', Users, 'hq.tools.meetings'],
                  ['tour', Compass, 'hq.tools.tour'],
                ] as const).map(([id, Icon, key]) => (
                  <li key={id}><button type="button" onClick={() => { setOverlay(id); setToolsOpen(false); }}><Icon aria-hidden="true" />{t(key as MessageKey)}</button></li>
                ))}
                <li className="hqMenuNote"><small>{t('hq.credits')}</small></li>
              </ul>
            )}
          </div>
        </div>
      </header>

      {!webglOk && (
        <p className="hqNotice" role="status"><AlertTriangle aria-hidden="true" />{t('hq.webgl.off')}{webglNote ? ` (${webglNote})` : ''}</p>
      )}
      {campusError && (
        <div className="hqNotice error" role="alert">
          {campusError.denied ? <Lock aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <span>{campusError.denied ? t('hq.error.denied') : t('hq.error.campus', { reason: campusError.message })}</span>
          {!campusError.denied && <button type="button" className="hqGhost" onClick={retry}>{t('hq.retry')}</button>}
        </div>
      )}
      {state?.error && state.status !== 'ready' && !campusError && (
        <div className="hqNotice error" role="alert">
          <AlertTriangle aria-hidden="true" /><span>{state.error.code === 'NOT_AUTHORIZED' ? t('hq.error.denied') : state.error.message}</span>
          {state.error.canRetry && <button type="button" className="hqGhost" onClick={retry}>{t('hq.retry')}</button>}
        </div>
      )}

      <div className="hqMain">
        <HqNavigation
          handles={navHandles} level={level} view={effectiveView} webglOk={webglOk} night={night} campus={campus}
          tenantId={scope.tenantId} workspaceId={scope.workspaceId} openKey={openKey} selectedBuildingId={campusPick}
          building={registryBuilding} floor={floor} selectedRoomId={roomId} presence={presenceIndex}
          onView={(v) => { if (v === '3d' && !webglOk) return; setView(v); }}
          onShowCampus={() => setLevel('campus')} onSelectBuilding={setCampusPick} onEnter={enterBuilding} onFloor={goFloor}
          onRoom={(fid, rid) => openRoomById(fid, rid)} onAgent={(id, desk) => selectAgent(id, desk)}
          onOverlay={(o) => setOverlay(o)} onGo={goTo}
          dock={props.renderPaDock ? props.renderPaDock({ close: () => undefined }) : null}
          scene={<>
          {loading && <div className="hqLoading" role="status" aria-busy="true"><span className="hqSpinner" aria-hidden="true" />{t('hq.loading')}</div>}
          {campus && effectiveView === 'list' && (
            <CampusListView
              campus={campus} currentWorkspaceId={scope.workspaceId} detail={projection?.buildings ?? []}
              onEnter={enterBuilding} canManage={canManage} onOpenFlow={() => setFlowOpen(true)} onOpenGaps={() => setOverlay('gaps')} onOpenRoom={(ws, fid, rid) => { if (ws === scope.workspaceId) openRoomById(fid, rid); }}
            />
          )}
          {campus && webglOk && effectiveView !== 'list' && level === 'campus' && (
            <CampusView
              campus={campus} unreported={unreported} view={effectiveView} night={night} reducedMotion={reducedMotion} fullscreen={fullscreen}
              currentWorkspaceId={openKey} avatars={avatars} departments={departments} nav={navHandles} selectedId={campusPick} onSelect={setCampusPick} onEnter={enterBuilding}
              onToggleFullscreen={() => void toggleFullscreen()} onUnavailable={onUnavailable} onNight={setNight} onSaveProfile={saveProfile}
            />
          )}
          {campus && !webglOk && level === 'building' && effectiveView !== 'list' && floor && (
            <>
              <HqPlanFallback flow={{ snapshot: flow.snapshot, batch: flow.batch, onPickRoom: (id) => { setRoomFilter(id); setFlowOpen(true); } }} floor={floor} selectedRoomId={roomId} night={night} onSelectRoom={selectRoom} />
              {sidePanel && <div className="hqSideInline">{sidePanel}</div>}
            </>
          )}
          {campus && webglOk && effectiveView !== 'list' && level === 'building' && (registryBuilding && floor ? (
            <BuildingView
              flow={{ snapshot: flow.snapshot, batch: flow.batch, onPickRoom: (id) => { setRoomFilter(id); setFlowOpen(true); } }}
              workspaceName={currentBuilding?.name ?? scope.workspaceId} kindLabel={currentBuilding?.registryBuildingId ? (currentBuilding.workspaceName ?? '') : t(`hq.kind.${buildingKind}` as MessageKey)}
              buildings={projection?.buildings ?? []} building={registryBuilding} floor={floor}
              presence={presenceIndex} unreported={unreported} verifiedAt={verifiedAt}
              liveMeetings={meetings ? meetings.liveMeetings : null} liveMeetingRoomIds={props.liveMeetingRoomIds ?? NO_ROOMS}
              commerce={commerce} tyre={tyre} view={effectiveView} night={night} reducedMotion={reducedMotion} fullscreen={fullscreen}
              selectedRoomId={roomId} selectedAgentId={agentId}
              onBack={() => setLevel('campus')} onSelectBuilding={(id) => controller.selectBuilding(id)} onSelectFloor={goFloor}
              onSelectRoom={selectRoom} onSelectAgent={(id, desk) => selectAgent(id, desk)}
              nav={navHandles} onManageSpaces={() => setOverlay('spaces')} onToggleFullscreen={() => void toggleFullscreen()} onUnavailable={onUnavailable} onNight={setNight}
              side={sidePanel}
            />
          ) : (
            <div className="hqEmptyScene" role="status">
              <DoorOpen aria-hidden="true" />
              <h2>{state?.busy ? t('hq.loading') : t('hq.noLayout.title')}</h2>
              <p>{state?.busy ? t('hq.loadingLayout') : t('hq.noLayout.body')}</p>
              {!state?.busy && <p>{t('hqSc.emptyBody')}</p>}
              {!state?.busy && <button type="button" className="hqPrimary" onClick={() => setStarterOpen(true)}>{t('hqSc.open')}</button>}
              {!state?.busy && <button type="button" className="hqGhost" onClick={() => setOverlay('spaces')}>{t('hq.manageSpaces')}</button>}
              <button type="button" className="hqGhost" onClick={() => setLevel('campus')}>{t('hq.back')}</button>
            </div>
          ))}
          </>}
          flowDock={
          <HqFlowDock snapshot={flow.snapshot} status={flow.status} error={flow.error} link={flow.link} batch={flow.batch}
            rooms={roomLabels} open={flowOpen} onToggle={() => setFlowOpen((v) => !v)} onReload={() => void flow.reload()}
            roomFilter={roomFilter} onRoomFilter={setRoomFilter} onFocusRoom={focusRoomById} onNavigate={goTo} />
          }
        />
      </div>

      <AssignHost onAssigned={() => void flow.reload()} onNavigate={goTo}
        seatedAgentIds={(rid) => new Set((registryBuilding?.floors ?? []).flatMap((f) => f.desks).filter((d) => d.roomId === rid && d.agentId).map((d) => d.agentId as string))} />
      {starterOpen && <Suspense fallback={null}><StarterCampusDialog onClose={() => setStarterOpen(false)}
        onCreated={() => { void controller.refresh(); void flow.reload(); void gaps.reload(); }} onOpenGaps={() => setOverlay('gaps')} /></Suspense>}

      {overlay !== 'none' && (
        <div className="hqModalHost" role="dialog" aria-modal="false" aria-label={t('hq.tools')}>
          {overlay === 'teams' ? <div className="hqTeamModal" role="dialog" aria-modal="true" aria-label={t('hqTeam.title')}>
            <HqTeamPanel buildingId={registryBuilding?.id ?? null} refreshKey={flow.snapshot?.as_of ?? 0} onNavigate={goTo} onOpenSpaces={() => setOverlay('spaces')} onFocusRoom={(id) => { focusRoomById(id); setOverlay('none'); }} />
            <OrgGapsPanel state={gaps} onNavigate={goTo} onProvisioned={() => { void flow.reload(); void controller.refresh(); }} />
          </div>
          : overlay === 'gaps' ? <div className="hqTeamModal" role="dialog" aria-modal="true" aria-label={t('hqProv.title')}>
            <OrgGapsPanel state={gaps} onNavigate={goTo} onProvisioned={() => { void flow.reload(); void controller.refresh(); }} />
          </div>
          : overlay === 'spaces' ? <OfficeSpaceLiveManager onClose={() => { setOverlay('none'); void controller.refresh(); }} />
            : overlay === 'workstations' ? <OfficeWorkstations onClose={() => setOverlay('none')} />
              : overlay === 'agentMeetings' ? <AgentMeetingSessions onClose={() => setOverlay('none')} />
                  : <GuidedExecutiveTour site={currentBuilding?.name ?? 'ANUM MINE'} stops={tourStops} onClose={() => setOverlay('none')} onArrive={(id) => { const dest = floor?.rooms.find((r) => r.id === id); if (dest) openRoomById(dest.floorId, dest.id); }} />}
        </div>
      )}
    </section>
  );
}
