// Agents (docs/agents.md): the agents people build in a workspace. A list with category filters and
// search, a create/edit wizard (start from a template or from scratch), and an agent's page with
// Chat, Call, Edit and More, and the Overview, Skills, Memory and Settings tabs.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type CSSProperties, type FormEvent, type ReactNode } from 'react';
import {
  AlertTriangle, ArrowLeft, BarChart3, Bot, Briefcase, Calendar, Check, CircleDot, Ellipsis, FileText, Heart, Lock, Mail, MessageSquare,
  Pause, Pencil, Phone, Play, Plus, RefreshCw, Search, ShoppingCart, Sparkles, Trash2, Truck, User, Users, Wrench,
} from 'lucide-react';
import type { Task } from '@anum/contracts';
import { AgentSkillsPanel } from './AgentSkillsPanel';
import { ChatView } from './ChatView';
import { ConfirmDialog } from './Dialog';
import { OrgChartView, type OrgGo } from './OrgChartView';
import * as api from './lib/api';
import {
  agentsApi, ALWAYS_ON_TOOL, CATEGORIES, COLORS, DESCRIPTION_MAX, DETAIL_TABS, draftBody, draftChanges, draftFromAgent,
  draftFromTemplate, EDIT_STEPS, effectiveLevel, emptyDraft, INSTRUCTIONS_MAX, levelsFor, MEMORY_MAX, mergeById, NAME_MAX,
  recentTasksPath, skillNames, skillsMissingTools, templateKey, toggleSkill, toggleTool, TOOL_CATEGORIES, toolGroups, validateDraft,
  WIZARD_STEPS, callSummaryPrompt,
  type AgentCatalog, type AgentDraft, type AgentMemory, type AgentProfile, type AgentStatus, type AgentTemplate,
  type CategoryFilter, type DetailTab, type DraftErrors, type WizardStep,
} from './lib/agents';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { statusLabel } from './lib/labels';
import './agents.css';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);

const ICONS: Record<string, typeof Bot> = {
  bot: Bot, truck: Truck, search: Search, mail: Mail, calendar: Calendar, 'file-text': FileText, phone: Phone,
  briefcase: Briefcase, user: User, sparkles: Sparkles, wrench: Wrench, chart: BarChart3, 'shopping-cart': ShoppingCart, heart: Heart,
};

function AgentAvatar({ icon, color, size = 'md' }: { icon: string; color: string; size?: 'md' | 'lg' }) {
  const Icon = ICONS[icon] ?? Bot;
  return <span className={`agentAvatar ${size}`} style={{ '--agent-color': color } as CSSProperties} aria-hidden="true"><Icon /></span>;
}

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

/** Why something failed, in words that say who can fix it. */
function explain(error: unknown, action: MessageKey): string {
  if (isPermissionDenied(error)) return describeError(error);
  if (isBudgetExceeded(error)) return t('common.budgetReached', { message: budgetMessage(error) });
  return t(action, { reason: describeError(error) });
}

function useRole(): 'owner' | 'member' | 'viewer' | null {
  const [role, setRole] = useState<'owner' | 'member' | 'viewer' | null>(null);
  useEffect(() => {
    void api.getCurrentMembership().then((m) => setRole(m.role === 'owner' || m.role === 'member' ? m.role : 'viewer')).catch(() => setRole('viewer'));
  }, []);
  return role;
}

type Mode = { kind: 'list' } | { kind: 'detail'; id: string } | { kind: 'wizard'; agent: AgentProfile | null };
type ListTab = 'agents' | 'org';
const TAB_KEY = 'anum.agents.tab';

function readTab(): ListTab {
  try { return localStorage.getItem(TAB_KEY) === 'org' ? 'org' : 'agents'; } catch { return 'agents'; }
}

export function AgentsView({ onOpenSkills, onOpenTasks, onOpenApprovals, onOpenBudgets, onOpenFiles, onGo }: {
  onOpenSkills?: () => void; onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void; onOpenFiles?: () => void;
  /** Opens Integrations, Assistants, Team or Automation from the org chart (without it, the URL hash does). */
  onGo?: (view: OrgGo) => void;
}) {
  const [mode, setMode] = useState<Mode>({ kind: 'list' });
  const [tab, setTabState] = useState<ListTab>(readTab);
  const setTab = (next: ListTab) => { setTabState(next); try { localStorage.setItem(TAB_KEY, next); } catch { /* storage blocked */ } };
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const role = useRole();
  const loadCatalog = useCallback(async () => {
    setCatalogError(null);
    try { setCatalog(await agents.catalog()); } catch (e) { setCatalogError(explain(e, 'agents.err.catalog')); }
  }, []);
  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  const top = useRef<HTMLDivElement>(null);
  const go = (next: Mode, message: string | null = null) => {
    setMode(next); setFlash(message);
    window.requestAnimationFrame(() => top.current?.focus());
  };
  return <div className="agentsView" ref={top} tabIndex={-1}>
    {flash && <Notice>{flash}</Notice>}
    {mode.kind === 'list' && <nav className="settingsTabs agentTabs agentViewTabs" aria-label={t('org.tabs')}>
      {(['agents', 'org'] as const).map((value) => <button key={value} type="button" aria-current={tab === value ? 'page' : undefined} onClick={() => setTab(value)}>{t(`org.tab.${value}`)}</button>)}
    </nav>}
    {mode.kind === 'list' && tab === 'org' && <OrgChartView role={role} onOpenAgent={(id) => go({ kind: 'detail', id })} onEditAgent={(agent) => go({ kind: 'wizard', agent })}
      onCreate={() => go({ kind: 'wizard', agent: null })} onOpenApprovals={onOpenApprovals} onOpenTasks={onOpenTasks} onOpenBudgets={onOpenBudgets} onGo={onGo} />}
    {mode.kind === 'list' && tab === 'agents' && <AgentList catalog={catalog} catalogError={catalogError} role={role} onRetryCatalog={() => void loadCatalog()}
      onOpen={(id) => go({ kind: 'detail', id })} onCreate={() => go({ kind: 'wizard', agent: null })} onOpenSkills={onOpenSkills} />}
    {mode.kind === 'wizard' && <AgentWizard catalog={catalog} catalogError={catalogError} onRetryCatalog={() => void loadCatalog()} agent={mode.agent}
      onCancel={() => go(mode.agent ? { kind: 'detail', id: mode.agent.id } : { kind: 'list' })}
      onSaved={(saved, created) => go({ kind: 'detail', id: saved.id }, t(created ? 'agents.created' : 'agents.saved', { name: saved.name }))}
      onOpenSkills={onOpenSkills} onOpenFiles={onOpenFiles} />}
    {mode.kind === 'detail' && <AgentDetail key={mode.id} id={mode.id} catalog={catalog} catalogError={catalogError} onOpenSkills={onOpenSkills} onBack={() => go({ kind: 'list' })}
      onEdit={(agent) => go({ kind: 'wizard', agent })} onDeleted={(name) => go({ kind: 'list' }, t('agents.deleted', { name }))}
      onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />}
  </div>;
}

