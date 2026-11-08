// The Agents page's "My agents" tab (docs/agents.md#the-agents-page): an overview rail (counts,
// this month's spending for owners, a start-here card and the permission rules), the filtered card
// grid, and a preview panel for the selected agent on wide screens. Every figure is from the API.
import { useCallback, useEffect, useId, useState } from 'react';
import {
  ArrowRight, Bot, ChevronRight, Clock, Cpu, ExternalLink, FileText, Lock, MessageSquare, Phone, Plus, RefreshCw, Search, ShieldCheck, Sparkles, Users, Wallet, Wrench, X,
} from 'lucide-react';
import { AvatarChip } from './AvatarStyleEditor';
import { AgentAvatar, BoundaryList, capText, explain, Meter, Notice, type CapsLoad, type Role } from './AgentsParts';
import { FinBanner, FinCard, StatusChip } from './FinanceShell';
import * as api from './lib/api';
import { formatUsd } from './lib/admin';
import { approvalSummary, capFor, spendTotals, toolSummary } from './lib/agentsPage';
import {
  agentsApi, CATEGORIES, mergeById, skillNames, type AgentCatalog, type AgentProfile, type AgentStatus, type CategoryFilter, type DetailTab,
} from './lib/agents';
import { formatDateTime, t, type MessageKey } from './lib/i18n';

const agents = agentsApi(api.apiRequest, api.apiRequestVoid);

type StatusFilter = AgentStatus | 'all';

