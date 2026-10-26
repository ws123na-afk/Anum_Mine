/**
 * The campus: one building per authorised workspace around the ANUM MINE monument. Picking,
 * label cards, camera, minimap and totals all read from the campus model; nothing is drawn for a
 * building the person cannot open, and no count appears without a presence record behind it.
 */
import { ArrowRight, Briefcase, Building2, ChartColumn, Factory, House, Landmark, LockKeyhole, ShieldCheck, Store, Truck, UserRound } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { BottomToolbar, CameraTools, Minimap, TotalsPanel, statusLabel, type HqView } from './HqChrome';
import { HqStage, useAspect, type StageLabel } from './HqStage';
import { buildCampusScene } from './lib/campusScene.ts';
import { layoutKey, planSize, type PlanInput } from './lib/hqPlanFrame.ts';
import type { GlassPlanData } from './lib/hqPlanAtlas.ts';
import type { PlannedSize } from './lib/campusModel.ts';
import {
  buildingStatus, campusTotals, layoutCampus, readingOrder, stepBuilding,
  type Campus, type CampusBuilding, type CampusBuildingKind, type StatusKey,
} from './lib/campusModel.ts';
import { campusCamera, campusFraming, clampTarget, faceNorth, rotateBy, threeDView, topView, viewFootprint, zoomBy } from './lib/hqCamera.ts';
import { dolly, type CameraState } from './lib/headquartersSpatial.ts';
import type { PickTarget } from './lib/hqMath.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { sceneName } from './lib/registryNames.ts';
import { useLocale } from './useLocale';
import { useArabicSignFont } from './lib/arabicFont.ts';
import type { HqNavHandles } from './hq-nav/HqNavigation';
import { useNavLayout } from './hq-nav/useNavLayout.ts';
import { cardDensities, cardTags, type CardDensity } from './hq-nav/navModel.ts';