// ---------------------------------------------------------------------------- list

function AgentList({ catalog, catalogError, role, onOpen, onCreate, onRetryCatalog, onOpenSkills }: {
  catalog: AgentCatalog | null; catalogError: string | null; role: 'owner' | 'member' | 'viewer' | null;
  onOpen: (id: string) => void; onCreate: () => void; onRetryCatalog: () => void; onOpenSkills?: () => void;
}) {
  const [category, setCategory] = useState<CategoryFilter>('all');
  const [status, setStatus] = useState<AgentStatus | 'all'>('all');
  const [query, setQuery] = useState('');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<AgentProfile[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({ all: 0, work: 0, personal: 0, custom: 0 });
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const canCreate = role === 'owner' || role === 'member';
  useEffect(() => { const timer = window.setTimeout(() => setQ(query), 250); return () => window.clearTimeout(timer); }, [query]);
  const load = useCallback(async () => {
    setState('loading'); setError(null);
    try {
      const page = await agents.list({ category, q, status });
      setItems(page.items); setCounts(page.counts); setCursor(page.next_cursor); setState('ready');
    } catch (e) { setError(explain(e, 'agents.err.list')); setState('error'); }
  }, [category, q, status]);
  useEffect(() => { void load(); }, [load]);
  async function loadMore() {
    if (!cursor) return;
    setMore(true);
    try { const page = await agents.list({ category, q, status, cursor }); setItems((old) => mergeById(old, page.items)); setCursor(page.next_cursor); }
    catch (e) { setError(explain(e, 'agents.err.list')); }
    finally { setMore(false); }
  }
  const filtered = Boolean(q.trim()) || status !== 'all' || category !== 'all';
  const searchId = useId();
  return <>
    <section className="moduleIntro">
      <div><h2>{t('agents.list.title')}</h2><p>{t('agents.list.intro')}</p></div>
      {canCreate && <div className="actions"><button type="button" onClick={onCreate}><Plus aria-hidden="true" />{t('agents.create')}</button></div>}
    </section>
    {role === 'viewer' && <Notice>{t('agents.viewerNote')}</Notice>}
    <div className="agentFilters">
      <div className="segmented" role="group" aria-label={t('agents.filter.category')}>
        {(['all', ...CATEGORIES] as CategoryFilter[]).map((c) => <button key={c} type="button" aria-pressed={category === c} onClick={() => setCategory(c)}>
          {t(`agents.category.${c}` as MessageKey)} <span className="agentCount">{counts[c] ?? 0}</span>
        </button>)}
      </div>
      <div className="agentSearch">
        <label className="visuallyHidden" htmlFor={searchId}>{t('agents.search')}</label>
        <Search aria-hidden="true" />
        <input id={searchId} type="search" value={query} placeholder={t('agents.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} />
      </div>
      <label className="agentStatusFilter"><span className="visuallyHidden">{t('agents.filter.status')}</span>
        <select value={status} onChange={(e) => setStatus(e.target.value as AgentStatus | 'all')} aria-label={t('agents.filter.status')}>
          <option value="all">{t('agents.status.all')}</option><option value="active">{t('agents.status.active')}</option><option value="paused">{t('agents.status.paused')}</option>
        </select>
      </label>
    </div>
    {state === 'loading' && <p className="muted agentState" role="status">{t('agents.loading')}</p>}
    {state === 'error' && <div className="agentState" role="alert"><Notice error>{error}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
    {state === 'ready' && items.length > 0 && <ul className="agentGrid" aria-label={t('agents.list.title')}>
      {items.map((agent) => <li key={agent.id}>
        <article className={`surface agentRow agentCard ${agent.status === 'paused' ? 'isPaused' : ''}`}>
          <AgentAvatar icon={agent.icon} color={agent.color} />
          <div className="agentMain">
            <div className="rowTitle"><h3 dir="auto">
              <button type="button" className="agentOpen" onClick={() => onOpen(agent.id)}>{agent.name}</button>
            </h3>{agent.status === 'paused' && <span className="statusBadge warning">{t('agents.status.paused')}</span>}</div>
            <p dir="auto">{agent.description || t('agents.noDescription')}</p>
            <div className="agentMeta">
              <span>{t(`agents.category.${agent.category}` as MessageKey)}{agent.department ? ` · ${agent.department}` : ''}</span>
              <span>{agent.sharing === 'workspace' ? <><Users aria-hidden="true" />{t('agents.sharing.workspace')}</> : <><Lock aria-hidden="true" />{t('agents.sharing.private')}</>}</span>
              <span>{t('agents.toolCount', { count: agent.allowed_tools.length + 1 })}</span>
            </div>
            <AgentSkillChips agent={agent} catalog={catalog} />
          </div>
        </article>
      </li>)}
    </ul>}
    {state === 'ready' && cursor && <div className="actions"><button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('agents.loadMore')}</button></div>}
    {state === 'ready' && items.length === 0 && (filtered
      ? <div className="emptyState"><Search aria-hidden="true" /><h3>{t('agents.emptyFiltered')}</h3><p>{t('agents.emptyFilteredText')}</p>
          <button type="button" className="secondary" onClick={() => { setQuery(''); setQ(''); setStatus('all'); setCategory('all'); }}>{t('agents.clearFilters')}</button></div>
      : <div className="emptyState"><Bot aria-hidden="true" /><h3>{t('agents.emptyTitle')}</h3><p>{canCreate ? t('agents.emptyText') : t('agents.emptyViewer')}</p>
          {canCreate && <div className="actions"><button type="button" onClick={onCreate}><Plus aria-hidden="true" />{t('agents.createFirst')}</button></div>}
          {catalogError && <><Notice error>{catalogError}</Notice><button type="button" className="secondary" onClick={onRetryCatalog}>{t('common.tryAgain')}</button></>}
          {catalog && canCreate && <p className="muted">{t('agents.templatesHint', { count: catalog.templates.length })}</p>}
          {onOpenSkills && <button type="button" className="secondary" onClick={onOpenSkills}>{t('agents.browseSkills')}</button>}
        </div>)}
  </>;
}

/** The skills an agent loads, by name, and a warning when some no longer load. */
function AgentSkillChips({ agent, catalog }: { agent: AgentProfile; catalog: AgentCatalog | null }) {
  if (agent.allowed_skills.length === 0) return null;
  const { names, more } = skillNames(agent, catalog);
  return <div className="agentSkillChips">
    <span className="visuallyHidden">{t('agentSkills.cardCount', { count: agent.allowed_skills.length })}</span>
    <ul aria-label={t('agentSkills.cardCount', { count: agent.allowed_skills.length })}>
      {names.map((name) => <li key={name} dir="auto">{name}</li>)}
      {more > 0 && <li>{t('agentSkills.cardMore', { count: more })}</li>}
    </ul>
    {agent.unavailable_skills.length > 0 && <span className="agentSkillWarn"><AlertTriangle aria-hidden="true" />{t('agentSkills.cardAttention', { count: agent.unavailable_skills.length })}</span>}
  </div>;
}

// ---------------------------------------------------------------------------- wizard

function FieldError({ id, error }: { id: string; error?: MessageKey }) {
  return error ? <small id={id} className="fieldError" role="alert">{t(error)}</small> : null;
}

function AgentWizard({ catalog, catalogError, onRetryCatalog, agent, onCancel, onSaved, onOpenSkills, onOpenFiles }: {
  catalog: AgentCatalog | null; catalogError: string | null; onRetryCatalog: () => void; agent: AgentProfile | null;
  onCancel: () => void; onSaved: (agent: AgentProfile, created: boolean) => void; onOpenSkills?: () => void; onOpenFiles?: () => void;
}) {
  const steps = agent ? EDIT_STEPS : WIZARD_STEPS;
  const [step, setStep] = useState<WizardStep>(steps[0]);
  const [draft, setDraft] = useState<AgentDraft>(() => agent ? draftFromAgent(agent) : emptyDraft());
  const [errors, setErrors] = useState<DraftErrors>({});
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [sources, setSources] = useState<{ type: 'memory' | 'file'; id: string; name: string }[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const index = steps.indexOf(step);
  useEffect(() => { heading.current?.focus(); }, [step]);
  useEffect(() => {
    if (step !== 'autonomy' || sources) return;
    api.getRetrievalStatus().then((status) => setSources(status.sources.filter((s) => s.status === 'indexed').map((s) => ({ type: s.source_type as 'memory' | 'file', id: s.source_id, name: s.name ?? s.source_id }))))
      .catch((e) => setSourcesError(explain(e, 'agents.err.sources')));
  }, [step, sources]);
  const set = <K extends keyof AgentDraft>(key: K, value: AgentDraft[K]) => setDraft((old) => ({ ...old, [key]: value }));
  function next() {
    const found = validateDraft(draft, step, catalog);
    setErrors(found);
    if (Object.keys(found).length) return;
    setStep(steps[Math.min(index + 1, steps.length - 1)]);
  }
  function pickTemplate(template: AgentTemplate | null) {
    setDraft(template ? draftFromTemplate(template, t(templateKey(template.id, 'name')), t(templateKey(template.id, 'description'))) : emptyDraft());
    setStep('identity');
  }
  async function save(event?: FormEvent) {
    event?.preventDefault();
    const found = validateDraft(draft, 'review', catalog);
    setErrors(found);
    if (Object.keys(found).length) { setSaveError(t('agents.err.fixFields')); return; }
    setBusy(true); setSaveError(null);
    try {
      const saved = agent ? await agents.update(agent.id, draftChanges(agent, draft)) : await agents.create(draftBody(draft));
      onSaved(saved, !agent);
    } catch (e) { setSaveError(explain(e, agent ? 'agents.err.save' : 'agents.err.create')); }
    finally { setBusy(false); }
  }
  const nameId = useId(), descId = useId(), deptId = useId(), instrId = useId();
  const groups = catalog ? toolGroups(catalog) : null;
  const missing = catalog ? skillsMissingTools(draft, catalog) : [];
  return <section className="surface agentWizard" aria-labelledby="agent-wizard-title">
    <div className="wizardHead">
      <button type="button" className="secondary" onClick={onCancel}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('common.cancel')}</button>
      <h2 id="agent-wizard-title">{agent ? t('agents.wizard.editTitle', { name: agent.name }) : t('agents.wizard.createTitle')}</h2>
    </div>
    <ol className="wizardSteps" aria-label={t('agents.wizard.steps')}>
      {steps.map((s, i) => <li key={s} aria-current={s === step ? 'step' : undefined} className={i < index ? 'done' : ''}>
        <button type="button" disabled={i > index} onClick={() => setStep(s)}><span aria-hidden="true">{i < index ? <Check /> : i + 1}</span>{t(`agents.step.${s}` as MessageKey)}</button>
      </li>)}
    </ol>
    {!catalog && !catalogError && <p className="muted" role="status">{t('agents.loadingCatalog')}</p>}
    {catalogError && <><Notice error>{catalogError}</Notice><div className="actions"><button type="button" className="secondary" onClick={onRetryCatalog}>{t('common.tryAgain')}</button></div></>}
    {catalog && <form className="wizardBody" onSubmit={(e) => { e.preventDefault(); if (step === 'review') void save(); else next(); }} noValidate>
      <h3 ref={heading} tabIndex={-1} className="wizardStepTitle">{t(`agents.step.${step}` as MessageKey)}</h3>
      {step === 'start' && <>
        <p className="muted">{t('agents.wizard.startText')}</p>
        <div className="templateGrid">
          <button type="button" className="templateCard" onClick={() => pickTemplate(null)}>
            <AgentAvatar icon="sparkles" color="#475569" /><span><strong>{t('agents.wizard.blank')}</strong><small>{t('agents.wizard.blankText')}</small></span>
          </button>
          {catalog.templates.map((template) => <button type="button" key={template.id} className="templateCard" onClick={() => pickTemplate(template)}>
            <AgentAvatar icon={template.icon} color={template.color} />
            <span><strong>{t(templateKey(template.id, 'name'))}</strong><small>{t(templateKey(template.id, 'description'))}</small>
              {template.missing_capabilities.length > 0 && <small className="templateMissing">{t('agents.wizard.notConnected', { what: template.missing_capabilities.join(t('common.listSeparator')) })}</small>}
            </span>
          </button>)}
        </div>
      </>}
      {step === 'identity' && <>
        <label className="field"><span>{t('agents.field.name')}</span>
          <input value={draft.name} maxLength={NAME_MAX} dir="auto" onChange={(e) => set('name', e.target.value)} aria-invalid={Boolean(errors.name)} aria-describedby={errors.name ? nameId : undefined} required />
          <FieldError id={nameId} error={errors.name} /></label>
        <label className="field"><span>{t('agents.field.description')}</span>
          <textarea className="short" value={draft.description} maxLength={DESCRIPTION_MAX} dir="auto" onChange={(e) => set('description', e.target.value)} aria-invalid={Boolean(errors.description)} aria-describedby={errors.description ? descId : undefined} />
          <FieldError id={descId} error={errors.description} /></label>
        <fieldset className="field"><legend>{t('agents.field.category')}</legend>
          <div className="segmented">{CATEGORIES.map((c) => <button key={c} type="button" aria-pressed={draft.category === c} onClick={() => set('category', c)}>{t(`agents.category.${c}` as MessageKey)}</button>)}</div>
        </fieldset>
        <label className="field"><span>{t('agents.field.department')}</span>
          <input value={draft.department} maxLength={80} dir="auto" placeholder={t('agents.field.departmentHint')} onChange={(e) => set('department', e.target.value)} aria-describedby={errors.department ? deptId : undefined} />
          <FieldError id={deptId} error={errors.department} /></label>
        <fieldset className="field"><legend>{t('agents.field.icon')}</legend>
          <div className="iconPicker" role="radiogroup" aria-label={t('agents.field.icon')}>
            {catalog.icons.map((icon) => { const Icon = ICONS[icon] ?? Bot; return <button key={icon} type="button" role="radio" aria-checked={draft.icon === icon} aria-label={t(`agents.icon.${icon}` as MessageKey)} title={t(`agents.icon.${icon}` as MessageKey)} onClick={() => set('icon', icon)}><Icon aria-hidden="true" /></button>; })}
          </div>
        </fieldset>
        <fieldset className="field"><legend>{t('agents.field.color')}</legend>
          <div className="colorPicker" role="radiogroup" aria-label={t('agents.field.color')}>
            {COLORS.map((color, i) => <button key={color} type="button" role="radio" aria-checked={draft.color === color} aria-label={t('agents.colorN', { n: i + 1 })} style={{ background: color }} onClick={() => set('color', color)}>{draft.color === color && <Check aria-hidden="true" />}</button>)}
          </div>
        </fieldset>
      </>}
      {step === 'instructions' && <>
        <label className="field"><span>{t('agents.field.instructions')}</span>
          <textarea value={draft.instructions} maxLength={INSTRUCTIONS_MAX} dir="auto" rows={10} placeholder={t('agents.field.instructionsHint')} onChange={(e) => set('instructions', e.target.value)} aria-invalid={Boolean(errors.instructions)} aria-describedby={`${instrId}-count${errors.instructions ? ` ${instrId}` : ''}`} />
          <small id={`${instrId}-count`} className="muted">{t('agents.charCount', { used: draft.instructions.length, max: INSTRUCTIONS_MAX })}</small>
          <FieldError id={instrId} error={errors.instructions} /></label>
        <p className="muted">{t('agents.field.instructionsNote')}</p>
        <fieldset className="field"><legend>{t('agents.field.model')}</legend>
          <div className="choiceList">{(['capable', 'fast'] as const).map((route) => <label key={route} className="choice">
            <input type="radio" name="model_route" checked={draft.model_route === route} onChange={() => set('model_route', route)} />
            <span><strong>{t(`agents.model.${route}` as MessageKey)}</strong><small>{t(`agents.model.${route}Text` as MessageKey)}</small></span>
          </label>)}</div>
        </fieldset>
      </>}
      {step === 'capabilities' && groups && <>
        <fieldset className="field"><legend>{t('agents.field.skills')}</legend>
          {catalog.skills.length === 0 && <p className="muted">{t('agents.noSkills')} {onOpenSkills && <button type="button" className="linkButton" onClick={onOpenSkills}>{t('agents.browseSkills')}</button>}</p>}
          <div className="choiceList">{catalog.skills.map((skill) => <label key={skill.id} className="choice">
            <input type="checkbox" checked={draft.allowed_skills.includes(skill.id)} onChange={(e) => setDraft((old) => toggleSkill(old, skill, e.target.checked, catalog))} />
            <span><strong dir="auto">{skill.name}</strong><small dir="auto">{skill.description}</small>
              <small>{skill.source === 'installed' ? t('agents.skill.installed') : t('agents.skill.builtin')}{skill.required_tools.length ? ` · ${t('agents.skill.uses', { tools: skill.required_tools.join(t('common.listSeparator')) })}` : ''}</small></span>
          </label>)}</div>
          {errors.skills && <p className="fieldError" role="alert">{t(errors.skills)} {missing.map((m) => `${m.skill.name}: ${m.tools.join(t('common.listSeparator'))}`).join('; ')}</p>}
        </fieldset>
        <fieldset className="field"><legend>{t('agents.field.tools')}</legend>
          <p className="muted">{t('agents.alwaysOn', { tool: ALWAYS_ON_TOOL })}</p>
          {TOOL_CATEGORIES.map((category) => groups[category].length > 0 && <div key={category} className="toolGroup">
            <h4>{t(`agents.toolCategory.${category}` as MessageKey)}</h4>
            <div className="choiceList">{groups[category].map((tool) => <label key={tool.name} className={`choice ${tool.available_to_you ? '' : 'isUnavailable'}`}>
              <input type="checkbox" disabled={!tool.available_to_you && !draft.allowed_tools.includes(tool.name)} checked={draft.allowed_tools.includes(tool.name)} onChange={(e) => setDraft((old) => toggleTool(old, tool.name, e.target.checked))} />
              <span><strong dir="ltr">{tool.name}</strong><small>{tool.description}</small>{!tool.available_to_you && tool.reason && <small className="fieldError">{tool.reason}</small>}</span>
            </label>)}</div>
          </div>)}
          {TOOL_CATEGORIES.every((c) => groups[c].length === 0) && <p className="muted">{t('agents.noTools')}</p>}
        </fieldset>
      </>}
      {step === 'autonomy' && <>
        <p className="muted">{t('agents.autonomy.intro')}</p>
        {TOOL_CATEGORIES.map((category) => {
          const floor = catalog.autonomy_floor[category];
          const chosen = draft.autonomy[category];
          return <fieldset key={category} className="field autonomyRow"><legend>{t(`agents.toolCategory.${category}` as MessageKey)}</legend>
            <div className="segmented">{levelsFor(category).map((level) => <button key={level} type="button" aria-pressed={chosen === level} onClick={() => set('autonomy', { ...draft.autonomy, [category]: level })}>{t(`agents.level.${level}` as MessageKey)}</button>)}</div>
            <small className="muted">{t(`agents.levelText.${chosen}` as MessageKey)}{effectiveLevel(chosen, floor) !== chosen ? ` ${t('agents.floorNote')}` : ''}</small>
          </fieldset>;
        })}
        <fieldset className="field"><legend>{t('agents.field.knowledge')}</legend>
          <div className="choiceList">{(['none', 'workspace', 'selected'] as const).map((mode) => <label key={mode} className="choice">
            <input type="radio" name="knowledge_mode" checked={draft.knowledge_mode === mode} onChange={() => set('knowledge_mode', mode)} />
            <span><strong>{t(`agents.knowledge.${mode}` as MessageKey)}</strong><small>{t(`agents.knowledge.${mode}Text` as MessageKey)}</small></span>
          </label>)}</div>
          {draft.knowledge_mode === 'selected' && <div className="sourcePicker">
            {sourcesError && <Notice error>{sourcesError}</Notice>}
            {!sources && !sourcesError && <p className="muted" role="status">{t('common.loading')}</p>}
            {sources && sources.length === 0 && <p className="muted">{t('agents.noSources')} {onOpenFiles && <button type="button" className="linkButton" onClick={onOpenFiles}>{t('agents.openFiles')}</button>}</p>}
            {sources && sources.length > 0 && <div className="choiceList compact">{sources.map((source) => {
              const on = draft.knowledge_sources.some((s) => s.type === source.type && s.id === source.id);
              return <label key={`${source.type}:${source.id}`} className="choice">
                <input type="checkbox" checked={on} onChange={(e) => set('knowledge_sources', e.target.checked ? [...draft.knowledge_sources, { type: source.type, id: source.id }] : draft.knowledge_sources.filter((s) => !(s.type === source.type && s.id === source.id)))} />
                <span><strong dir="auto">{source.name}</strong><small>{t(source.type === 'file' ? 'agents.source.file' : 'agents.source.memory')}</small></span>
              </label>;
            })}</div>}
            {errors.knowledge && <p className="fieldError" role="alert">{t(errors.knowledge)}</p>}
          </div>}
        </fieldset>
      </>}
      {step === 'review' && <>
        <div className="reviewHead"><AgentAvatar icon={draft.icon} color={draft.color} size="lg" /><div><strong dir="auto">{draft.name || t('agents.unnamed')}</strong><p className="muted" dir="auto">{draft.description}</p></div></div>
        <dl className="agentFacts">
          <div><dt>{t('agents.field.category')}</dt><dd>{t(`agents.category.${draft.category}` as MessageKey)}{draft.department ? ` · ${draft.department}` : ''}</dd></div>
          <div><dt>{t('agents.field.model')}</dt><dd>{t(`agents.model.${draft.model_route}` as MessageKey)}</dd></div>
          <div><dt>{t('agents.field.skills')}</dt><dd>{draft.allowed_skills.length ? draft.allowed_skills.join(t('common.listSeparator')) : t('agents.none')}</dd></div>
          <div><dt>{t('agents.field.tools')}</dt><dd dir="ltr">{[ALWAYS_ON_TOOL, ...draft.allowed_tools].join(', ')}</dd></div>
          <div><dt>{t('agents.field.autonomy')}</dt><dd>{TOOL_CATEGORIES.map((c) => `${t(`agents.toolCategory.${c}` as MessageKey)}: ${t(`agents.level.${draft.autonomy[c]}` as MessageKey)}`).join(t('common.listSeparator'))}</dd></div>
          <div><dt>{t('agents.field.knowledge')}</dt><dd>{t(`agents.knowledge.${draft.knowledge_mode}` as MessageKey)}{draft.knowledge_mode === 'selected' ? ` (${draft.knowledge_sources.length})` : ''}</dd></div>
        </dl>
        {(!agent || agent.owner_user_id === api.currentUserId()) && <fieldset className="field"><legend>{t('agents.field.sharing')}</legend>
          <div className="choiceList">{(['private', 'workspace'] as const).map((sharing) => <label key={sharing} className="choice">
            <input type="radio" name="sharing" checked={draft.sharing === sharing} onChange={() => set('sharing', sharing)} />
            <span><strong>{t(`agents.sharing.${sharing}` as MessageKey)}</strong><small>{t(`agents.sharing.${sharing}Text` as MessageKey)}</small></span>
          </label>)}</div>
        </fieldset>}
      </>}
      {saveError && <Notice error>{saveError}</Notice>}
      {step !== 'start' && <div className="actions wizardActions">
        {index > 0 && <button type="button" className="secondary" onClick={() => setStep(steps[index - 1])}>{t('agents.back')}</button>}
        {step === 'review'
          ? <button type="submit" disabled={busy}>{busy ? t('dialog.working') : agent ? t('agents.saveChanges') : t('agents.createAgent')}</button>
          : <button type="submit">{t('agents.next')}</button>}
      </div>}
    </form>}
  </section>;
}

// ---------------------------------------------------------------------------- detail

type Panel = DetailTab | 'chat' | 'call';

function AgentDetail({ id, catalog, catalogError, onOpenSkills, onBack, onEdit, onDeleted, onOpenTasks, onOpenApprovals, onOpenBudgets }: {
  id: string; catalog: AgentCatalog | null; catalogError: string | null; onOpenSkills?: () => void; onBack: () => void; onEdit: (agent: AgentProfile) => void; onDeleted: (name: string) => void;
  onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void;
}) {
  const [agent, setAgent] = useState<AgentProfile | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'missing'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>('overview');
  const [menu, setMenu] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [tasksVersion, setTasksVersion] = useState(0);
  const load = useCallback(async () => {
    setState('loading');
    try { setAgent(await agents.get(id)); setState('ready'); }
    catch (e) {
      if (e instanceof Error && 'status' in e && (e as { status: number }).status === 404) { setState('missing'); return; }
      setError(explain(e, 'agents.err.load')); setState('error');
    }
  }, [id]);
  useEffect(() => { void load(); }, [load]);
  async function update(body: Parameters<typeof agents.update>[1]) {
    if (!agent) return;
    setBusy(true); setActionError(null);
    try { setAgent(await agents.update(agent.id, { ...body, version: agent.version })); }
    catch (e) { setActionError(explain(e, 'agents.err.save')); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!agent) return;
    setBusy(true);
    try { await agents.remove(agent.id); onDeleted(agent.name); }
    catch (e) { setActionError(explain(e, 'agents.err.delete')); setConfirmDelete(false); }
    finally { setBusy(false); }
  }
  const back = <button type="button" className="secondary backButton" onClick={onBack}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('agents.allAgents')}</button>;
  if (state === 'loading') return <>{back}<p className="muted agentState" role="status">{t('agents.loadingOne')}</p></>;
  if (state === 'missing') return <>{back}<div className="emptyState"><Search aria-hidden="true" /><h3>{t('agents.missingTitle')}</h3><p>{t('agents.missingText')}</p></div></>;
  if (state === 'error' || !agent) return <>{back}<Notice error>{error}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></div></>;
  const paused = agent.status === 'paused';
  return <>
    {back}
    <section className="surface agentHero" aria-labelledby="agent-name">
      <AgentAvatar icon={agent.icon} color={agent.color} size="lg" />
      <div className="agentHeroText">
        <h2 id="agent-name" dir="auto">{agent.name}</h2>
        <p className="muted" dir="auto">{agent.description || t('agents.noDescription')}</p>
        <div className="agentMeta">
          <span className={`statusBadge ${paused ? 'warning' : 'success'}`}>{t(`agents.status.${agent.status}` as MessageKey)}</span>
          <span>{t(`agents.category.${agent.category}` as MessageKey)}{agent.department ? ` · ${agent.department}` : ''}</span>
          <span>{agent.sharing === 'workspace' ? t('agents.sharing.workspace') : t('agents.sharing.private')}</span>
        </div>
      </div>
      <div className="agentActions">
        <button type="button" onClick={() => setPanel('chat')} disabled={paused} aria-pressed={panel === 'chat'}><MessageSquare aria-hidden="true" />{t('agents.chat')}</button>
        <button type="button" className="secondary" onClick={() => setPanel('call')} disabled={paused} aria-pressed={panel === 'call'}><Phone aria-hidden="true" />{t('agents.call')}</button>
        {agent.can_edit && <button type="button" className="secondary" onClick={() => onEdit(agent)}><Pencil aria-hidden="true" />{t('agents.edit')}</button>}
        {agent.can_edit && <MoreMenu open={menu} onToggle={setMenu} items={[
          { label: paused ? t('agents.resume') : t('agents.pause'), icon: paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />, run: () => void update({ status: paused ? 'active' : 'paused' }) },
          { label: t('agents.delete'), icon: <Trash2 aria-hidden="true" />, danger: true, run: () => setConfirmDelete(true) },
        ]} />}
      </div>
    </section>
    {paused && <Notice>{agent.can_edit ? t('agents.pausedOwner') : t('agents.pausedOther')}</Notice>}
    {!agent.can_edit && <Notice>{t('agents.readOnly')}</Notice>}
    {actionError && <Notice error>{actionError}</Notice>}
    {(agent.unavailable_tools.length > 0 || agent.unavailable_skills.length > 0) && <Notice error>{t('agents.unavailable', { names: [...agent.unavailable_tools, ...agent.unavailable_skills].join(t('common.listSeparator')) })}</Notice>}
    <nav className="settingsTabs agentTabs" aria-label={t('agents.sections')}>
      {DETAIL_TABS.map((tab) => <button key={tab} type="button" aria-current={panel === tab ? 'page' : undefined} onClick={() => setPanel(tab)}>{t(`agents.tab.${tab}` as MessageKey)}</button>)}
    </nav>
    {panel === 'overview' && <OverviewTab agent={agent} tasksVersion={tasksVersion} onRan={() => setTasksVersion((v) => v + 1)} onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />}
    {panel === 'skills' && <AgentSkillsPanel agent={agent} catalog={catalog} catalogError={catalogError} onAgentChanged={setAgent} onOpenSkills={onOpenSkills} />}
    {panel === 'memory' && <MemoryTab agent={agent} />}
    {panel === 'settings' && <SettingsTab agent={agent} catalog={catalog} busy={busy} onUpdate={(body) => void update(body)} onDelete={() => setConfirmDelete(true)} />}
    {panel === 'chat' && <section className="agentChat" aria-label={t('agents.chatWith', { name: agent.name })}><ChatView key={agent.id} agent={{ id: agent.id, name: agent.name }} onOpenBudgets={onOpenBudgets ?? (() => undefined)} /></section>}
    {panel === 'call' && <CallPanel agent={agent} onRan={() => setTasksVersion((v) => v + 1)} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />}
    {confirmDelete && <ConfirmDialog title={t('agents.deleteTitle', { name: agent.name })} confirmLabel={t('agents.delete')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setConfirmDelete(false)}>
      <p>{t('agents.deleteText')}</p>
    </ConfirmDialog>}
  </>;
}

function MoreMenu({ open, onToggle, items }: { open: boolean; onToggle: (open: boolean) => void; items: { label: string; icon: ReactNode; danger?: boolean; run: () => void }[] }) {
  const menuId = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    box.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { onToggle(false); box.current?.querySelector<HTMLButtonElement>('button')?.focus(); } };
    const onClick = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) onToggle(false); };
    document.addEventListener('keydown', onKey); document.addEventListener('mousedown', onClick);
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onClick); };
  }, [open, onToggle]);
  return <div className="moreMenu" ref={box}>
    <button type="button" className="secondary" aria-haspopup="menu" aria-expanded={open} aria-controls={menuId} onClick={() => onToggle(!open)}><Ellipsis aria-hidden="true" />{t('agents.more')}</button>
    {open && <div className="surface moreMenuList" role="menu" id={menuId} onKeyDown={(event) => {
      const entries = [...(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))];
      const at = entries.indexOf(document.activeElement as HTMLButtonElement);
      if (event.key === 'ArrowDown') { event.preventDefault(); entries[(at + 1) % entries.length]?.focus(); }
      if (event.key === 'ArrowUp') { event.preventDefault(); entries[(at - 1 + entries.length) % entries.length]?.focus(); }
    }}>
      {items.map((item) => <button key={item.label} type="button" role="menuitem" className={item.danger ? 'dangerItem' : undefined} onClick={() => { onToggle(false); item.run(); }}>{item.icon}{item.label}</button>)}
    </div>}
  </div>;
}

