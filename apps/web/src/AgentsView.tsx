// Agents (docs/agents.md): the agents people build in a workspace. The page head with the My agents,
// Templates, Org chart and Spending tabs; a card grid with an overview rail and a preview panel; a
// create/edit wizard (start from a template or from scratch); and an agent's page with Chat, Call,
// Edit and More, the identity and boundaries side cards, and the Overview, Skills, Memory, Settings
// and Activity tabs.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowLeft, Bot, Check, Ellipsis, FileText, History, LayoutTemplate, Lock, MessageSquare,
  Pause, Pencil, Phone, Play, Plus, RefreshCw, Search, Settings2, ShieldCheck, Trash2, Wallet,
} from 'lucide-react';
import type { Task } from '@anum/contracts';
import { AgentMemoryPanel } from './AgentMemoryPanel';
import { AgentSkillsPanel } from './AgentSkillsPanel';
import { AgentList } from './AgentsList';
import { AGENT_ICONS, AgentAvatar, BoundaryList, capText, explain, Meter, Notice, useAgentCaps, useRole, type CapsLoad, type Role } from './AgentsParts';
import { AvatarStyleEditor, roleFamilyOf } from './AvatarStyleEditor';
import { ChatView } from './ChatView';
import { ConfirmDialog } from './Dialog';
import { FinancePageHead, FinanceTabs, FinCard, StatusChip, tabPanel } from './FinanceShell';
import { OrgChartView, openByHash, type OrgGo } from './OrgChartView';
import { OrgProfile } from './OrgProfile';
import { Breadcrumbs, PaneDenied } from './PageParts';
import { parseProfileHash, profileHash, TEAM_HASH, type ProfileRef, type ProfileTab } from './lib/orgChart';
import * as api from './lib/api';
import { formatUsd } from './lib/admin';
import { capFor } from './lib/agentsPage';
import {
  agentsApi, ALWAYS_ON_TOOL, CATEGORIES, COLORS, DESCRIPTION_MAX, DETAIL_TABS, draftBody, draftChanges, draftFromAgent,
  draftFromTemplate, EDIT_STEPS, effectiveLevel, emptyDraft, INSTRUCTIONS_MAX, levelsFor, NAME_MAX,
  recentTasksPath, skillsMissingTools, templateKey, toggleSkill, toggleTool, TOOL_CATEGORIES, toolGroups, validateDraft,
  WIZARD_STEPS, callSummaryPrompt,
  type AgentCatalog, type AgentDraft, type AgentProfile, type AgentStatus, type AgentTemplate,
  type DetailTab, type DraftErrors, type WizardStep,
} from './lib/agents';
import { budgetMessage, isBudgetExceeded } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { statusLabel } from './lib/labels';
import './agents.css';
import './agentsPage.css';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);

type Panel = DetailTab | 'activity' | 'chat' | 'call';
type Mode = { kind: 'list' } | { kind: 'detail'; id: string; panel?: Panel } | { kind: 'wizard'; agent: AgentProfile | null; template?: AgentTemplate | null } | { kind: 'profile'; target: ProfileRef & { tab: ProfileTab } };

/** What the URL hash opens: a member's profile (`#agents/team/...`), the org chart tab (`#agents/team`) or the list. */
function modeFromHash(): Mode {
  const target = typeof window === 'undefined' ? null : parseProfileHash(window.location.hash);
  return target ? { kind: 'profile', target } : { kind: 'list' };
}
type ListTab = 'agents' | 'templates' | 'org' | 'spending';
const LIST_TABS: readonly ListTab[] = ['agents', 'templates', 'org', 'spending'];
const TAB_KEY = 'anum.agents.tab';

function readTab(): ListTab {
  try { const saved = localStorage.getItem(TAB_KEY) as ListTab | null; return saved && LIST_TABS.includes(saved) ? saved : 'agents'; } catch { return 'agents'; }
}

