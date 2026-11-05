/**
 * Controls drawn over the headquarters scenes: view switch, camera tools, compass, day and night,
 * bottom toolbar, totals panel with donut, and the minimap. The app's own sidebar and top bar stay
 * the only navigation; this file adds only what is specific to the scene.
 */
import { Box, DoorOpen, ChevronLeft, ChevronRight, Crosshair, LayoutGrid, List, Map as MapIcon, Maximize2, Minimize2, Moon, Plus, Minus, RotateCcw, RotateCw, Sun, Sunset, Maximize } from 'lucide-react';
import { useEffect, useId, useRef, useState, useSyncExternalStore, type ReactElement, type ReactNode } from 'react';
import { readNightPreset, subscribeNightPreset, writeNightPreset } from './lib/hqLightPreset.ts';
import { footprintRect } from './lib/campusFacing.ts';
import { arcPath, donutArcs, STATUS_ORDER, STATUS_TONE, type CampusTotals, type Placement, type StatusKey } from './lib/campusModel.ts';
import { mapToWorld, worldToMap } from './lib/hqCamera.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import type { PresenceLink } from './lib/presenceStream.ts';
import { getGpuState, subscribeGpuState } from './lib/hqGpuState.ts';
import { chipLabel, isReduced, reasonText } from './lib/hqGpuText.ts';
import { tierById } from './lib/hqQualityTiers.ts';

export type HqView = '3d' | 'top' | 'list';

const linkKey = (link: PresenceLink) => (link === 'live' ? 'live' : link === 'reconnecting' ? 'reconnecting' : link === 'polling' ? 'polling' : link === 'connecting' ? 'connecting' : link === 'denied' ? 'denied' : 'offline');
const linkTone = (link: PresenceLink): Tone => (link === 'live' ? 'ok' : link === 'polling' || link === 'reconnecting' || link === 'connecting' ? 'warn' : 'stop');
type Tone = 'ok' | 'warn' | 'stop';

/** Whether the people on the scene are updating by themselves: Live, Reconnecting, Polling, or Not connected. */
export function PresenceLinkChip({ link }: { link: PresenceLink }) {
  const key = linkKey(link);
  const tone = linkTone(link);
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

/**
 * The one status chip of the page header (docs/ANUM_MINE_HQ_PAGE_LAYOUT.md, "Header"): it names the most pressing of the people link
 * (Live, Checking, Not connected), the office data read (Connected, Updating, Connection problem) and the graphics tier, in that tone, and
 * opens a small panel with all three, each in its own words. Nothing is hidden: a problem is the chip's own word, and the panel lists the rest.
 */
export function StatusChip(props: { link: PresenceLink; data: { tone: Tone; text: string }; graphics: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const gpu = useSyncExternalStore(subscribeGpuState, getGpuState, getGpuState);
  const people = { tone: linkTone(props.link), text: t(`hq.live.${linkKey(props.link)}` as MessageKey) };
  const rank = (tone: Tone) => (tone === 'stop' ? 2 : tone === 'warn' ? 1 : 0);
  // The word on the chip: a stopped link first, then a slow or changing one (people before the data read), else the people link (Live).
  const lead = rank(props.data.tone) > rank(people.tone) ? props.data : people;
  const gpuTone: Tone = gpu && isReduced(gpu) ? 'warn' : 'ok';
  const tone: Tone = [lead.tone, props.data.tone, people.tone, gpuTone].reduce<Tone>((a, b) => (rank(b) > rank(a) ? b : a), 'ok');
  const summary = [people.text, props.data.text, gpu ? chipLabel(gpu) : null].filter(Boolean).join(', ');
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e: PointerEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);
  return (
    <div className="hqStatusWrap" ref={wrap} onKeyDown={(e) => { if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false); button.current?.focus(); } }}>
      <button type="button" ref={button} className={`hqConn hqStatusChip tone-${tone}`} data-testid="hq-status-chip" data-tone={tone} aria-expanded={open} aria-controls={id}
        aria-label={t('hq.statusChip.aria', { summary })} title={summary} onClick={() => setOpen((v) => !v)}>
        {lead.text}
      </button>
      <p className="hqSrOnly" role="status" aria-live="polite">{lead.text}</p>
      <div className="hqStatusPanel" id={id} role="group" aria-label={t('hq.statusChip.label')} hidden={!open}>
        <dl>
          <div><dt>{t('hq.statusChip.people')}</dt><dd><PresenceLinkChip link={props.link} /></dd></div>
          <div><dt>{t('hq.statusChip.data')}</dt><dd><span className={`hqConn tone-${props.data.tone}`} data-testid="hq-data-status">{props.data.text}</span></dd></div>
          {props.graphics && <div><dt>{t('hq.statusChip.graphics')}</dt><dd>{props.graphics}</dd></div>}
        </dl>
      </div>
    </div>
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

/** The compass face of the Face north button: it turns with the camera. */
function Compass({ yaw }: { yaw: number }) {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true" className="hqCompassFace" style={{ transform: `rotate(${yaw}deg)` }}>
      <circle cx="20" cy="20" r="18" className="ring" />
      <path d="M20 5 L25 21 L20 18 L15 21 Z" className="north" />
      <path d="M20 35 L15 19 L20 22 L25 19 Z" className="south" />
    </svg>
  );
}

