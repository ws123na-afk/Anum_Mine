/** Built-in side panels: the room summary and the workstation summary with the desk's real work.
 * The meetings and executive workstreams replace them through `renderRoomPanel` and `renderWorkstationPanel`. */
import { lazy, Suspense } from 'react';
import { ArrowRight, ClipboardPlus, X } from 'lucide-react';
import { openAssign } from './lib/hqAssign.ts';
import { RoomModuleAction } from './HqRoomModule';
import type { WallModel } from './lib/hqCommandWall.ts';
import { statusLabel } from './HqChrome';
import { WorkstationWork } from './HqWorkstationWork';
import type { RoomGeometry, SceneDesk } from './lib/headquartersProjection.ts';
import { presenceForDesk, type HqAgentPresence, type HqPresenceIndex, type RoomMeeting } from './lib/hqPresence.ts';
import { profileHash } from './lib/orgChart.ts';
import { STATUS_TONE } from './lib/campusModel.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { deskLabel, roomName } from './lib/registryNames.ts';
import { HqShiftBar } from './HqShiftBar';
import { roleText } from './lib/hqRoles.ts';
import { deskShiftLine, roomHasShiftDesks, WORK_STATUSES, type DeskShiftInfo, type ShiftSnapshot } from './lib/hqShifts.ts';

// The wall's code loads only when an operations room is opened.
const HqCommandWall = lazy(() => import('./HqCommandWall').then((m) => ({ default: m.HqCommandWall })));
const noop = () => undefined;

export function StatusPill({ presence }: { presence: HqAgentPresence | undefined }) {
  return (
    <span className={`hqPill tone-${presence ? STATUS_TONE[presence.status] : 'faint'}`} data-stale={presence?.stale === true}>
      {presence ? statusLabel(presence.status) : t('hq.status.unavailable')}{presence?.stale ? ` (${t('hq.agent.stale')})` : ''}
    </span>
  );
}

/** The status pill of a desk: the work state, or "Off shift" / "Off shift, work waiting" for a person whose shift is not now. */
export function DeskPill({ info, p }: { info: DeskShiftInfo | undefined; p: HqAgentPresence | undefined }) {
  const line = deskShiftLine(info, p ? WORK_STATUSES.has(p.status) : false);
  if (line === 'off' || line === 'off_waiting') {
    return <span className="hqPill tone-faint" data-testid="desk-offshift" data-waiting={line === 'off_waiting'}>{t(line === 'off_waiting' ? 'hqGc.offWaiting' : 'hqGc.off')}</span>;
  }
  return <StatusPill presence={p} />;
}

/** The hours line under a desk: the person's working hours (or an older seat's shift and its hours), and for a person off shift the real record that is waiting. */
export function DeskShiftText({ info, p }: { info: DeskShiftInfo | undefined; p: HqAgentPresence | undefined }) {
  const line = deskShiftLine(info, p ? WORK_STATUSES.has(p.status) : false);
  if (!info || !line) return null;
  if (line === 'manager') return <small data-testid="desk-shift">{t('hqGc.shift.manager')}</small>;
  const win = !info.start || !info.end ? ''
    : info.shift ? t('hqGc.shift.line', { shift: t(info.shift === 'day' ? 'hqGc.shift.day' : 'hqGc.shift.night'), start: info.start, end: info.end })
      : t('hqGc.hours.line', { start: info.start, end: info.end });
  return <small data-testid="desk-shift">{win}{line === 'off_waiting' && p?.taskTitles[0] ? ` · ${p.taskTitles[0]}` : ''}</small>;
}

