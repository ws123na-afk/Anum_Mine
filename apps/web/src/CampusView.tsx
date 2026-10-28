/**
 * The campus: one building per authorised workspace around the ANUM MINE monument. Picking,
 * label cards, camera, minimap and totals all read from the campus model; nothing is drawn for a
 * building the person cannot open, and no count appears without a presence record behind it.
 */
import { ArrowRight, Briefcase, Building2, ChartColumn, Factory, GraduationCap, House, Landmark, LockKeyhole, Map as MapIcon, ShieldCheck, Store, Truck, UserRound } from 'lucide-react';
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactElement } from 'react';
import type { WorkSiteInput, WorkUi } from './HqCampusWork';
import type { ViewSink, WalkerCard, WalkerFeed, WalkerUi } from './HqCampusWalkers';
import type { CardWork, FeedNote } from './lib/hqWorkMotion.ts';
import './hqCampusMotion.css';
import { BottomToolbar, CameraTools, Minimap, statusLabel, type HqView } from './HqChrome';
import { HqStage, useAspect, type StageLabel } from './HqStage';
import { buildCampusScene } from './lib/campusScene.ts';
import { layoutKey, planSize, type PlanInput } from './lib/hqPlanFrame.ts';
import type { GlassPlanData } from './lib/hqPlanAtlas.ts';
import type { PlannedSize } from './lib/campusModel.ts';
import type { BillboardFlow } from './hq-billboards/billboardModel.ts';
import {
  buildingStatus, layoutCampus, readingOrder, stepBuilding, workspaceOf,
  type Campus, type CampusBuilding, type CampusBuildingKind, type StatusKey,
} from './lib/campusModel.ts';
import { campusCamera, campusFraming, clampTarget, faceNorth, rotateBy, threeDView, topView, viewFootprint, zoomBy } from './lib/hqCamera.ts';
import { cameraEye, dolly, type CameraState } from './lib/headquartersSpatial.ts';
import { getGpuState, subscribeGpuState } from './lib/hqGpuState.ts';
import { boardSpec } from './lib/hqQualityTiers.ts';
import type { PickTarget } from './lib/hqMath.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { sceneName } from './lib/registryNames.ts';
import { useLocale } from './useLocale';
import { useArabicSignFont } from './lib/arabicFont.ts';
import type { Logo as SignLogo } from './hq-logo-signs/campusSigns.ts';
import type { HqNavHandles } from './hq-nav/HqNavigation';
import { useNavLayout } from './hq-nav/useNavLayout.ts';
import { MAX_CHIPS, MAX_CHIPS_COMPACT, quietDensities, type CardDensity } from './hq-nav/navModel.ts';

// Record-backed work motion is its own chunk, fetched only for a campus that has buildings.
const NO_WORK: WorkUi = { plan: { motions: [], racks: [] }, cards: {}, feeds: {} };
const CampusWork = lazy(() => import('./HqCampusWork'));
// Agent walkers (docs/ANUM_MINE_HQ_AGENT_WALKERS.md) are their own chunk, fetched only for a workspace whose registry holds two or more buildings.
const NO_WALK: WalkerUi = { plan: { walkers: [] }, cards: {}, off: false, count: 0, waiting: 0 };
const CampusWalkers = lazy(() => import('./HqCampusWalkers'));

const NO_LOGOS: ReadonlyMap<string, SignLogo> = new Map();

const KIND_ICON: Record<CampusBuildingKind, ReactElement> = {
  executive: <Landmark aria-hidden="true" />, business: <Building2 aria-hidden="true" />,
  department: <Briefcase aria-hidden="true" />, personal: <UserRound aria-hidden="true" />,
  career: <GraduationCap aria-hidden="true" />,
};
/** The icon names of the campus contract (an owner picks one per building). */
export const BUILDING_ICONS: Record<string, ReactElement> = {
  building: <Building2 aria-hidden="true" />, briefcase: <Briefcase aria-hidden="true" />, store: <Store aria-hidden="true" />, truck: <Truck aria-hidden="true" />,
  home: <House aria-hidden="true" />, user: <UserRound aria-hidden="true" />, landmark: <Landmark aria-hidden="true" />, factory: <Factory aria-hidden="true" />,
  shield: <ShieldCheck aria-hidden="true" />, chart: <ChartColumn aria-hidden="true" />,
};
const iconOf = (b: CampusBuilding): ReactElement => (b.icon ? BUILDING_ICONS[b.icon] : undefined) ?? KIND_ICON[b.kind];