export function AgentsView({ onOpenSkills, onOpenTasks, onOpenApprovals, onOpenBudgets, onOpenFiles, onGo }: {
  onOpenSkills?: () => void; onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void; onOpenFiles?: () => void;
  /** Opens Integrations, Assistants, Team or Automation from the org chart (without it, the URL hash does). */
  onGo?: (view: OrgGo) => void;
}) {
  const [mode, setMode] = useState<Mode>(modeFromHash);
  const [tab, setTabState] = useState<ListTab>(() => typeof window !== 'undefined' && window.location.hash.startsWith(TEAM_HASH) ? 'org' : readTab());
  // Back and Forward move between the org chart and the profiles opened from it.
  useEffect(() => {
    const onPop = () => { const next = modeFromHash(); if (next.kind === 'profile' || window.location.hash.startsWith('#agents')) { setMode(next); if (next.kind === 'profile') setTabState('org'); } };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  const openProfile = (ref: ProfileRef) => {
    window.history.pushState({ anumProfile: true }, '', profileHash(ref));
    go({ kind: 'profile', target: { ...ref, tab: 'overview' } });
  };
  const leaveProfile = () => {
    if ((window.history.state as { anumProfile?: boolean } | null)?.anumProfile) { window.history.back(); return; }
    window.history.replaceState(null, '', TEAM_HASH);
    setTab('org'); go({ kind: 'list' });
  };
  const setTab = (next: ListTab) => {
    setTabState(next);
    try { localStorage.setItem(TAB_KEY, next); } catch { /* storage blocked */ }
    if (window.location.hash.startsWith('#agents')) window.history.replaceState(null, '', next === 'org' ? TEAM_HASH : '#agents');
  };
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const role = useRole();
  const { caps, reload: reloadCaps } = useAgentCaps();
  const loadCatalog = useCallback(async () => {
    setCatalogError(null);
    try { setCatalog(await agents.catalog()); } catch (e) { setCatalogError(explain(e, 'agents.err.catalog')); }
  }, []);
  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  const top = useRef<HTMLDivElement>(null);
  const go = (next: Mode, message: string | null = null) => {
    // Leaving a profile for the agent page or the editor drops its address, so a reload or a later Back does not reopen it.
    if (next.kind !== 'profile' && window.location.hash.startsWith(`${TEAM_HASH}/`)) window.history.replaceState(null, '', next.kind === 'list' ? TEAM_HASH : '#agents');
    setMode(next); setFlash(message);
    window.requestAnimationFrame(() => top.current?.focus());
  };
  const canCreate = role === 'owner' || role === 'member';
  const create = (template: AgentTemplate | null = null) => go({ kind: 'wizard', agent: null, template });
  const backToList: { label: string; href: string; onClick: () => void } = { label: t('nav.agents'), href: '#agents', onClick: () => go({ kind: 'list' }) };
  return <div className="agentsView agPage" ref={top} tabIndex={-1}>
    {mode.kind === 'list' && <FinancePageHead title={t('nav.agents')} subtitle={t('agentsPage.subtitle')}
      actions={canCreate ? <>
        <button type="button" onClick={() => create()}><Plus aria-hidden="true" />{t('agents.create')}</button>
        <button type="button" className="secondary" onClick={() => setTab('templates')}><LayoutTemplate aria-hidden="true" />{t('agentsPage.tab.templates')}</button>
      </> : undefined}
      tabs={<FinanceTabs label={t('org.tabs')} idPrefix="agents" value={tab} onChange={setTab} items={[
        { id: 'agents', label: t('agentsPage.tab.agents') }, { id: 'templates', label: t('agentsPage.tab.templates') },
        { id: 'org', label: t('org.tab.org') }, { id: 'spending', label: t('agentsPage.tab.spending') },
      ]} />} />}
    {mode.kind === 'profile' && <FinancePageHead title={t('nav.agents')} crumbs={[backToList, { label: t('org.tab.org') }]} />}
    {flash && <Notice>{flash}</Notice>}
    {mode.kind === 'profile' && <OrgProfile key={`${mode.target.kind}:${mode.target.id}`} target={mode.target} role={role} onBack={leaveProfile}
      onTab={(next) => { window.history.replaceState(window.history.state, '', profileHash(mode.target, next)); setMode({ kind: 'profile', target: { ...mode.target, tab: next } }); }}
      onOpenAgent={(id) => go({ kind: 'detail', id })} onEditAgent={(agent) => go({ kind: 'wizard', agent })} onOpenBudgets={onOpenBudgets}
      onGo={(view) => { if (view === 'approvals' && onOpenApprovals) onOpenApprovals(); else if (view === 'tasks' && onOpenTasks) onOpenTasks(); else if (onGo) onGo(view); else openByHash(view); }} />}
    {mode.kind === 'list' && <div {...tabPanel('agents', tab)}>
      {tab === 'org' && <OrgChartView role={role} onOpenProfile={openProfile} onOpenAgent={(id) => go({ kind: 'detail', id })} onEditAgent={(agent) => go({ kind: 'wizard', agent })}
        onCreate={() => create()} onOpenApprovals={onOpenApprovals} onOpenTasks={onOpenTasks} onOpenBudgets={onOpenBudgets} onGo={onGo} />}
      {tab === 'agents' && <AgentList catalog={catalog} catalogError={catalogError} role={role} caps={caps} onRetryCatalog={() => void loadCatalog()}
        onOpen={(id, panel) => go({ kind: 'detail', id, panel })} onCreate={() => create()} onTemplates={() => setTab('templates')} onOpenSkills={onOpenSkills} onOpenApprovals={onOpenApprovals} />}
      {tab === 'templates' && <TemplatesTab catalog={catalog} catalogError={catalogError} canCreate={canCreate} onRetry={() => void loadCatalog()} onUse={create} />}
      {tab === 'spending' && <SpendingTab caps={caps} onRetry={reloadCaps} onOpen={(id) => go({ kind: 'detail', id })} />}
    </div>}
    {mode.kind === 'wizard' && <AgentWizard catalog={catalog} catalogError={catalogError} onRetryCatalog={() => void loadCatalog()} agent={mode.agent} template={mode.template ?? null} crumbs={backToList}
      onCancel={() => go(mode.agent ? { kind: 'detail', id: mode.agent.id } : { kind: 'list' })}
      onSaved={(saved, created) => go({ kind: 'detail', id: saved.id }, t(created ? 'agents.created' : 'agents.saved', { name: saved.name }))}
      onOpenSkills={onOpenSkills} onOpenFiles={onOpenFiles} />}
    {mode.kind === 'detail' && <AgentDetail key={mode.id} id={mode.id} initialPanel={mode.panel} catalog={catalog} catalogError={catalogError} caps={caps} role={role} crumbs={backToList}
      onOpenSkills={onOpenSkills} onBack={() => go({ kind: 'list' })}
      onEdit={(agent) => go({ kind: 'wizard', agent })} onDeleted={(name) => go({ kind: 'list' }, t('agents.deleted', { name }))}
      onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />}
  </div>;
}

// ---------------------------------------------------------------------------- templates and spending tabs

function TemplatesTab({ catalog, catalogError, canCreate, onRetry, onUse }: {
  catalog: AgentCatalog | null; catalogError: string | null; canCreate: boolean; onRetry: () => void; onUse: (template: AgentTemplate | null) => void;
}) {
  if (catalogError) return <div role="alert"><Notice error>{catalogError}</Notice><div className="actions"><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>;
  if (!catalog) return <p className="muted agentState" role="status">{t('agents.loadingCatalog')}</p>;
  return <FinCard title={t('agentsPage.templates.title', { count: catalog.templates.length })} icon={<LayoutTemplate />} subtitle={t('agentsPage.templates.text')}>
    {!canCreate && <Notice>{t('agents.viewerNote')}</Notice>}
    {catalog.templates.length === 0 && <div className="emptyState"><LayoutTemplate aria-hidden="true" /><h3>{t('agentsPage.templates.empty')}</h3>
      {canCreate && <button type="button" onClick={() => onUse(null)}><Plus aria-hidden="true" />{t('agents.wizard.blank')}</button>}</div>}
    {catalog.templates.length > 0 && <ul className="agTemplateGrid" aria-label={t('agentsPage.tab.templates')}>{catalog.templates.map((template) => <li key={template.id} className="agTemplate">
      <AgentAvatar icon={template.icon} color={template.color} />
      <div><h3>{t(templateKey(template.id, 'name'))}</h3><p className="agSmall muted">{t(templateKey(template.id, 'description'))}</p>
        {template.missing_capabilities.length > 0 && <p className="agSmall templateMissing">{t('agents.wizard.notConnected', { what: template.missing_capabilities.join(t('common.listSeparator')) })}</p>}</div>
      {canCreate && <button type="button" className="secondary" aria-label={t('agentsPage.templates.useNamed', { name: t(templateKey(template.id, 'name')) })} onClick={() => onUse(template)}>{t('agentsPage.templates.use')}</button>}
    </li>)}</ul>}
  </FinCard>;
}

function SpendingTab({ caps, onRetry, onOpen }: { caps: CapsLoad; onRetry: () => void; onOpen: (id: string) => void }) {
  if (caps.state === 'loading') return <p className="muted agentState" role="status">{t('common.loading')}</p>;
  if (caps.state === 'denied') return <PaneDenied title={t('agentsPage.spending.deniedTitle')} text={caps.message} />;
  if (caps.state === 'error') return <div role="alert"><Notice error>{t('agentsPage.spend.failed', { message: caps.message })}</Notice><div className="actions"><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>;
  return <FinCard title={t('agentsPage.spending.title')} icon={<Wallet />} subtitle={t('agentsPage.spend.resets', { date: formatDateTime(caps.resetsOn) })}
    actions={<a className="button secondary" href="#agentControls"><Settings2 aria-hidden="true" />{t('agentsPage.spending.manage')}</a>}>
    {caps.total > caps.items.length && <Notice>{t('agentsPage.spending.partial', { shown: caps.items.length, total: caps.total })}</Notice>}
    <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('agentsPage.spending.title')}><table className="finGrid">
      <thead><tr><th scope="col">{t('agentsPage.spending.agent')}</th><th scope="col">{t('agents.field.status')}</th><th scope="col" className="num">{t('agentsPage.spending.used')}</th><th scope="col" className="num">{t('agentsPage.spending.cap')}</th><th scope="col">{t('agentsPage.spending.share')}</th></tr></thead>
      <tbody>{caps.items.map((cap) => <tr key={cap.agent_id}>
        <th scope="row">{cap.is_default ? <span dir="auto">{cap.name}</span> : <button type="button" className="linkButton" onClick={() => onOpen(cap.agent_id)} dir="auto">{cap.name}</button>}</th>
        <td><StatusChip tone={cap.exceeded ? 'bad' : cap.status === 'active' ? 'ok' : 'warn'}>{cap.exceeded ? t('agentsPage.spending.overCap') : t(cap.status === 'paused' ? 'agents.status.paused' : 'agents.status.active')}</StatusChip></td>
        <td className="num">{formatUsd(cap.usage.estimated_cost_usd)}</td>
        <td className="num">{cap.monthly_cost_limit_usd === null ? t('agentControls.noCap') : formatUsd(cap.monthly_cost_limit_usd)}</td>
        <td>{cap.percent_used === null ? '-' : <span className="agMeterCell"><Meter percent={cap.percent_used} label={t('agentsPage.spend.meter', { percent: Math.round(cap.percent_used) })} /><span>{t('agentsPage.percent', { percent: Math.round(cap.percent_used) })}</span></span>}</td>
      </tr>)}</tbody>
    </table></div>
  </FinCard>;
}

// ---------------------------------------------------------------------------- wizard

function FieldError({ id, error }: { id: string; error?: MessageKey }) {
  return error ? <small id={id} className="fieldError" role="alert">{t(error)}</small> : null;
}

function AgentWizard({ catalog, catalogError, onRetryCatalog, agent, template, crumbs, onCancel, onSaved, onOpenSkills, onOpenFiles }: {
  catalog: AgentCatalog | null; catalogError: string | null; onRetryCatalog: () => void; agent: AgentProfile | null; template: AgentTemplate | null;
  crumbs: { label: string; href: string; onClick: () => void };
  onCancel: () => void; onSaved: (agent: AgentProfile, created: boolean) => void; onOpenSkills?: () => void; onOpenFiles?: () => void;
}) {
  const steps = agent ? EDIT_STEPS : WIZARD_STEPS;
  // A template picked on the Templates tab skips the Start step.
  const [step, setStep] = useState<WizardStep>(template && !agent ? 'identity' : steps[0]);
  const [draft, setDraft] = useState<AgentDraft>(() => agent ? draftFromAgent(agent) : template ? draftFromTemplate(template, t(templateKey(template.id, 'name')), t(templateKey(template.id, 'description'))) : emptyDraft());
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
  const title = agent ? t('agents.wizard.editTitle', { name: agent.name }) : t('agents.wizard.createTitle');
  return <section className="surface agentWizard" aria-labelledby="view-title">
    <Breadcrumbs trail={[crumbs, ...(agent ? [{ label: agent.name }] : []), { label: agent ? t('agents.edit') : t('agents.create') }]} />
    <div className="wizardHead">
      <button type="button" className="secondary" onClick={onCancel}><ArrowLeft className="flipRtl" aria-hidden="true" />{t('common.cancel')}</button>
      <h1 id="view-title" className="agWizardTitle">{title}</h1>
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
            {catalog.icons.map((icon) => { const Icon = AGENT_ICONS[icon] ?? Bot; return <button key={icon} type="button" role="radio" aria-checked={draft.icon === icon} aria-label={t(`agents.icon.${icon}` as MessageKey)} title={t(`agents.icon.${icon}` as MessageKey)} onClick={() => set('icon', icon)}><Icon aria-hidden="true" /></button>; })}
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

function AgentDetail({ id, initialPanel, catalog, catalogError, caps, role, crumbs, onOpenSkills, onBack, onEdit, onDeleted, onOpenTasks, onOpenApprovals, onOpenBudgets }: {
  id: string; initialPanel?: Panel; catalog: AgentCatalog | null; catalogError: string | null; caps: CapsLoad; role: Role; crumbs: { label: string; href: string; onClick: () => void };
  onOpenSkills?: () => void; onBack: () => void; onEdit: (agent: AgentProfile) => void; onDeleted: (name: string) => void;
  onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void;
}) {
  const [agent, setAgent] = useState<AgentProfile | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'missing'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(initialPanel ?? 'overview');
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
  const head = (title: string) => <><Breadcrumbs trail={[crumbs, { label: title }]} /><h1 id="view-title" className="visuallyHidden">{title}</h1>{back}</>;
  if (state === 'loading') return <>{head(t('nav.agents'))}<p className="muted agentState" role="status">{t('agents.loadingOne')}</p></>;
  if (state === 'missing') return <>{head(t('agents.missingTitle'))}<div className="emptyState"><Search aria-hidden="true" /><h3>{t('agents.missingTitle')}</h3><p>{t('agents.missingText')}</p></div></>;
  if (state === 'error' || !agent) return <>{head(t('nav.agents'))}<Notice error>{error}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></div></>;
  const paused = agent.status === 'paused';
  const cap = capFor(caps.state === 'ready' ? caps.items : null, agent.id);
  const tabs: Panel[] = [...DETAIL_TABS];
  return <>
    <Breadcrumbs trail={[crumbs, { label: agent.name }]} />
    <section className="agHero" aria-labelledby="view-title">
      <AgentAvatar icon={agent.icon} color={agent.color} size="xl" />
      <div className="agentHeroText">
        <div className="agHeroTitle"><h1 id="view-title" dir="auto">{agent.name}</h1><StatusChip tone={paused ? 'warn' : 'ok'}>{t(`agents.status.${agent.status}` as MessageKey)}</StatusChip></div>
        <p className="muted" dir="auto"><strong>{t('agentsPage.preview.purpose')}:</strong> {agent.description || t('agents.noDescription')}</p>
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
    <nav className="settingsTabs agentTabs agDetailTabs" aria-label={t('agents.sections')}>
      {tabs.map((tab) => <button key={tab} type="button" aria-current={panel === tab ? 'page' : undefined} onClick={() => setPanel(tab)}>{t(`agents.tab.${tab}` as MessageKey)}</button>)}
    </nav>
    {paused && <Notice>{agent.can_edit ? t('agents.pausedOwner') : t('agents.pausedOther')}</Notice>}
    {!agent.can_edit && <Notice>{t('agents.readOnly')}</Notice>}
    {actionError && <Notice error>{actionError}</Notice>}
    {(agent.unavailable_tools.length > 0 || agent.unavailable_skills.length > 0) && <Notice error>{t('agents.unavailable', { names: [...agent.unavailable_tools, ...agent.unavailable_skills].join(t('common.listSeparator')) })}</Notice>}
    <div className="agDetailGrid">
      <IdentityCard agent={agent} caps={caps} cap={cap} role={role} />
      <div className="agDetailMain">
        {panel === 'overview' && <OverviewTab agent={agent} tasksVersion={tasksVersion} onOpenTasks={onOpenTasks} />}
        {panel === 'skills' && <AgentSkillsPanel agent={agent} catalog={catalog} catalogError={catalogError} onAgentChanged={setAgent} onOpenSkills={onOpenSkills} />}
        {panel === 'memory' && <AgentMemoryPanel agentId={agent.id} canEdit={agent.can_edit} />}
        {panel === 'settings' && <SettingsTab agent={agent} catalog={catalog} busy={busy} onUpdate={(body) => void update(body)} onDelete={() => setConfirmDelete(true)}
          onSaveAvatar={async (avatar) => { setAgent(await agents.update(agent.id, { avatar, version: agent.version })); }} />}
        {panel === 'chat' && <section className="agentChat" aria-label={t('agents.chatWith', { name: agent.name })}><ChatView key={agent.id} agent={{ id: agent.id, name: agent.name }} onOpenBudgets={onOpenBudgets ?? (() => undefined)} /></section>}
        {panel === 'call' && <CallPanel agent={agent} onRan={() => setTasksVersion((v) => v + 1)} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />}
      </div>
      <div className="agDetailSide">
        <FinCard title={t('agentsPage.preview.boundaries')} icon={<ShieldCheck />}>
          <BoundaryList agent={agent} catalog={catalog} />
          <p className="agSmall muted">{t('agentsPage.bound.note')}</p>
        </FinCard>
        <RunCard agent={agent} onRan={() => setTasksVersion((v) => v + 1)} onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />
        <FinCard title={t('agentsPage.version.title')} icon={<History />} actions={<StatusChip tone="info">{t('agentsPage.version.current', { version: agent.version })}</StatusChip>}>
          <dl className="agFactList">
            <div><dt>{t('agentsPage.version.changed')}</dt><dd>{formatDateTime(agent.updated_at)}</dd></div>
            <div><dt>{t('agents.createdAt')}</dt><dd>{formatDateTime(agent.created_at)}</dd></div>
          </dl>
          <p className="agSmall muted">{t('agentsPage.version.note')}</p>
        </FinCard>
      </div>
    </div>
    {confirmDelete && <ConfirmDialog title={t('agents.deleteTitle', { name: agent.name })} confirmLabel={t('agents.delete')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setConfirmDelete(false)}>
      <p>{t('agents.deleteText')}</p>
    </ConfirmDialog>}
  </>;
}

/** Who the agent is: owner, model, privacy, this month's spending (owners only) and when it last changed. */
function IdentityCard({ agent, caps, cap, role }: { agent: AgentProfile; caps: CapsLoad; cap: import('./lib/agentControls').AgentCap | null; role: Role }) {
  const isOwner = agent.owner_user_id === api.currentUserId();
  return <FinCard title={t('agentsPage.identity.title')} className="agIdentity">
    <div className="agIdentityHead"><AgentAvatar icon={agent.icon} color={agent.color} size="lg" />
      <div><strong dir="auto">{agent.name}</strong><StatusChip tone={agent.status === 'paused' ? 'warn' : 'ok'}>{t(`agents.status.${agent.status}` as MessageKey)}</StatusChip></div></div>
    <dl className="agFactList">
      <div><dt>{t('agents.owner')}</dt><dd>{isOwner ? t('agents.you') : t('agentsPage.preview.someoneElse')}</dd></div>
      <div><dt>{t('agentsPage.card.model')}</dt><dd>{t(`agents.model.${agent.model_route}` as MessageKey)}</dd></div>
      <div><dt>{t('agentsPage.identity.privacy')}</dt><dd>{agent.sharing === 'workspace' ? t('agents.sharing.workspace') : t('agents.sharing.private')}</dd></div>
      <div><dt>{t('agents.field.category')}</dt><dd>{t(`agents.category.${agent.category}` as MessageKey)}{agent.department ? ` · ${agent.department}` : ''}</dd></div>
      <div><dt>{t('agentsPage.card.spend')}</dt><dd>{cap ? capText(cap) : caps.state === 'denied' || role !== 'owner' ? <span className="muted"><Lock aria-hidden="true" className="inlineIcon" />{t('agentsPage.spend.ownersOnlyShort')}</span> : '-'}</dd></div>
    </dl>
    {cap?.percent_used !== null && cap?.percent_used !== undefined && <Meter percent={cap.percent_used} label={t('agentsPage.spend.meter', { percent: Math.round(cap.percent_used) })} />}
    <p className="agSmall muted">{t('agentsPage.card.updated', { when: formatDateTime(agent.updated_at) })}</p>
  </FinCard>;
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

/** "Run a task" with this agent: a real task, so anything that sends, pays or acts outside ANUM waits for approval. */
function RunCard({ agent, onRan, onOpenTasks, onOpenApprovals, onOpenBudgets }: {
  agent: AgentProfile; onRan: () => void; onOpenTasks?: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void;
}) {
  const [prompt, setPrompt] = useState('');
  const runner = useRunAsAgent(agent, onRan);
  const promptId = useId();
  return <FinCard title={t('agents.run.title')} icon={<Play />} id={`${promptId}-h`} actions={<StatusChip tone="warn">{t('agentsPage.run.approvals')}</StatusChip>}>
    <p className="agSmall muted">{t('agentsPage.run.note')}</p>
    {agent.can_run ? <form onSubmit={(e) => { e.preventDefault(); if (prompt.trim()) void runner.run(prompt.trim()).then((ok) => { if (ok) setPrompt(''); }); }}>
      <label className="field"><span>{t('agents.run.label', { name: agent.name })}</span>
        <textarea id={promptId} className="short" value={prompt} maxLength={8000} dir="auto" placeholder={t('agents.run.placeholder')} onChange={(e) => setPrompt(e.target.value)} /></label>
      <div className="actions"><button type="submit" disabled={runner.busy || !prompt.trim()}><Play aria-hidden="true" />{runner.busy ? t('agents.run.running') : t('agents.run.submit')}</button></div>
    </form> : <p className="muted">{agent.status === 'paused' ? t('agents.run.paused') : t('agents.run.noPermission')}</p>}
    <RunFeedback runner={runner} onOpenTasks={onOpenTasks} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} />
  </FinCard>;
}

function OverviewTab({ agent, tasksVersion, onOpenTasks }: { agent: AgentProfile; tasksVersion: number; onOpenTasks?: () => void }) {
  return <div className="agentStack">
    <section className="surface agentPanel" aria-labelledby="agent-instructions-h">
      <h3 id="agent-instructions-h">{t('agents.field.instructions')}</h3>
      {agent.instructions ? <p className="agentInstructions" dir="auto">{agent.instructions}</p> : <p className="muted">{t('agents.noInstructions')}</p>}
      <dl className="agentFacts">
        <div><dt>{t('agents.field.model')}</dt><dd>{t(`agents.model.${agent.model_route}` as MessageKey)}</dd></div>
        <div><dt>{t('agents.field.knowledge')}</dt><dd>{t(`agents.knowledge.${agent.knowledge_mode}` as MessageKey)}{agent.knowledge_mode === 'selected' ? ` (${agent.knowledge_sources.length})` : ''}</dd></div>
        <div><dt>{t('agents.field.autonomy')}</dt><dd>{TOOL_CATEGORIES.map((c) => `${t(`agents.toolCategory.${c}` as MessageKey)}: ${t(`agents.level.${agent.autonomy[c]}` as MessageKey)}`).join(t('common.listSeparator'))}</dd></div>
        <div><dt>{t('agents.field.tools')}</dt><dd dir="ltr">{[ALWAYS_ON_TOOL, ...agent.allowed_tools].join(', ')}</dd></div>
      </dl>
    </section>
    <RecentTasks agent={agent} version={tasksVersion} onOpenTasks={onOpenTasks} />
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

function SettingsTab({ agent, catalog, busy, onUpdate, onDelete, onSaveAvatar }: { agent: AgentProfile; catalog: AgentCatalog | null; busy: boolean; onUpdate: (body: { status?: AgentStatus; sharing?: 'private' | 'workspace' }) => void; onDelete: () => void; onSaveAvatar: (avatar: AgentProfile['avatar']) => Promise<void> }) {
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
    <section className="surface agentPanel">
      <AvatarStyleEditor seed={agent.id} role={roleFamilyOf(agent.department)} value={agent.avatar} canEdit={agent.can_edit} onSave={onSaveAvatar} />
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