export function RoomPanel(props: Readonly<{
  room: RoomGeometry; desks: readonly SceneDesk[]; presence: HqPresenceIndex | null;
  onClose: () => void; onOpenAgent: (agentId: string, desk: SceneDesk) => void;
  buildingKind?: string | null; canManage?: boolean; onOpenFlow?: (roomId?: string) => void; onOpenGaps?: () => void;
  /** Opens the visitor register; `visitors` is the stored count of visitors checked in at this reception room (undefined: not read, no line). */
  onOpenReception?: (roomId?: string) => void; visitors?: number;
  /** The command centre wall, for an operations room only (null elsewhere). */
  wall?: WallModel | null; onReloadWall?: () => void;
  /** The live meeting held in this room: from the stored `room_id` of an active meeting. Null when none; undefined when unknown. */
  meeting?: RoomMeeting | null;
  /** True once the meetings could be read; false shows an honest "could not be read" note on meeting rooms. */
  meetingsKnown?: boolean;
  onStartMeeting?: () => void;
  /** The clock of the building (only for a building whose seats have working hours). */
  shifts?: ShiftSnapshot | null;
}>) {
  const { room } = props;
  const assigned = props.desks.filter((d) => d.agentId || presenceForDesk(props.presence, d)).length;
  return (
    <aside className="hqPanel hqSide" aria-label={t('hq.room.panel', { name: roomName(room) })}>
      <div className="hqPanelHead">
        <h2>{roomName(room)}</h2>
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
      {props.shifts && roomHasShiftDesks(props.desks) && <HqShiftBar snapshot={props.shifts} desks={props.desks} />}
      {props.wall && <Suspense fallback={null}><HqCommandWall model={props.wall} variant="panel" onReload={props.onReloadWall} /></Suspense>}
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
                    ? <button type="button" onClick={() => props.onOpenAgent(key, d)} data-testid="room-desk" data-desk={d.id}>
                      <span className="hqLabelText"><strong>{hidden ? t('hq.agent.private') : (p?.name || t('hq.agent.assigned'))}</strong><small>{deskLabel(d.label)}{p?.role ? ` · ${roleText(p.role)}` : ''}</small>
                        <DeskShiftText info={d.shiftInfo} p={p} /></span>
                      <DeskPill info={d.shiftInfo} p={p} />
                    </button>
                    : <div className="hqVacant"><span className="hqLabelText"><strong>{deskLabel(d.label)}</strong><small>{t('hq.room.vacant')}</small></span></div>}
                </li>
              );
            })}
          </ul>
        )}
      {props.desks.some((d) => d.agentId) && <button type="button" className="hqPrimary" onClick={() => openAssign({ roomId: room.id, roomName: roomName(room), source: 'room' })}><ClipboardPlus aria-hidden="true" />{t('hqAssign.openRoom')}</button>}
      {room.kind === 'reception' && props.visitors !== undefined && <p className="hqNote" data-testid="room-visitors">{props.visitors > 0 ? t('hqRec.room.count', { count: props.visitors }) : t('hqRec.room.none')}</p>}
      <RoomModuleAction roomId={room.id} room={{ department: room.teamDepartment, kind: room.kind, name: room.label, buildingKind: props.buildingKind }} liveMeetingId={props.meeting?.meetingId ?? null} canManage={props.canManage} onOpenFlow={props.onOpenFlow ?? noop} onOpenGaps={props.onOpenGaps ?? noop} onOpenReception={props.onOpenReception} />
    </aside>
  );
}

/** "In a meeting": the stored title and only the agents stored as participants (names from the presence records the viewer may see). */
export function RoomMeetingSection({ meeting, presence }: Readonly<{ meeting: RoomMeeting; presence: HqPresenceIndex | null }>) {
  return (
    <section className="hqRoomMeeting" aria-label={t('hq.room.inMeeting')} data-testid="room-meeting">
      <p><span className="hqPill tone-ok">{t('hq.room.inMeeting')}</span> {meeting.title && <strong dir="auto">{meeting.title}</strong>}</p>
      <h3 className="hqSub">{t('hq.room.meeting.who')}</h3>
      {meeting.agentIds.length === 0 && !meeting.leftAgentIds?.length
        ? <p className="hqNote">{t('hq.room.meeting.noAgents')}</p>
        : <ul className="hqDeskList" aria-label={t('hq.room.meeting.who')}>
          {meeting.agentIds.map((id) => {
            const p = presence?.byAgent.get(id);
            return <li key={id}><div className="hqVacant" data-testid="room-meeting-agent"><span className="hqLabelText"><strong dir="auto">{p?.name || t('hq.agent.private')}</strong><small>{p?.role ?? t('hq.agent.noRole')}</small></span></div></li>;
          })}
          {(meeting.leftAgentIds ?? []).map((id) => {
            const p = presence?.byAgent.get(id);
            return <li key={`left:${id}`}><div className="hqVacant" data-testid="room-meeting-left"><span className="hqLabelText"><strong dir="auto">{p?.name || t('hq.agent.private')}</strong><small>{t('hq.room.meeting.leftTag')}</small></span></div></li>;
          })}
        </ul>}
      {meeting.people > 0 && <p className="hqNote">{t('hq.room.meeting.people', { count: meeting.people })}</p>}
      {(meeting.leftPeople ?? 0) > 0 && <p className="hqNote" data-testid="room-meeting-people-left">{t('hq.room.meeting.peopleLeft', { count: meeting.leftPeople ?? 0 })}</p>}
    </section>
  );
}

