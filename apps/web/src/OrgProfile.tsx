// A member's full profile page from the AI team org chart (docs/org-chart.md#profile-page):
// linkable at `#agents/team/<agent|assistant|person>/<id>[/<tab>]`, with Back. Agents get
// Overview, Work, Skills and tools, Approvals, Activity and Performance; department assistants and
// people get the same depth for what the API holds about them. Every section reads live data and
// has its own loading, empty, error and permission states.
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { AlertTriangle, ArrowLeft, MessageSquare, Pause, Pencil, Play, RefreshCw, UserRound } from 'lucide-react';
import type { AgentProfile, AgentSkillLoadout, WorkItemPage, Workload } from '@anum/contracts';
import * as api from './lib/api';
import { agentsApi } from './lib/agents';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, getLocale, t, type MessageKey } from './lib/i18n';
import { riskLabel, statusLabel } from './lib/labels';
import {
  agentTasksByStatusPath, agentTasksPath, approvalsForTasks, autonomyRules, averageDurationMs, buildOrgChart, durationParts, loadOrgSources,
  PROFILE_TABS, summaryPath, taskStats, toolChips, type OrgMember, type OrgSources, type ProfileRef, type ProfileTab, type TaskStats,
} from './lib/orgChart';
import { workQuery } from './lib/team';
import { AssignTask, Notice, why } from './OrgMemberDrawer';
import { agentRole, ALIASES, Avatar, chipLabel, chipStatus, departmentLabel, roleText, statusText, ToolButton, type OrgGo } from './OrgChartView';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);
type Role = 'owner' | 'member' | 'viewer' | null;
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string; denied: boolean };