/**
 * The one camera group of a scene (docs/ANUM_MINE_HQ_LABELS.md, "Declutter"): rotate, the floor or building steps, Inside, Floor overview,
 * Pan, zoom, Reset view, an optional extra (the campus map), More camera options (Face north and the light) and Full screen. It fades
 * while the scene is idle and comes back on hover, focus or touch (`useIdleFade`).
 */
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
  /** Back to the home framing. */
  onReset?: () => void;
  /** Face north (the compass turns with the camera) and the light presets, under More camera options. */
  north?: { yaw: number; onNorth: () => void };
  light?: { night: boolean; onNight: (night: boolean) => void };
  /** One more button before More camera options (the campus map toggle). */
  extra?: ReactNode;
  /** The scene is idle: the group fades (hover, focus and touch bring it back). */
  idle?: boolean;
}) {
  const preset = useSyncExternalStore(subscribeNightPreset, readNightPreset, () => 'night' as const);
  const moreId = useId();
  const [more, setMore] = useState(false);
  const moreButton = useRef<HTMLButtonElement>(null);
  const closeMore = (focus: boolean) => { setMore(false); if (focus) moreButton.current?.focus(); };
  // A press anywhere outside the group closes the options.
  useEffect(() => {
    if (!more) return undefined;
    const onDown = (e: PointerEvent) => { if (!moreButton.current?.parentElement?.contains(e.target as Node)) setMore(false); };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [more]);
  return (
    <div className="hqToolbar hqCamGroup" role="toolbar" aria-label={t('hq.tool.label')} data-idle={props.idle === true}
      onKeyDown={(e) => { if (e.key === 'Escape' && more) { e.stopPropagation(); closeMore(true); } }}
      onBlur={(e) => { if (more && !e.currentTarget.contains(e.relatedTarget as Node | null)) setMore(false); }}>
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
      {props.onReset && <button type="button" onClick={props.onReset} data-draws="" aria-label={t('hq.cam.reset')} title={t('hq.cam.reset')}><Crosshair aria-hidden="true" /></button>}
      {props.extra}
      {(props.north || props.light) && (
        <span className="hqCamMore">
          <button type="button" ref={moreButton} aria-expanded={more} aria-controls={moreId} aria-label={t('hq.cam.more')} title={t('hq.cam.more')} data-testid="hq-cam-more" onClick={() => setMore((v) => !v)}>
            {props.north ? <Compass yaw={props.north.yaw} /> : <Sun aria-hidden="true" />}
          </button>
          <span className="hqCamMorePanel" id={moreId} role="group" aria-label={t('hq.cam.more')} hidden={!more}>
            {props.north && <button type="button" className="hqCompass" onClick={() => { props.north!.onNorth(); closeMore(true); }} aria-label={t('hq.cam.north')} title={t('hq.cam.north')}><Compass yaw={props.north.yaw} /><span>{t('hq.cam.north')}</span></button>}
            {props.light && (
              <span className="hqToolsSplit" role="group" aria-label={t('hq.cam.light')}>
                <button type="button" aria-pressed={!props.light.night} onClick={() => props.light!.onNight(false)} aria-label={t('hq.cam.day')} title={t('hq.cam.day')}><Sun aria-hidden="true" /><span>{t('hq.cam.day')}</span></button>
                <button type="button" aria-pressed={props.light.night && preset === 'dusk'} onClick={() => { writeNightPreset('dusk'); props.light!.onNight(true); }} aria-label={t('hq.cam.dusk')} title={t('hq.cam.dusk')}><Sunset aria-hidden="true" /><span>{t('hq.cam.dusk')}</span></button>
                <button type="button" aria-pressed={props.light.night && preset === 'night'} onClick={() => { writeNightPreset('night'); props.light!.onNight(true); }} aria-label={t('hq.cam.night')} title={t('hq.cam.night')}><Moon aria-hidden="true" /><span>{t('hq.cam.night')}</span></button>
              </span>
            )}
          </span>
        </span>
      )}
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
          const f = footprintRect(p);
          const a = worldToMap(f.x0, f.z0, e, size);
          const b = worldToMap(f.x1, f.z1, e, size);
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