export type CampusViewProps = Readonly<{
  campus: Campus;
  /**
   * The real floor plans and occupancy of the registry buildings on the campus, by campus building id. A building listed here is
   * drawn from its plans (footprint, storeys, the rooms behind its glass); any other building keeps the generic look.
   */
  plans?: ReadonlyMap<string, PlanInput>;
  /** The live flow read of the open workspace (open work on the billboards); without it the billboards say "Not connected". */
  flow?: BillboardFlow | null;
  /** The presence stream is connected right now: only then do the 3D boards show their Live tick. */
  presenceLive?: boolean;
  unreported: readonly StatusKey[];
  view: Exclude<HqView, 'list'>;
  night: boolean;
  reducedMotion: boolean;
  fullscreen: boolean;
  currentWorkspaceId: string;
  /** The navigation layer's camera bridge and thumbnail capture (flights, snapshots). */
  nav?: HqNavHandles;
  /** What the walkers read: the registry, presence and meetings the headquarters already holds. Null when there is nothing to read. */
  walkerFeed?: WalkerFeed | null;
  selectedId: string | null;
  onSelect: (workspaceId: string | null) => void;
  onEnter: (workspaceId: string) => void;
  onToggleFullscreen: () => void;
  onUnavailable: (reason: string) => void;
  onNight: (night: boolean) => void;
  /** Saves the building type of the open workspace (owners). Resolves to an error message, or null when saved. */
  onSaveProfile: (kind: CampusBuildingKind, icon: string, revision: number | null) => Promise<string | null>;
}>;

