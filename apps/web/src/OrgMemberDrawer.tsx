// A member's complete profile from the org chart (docs/org-chart.md#member-drawer): what it does,
// skills, tools and their connections, what it may do alone and what waits for approval, its
// current and recent tasks, latest result and last error, who owns it, and the actions the API
// already supports (open, edit, pause or resume, assign a task through the governed task path).
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { AlertTriangle, CircleDot, ExternalLink, Pause, Pencil, Play, RefreshCw } from 'lucide-react';
import type { AgentProfile } from '@anum/contracts';
import { Drawer } from './Dialog';
import * as api from './lib/api';
import { agentsApi } from './lib/agents';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { statusLabel } from './lib/labels';
import { agentTasksPath, autonomyRules, type OrgMember, type OrgSources } from './lib/orgChart';
import {
  chipLabel, chipStatus, countsText, departmentLabel, roleText, Section, statusText, ToolButton, type OrgGo,
} from './OrgChartView';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);
const PAGE = 10;

function Notice({ text, error }: { text: string; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{text}</p>;
}

/** Why a section could not load: who can see it, or what failed. */
function why(error: unknown, action: MessageKey): string {
  if (isPermissionDenied(error)) return t('org.drawer.denied', { message: describeError(error) });
  return t(action, { reason: describeError(error) });
}

export function OrgMemberDrawer({ member, sources, role, onClose, onOpenAgent, onEditAgent, onGo, onOpenBudgets, onChanged }: {
  member: OrgMember; sources: OrgSources; role: 'owner' | 'member' | 'viewer' | null; onClose: () => void;
  onOpenAgent: (id: string) => void; onEditAgent: (agent: AgentProfile) => void; onGo: (view: OrgGo) => void;
  onOpenBudgets?: () => void; onChanged: () => void;
}) {
  return <Drawer title={member.name} onClose={onClose}>
    <div className="orgDrawer">
      <p className="orgDrawerLead">
        <span className={`orgStatus is-${member.kind === 'person' ? 'person' : member.status}`}><span className="orgDot" aria-hidden="true" />{statusText(member)}</span>
        <span>{t(`org.kind.${member.kind}` as MessageKey)} · <bdi>{departmentLabel(member.department)}</bdi></span>
      </p>
      {member.agent && <AgentProfilePanel member={member} agent={member.agent} sources={sources} onClose={onClose} onOpenAgent={onOpenAgent}
        onEditAgent={onEditAgent} onGo={onGo} onOpenBudgets={onOpenBudgets} onChanged={onChanged} />}
      {member.assistant && <AssistantPanel member={member} onGo={onGo} role={role} />}
      {member.person && <PersonPanel member={member} onGo={onGo} />}
    </div>
  </Drawer>;
}

function AgentProfilePanel({ member, agent: initial, sources, onClose, onOpenAgent, onEditAgent, onGo, onOpenBudgets, onChanged }: {
  member: OrgMember; agent: AgentProfile; sources: OrgSources; onClose: () => void; onOpenAgent: (id: string) => void;
  onEditAgent: (agent: AgentProfile) => void; onGo: (view: OrgGo) => void; onOpenBudgets?: () => void; onChanged: () => void;
}) {
  const [agent, setAgent] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [tasksVersion, setTasksVersion] = useState(0);
  const id = useId();
  const paused = agent.status === 'paused';
  const ownerName = agent.owner_user_id === api.currentUserId() ? t('agents.you')
    : sources.people?.find((p) => p.user_id === agent.owner_user_id)?.display_name ?? agent.owner_user_id;
  async function togglePause() {
    setBusy(true); setNote(null);
    try {
      const next = await agents.update(agent.id, { status: paused ? 'active' : 'paused', version: agent.version });
      setAgent(next); onChanged();
      setNote({ text: t(paused ? 'org.drawer.resumed' : 'org.drawer.paused', { name: agent.name }) });
    } catch (e) { setNote({ text: why(e, 'agents.err.save'), error: true }); }
    finally { setBusy(false); }
  }
  const rules = autonomyRules(agent, sources.catalog);
  const catalogSkills = new Map((sources.catalog?.skills ?? []).map((s) => [s.id, s]));
  const fixes = [...agent.unavailable_tools, ...agent.unavailable_skills];
  return <>
    <div className="actions orgDrawerActions">
      <button type="button" onClick={() => { onClose(); onOpenAgent(agent.id); }}><ExternalLink aria-hidden="true" />{t('org.drawer.open')}</button>
      {agent.can_edit && <button type="button" className="secondary" onClick={() => { onClose(); onEditAgent(agent); }}><Pencil aria-hidden="true" />{t('agents.edit')}</button>}
      {agent.can_edit && <button type="button" className="secondary" disabled={busy} onClick={() => void togglePause()}>
        {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}{paused ? t('agents.resume') : t('agents.pause')}</button>}
    </div>
    {note && <Notice text={note.text} error={note.error} />}
    {!agent.can_edit && <p className="muted">{t('org.drawer.readOnly')}</p>}

    <Section id={`${id}-what`} title={t('org.drawer.what')}>
      <p dir="auto">{agent.description || t('agents.noDescription')}</p>
      <details className="orgInstructions"><summary>{t('agents.field.instructions')}</summary>
        {agent.instructions ? <p dir="auto">{agent.instructions}</p> : <p className="muted">{t('agents.noInstructions')}</p>}</details>
      <dl className="agentFacts">
        <div><dt>{t('org.drawer.owner')}</dt><dd dir="auto">{ownerName}</dd></div>
        <div><dt>{t('agents.field.sharing')}</dt><dd>{t(`agents.sharing.${agent.sharing}` as MessageKey)}</dd></div>
        <div><dt>{t('org.col.status')}</dt><dd>{t(`agents.status.${agent.status}` as MessageKey)} · {countsText(member)}</dd></div>
        <div><dt>{t('agents.field.model')}</dt><dd>{t(`agents.model.${agent.model_route}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.field.knowledge')}</dt><dd>{t(`agents.knowledge.${agent.knowledge_mode}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.updated')}</dt><dd>{formatDateTime(agent.updated_at)}</dd></div>
      </dl>
    </Section>

    <AssignTask agent={agent} onRan={() => { setTasksVersion((v) => v + 1); onChanged(); }} onGo={onGo} onOpenBudgets={onOpenBudgets} />

    <Section id={`${id}-skills`} title={t('org.drawer.skills', { count: agent.allowed_skills.length })}>
      {agent.allowed_skills.length === 0 ? <p className="muted">{t('org.drawer.noSkills')}</p>
        : <ul className="orgDrawerList">{agent.allowed_skills.map((skillId) => {
          const skill = catalogSkills.get(skillId);
          const missing = agent.unavailable_skills.includes(skillId);
          return <li key={skillId}><strong dir="auto">{skill?.name ?? skillId}</strong>
            {skill?.description && <small dir="auto">{skill.description}</small>}
            {missing && <small className="fieldError">{t('org.drawer.skillMissing')}</small>}</li>;
        })}</ul>}
    </Section>

    <Section id={`${id}-tools`} title={t('org.drawer.tools')}>
      {member.tools.length === 0 && <p className="muted">{t('org.drawer.noTools')}</p>}
      {member.tools.length > 0 && <ul className="orgDrawerList">{member.tools.map((chip) => <li key={chip.key}>
        <ToolButton chip={chip} onGo={onGo} />
        <small dir="ltr">{chip.tools.join(', ')}</small>
        <small>{t(`org.toolHint.${chip.status}` as MessageKey, { name: chipLabel(chip), status: chipStatus(chip) })}</small>
      </li>)}</ul>}
      <p className="muted">{t('org.drawer.builtin', { count: member.builtin + 1 })}</p>
      {!sources.setup && <p className="muted">{t('org.drawer.setupUnknown')}</p>}
    </Section>

    <Section id={`${id}-rules`} title={t('org.drawer.rules')}>
      <ul className="orgDrawerList">{rules.map((rule) => <li key={rule.category}>
        <strong>{t(`agents.toolCategory.${rule.category}` as MessageKey)}: {t(`agents.level.${rule.level}` as MessageKey)}</strong>
        <small>{t(`agents.levelText.${rule.level}` as MessageKey)}{rule.floored ? ` ${t('agents.floorNote')}` : ''}</small>
        {rule.tools.length > 0 && <small dir="ltr">{rule.tools.join(', ')}</small>}
      </li>)}</ul>
      {sources.policy
        ? <ul className="orgDrawerList">
            <li><small>{sources.policy.two_person_rule ? t('org.drawer.twoPersonOn') : t('org.drawer.twoPersonOff')}</small></li>
            <li><small>{sources.policy.medium_risk_requires_approval ? t('org.drawer.mediumOn') : t('org.drawer.mediumOff')}</small></li>
          </ul>
        : <p className="muted">{t('org.drawer.policyUnknown')}</p>}
    </Section>

    <AgentTasks agent={agent} version={tasksVersion} onGo={onGo} fixes={fixes} />

    <Section id={`${id}-auto`} title={t('org.drawer.automations')}>
      <p className="muted">{t('org.drawer.automationsGap')}</p>
      <button type="button" className="linkButton" onClick={() => onGo('automation')}>{t('org.drawer.openAutomation')}</button>
    </Section>
  </>;
}

function AssignTask({ agent, onRan, onGo, onOpenBudgets }: { agent: AgentProfile; onRan: () => void; onGo: (view: OrgGo) => void; onOpenBudgets?: () => void }) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<api.RunTaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const fieldId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true); setError(null); setBudget(null); setResult(null);
    try { setResult(await api.createAndRunTask(prompt.trim(), undefined, agent.id)); setPrompt(''); onRan(); }
    catch (e) { if (isBudgetExceeded(e)) setBudget(budgetMessage(e)); else setError(why(e, 'agents.err.run')); }
    finally { setBusy(false); }
  }
  return <Section id={`${fieldId}-h`} title={t('org.drawer.assign')}>
    {agent.can_run ? <form onSubmit={(e) => void submit(e)}>
      <label className="field"><span>{t('agents.run.label', { name: agent.name })}</span>
        <textarea id={fieldId} className="short" value={prompt} maxLength={8000} dir="auto" placeholder={t('agents.run.placeholder')} onChange={(e) => setPrompt(e.target.value)} /></label>
      <p className="muted">{t('org.drawer.assignNote')}</p>
      <div className="actions"><button type="submit" disabled={busy || !prompt.trim()}><Play aria-hidden="true" />{busy ? t('agents.run.running') : t('agents.run.submit')}</button></div>
    </form> : <p className="muted">{agent.status === 'paused' ? t('agents.run.paused') : t('agents.run.noPermission')}</p>}
    {error && <Notice text={error} error />}
    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('common.budgetReached', { message: budget })} {onOpenBudgets && <button type="button" className="linkButton" onClick={onOpenBudgets}>{t('agents.openBudgets')}</button>}</p>}
    {result && <div className="agentRunResult" role="status">
      <p><strong>{statusLabel(result.task.status)}</strong> · <span dir="auto">{result.task.title}</span></p>
      {result.approval ? <p>{t('agents.run.waiting')} <button type="button" className="linkButton" onClick={() => onGo('approvals')}>{t('agents.run.openApprovals')}</button></p>
        : result.run.result && <p dir="auto">{result.run.result}</p>}
    </div>}
  </Section>;
}

