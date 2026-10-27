/**
 * Controls drawn over the headquarters scenes: view switch, camera tools, compass, day and night,
 * bottom toolbar, totals panel with donut, and the minimap. The app's own sidebar and top bar stay
 * the only navigation; this file adds only what is specific to the scene.
 */
import { Box, DoorOpen, ChevronLeft, ChevronRight, Crosshair, LayoutGrid, List, Map as MapIcon, Maximize2, Minimize2, Moon, Plus, Minus, RotateCcw, RotateCw, Sun, Sunset, Maximize } from 'lucide-react';
import { useId, useRef, useState, useSyncExternalStore, type ReactElement, type ReactNode } from 'react';
import { readNightPreset, subscribeNightPreset, writeNightPreset } from './lib/hqLightPreset.ts';
import { arcPath, donutArcs, STATUS_ORDER, STATUS_TONE, type CampusTotals, type Placement, type StatusKey } from './lib/campusModel.ts';
import { mapToWorld, worldToMap } from './lib/hqCamera.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import type { PresenceLink } from './lib/presenceStream.ts';
import { getGpuState, subscribeGpuState } from './lib/hqGpuState.ts';
import { chipLabel, isReduced, reasonText } from './lib/hqGpuText.ts';
import { tierById } from './lib/hqQualityTiers.ts';

export type HqView = '3d' | 'top' | 'list';

/** Whether the people on the scene are updating by themselves: Live, Reconnecting, Polling, or Not connected. */
export function PresenceLinkChip({ link }: { link: PresenceLink }) {
  const key = link === 'live' ? 'live' : link === 'reconnecting' ? 'reconnecting' : link === 'polling' ? 'polling' : link === 'connecting' ? 'connecting' : link === 'denied' ? 'denied' : 'offline';
  const tone = link === 'live' ? 'ok' : link === 'polling' || link === 'reconnecting' || link === 'connecting' ? 'warn' : 'stop';
  return (
    <span className={`hqConn hqLink tone-${tone}`} role="status" data-testid="hq-live-link" data-link={link} title={t(`hq.live.${key}.hint` as MessageKey)}>
      {t(`hq.live.${key}` as MessageKey)}
    </span>
  );
}

/** The current graphics tier next to the live status chips. It opens the Graphics check; the tooltip says why this tier is in force. */
export function GraphicsChip({ onOpen }: { onOpen: () => void }) {
  const state = useSyncExternalStore(subscribeGpuState, getGpuState, getGpuState);
  if (!state) return null;
  const reduced = isReduced(state);
  return (
    <button type="button" className={`hqConn hqGpuChip tone-${reduced ? 'warn' : 'ok'}`} data-testid="hq-gpu-chip" data-tier={tierById(state.tier).key} data-reduced={String(reduced)}
      title={reasonText(state)} aria-label={`${chipLabel(state)}. ${t('hqGpu.chip.open')}`} onClick={onOpen}>
      {chipLabel(state)}
    </button>
  );
}

export const statusLabel = (key: StatusKey): string => t(`hq.status.${key}` as MessageKey);

export function ViewSwitch({ view, onChange }: { view: HqView; onChange: (view: HqView) => void }) {
  const items: Array<{ id: HqView; icon: ReactElement }> = [
    { id: '3d', icon: <Box aria-hidden="true" /> },
    { id: 'top', icon: <MapIcon aria-hidden="true" /> },
    { id: 'list', icon: <List aria-hidden="true" /> },
  ];
  return (
    <div className="hqSegment" role="group" aria-label={t('hq.view.label')}>
      {items.map((item) => (
        <button key={item.id} type="button" aria-pressed={view === item.id} onClick={() => onChange(item.id)}>
          {item.icon}<span>{t(`hq.view.${item.id}` as MessageKey)}</span>
        </button>
      ))}
    </div>
  );
}

