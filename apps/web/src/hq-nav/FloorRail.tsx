/** Left rail of a building: its floors, top floor first, each with a thumbnail and a line from the saved rooms. */
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { type ReactElement } from 'react';
import { t, type MessageKey } from '../lib/i18n.ts';
import type { SceneBuilding, SceneFloor } from '../lib/headquartersProjection.ts';
import { floorEntries, planModel, type FloorEntry } from './navModel.ts';
import { arrowFocus, useThumb } from './navParts.tsx';

export type FloorRailProps = Readonly<{
  building: SceneBuilding;
  activeFloorId: string | null;
  slim: boolean;
  thumbKeyOf: (floorId: string) => string;
  onPick: (floorId: string) => void;
  onToggle?: () => void;
  /** Inside a drawer there is no collapse control. */
  drawer?: boolean;
}>;

export function floorSubtitle(entry: FloorEntry): string {
  if (entry.rooms === 0) return t('hqNav.floor.noRooms');
  return entry.kinds.slice(0, 2).map((k) => t(`hq.roomKind.${k.kind}` as MessageKey)).join(', ');
}

function MiniPlan({ floor }: { floor: SceneFloor }): ReactElement {
  const plan = planModel(floor, 60, 3);
  return (
    <svg viewBox={`0 0 ${plan.width} ${plan.height}`} aria-hidden="true" className="hqMiniPlan" preserveAspectRatio="xMidYMid meet">
      {plan.rooms.map((r) => <rect key={r.id} x={r.x} y={r.y} width={r.w} height={r.h} rx="1" className={`kind-${r.kind}`} />)}
    </svg>
  );
}

function Item({ entry, floor, active, slim, thumbKey, onPick }: { entry: FloorEntry; floor: SceneFloor; active: boolean; slim: boolean; thumbKey: string; onPick: () => void }) {
  const shot = useThumb(thumbKey);
  const summary = [floorSubtitle(entry), entry.rooms > 0 ? t('hqNav.floor.rooms', { count: entry.rooms }) : null, entry.assigned > 0 ? t('hqNav.floor.assigned', { count: entry.assigned }) : null].filter(Boolean).join('. ');
  return (
    <li>
      <button type="button" className="hqRailItem" data-nav-item="" data-active={active} aria-current={active ? 'true' : undefined} aria-label={`${entry.name}. ${summary}.`} title={slim ? entry.name : undefined} onClick={onPick}>
        <span className="hqThumb">{shot ? <img src={shot} alt="" /> : <MiniPlan floor={floor} />}</span>
        {!slim && (
          <span className="hqRailText">
            <strong>{entry.name}</strong>
            <small>{floorSubtitle(entry)}</small>
            {entry.rooms > 0 && <small className="hqRailMeta">{t('hqNav.floor.rooms', { count: entry.rooms })}{entry.assigned > 0 ? `, ${t('hqNav.floor.assigned', { count: entry.assigned })}` : ''}</small>}
          </span>
        )}
      </button>
    </li>
  );
}

export function FloorRail(props: FloorRailProps) {
  const entries = floorEntries(props.building);
  const byId = new Map(props.building.floors.map((f) => [f.id, f]));
  const Chevron = props.slim ? ChevronRight : ChevronLeft;
  return (
    <nav className="hqNavRail" data-slim={props.slim} aria-label={t('hqNav.rail.label', { name: props.building.name })} onKeyDown={(e) => arrowFocus(e, 'vertical')}>
      <header>
        {!props.slim && <h2>{t('hqNav.rail.title')}</h2>}
        {!props.drawer && props.onToggle && (
          <button type="button" className="hqIconButton" onClick={props.onToggle} aria-label={props.slim ? t('hqNav.rail.expand') : t('hqNav.rail.collapse')} title={props.slim ? t('hqNav.rail.expand') : t('hqNav.rail.collapse')} aria-expanded={!props.slim}>
            <Chevron aria-hidden="true" className="flip" />
          </button>
        )}
      </header>
      {entries.length === 0
        ? <p className="hqMuted" role="status">{t('hqNav.rail.empty')}</p>
        : <ul>{entries.map((e) => <Item key={e.id} entry={e} floor={byId.get(e.id)!} active={e.id === props.activeFloorId} slim={props.slim} thumbKey={props.thumbKeyOf(e.id)} onPick={() => props.onPick(e.id)} />)}</ul>}
    </nav>
  );
}