const KIND_ICON: Record<CampusBuildingKind, ReactElement> = {
  executive: <Landmark aria-hidden="true" />, business: <Building2 aria-hidden="true" />,
  department: <Briefcase aria-hidden="true" />, personal: <UserRound aria-hidden="true" />,
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
  unreported: readonly StatusKey[];
  view: Exclude<HqView, 'list'>;
  night: boolean;
  reducedMotion: boolean;
  fullscreen: boolean;
  currentWorkspaceId: string;
  /** Initials of agents seen in the open workspace, for the avatar stack on its label. */
  avatars: ReadonlyArray<string>;
  /** Departments of the open workspace's agents, for the tags of its card. */
  departments?: ReadonlyArray<string>;
  /** The navigation layer's camera bridge and thumbnail capture (flights, snapshots). */
  nav?: HqNavHandles;
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
  const [kit, setKit] = useState<typeof import('./lib/hqPlanKit.ts') | null>(null);
  useEffect(() => {
    if (!sizes.size || kit) return;
    let live = true;
    void import('./lib/hqPlanKit.ts').then((m) => { if (live) setKit(m); }).catch(() => undefined);
    return () => { live = false; };
  }, [sizes, kit]);
  // Presence refreshes the buildings every few seconds; the placements (and so the meshes) only move when the layout does.
  const rawPlacements = useMemo(() => layoutCampus(campus.buildings, sizes), [campus.buildings, sizes]);
  const stablePlacements = useRef(rawPlacements);
  if (JSON.stringify(stablePlacements.current) !== JSON.stringify(rawPlacements)) stablePlacements.current = rawPlacements;
  const placements = stablePlacements.current;
  const scene = useMemo(() => {
    const p = plansRef.current;
    if (kit && p && sizes.size) return buildCampusScene(placements, { draw: kit.plannedDrawer(kit.plannedRows(placements, p), p) });
    return buildCampusScene(placements);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placements, kit, layoutSig]);
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
  const [wrap, aspect] = useAspect();
  const framing = campusFraming(scene.extent);
  const home = useMemo(() => campusCamera(framing.radius, framing.centerZ, aspect), [framing.radius, framing.centerZ, aspect]);
  const [camera, setCamera] = useState<CameraState>(home);
  const [hover, setHover] = useState<string | null>(null);
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

  const targets: PickTarget[] = useMemo(() => scene.buildings.map((b) => ({ id: b.workspaceId, box: b.box })), [scene]);
  const homeNow = useRef(home);
  homeNow.current = view === 'top' ? topView(home) : home;
  const boxes = useMemo(() => targets.map((x) => x.box), [targets]);
  // The controller reads the latest values through a ref, so it attaches once and a refresh of the campus never stops a flight.
  const controller = useRef({ boxes, frameOf, ground: scene.groundRadius });
  controller.current = { boxes, frameOf, ground: scene.groundRadius };
  useEffect(() => props.nav?.bridge.attach({
    get: () => cameraRef.current, set: (c) => setCamera(clampTarget(c, controller.current.ground * 0.8)), obstacles: () => controller.current.boxes,
    frame: (id) => controller.current.frameOf(id), home: () => homeNow.current,
  }), [props.nav]);
  // The lettering on the entrance signs is the real building name over the wordmark; the last cell is the gate.
  const renderScene = useMemo(() => ({
    opaque: [{ id: 'scenery', vertices: scene.scenery.vertices, bounds: scene.scenery.bounds, cull: false }, ...scene.buildings.map((b) => ({ id: b.workspaceId, vertices: b.mesh.vertices, bounds: b.mesh.bounds }))],
    glass: [],
    signs: [...scene.buildings.map((b) => ({ title: 'ANUM MINE', subtitle: sceneName(byId.get(b.workspaceId) ?? { name: '' }, locale), fontsReady }))],
    shadow: scene.shadow,
    halos: scene.halos,
    scenery: scene.motion,
  }), [scene, byId, locale, fontsReady]);

  // A phone-sized stage has room for two full cards and a handful of compact ones.
  const compact = useNavLayout().layout.compact;
  const density = useMemo(() => (compact ? cardDensities(campus.buildings, { selectedId, currentId: props.currentWorkspaceId }, 2, 6) : cardDensities(campus.buildings, { selectedId, currentId: props.currentWorkspaceId })), [campus.buildings, selectedId, props.currentWorkspaceId, compact]);
  const labels: StageLabel[] = useMemo(() => {
    const list: StageLabel[] = scene.buildings.map((b) => {
      const building = byId.get(b.workspaceId)!;
      return {
        id: b.workspaceId, anchor: b.anchor, priority: b.workspaceId === selectedId ? 0 : 1,
        node: (
          <BuildingCard
            building={building} selected={b.workspaceId === selectedId} current={b.workspaceId === props.currentWorkspaceId || building.registryBuildingId !== undefined}
            avatars={building.people ?? (b.workspaceId === props.currentWorkspaceId ? props.avatars : [])}
            density={density.get(b.workspaceId) ?? 'full'} departments={building.departments ?? (b.workspaceId === props.currentWorkspaceId ? props.departments ?? [] : [])}
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
  }, [scene, byId, selectedId, props.currentWorkspaceId, props.avatars, props.departments, density, activate]);

  const onView = useCallback((cam: CameraState, aspect: number) => {
    const now = performance.now();
    if (now - lastFootprint.current < 120) return;
    lastFootprint.current = now;
    setFootprint(viewFootprint(cam, aspect));
  }, []);

  const totals = useMemo(() => campusTotals(campus.buildings), [campus.buildings]);
  const selected = selectedId ? byId.get(selectedId) ?? null : null;
  const change = (fn: (c: CameraState) => CameraState) => setCamera((c) => clampTarget(fn(c), scene.groundRadius * 0.8));
  const step = (direction: 1 | -1) => { const next = stepBuilding(order, selectedId, direction); if (next) select(next); };

  return (
    <>
    <div className="hqSceneWrap" ref={wrap}>
      <HqStage
        label={t('hq.campus.stageLabel')} description={t('hq.campus.stageHelp')}
        scene={renderScene} glassPlan={glassPlan} pickTargets={targets} camera={camera} homeCamera={view === 'top' ? topView(home) : home}
        onCamera={(c) => setCamera(clampTarget(c, scene.groundRadius * 0.8))} night={night} reducedMotion={props.reducedMotion}
        hoverId={hover} selectedId={selectedId} onHover={setHover}
        onPick={(id) => { if (id) activate(id); else props.onSelect(null); }}
        labels={labels} onView={onView} onUnavailable={props.onUnavailable} onCanvasFrame={props.nav?.capture.onFrame}
      />
      <div className="hqOverlay hqOverlayRight">
        <CameraTools
          yaw={camera.yaw} night={night} onNight={props.onNight}
          onZoomIn={() => change((c) => zoomBy(c, 0.8))} onZoomOut={() => change((c) => zoomBy(c, 1.25))}
          onReset={() => setCamera(view === 'top' ? topView(home) : home)} onNorth={() => change(faceNorth)}
        />
      </div>
      <div className="hqOverlay hqOverlayBottomLeft">
        <TotalsPanel
          title={t('hq.totals.title')} totals={totals.totals} unreported={props.unreported}
          note={totals.totals && totals.missing > 0 ? t('hq.totals.partial', { reporting: totals.reporting, total: campus.buildings.length }) : null}
          verifiedAt={campus.buildings.find((b) => b.presence_verified_at)?.presence_verified_at ?? null}
        />
      </div>
      <div className="hqOverlay hqOverlayBottomRight">
        <Minimap
          placements={placements} extent={scene.extent.radius} selectedId={selectedId} footprint={footprint}
          onJump={(x, z) => change((c) => ({ ...c, target: { x, y: 0, z } }))} onSelect={(id) => select(id)}
          nameOf={(id) => { const found = byId.get(id); return found ? sceneName(found, locale) : id; }}
        />
      </div>
      <div className="hqOverlay hqOverlayBottom">
        <BottomToolbar
          onRotateLeft={() => change((c) => rotateBy(c, -20))} onRotateRight={() => change((c) => rotateBy(c, 20))}
          onPrev={() => step(-1)} onNext={() => step(1)} prevLabel={t('hq.campus.prev')} nextLabel={t('hq.campus.next')}
          onZoomIn={() => change((c) => dolly(c, -250))} onZoomOut={() => change((c) => dolly(c, 250))}
          fullscreen={props.fullscreen} onFullscreen={props.onToggleFullscreen}
        />
      </div>
      {selected && (
        <aside className="hqOverlay hqOverlayTopLeft hqPanel hqDetail" aria-label={t('hq.detail.label', { name: sceneName(selected, locale) })}>
          <BuildingDetail building={selected} current={selected.workspace_id === props.currentWorkspaceId || selected.registryBuildingId !== undefined} onEnter={() => props.onEnter(selected.workspace_id)} onClose={() => props.onSelect(null)} onSaveProfile={props.onSaveProfile} />
        </aside>
      )}
    </div>
      <div className="hqTotalsInline">
        <TotalsPanel
          title={t('hq.totals.title')} totals={totals.totals} unreported={props.unreported}
          note={totals.totals && totals.missing > 0 ? t('hq.totals.partial', { reporting: totals.reporting, total: campus.buildings.length }) : null}
          verifiedAt={campus.buildings.find((b) => b.presence_verified_at)?.presence_verified_at ?? null}
        />
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

function BuildingCard(props: {
  building: CampusBuilding; selected: boolean; current: boolean; avatars: readonly string[]; density: CardDensity; departments: readonly string[];
  onActivate: () => void; onHover: (on: boolean) => void;
}) {
  const b = props.building;
  const status = buildingStatus(b);
  const full = props.density === 'full';
  const tags = full ? cardTags(b, props.departments) : [];
  const tagText = (tag: (typeof tags)[number]): string => (tag.kind === 'type' ? t(`hq.kind.${tag.value}` as MessageKey) : tag.kind === 'workspace' ? String(tag.value) : tag.kind === 'floors' ? t('hq.building.floors', { count: Number(tag.value) }) : tag.kind === 'rooms' ? t('hqNav.card.rooms', { count: Number(tag.value) }) : String(tag.value));
  const summary = buildingSummary(b);
  return (
    <button
      type="button" className="hqLabelCard hqBuildingCard" data-selected={props.selected} data-status={status} data-kind={b.kind} data-density={props.density}
      aria-label={`${sceneName(b)}. ${summary}.${props.selected ? ' ' + t('hq.building.pressToOpen') : ''}`}
      aria-pressed={props.selected} title={props.density === 'dot' ? sceneName(b) : undefined}
      onClick={props.onActivate} onMouseEnter={() => props.onHover(true)} onMouseLeave={() => props.onHover(false)}
    >
      <span className="hqLabelIcon">{iconOf(b)}</span>
      {props.density !== 'dot' && (
        <span className="hqLabelText">
          <strong>{sceneName(b)}</strong>
          {tags.length > 0 && <span className="hqCardTags" aria-hidden="true">{tags.map((tag, i) => <span key={i}>{tagText(tag)}</span>)}</span>}
          <small>{summary}</small>
        </span>
      )}
      <span className="hqStatusDot" aria-hidden="true" />
      {full && props.avatars.length > 0 && (
        <span className="hqAvatarStack" aria-hidden="true">{props.avatars.slice(0, 4).map((a, i) => <span key={i}>{a}</span>)}</span>
      )}
      {!b.accessible && <LockKeyhole aria-hidden="true" className="hqLock" />}
    </button>
  );
}

function BuildingDetail(props: { building: CampusBuilding; current: boolean; onEnter: () => void; onClose: () => void; onSaveProfile: CampusViewProps['onSaveProfile'] }) {
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

const KINDS: readonly CampusBuildingKind[] = ['executive', 'business', 'department', 'personal'];

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
