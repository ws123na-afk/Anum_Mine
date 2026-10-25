/** Built-in side panels: the room summary and the workstation summary with the desk's real work.
 * The meetings and executive workstreams replace them through `renderRoomPanel` and `renderWorkstationPanel`. */
import { ArrowRight, X } from 'lucide-react';
import { RoomModuleAction } from './HqRoomModule';
import { statusLabel } from './HqChrome';
import { WorkstationWork } from './HqWorkstationWork';
import type { RoomGeometry, SceneDesk } from './lib/headquartersProjection.ts';
import { presenceForDesk, type HqAgentPresence, type HqPresenceIndex } from './lib/hqPresence.ts';
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
      <RoomModuleAction room={{ department: room.teamDepartment, kind: room.kind, name: room.label, buildingKind: props.buildingKind }} canManage={props.canManage} onOpenFlow={props.onOpenFlow ?? noop} onOpenGaps={props.onOpenGaps ?? noop} />
    </aside>
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
      {!hidden && <a className="hqPrimary" href={profileHash({ kind: 'agent', id: props.agentId })}>{t('hq.agent.profile')}<ArrowRight aria-hidden="true" className="flip" /></a>}
    </aside>
  );
}