function RunResult({ result, onOpenTasks, onOpenApprovals }: { result: api.RunTaskResult; onOpenTasks?: () => void; onOpenApprovals?: () => void }) {
  const failed = result.run.status === 'failed';
  const summary = result.run.result ?? result.run.steps[result.run.steps.length - 1]?.summary ?? '';
  return <div className={`agentRunResult ${failed ? 'isFailed' : ''}`} role="status">
    <p><strong>{statusLabel(result.task.status)}</strong> · <span dir="auto">{result.task.title}</span></p>
    {result.approval ? <p>{t('agents.run.waiting')} {onOpenApprovals && <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('agents.run.openApprovals')}</button>}</p>
      : summary && <p className="agentAnswer" dir="auto">{summary}</p>}
    {onOpenTasks && <button type="button" className="linkButton" onClick={onOpenTasks}>{t('agents.run.openTasks')}</button>}
  </div>;
}

function useRunAsAgent(agent: AgentProfile, onRan: () => void) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<api.RunTaskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  async function run(prompt: string, title?: string) {
    setBusy(true); setError(null); setBudget(null); setResult(null);
    try { setResult(await api.createAndRunTask(prompt, title, agent.id)); onRan(); return true; }
    catch (e) { if (isBudgetExceeded(e)) setBudget(budgetMessage(e)); else setError(explain(e, 'agents.err.run')); return false; }
    finally { setBusy(false); }
  }
  return { busy, result, error, budget, run };
}

