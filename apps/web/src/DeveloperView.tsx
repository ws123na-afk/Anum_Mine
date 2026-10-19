// Developer mode (docs/developer-mode.md): the owner picks a project (one of the workspace's GitHub
// repositories) and asks for a change; a coding agent builds it in that repository's GitHub Actions
// and opens a draft pull request, and the merge waits for the owner's approval with the payload hash
// shown here. Projects (add, edit, test the connection, install the workflow, turn off, delete) live
// in the second section. Owners only; everyone else sees what it is and who can use it.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  CheckCircle2, CircleDot, Copy, ExternalLink, Fingerprint, FolderGit2, GitPullRequest, Globe, Hammer, Lock, Pencil, Plus, Power,
  RefreshCw, Search, Send, ShieldCheck, Trash2, Wrench, XCircle,
} from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { argumentRows, expiryLabel, shortHash } from './lib/approvals';
import {
  MAX_REQUEST_CHARS, SECRET_NAMES, VARIABLE_NAMES, busyProjects, canCancel, canInstallWorkflow, canRequestMerge, checksLabel,
  defaultProjectId, draftFromProject, emptyProjectDraft, exampleRequests, filterProjects, pollDelay, projectPayload, projectState,
  requestBody, sendableProjects, setupSteps, shortText, stageLabel, stageStateLabel, statusLabel, statusTone, summaryText,
  type DevProject, type DevRequest, type DevSetup, type ProjectDraft,
} from './lib/developer';
import {
  cancelDeveloperRequest, createDeveloperRequest, createProject, deleteProject, getDeveloperRequest, getDeveloperSetup, getWorkflowTemplate,
  installWorkflow, listDeveloperRequests, listProjects, pendingApproval, requestMergeApproval, testProject, updateProject,
} from './lib/developerApi';
import { describeError, isBudgetExceeded, isPermissionDenied, budgetMessage } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { rich } from './rich';
import { useIsOwner } from './useIsOwner';
import './developer.css';
import { HelpLink } from './HelpLink';

const PAGE = 20;
const LAST_PROJECT_KEY = 'anum.developer.lastProject';
type Section = 'requests' | 'projects';

function readLastProject(): string | null {
  try { return window.localStorage.getItem(LAST_PROJECT_KEY); } catch { return null; }
}
function rememberProject(id: string): void {
  try { window.localStorage.setItem(LAST_PROJECT_KEY, id); } catch { /* this page only */ }
}

export function DeveloperView({ onOpenBudgets }: { onOpenBudgets?: () => void } = {}) {
  const owner = useIsOwner();
  if (owner === null) return <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('dev.checkingRole')}</p>;
  if (!owner) return <OwnerOnly />;
  return <DeveloperWorkspace onOpenBudgets={onOpenBudgets} />;
}

function OwnerOnly() {
  return <section className="surface devPanel" aria-labelledby="dev-owner-only">
    <Lock aria-hidden="true" className="devPanelIcon" />
    <div>
      <h2 id="dev-owner-only">{t('dev.ownerOnly.title')}</h2>
      <p className="muted">{t('dev.ownerOnly.text')}</p>
    </div>
  </section>;
}

