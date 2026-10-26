/**
 * The cards the building scene hangs on rooms, seated agents and floors (docs/ANUM_MINE_HQ_LABELS.md). Plain buttons in the DOM:
 * keyboard reachable, named for a screen reader, and placed by `lib/hqLabels.ts` without a React render per frame. The
 * wrapper's `data-density` (full, compact, dot) decides how much of a card shows; the markup is the same in every density.
 */
import type { AvatarAppearance } from '@anum/contracts';
import { ChevronRight } from 'lucide-react';
import { AvatarChip } from './AvatarStyleEditor';
import { statusLabel } from './HqChrome';
import { STATUS_TONE, type StatusKey } from './lib/campusModel.ts';
import { floorNameParts } from './lib/hqLabelModel.ts';
import { t } from './lib/i18n.ts';
import './hqLabels.css';

/** One small drawing per room kind (drawn here, not imported: every library icon costs more than its few strokes, and the headquarters chunk has a byte budget). */
const KIND_PATH: Readonly<Record<string, string>> = {
  office: 'M4 8h16v11H4zM9 8V5h6v3M4 13h16',
  meeting: 'M3 17h18M5 17v-3a7 7 0 0 1 14 0v3M9 9h.01M15 9h.01M12 6h.01',
  reception: 'M4 18h16M6 18a6 6 0 0 1 12 0M12 8V5M10 5h4',
  warehouse: 'M3 10l9-6 9 6v9H3zM8 19v-6h8v6',
};
export function RoomKindIcon({ kind }: { kind: string }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={KIND_PATH[kind] ?? 'M4 20V6l8-3 8 3v14M9 20v-5h6v5'} /></svg>;
}

export function RoomLabelCard(props: { name: string; kind: string; sub: string; working: number | null; live: boolean; selected: boolean; onPick: () => void }) {
  const sub = props.live ? `${t('hq.room.inMeeting')} \u00B7 ${props.sub}` : props.sub;
  return (
    <button type="button" className="hqLabelCard hqRoomCard" title={props.name} data-selected={props.selected} data-live={props.live} data-kind={props.kind} aria-pressed={props.selected}
      aria-label={t('hqLabels.roomAria', { name: props.name, sub: props.working ? `${sub}. ${t('hqLabels.working', { count: props.working })}` : sub })} onClick={props.onPick}>
      <span className="hqLabelIcon"><RoomKindIcon kind={props.kind} /></span>
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
      <ChevronRight className="hqAgentGo flip" aria-hidden="true" />
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