export function AgentPanel(props: Readonly<{
  agentId: string; deskId: string | null; presence: HqAgentPresence | undefined; roomLabel: string | null; deskLabel: string | null;
  refreshKey: string | null; onClose: () => void;
  /** What the desk says about its shift (a person off shift is never lit, whatever work is waiting). */
  shiftInfo?: DeskShiftInfo;
}>) {
  const p = props.presence;
  const shiftLine = deskShiftLine(props.shiftInfo, p ? WORK_STATUSES.has(p.status) : false);
  const hidden = p?.reason === 'agent_not_visible' || props.agentId.startsWith('desk:');
  const name = hidden ? t('hq.agent.private') : (p?.name || t('hq.agent.assigned'));
  return (
    <aside className="hqPanel hqSide" aria-label={t('hq.agent.panel', { name })}>
      <div className="hqPanelHead">
        <h2>{name}</h2>
        <button type="button" className="hqIconButton" onClick={props.onClose} aria-label={t('hq.panel.close')}><X aria-hidden="true" /></button>
      </div>
      <p className="hqMuted">{p?.role ? roleText(p.role) : t('hq.agent.noRole')}{props.roomLabel ? ` · ${props.roomLabel}` : ''}{props.deskLabel ? ` · ${deskLabel(props.deskLabel)}` : ''}</p>
      <p><DeskPill info={props.shiftInfo} p={p} /></p>
      {props.shiftInfo && shiftLine && <section className="hqShiftCard" aria-label={t('hqGc.shift.bar')} data-testid="agent-shift">
        <DeskShiftText info={props.shiftInfo} p={p} />
        {props.shiftInfo.hoursAreDefault && <small>{t('hqGc.shift.hoursDefault')}</small>}
        {shiftLine === 'on' && <small>{t('hqGc.shift.onLine')}</small>}
        {shiftLine === 'off' && <small>{t('hqGc.shift.offLine')}</small>}
        {shiftLine === 'off_waiting' && <small>{t('hqGc.shift.offWaitingLine')}</small>}
      </section>}
      {p && <p className="hqNote" data-testid="agent-state-help">{t(`hq.stateHelp.${p.status}` as MessageKey)}</p>}
      {p && <p className="hqNote">{t('hq.agent.observed', { time: new Date(p.observedAt).toLocaleTimeString() })}</p>}
      <WorkstationWork deskId={props.deskId} fallbackTasks={p?.taskTitles ?? []} hasPresence={Boolean(p)} refreshKey={props.refreshKey} />
      {!hidden && !props.agentId.startsWith('desk:') && <button type="button" className="hqPrimary" onClick={() => openAssign({ agentId: props.agentId, source: props.deskId ? 'desk' : 'agent_card' })}><ClipboardPlus aria-hidden="true" />{t('hqAssign.openFor', { name })}</button>}
      {!hidden && <a className="hqPrimary" href={profileHash({ kind: 'agent', id: props.agentId })}>{t('hq.agent.profile')}<ArrowRight aria-hidden="true" className="flip" /></a>}
    </aside>
  );
}
