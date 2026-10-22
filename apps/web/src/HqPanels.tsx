/** Built-in side panels: the room summary and the workstation summary. The meetings and executive
 * workstreams replace them through `renderRoomPanel` and `renderWorkstationPanel`. */
import { ArrowRight, X } from 'lucide-react';
import { statusLabel } from './HqChrome';
import type { RoomGeometry, SceneDesk } from './lib/headquartersProjection.ts';
import type { HqAgentPresence } from './lib/hqPresence.ts';
import { profileHash } from './lib/orgChart.ts';
import { STATUS_TONE } from './lib/campusModel.ts';
import { t, type MessageKey } from './lib/i18n.ts';

const MODULE_ROUTES: Readonly<Record<string, string>> = {
  meeting: 'meetings', reception: 'inbox', office: 'tasks', workshop: 'tyre', warehouse: 'commerce', lounge: 'calendar',
};

export function RoomPanel(props: Readonly<{
  room: RoomGeometry; desks: readonly SceneDesk[]; presence: ReadonlyMap<string, HqAgentPresence> | null;
  onClose: () => void; onOpenAgent: (agentId: string, desk: SceneDesk) => void;
}>) {
  const { room } = props;
  const assigned = props.desks.filter((d) => d.agentId).length;
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
              const p = d.agentId ? props.presence?.get(d.agentId) : undefined;
              return (
                <li key={d.id}>
                  {d.agentId
                    ? <button type="button" onClick={() => props.onOpenAgent(d.agentId!, d)}>
                      <span className="hqLabelText"><strong>{p?.name ?? t('hq.agent.assigned')}</strong><small>{d.label}</small></span>
                      <span className={`hqPill tone-${p ? STATUS_TONE[p.status] : 'faint'}`}>{p ? statusLabel(p.status) : t('hq.status.unavailable')}</span>
                    </button>
                    : <div className="hqVacant"><span className="hqLabelText"><strong>{d.label}</strong><small>{t('hq.room.vacant')}</small></span></div>}
                </li>
              );
            })}
          </ul>
        )}
      <a className="hqPrimary" href={`#${MODULE_ROUTES[room.kind] ?? 'command'}`}>{t('hq.room.openModule', { kind: t(`hq.roomKind.${room.kind}` as MessageKey) })}<ArrowRight aria-hidden="true" className="flip" /></a>
    </aside>
  );
}

export function AgentPanel(props: Readonly<{
  agentId: string; presence: HqAgentPresence | undefined; roomLabel: string | null; deskLabel: string | null; onClose: () => void;
}>) {
  const p = props.presence;
  return (
    <aside className="hqPanel hqSide" aria-label={t('hq.agent.panel', { name: p?.name ?? t('hq.agent.assigned') })}>
      <div className="hqPanelHead">
        <h2>{p?.name ?? t('hq.agent.assigned')}</h2>
        <button type="button" className="hqIconButton" onClick={props.onClose} aria-label={t('hq.panel.close')}><X aria-hidden="true" /></button>
      </div>
      <p className="hqMuted">{p?.role ?? t('hq.agent.noRole')}{props.roomLabel ? ` · ${props.roomLabel}` : ''}{props.deskLabel ? ` · ${props.deskLabel}` : ''}</p>
      <p><span className={`hqPill tone-${p ? STATUS_TONE[p.status] : 'faint'}`}>{p ? statusLabel(p.status) : t('hq.status.unavailable')}</span></p>
      {p && <p className="hqNote">{t('hq.agent.observed', { time: new Date(p.observedAt).toLocaleTimeString() })}</p>}
      <h3>{t('hq.agent.tasks')}</h3>
      {p && p.taskTitles.length > 0
        ? <ul className="hqTaskList">{p.taskTitles.map((title, i) => <li key={i}>{title}</li>)}</ul>
        : <p className="hqNote">{p ? t('hq.agent.noTasks') : t('hq.agent.noEvidence')}</p>}
      <a className="hqPrimary" href={profileHash({ kind: 'agent', id: props.agentId })}>{t('hq.agent.profile')}<ArrowRight aria-hidden="true" className="flip" /></a>
    </aside>
  );
}
