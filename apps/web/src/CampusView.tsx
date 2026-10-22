/**
 * The campus: one building per authorised workspace around the ANUM MINE monument. Picking,
 * label cards, camera, minimap and totals all read from the campus model; nothing is drawn for a
 * building the person cannot open, and no count appears without a presence record behind it.
 */
import { ArrowRight, Briefcase, Building2, Landmark, LockKeyhole, UserRound } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from 'react';
import { BottomToolbar, CameraTools, Minimap, TotalsPanel, statusLabel, type HqView } from './HqChrome';
import { HqStage, useAspect, type StageLabel } from './HqStage';
import { buildCampusScene } from './lib/campusScene.ts';
import {
  buildingStatus, campusTotals, layoutCampus, readingOrder, stepBuilding,
  type Campus, type CampusBuilding, type CampusBuildingKind, type StatusKey,
} from './lib/campusModel.ts';
import { campusCamera, clampTarget, faceNorth, rotateBy, threeDView, topView, viewFootprint, zoomBy } from './lib/hqCamera.ts';
import { dolly, type CameraState } from './lib/headquartersSpatial.ts';
import type { PickTarget } from './lib/hqMath.ts';
import { t, type MessageKey } from './lib/i18n.ts';

const KIND_ICON: Record<CampusBuildingKind, ReactElement> = {
  executive: <Landmark aria-hidden="true" />, business: <Building2 aria-hidden="true" />,
  department: <Briefcase aria-hidden="true" />, personal: <UserRound aria-hidden="true" />,
};

export type CampusViewProps = Readonly<{
  campus: Campus;
  unreported: readonly StatusKey[];
  view: Exclude<HqView, 'list'>;
  night: boolean;
  reducedMotion: boolean;
  fullscreen: boolean;
  currentWorkspaceId: string;
  /** Initials of agents seen in the open workspace, for the avatar stack on its label. */
  avatars: ReadonlyArray<string>;
  selectedId: string | null;
  onSelect: (workspaceId: string | null) => void;
  onEnter: (workspaceId: string) => void;
  onToggleFullscreen: () => void;
  onUnavailable: (reason: string) => void;
  onNight: (night: boolean) => void;
}>;

