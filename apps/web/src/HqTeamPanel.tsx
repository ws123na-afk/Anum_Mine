import { Palette, Users } from 'lucide-react';
import { useState } from 'react';
import type { AvatarAppearance } from '@anum/contracts';
import { AvatarChip, AvatarStyleDialog, roleFamilyOf } from './AvatarStyleEditor';
import * as api from './lib/api';
import { agentsApi } from './lib/agents';
import type { OfficeTeam, OfficeTeamMember } from '@anum/contracts';
import { t, type MessageKey } from './lib/i18n';
import { ageText, reasonText } from './lib/officeFlow';
import { useOfficeTeam } from './useOfficeFlow';
import { useLocale } from './useLocale';
import { openAssign } from './lib/hqAssign';
import './hqFlow.css';

export interface HqTeamPanelProps {
  roomId?: string | null; floorId?: string | null; buildingId?: string | null;
  /** Changes whenever the live flow changes, so the roster re-reads with it. */
  refreshKey: string | number;
  onNavigate: (hash: string) => void;
  onOpenSpaces: () => void;
  onFocusRoom?: (roomId: string) => void;
  /** Opens the office history for one team member (the history panel). */
  onHistory?: (member: { agentId: string; name: string }) => void;
  heading?: string;
  /** Called after an avatar style was saved, so the scene can redraw the figure. */
  onAvatarChanged?: () => void;
}

const agentsClient = agentsApi(api.apiRequest, api.apiRequestVoid);

export function HqTeamPanel({ roomId, floorId, buildingId, refreshKey, onNavigate, onOpenSpaces, onFocusRoom, onHistory, heading, onAvatarChanged }: HqTeamPanelProps) {
  useLocale();
  const { team, status, error } = useOfficeTeam({ roomId, floorId, buildingId }, refreshKey);
  // Styles saved from this panel show at once and stay, because the roster is not read again on a save.
  const [saved, setSaved] = useState<ReadonlyMap<string, AvatarAppearance | null>>(new Map());
  const title = heading ?? (roomId ? t('hqTeam.scope.room') : t('hqTeam.scope.building'));
  return <section className="hqTeam" aria-label={t('hqTeam.region')}>
    <h3 className="hqFlowH"><Users size={15} aria-hidden="true" /> {t('hqTeam.title')} <span>{title}{team ? ` · ${t('hqTeam.count', { count: team.counts.agents })}` : ''}</span></h3>
    {status === 'loading' && <p className="hqFlowNote" role="status">{t('hqTeam.loading')}</p>}
    {status === 'denied' && <p className="hqFlowNote" role="alert">{t('hqTeam.denied')}</p>}
    {status === 'error' && <p className="hqFlowNote" role="alert">{t('hqTeam.error', { detail: error })}</p>}
    {team && status !== 'denied' && <TeamBody team={team} saved={saved} setSaved={setSaved} onNavigate={onNavigate} onOpenSpaces={onOpenSpaces} onFocusRoom={onFocusRoom} onHistory={onHistory} showRoom={!roomId} onAvatarChanged={onAvatarChanged} />}
  </section>;
}

function TeamBody({ team, saved, setSaved, onNavigate, onOpenSpaces, onFocusRoom, onHistory, showRoom, onAvatarChanged }: {
  saved: ReadonlyMap<string, AvatarAppearance | null>; setSaved: (update: (s: ReadonlyMap<string, AvatarAppearance | null>) => ReadonlyMap<string, AvatarAppearance | null>) => void;
  team: OfficeTeam; onNavigate: (hash: string) => void; onOpenSpaces: () => void; onFocusRoom?: (roomId: string) => void; onHistory?: (member: { agentId: string; name: string }) => void; showRoom: boolean; onAvatarChanged?: () => void;
}) {
  const [editing, setEditing] = useState<OfficeTeamMember | null>(null);
  const avatarOf = (m: OfficeTeamMember): AvatarAppearance | null => (saved.has(m.agent_id) ? saved.get(m.agent_id)! : m.avatar ?? null);
  return <>
    {editing && <AvatarStyleDialog name={editing.name} seed={editing.agent_id} role={roleFamilyOf(editing.department)} value={avatarOf(editing)} canEdit onClose={() => setEditing(null)}
      onSave={async (value) => { const next = await agentsClient.update(editing.agent_id, { avatar: value }); setSaved((s) => new Map(s).set(editing.agent_id, next.avatar ?? null)); }}
      onSaved={() => onAvatarChanged?.()} />}
    {team.members.length === 0 ? <div className="hqFlowNote">
      <p>{t('hqTeam.empty')}</p>
      <button type="button" onClick={onOpenSpaces}>{t('hqTeam.emptyAction')}</button>
    </div> : <ul className="hqTeamList" aria-label={t('hqTeam.region')}>
      {team.members.map((m) => <MemberCard key={m.agent_id} member={m} avatar={avatarOf(m)} onEditAvatar={() => setEditing(m)} onNavigate={onNavigate} onFocusRoom={onFocusRoom} onHistory={onHistory} showRoom={showRoom} />)}
    </ul>}
    {team.counts.vacant_desks > 0 && <p className="hqFlowNote">{t('hqTeam.vacant', { count: team.counts.vacant_desks })}</p>}
    {team.hidden_assignments > 0 && <p className="hqFlowNote">{t('hqTeam.hidden', { count: team.hidden_assignments })}</p>}
    {team.unassigned_total > 0 && <details className="hqFlowRules">
      <summary>{t('hqTeam.unassigned')} ({team.unassigned_total})</summary>
      <p>{t('hqTeam.unassignedHint')}</p>
      <ul>{team.unassigned_agents.map((a) => <li key={a.agent_id}>{a.name}{a.department ? ` · ${a.department}` : ''}</li>)}</ul>
      <button type="button" onClick={onOpenSpaces}>{t('hqTeam.emptyAction')}</button>
    </details>}
  </>;
}

