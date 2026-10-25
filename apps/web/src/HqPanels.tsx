/** Built-in side panels: the room summary and the workstation summary with the desk's real work.
 * The meetings and executive workstreams replace them through `renderRoomPanel` and `renderWorkstationPanel`. */
import { ArrowRight, ClipboardPlus, X } from 'lucide-react';
import { openAssign } from './lib/hqAssign.ts';
import { RoomModuleAction } from './HqRoomModule';
import { statusLabel } from './HqChrome';
import { WorkstationWork } from './HqWorkstationWork';
import type { RoomGeometry, SceneDesk } from './lib/headquartersProjection.ts';
import { presenceForDesk, type HqAgentPresence, type HqPresenceIndex, type RoomMeeting } from './lib/hqPresence.ts';
import { profileHash } from './lib/orgChart.ts';
import { STATUS_TONE } from './lib/campusModel.ts';
import { t, type MessageKey } from './lib/i18n.ts';

const noop = () => undefined;

export function StatusPill({ presence }: { presence: HqAgentPresence | undefined }) {
  return (
    <span className={`hqPill tone-${presence ? STATUS_TONE[presence.status] : 'faint'}`} data-stale={presence?.stale === true}>
      {presence ? statusLabel(presence.status) : t('hq.status.unavailable')}{presence?.stale ? ` (${t('hq.agent.stale')})` : ''}
    </span>
  );
}

export function RoomPanel(props: Readonly<{
  room: RoomGeometry; desks: readonly SceneDesk[]; presence: HqPresenceIndex | null;
  onClose: () => void; onOpenAgent: (agentId: string, desk: SceneDesk) => void;
  buildingKind?: string | null; canManage?: boolean; onOpenFlow?: () => void; onOpenGaps?: () => void;
  /** The live meeting held in this room: from the stored `room_id` of an active meeting. Null when none; undefined when unknown. */
  meeting?: RoomMeeting | null;
  /** True once the meetings could be read; false shows an honest "could not be read" note on meeting rooms. */
  meetingsKnown?: boolean;
  onStartMeeting?: () => void;
}>) {
  const { room } = props;
  const assigned = props.desks.filter((d) => d.agentId || presenceForDesk(props.presence, d)).length;
  return (
    <aside className="hqPanel hqSide" aria-label={t('hq.room.panel', { name: room.label })}>
      <div className="hqPanelHead">
        <h2>{room.label}</h2>
        <button type="button" className="hqIconButton" onClick={props.onClose} aria-label={t('hq.panel.close')}><X aria-hidden="true" /></button>
      </div>
      <p className="hqMuted">{t(`hq.roomKind.${room.kind}` as MessageKey)} · {t('hq.room.capacity', { count: room.capacity })}</p>
      {props.meeting && <RoomMeetingSection meeting={props.meeting} presence={props.presence} />}
      {!props.meeting && room.kind === 'meeting' && props.meetingsKnown === false && <p className="hqNote" data-testid="room-meeting-unknown">{t('hq.room.meeting.unknown')}</p>}
      {!props.meeting && room.kind === 'meeting' && props.meetingsKnown !== false && props.onStartMeeting && (
        props.canManage
          ? <button type="button" className="hqGhost" data-testid="room-start-meeting" onClick={props.onStartMeeting}>{t('hq.room.meeting.start')}</button>
          : <p className="hqNote">{t('hq.room.meeting.startOwner')}</p>
      )}
      <ul className="hqStats">
        <li><strong>{props.desks.length}</strong><span>{t('hq.room.desks')}</span></li>
        <li><strong>{assigned}</strong><span>{t('hq.room.assignedLabel')}</span></li>
      </ul>
      {props.desks.length === 0
        ? <p className="hqNote">{t('hq.room.noDesks')}</p>
        : (
          <ul className="hqDeskList">
            {props.desks.map((d) => {
              const p = presenceForDesk(props.presence, d);
              const key = d.agentId ?? p?.agentId ?? null;
              const hidden = p?.reason === 'agent_not_visible' || (key?.startsWith('desk:') ?? false);
              return (
                <li key={d.id}>
                  {key
                    ? <button type="button" onClick={() => props.onOpenAgent(key, d)}>
                      <span className="hqLabelText"><strong>{hidden ? t('hq.agent.private') : (p?.name || t('hq.agent.assigned'))}</strong><small>{d.label}</small></span>
                      <StatusPill presence={p} />
                    </button>
                    : <div className="hqVacant"><span className="hqLabelText"><strong>{d.label}</strong><small>{t('hq.room.vacant')}</small></span></div>}
                </li>
              );
            })}
          </ul>
        )}
      {props.desks.some((d) => d.agentId) && <button type="button" className="hqPrimary" onClick={() => openAssign({ roomId: room.id, roomName: room.label, source: 'room' })}><ClipboardPlus aria-hidden="true" />{t('hqAssign.openRoom')}</button>}
      <RoomModuleAction room={{ department: room.teamDepartment, kind: room.kind, name: room.label, buildingKind: props.buildingKind }} liveMeetingId={props.meeting?.meetingId ?? null} canManage={props.canManage} onOpenFlow={props.onOpenFlow ?? noop} onOpenGaps={props.onOpenGaps ?? noop} />
    </aside>
  );
}