export function CameraTools(props: {
  yaw: number; night: boolean; onNight: (night: boolean) => void;
  onZoomIn: () => void; onZoomOut: () => void; onReset: () => void; onNorth: () => void;
  /** One more button at the end of the corner rail (the campus map toggle). */
  extra?: ReactNode;
}) {
  const preset = useSyncExternalStore(subscribeNightPreset, readNightPreset, () => 'night' as const);
  return (
    <div className="hqTools" role="group" aria-label={t('hq.cam.label')}>
      <button type="button" className="hqCompass" onClick={props.onNorth} aria-label={t('hq.cam.north')} title={t('hq.cam.north')}>
        <svg viewBox="0 0 40 40" aria-hidden="true" style={{ transform: `rotate(${props.yaw}deg)` }}>
          <circle cx="20" cy="20" r="18" className="ring" />
          <path d="M20 5 L25 21 L20 18 L15 21 Z" className="north" />
          <path d="M20 35 L15 19 L20 22 L25 19 Z" className="south" />
        </svg>
        <span className="hqCompassN" aria-hidden="true">N</span>
      </button>
      <button type="button" onClick={props.onZoomIn} data-draws="" aria-label={t('hq.cam.zoomIn')} title={t('hq.cam.zoomIn')}><Plus aria-hidden="true" /></button>
      <button type="button" onClick={props.onZoomOut} data-draws="" aria-label={t('hq.cam.zoomOut')} title={t('hq.cam.zoomOut')}><Minus aria-hidden="true" /></button>
      <button type="button" onClick={props.onReset} data-draws="" aria-label={t('hq.cam.reset')} title={t('hq.cam.reset')}><Crosshair aria-hidden="true" /></button>
      <div className="hqToolsSplit" role="group" aria-label={t('hq.cam.light')}>
        <button type="button" aria-pressed={!props.night} onClick={() => props.onNight(false)} aria-label={t('hq.cam.day')} title={t('hq.cam.day')}><Sun aria-hidden="true" /></button>
        <button type="button" aria-pressed={props.night && preset === 'dusk'} onClick={() => { writeNightPreset('dusk'); props.onNight(true); }} aria-label={t('hq.cam.dusk')} title={t('hq.cam.dusk')}><Sunset aria-hidden="true" /></button>
        <button type="button" aria-pressed={props.night && preset === 'night'} onClick={() => { writeNightPreset('night'); props.onNight(true); }} aria-label={t('hq.cam.night')} title={t('hq.cam.night')}><Moon aria-hidden="true" /></button>
      </div>
      {props.extra && <div className="hqToolsSplit">{props.extra}</div>}
    </div>
  );
}