/** One request as a section's state, with retry. */
function useLoad<T>(run: () => Promise<T>, deps: unknown[]): [Load<T>, () => void] {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    // A refresh keeps showing the last answer, so a section never blanks while it reloads.
    setLoad((old) => old.state === 'ready' ? old : { state: 'loading' });
    run().then((value) => { if (live) setLoad({ state: 'ready', value }); })
      .catch((e) => { if (live) setLoad({ state: 'error', message: describeError(e), denied: isPermissionDenied(e) }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return [load, () => setTick((n) => n + 1)];
}

/** The loading, error and permission states every section shares; `children` renders the value. */
function Loaded<T>({ load, retry, children }: { load: Load<T>; retry: () => void; children: (value: T) => ReactNode }) {
  if (load.state === 'loading') return <p className="muted" role="status">{t('common.loading')}</p>;
  if (load.state === 'error') return load.denied
    ? <p className="notice" role="status">{t('org.profile.denied', { message: load.message })}</p>
    : <div><Notice text={t('org.profile.failed', { reason: load.message })} error /><button type="button" className="secondary" onClick={retry}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>;
  return <>{children(load.value)}</>;
}

function Panel({ title, children, wide, action }: { title: string; children: ReactNode; wide?: boolean; action?: ReactNode }) {
  const id = useId();
  return <section className={`surface orgPanel ${wide ? 'isWide' : ''}`} aria-labelledby={id}>
    <div className="orgPanelHead"><h3 id={id}>{title}</h3>{action}</div>
    {children}
  </section>;
}

function Stat({ label, value, tone = 'neutral', note }: { label: string; value: string | number; tone?: string; note?: string }) {
  return <div className={`orgKpi tone-${tone}`}><dt>{label}</dt><dd>{value}</dd>{note && <dd className="orgKpiNote muted">{note}</dd>}</div>;
}

function duration(ms: number): string {
  const { value, unit } = durationParts(ms);
  return new Intl.NumberFormat(getLocale(), { style: 'unit', unit, unitDisplay: 'short', maximumFractionDigits: 1 }).format(value);
}

const percent = (value: number) => new Intl.NumberFormat(getLocale(), { style: 'percent', maximumFractionDigits: 0 }).format(value);
const tone = (status: string) => status === 'completed' || status === 'done' || status === 'approved' ? 'success'
  : status === 'failed' || status === 'cancelled' || status === 'rejected' || status === 'blocked' ? 'dangerTone' : 'warning';

export function OrgProfile({ target, role, onTab, onBack, onOpenAgent, onEditAgent, onOpenBudgets, onGo }: {
  target: ProfileRef & { tab: ProfileTab }; role: Role; onTab: (tab: ProfileTab) => void; onBack: () => void;
  onOpenAgent: (id: string) => void; onEditAgent: (agent: AgentProfile) => void; onOpenBudgets?: () => void; onGo: (view: OrgGo) => void;
}) {
  const [version, setVersion] = useState(0);
  const [load, retry] = useLoad(() => loadOrgSources(api.apiRequest), [version]);
  const sources = load.state === 'ready' ? load.value : null;
  const chart = useMemo(() => sources ? buildOrgChart({ ...sources, aliases: ALIASES }) : null, [sources]);
  const member = chart?.departments.flatMap((d) => d.members).find((m) => m.kind === target.kind && m.id === target.id) ?? null;
  const back = <button type="button" className="secondary backButton" onClick={onBack}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('org.profile.back')}</button>;
  if (load.state !== 'ready') return <div className="orgProfile">{back}<Loaded load={load} retry={retry}>{() => null}</Loaded></div>;
  if (!member || !sources || !chart) return <div className="orgProfile">{back}
    <div className="emptyState"><UserRound aria-hidden="true" /><h3>{t('org.profile.missingTitle')}</h3><p>{t('org.profile.missingText')}</p></div></div>;
  const tabs = PROFILE_TABS[member.kind] as readonly ProfileTab[];
  const tab = tabs.includes(target.tab) ? target.tab : 'overview';
  return <div className="orgProfile">
    {back}
    <ProfileHero member={member} sources={sources} role={role} onOpenAgent={onOpenAgent} onEditAgent={onEditAgent} onChanged={() => setVersion((v) => v + 1)} onTab={onTab} />
    <nav className="settingsTabs orgProfileTabs" aria-label={t('org.profile.sections')}>
      {tabs.map((value) => <button key={value} type="button" aria-current={tab === value ? 'page' : undefined} onClick={() => onTab(value)}>{t(`org.tab.${value}` as MessageKey)}</button>)}
    </nav>
    {member.agent && <AgentTabs tab={tab} member={member} agent={member.agent} sources={sources} onGo={onGo} onOpenBudgets={onOpenBudgets} onEditAgent={onEditAgent} onRan={() => setVersion((v) => v + 1)} />}
    {member.assistant && <AssistantTabs tab={tab} member={member} sources={sources} onGo={onGo} />}
    {member.person && <PersonTabs tab={tab} member={member} onGo={onGo} />}
  </div>;
}

function ProfileHero({ member, sources, role, onOpenAgent, onEditAgent, onChanged, onTab }: {
  member: OrgMember; sources: OrgSources; role: Role; onOpenAgent: (id: string) => void; onEditAgent: (agent: AgentProfile) => void; onChanged: () => void; onTab: (tab: ProfileTab) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const agent = member.agent;
  const paused = agent?.status === 'paused';
  async function togglePause() {
    if (!agent) return;
    setBusy(true); setNote(null);
    try {
      await agents.update(agent.id, { status: paused ? 'active' : 'paused', version: agent.version });
      setNote({ text: t(paused ? 'org.drawer.resumed' : 'org.drawer.paused', { name: agent.name }) }); onChanged();
    } catch (e) { setNote({ text: why(e, 'agents.err.save'), error: true }); }
    finally { setBusy(false); }
  }
  const owner = agent ? (agent.owner_user_id === api.currentUserId() ? t('agents.you') : sources.people?.find((p) => p.user_id === agent.owner_user_id)?.display_name ?? agent.owner_user_id) : null;
  return <section className="surface orgHero" aria-labelledby="org-profile-name">
    <div className="orgHeroMain">
      {member.kind === 'person' ? <span className="agentAvatar orgAvatar lg orgPerson" aria-hidden="true"><UserRound /></span> : <Avatar icon={member.icon} color={member.color} />}
      <div className="orgHeroText">
        <h2 id="org-profile-name" dir="auto">{member.name}</h2>
        <p className="orgHeroLine">
          <span className={`orgStatus is-${member.kind === 'person' ? 'person' : member.status}`}><span className="orgDot" aria-hidden="true" />{statusText(member)}</span>
          <span>{member.kind === 'person' ? roleText(member) : member.kind === 'agent' ? agentRole(member) : roleText(member)}</span>
          <span>{t('org.col.department')}: <bdi>{departmentLabel(member.department)}</bdi></span>
          {owner && <span>{t('org.drawer.owner')}: <bdi>{owner}</bdi></span>}
        </p>
        {member.description && <p className="orgHeroMission" dir="auto">{member.description}</p>}
      </div>
      <div className="actions orgHeroActions">
        {agent && <button type="button" onClick={() => onTab('work')} disabled={!agent.can_run}><Play aria-hidden="true" />{t('org.drawer.assign')}</button>}
        {agent && <button type="button" className="secondary" onClick={() => onOpenAgent(agent.id)}><MessageSquare aria-hidden="true" />{t('org.drawer.open')}</button>}
        {agent?.can_edit && <button type="button" className="secondary" onClick={() => onEditAgent(agent)}><Pencil aria-hidden="true" />{t('agents.edit')}</button>}
        {agent?.can_edit && <button type="button" className="secondary" disabled={busy} onClick={() => void togglePause()}>{paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}{paused ? t('agents.resume') : t('agents.pause')}</button>}
      </div>
    </div>
    {member.kind !== 'person' && <dl className="orgKpis orgHeroKpis">
      <Stat label={t('org.kpi.running')} value={member.running} tone={member.running ? 'sky' : 'neutral'} />
      <Stat label={t('org.kpi.approvals')} value={member.approvals} tone={member.approvals ? 'warn' : 'neutral'} />
      <Stat label={t('org.kpi.failed')} value={member.failed} tone={member.failed ? 'stop' : 'neutral'} />
      <Stat label={t('org.skills')} value={member.skills.length} />
      <Stat label={t('org.tools')} value={member.tools.length} />
      <Stat label={t('org.profile.notConnected')} value={member.tools.filter((c) => c.status !== 'connected').length} tone={member.tools.some((c) => c.status === 'error' || c.status === 'not_configured') ? 'warn' : 'neutral'} />
    </dl>}
    {agent && !agent.can_edit && <p className="muted">{t('org.drawer.readOnly')}</p>}
    {member.assistant && role !== 'owner' && <p className="muted">{t('org.drawer.assistantOwners')}</p>}
    {note && <Notice text={note.text} error={note.error} />}
  </section>;
}

// ---------------------------------------------------------------------------- agents

function AgentTabs({ tab, member, agent, sources, onGo, onOpenBudgets, onEditAgent, onRan }: {
  tab: ProfileTab; member: OrgMember; agent: AgentProfile; sources: OrgSources; onGo: (view: OrgGo) => void;
  onOpenBudgets?: () => void; onEditAgent: (agent: AgentProfile) => void; onRan: () => void;
}) {
  if (tab === 'work') return <AgentWork agent={agent} onGo={onGo} onOpenBudgets={onOpenBudgets} onRan={onRan} />;
  if (tab === 'skills') return <AgentSkills member={member} agent={agent} sources={sources} onGo={onGo} onEditAgent={onEditAgent} />;
  if (tab === 'approvals') return <AgentApprovals agent={agent} onGo={onGo} />;
  if (tab === 'activity') return <AgentActivity agent={agent} onGo={onGo} onEditAgent={onEditAgent} />;
  if (tab === 'performance') return <AgentPerformance agent={agent} />;
  return <AgentOverview member={member} agent={agent} sources={sources} onOpenBudgets={onOpenBudgets} />;
}

function AgentOverview({ member, agent, sources, onOpenBudgets }: { member: OrgMember; agent: AgentProfile; sources: OrgSources; onOpenBudgets?: () => void }) {
  const rules = autonomyRules(agent, sources.catalog);
  return <div className="orgPanels">
    <Panel title={t('org.profile.mission')}>
      <p dir="auto">{agent.description || t('agents.noDescription')}</p>
      <h4 className="orgSubhead">{t('agents.field.instructions')}</h4>
      {agent.instructions ? <p className="orgInstructionsFull" dir="auto">{agent.instructions}</p> : <p className="muted">{t('agents.noInstructions')}</p>}
    </Panel>
    <Panel title={t('org.profile.facts')}>
      <dl className="agentFacts orgFacts">
        <div><dt>{t('org.drawer.owner')}</dt><dd dir="auto">{agent.owner_user_id === api.currentUserId() ? t('agents.you') : sources.people?.find((p) => p.user_id === agent.owner_user_id)?.display_name ?? agent.owner_user_id}</dd></div>
        <div><dt>{t('agents.field.sharing')}</dt><dd>{t(`agents.sharing.${agent.sharing}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.field.category')}</dt><dd>{t(`agents.category.${agent.category}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.field.model')}</dt><dd>{t(`agents.model.${agent.model_route}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.field.knowledge')}</dt><dd>{t(`agents.knowledge.${agent.knowledge_mode}` as MessageKey)}{agent.knowledge_mode === 'selected' ? ` (${agent.knowledge_sources.length})` : ''}</dd></div>
        <div><dt>{t('agents.createdAt')}</dt><dd>{formatDateTime(agent.created_at)}</dd></div>
        <div><dt>{t('agents.updated')}</dt><dd>{formatDateTime(agent.updated_at)}</dd></div>
        <div><dt>{t('org.profile.version')}</dt><dd>{agent.version}</dd></div>
      </dl>
    </Panel>
    <Panel title={t('org.drawer.rules')} wide>
      <table className="orgRulesTable">
        <caption className="visuallyHidden">{t('org.drawer.rules')}</caption>
        <thead><tr><th scope="col">{t('org.profile.actionKind')}</th><th scope="col">{t('org.profile.level')}</th><th scope="col">{t('org.profile.meaning')}</th><th scope="col">{t('org.tools')}</th></tr></thead>
        <tbody>{rules.map((rule) => <tr key={rule.category} className={`level-${rule.level}`}>
          <th scope="row" data-label={t('org.profile.actionKind')}>{t(`agents.toolCategory.${rule.category}` as MessageKey)}</th>
          <td data-label={t('org.profile.level')}><span className={`statusBadge ${rule.level === 'auto' ? 'success' : rule.level === 'ask' ? 'warning' : 'neutral'}`}>{t(`agents.level.${rule.level}` as MessageKey)}</span></td>
          <td data-label={t('org.profile.meaning')}>{t(`agents.levelText.${rule.level}` as MessageKey)}{rule.floored ? ` ${t('agents.floorNote')}` : ''}</td>
          <td data-label={t('org.tools')} dir="ltr">{rule.tools.join(', ') || '—'}</td>
        </tr>)}</tbody>
      </table>
      {sources.policy
        ? <ul className="orgDrawerList">
            <li><small>{sources.policy.two_person_rule ? t('org.drawer.twoPersonOn') : t('org.drawer.twoPersonOff')}</small></li>
            <li><small>{sources.policy.medium_risk_requires_approval ? t('org.drawer.mediumOn') : t('org.drawer.mediumOff')}</small></li>
          </ul>
        : <p className="muted">{t('org.drawer.policyUnknown')}</p>}
    </Panel>
    <Panel title={t('org.profile.budget')}>
      <p className="muted">{t('org.profile.budgetGap')}</p>
      {onOpenBudgets && <button type="button" className="linkButton" onClick={onOpenBudgets}>{t('agents.openBudgets')}</button>}
    </Panel>
    <Panel title={t('org.drawer.automations')}>
      <p className="muted">{t('org.drawer.automationsGap')}</p>
    </Panel>
    {member.unavailable > 0 && <Panel title={t('org.drawer.lastError')}>
      <p className="orgWarn"><AlertTriangle aria-hidden="true" />{t('org.drawer.fixUnavailable', { names: [...agent.unavailable_tools, ...agent.unavailable_skills].join(t('common.listSeparator')) })}</p>
    </Panel>}
  </div>;
}

/** A paged list of an agent's tasks for some statuses; every task opens Tasks. */
const RUNNING = ['created', 'queued', 'running'] as const, WAITING = ['waiting_approval'] as const, FAILED = ['failed'] as const, ALL = [] as const;

function TaskList({ agentId, statuses, empty, onGo, version }: { agentId: string; statuses: readonly string[]; empty: string; onGo: (view: OrgGo) => void; version: number }) {
  const PAGE = 10;
  const [items, setItems] = useState<api.ApiTask[]>([]);
  const [more, setMore] = useState(false);
  const [paging, setPaging] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const path = useCallback((cursor: string | null) => statuses.length
    ? agentTasksByStatusPath(agentId, statuses, PAGE, cursor ? { cursor } : {}) : agentTasksPath(agentId, cursor, PAGE), [agentId, statuses]);
  const [load, retry] = useLoad(async () => {
    const rows = await api.apiRequest<api.ApiTask[]>(path(null), { method: 'GET' });
    const list = Array.isArray(rows) ? rows : [];
    setItems(list); setMore(list.length === PAGE);
    return list;
  }, [path, version]);
  async function next() {
    const last = items[items.length - 1];
    if (!last) return;
    setPaging(true); setPageError(null);
    try {
      const rows = await api.apiRequest<api.ApiTask[]>(path(last.id), { method: 'GET' });
      const list = Array.isArray(rows) ? rows : [];
      setItems((old) => [...old, ...list.filter((r) => !old.some((o) => o.id === r.id))]); setMore(list.length === PAGE);
    } catch (e) { setPageError(why(e, 'agents.err.tasks')); }
    finally { setPaging(false); }
  }
  return <Loaded load={load} retry={retry}>{() => items.length === 0 ? <p className="muted">{empty}</p> : <>
    <ul className="agentTaskList orgTaskList">{items.map((task) => <li key={task.id}>
      <button type="button" className="linkButton" dir="auto" onClick={() => onGo('tasks')} title={t('org.drawer.openTask')}>{task.title}</button>
      <span className={`statusBadge ${tone(task.status)}`}>{statusLabel(task.status)}</span>
      <time dateTime={task.created_at}>{formatDateTime(task.created_at)}</time>
    </li>)}</ul>
    {pageError && <Notice text={pageError} error />}
    {more && <div className="actions"><button type="button" className="secondary" disabled={paging} onClick={() => void next()}>{paging ? t('common.loading') : t('agents.loadMore')}</button></div>}
  </>}</Loaded>;
}

function AgentWork({ agent, onGo, onOpenBudgets, onRan }: { agent: AgentProfile; onGo: (view: OrgGo) => void; onOpenBudgets?: () => void; onRan: () => void }) {
  const [version, setVersion] = useState(0);
  return <div className="orgPanels">
    <div className="surface orgPanel"><AssignTask agent={agent} onGo={onGo} onOpenBudgets={onOpenBudgets} onRan={() => { setVersion((v) => v + 1); onRan(); }} /></div>
    <Panel title={t('org.work.running')}><TaskList agentId={agent.id} statuses={RUNNING} empty={t('org.work.noRunning')} onGo={onGo} version={version} /></Panel>
    <Panel title={t('org.work.waiting')} action={<button type="button" className="linkButton" onClick={() => onGo('approvals')}>{t('org.strip.open')}</button>}>
      <TaskList agentId={agent.id} statuses={WAITING} empty={t('org.work.noWaiting')} onGo={onGo} version={version} /></Panel>
    <Panel title={t('org.work.failed')}><TaskList agentId={agent.id} statuses={FAILED} empty={t('org.work.noFailed')} onGo={onGo} version={version} /></Panel>
    <Panel title={t('org.work.all')} wide action={<button type="button" className="linkButton" onClick={() => onGo('tasks')}>{t('agents.allTasks')}</button>}>
      <TaskList agentId={agent.id} statuses={ALL} empty={t('org.drawer.noTasks')} onGo={onGo} version={version} /></Panel>
  </div>;
}

function AgentSkills({ member, agent, sources, onGo, onEditAgent }: { member: OrgMember; agent: AgentProfile; sources: OrgSources; onGo: (view: OrgGo) => void; onEditAgent: (agent: AgentProfile) => void }) {
  const [load, retry] = useLoad<AgentSkillLoadout>(() => agents.skills(agent.id), [agent.id]);
  const catalogTools = new Map((sources.catalog?.tools ?? []).map((tool) => [tool.name, tool]));
  const tools = [...new Set(['anum.respond', ...agent.allowed_tools])];
  const connection = (name: string) => toolChips([name], sources.setup).chips[0] ?? null;
  return <div className="orgPanels">
    <Panel title={t('org.drawer.skills', { count: agent.allowed_skills.length })} wide>
      <Loaded load={load} retry={retry}>{(loadout) => loadout.items.length === 0
        ? <p className="muted">{t('org.drawer.noSkills')} {agent.can_edit && <button type="button" className="linkButton" onClick={() => onEditAgent(agent)}>{t('agents.edit')}</button>}</p>
        : <ul className="orgSkillList">{loadout.items.map((skill) => <li key={skill.id} className="surface">
          <div className="orgSkillHead"><strong dir="auto">{skill.name}</strong>
            <span className={`statusBadge ${skill.state === 'ready' ? 'success' : 'warning'}`}>{t(`agentSkills.state.${skill.state}` as MessageKey)}</span></div>
          {skill.description && <p dir="auto">{skill.description}</p>}
          <p className="muted">{[skill.source === 'installed' ? t('agents.skill.installed') : t('agents.skill.builtin'), skill.version ? `v${skill.version}` : '',
            skill.activation === 'always' ? t('org.skill.always') : t('org.skill.onRequest', { words: skill.triggers.slice(0, 4).join(t('common.listSeparator')) || '—' })].filter(Boolean).join(' · ')}</p>
          {skill.tools.length > 0 && <ol className="orgSteps" aria-label={t('org.skill.steps')}>{skill.tools.map((tool) => {
            const chip = connection(tool.name);
            return <li key={tool.name}><span dir="ltr">{tool.name}</span>
              <span className={`statusBadge ${tool.allowed && tool.registered ? 'success' : 'warning'}`}>{!tool.registered ? t('org.skill.toolMissing') : tool.allowed ? t('org.skill.toolAllowed') : t('org.skill.toolNotAllowed')}</span>
              {chip && <ToolButton chip={chip} onGo={onGo} />}</li>;
          })}</ol>}
          {skill.reason && <p className="orgWarn"><AlertTriangle aria-hidden="true" />{skill.reason}</p>}
        </li>)}</ul>}</Loaded>
    </Panel>
    <Panel title={t('org.profile.allTools', { count: tools.length })} wide>
      <ul className="orgToolList">{tools.map((name) => {
        const info = catalogTools.get(name);
        const chip = connection(name);
        const missing = agent.unavailable_tools.includes(name);
        return <li key={name}>
          <div><strong dir="ltr">{name}</strong>{info && <small className="muted">{info.description}</small>}</div>
          <span className="muted">{info ? `${t(`agents.toolCategory.${info.category}` as MessageKey)} · ${t('org.profile.risk', { risk: riskLabel(info.risk_level) })}` : ''}</span>
          <span>{chip ? <ToolButton chip={chip} onGo={onGo} /> : <span className="orgTool tool-builtin"><span className="orgDot" aria-hidden="true" />{t('org.tool.builtin')}</span>}</span>
          <span className="orgFix">{missing
            ? agent.can_edit ? <button type="button" className="linkButton" onClick={() => onEditAgent(agent)}>{t('org.profile.fixRemove')}</button> : <small>{t('org.profile.fixAskOwner')}</small>
            : chip && chip.status !== 'connected' ? <button type="button" className="linkButton" onClick={() => onGo('integrations')}>{t('org.profile.fixConnect', { name: chipLabel(chip) })}</button>
            : <small className="muted">{t('org.profile.fixNone')}</small>}</span>
        </li>;
      })}</ul>
      {member.builtin > 0 && <p className="muted">{t('org.drawer.builtin', { count: member.builtin })}</p>}
    </Panel>
  </div>;
}

function AgentApprovals({ agent, onGo }: { agent: AgentProfile; onGo: (view: OrgGo) => void }) {
  const [load, retry] = useLoad(async () => {
    const [tasks, approvals] = await Promise.all([
      api.apiRequest<api.ApiTask[]>(agentTasksPath(agent.id, null, 100), { method: 'GET' }),
      api.apiRequest<api.ApiApproval[]>('/api/v1/approvals', { method: 'GET' }),
    ]);
    return approvalsForTasks(Array.isArray(approvals) ? approvals : [], new Set((Array.isArray(tasks) ? tasks : []).map((task) => task.id)))
      .map((a) => ({ ...a, title: (tasks as api.ApiTask[]).find((task) => task.id === a.task_id)?.title ?? a.task_id }));
  }, [agent.id]);
  return <div className="orgPanels">
    <Panel title={t('org.tab.approvals')} wide action={<button type="button" className="linkButton" onClick={() => onGo('approvals')}>{t('org.strip.open')}</button>}>
      <p className="muted">{t('org.approvals.intro')}</p>
      <Loaded load={load} retry={retry}>{(rows) => rows.length === 0 ? <p className="muted">{t('org.approvals.none')}</p>
        : <table className="orgRulesTable orgApprovals">
          <caption className="visuallyHidden">{t('org.tab.approvals')}</caption>
          <thead><tr><th scope="col">{t('org.approvals.task')}</th><th scope="col">{t('org.approvals.action')}</th><th scope="col">{t('org.approvals.risk')}</th><th scope="col">{t('org.col.status')}</th><th scope="col">{t('org.approvals.when')}</th></tr></thead>
          <tbody>{rows.map((a) => <tr key={a.id}>
            <th scope="row" data-label={t('org.approvals.task')} dir="auto">{a.title}<small className="muted" dir="auto">{a.reason}</small></th>
            <td data-label={t('org.approvals.action')} dir="ltr">{a.action}</td>
            <td data-label={t('org.approvals.risk')}>{riskLabel(a.risk_level)}</td>
            <td data-label={t('org.col.status')}><span className={`statusBadge ${tone(a.status)}`}>{t(`org.approvals.status.${a.status}` as MessageKey)}</span></td>
            <td data-label={t('org.approvals.when')}>{formatDateTime(a.created_at)}{a.decided_at ? <small className="muted">{t('org.approvals.decided', { time: formatDateTime(a.decided_at) })}</small> : null}</td>
          </tr>)}</tbody>
        </table>}</Loaded>
    </Panel>
  </div>;
}

function AgentActivity({ agent, onGo, onEditAgent }: { agent: AgentProfile; onGo: (view: OrgGo) => void; onEditAgent: (agent: AgentProfile) => void }) {
  const [load, retry] = useLoad(async () => {
    const tasks = await api.apiRequest<api.ApiTask[]>(agentTasksPath(agent.id, null, 8), { method: 'GET' });
    const list = Array.isArray(tasks) ? tasks : [];
    const runs = await Promise.all(list.slice(0, 5).map(async (task) => {
      try { return { task, run: await api.getLatestRun(task.id) }; }
      catch (e) { if (e instanceof Error && 'status' in e && (e as { status: number }).status === 404) return { task, run: null }; throw e; }
    }));
    return { list, runs };
  }, [agent.id]);
  return <div className="orgPanels">
    <Loaded load={load} retry={retry}>{({ list, runs }) => {
      const done = runs.find((r) => r.task.status === 'completed' && r.run);
      const failed = runs.find((r) => r.task.status === 'failed') ?? (list.find((task) => task.status === 'failed') ? { task: list.find((task) => task.status === 'failed')!, run: null } : undefined);
      const result = done?.run ? done.run.result ?? done.run.steps[done.run.steps.length - 1]?.summary ?? null : null;
      const failure = failed?.run ? failed.run.steps[failed.run.steps.length - 1]?.summary ?? failed.run.result ?? null : null;
      return <>
        <Panel title={t('org.drawer.latest')}>
          {result ? <><p className="agentAnswer" dir="auto">{result}</p><small className="muted" dir="auto">{done!.task.title} · {formatDateTime(done!.task.updated_at)}</small></> : <p className="muted">{t('org.drawer.noResult')}</p>}
        </Panel>
        <Panel title={t('org.drawer.lastError')}>
          {[...agent.unavailable_tools, ...agent.unavailable_skills].length > 0 && <p className="orgWarn"><AlertTriangle aria-hidden="true" />{t('org.drawer.fixUnavailable', { names: [...agent.unavailable_tools, ...agent.unavailable_skills].join(t('common.listSeparator')) })}
            {agent.can_edit && <> <button type="button" className="linkButton" onClick={() => onEditAgent(agent)}>{t('agents.edit')}</button></>}</p>}
          {failed ? <><p><strong dir="auto">{failed.task.title}</strong> · <time dateTime={failed.task.updated_at}>{formatDateTime(failed.task.updated_at)}</time></p>
            {failure && <p dir="auto">{failure}</p>}<p className="muted">{t('org.drawer.fixFailed')}</p>
            <button type="button" className="linkButton" onClick={() => onGo('tasks')}>{t('org.drawer.openTask')}</button></>
            : <p className="muted">{t('org.drawer.noError')}</p>}
        </Panel>
        <Panel title={t('org.activity.runs')} wide>
          {runs.length === 0 ? <p className="muted">{t('org.drawer.noTasks')}</p> : <ol className="orgTimeline">{runs.map(({ task, run }) => <li key={task.id}>
            <div className="orgTimelineHead"><strong dir="auto">{task.title}</strong><span className={`statusBadge ${tone(task.status)}`}>{statusLabel(task.status)}</span>
              <time dateTime={task.updated_at} className="muted">{formatDateTime(task.updated_at)}</time></div>
            {run ? run.steps.length > 0 ? <ol className="orgRunSteps">{run.steps.map((step) => <li key={step.id}><span className="muted">{statusLabel(step.type)}</span> <span dir="auto">{step.summary}</span></li>)}</ol>
              : <p className="muted">{run.result ?? t('org.activity.noSteps')}</p>
              : <p className="muted">{t('org.activity.neverRan')}</p>}
          </li>)}</ol>}
        </Panel>
      </>;
    }}</Loaded>
  </div>;
}

function AgentPerformance({ agent }: { agent: AgentProfile }) {
  const [load, retry] = useLoad(async () => {
    const now = Date.now();
    const [week, month, completed] = await Promise.all([
      api.apiRequest<{ by_status: Record<string, number> }>(summaryPath(agent.id, 7, now), { method: 'GET' }),
      api.apiRequest<{ by_status: Record<string, number> }>(summaryPath(agent.id, 30, now), { method: 'GET' }),
      api.apiRequest<api.ApiTask[]>(agentTasksByStatusPath(agent.id, ['completed'], 100, { created_after: new Date(now - 30 * 86_400_000).toISOString() }), { method: 'GET' }),
    ]);
    return { week: taskStats(week?.by_status ?? {}), month: taskStats(month?.by_status ?? {}), avg: averageDurationMs(Array.isArray(completed) ? completed : []) };
  }, [agent.id]);
  const block = (title: string, stats: TaskStats, avg: number | null | undefined) => <Panel title={title}>
    <dl className="orgKpis">
      <Stat label={t('org.perf.total')} value={stats.total} />
      <Stat label={t('org.perf.completed')} value={stats.completed} tone={stats.completed ? 'ok' : 'neutral'} />
      <Stat label={t('org.perf.failed')} value={stats.failed} tone={stats.failed ? 'stop' : 'neutral'} />
      <Stat label={t('org.perf.success')} value={stats.successRate === null ? '—' : percent(stats.successRate)} note={stats.successRate === null ? t('org.perf.noFinished') : undefined} />
      {avg !== undefined && <Stat label={t('org.perf.duration')} value={avg === null ? '—' : duration(avg)} note={t('org.perf.durationNote')} />}
    </dl>
    {stats.total > 0 && <div className="orgBars" role="img" aria-label={t('org.perf.barsLabel', { completed: stats.completed, failed: stats.failed, cancelled: stats.cancelled, open: stats.open })}>
      {(['completed', 'failed', 'cancelled', 'open'] as const).map((key) => stats[key] > 0 && <span key={key} className={`orgBar bar-${key}`} style={{ flexGrow: stats[key] }} title={`${t(`org.perf.${key}` as MessageKey)}: ${stats[key]}`} />)}
    </div>}
    {stats.total > 0 && <p className="orgBarLegend">{(['completed', 'failed', 'cancelled', 'open'] as const).map((key) => <span key={key}><span className={`orgBarKey bar-${key}`} aria-hidden="true" />{t(`org.perf.${key}` as MessageKey)} {stats[key]}</span>)}</p>}
  </Panel>;
  return <div className="orgPanels">
    <Loaded load={load} retry={retry}>{({ week, month, avg }) => <>
      {block(t('org.perf.week'), week, undefined)}
      {block(t('org.perf.month'), month, avg)}
      <Panel title={t('org.perf.notMeasured')} wide><p className="muted">{t('org.perf.notMeasuredText')}</p></Panel>
    </>}</Loaded>
  </div>;
}

// ---------------------------------------------------------------------------- assistants and people

function WorkItems({ assigneeType, assigneeId, onGo }: { assigneeType: 'assistant' | 'user'; assigneeId: string; onGo: (view: OrgGo) => void }) {
  const [offset, setOffset] = useState(0);
  const [load, retry] = useLoad(() => api.apiRequest<WorkItemPage>(workQuery({ status: 'all', sort: 'updated', assigneeType, assigneeId, limit: 20, offset }), { method: 'GET' }), [assigneeType, assigneeId, offset]);
  return <Loaded load={load} retry={retry}>{(page) => !page || !Array.isArray(page.items) || page.items.length === 0 ? <p className="muted">{t('org.work.noItems')}</p> : <>
    <p className="muted">{t('org.work.itemsTotal', { count: page.total })}</p>
    <ul className="agentTaskList orgTaskList">{page.items.map((item) => <li key={item.id}>
      <button type="button" className="linkButton" dir="auto" onClick={() => onGo('team')}>{item.title}</button>
      <span className={`statusBadge ${tone(item.status)}`}>{t(`team.status.${item.status}` as MessageKey)}</span>
      <span className="muted">{item.due_at ? t('org.work.due', { time: formatDateTime(item.due_at) }) : t(`team.priority.${item.priority}` as MessageKey)}{item.overdue ? ` · ${t('org.work.overdue')}` : ''}</span>
    </li>)}</ul>
    <div className="actions">
      {offset > 0 && <button type="button" className="secondary" onClick={() => setOffset(Math.max(0, offset - 20))}>{t('org.work.previous')}</button>}
      {page.next_offset !== null && <button type="button" className="secondary" onClick={() => setOffset(page.next_offset ?? 0)}>{t('org.work.next')}</button>}
    </div>
  </>}</Loaded>;
}

function AssistantTabs({ tab, member, sources, onGo }: { tab: ProfileTab; member: OrgMember; sources: OrgSources; onGo: (view: OrgGo) => void }) {
  const a = member.assistant!;
  if (tab === 'work') return <div className="orgPanels"><Panel title={t('org.work.assigned')} wide action={<button type="button" className="linkButton" onClick={() => onGo('team')}>{t('org.drawer.openTeam')}</button>}>
    <WorkItems assigneeType="assistant" assigneeId={a.id} onGo={onGo} /></Panel></div>;
  if (tab === 'skills') return <div className="orgPanels"><Panel title={t('org.profile.allTools', { count: a.allowed_tools.length })} wide>
    <ul className="orgToolList">{a.allowed_tools.map((name) => {
      const chip = toolChips([name], sources.setup).chips[0] ?? null;
      return <li key={name}><div><strong dir="ltr">{name}</strong></div><span />
        <span>{chip ? <ToolButton chip={chip} onGo={onGo} /> : <span className="orgTool tool-builtin"><span className="orgDot" aria-hidden="true" />{t('org.tool.builtin')}</span>}</span>
        <span className="orgFix">{chip && chip.status !== 'connected' ? <button type="button" className="linkButton" onClick={() => onGo('integrations')}>{t('org.profile.fixConnect', { name: chipLabel(chip) })}</button> : <small className="muted">{t('org.profile.fixNone')}</small>}</span>
      </li>;
    })}</ul></Panel></div>;
  return <div className="orgPanels">
    <Panel title={t('org.profile.mission')}>
      {a.instructions ? <p className="orgInstructionsFull" dir="auto">{a.instructions}</p> : <p className="muted">{t('agents.noInstructions')}</p>}
    </Panel>
    <Panel title={t('org.profile.facts')}>
      <dl className="agentFacts orgFacts">
        <div><dt>{t('org.col.status')}</dt><dd>{statusText(member)}</dd></div>
        <div><dt>{t('org.profile.autonomy')}</dt><dd>{t(`assistants.autonomy.${a.autonomy}` as MessageKey)}</dd></div>
        <div><dt>{t('org.drawer.keywords')}</dt><dd dir="auto">{a.routing_keywords.join(', ') || t('agents.none')}</dd></div>
        <div><dt>{t('org.drawer.channels')}</dt><dd>{a.inbound_channels.join(', ') || t('agents.none')}</dd></div>
        <div><dt>{t('agents.createdAt')}</dt><dd>{formatDateTime(a.created_at)}</dd></div>
        <div><dt>{t('agents.updated')}</dt><dd>{formatDateTime(a.updated_at)}</dd></div>
      </dl>
      <button type="button" className="linkButton" onClick={() => onGo('assistants')}>{t('org.drawer.openAssistants')}</button>
    </Panel>
  </div>;
}

function PersonTabs({ tab, member, onGo }: { tab: ProfileTab; member: OrgMember; onGo: (view: OrgGo) => void }) {
  const p = member.person!;
  const [load, retry] = useLoad(() => api.apiRequest<Workload>('/api/v1/work-items/workload', { method: 'GET' }), [p.user_id]);
  if (tab === 'work') return <div className="orgPanels"><Panel title={t('org.work.assigned')} wide action={<button type="button" className="linkButton" onClick={() => onGo('team')}>{t('org.drawer.openTeam')}</button>}>
    <WorkItems assigneeType="user" assigneeId={p.user_id} onGo={onGo} /></Panel></div>;
  return <div className="orgPanels">
    <Panel title={t('org.drawer.person')}>
      <dl className="agentFacts orgFacts">
        <div><dt>{t('org.col.role')}</dt><dd dir="auto">{roleText(member) || t('agents.none')}</dd></div>
        {p.email && <div><dt>{t('org.drawer.email')}</dt><dd dir="ltr">{p.email}</dd></div>}
        <div><dt>{t('org.drawer.timezone')}</dt><dd dir="ltr">{p.timezone}</dd></div>
        <div><dt>{t('org.drawer.capacity')}</dt><dd>{p.capacity_hours === null ? t('agents.none') : t('org.drawer.hours', { count: p.capacity_hours })}</dd></div>
        <div><dt>{t('org.drawer.assignable')}</dt><dd>{p.assignable ? t('org.drawer.yes') : t('org.drawer.no')}</dd></div>
      </dl>
    </Panel>
    <Panel title={t('org.person.workload')}>
      <Loaded load={load} retry={retry}>{(workload) => {
        const row = workload?.rows?.find((r) => r.assignee_type === 'user' && r.assignee_id === p.user_id);
        if (!row) return <p className="muted">{t('org.person.noWorkload')}</p>;
        return <dl className="orgKpis">
          <Stat label={t('org.person.open')} value={row.open} />
          <Stat label={t('team.status.in_progress')} value={row.in_progress} tone={row.in_progress ? 'sky' : 'neutral'} />
          <Stat label={t('team.status.blocked')} value={row.blocked} tone={row.blocked ? 'stop' : 'neutral'} />
          <Stat label={t('org.work.overdue')} value={row.overdue} tone={row.overdue ? 'warn' : 'neutral'} />
          <Stat label={t('org.person.load')} value={row.load_percent === null ? '—' : percent(row.load_percent / 100)} note={t('org.person.loadNote', { hours: row.estimate_hours })} />
        </dl>;
      }}</Loaded>
    </Panel>
  </div>;
}

