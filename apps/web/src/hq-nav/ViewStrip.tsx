/** Bottom strip of quick views: the campus aerial, each building's entrance and the rooms of the open building. */
import { ChevronDown, ChevronUp, LockKeyhole } from 'lucide-react';
import { t, type MessageKey } from '../lib/i18n.ts';
import type { StripStop } from './navModel.ts';
import { stopName } from './stopName.ts';
import { arrowFocus, BUILDING_ICON, CAMPUS_ICON, ROOM_ICONS, useThumb } from './navParts.tsx';

export type ViewStripProps = Readonly<{
  stops: readonly StripStop[];
  currentId: string;
  open: boolean;
  thumbKeyOf: (stop: StripStop) => string;
  onToggle: () => void;
  onGo: (stop: StripStop) => void;
}>;

export function stopSub(stop: StripStop): string {
  if (stop.kind === 'campus') return t('hqNav.strip.campusSub');
  if (stop.kind === 'building') return stop.accessible ? t('hqNav.strip.building') : t('hqNav.strip.locked');
  return `${t(`hq.roomKind.${stop.roomKind}` as MessageKey)}, ${stop.floorName}`;
}

function Stop({ stop, current, thumbKey, onGo }: { stop: StripStop; current: boolean; thumbKey: string; onGo: () => void }) {
  const shot = useThumb(thumbKey);
  const icon = stop.kind === 'campus' ? CAMPUS_ICON : stop.kind === 'building' ? BUILDING_ICON : ROOM_ICONS[stop.roomKind] ?? BUILDING_ICON;
  const locked = stop.kind === 'building' && !stop.accessible;
  return (
    <li>
      <button type="button" className="hqStop" title={stopName(stop)} data-nav-item="" data-current={current} data-kind={stop.kind} aria-current={current ? 'true' : undefined} disabled={locked}
        aria-label={t('hqNav.strip.stop', { name: stopName(stop), sub: current ? t('hqNav.strip.here') : stopSub(stop) })} onClick={onGo}>
        <span className="hqStopThumb">{shot ? <img src={shot} alt="" /> : <span className="hqStopIcon">{icon}</span>}{locked && <LockKeyhole aria-hidden="true" className="hqLock" />}</span>
        <span className="hqStopCap">{stopName(stop)}</span>
      </button>
    </li>
  );
}

export function ViewStrip(props: ViewStripProps) {
  const Toggle = props.open ? ChevronDown : ChevronUp;
  return (
    <div className="hqStrip" data-open={props.open}>
      <button type="button" className="hqStripToggle" onClick={props.onToggle} aria-expanded={props.open} aria-label={props.open ? t('hqNav.strip.collapse') : t('hqNav.strip.expand')}>
        <Toggle aria-hidden="true" /><span>{props.open ? t('hqNav.strip.collapseShort') : t('hqNav.strip.label')}</span>
      </button>
      {props.open && (
        <nav aria-label={t('hqNav.strip.label')} onKeyDown={(e) => arrowFocus(e, 'horizontal')}>
          <ul>{props.stops.map((s) => <Stop key={s.id} stop={s} current={s.id === props.currentId} thumbKey={props.thumbKeyOf(s)} onGo={() => props.onGo(s)} />)}</ul>
        </nav>
      )}
    </div>
  );
}