/** "In a meeting": the stored title and only the agents stored as participants (names from the presence records the viewer may see). */
export function RoomMeetingSection({ meeting, presence }: Readonly<{ meeting: RoomMeeting; presence: HqPresenceIndex | null }>) {
  return (
    <section className="hqRoomMeeting" aria-label={t('hq.room.inMeeting')} data-testid="room-meeting">
      <p><span className="hqPill tone-ok">{t('hq.room.inMeeting')}</span> {meeting.title && <strong dir="auto">{meeting.title}</strong>}</p>
      <h3 className="hqSub">{t('hq.room.meeting.who')}</h3>
      {meeting.agentIds.length === 0
        ? <p className="hqNote">{t('hq.room.meeting.noAgents')}</p>
        : <ul className="hqDeskList" aria-label={t('hq.room.meeting.who')}>
          {meeting.agentIds.map((id) => {
            const p = presence?.byAgent.get(id);
            return <li key={id}><div className="hqVacant" data-testid="room-meeting-agent"><span className="hqLabelText"><strong dir="auto">{p?.name || t('hq.agent.private')}</strong><small>{p?.role ?? t('hq.agent.noRole')}</small></span></div></li>;
          })}
        </ul>}
      {meeting.people > 0 && <p className="hqNote">{t('hq.room.meeting.people', { count: meeting.people })}</p>}
    </section>
  );
}

export function AgentPanel(props: Readonly<{
  agentId: string; deskId: string | null; presence: HqAgentPresence | undefined; roomLabel: string | null; deskLabel: string | null;
  refreshKey: string | null; onClose: () => void;
}>) {
  const p = props.presence;
  const hidden = p?.reason === 'agent_not_visible' || props.agentId.startsWith('desk:');
  const name = hidden ? t('hq.agent.private') : (p?.name || t('hq.agent.assigned'));
  return (
    <aside className="hqPanel hqSide" aria-label={t('hq.agent.panel', { name })}>
      <div className="hqPanelHead">
        <h2>{name}</h2>
        <button type="button" className="hqIconButton" onClick={props.onClose} aria-label={t('hq.panel.close')}><X aria-hidden="true" /></button>
      </div>
      <p className="hqMuted">{p?.role ?? t('hq.agent.noRole')}{props.roomLabel ? ` · ${props.roomLabel}` : ''}{props.deskLabel ? ` · ${props.deskLabel}` : ''}</p>
      <p><StatusPill presence={p} /></p>
      {p && <p className="hqNote">{t('hq.agent.observed', { time: new Date(p.observedAt).toLocaleTimeString() })}</p>}
      <WorkstationWork deskId={props.deskId} fallbackTasks={p?.taskTitles ?? []} hasPresence={Boolean(p)} refreshKey={props.refreshKey} />
      {!hidden && !props.agentId.startsWith('desk:') && <button type="button" className="hqPrimary" onClick={() => openAssign({ agentId: props.agentId, source: props.deskId ? 'desk' : 'agent_card' })}><ClipboardPlus aria-hidden="true" />{t('hqAssign.openFor', { name })}</button>}
      {!hidden && <a className="hqPrimary" href={profileHash({ kind: 'agent', id: props.agentId })}>{t('hq.agent.profile')}<ArrowRight aria-hidden="true" className="flip" /></a>}
    </aside>
  );
}