function AgentTasks({ agent, version, onGo, fixes }: { agent: AgentProfile; version: number; onGo: (view: OrgGo) => void; fixes: string[] }) {
  const [tasks, setTasks] = useState<api.ApiTask[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(true);
  const [paging, setPaging] = useState(false);
  const [latest, setLatest] = useState<{ result: string | null; failure: { task: api.ApiTask; summary: string | null } | null; state: 'loading' | 'ready' | 'error' }>({ result: null, failure: null, state: 'loading' });
  const id = useId();
  const load = useCallback(async () => {
    setState('loading'); setError(null);
    try {
      const page = await api.apiRequest<api.ApiTask[]>(agentTasksPath(agent.id, null, PAGE), { method: 'GET' });
      const list = Array.isArray(page) ? page : [];
      setTasks(list); setMore(list.length === PAGE); setState('ready');
      const done = list.find((task) => task.status === 'completed');
      const failed = list.find((task) => task.status === 'failed');
      try {
        const run = async (task: api.ApiTask | undefined) => {
          if (!task) return null;
          try { return await api.getLatestRun(task.id); } catch (e) { if (e instanceof Error && 'status' in e && (e as { status: number }).status === 404) return null; throw e; }
        };
        const [doneRun, failedRun] = await Promise.all([run(done), run(failed)]);
        setLatest({
          result: doneRun ? doneRun.result ?? doneRun.steps[doneRun.steps.length - 1]?.summary ?? null : null,
          failure: failed ? { task: failed, summary: failedRun ? failedRun.steps[failedRun.steps.length - 1]?.summary ?? failedRun.result ?? null : null } : null,
          state: 'ready',
        });
      } catch { setLatest({ result: null, failure: failed ? { task: failed, summary: null } : null, state: 'error' }); }
    } catch (e) { setError(why(e, 'agents.err.tasks')); setState('error'); setLatest((old) => ({ ...old, state: 'error' })); }
  }, [agent.id]);
  useEffect(() => { void load(); }, [load, version]);
  async function loadMore() {
    const last = tasks[tasks.length - 1];
    if (!last) return;
    setPaging(true);
    try {
      const page = await api.apiRequest<api.ApiTask[]>(agentTasksPath(agent.id, last.id, PAGE), { method: 'GET' });
      const list = Array.isArray(page) ? page : [];
      setTasks((old) => [...old, ...list.filter((task) => !old.some((o) => o.id === task.id))]); setMore(list.length === PAGE);
    } catch (e) { setError(why(e, 'agents.err.tasks')); }
    finally { setPaging(false); }
  }
  const tone = (status: string) => status === 'completed' ? 'success' : status === 'failed' || status === 'cancelled' ? 'dangerTone' : 'warning';
  return <>
    <Section id={`${id}-tasks`} title={t('org.drawer.tasks')}>
      {state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
      {state === 'error' && <><Notice text={error ?? ''} error /><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></>}
      {state === 'ready' && tasks.length === 0 && <p className="muted">{t('org.drawer.noTasks')}</p>}
      {tasks.length > 0 && <ul className="agentTaskList orgTaskList">{tasks.map((task) => <li key={task.id}>
        <button type="button" className="linkButton" dir="auto" onClick={() => onGo('tasks')} title={t('org.drawer.openTask')}>{task.title}</button>
        <span className={`statusBadge ${tone(task.status)}`}>{statusLabel(task.status)}</span>
        <time dateTime={task.created_at}>{formatDateTime(task.created_at)}</time>
      </li>)}</ul>}
      {state === 'ready' && more && tasks.length > 0 && <div className="actions"><button type="button" className="secondary" disabled={paging} onClick={() => void loadMore()}>{paging ? t('common.loading') : t('agents.loadMore')}</button></div>}
    </Section>
    <Section id={`${id}-result`} title={t('org.drawer.latest')}>
      {latest.state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
      {latest.state === 'error' && <p className="muted">{t('org.drawer.latestFailed')}</p>}
      {latest.state === 'ready' && (latest.result ? <p className="agentAnswer" dir="auto">{latest.result}</p> : <p className="muted">{t('org.drawer.noResult')}</p>)}
    </Section>
    <Section id={`${id}-error`} title={t('org.drawer.lastError')}>
      {fixes.length > 0 && <p className="orgWarn"><AlertTriangle aria-hidden="true" />{t('org.drawer.fixUnavailable', { names: fixes.join(t('common.listSeparator')) })}</p>}
      {latest.failure ? <>
        <p><strong dir="auto">{latest.failure.task.title}</strong> · <time dateTime={latest.failure.task.updated_at}>{formatDateTime(latest.failure.task.updated_at)}</time></p>
        {latest.failure.summary && <p dir="auto">{latest.failure.summary}</p>}
        <p className="muted">{t('org.drawer.fixFailed')}</p>
      </> : fixes.length === 0 && latest.state !== 'loading' && <p className="muted">{t('org.drawer.noError')}</p>}
    </Section>
  </>;
}

function AssistantPanel({ member, onGo, role }: { member: OrgMember; onGo: (view: OrgGo) => void; role: 'owner' | 'member' | 'viewer' | null }) {
  const a = member.assistant!;
  const id = useId();
  return <>
    <div className="actions orgDrawerActions"><button type="button" onClick={() => onGo('assistants')}><ExternalLink aria-hidden="true" />{t('org.drawer.openAssistants')}</button></div>
    {role !== 'owner' && <p className="muted">{t('org.drawer.assistantOwners')}</p>}
    <Section id={`${id}-what`} title={t('org.drawer.what')}>
      <p>{roleText(member)}</p>
      {a.instructions ? <p dir="auto">{a.instructions}</p> : <p className="muted">{t('agents.noInstructions')}</p>}
      <dl className="agentFacts">
        <div><dt>{t('org.col.status')}</dt><dd>{a.enabled ? t('org.status.idle') : t('org.status.off')}</dd></div>
        <div><dt>{t('org.drawer.keywords')}</dt><dd dir="auto">{a.routing_keywords.join(', ') || t('agents.none')}</dd></div>
        <div><dt>{t('org.drawer.channels')}</dt><dd>{a.inbound_channels.join(', ') || t('agents.none')}</dd></div>
      </dl>
    </Section>
    <Section id={`${id}-tools`} title={t('org.drawer.tools')}>
      {member.tools.length === 0 ? <p className="muted">{t('org.drawer.noTools')}</p>
        : <ul className="orgDrawerList">{member.tools.map((chip) => <li key={chip.key}><ToolButton chip={chip} onGo={onGo} /><small dir="ltr">{chip.tools.join(', ')}</small></li>)}</ul>}
    </Section>
    <Section id={`${id}-tasks`} title={t('org.drawer.tasks')}>
      <p className="muted">{t('org.drawer.assistantWork')}</p>
      <button type="button" className="linkButton" onClick={() => onGo('team')}>{t('org.drawer.openTeam')}</button>
    </Section>
  </>;
}

function PersonPanel({ member, onGo }: { member: OrgMember; onGo: (view: OrgGo) => void }) {
  const p = member.person!;
  const id = useId();
  return <>
    <div className="actions orgDrawerActions"><button type="button" onClick={() => onGo('team')}><ExternalLink aria-hidden="true" />{t('org.drawer.openTeam')}</button></div>
    <Section id={`${id}-who`} title={t('org.drawer.person')}>
      <dl className="agentFacts">
        <div><dt>{t('org.col.role')}</dt><dd dir="auto">{roleText(member) || t('agents.none')}</dd></div>
        {p.email && <div><dt>{t('org.drawer.email')}</dt><dd dir="ltr">{p.email}</dd></div>}
        <div><dt>{t('org.drawer.timezone')}</dt><dd dir="ltr">{p.timezone}</dd></div>
        {p.capacity_hours !== null && <div><dt>{t('org.drawer.capacity')}</dt><dd>{t('org.drawer.hours', { count: p.capacity_hours })}</dd></div>}
        <div><dt>{t('org.drawer.assignable')}</dt><dd>{p.assignable ? t('org.drawer.yes') : t('org.drawer.no')}</dd></div>
      </dl>
    </Section>
  </>;
}