function DeveloperWorkspace({ onOpenBudgets }: { onOpenBudgets?: () => void }) {
  const [section, setSection] = useState<Section>('requests');
  const [setup, setSetup] = useState<DevSetup | null>(null);
  const [projects, setProjects] = useState<DevProject[]>([]);
  const [items, setItems] = useState<DevRequest[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [projectId, setProjectId] = useState('');
  const [allowProtected, setAllowProtected] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [filter, setFilter] = useState('');
  const [more, setMore] = useState(false);
  const [reviews, setReviews] = useState<Record<string, Approval>>({});
  // "Not now" hides a pending merge approval here; it stays in Approvals until it lapses.
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const input = useRef<HTMLTextAreaElement | null>(null);
  const draftId = useId();

  const loadProjects = useCallback(async () => {
    const [s, list] = await Promise.all([getDeveloperSetup(), listProjects().catch((e) => {
      // Not configured or not enabled here: the setup panel explains it; the list is just empty.
      if (e instanceof Error && 'status' in e && ((e as { status: number }).status === 409 || (e as { status: number }).status === 403)) return [] as DevProject[];
      throw e;
    })]);
    setSetup(s);
    setProjects(list);
    setProjectId((current) => current && list.some((project) => project.id === current) ? current : defaultProjectId(list, readLastProject()));
    return list;
  }, []);

  const load = useCallback(async (search = appliedQuery, only = filter) => {
    try {
      const [, list] = await Promise.all([loadProjects(), listDeveloperRequests({ limit: PAGE, query: search, projectId: only || undefined })]);
      setItems(list);
      setMore(list.length === PAGE);
      setState('ready');
      setError(null);
    } catch (e) {
      setState('error');
      setError(isPermissionDenied(e) ? t('dev.ownersOnlyError') : t('dev.loadFailed', { message: describeError(e) }));
    }
  }, [appliedQuery, filter, loadProjects]);

  useEffect(() => { void load(); }, [load]);

  // Refresh while something is moving (queued, coding, CI); the API reads the progress from GitHub.
  useEffect(() => {
    const delay = pollDelay(items);
    if (delay === null || state !== 'ready' || section !== 'requests') return;
    const timer = window.setTimeout(() => { void load(); }, delay);
    return () => window.clearTimeout(timer);
  }, [items, state, section, load]);

  const replace = (next: DevRequest) => setItems((current) => current.map((item) => item.id === next.id ? next : item));
  const sendable = useMemo(() => sendableProjects(projects), [projects]);
  const selected = projects.find((project) => project.id === projectId) ?? null;
  const busyIds = useMemo(() => busyProjects(items), [items]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = requestBody(draft, allowProtected, projectId);
    if (!body.ok) { setNotice(body.error); return; }
    setBusy('create');
    setNotice(null);
    setBudget(null);
    try {
      const created = await createDeveloperRequest(body.value, 'text');
      setItems((current) => [created, ...current]);
      setDraft('');
      setAllowProtected(false);
      rememberProject(projectId);
      setNotice(t('dev.form.sent', { project: created.project_name ?? selected?.name ?? '' }));
      setSetup((current) => current ? { ...current, requests_today: current.requests_today + 1 } : current);
    } catch (e) {
      if (isBudgetExceeded(e)) setBudget(budgetMessage(e));
      else setNotice(t('dev.form.notSent', { message: describeError(e) }));
    } finally { setBusy(null); }
  }

  async function act(id: string, kind: 'refresh' | 'cancel' | 'review', fn: () => Promise<void>) {
    setBusy(`${kind}:${id}`);
    setNotice(null);
    try { await fn(); } catch (e) { setNotice(describeError(e)); } finally { setBusy(null); }
  }

  const refresh = (item: DevRequest) => act(item.id, 'refresh', async () => replace(await getDeveloperRequest(item.id, true)));
  const cancel = (item: DevRequest) => act(item.id, 'cancel', async () => {
    if (!confirm(t('dev.cancelConfirm', { text: shortText(item.text, 60) }))) return;
    replace(await cancelDeveloperRequest(item.id));
  });
  const review = (item: DevRequest) => act(item.id, 'review', async () => {
    const result = await requestMergeApproval(item.id);
    replace(result.request);
    setDismissed((current) => { const next = new Set(current); next.delete(item.id); return next; });
    setReviews((current) => ({ ...current, [item.id]: result.approval }));
  });
  async function merge(item: DevRequest, approval: Approval) {
    setBusy(`merge:${item.id}`);
    setNotice(null);
    try {
      const result = await api.approveTask(approval);
      setReviews((current) => { const next = { ...current }; delete next[item.id]; return next; });
      const updated = await getDeveloperRequest(item.id, true);
      replace(updated);
      setNotice(updated.status === 'merged' ? t('dev.merged', { number: updated.pr_number ?? '' }) : (result.run?.result ?? updated.merge_error ?? t('dev.mergeNotDone')));
    } catch (e) {
      setNotice(isPermissionDenied(e) ? t('dev.mergeRefused', { message: describeError(e) }) : t('dev.notMerged', { message: describeError(e) }));
    } finally { setBusy(null); }
  }

  async function loadMore() {
    const last = items[items.length - 1];
    if (!last) return;
    setBusy('more');
    try {
      const page = await listDeveloperRequests({ limit: PAGE, before: last.id, query: appliedQuery, projectId: filter || undefined });
      setItems((current) => [...current, ...page]);
      setMore(page.length === PAGE);
    } catch (e) { setNotice(t('dev.moreFailed', { message: describeError(e) })); } finally { setBusy(null); }
  }

  const ready = Boolean(setup?.configured && setup.enabled_here);
  const limitReached = Boolean(setup && setup.requests_today >= setup.max_requests_per_day);
  const projectBusy = Boolean(projectId && busyIds.has(projectId));
  const openProjects = () => setSection('projects');

  return <div className="devLayout">
    <section className="moduleIntro">
      <div>
        <p className="eyebrow">{t('dev.eyebrow')}</p>
        <h2>{t('dev.title')}</h2>
        <p>{t('dev.intro')}</p>
      </div>
      <button type="button" className="secondary" onClick={() => void load()} disabled={state === 'loading'}><RefreshCw aria-hidden="true" />{t('dev.refresh')}</button>
    </section>

    {state === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('dev.loading')}</p>}
    {state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{error}</span><span className="actions devInline"><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></span></div>}

    {setup && !ready && <section className="surface devPanel" aria-labelledby="dev-setup">
      <Hammer aria-hidden="true" className="devPanelIcon" />
      <div>
        <h2 id="dev-setup">{t('dev.setup.title')}</h2>
        <p className="muted" dir="auto">{setup.message}</p>
        {setup.missing.length > 0 && <>
          <p className="devSetupLead">{t('dev.setup.lead')}</p>
          <ul className="devSetupList">{setupSteps(setup).map((step) => <li key={step.name}><code dir="ltr">{step.name}</code>: {step.purpose}</li>)}</ul>
          <p className="muted">{t('dev.setup.next')}</p>
        </>}
      </div>
    </section>}

    {ready && <nav className="settingsTabs devTabs" aria-label={t('dev.sections')}>
      {(['requests', 'projects'] as const).map((id) => <button key={id} type="button" aria-current={section === id ? 'page' : undefined} onClick={() => setSection(id)}>
        {id === 'requests' ? <Hammer aria-hidden="true" /> : <FolderGit2 aria-hidden="true" />}{t(`dev.tab.${id}`)}{id === 'projects' && <span className="devTabCount">{projects.length}</span>}
      </button>)}
    </nav>}

    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{budget}</span>{onOpenBudgets && <span className="actions devInline"><button type="button" className="secondary" onClick={onOpenBudgets}>{t('dev.openBudgets')}</button></span>}</p>}
    {notice && <p className="notice" role="status" aria-live="polite"><CircleDot aria-hidden="true" /><span dir="auto">{notice}</span></p>}

    {ready && section === 'projects' && <ProjectsPanel projects={projects} setup={setup} onChanged={loadProjects} onNotice={setNotice} />}

    {ready && section === 'requests' && state === 'ready' && sendable.length === 0 && <section className="surface emptyState" aria-labelledby="dev-no-projects">
      <FolderGit2 aria-hidden="true" />
      <h3 id="dev-no-projects">{projects.length ? t('dev.noReady.title') : t('dev.noProjects.title')}</h3>
      <p>{projects.length ? t('dev.noReady.text') : t('dev.noProjects.text')}</p>
      <div className="actions devExamples"><button type="button" onClick={openProjects}><Plus aria-hidden="true" />{projects.length ? t('dev.openProjects') : t('dev.noProjects.action')}</button></div>
    </section>}

    {ready && section === 'requests' && setup && sendable.length > 0 && <form className="surface devForm" onSubmit={(e) => void submit(e)} aria-label={t('dev.form.label')}>
      <div className="devFormGrid">
        <label className="field" htmlFor={`${draftId}-project`}>
          <span>{t('dev.form.project')}</span>
          <select id={`${draftId}-project`} value={projectId} onChange={(e) => setProjectId(e.target.value)} required>
            <option value="">{t('dev.form.chooseProject')}</option>
            {sendable.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.repository})</option>)}
          </select>
        </label>
        <label className="field" htmlFor={draftId}>
          <span>{t('dev.form.what')}</span>
          <textarea id={draftId} ref={input} value={draft} maxLength={MAX_REQUEST_CHARS} rows={3} dir="auto" placeholder={t('dev.form.placeholder')} onChange={(e) => setDraft(e.target.value)} />
        </label>
      </div>
      <div className="actions devFormFooter">
        <label className="devCheck"><input type="checkbox" checked={allowProtected} onChange={(e) => setAllowProtected(e.target.checked)} /><span>{t('dev.form.allowProtected')}</span></label>
        <small className="muted">{selected
          ? t('dev.form.usage', { used: setup.requests_today, max: setup.max_requests_per_day, repository: selected.repository, branch: selected.default_branch })
          : t('dev.form.usageNoProject', { used: setup.requests_today, max: setup.max_requests_per_day })}</small>
        <button type="submit" disabled={busy === 'create' || !draft.trim() || !projectId || limitReached || projectBusy}><Send aria-hidden="true" />{busy === 'create' ? t('dev.form.sending') : t('dev.form.send')}</button>
      </div>
      {projectBusy && selected && <p className="muted devHint">{t('dev.form.oneAtATime', { project: selected.name })}</p>}
      {limitReached && <p className="muted devHint">{t('dev.form.limitReached')}</p>}
    </form>}

    {ready && section === 'requests' && state === 'ready' && (items.length > 0 || appliedQuery || filter) && <div className="actions devSearch">
      <form className="devSearchForm" role="search" onSubmit={(e) => { e.preventDefault(); setAppliedQuery(query); }}>
        <label className="visuallyHidden" htmlFor={`${draftId}-search`}>{t('dev.search.label')}</label>
        <input id={`${draftId}-search`} type="search" value={query} dir="auto" placeholder={t('dev.search.label')} onChange={(e) => setQuery(e.target.value)} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
        {appliedQuery && <button type="button" className="secondary" onClick={() => { setQuery(''); setAppliedQuery(''); }}>{t('common.clear')}</button>}
      </form>
      {projects.length > 1 && <label className="devFilter" htmlFor={`${draftId}-filter`}>
        <span>{t('dev.filter.project')}</span>
        <select id={`${draftId}-filter`} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">{t('dev.filter.all')}</option>
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
      </label>}
    </div>}

    {ready && section === 'requests' && state === 'ready' && items.length === 0 && sendable.length > 0 && <section className="surface emptyState">
      <Hammer aria-hidden="true" />
      <h3>{appliedQuery || filter ? t('dev.empty.noMatch') : t('dev.empty.title')}</h3>
      <p>{appliedQuery || filter ? t('dev.empty.noMatchText') : t('dev.empty.ready')}</p>
      {!appliedQuery && !filter && <div className="actions devExamples">{exampleRequests().map((example) => <button type="button" className="secondary" key={example} onClick={() => { setDraft(example); input.current?.focus(); }}>{example}</button>)}</div>}
    </section>}

    {(!ready || section === 'requests') && items.length > 0 && <ol className="devList" aria-label={t('dev.list')}>
      {items.map((item) => <li key={item.id}>
        <RequestCard
          item={item}
          busy={busy}
          review={dismissed.has(item.id) ? null : reviews[item.id] ?? pendingApproval(item)}
          onRefresh={() => void refresh(item)}
          onCancel={() => void cancel(item)}
          onReview={() => void review(item)}
          onMerge={(approval) => void merge(item, approval)}
          onDismissReview={() => setDismissed((current) => new Set(current).add(item.id))}
        />
      </li>)}
    </ol>}
    {(!ready || section === 'requests') && more && <div className="actions devMore"><button type="button" className="secondary" onClick={() => void loadMore()} disabled={busy === 'more'}>{busy === 'more' ? t('common.loading') : t('dev.more')}</button></div>}
  </div>;
}