export function CampusView(props: CampusViewProps) {
  const { campus, view, night, selectedId } = props;
  const placements = useMemo(() => layoutCampus(campus.buildings), [campus.buildings]);
  const scene = useMemo(() => buildCampusScene(placements), [placements]);
  const byId = useMemo(() => new Map(campus.buildings.map((b) => [b.workspace_id, b])), [campus.buildings]);
  const order = useMemo(() => readingOrder(campus.buildings).map((b) => b.workspace_id), [campus.buildings]);
  const [wrap, aspect] = useAspect();
  const home = useMemo(() => campusCamera(scene.extent.radius, 0, aspect), [scene.extent.radius, aspect]);
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

  const focus = useCallback((id: string) => {
    const p = placements.find((x) => x.workspaceId === id);
    if (!p) return;
    setCamera((c) => ({ ...c, target: { x: p.x, y: 0, z: p.z }, distance: Math.max(c.minDistance, Math.min(c.maxDistance, Math.max(p.width, p.depth) * 3.6 + p.height * 0.8)) }));
  }, [placements]);

  const latest = useRef(props);
  latest.current = props;
  const select = useCallback((id: string | null) => {
    latest.current.onSelect(id);
    if (id) focus(id);
  }, [focus]);

  const activate = useCallback((id: string) => {
    if (id === latest.current.selectedId) latest.current.onEnter(id);
    else select(id);
  }, [select]);

  const targets: PickTarget[] = useMemo(() => scene.buildings.map((b) => ({ id: b.workspaceId, box: b.box })), [scene]);
  const renderScene = useMemo(() => ({
    opaque: [{ id: 'scenery', vertices: scene.scenery.vertices, bounds: scene.scenery.bounds, cull: false }, ...scene.buildings.map((b) => ({ id: b.workspaceId, vertices: b.mesh.vertices, bounds: b.mesh.bounds }))],
    glass: [],
  }), [scene]);

  const labels: StageLabel[] = useMemo(() => {
    const list: StageLabel[] = scene.buildings.map((b) => {
      const building = byId.get(b.workspaceId)!;
      return {
        id: b.workspaceId, anchor: b.anchor, priority: b.workspaceId === selectedId ? 0 : 1,
        node: (
          <BuildingCard
            building={building} selected={b.workspaceId === selectedId} current={b.workspaceId === props.currentWorkspaceId}
            avatars={b.workspaceId === props.currentWorkspaceId ? props.avatars : []}
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
  }, [scene, byId, selectedId, props.currentWorkspaceId, props.avatars, activate]);

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
    <div className="hqSceneWrap" ref={wrap}>
      <HqStage
        label={t('hq.campus.stageLabel')} description={t('hq.campus.stageHelp')}
        scene={renderScene} pickTargets={targets} camera={camera} homeCamera={view === 'top' ? topView(home) : home}
        onCamera={(c) => setCamera(clampTarget(c, scene.groundRadius * 0.8))} night={night} reducedMotion={props.reducedMotion}
        hoverId={hover} selectedId={selectedId} onHover={setHover}
        onPick={(id) => { if (id) activate(id); else props.onSelect(null); }}
        labels={labels} onView={onView} onUnavailable={props.onUnavailable}
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
          nameOf={(id) => byId.get(id)?.name ?? id}
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
        <aside className="hqOverlay hqOverlayTopLeft hqPanel hqDetail" aria-label={t('hq.detail.label', { name: selected.name })}>
          <BuildingDetail building={selected} current={selected.workspace_id === props.currentWorkspaceId} onEnter={() => props.onEnter(selected.workspace_id)} onClose={() => props.onSelect(null)} />
        </aside>
      )}
    </div>
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
  building: CampusBuilding; selected: boolean; current: boolean; avatars: readonly string[];
  onActivate: () => void; onHover: (on: boolean) => void;
}) {
  const b = props.building;
  const status = buildingStatus(b);
  return (
    <button
      type="button" className="hqLabelCard hqBuildingCard" data-selected={props.selected} data-status={status} data-kind={b.kind}
      aria-label={`${b.name}. ${buildingSummary(b)}.${props.selected ? ' ' + t('hq.building.pressToOpen') : ''}`}
      aria-pressed={props.selected}
      onClick={props.onActivate} onMouseEnter={() => props.onHover(true)} onMouseLeave={() => props.onHover(false)}
    >
      <span className="hqLabelIcon">{KIND_ICON[b.kind]}</span>
      <span className="hqLabelText">
        <strong>{b.name}</strong>
        <small>{buildingSummary(b)}</small>
      </span>
      <span className="hqStatusDot" aria-hidden="true" />
      {props.avatars.length > 0 && (
        <span className="hqAvatarStack" aria-hidden="true">{props.avatars.slice(0, 4).map((a, i) => <span key={i}>{a}</span>)}</span>
      )}
      {!b.accessible && <LockKeyhole aria-hidden="true" className="hqLock" />}
    </button>
  );
}

function BuildingDetail(props: { building: CampusBuilding; current: boolean; onEnter: () => void; onClose: () => void }) {
  const b = props.building;
  const status = buildingStatus(b);
  return (
    <>
      <div className="hqPanelHead">
        <h2>{b.name}</h2>
        <button type="button" className="hqIconButton" onClick={props.onClose} aria-label={t('hq.detail.close')}>×</button>
      </div>
      <p className="hqMuted">{t(`hq.kind.${b.kind}` as MessageKey)}{b.floors ? ` · ${t('hq.building.floors', { count: b.floors })}` : ''}</p>
      <p className="hqDetailStatus" data-status={status}><span className="hqStatusDot" aria-hidden="true" />{buildingSummary(b)}</p>
      {b.totals && b.totals.agents > 0 && (
        <ul className="hqChips">{(['working', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'] as const).filter((k) => b.totals![k] > 0).map((k) => <li key={k}>{statusLabel(k)} {b.totals![k]}</li>)}</ul>
      )}
      {!b.accessible
        ? <p className="hqNote">{t('hq.detail.notAccessible')}</p>
        : <>
          {!props.current && <p className="hqNote">{t('hq.detail.switchNote')}</p>}
          <button type="button" className="hqPrimary" onClick={props.onEnter}>{t('hq.building.enter', { name: b.name })}<ArrowRight aria-hidden="true" className="flip" /></button>
        </>}
    </>
  );
}