export function AgentList({ catalog, catalogError, role, caps, onOpen, onCreate, onTemplates, onRetryCatalog, onOpenSkills, onOpenApprovals }: {
  catalog: AgentCatalog | null; catalogError: string | null; role: Role; caps: CapsLoad;
  onOpen: (id: string, tab?: DetailTab | 'chat' | 'call') => void; onCreate: () => void; onTemplates: () => void; onRetryCatalog: () => void;
  onOpenSkills?: () => void; onOpenApprovals?: () => void;
}) {
  const [category, setCategory] = useState<CategoryFilter>('all');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [query, setQuery] = useState('');
  const [q, setQ] = useState('');
  const [items, setItems] = useState<AgentProfile[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({ all: 0, work: 0, personal: 0, custom: 0 });
  const [statusCounts, setStatusCounts] = useState<Record<StatusFilter, number | null>>({ all: null, active: null, paused: null });
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const canCreate = role === 'owner' || role === 'member';
  useEffect(() => { const timer = window.setTimeout(() => setQ(query), 250); return () => window.clearTimeout(timer); }, [query]);
  const load = useCallback(async () => {
    setState('loading'); setError(null);
    try {
      const page = await agents.list({ category, q, status });
      setItems(page.items); setCounts(page.counts); setCursor(page.next_cursor); setState('ready');
      setSelected((current) => current && page.items.some((a) => a.id === current) ? current : page.items[0]?.id ?? null);
    } catch (e) { setError(explain(e, 'agents.err.list')); setState('error'); }
  }, [category, q, status]);
  useEffect(() => { void load(); }, [load]);
  // The status chips' counts: one small read per status, with the same search and category.
  useEffect(() => {
    let live = true;
    const count = (s: StatusFilter) => agents.list({ category, q, status: s, limit: 1 }).then((page) => page.counts[category] ?? page.counts.all, () => null);
    void Promise.all([count('all'), count('active'), count('paused')]).then(([all, active, paused]) => { if (live) setStatusCounts({ all, active, paused }); });
    return () => { live = false; };
  }, [category, q]);
  async function loadMore() {
    if (!cursor) return;
    setMore(true);
    try { const page = await agents.list({ category, q, status, cursor }); setItems((old) => mergeById(old, page.items)); setCursor(page.next_cursor); }
    catch (e) { setError(explain(e, 'agents.err.list')); }
    finally { setMore(false); }
  }
  const filtered = Boolean(q.trim()) || status !== 'all' || category !== 'all';
  const searchId = useId();
  const capItems = caps.state === 'ready' ? caps.items : null;
  const preview = items.find((a) => a.id === selected) ?? null;
  return <div className="agBoard">
    <aside className="agRail" aria-label={t('agentsPage.rail')}>
      <OverviewCard counts={statusCounts} />
      <SpendCard caps={caps} />
      {canCreate && <StartCard onCreate={onCreate} onTemplates={onTemplates} />}
      <FinCard title={t('agentsPage.audit.title')} icon={<ShieldCheck />} className="agAuditCard">
        <p className="agSmall">{t('agentsPage.audit.text')}</p>
        {onOpenApprovals && <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('agentsPage.audit.approvals')}</button>}
      </FinCard>
    </aside>
    <div className="agMain">
      {role === 'viewer' && <Notice>{t('agents.viewerNote')}</Notice>}
      <div className="agFilters">
        <div className="agChips" role="group" aria-label={t('agents.filter.status')}>
          {(['all', 'active', 'paused'] as const).map((s) => <button key={s} type="button" className={`agChip ${s}`} aria-pressed={status === s} onClick={() => setStatus(s)}>
            {s !== 'all' && <span className="agDot" aria-hidden="true" />}{t(s === 'all' ? 'agentsPage.status.all' : `agents.status.${s}` as MessageKey)}{statusCounts[s] !== null && <span className="agentCount">{statusCounts[s]}</span>}
          </button>)}
        </div>
        <div className="segmented agCategories" role="group" aria-label={t('agents.filter.category')}>
          {(['all', ...CATEGORIES] as CategoryFilter[]).map((c) => <button key={c} type="button" aria-pressed={category === c} onClick={() => setCategory(c)}>
            {t(`agents.category.${c}` as MessageKey)} <span className="agentCount">{counts[c] ?? 0}</span>
          </button>)}
        </div>
        <div className="agentSearch">
          <label className="visuallyHidden" htmlFor={searchId}>{t('agents.search')}</label>
          <Search aria-hidden="true" />
          <input id={searchId} type="search" value={query} placeholder={t('agents.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </div>
      {state === 'loading' && <p className="muted agentState" role="status">{t('agents.loading')}</p>}
      {state === 'error' && <div className="agentState" role="alert"><Notice error>{error}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></div>}
      {state === 'ready' && items.length > 0 && <ul className="agentGrid agCards" aria-label={t('agents.list.title')}>
        {items.map((agent) => <li key={agent.id}>
          <AgentCard agent={agent} catalog={catalog} cap={capFor(capItems, agent.id)} showSpend={caps.state === 'ready'} selected={agent.id === selected}
            onOpen={() => onOpen(agent.id)} onPreview={() => setSelected(agent.id)} />
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
    </div>
    {preview && <AgentPreview agent={preview} catalog={catalog} cap={capFor(capItems, preview.id)} onOpen={onOpen} onClose={() => setSelected(null)} />}
  </div>;
}

function OverviewCard({ counts }: { counts: Record<StatusFilter, number | null> }) {
  const figure = (value: number | null) => value === null ? '-' : String(value);
  return <FinCard title={t('agentsPage.overview.title')} icon={<Users />}>
    <dl className="agOverview">
      <div><dt>{t('agentsPage.overview.total')}</dt><dd>{figure(counts.all)}</dd></div>
      <div><dt><span className="agDot active" aria-hidden="true" />{t('agents.status.active')}</dt><dd>{figure(counts.active)}</dd></div>
      <div><dt><span className="agDot paused" aria-hidden="true" />{t('agents.status.paused')}</dt><dd>{figure(counts.paused)}</dd></div>
    </dl>
  </FinCard>;
}

/** This month's model spending across agents: owners only (the caps list is theirs to read). */
function SpendCard({ caps }: { caps: CapsLoad }) {
  return <FinCard title={t('agentsPage.spend.title')} icon={<Wallet />} className="agSpendCard">
    {caps.state === 'loading' && <p className="muted agSmall" role="status">{t('common.loading')}</p>}
    {caps.state === 'denied' && <p className="muted agSmall"><Lock aria-hidden="true" className="inlineIcon" />{t('agentsPage.spend.ownersOnly')}</p>}
    {caps.state === 'error' && <p className="agSmall" role="alert">{t('agentsPage.spend.failed', { message: caps.message })}</p>}
    {caps.state === 'ready' && (() => {
      const totals = spendTotals(caps.items);
      return <>
        <p className="agSpendFigure"><strong>{totals.limit === null ? formatUsd(totals.used) : t('agentsPage.spend.ofCap', { used: formatUsd(totals.used), limit: formatUsd(totals.limit) })}</strong>
          {totals.percent !== null && <span>{t('agentsPage.percent', { percent: totals.percent })}</span>}</p>
        {totals.percent !== null && <Meter percent={totals.percent} label={t('agentsPage.spend.meter', { percent: totals.percent })} />}
        <p className="muted agSmall">{totals.capped ? t('agentsPage.spend.capped', { count: totals.capped }) : t('agentsPage.spend.noneCapped')} {t('agentsPage.spend.resets', { date: formatDateTime(caps.resetsOn) })}</p>
        {totals.over > 0 && <FinBanner tone="warn" title={t('agentsPage.spend.over', { count: totals.over })} />}
      </>;
    })()}
  </FinCard>;
}

/** The mockup's "Create agent" preview: pick how to start, then the full wizard opens. */
function StartCard({ onCreate, onTemplates }: { onCreate: () => void; onTemplates: () => void }) {
  const [how, setHow] = useState<'template' | 'scratch'>('template');
  const name = useId();
  return <FinCard title={t('agentsPage.start.title')} icon={<Sparkles />}>
    <ol className="agStartSteps" aria-label={t('agents.wizard.steps')}>
      <li aria-current="step"><span>1</span>{t('agentsPage.start.choose')}</li><li><span>2</span>{t('agentsPage.start.configure')}</li><li><span>3</span>{t('agentsPage.start.review')}</li>
    </ol>
    <fieldset className="agStartChoices"><legend>{t('agentsPage.start.how')}</legend>
      {(['template', 'scratch'] as const).map((value) => <label key={value} className={`agStartChoice ${how === value ? 'on' : ''}`}>
        <input type="radio" name={name} checked={how === value} onChange={() => setHow(value)} />
        <span><strong>{t(`agentsPage.start.${value}` as MessageKey)}</strong><small>{t(`agentsPage.start.${value}Text` as MessageKey)}</small></span>
      </label>)}
    </fieldset>
    <button type="button" onClick={how === 'template' ? onTemplates : onCreate}>{t('agentsPage.start.continue')}<ArrowRight aria-hidden="true" className="flipRtl" /></button>
  </FinCard>;
}

function AgentCard({ agent, catalog, cap, showSpend, selected, onOpen, onPreview }: {
  agent: AgentProfile; catalog: AgentCatalog | null; cap: import('./lib/agentControls').AgentCap | null; showSpend: boolean; selected: boolean; onOpen: () => void; onPreview: () => void;
}) {
  const approval = approvalSummary(agent, catalog);
  const tools = toolSummary(agent);
  const { names: skills, more: moreSkills } = skillNames(agent, catalog);
  return <article className={`surface agCard ${agent.status === 'paused' ? 'isPaused' : ''} ${selected ? 'isSelected' : ''}`}>
    <div className="agCardHead">
      <AgentAvatar icon={agent.icon} color={agent.color} />
      <h3 dir="auto"><button type="button" className="agentOpen" onClick={onOpen}>{agent.name}</button></h3>
      <StatusChip tone={agent.status === 'active' ? 'ok' : 'warn'}>{t(`agents.status.${agent.status}` as MessageKey)}</StatusChip>
    </div>
    <p className="agCardText" dir="auto">{agent.description || t('agents.noDescription')}</p>
    <dl className="agCardFacts">
      <div><dt><Cpu aria-hidden="true" />{t('agentsPage.card.model')}</dt><dd>{t(`agents.model.${agent.model_route}` as MessageKey)}</dd></div>
      <div><dt><Wrench aria-hidden="true" />{t('agentsPage.card.tools')}</dt><dd className="agTags">{tools.names.length === 0 ? <span className="muted">{t('agentsPage.card.replyOnly')}</span>
        : <>{tools.names.map((name) => <span key={name} className="agTag" dir="ltr">{name}</span>)}{tools.more > 0 && <span className="agTag">{t('agentSkills.cardMore', { count: tools.more })}</span>}</>}</dd></div>
      {skills.length > 0 && <div><dt><FileText aria-hidden="true" />{t('agents.tab.skills')}</dt><dd className="agTags">{skills.map((name) => <span key={name} className="agTag" dir="auto">{name}</span>)}{moreSkills > 0 && <span className="agTag">{t('agentSkills.cardMore', { count: moreSkills })}</span>}</dd></div>}
    </dl>
    <p className={`agApproval ${approval.tone}`}><ShieldCheck aria-hidden="true" />{t(approval.key)}</p>
    {showSpend && cap && <div className="agCardSpend"><span>{t('agentsPage.card.spend')}</span><strong>{capText(cap)}</strong>
      {cap.percent_used !== null && <Meter percent={cap.percent_used} label={t('agentsPage.spend.meter', { percent: Math.round(cap.percent_used) })} />}</div>}
    {agent.unavailable_skills.length + agent.unavailable_tools.length > 0 && <p className="agentSkillWarn">{t('agentSkills.cardAttention', { count: agent.unavailable_skills.length + agent.unavailable_tools.length })}</p>}
    <div className="agCardFoot">
      <span><Clock aria-hidden="true" />{t('agentsPage.card.updated', { when: formatDateTime(agent.updated_at) })}</span>
      <span>{agent.sharing === 'workspace' ? <><Users aria-hidden="true" />{t('agents.sharing.workspace')}</> : <><Lock aria-hidden="true" />{t('agents.sharing.private')}</>}</span>
      {agent.department && <span dir="auto">{agent.department}</span>}
      <AvatarChip id={agent.id} name={agent.name} avatar={agent.avatar} department={agent.department} size={22} />
      <button type="button" className="agPreviewToggle" aria-pressed={selected} aria-label={t('agentsPage.card.preview', { name: agent.name })} title={t('agentsPage.card.previewShort')} onClick={onPreview}><ChevronRight aria-hidden="true" className="flipRtl" /></button>
    </div>
  </article>;
}

/** The selected agent at a glance (wide screens): what it is for, what it may use, and its limits. */
function AgentPreview({ agent, catalog, cap, onOpen, onClose }: {
  agent: AgentProfile; catalog: AgentCatalog | null; cap: import('./lib/agentControls').AgentCap | null; onOpen: (id: string, tab?: DetailTab | 'chat' | 'call') => void; onClose: () => void;
}) {
  const paused = agent.status === 'paused';
  const isOwner = agent.owner_user_id === api.currentUserId();
  return <aside className="agPreview finCard" aria-labelledby="ag-preview-title">
    <div className="agPreviewHead">
      <AgentAvatar icon={agent.icon} color={agent.color} size="lg" />
      <div><h2 id="ag-preview-title" dir="auto">{agent.name}</h2><StatusChip tone={paused ? 'warn' : 'ok'}>{t(`agents.status.${agent.status}` as MessageKey)}</StatusChip></div>
      <button type="button" className="iconButton" aria-label={t('agentsPage.preview.close')} title={t('agentsPage.preview.close')} onClick={onClose}><X aria-hidden="true" /></button>
    </div>
    <p className="agSmall muted">{t('agentsPage.preview.owner', { owner: isOwner ? t('agents.you') : t('agentsPage.preview.someoneElse') })} · {agent.sharing === 'workspace' ? t('agents.sharing.workspace') : t('agents.sharing.private')}</p>
    <div className="agPreviewActions">
      <button type="button" onClick={() => onOpen(agent.id)}><ExternalLink aria-hidden="true" />{t('agentsPage.preview.open')}</button>
      <button type="button" className="secondary" disabled={paused} onClick={() => onOpen(agent.id, 'chat')}><MessageSquare aria-hidden="true" />{t('agents.chat')}</button>
      <button type="button" className="secondary" disabled={paused} onClick={() => onOpen(agent.id, 'call')}><Phone aria-hidden="true" />{t('agents.call')}</button>
    </div>
    <nav className="agPreviewLinks" aria-label={t('agentsPage.preview.sections')}>
      {(['skills', 'memory', 'settings'] as const).map((tab) => <button key={tab} type="button" className="linkButton" onClick={() => onOpen(agent.id, tab)}>{t(`agents.tab.${tab}` as MessageKey)}</button>)}
    </nav>
    <section className="agPreviewBlock"><h3>{t('agentsPage.preview.purpose')}</h3><p dir="auto">{agent.description || t('agents.noDescription')}</p></section>
    <section className="agPreviewBlock"><h3>{t('agentsPage.preview.modelTools')}</h3>
      <p>{t('agentsPage.card.model')}: {t(`agents.model.${agent.model_route}` as MessageKey)}</p>
      <p>{t('agentsPage.card.tools')}: <bdi dir="ltr">{agent.allowed_tools.length ? agent.allowed_tools.join(', ') : t('agentsPage.card.replyOnly')}</bdi></p>
    </section>
    <section className="agPreviewBlock"><h3>{t('agentsPage.preview.limits')}</h3>
      <p className={`agApproval ${approvalSummary(agent, catalog).tone}`}><ShieldCheck aria-hidden="true" />{t(approvalSummary(agent, catalog).key)}</p>
      {cap ? <><p>{t('agentsPage.card.spend')}: {capText(cap)}</p>{cap.percent_used !== null && <Meter percent={cap.percent_used} label={t('agentsPage.spend.meter', { percent: Math.round(cap.percent_used) })} />}</> : null}
    </section>
    <section className="agPreviewBlock"><h3>{t('agentsPage.preview.boundaries')}</h3><BoundaryList agent={agent} catalog={catalog} /></section>
    <section className="agPreviewBlock"><h3>{t('agentsPage.preview.lastChange')}</h3><p>{formatDateTime(agent.updated_at)}</p></section>
  </aside>;
}