export function BottomToolbar(props: {
  onRotateLeft: () => void; onRotateRight: () => void; onPrev?: () => void; onNext?: () => void; prevDisabled?: boolean; nextDisabled?: boolean;
  prevLabel?: string; nextLabel?: string; fullscreen: boolean; onFullscreen: () => void;
  onZoomIn: () => void; onZoomOut: () => void;
  /** The Inside view of the selected room: absent on screens that have no rooms. */
  inside?: { active: boolean; disabled: boolean; onToggle: () => void };
  /** The whole active floor from above, every room labelled (spec: docs/ANUM_MINE_FLOOR_OVERVIEW.md). */
  overview?: { active: boolean; onToggle: () => void };
  /** Dragging moves the scene sideways while on (the mock's Pan). */
  pan?: { active: boolean; onToggle: () => void };
}) {
  return (
    <div className="hqToolbar" role="toolbar" aria-label={t('hq.tool.label')}>
      <button type="button" onClick={props.onRotateLeft} data-draws="" aria-label={t('hq.tool.rotateLeft')} title={t('hq.tool.rotateLeft')}><RotateCcw aria-hidden="true" /><span>{t('hq.tool.rotateLeft')}</span></button>
      {props.onPrev && <button type="button" disabled={props.prevDisabled} onClick={props.onPrev} aria-label={props.prevLabel ?? t('hq.tool.prev')} title={props.prevLabel ?? t('hq.tool.prev')}><ChevronLeft aria-hidden="true" className="flip" /></button>}
      {props.onNext && <button type="button" disabled={props.nextDisabled} onClick={props.onNext} aria-label={props.nextLabel ?? t('hq.tool.next')} title={props.nextLabel ?? t('hq.tool.next')}><ChevronRight aria-hidden="true" className="flip" /></button>}
      <button type="button" onClick={props.onRotateRight} data-draws="" aria-label={t('hq.tool.rotateRight')} title={t('hq.tool.rotateRight')}><RotateCw aria-hidden="true" /><span>{t('hq.tool.rotateRight')}</span></button>
      {props.inside && (
        <button type="button" onClick={props.inside.onToggle} disabled={props.inside.disabled && !props.inside.active} aria-pressed={props.inside.active} data-testid="hq-inside" data-draws=""
          aria-label={props.inside.active ? t('hq.inside.leave') : t('hq.inside.enter')} title={props.inside.disabled && !props.inside.active ? t('hq.inside.pickRoom') : props.inside.active ? t('hq.inside.leave') : t('hq.inside.enter')}>
          <DoorOpen aria-hidden="true" /><span>{props.inside.active ? t('hq.inside.leaveShort') : t('hq.inside.enterShort')}</span>
        </button>
      )}
      {props.overview && (
        <button type="button" onClick={props.overview.onToggle} aria-pressed={props.overview.active} data-testid="hq-overview" data-draws=""
          aria-label={props.overview.active ? t('hqFloor.leave') : t('hqFloor.enter')} title={props.overview.active ? t('hqFloor.leave') : t('hqFloor.enter')}>
          <LayoutGrid aria-hidden="true" /><span>{t('hqFloor.short')}</span>
        </button>
      )}
      {props.pan && (
        <button type="button" onClick={props.pan.onToggle} aria-pressed={props.pan.active} data-testid="hq-pan" data-draws="" aria-label={t('hqLabels.pan')} title={t('hqLabels.panHint')}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 2v20M2 12h20M15 19l-3 3-3-3M19 9l3 3-3 3M5 9l-3 3 3 3M9 5l3-3 3 3" /></svg><span>{t('hqLabels.pan')}</span>
        </button>
      )}
      <span className="hqToolbarSep" aria-hidden="true" />
      <button type="button" onClick={props.onZoomOut} data-draws="" aria-label={t('hq.cam.zoomOut')} title={t('hq.cam.zoomOut')}><Minus aria-hidden="true" /></button>
      <button type="button" onClick={props.onZoomIn} data-draws="" aria-label={t('hq.cam.zoomIn')} title={t('hq.cam.zoomIn')}><Plus aria-hidden="true" /></button>
      <button type="button" onClick={props.onFullscreen} aria-pressed={props.fullscreen} aria-label={props.fullscreen ? t('hq.tool.exitFullscreen') : t('hq.tool.fullscreen')} title={props.fullscreen ? t('hq.tool.exitFullscreen') : t('hq.tool.fullscreen')}>
        {props.fullscreen ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
        <span>{props.fullscreen ? t('hq.tool.exitFullscreen') : t('hq.tool.fullscreen')}</span>
      </button>
    </div>
  );
}

export function Donut({ totals, size = 96 }: { totals: CampusTotals; size?: number }) {
  const c = size / 2;
  const arcs = donutArcs(totals);
  return (
    <svg className="hqDonut" width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={t('hq.totals.donut', { agents: totals.agents })}>
      <circle cx={c} cy={c} r={c * 0.78} className="track" fill="none" strokeWidth={c * 0.3} />
      {arcs.map((arc) => (
        <path key={arc.key} d={arcPath(c, c, c - 1, c * 0.55, arc.start, arc.end)} className={`tone-${STATUS_TONE[arc.key]}`} />
      ))}
      <text x={c} y={c - 1} textAnchor="middle" className="num">{totals.agents}</text>
      <text x={c} y={c + 12} textAnchor="middle" className="cap">{t('hq.totals.agentsCap')}</text>
    </svg>
  );
}

/** Totals panel: counts per status, or "Presence unavailable" when no record backs them. */
export function TotalsPanel(props: {
  title: string; totals: CampusTotals | null; unreported?: readonly StatusKey[];
  note?: string | null; verifiedAt?: string | null; compact?: boolean;
}) {
  const unreported = new Set(props.unreported ?? []);
  const shown = STATUS_ORDER.filter((key) => key === 'working' || key === 'meeting' || key === 'idle' || (props.totals?.[key] ?? 0) > 0);
  return (
    <section className={`hqPanel hqTotals ${props.compact ? 'compact' : ''}`} aria-label={props.title}>
      <h2>{props.title}</h2>
      {props.totals ? (
        <div className="hqTotalsBody">
          <Donut totals={props.totals} size={props.compact ? 76 : 96} />
          <ul className="hqLegend">
            {shown.map((key) => (
              <li key={key} className={`tone-${STATUS_TONE[key]}`}>
                <span className="dot" aria-hidden="true" />
                <span className="name">{statusLabel(key)}</span>
                <strong>{unreported.has(key) ? '-' : props.totals![key]}</strong>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="hqUnavailable" role="status">
          <strong>{t('hq.totals.unavailable')}</strong>
          <span>{t('hq.totals.unavailableHint')}</span>
        </div>
      )}
      {props.note && <p className="hqNote">{props.note}</p>}
      {props.verifiedAt && <p className="hqNote">{t('hq.totals.verified', { time: new Date(props.verifiedAt).toLocaleTimeString() })}</p>}
    </section>
  );
}

export function Minimap(props: {
  placements: readonly Placement[]; extent: number; selectedId: string | null;
  footprint: ReadonlyArray<{ x: number; z: number }> | null; onJump: (x: number, z: number) => void;
  onSelect: (workspaceId: string) => void; nameOf: (workspaceId: string) => string;
}) {
  const [large, setLarge] = useState(false);
  const size = large ? 240 : 150;
  const id = useId();
  const svg = useRef<SVGSVGElement>(null);
  const e = Math.max(40, props.extent + 14);
  const points = props.footprint?.map((p) => { const m = worldToMap(p.x, p.z, e, size); return `${m.x.toFixed(1)},${m.y.toFixed(1)}`; }).join(' ');
  return (
    <section className="hqPanel hqMinimap" aria-labelledby={id}>
      <div className="hqPanelHead">
        <h2 id={id}>{t('hq.minimap.title')}</h2>
        <button type="button" className="hqIconButton" onClick={() => setLarge((v) => !v)} aria-pressed={large} aria-label={large ? t('hq.minimap.shrink') : t('hq.minimap.enlarge')} title={large ? t('hq.minimap.shrink') : t('hq.minimap.enlarge')}>
          {large ? <Minimize2 aria-hidden="true" /> : <Maximize aria-hidden="true" />}
        </button>
      </div>
      <svg
        ref={svg} width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="hqMapSvg" role="img" aria-label={t('hq.minimap.hint')}
        onClick={(event) => {
          const box = svg.current?.getBoundingClientRect();
          if (!box) return;
          const w = mapToWorld(((event.clientX - box.left) / box.width) * size, ((event.clientY - box.top) / box.height) * size, e, size);
          props.onJump(w.x, w.z);
        }}
      >
        <rect x="0" y="0" width={size} height={size} className="ground" />
        {props.placements.map((p) => {
          const a = worldToMap(p.x - p.width / 2, p.z - p.depth / 2, e, size);
          const b = worldToMap(p.x + p.width / 2, p.z + p.depth / 2, e, size);
          return (
            <rect
              key={p.workspaceId} x={a.x} y={a.y} width={b.x - a.x} height={b.y - a.y} rx="1.5"
              className={`bld ${p.kind} ${props.selectedId === p.workspaceId ? 'selected' : ''}`}
              onClick={(event) => { event.stopPropagation(); props.onSelect(p.workspaceId); }}
            ><title>{props.nameOf(p.workspaceId)}</title></rect>
          );
        })}
        {points && <polygon points={points} className="view" />}
      </svg>
    </section>
  );
}