function MemberCard({ member: m, avatar, onEditAvatar, onNavigate, onFocusRoom, onHistory, showRoom }: {
  member: OfficeTeamMember; avatar: AvatarAppearance | null; onEditAvatar: () => void; onNavigate: (hash: string) => void; onFocusRoom?: (roomId: string) => void; onHistory?: (member: { agentId: string; name: string }) => void; showRoom: boolean;
}) {
  const state = (['working', 'queued', 'meeting', 'waiting_approval', 'blocked', 'idle', 'paused', 'unknown'].includes(m.state) ? m.state : 'unknown');
  const role = m.department ?? t('hqTeam.noRole');
  return <li className="hqTeamCard" data-state={state} data-stuck={m.stuck?.severity ?? 'none'}>
    <div className="hqTeamHead">
      <AvatarChip id={m.agent_id} name={m.name} avatar={avatar} department={m.department} size={36} />
      <div>
        <strong>{m.name}</strong>
        <small>{t('hqTeam.role')}: {role} · {t(`hqTeam.kind.${m.category}` as MessageKey)}</small>
      </div>
      <span className="hqTeamState" data-state={state}>{t(`hqTeam.state.${state}` as MessageKey)}{m.stale ? ` (${t('hqTeam.stale')})` : ''}</span>
    </div>
    {m.description && <p className="hqTeamDesc">{m.description}</p>}
    <p className="hqTeamTask">{m.task ? t('hqTeam.task', { title: m.task.title }) : t('hqTeam.noTask')}</p>
    {m.stuck && <p className="hqFlowReason"><span className="hqTeamStuck">{t('hqTeam.stuckBadge')}</span>
      <span>{reasonText({ stuck: m.stuck, location: { agent_name: m.name } as never })} ({ageText(m.stuck.age_seconds)})</span></p>}
    <div className="hqTeamSkills">
      <span className="hqTeamSkillsLabel">{t('hqTeam.skills')}</span>
      {m.skills === null ? <span>{m.skills_total ? t('hqTeam.skillsHidden', { count: m.skills_total }) : t('hqTeam.noSkills')}</span>
        : m.skills.length === 0 ? <span>{t('hqTeam.noSkills')}</span>
        : <ul>{m.skills.map((s) => <li key={s.id} data-state={s.state} title={s.reason ?? undefined}>
          {s.name} <small>{t(`hqTeam.skillState.${s.state}` as MessageKey)}</small></li>)}</ul>}
    </div>
    <div className="hqTeamActions">
      {showRoom && onFocusRoom && <button type="button" className="hqLinkButton" onClick={() => onFocusRoom(m.desk.room_id)}>{m.desk.room_name}</button>}
      <button type="button" className="hqLinkButton" onClick={() => openAssign({ agentId: m.agent_id, roomId: m.desk.room_id, roomName: m.desk.room_name, source: 'team_panel' })}>{t('hqAssign.openFor', { name: m.name })}</button>
      <button type="button" className="hqLinkButton" onClick={onEditAvatar} aria-label={t('avatar.editFor', { name: m.name })} data-avatar-edit={m.agent_id}><Palette size={14} aria-hidden="true" /> {t('avatar.edit')}</button>
      <button type="button" className="hqLinkButton" onClick={() => onNavigate('#agents')}>{t('hqTeam.openAgent')}</button>
      {onHistory && <button type="button" className="hqLinkButton" onClick={() => onHistory({ agentId: m.agent_id, name: m.name })}>{t('hqHist.launch.member', { name: m.name })}</button>}
    </div>
  </li>;
}