function RunFeedback({ runner, onOpenTasks, onOpenApprovals, onOpenBudgets }: { runner: ReturnType<typeof useRunAsAgent>; onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void }) {
  return <>
    {runner.error && <Notice error>{runner.error}</Notice>}
    {runner.budget && <Notice error>{t('common.budgetReached', { message: runner.budget })} {onOpenBudgets && <button type="button" className="linkButton" onClick={onOpenBudgets}>{t('agents.openBudgets')}</button>}</Notice>}
    {runner.result && <RunResult result={runner.result} onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} />}
  </>;
}

function OverviewTab({ agent, tasksVersion, onRan, onOpenTasks, onOpenApprovals, onOpenBudgets }: {
  agent: AgentProfile; tasksVersion: number; onRan: () => void; onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void;
}) {
  const [prompt, setPrompt] = useState('');
  const runner = useRunAsAgent(agent, onRan);
  const promptId = useId();
  return <div className="agentColumns">
    <div className="agentStack">
      <section className="surface agentPanel" aria-labelledby={`${promptId}-h`}>
        <h3 id={`${promptId}-h`}>{t('agents.run.title')}</h3>
        {agent.can_run ? <form onSubmit={(e) => { e.preventDefault(); if (prompt.trim()) void runner.run(prompt.trim()).then((ok) => { if (ok) setPrompt(''); }); }}>
          <label className="field"><span>{t('agents.run.label', { name: agent.name })}</span>
            <textarea id={promptId} className="short" value={prompt} maxLength={8000} dir="auto" placeholder={t('agents.run.placeholder')} onChange={(e) => setPrompt(e.target.value)} /></label>
          <div className="actions"><button type="submit" disabled={runner.busy || !prompt.trim()}><Play aria-hidden="true" />{runner.busy ? t('agents.run.running') : t('agents.run.submit')}</button></div>
        </form> : <p className="muted">{agent.status === 'paused' ? t('agents.run.paused') : t('agents.run.noPermission')}</p>}
        <RunFeedback runner={runner} onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />
      </section>
      <RecentTasks agent={agent} version={tasksVersion} onOpenTasks={onOpenTasks} />
    </div>
    <section className="surface agentPanel" aria-labelledby="agent-instructions-h">
      <h3 id="agent-instructions-h">{t('agents.field.instructions')}</h3>
      {agent.instructions ? <p className="agentInstructions" dir="auto">{agent.instructions}</p> : <p className="muted">{t('agents.noInstructions')}</p>}
      <dl className="agentFacts">
        <div><dt>{t('agents.field.model')}</dt><dd>{t(`agents.model.${agent.model_route}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.field.knowledge')}</dt><dd>{t(`agents.knowledge.${agent.knowledge_mode}` as MessageKey)}{agent.knowledge_mode === 'selected' ? ` (${agent.knowledge_sources.length})` : ''}</dd></div>
        <div><dt>{t('agents.field.autonomy')}</dt><dd>{TOOL_CATEGORIES.map((c) => `${t(`agents.toolCategory.${c}` as MessageKey)}: ${t(`agents.level.${agent.autonomy[c]}` as MessageKey)}`).join(t('common.listSeparator'))}</dd></div>
        <div><dt>{t('agents.updated')}</dt><dd>{formatDateTime(agent.updated_at)}</dd></div>
      </dl>
    </section>
  </div>;
}

function RecentTasks({ agent, version, onOpenTasks }: { agent: AgentProfile; version: number; onOpenTasks?: () => void }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try { setTasks((await api.apiRequest<api.ApiTask[]>(recentTasksPath(agent.id), { method: 'GET' })).map(api.mapTask)); }
    catch (e) { setError(explain(e, 'agents.err.tasks')); }
  }, [agent.id]);
  useEffect(() => { void load(); }, [load, version]);
  return <section className="surface agentPanel" aria-labelledby="agent-tasks-h">
    <div className="sectionHeader"><h3 id="agent-tasks-h">{t('agents.recentTasks')}</h3>{onOpenTasks && <button type="button" className="linkButton" onClick={onOpenTasks}>{t('agents.allTasks')}</button>}</div>
    {error && <><Notice error>{error}</Notice><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></>}
    {!tasks && !error && <p className="muted" role="status">{t('common.loading')}</p>}
    {tasks && tasks.length === 0 && <p className="muted">{t('agents.noTasks')}</p>}
    {tasks && tasks.length > 0 && <ul className="agentTaskList">{tasks.map((task) => <li key={task.id}>
      <span dir="auto">{task.title}</span>
      <span className={`statusBadge ${task.status === 'completed' ? 'success' : task.status === 'failed' || task.status === 'cancelled' ? 'dangerTone' : 'warning'}`}>{statusLabel(task.status)}</span>
      <time dateTime={task.createdAt}>{formatDateTime(task.createdAt)}</time>
    </li>)}</ul>}
  </section>;
}

function MemoryTab({ agent }: { agent: AgentProfile }) {
  const [items, setItems] = useState<AgentMemory[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [q, setQ] = useState('');
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<AgentMemory | null>(null);
  useEffect(() => { const timer = window.setTimeout(() => setQ(query), 250); return () => window.clearTimeout(timer); }, [query]);
  const load = useCallback(async () => {
    setState('loading'); setError(null);
    try { const page = await agents.memories(agent.id, q); setItems(page.items); setTotal(page.total); setCursor(page.next_cursor); setState('ready'); }
    catch (e) { setError(explain(e, 'agents.err.memories')); setState('error'); }
  }, [agent.id, q]);
  useEffect(() => { void load(); }, [load]);
  async function add(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    setBusy(true); setError(null);
    try { await agents.addMemory(agent.id, draft.trim()); setDraft(''); await load(); }
    catch (e) { setError(explain(e, 'agents.err.memorySave')); }
    finally { setBusy(false); }
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await agents.removeMemory(agent.id, removing.id); setRemoving(null); await load(); }
    catch (e) { setError(explain(e, 'agents.err.memoryDelete')); setRemoving(null); }
    finally { setBusy(false); }
  }
  async function loadMore() {
    if (!cursor) return;
    try { const page = await agents.memories(agent.id, q, cursor); setItems((old) => mergeById(old, page.items)); setCursor(page.next_cursor); }
    catch (e) { setError(explain(e, 'agents.err.memories')); }
  }
  const searchId = useId(), addId = useId();
  return <section className="surface agentPanel" aria-labelledby="agent-memory-h">
    <div className="sectionHeader"><h3 id="agent-memory-h">{t('agents.memory.title')}</h3><span className="muted">{t('agents.memory.count', { count: total })}</span></div>
    <p className="muted">{t('agents.memory.intro')}</p>
    {agent.can_edit ? <form className="memoryAdd" onSubmit={(e) => void add(e)}>
      <label className="field"><span>{t('agents.memory.add')}</span>
        <textarea id={addId} className="short" value={draft} maxLength={MEMORY_MAX} dir="auto" placeholder={t('agents.memory.placeholder')} onChange={(e) => setDraft(e.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={busy || !draft.trim()}><Plus aria-hidden="true" />{t('agents.memory.save')}</button></div>
    </form> : <p className="muted">{t('agents.memory.readOnly')}</p>}
    <div className="agentSearch memorySearch"><label className="visuallyHidden" htmlFor={searchId}>{t('agents.memory.search')}</label><Search aria-hidden="true" />
      <input id={searchId} type="search" value={query} placeholder={t('agents.memory.search')} onChange={(e) => setQuery(e.target.value)} /></div>
    {error && <Notice error>{error}</Notice>}
    {state === 'loading' && <p className="muted" role="status">{t('common.loading')}</p>}
    {state === 'error' && <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button>}
    {state === 'ready' && items.length === 0 && <p className="muted">{q ? t('agents.memory.noMatch') : t('agents.memory.empty')}</p>}
    {items.length > 0 && <ul className="agentMemoryList">{items.map((memory) => <li key={memory.id}>
      <div><p dir="auto">{memory.content}</p><small className="muted">{t(memory.source === 'task' ? 'agents.memory.fromTask' : 'agents.memory.manual')} · <time dateTime={memory.created_at}>{formatDateTime(memory.created_at)}</time></small></div>
      {agent.can_edit && <button type="button" className="iconButton" aria-label={t('agents.memory.deleteNamed', { text: memory.content.slice(0, 40) })} title={t('common.delete')} onClick={() => setRemoving(memory)}><Trash2 aria-hidden="true" /></button>}
    </li>)}</ul>}
    {cursor && <div className="actions"><button type="button" className="secondary" onClick={() => void loadMore()}>{t('agents.loadMore')}</button></div>}
    {removing && <ConfirmDialog title={t('agents.memory.deleteTitle')} confirmLabel={t('common.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p dir="auto">{removing.content}</p>
    </ConfirmDialog>}
  </section>;
}

function SettingsTab({ agent, catalog, busy, onUpdate, onDelete }: { agent: AgentProfile; catalog: AgentCatalog | null; busy: boolean; onUpdate: (body: { status?: AgentStatus; sharing?: 'private' | 'workspace' }) => void; onDelete: () => void }) {
  const isOwner = agent.owner_user_id === api.currentUserId();
  return <div className="agentColumns">
    <section className="surface agentPanel" aria-labelledby="agent-settings-h">
      <h3 id="agent-settings-h">{t('agents.tab.settings')}</h3>
      <dl className="agentFacts">
        <div><dt>{t('agents.owner')}</dt><dd dir="ltr">{isOwner ? t('agents.you') : agent.owner_user_id}</dd></div>
        <div><dt>{t('agents.createdAt')}</dt><dd>{formatDateTime(agent.created_at)}</dd></div>
      </dl>
      <fieldset className="field"><legend>{t('agents.field.sharing')}</legend>
        {isOwner ? <div className="choiceList">{(['private', 'workspace'] as const).map((sharing) => <label key={sharing} className="choice">
          <input type="radio" name="detail-sharing" disabled={busy} checked={agent.sharing === sharing} onChange={() => onUpdate({ sharing })} />
          <span><strong>{t(`agents.sharing.${sharing}` as MessageKey)}</strong><small>{t(`agents.sharing.${sharing}Text` as MessageKey)}</small></span>
        </label>)}</div> : <p className="muted">{t(`agents.sharing.${agent.sharing}` as MessageKey)} · {t('agents.sharingOwnerOnly')}</p>}
      </fieldset>
      <fieldset className="field"><legend>{t('agents.field.status')}</legend>
        {agent.can_edit ? <label className="settingToggle"><div><strong>{t('agents.activeToggle')}</strong><p>{t('agents.activeToggleText')}</p></div>
          <input type="checkbox" role="switch" disabled={busy} checked={agent.status === 'active'} onChange={(e) => onUpdate({ status: e.target.checked ? 'active' : 'paused' })} /></label>
          : <p className="muted">{t(`agents.status.${agent.status}` as MessageKey)}</p>}
      </fieldset>
    </section>
    <section className="surface agentPanel" aria-labelledby="agent-autonomy-h">
      <h3 id="agent-autonomy-h">{t('agents.field.autonomy')}</h3>
      <ul className="agentCapList">{TOOL_CATEGORIES.map((category) => {
        const level = agent.autonomy[category];
        const floor = catalog?.autonomy_floor[category] ?? level;
        const effective = effectiveLevel(level, floor);
        return <li key={category}><strong>{t(`agents.toolCategory.${category}` as MessageKey)}: {t(`agents.level.${effective}` as MessageKey)}</strong>
          <small>{t(`agents.levelText.${effective}` as MessageKey)}{effective !== level ? ` ${t('agents.floorNote')}` : ''}</small></li>;
      })}</ul>
      {agent.can_edit && <div className="dangerZone"><h4>{t('agents.deleteTitle', { name: agent.name })}</h4><p className="muted">{t('agents.deleteText')}</p>
        <div className="actions"><button type="button" className="danger" onClick={onDelete}><Trash2 aria-hidden="true" />{t('agents.delete')}</button></div></div>}
    </section>
  </div>;
}

function CallPanel({ agent, onRan, onOpenApprovals, onOpenBudgets }: { agent: AgentProfile; onRan: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void }) {
  const [notes, setNotes] = useState('');
  const runner = useRunAsAgent(agent, onRan);
  const notesId = useId();
  return <section className="surface agentPanel" aria-labelledby={`${notesId}-h`}>
    <h3 id={`${notesId}-h`}>{t('agents.callTitle')}</h3>
    <Notice>{t('agents.callNote')}</Notice>
    {agent.can_run ? <form onSubmit={(e) => { e.preventDefault(); if (notes.trim()) void runner.run(callSummaryPrompt(notes, t('agents.callPrompt')), t('agents.callTaskTitle', { name: agent.name })); }}>
      <label className="field"><span>{t('agents.callLabel')}</span>
        <textarea id={notesId} value={notes} maxLength={7000} dir="auto" rows={8} placeholder={t('agents.callPlaceholder')} onChange={(e) => setNotes(e.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={runner.busy || !notes.trim()}><FileText aria-hidden="true" />{runner.busy ? t('agents.run.running') : t('agents.callSubmit')}</button></div>
    </form> : <p className="muted">{t('agents.run.noPermission')}</p>}
    <RunFeedback runner={runner} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />
  </section>;
}
