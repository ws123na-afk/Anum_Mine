/**
 * The cards the building scene hangs on rooms, seated agents and floors (docs/ANUM_MINE_HQ_LABELS.md). Plain buttons in the DOM:
 * keyboard reachable, named for a screen reader, and placed by `lib/hqLabels.ts` without a React render per frame. The
 * wrapper's `data-density` (full, compact, dot) decides how much of a card shows; the markup is the same in every density.
 */
import type { AvatarAppearance } from '@anum/contracts';
import { AvatarChip } from './AvatarStyleEditor';
import { statusLabel } from './HqChrome';
import { STATUS_TONE, type StatusKey } from './lib/campusModel.ts';
import { floorNameParts } from './lib/hqLabelModel.ts';
import { t } from './lib/i18n.ts';
import './hqLabels.css';

export function RoomLabelCard(props: { name: string; kind: string; sub: string; working: number | null; live: boolean; selected: boolean; onPick: () => void }) {
  const sub = props.live ? `${t('hq.room.inMeeting')} \u00B7 ${props.sub}` : props.sub;
  return (
    <button type="button" className="hqLabelCard hqRoomCard" data-selected={props.selected} data-live={props.live} data-kind={props.kind} aria-pressed={props.selected}
      aria-label={t('hqLabels.roomAria', { name: props.name, sub: props.working ? `${sub}. ${t('hqLabels.working', { count: props.working })}` : sub })} onClick={props.onPick}>
      <span className="hqLabelIcon" data-kind={props.kind} aria-hidden="true" />
      <span className="hqLabelText" aria-hidden="true"><strong dir="auto">{props.name}</strong><small dir="auto">{sub}</small></span>
      {props.working ? <span className="hqRoomWorking" aria-hidden="true" title={t('hqLabels.working', { count: props.working })}>{props.working}</span> : null}
    </button>
  );
}

export function AgentLabelCard(props: {
  chip: { id: string; avatar: AvatarAppearance | null; department: string | null } | null; name: string; role: string | null;
  status: StatusKey | null; stale: boolean; selected: boolean; onPick: () => void;
}) {
  const tone = props.status ? STATUS_TONE[props.status] : 'faint';
  const statusText = props.status ? statusLabel(props.status) : t('hq.status.unavailable');
  return (
    <button type="button" className={`hqAgentCard tone-${tone}`} data-selected={props.selected} data-stale={props.stale} aria-pressed={props.selected}
      aria-label={t('hqLabels.agentAria', { name: props.name, role: props.role ?? t('hqLabels.agentNoRole'), status: props.stale ? `${statusText} (${t('hq.agent.stale')})` : statusText })} onClick={props.onPick}>
      {props.chip
        ? <AvatarChip id={props.chip.id} name={props.name} avatar={props.chip.avatar} department={props.chip.department} size={34} decorative />
        : <span className="hqAvatar" aria-hidden="true">{props.name.slice(0, 1).toUpperCase()}</span>}
      <span className="hqLabelText" aria-hidden="true">
        <strong dir="auto">{props.name}</strong>
        {props.role && <small dir="auto">{props.role}</small>}
        <span className={`hqPill tone-${tone}`}>{statusText}{props.stale ? ` (${t('hq.agent.stale')})` : ''}</span>
      </span>
      <span className="hqAgentGo" aria-hidden="true">{'\u203A'}</span>
    </button>
  );
}

export function FloorTagCard(props: { name: string; rooms: number; active: boolean; onPick: () => void }) {
  const parts = floorNameParts(props.name);
  return (
    <button type="button" className="hqLabelCard hqFloorTag" data-selected={props.active} aria-pressed={props.active} aria-label={t('hqLabels.floorTagAria', { name: props.name })} onClick={props.onPick}>
      <span className="hqLabelText" aria-hidden="true"><strong dir="auto">{parts.short}</strong><small dir="auto">{parts.rest ? `${parts.rest} \u00B7 ` : ''}{t('hqLabels.floorTagRooms', { count: props.rooms })}</small></span>
    </button>
  );
}