function RequestCard({ item, busy, review, onRefresh, onCancel, onReview, onMerge, onDismissReview }: {
  item: DevRequest;
  busy: string | null;
  review: Approval | null;
  onRefresh: () => void;
  onCancel: () => void;
  onReview: () => void;
  onMerge: (approval: Approval) => void;
  onDismissReview: () => void;
}) {
  const title = item.pr_title ?? shortText(item.text);
  return <article className="surface devCard" aria-label={t('dev.card.label', { text: shortText(item.text, 60) })}>
    <header className="devCardHeader">
      <div>
        <h3 dir="auto">{title}</h3>
        <small className="muted">
          {formatDateTime(item.created_at)} · {t(item.source === 'voice' ? 'dev.source.voice' : 'dev.source.text')}
          {item.project_name && <> · <span className="devProjectChip"><FolderGit2 aria-hidden="true" />{item.project_name}</span></>}
          {' · '}<code dir="ltr">{item.branch}</code>
        </small>
      </div>
      <span className={`statusBadge ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
    </header>
    {item.pr_title && <p className="devRequestText" dir="auto">{item.text}</p>}
    <ol className="devTimeline" aria-label={t('dev.card.progress')}>
      {item.timeline.map((stage) => <li key={stage.id} className={`devStage ${stage.state}`} aria-current={stage.state === 'active' ? 'step' : undefined}>
        <span className="devStageDot" aria-hidden="true" />
        <span>{stageLabel(stage)}<span className="visuallyHidden">: {stageStateLabel(stage.state)}</span></span>
      </li>)}
    </ol>
    <p className="devSummary" role="status" dir="auto">{summaryText(item)}</p>
    {item.pr_number !== null && <p className="muted devChecks">{checksLabel(item.checks)}</p>}
    {item.status === 'failed' && item.failure_reason && <p className="devProblem" dir="auto">{item.failure_reason}</p>}
    {item.merge_error && item.status !== 'merged' && <p className="devProblem" dir="auto">{item.merge_error}</p>}
    <div className="devLinks">
      {item.pr_url && <a href={item.pr_url} target="_blank" rel="noreferrer"><GitPullRequest aria-hidden="true" />{t('dev.link.pr', { number: item.pr_number ?? '' })}<ExternalLink aria-hidden="true" className="devExternal flipRtl" /></a>}
      {item.run_url && <a href={item.run_url} target="_blank" rel="noreferrer"><Hammer aria-hidden="true" />{t('dev.link.run')}<ExternalLink aria-hidden="true" className="devExternal flipRtl" /></a>}
    </div>
    {review && item.status === 'waiting_approval' && <MergeReview approval={review} busy={busy === `merge:${item.id}`} onMerge={() => onMerge(review)} onDismiss={onDismissReview} />}
    <div className="actions devActions">
      {canRequestMerge(item) && !review && <button type="button" onClick={onReview} disabled={busy !== null}><ShieldCheck aria-hidden="true" />{busy === `review:${item.id}` ? t('dev.action.preparing') : t('dev.action.approveMerge')}</button>}
      {item.status !== 'merged' && item.status !== 'cancelled' && item.status !== 'failed' && <button type="button" className="secondary" onClick={onRefresh} disabled={busy !== null}><RefreshCw aria-hidden="true" />{busy === `refresh:${item.id}` ? t('dev.action.checking') : t('dev.action.checkNow')}</button>}
      {canCancel(item) && <button type="button" className="danger" onClick={onCancel} disabled={busy !== null}><XCircle aria-hidden="true" />{t('dev.action.cancel')}</button>}
    </div>
  </article>;
}

/** The explicit approval step: exactly what will run, bound to the payload hash shown here. */
function MergeReview({ approval, busy, onMerge, onDismiss }: { approval: Approval; busy: boolean; onMerge: () => void; onDismiss: () => void }) {
  const rows = argumentRows(approval.arguments);
  const expiry = expiryLabel(approval);
  return <section className="devReview" aria-label={t('dev.review.label')}>
    <h4><ShieldCheck aria-hidden="true" />{t('dev.review.title')}</h4>
    <p className="muted" dir="auto">{approval.reason}</p>
    {approval.target && <small className="approvalTarget"><Globe aria-hidden size={12} /> {rich('dev.review.sendsTo', { target: <code dir="ltr">{approval.target}</code> })}</small>}
    <dl className="approvalArgs">{rows.map((row) => <div className="approvalArg" key={row.key}><dt dir="ltr">{row.key}</dt><dd><code dir="ltr">{row.value}</code></dd></div>)}</dl>
    <small className="approvalHash" title={approval.payloadHash ?? undefined}><Fingerprint aria-hidden size={12} /> {rich('dev.review.hash', { hash: <code dir="ltr">{shortHash(approval.payloadHash)}</code> })}{expiry && <> · {expiry}</>}</small>
    <p className="muted">{t('dev.review.note')}</p>
    <div className="actions">
      <button type="button" onClick={onMerge} disabled={busy || !approval.payloadHash}><CheckCircle2 aria-hidden="true" />{busy ? t('dev.review.merging') : t('dev.review.confirm')}</button>
      <button type="button" className="secondary" onClick={onDismiss} disabled={busy}>{t('dev.review.notNow')}</button>
    </div>
  </section>;
}

// ---- Projects ----

function ProjectsPanel({ projects, setup, onChanged, onNotice }: {
  projects: DevProject[];
  setup: DevSetup | null;
  onChanged: () => Promise<DevProject[]>;
  onNotice: (text: string | null) => void;
}) {
  const [editing, setEditing] = useState<DevProject | 'new' | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const searchId = useId();
  const shown = filterProjects(projects, query);
  const atLimit = Boolean(setup?.max_projects && projects.length >= setup.max_projects);

  async function run(key: string, fn: () => Promise<string | null>) {
    setBusy(key);
    onNotice(null);
    try {
      const message = await fn();
      if (message) onNotice(message);
    } catch (e) {
      onNotice(isPermissionDenied(e) ? t('dev.projects.ownersOnly') : describeError(e));
    } finally { setBusy(null); }
  }

  const test = (project: DevProject) => run(`test:${project.id}`, async () => {
    const result = await testProject(project.id);
    await onChanged();
    return t('dev.project.tested', { name: project.name, message: result.last_check?.message ?? '' });
  });
  const install = (project: DevProject) => run(`install:${project.id}`, async () => {
    const result = await installWorkflow(project.id);
    await onChanged();
    return t(result.created ? 'dev.project.installed' : 'dev.project.installExisting', { number: result.pull_request_number, repository: project.repository });
  });
  const toggle = (project: DevProject) => run(`toggle:${project.id}`, async () => {
    await updateProject(project.id, { enabled: !project.enabled });
    await onChanged();
    return t(project.enabled ? 'dev.project.turnedOff' : 'dev.project.turnedOn', { name: project.name });
  });
  const remove = (project: DevProject) => run(`delete:${project.id}`, async () => {
    if (!confirm(t('dev.project.deleteConfirm', { name: project.name }))) return null;
    await deleteProject(project.id);
    await onChanged();
    return t('dev.project.deleted', { name: project.name });
  });
  const copyTemplate = () => run('template', async () => {
    const template = await getWorkflowTemplate();
    await navigator.clipboard.writeText(template.content);
    return t('dev.template.copied', { path: template.path });
  });

  return <section className="devProjects" aria-labelledby="dev-projects-title">
    <div className="devProjectsHead">
      <div>
        <h3 id="dev-projects-title">{t('dev.projects.title')}</h3>
        <p className="muted">{t('dev.projects.intro')}</p>
      </div>
      <button type="button" onClick={() => setEditing('new')} disabled={editing !== null || atLimit}><Plus aria-hidden="true" />{t('dev.projects.add')}</button>
    </div>
    {atLimit && <p className="muted devHint">{t('dev.projects.limit', { max: setup?.max_projects ?? 0 })}</p>}

    {editing && <ProjectForm
      project={editing === 'new' ? null : editing}
      environmentToken={Boolean(setup?.environment_token_configured)}
      onCancel={() => setEditing(null)}
      onSaved={async (saved, created) => {
        setEditing(null);
        await onChanged();
        onNotice(created ? t('dev.project.created', { name: saved.name }) : t('dev.project.saved', { name: saved.name }));
      }}
    />}

    <section className="surface devSecrets" aria-labelledby="dev-secrets-title">
      <h4 id="dev-secrets-title"><Wrench aria-hidden="true" />{t('dev.secrets.title')}</h4>
      <p className="muted">{rich('dev.secrets.text', {
        secrets: <>{SECRET_NAMES.map((name, index) => <span key={name}>{index > 0 && t('common.listSeparator')}<code dir="ltr">{name}</code></span>)}</>,
        variables: <>{VARIABLE_NAMES.map((name, index) => <span key={name}>{index > 0 && t('common.listSeparator')}<code dir="ltr">{name}</code></span>)}</>,
      })}</p>
      <div className="actions"><button type="button" className="secondary" onClick={() => void copyTemplate()} disabled={busy === 'template'}><Copy aria-hidden="true" />{t('dev.template.copy')}</button></div>
    </section>

    {projects.length > 6 && <div className="actions devSearch" role="search">
      <label className="visuallyHidden" htmlFor={searchId}>{t('dev.projects.search')}</label>
      <input id={searchId} type="search" value={query} dir="auto" placeholder={t('dev.projects.search')} onChange={(e) => setQuery(e.target.value)} />
    </div>}

    {projects.length === 0 && !editing && <section className="surface emptyState">
      <FolderGit2 aria-hidden="true" />
      <h3>{t('dev.projects.empty.title')}</h3>
      <p>{t('dev.projects.empty.text')}</p><HelpLink />
      <div className="actions devExamples"><button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('dev.projects.add')}</button></div>
    </section>}
    {projects.length > 0 && shown.length === 0 && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('dev.projects.noMatch', { query })}</p>}

    {shown.length > 0 && <ul className="devProjectList" aria-label={t('dev.projects.list')}>
      {shown.map((project) => <li key={project.id}>
        <ProjectCard
          project={project}
          busy={busy}
          onTest={() => void test(project)}
          onInstall={() => void install(project)}
          onEdit={() => setEditing(project)}
          onToggle={() => void toggle(project)}
          onDelete={() => void remove(project)}
          editingOther={editing !== null}
        />
      </li>)}
    </ul>}
  </section>;
}

const STATE_TONE = { ready: 'success', untested: 'warning', attention: 'dangerTone', off: 'neutral' } as const;

function ProjectCard({ project, busy, onTest, onInstall, onEdit, onToggle, onDelete, editingOther }: {
  project: DevProject;
  busy: string | null;
  onTest: () => void;
  onInstall: () => void;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
  editingOther: boolean;
}) {
  const state = projectState(project);
  const check = project.last_check;
  const disabled = busy !== null;
  return <article className="surface devProjectCard" aria-label={t('dev.project.label', { name: project.name })}>
    <header className="devCardHeader">
      <div>
        <h4 dir="auto">{project.name}</h4>
        <small className="muted"><code dir="ltr">{project.repository}</code> · {rich('dev.project.branch', { branch: <code dir="ltr">{project.default_branch}</code> })}</small>
      </div>
      <span className={`statusBadge ${STATE_TONE[state]}`}>{t(`dev.project.state.${state}`)}</span>
    </header>
    {project.description && <p className="devRequestText" dir="auto">{project.description}</p>}
    <dl className="devFacts">
      <div><dt>{t('dev.project.tokenLabel')}</dt><dd>{project.token_configured ? t(project.token_source === 'project' ? 'dev.project.token.project' : 'dev.project.token.environment') : t('dev.project.token.missing')}</dd></div>
      <div><dt>{t('dev.project.protectedLabel')}</dt><dd>{project.protected_paths.length ? <span className="devPaths">{project.protected_paths.map((path) => <code dir="ltr" key={path}>{path}</code>)}</span> : t('dev.project.protectedNone')}</dd></div>
      <div><dt>{t('dev.project.checkLabel')}</dt><dd dir="auto">{check ? t('dev.project.lastCheck', { date: formatDateTime(check.checked_at), message: check.message }) : t('dev.project.notTested')}</dd></div>
    </dl>
    {project.problems.length > 0 && <ul className="devProblems">{project.problems.map((problem) => <li key={problem} className="devProblem" dir="auto">{problem}</li>)}</ul>}
    {project.install_pr_url && !check?.workflow_present && <div className="devLinks"><a href={project.install_pr_url} target="_blank" rel="noreferrer"><GitPullRequest aria-hidden="true" />{t('dev.project.installPr', { number: project.install_pr_number ?? '' })}<ExternalLink aria-hidden="true" className="devExternal flipRtl" /></a></div>}
    <div className="actions devActions">
      <button type="button" className="secondary" onClick={onTest} disabled={disabled || !project.token_configured}><RefreshCw aria-hidden="true" />{busy === `test:${project.id}` ? t('dev.project.testing') : t('dev.project.test')}</button>
      {canInstallWorkflow(project) && <button type="button" className="secondary" onClick={onInstall} disabled={disabled}><GitPullRequest aria-hidden="true" />{busy === `install:${project.id}` ? t('dev.project.installing') : t('dev.project.install')}</button>}
      <button type="button" className="secondary" onClick={onEdit} disabled={disabled || editingOther}><Pencil aria-hidden="true" />{t('dev.project.edit')}</button>
      <button type="button" className="secondary" onClick={onToggle} disabled={disabled}><Power aria-hidden="true" />{project.enabled ? t('dev.project.turnOff') : t('dev.project.turnOn')}</button>
      <button type="button" className="danger" onClick={onDelete} disabled={disabled}><Trash2 aria-hidden="true" />{t('dev.project.delete')}</button>
    </div>
  </article>;
}

function ProjectForm({ project, environmentToken, onCancel, onSaved }: {
  project: DevProject | null;
  environmentToken: boolean;
  onCancel: () => void;
  onSaved: (project: DevProject, created: boolean) => Promise<void>;
}) {
  const [draft, setDraft] = useState<ProjectDraft>(() => project ? draftFromProject(project) : emptyProjectDraft());
  const [problem, setProblem] = useState<{ field: keyof ProjectDraft | null; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const id = useId();
  const first = useRef<HTMLInputElement | null>(null);
  useEffect(() => { first.current?.focus(); }, []);
  const set = <K extends keyof ProjectDraft>(key: K, value: ProjectDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  // The hint (when there is one) and the error describe the field; the label alone names it.
  const described = (field: keyof ProjectDraft, hint = false) => {
    const ids = [hint ? `${id}-${field}-hint` : '', problem?.field === field ? `${id}-error` : ''].filter(Boolean).join(' ');
    return { ...(problem?.field === field ? { 'aria-invalid': true } : {}), ...(ids ? { 'aria-describedby': ids } : {}) };
  };
  const hint = (field: keyof ProjectDraft, text: string) => <small className="muted" id={`${id}-${field}-hint`}>{text}</small>;

  async function save(event: FormEvent) {
    event.preventDefault();
    const payload = projectPayload(draft);
    if (!payload.ok) { setProblem({ field: payload.field, text: payload.error }); return; }
    setSaving(true);
    setProblem(null);
    try {
      const saved = project ? await updateProject(project.id, payload.value) : await createProject(payload.value);
      await onSaved(saved, project === null);
    } catch (e) {
      setProblem({ field: null, text: isPermissionDenied(e) ? t('dev.projects.ownersOnly') : describeError(e) });
    } finally { setSaving(false); }
  }

  return <form className="surface devProjectForm" onSubmit={(e) => void save(e)} aria-labelledby={`${id}-title`} onKeyDown={(e) => { if (e.key === 'Escape') onCancel(); }}>
    <h4 id={`${id}-title`}>{project ? t('dev.projectForm.editTitle', { name: project.name }) : t('dev.projectForm.addTitle')}</h4>
    <div className="devFormGrid two">
      <div className="field">
        <label htmlFor={`${id}-name`}>{t('dev.projectForm.name')}</label>
        <input id={`${id}-name`} ref={first} value={draft.name} maxLength={60} dir="auto" onChange={(e) => set('name', e.target.value)} {...described('name', true)} />
        {hint('name', t('dev.projectForm.nameHint'))}
      </div>
      <div className="field">
        <label htmlFor={`${id}-repo`}>{t('dev.projectForm.repository')}</label>
        <input id={`${id}-repo`} value={draft.repository} dir="ltr" placeholder="owner/repo" autoComplete="off" spellCheck={false} onChange={(e) => set('repository', e.target.value)} {...described('repository', true)} />
        {hint('repository', t('dev.projectForm.repositoryHint'))}
      </div>
      <div className="field">
        <label htmlFor={`${id}-branch`}>{t('dev.projectForm.branch')}</label>
        <input id={`${id}-branch`} value={draft.default_branch} dir="ltr" spellCheck={false} onChange={(e) => set('default_branch', e.target.value)} {...described('default_branch')} />
      </div>
      <div className="field">
        <label htmlFor={`${id}-workflow`}>{t('dev.projectForm.workflow')}</label>
        <input id={`${id}-workflow`} value={draft.workflow_file} dir="ltr" spellCheck={false} onChange={(e) => set('workflow_file', e.target.value)} {...described('workflow_file')} />
      </div>
    </div>
    <div className="field">
      <label htmlFor={`${id}-description`}>{t('dev.projectForm.description')}</label>
      <textarea id={`${id}-description`} value={draft.description} maxLength={500} rows={2} dir="auto" onChange={(e) => set('description', e.target.value)} {...described('description', true)} />
      {hint('description', t('dev.projectForm.descriptionHint'))}
    </div>
    <div className="field">
      <label htmlFor={`${id}-paths`}>{t('dev.projectForm.protected')}</label>
      <textarea id={`${id}-paths`} value={draft.protected_paths} rows={2} dir="ltr" spellCheck={false} placeholder={'docs/legal/\nsrc/billing.py'} onChange={(e) => set('protected_paths', e.target.value)} {...described('protected_paths', true)} />
      {hint('protected_paths', t('dev.projectForm.protectedHint'))}
    </div>
    <fieldset className="devTokenChoice">
      <legend>{t('dev.projectForm.tokenSource')}</legend>
      <label className="devCheck"><input type="radio" name={`${id}-source`} checked={draft.token_source === 'project'} onChange={() => set('token_source', 'project')} /><span>{t('dev.projectForm.tokenProject')}</span></label>
      <label className="devCheck"><input type="radio" name={`${id}-source`} checked={draft.token_source === 'environment'} onChange={() => set('token_source', 'environment')} /><span>{t('dev.projectForm.tokenEnvironment')}</span></label>
      {draft.token_source === 'environment' && !environmentToken && <p className="muted devHint">{t('dev.projectForm.environmentMissing')}</p>}
      {draft.token_source === 'project' && <div className="field">
        <label htmlFor={`${id}-token`}>{t('dev.projectForm.token')}</label>
        <input id={`${id}-token`} type="password" value={draft.token} dir="ltr" autoComplete="new-password" spellCheck={false} placeholder={project?.token_configured ? t('dev.projectForm.tokenKeepPlaceholder') : ''} onChange={(e) => set('token', e.target.value)} {...described('token', true)} />
        {hint('token', project?.token_configured && project.token_source === 'project' ? t('dev.projectForm.tokenKeep') : t('dev.projectForm.tokenHint'))}
      </div>}
      {draft.token_source === 'project' && project?.token_configured && project.token_source === 'project' && <label className="devCheck"><input type="checkbox" checked={draft.clear_token} onChange={(e) => set('clear_token', e.target.checked)} /><span>{t('dev.projectForm.tokenRemove')}</span></label>}
    </fieldset>
    <label className="devCheck"><input type="checkbox" checked={draft.enabled} onChange={(e) => set('enabled', e.target.checked)} /><span>{t('dev.projectForm.enabled')}</span></label>
    {problem && <p className="devProblem" id={`${id}-error`} role="alert" dir="auto">{problem.text}</p>}
    <div className="actions">
      <button type="submit" disabled={saving}><CheckCircle2 aria-hidden="true" />{saving ? t('dev.projectForm.saving') : t('dev.projectForm.save')}</button>
      <button type="button" className="secondary" onClick={onCancel} disabled={saving}>{t('common.cancel')}</button>
    </div>
  </form>;
}