export function CampusView(props: CampusViewProps) {
  const locale = useLocale();
  const fontsReady = useArabicSignFont(locale === 'ar');
  const { campus, view, night, selectedId } = props;
  // Floor plans: the scene (footprints, shells) follows the layout only; presence moves the glass atlas, never the meshes.
  const plans = props.plans;
  const plansRef = useRef(plans);
  plansRef.current = plans;
  const layoutSig = useMemo(() => (plans ? [...plans].map(([id, p]) => `${id}:${layoutKey(p.floors)}`).join('|') : ''), [plans]);
  const sizes = useMemo(() => {
    const map = new Map<string, PlannedSize>();
    for (const [id, p] of plansRef.current ?? []) { const size = planSize(p.floors); if (size) map.set(id, size); }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutSig]);
  // Presence refreshes the buildings every few seconds; the placements (and so the meshes) only move when the layout does.
  const rawPlacements = useMemo(() => layoutCampus(campus.buildings, sizes), [campus.buildings, sizes]);
  const stablePlacements = useRef(rawPlacements);
  if (JSON.stringify(stablePlacements.current) !== JSON.stringify(rawPlacements)) stablePlacements.current = rawPlacements;
  const placements = stablePlacements.current;
  // The plan kit (lazy) draws planned buildings from their floor plans and the career centre (owner decision O2).
  const hasCareer = placements.some((p) => p.kind === 'career');
  const [kit, setKit] = useState<typeof import('./lib/hqPlanKit.ts') | null>(null);
  useEffect(() => {
    if ((!sizes.size && !hasCareer) || kit) return;
    let live = true;
    void import('./lib/hqPlanKit.ts').then((m) => { if (live) setKit(m); }).catch(() => undefined);
    return () => { live = false; };
  }, [sizes, hasCareer, kit]);
  const scene = useMemo(() => {
    const p = plansRef.current;
    if (!kit) return buildCampusScene(placements);
    const planned = p && sizes.size ? kit.plannedDrawer(kit.plannedRows(placements, p), p) : null;
    const career = kit.careerDrawer();
    return buildCampusScene(placements, { draw: (pl, a, i, n) => planned?.(pl, a, i, n) ?? career(pl, a, i, n) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placements, kit, layoutSig]);
  // The billboards (docs/ANUM_MINE_HQ_BILLBOARDS.md) are a lazy chunk of their own, fetched once a campus has buildings.
  const [bb, setBb] = useState<typeof import('./hq-billboards/index.ts') | null>(null);
  useEffect(() => {
    if (bb || !campus.buildings.length) return;
    let live = true;
    void import('./hq-billboards/index.ts').then((m) => { if (live) setBb(m); }).catch(() => undefined);
    return () => { live = false; };
  }, [bb, campus.buildings.length]);
  const boards = useMemo(() => (bb ? bb.billboardLayer(scene, placements) : null), [bb, scene, placements]);
  // The brand signs (docs/ANUM_MINE_HQ_LOGO_SIGNS.md): a lazy chunk, fetched only when a building has a sign set up. Its logos are small decoded
  // bitmaps drawn into the existing sign atlas, loaded for the nearest buildings first; a logo that fails leaves the lettered sign.
  const [signKit, setSignKit] = useState<typeof import('./hq-logo-signs/campusSigns.ts') | null>(null);
  const anySign = campus.buildings.some((b) => b.sign);
  useEffect(() => {
    if (signKit || !anySign) return undefined;
    let live = true;
    void import('./hq-logo-signs/campusSigns.ts').then((m) => { if (live) setSignKit(m); }).catch(() => undefined);
    return () => { live = false; };
  }, [signKit, anySign]);
  const [logos, setLogos] = useState<ReadonlyMap<string, SignLogo>>(NO_LOGOS);
  const logosRef = useRef(logos);
  logosRef.current = logos;
  const logoSig = campus.buildings.map((b) => (b.sign?.logoSha256 ? `${b.sign.buildingId}:${b.sign.logoSha256}` : '')).join('|');
  useEffect(() => {
    if (!signKit) return undefined;
    const controller = new AbortController();
    const where = new Map(placements.map((p) => [p.workspaceId, p]));
    const focus = cameraRef.current.target;
    const targets = signKit.pickLogoTargets(campus.buildings.flatMap((b) => (b.sign?.logoSha256 ? [{ id: b.workspace_id, buildingId: b.sign.buildingId, sha: b.sign.logoSha256, x: where.get(b.workspace_id)?.x ?? 0, z: where.get(b.workspace_id)?.z ?? 0 }] : [])), { x: focus.x, z: focus.z });
    void signKit.loadLogos(targets, logosRef.current, controller.signal).then((next) => { if (!controller.signal.aborted) setLogos(next); }).catch(() => undefined);
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signKit, logoSig, selectedId]);
  useEffect(() => () => { for (const logo of logosRef.current.values()) logo.image.close(); }, []);
  const signStats = useMemo(() => (signKit ? signKit.signStats(campus.buildings, logos) : { branded: campus.buildings.filter((b) => b.sign).length, logos: 0 }), [signKit, campus.buildings, logos]);
  const atlasCache = useRef<GlassPlanData | null>(null);
  const glassPlan = useMemo(() => {
    if (!kit || !plans || !sizes.size) { atlasCache.current = null; return null; }
    const next = kit.planAtlas(placements, plans, kit.plannedRows(placements, plans));
    if (next && atlasCache.current?.revision === next.revision) return atlasCache.current;
    atlasCache.current = next;
    return next;
  }, [kit, plans, placements, sizes]);
  const byId = useMemo(() => new Map(campus.buildings.map((b) => [b.workspace_id, b])), [campus.buildings]);
  const order = useMemo(() => readingOrder(campus.buildings).map((b) => b.workspace_id), [campus.buildings]);
  const byIdRef = useRef(byId);
  byIdRef.current = byId;
  const [wrap, aspect] = useAspect();
  const framing = campusFraming(scene.extent);
  const home = useMemo(() => campusCamera(framing.radius, framing.centerZ, aspect), [framing.radius, framing.centerZ, aspect]);
  const [camera, setCamera] = useState<CameraState>(home);
  const [hover, setHover] = useState<string | null>(null);
  const [work, setWork] = useState<WorkUi>(NO_WORK);
  const workSites: readonly WorkSiteInput[] = useMemo(() => scene.buildings.map((b) => ({
    key: b.workspaceId, workspaceId: workspaceOf(byIdRef.current.get(b.workspaceId) ?? { workspace_id: b.workspaceId }), workshop: b.archetype === 'workshop', door: { x: b.door.x, z: b.door.z },
    box: { minX: b.box.min.x, minZ: b.box.min.z, maxX: b.box.max.x, maxZ: b.box.max.z },
  })), [scene]);
  const [walk, setWalk] = useState<WalkerUi>(NO_WALK);
  const viewSink: ViewSink = useRef(null);
  const walkSites = useMemo(() => scene.buildings.map((b) => ({ key: b.workspaceId, door: { x: b.door.x, z: b.door.z }, box: { minX: b.box.min.x, minZ: b.box.min.z, maxX: b.box.max.x, maxZ: b.box.max.z } })), [scene]);
  const [footprint, setFootprint] = useState<ReadonlyArray<{ x: number; z: number }> | null>(null);
  const lastFootprint = useRef(0);
  const homeRef = useRef(home);

  // A new set of buildings reframes the camera; the 3D/Top switch keeps the framing.
  useEffect(() => {
    if (homeRef.current === home) return;
    homeRef.current = home;
    setCamera(view === 'top' ? topView(home) : home);
  }, [home, view]);
  useEffect(() => { setCamera((c) => (view === 'top' ? topView(c) : threeDView(c))); }, [view]);

  const latest = useRef(props);
  latest.current = props;
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  // The 3D boards carry the live details (docs/ANUM_MINE_HQ_BILLBOARDS.md): a canvas atlas drawn in place, only for boards whose text changed.
  const [atlas, setAtlas] = useState<{ canvas: HTMLCanvasElement | null; version: number; boards: unknown; lines: string; stats: string } | null>(null);
  const feed = useRef<{ touch: () => void } | null>(null);
  const readFaces = useRef<() => ReturnType<typeof import('./hq-billboards/index.ts').boardFaces>>(() => []);
  // The lowest quality tiers draw smaller board cells (key line only); the close view (detail lines) is for the focused building and boards near the eye.
  const boardCell = boardSpec(useSyncExternalStore(subscribeGpuState, getGpuState, getGpuState)).cell;
  const closeKey = useMemo(() => (bb && boards ? bb.closeBoards(boards, cameraEye(camera)).join('|') : ''), [bb, boards, camera]);
  const detailIds = useMemo(() => new Set([selectedId, hover, ...closeKey.split('|')].filter((id): id is string => !!id)), [selectedId, hover, closeKey]);
  readFaces.current = () => (boards && bb ? bb.boardFaces({
    layer: boards, buildings: byId, plans: plansRef.current, flow: latest.current.flow, currentWorkspaceId: latest.current.currentWorkspaceId, live: latest.current.presenceLive === true, fontsReady, now: Date.now(),
    detailIds, cell: boardCell, logoReady: (id, sha) => logosRef.current.has(`${id}:${sha}`),
  }) : []);
  useEffect(() => {
    if (!bb || !boards) return undefined;
    const started = bb.startBoardFeed({
      count: boards.sites.length, cell: boardCell, read: () => readFaces.current(), logoOf: (key) => logosRef.current.get(key) ?? null,
      onDraw: (a) => setAtlas({ canvas: a.canvas, version: a.version, boards, lines: bb.boardLines(a.drawn), stats: bb.boardStats(a.drawn) }),
    });
    feed.current = started;
    return () => { started.stop(); feed.current = null; };
  }, [bb, boards, boardCell]);
  useEffect(() => { feed.current?.touch(); }, [campus.buildings, props.flow, plans, props.presenceLive, locale, fontsReady, props.currentWorkspaceId, detailIds, logos]);
  // The camera that frames one building: its centre, at a distance that fits its size.
  const frameOf = useCallback((id: string): CameraState | null => {
    const p = placements.find((x) => x.workspaceId === id);
    if (!p) return null;
    const c = cameraRef.current;
    return { ...c, target: { x: p.x, y: 0, z: p.z }, distance: Math.max(c.minDistance, Math.min(c.maxDistance, Math.max(p.width, p.depth) * 3.6 + p.height * 0.8)) };
  }, [placements]);
  const focus = useCallback((id: string) => {
    const next = frameOf(id);
    if (!next) return;
    if (latest.current.nav && !latest.current.reducedMotion) latest.current.nav.bridge.flyTo(next);
    else setCamera(next);
  }, [frameOf]);

  const select = useCallback((id: string | null) => {
    latest.current.onSelect(id);
    if (id) focus(id);
  }, [focus]);

  const activate = useCallback((id: string) => {
    if (id === latest.current.selectedId) latest.current.onEnter(id);
    else select(id);
  }, [select]);

  const buildingTargets: PickTarget[] = useMemo(() => scene.buildings.map((b) => ({ id: b.workspaceId, box: b.box })), [scene]);
  const targets: PickTarget[] = useMemo(() => (boards ? [...buildingTargets, ...boards.picks] : buildingTargets), [buildingTargets, boards]);
  const homeNow = useRef(home);
  homeNow.current = view === 'top' ? topView(home) : home;
  const boxes = useMemo(() => buildingTargets.map((x) => x.box), [buildingTargets]);
  // The controller reads the latest values through a ref, so it attaches once and a refresh of the campus never stops a flight.
  const controller = useRef({ boxes, frameOf, ground: scene.groundRadius });
  controller.current = { boxes, frameOf, ground: scene.groundRadius };
  useEffect(() => props.nav?.bridge.attach({
    get: () => cameraRef.current, set: (c) => setCamera(clampTarget(c, controller.current.ground * 0.8)), obstacles: () => controller.current.boxes,
    frame: (id) => controller.current.frameOf(id), home: () => homeNow.current,
  }), [props.nav]);
  // The lettering on the entrance signs is the real building name over the wordmark; the last cell is the gate.
  const renderScene = useMemo(() => ({
    opaque: [{ id: 'scenery', vertices: scene.scenery.vertices, bounds: scene.scenery.bounds, cull: false }, ...scene.buildings.map((b) => ({ id: b.workspaceId, vertices: b.mesh.vertices, bounds: b.mesh.bounds })),
      ...(boards && atlas?.boards === boards ? [{ id: 'billboards', vertices: boards.mesh.vertices, bounds: boards.mesh.bounds, board: true }] : [])],
    glass: [],
    signs: [...scene.buildings.map((b) => {
      const building = byId.get(b.workspaceId);
      const subtitle = sceneName(building ?? { name: '' }, locale);
      return { title: 'ANUM MINE', subtitle, fontsReady, ...(signKit && building?.sign ? signKit.signFor(building.sign, subtitle, locale === 'ar', logos) : {}) };
    })],
    shadow: scene.shadow,
    halos: boards ? new Float32Array([...scene.halos, ...boards.mesh.halos]) : scene.halos,
    scenery: scene.motion,
  }), [scene, boards, atlas?.boards, byId, locale, fontsReady, signKit, logos]);

  // A phone-sized stage has room for two full cards and a handful of compact ones.
  const compact = useNavLayout().layout.compact;
  // The quiet campus: a name chip for a few buildings (the selected, the open one, the nearest to where the camera looks), a status dot for the rest.
  const at = useMemo(() => new Map(placements.map((p) => [p.workspaceId, { x: p.x, z: p.z }])), [placements]);
  const chipSet = quietDensities(campus.buildings, { selectedId, currentId: props.currentWorkspaceId, target: camera.target, at, max: compact ? MAX_CHIPS_COMPACT : MAX_CHIPS });
  const chipKey = [...chipSet].map(([id, d]) => `${id}:${d}`).join('|');
  const density = useMemo(() => chipSet, [chipKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const labels: StageLabel[] = useMemo(() => {
    const list: StageLabel[] = scene.buildings.map((b) => {
      const building = byId.get(b.workspaceId)!;
      return {
        id: b.workspaceId, anchor: b.anchor, priority: b.workspaceId === selectedId ? 0 : 1,
        // A crowded stage gives a building chip less (the chip, then a dot) before two would cover each other; it is never left out.
        // A building that carries a work reason or a feed note is never reduced to a bare dot: the reason must stay readable.
        tiers: work.cards[b.workspaceId] || work.feeds[b.workspaceId] || walk.cards[b.workspaceId] || (walk.off && b.workspaceId === props.currentWorkspaceId) ? ['compact'] : density.get(b.workspaceId) === 'dot' ? ['dot'] : ['compact', 'dot'], keep: true,
        node: (
          <BuildingCard
            building={building} selected={b.workspaceId === selectedId}
            density={density.get(b.workspaceId) ?? 'dot'}
            work={work.cards[b.workspaceId]} feed={work.feeds[b.workspaceId]}
            walk={walk.cards[b.workspaceId]} walkOff={walk.off && b.workspaceId === props.currentWorkspaceId}
            onActivate={() => activate(b.workspaceId)} onHover={(on) => setHover(on ? b.workspaceId : null)}
          />
        ),
      };
    });
    list.push({
      id: 'monument', anchor: scene.monument, priority: 2,
      node: <div className="hqMonumentLabel" aria-hidden="true"><strong>ANUM MINE</strong><small>{t('hq.monument.sub')}</small></div>,
    });
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene, byId, selectedId, props.currentWorkspaceId, density, activate, work.cards, work.feeds, walk.cards, walk.off]);
  // The billboards are a separate pass (HqStage `extraLabels`): they can never move, hide or park a building card.
  const extraLabels: StageLabel[] = useMemo(() => (boards && bb ? bb.billboardLabels({
    layer: boards, buildings: byId, plans, flow: props.flow, selectedId, hoverId: hover, near: false, now: Date.now(),
    onActivate: activate, onOpen: (id) => latest.current.onEnter(id), onHover: setHover,
  }) : []), [boards, bb, byId, plans, props.flow, selectedId, hover, locale, activate]);

  const onView = useCallback((cam: CameraState, aspect: number) => {
    viewSink.current?.(cam, aspect);
    const now = performance.now();
    if (now - lastFootprint.current < 120) return;
    lastFootprint.current = now;
    setFootprint(viewFootprint(cam, aspect));
  }, []);

  // The campus map is a button in the corner rail: closed by default (it never sits over the buildings), and the choice is kept per browser.
  const [mapOpen, setMapOpen] = useState(() => { try { return window.localStorage.getItem('anum.hq.nav.map') === 'open'; } catch { return false; } });
  const toggleMap = () => setMapOpen((v) => { try { window.localStorage.setItem('anum.hq.nav.map', v ? 'closed' : 'open'); } catch { /* remembered for this page only */ } return !v; });
  const change = (fn: (c: CameraState) => CameraState) => setCamera((c) => clampTarget(fn(c), scene.groundRadius * 0.8));
  const step = (direction: 1 | -1) => { const next = stepBuilding(order, selectedId, direction); if (next) select(next); };

  return (
    <>
    <div className="hqSceneWrap" ref={wrap} data-billboards={boards ? boards.sites.length : 0} data-sign-brands={signStats.branded} data-sign-logos={signStats.logos} data-board-version={atlas?.version ?? 0} data-board-texts={atlas?.lines ?? ''} data-board-brands={atlas?.stats.split('|')[0] ?? 0} data-board-logos={atlas?.stats.split('|')[1] ?? 0}>
      <HqStage
        label={t('hq.campus.stageLabel')} description={t('hq.campus.stageHelp')}
        scene={renderScene} glassPlan={glassPlan} work={work.plan} walkers={walk.plan} pickTargets={targets} camera={camera} homeCamera={view === 'top' ? topView(home) : home}
        onCamera={(c) => setCamera(clampTarget(c, scene.groundRadius * 0.8))} night={night} reducedMotion={props.reducedMotion}
        hoverId={hover} selectedId={selectedId} onHover={setHover}
        onPick={(id) => { if (id) activate(id); else props.onSelect(null); }}
        boardAtlas={atlas && atlas.boards === boards ? atlas : null} labels={labels} labelKeepOut="[data-keepout]" extraLabels={extraLabels} extraKeepOut=".hqDetail, .hqOverlayRight, .hqOverlayMap, .hqOverlayBottom > *" onView={onView} onUnavailable={props.onUnavailable} onCanvasFrame={props.nav?.capture.onFrame}
      />
      <div className="hqSrOnly" role="status" aria-live="polite" data-testid="hq-work-live">
        {Object.entries(work.cards).map(([id, c]) => <p key={id}>{sceneName(byId.get(id) ?? { name: id }, locale)}: {c.because}</p>)}
        {Object.entries(work.feeds).filter(([, f]) => f.reason !== 'elsewhere').map(([id]) => <p key={id}>{sceneName(byId.get(id) ?? { name: id }, locale)}: {t('hqMotion.feed.off')}</p>)}
      </div>
      {scene.buildings.length > 0 && (
        <Suspense fallback={null}>
          <CampusWork campus={campus} sites={workSites} lanes={scene.motion.lanes} flow={props.flow?.snapshot ?? null} openKey={props.currentWorkspaceId} reducedMotion={props.reducedMotion} onChange={setWork} />
        </Suspense>
      )}
      {props.walkerFeed && scene.buildings.length > 1 && (
        <Suspense fallback={null}>
          <CampusWalkers feed={props.walkerFeed} sites={walkSites} spokes={scene.plaza.spokes} reducedMotion={props.reducedMotion} viewSink={viewSink} onChange={setWalk} />
        </Suspense>
      )}
      <div className="hqOverlay hqOverlayRight" data-keepout="">
        <CameraTools
          yaw={camera.yaw} night={night} onNight={props.onNight}
          onZoomIn={() => change((c) => zoomBy(c, 0.8))} onZoomOut={() => change((c) => zoomBy(c, 1.25))}
          onReset={() => setCamera(view === 'top' ? topView(home) : home)} onNorth={() => change(faceNorth)}
          extra={<button type="button" onClick={toggleMap} aria-pressed={mapOpen} aria-controls={mapOpen ? 'hq-campus-map' : undefined} data-testid="hq-map-toggle" aria-label={mapOpen ? t('hqPanels.map.hide') : t('hqPanels.map.show')} title={mapOpen ? t('hqPanels.map.hide') : t('hqPanels.map.show')}><MapIcon aria-hidden="true" /></button>}
        />
      </div>
      {mapOpen && (
        <div className="hqOverlay hqOverlayMap" id="hq-campus-map" data-keepout="">
          <Minimap
            placements={placements} extent={scene.extent.radius} selectedId={selectedId} footprint={footprint}
            onJump={(x, z) => change((c) => ({ ...c, target: { x, y: 0, z } }))} onSelect={(id) => select(id)}
            nameOf={(id) => { const found = byId.get(id); return found ? sceneName(found, locale) : id; }}
          />
        </div>
      )}
      <div className="hqOverlay hqOverlayBottom">
        <BottomToolbar
          onRotateLeft={() => change((c) => rotateBy(c, -20))} onRotateRight={() => change((c) => rotateBy(c, 20))}
          onPrev={() => step(-1)} onNext={() => step(1)} prevLabel={t('hq.campus.prev')} nextLabel={t('hq.campus.next')}
          onZoomIn={() => change((c) => dolly(c, -250))} onZoomOut={() => change((c) => dolly(c, 250))}
          fullscreen={props.fullscreen} onFullscreen={props.onToggleFullscreen}
        />
      </div>
    </div>
    </>
  );
}

export function buildingSummary(b: CampusBuilding): string {
  if (!b.totals) return t('hq.status.unavailable');
  if (b.totals.agents === 0) return t('hq.status.empty');
  const parts = [t('hq.building.agents', { count: b.totals.agents }), t('hq.building.working', { count: b.totals.working })];
  if (b.totals.meeting > 0) parts.push(t('hq.building.inMeeting', { count: b.totals.meeting }));
  return parts.join(', ');
}

/**
 * The page label of a building on the quiet campus: a small button with the name and a status dot (a chip), or only the status dot. The details
 * (what it does, counts, verified time) are on the building's 3D board and, for the focused building, its billboard card and the detail panel.
 * The accessible name is unchanged: the name and the presence summary, whatever the density.
 */
function BuildingCard(props: { building: CampusBuilding; selected: boolean; density: CardDensity; work?: CardWork; feed?: FeedNote; walk?: WalkerCard; walkOff?: boolean; onActivate: () => void; onHover: (on: boolean) => void }) {
  const b = props.building;
  const status = buildingStatus(b);
  const summary = buildingSummary(b);
  // A card with a work reason or a feed note is never reduced to a bare dot: the reason (a real record, a fixed template) must stay readable.
  const density: CardDensity = props.density === 'dot' && (props.work || props.feed || props.walk || props.walkOff) ? 'compact' : props.density;
  const feedHint = props.feed ? t(`hqMotion.feed.offHint.${props.feed.reason}` as MessageKey) : '';
  const workHint = props.work ? `${props.work.because}. ${t('hqMotion.legend')}` : '';
  return (
    <button
      type="button" className="hqLabelCard hqBuildingCard" data-selected={props.selected} data-status={status} data-kind={b.kind} data-density={density} data-walk={props.walk?.kind}
      aria-label={`${sceneName(b)}. ${summary}.${props.walk ? ' ' + props.walk.because + '.' : ''}${props.walkOff ? ' ' + t('hqWalkers.status.off') + '.' : ''}${props.work ? ' ' + props.work.because + '.' : ''}${props.feed ? ' ' + t('hqMotion.feed.off') + '.' : ''}${props.selected ? ' ' + t('hq.building.pressToOpen') : ''}`}
      aria-pressed={props.selected} title={density === 'dot' ? [sceneName(b), props.work?.because, props.feed ? t('hqMotion.feed.off') : ''].filter(Boolean).join('. ') : (workHint || feedHint || sceneName(b))} data-work={props.work?.kind} data-feed={props.feed?.reason}
      onClick={props.onActivate} onMouseEnter={() => props.onHover(true)} onMouseLeave={() => props.onHover(false)}
    >
      <span className="hqLabelIcon">{iconOf(b)}</span>
      <span className="hqLabelText">
        <strong>{sceneName(b)}</strong>
        {props.work && <small className="hqWorkLine" data-work={props.work.kind}>{props.work.because}</small>}
        {props.feed && <small className="hqWorkLine" data-feed={props.feed.reason}>{t('hqMotion.feed.off')}</small>}
        {props.walk && <small className="hqWalkLine" data-walk={props.walk.kind}>{props.walk.because}</small>}
        {props.walkOff && <small className="hqWalkLine" data-walkers-off="">{t('hqWalkers.status.off')}</small>}
      </span>
      <span className="hqStatusDot" aria-hidden="true" />
      {!b.accessible && <LockKeyhole aria-hidden="true" className="hqLock" />}
    </button>
  );
}

/** The selected building's panel. The page docks it in the side column (or under the scene); it never sits over the buildings. */
export function BuildingDetail(props: { building: CampusBuilding; current: boolean; onEnter: () => void; onClose: () => void; onSaveProfile: CampusViewProps['onSaveProfile'] }) {
  const b = props.building;
  const status = buildingStatus(b);
  return (
    <>
      <div className="hqPanelHead">
        <h2>{sceneName(b)}</h2>
        <button type="button" className="hqIconButton" onClick={props.onClose} aria-label={t('hq.detail.close')}>×</button>
      </div>
      <p className="hqMuted">{b.registryBuildingId ? (b.workspaceName ?? '') : b.kindKnown === false ? t('hq.kind.unset') : t(`hq.kind.${b.kind}` as MessageKey)}{b.floors ? ` · ${t('hq.building.floors', { count: b.floors })}` : ''}{b.archived ? ` · ${t('hq.detail.archived')}` : ''}</p>
      <p className="hqDetailStatus" data-status={status}><span className="hqStatusDot" aria-hidden="true" />{buildingSummary(b)}</p>
      {b.totals && b.totals.agents > 0 && (
        <ul className="hqChips">{(['working', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'] as const).filter((k) => b.totals![k] > 0).map((k) => <li key={k}>{statusLabel(k)} {b.totals![k]}</li>)}</ul>
      )}
      {props.current && b.role === 'owner' && !b.archived && !b.registryBuildingId && <BuildingProfileForm key={`${b.workspace_id}:${b.profileRevision ?? 'new'}`} building={b} onSave={props.onSaveProfile} />}
      {b.kindKnown === false && !b.registryBuildingId && !(props.current && b.role === 'owner') && <p className="hqNote">{t('hq.profile.ownerOnly')}</p>}
      {!b.accessible
        ? <p className="hqNote">{t('hq.detail.notAccessible')}</p>
        : <>
          {!props.current && <p className="hqNote">{t('hq.detail.switchNote')}</p>}
          <button type="button" className="hqPrimary" onClick={props.onEnter}>{t('hq.building.enter', { name: sceneName(b) })}<ArrowRight aria-hidden="true" className="flip" /></button>
        </>}
    </>
  );
}

const KINDS: readonly CampusBuildingKind[] = ['executive', 'business', 'department', 'personal', 'career'];

/** The owner's building type and icon (PUT /api/v1/office/campus/profile). It changes how the building is drawn and nothing else. */
function BuildingProfileForm(props: { building: CampusBuilding; onSave: CampusViewProps['onSaveProfile'] }) {
  const b = props.building;
  const [kind, setKind] = useState<CampusBuildingKind>(b.kindKnown === false ? 'business' : b.kind);
  const [icon, setIcon] = useState(b.icon ?? 'building');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const error = await props.onSave(kind, icon, b.profileRevision ?? null);
    setBusy(false);
    setMessage(error ? { error: true, text: error } : { error: false, text: t('hq.profile.saved') });
  };
  return (
    <form className="hqProfile" onSubmit={(e) => void submit(e)} aria-label={t('hq.profile.title')}>
      <h3>{t('hq.profile.title')}</h3>
      <label>{t('hq.profile.kind')}
        <select value={kind} onChange={(e) => setKind(e.target.value as CampusBuildingKind)}>
          {KINDS.map((k) => <option key={k} value={k}>{t(`hq.kind.${k}` as MessageKey)}</option>)}
        </select>
      </label>
      <label>{t('hq.profile.icon')}
        <select value={icon} onChange={(e) => setIcon(e.target.value)}>
          {Object.keys(BUILDING_ICONS).map((k) => <option key={k} value={k}>{t(`hq.icon.${k}` as MessageKey)}</option>)}
        </select>
      </label>
      <button type="submit" className="hqGhost" disabled={busy}>{busy ? t('hq.profile.saving') : t('hq.profile.save')}</button>
      {message && <p className="hqNote" role={message.error ? 'alert' : 'status'} data-error={message.error}>{message.text}</p>}
    </form>
  );
}
