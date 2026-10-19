// Team work (docs/team-work.md): "Ask ANUM" assignments, My tasks, the team board, workload,
// the activity feed and people. Everything comes from the live API; who may change what is the
// API's answer (its 403 is shown in place), and every list pages through the server.
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, Bot, CheckSquare, CircleDot, ClipboardList, Download, Gauge, History, MessageSquare, Paperclip, Plus, RefreshCw, Search, Send, Sparkles, Trash2, UserRound, Users } from 'lucide-react';
import type { ActivityEntry, AssistantProfile, CommandPreview, TeamPerson, WorkItem, WorkItemDetail, WorkItemPage, WorkStatus, Workload, WorkPriority } from '@anum/contracts';
import * as team from './lib/teamApi';
import { apiRequest, currentUserId, downloadFile, getFiles, uploadFile, type FileRecord } from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { BOARD_COLUMNS, PRIORITIES, STATUSES, assigneeOptions, assigneeValue, boardColumn, draftBody, dueState, emptyDraft, isoToLocalInput, loadTone, localInputToIso, parseAssignee, workloadTotals, workQuery, type WorkDraft } from './lib/team';
import { figure, type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { loadFailure, PageHeader, PageSummary } from './PageParts';
import { Modal } from './Dialog';
import { CallButtons } from './AppCalls';
import { CallHistory } from './CallHistory';
import './team.css';

// The board and the workload table are one "Team dashboard" tab (docs/team-work.md#dashboard).
type Tab = 'mine' | 'board' | 'activity' | 'people' | 'calls';
const TABS: Tab[] = ['mine', 'board', 'activity', 'people', 'calls'];
const savedTab = (): Tab => { const saved = sessionStorageGet('anum.team.tab'); return saved === 'workload' ? 'board' : (TABS as string[]).includes(saved ?? '') ? saved as Tab : 'mine'; };
type Flash = { text: string; error?: boolean } | null;

const statusLabel = (s: WorkStatus) => t(`team.status.${s}` as MessageKey);
const priorityLabel = (p: WorkPriority) => t(`team.priority.${p}` as MessageKey);
const when = (iso: string) => formatDateTime(iso, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function Empty({ icon, title, text, action }: { icon: ReactNode; title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState">{icon}<h3>{title}</h3><p>{text}</p>{action}</div>;
}

function StatusBadge({ status }: { status: WorkStatus }) {
  const tone = status === 'done' ? 'success' : status === 'blocked' ? 'dangerTone' : status === 'cancelled' ? 'neutral' : 'warning';
  return <span className={`statusBadge ${tone}`}>{statusLabel(status)}</span>;
}

function DueLabel({ item }: { item: Pick<WorkItem, 'due_at' | 'status'> }) {
  const state = dueState(item);
  if (state === 'none' || !item.due_at) return <span className="due none">{t('team.noDue')}</span>;
  const key: MessageKey = state === 'overdue' ? 'team.due.overdue' : state === 'today' ? 'team.due.today' : 'team.due.on';
  return <span className={`due ${state}`}>{t(key, { when: when(item.due_at) })}</span>;
}

/** Shared lookups for the assignee pickers: people (with profiles) and enabled assistants. */
function useDirectory() {
  const [people, setPeople] = useState<TeamPerson[]>([]);
  const [assistants, setAssistants] = useState<AssistantProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    try {
      const [p, a] = await Promise.all([team.getPeople(), team.getAssistants()]);
      setPeople(p); setAssistants(a); setError(null);
    } catch (e) { setError(describeError(e)); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  const options = useMemo(() => assigneeOptions(people, assistants, 'ANUM'), [people, assistants]);
  return { people, assistants, options, error, reload };
}

export function TeamView({ focusItemId = null, onFocusChange, live = 0 }: { focusItemId?: string | null; onFocusChange?(id: string | null): void; live?: number }) {
  const [tab, setTab] = useState<Tab>(savedTab);
  const [selected, setSelected] = useState<string | null>(focusItemId);
  const [creating, setCreating] = useState(false);
  const [revision, setRevision] = useState(0);
  const [workload, setWorkload] = useState<Load<Workload>>({ state: 'loading' });
  const dir = useDirectory();
  const changed = () => setRevision((n) => n + 1);
  useEffect(() => { if (focusItemId) setSelected(focusItemId); }, [focusItemId]);
  // Live events (someone else's change) reload the lists; an open form or detail keeps its state.
  useEffect(() => { if (live) setRevision((n) => n + 1); }, [live]);
  const loadWorkload = useCallback(() => { team.getWorkload().then((value) => setWorkload({ state: 'ready', value }), (e: unknown) => setWorkload(loadFailure(e))); }, []);
  useEffect(loadWorkload, [loadWorkload, revision]);
  const pick = (next: Tab) => { setTab(next); sessionStorageSet('anum.team.tab', next); };
  const open = (id: string | null) => { setSelected(id); onFocusChange?.(id); };
  const totals = workload.state === 'ready' ? workloadTotals(workload.value) : null;
  const figures = [
    figure('open', t('pages2.team.fig.open'), workload, () => ({ value: String(totals!.open), hint: t('pages2.team.fig.openHint', { count: totals!.people }) })),
    figure('progress', t('team.status.in_progress'), workload, () => ({ value: String(totals!.inProgress), hint: totals!.blocked ? t('pages2.team.fig.blocked', { count: totals!.blocked }) : t('pages2.team.fig.noneBlocked'), tone: totals!.blocked ? 'warn' : '' })),
    figure('overdue', t('team.load.overdue'), workload, () => ({ value: String(totals!.overdue), hint: totals!.overdue ? t('pages2.team.fig.overdueHint') : t('pages2.team.fig.noneOverdue'), tone: totals!.overdue ? 'danger' : '' })),
    figure('unassigned', t('pages2.team.fig.unassigned'), workload, () => ({ value: String(totals!.unassigned), hint: t('pages2.team.fig.week', { count: totals!.dueThisWeek }), tone: totals!.unassigned ? 'warn' : '' })),
  ];

  return <div className="teamView">
    <PageHeader title={viewLabel('team')} subtitle={t('team.intro')} actions={<button type="button" onClick={() => setCreating(true)} aria-haspopup="dialog"><Plus aria-hidden="true" />{t('team.newWork')}</button>} />
    <PageSummary label={t('pages2.team.summary')} figures={figures} onRetry={loadWorkload} />
    <CommandBox options={dir.options} onCreated={(item) => { changed(); open(item.id); }} />
    {creating && <Modal title={t('team.newWork')} className="formDialog workDialog" onClose={() => setCreating(false)}>
      <WorkForm options={dir.options} onCancel={() => setCreating(false)} onCreated={(item) => { setCreating(false); changed(); open(item.id); }} />
    </Modal>}
    {dir.error && <Notice error>{t('team.directoryFailed', { message: dir.error })}</Notice>}
    <nav className="settingsTabs teamTabs" aria-label={t('team.sections')}>
      {TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`team.tab.${id}` as MessageKey)}</button>)}
    </nav>
    <div className={`teamSplit ${selected ? 'hasDetail' : ''}`}>
      <div className="teamMain">
        {tab === 'mine' && <WorkList key={`mine-${revision}`} scope="mine" selected={selected} onOpen={open} onCreate={() => setCreating(true)} />}
        {tab === 'board' && <Board key={`board-${revision}`} workload={workload} onRetryWorkload={loadWorkload} selected={selected} onOpen={open} onCreate={() => setCreating(true)} />}
        {tab === 'activity' && <ActivityFeed key={`feed-${revision}`} onOpen={open} />}
        {tab === 'people' && <PeoplePanel people={dir.people} onChanged={() => { void dir.reload(); changed(); }} />}
        {tab === 'calls' && <CallHistory />}
      </div>
      {selected && <WorkDetail key={selected} itemId={selected} options={dir.options} onClose={() => open(null)} onChanged={changed} />}
    </div>
  </div>;
}

function sessionStorageGet(key: string): string | null { try { return sessionStorage.getItem(key); } catch { return null; } }
function sessionStorageSet(key: string, value: string): void { try { sessionStorage.setItem(key, value); } catch { /* page-only */ } }

// ------------------------------------------------------------------------- Ask ANUM

function CommandBox({ options, onCreated }: { options: ReturnType<typeof assigneeOptions>; onCreated(item: WorkItemDetail): void }) {
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<CommandPreview | null>(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const understand = async (event: FormEvent) => {
    event.preventDefault();
    if (text.trim().length < 3) { setFlash({ text: t('team.command.tooShort'), error: true }); return; }
    setBusy(true); setFlash(null);
    try {
      const next = await team.parseCommand(text.trim());
      setPreview(next);
      setChoice(next.assignee ? `${next.assignee.type}:${next.assignee.id}` : next.candidates[0] ? `${next.candidates[0].type}:${next.candidates[0].id}` : '');
    } catch (e) { setFlash({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const confirm = async () => {
    setBusy(true); setFlash(null);
    try {
      const item = await team.runCommand(text.trim(), parseAssignee(choice));
      setFlash({ text: t('team.command.created', { title: item.title, who: item.assignee_name ?? '' }) });
      setText(''); setPreview(null); onCreated(item);
    } catch (e) { setFlash({ text: isPermissionDenied(e) ? t('team.command.denied', { message: describeError(e) }) : describeError(e), error: true }); } finally { setBusy(false); }
  };
  const canCreate = Boolean(preview?.title) && Boolean(choice);
  return <section className="surface commandBox" aria-labelledby="ask-anum-title">
    <h2 id="ask-anum-title"><Sparkles aria-hidden="true" />{t('team.command.title')}</h2>
    <form onSubmit={understand} className="commandRow">
      <label className="field"><span>{t('team.command.label')}</span>
        <input value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} placeholder={t('team.command.placeholder')} maxLength={500} />
      </label>
      <div className="actions"><button type="submit" disabled={busy}><Search aria-hidden="true" />{t('team.command.understand')}</button></div>
    </form>
    {preview && <div className="commandPreview" aria-live="polite">
      {preview.title && <dl className="inlineDetails">
        <div><dt>{t('team.field.title')}</dt><dd>{preview.title}</dd></div>
        <div><dt>{t('team.field.due')}</dt><dd>{preview.due_at ? when(preview.due_at) : t('team.noDue')}</dd></div>
        <div><dt>{t('team.field.assignee')}</dt><dd>{preview.assignee?.name ?? preview.person}</dd></div>
      </dl>}
      {preview.problem && <Notice error={!preview.title}>{preview.problem}</Notice>}
      {preview.title && !preview.understood && <label className="field"><span>{t('team.command.pick')}</span>
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>
          <option value="">{t('team.command.pickPlaceholder')}</option>
          {(preview.candidates.length ? preview.candidates.map((c) => ({ value: `${c.type}:${c.id}`, label: c.name })) : options).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select></label>}
      <div className="actions">
        <button type="button" disabled={busy || !canCreate} onClick={() => void confirm()}><Send aria-hidden="true" />{t('team.command.assign')}</button>
        <button type="button" className="secondary" onClick={() => setPreview(null)}>{t('common.cancel')}</button>
      </div>
    </div>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
  </section>;
}

// ------------------------------------------------------------------------- new work

function WorkForm({ options, onCancel, onCreated }: { options: ReturnType<typeof assigneeOptions>; onCancel(): void; onCreated(item: WorkItemDetail): void }) {
  const [draft, setDraft] = useState<WorkDraft>(emptyDraft);
  const [errors, setErrors] = useState<ReturnType<typeof draftBody>['errors']>({});
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => { titleRef.current?.focus(); }, []);
  const set = (patch: Partial<WorkDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const { body, errors: found } = draftBody(draft);
    setErrors(found);
    if (!body) return;
    setBusy(true); setFlash(null);
    try { onCreated(await team.createWork(body)); } catch (e) { setFlash({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  return <form className="settingsForm workForm" onSubmit={submit} noValidate>
    <div className="formGrid">
      <div><label className="field"><span>{t('team.field.title')}</span>
        <input ref={titleRef} value={draft.title} onChange={(e) => set({ title: e.target.value })} maxLength={200} aria-invalid={Boolean(errors.title)} aria-describedby={errors.title ? 'work-title-error' : undefined} />
      </label>{errors.title && <small id="work-title-error" className="fieldError">{t('team.error.titleRequired')}</small>}</div>
      <label className="field"><span>{t('team.field.assignee')}</span>
        <select value={draft.assignee} onChange={(e) => set({ assignee: e.target.value })}>
          <option value="">{t('team.unassigned')}</option>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select></label>
      <label className="field"><span>{t('team.field.priority')}</span>
        <select value={draft.priority} onChange={(e) => set({ priority: e.target.value as WorkPriority })}>{PRIORITIES.map((p) => <option key={p} value={p}>{priorityLabel(p)}</option>)}</select></label>
      <label className="field"><span>{t('team.field.due')}</span>
        <input type="datetime-local" value={draft.due} onChange={(e) => set({ due: e.target.value })} aria-invalid={Boolean(errors.due)} /></label>
      <div><label className="field"><span>{t('team.field.followUp')}</span>
        <input type="datetime-local" value={draft.followUp} onChange={(e) => set({ followUp: e.target.value })} aria-describedby="follow-up-help" /></label>
        <small id="follow-up-help" className="muted">{t('team.followUpHelp')}</small></div>
      <div><label className="field"><span>{t('team.field.estimate')}</span>
        <input inputMode="decimal" value={draft.estimate} onChange={(e) => set({ estimate: e.target.value })} aria-invalid={Boolean(errors.estimate)} aria-describedby={errors.estimate ? 'estimate-error' : undefined} /></label>
        {errors.estimate && <small id="estimate-error" className="fieldError">{t('team.error.estimate')}</small>}</div>
    </div>
    <label className="field"><span>{t('team.field.description')}</span>
      <textarea value={draft.description} onChange={(e) => set({ description: e.target.value })} maxLength={8000} rows={3} /></label>
    <label className="field"><span>{t('team.field.checklist')}</span>
      <textarea value={draft.checklist} onChange={(e) => set({ checklist: e.target.value })} rows={3} placeholder={t('team.checklistPlaceholder')} /></label>
    <div className="actions">
      <button type="submit" disabled={busy}><Plus aria-hidden="true" />{t('team.create')}</button>
      <button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button>
    </div>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
  </form>;
}

// ------------------------------------------------------------------------- lists

type ListState = { kind: 'loading' } | { kind: 'ready'; page: WorkItemPage; items: WorkItem[] } | { kind: 'error'; detail: string; denied: boolean };

function usePagedWork(path: (offset: number) => string) {
  const [state, setState] = useState<ListState>({ kind: 'loading' });
  const [more, setMore] = useState(false);
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try { const page = await team.listWork(path(0)); setState({ kind: 'ready', page, items: page.items }); }
    catch (e) { setState({ kind: 'error', detail: describeError(e), denied: isPermissionDenied(e) }); }
  }, [path]);
  useEffect(() => { void load(); }, [load]);
  const loadMore = async () => {
    if (state.kind !== 'ready' || state.page.next_offset === null) return;
    setMore(true);
    try { const page = await team.listWork(path(state.page.next_offset)); setState({ kind: 'ready', page, items: [...state.items, ...page.items] }); }
    catch (e) { setState({ kind: 'error', detail: describeError(e), denied: isPermissionDenied(e) }); } finally { setMore(false); }
  };
  return { state, load, loadMore, more };
}

function ListError({ detail, denied, onRetry }: { detail: string; denied: boolean; onRetry(): void }) {
  return <div className="surface teamPanel">
    <Notice error>{denied ? t('team.denied', { message: detail }) : t('team.loadFailed', { message: detail })}</Notice>
    <div className="actions"><button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>
  </div>;
}

function WorkRow({ item, selected, onOpen }: { item: WorkItem; selected: boolean; onOpen(id: string): void }) {
  return <li><button type="button" className={`recordButton workRow ${selected ? 'selected' : ''}`} aria-current={selected ? 'true' : undefined} onClick={() => onOpen(item.id)}>
    <span>
      <strong>{item.title}</strong>
      <small>
        <span className={`priority ${item.priority}`}>{priorityLabel(item.priority)}</span>
        {' · '}{item.assignee_name ?? t('team.unassigned')}
        {item.checklist_total > 0 && <> · <CheckSquare aria-hidden="true" className="inlineIcon" />{t('team.checklistCount', { done: item.checklist_done, total: item.checklist_total })}</>}
        {item.comment_count > 0 && <> · <MessageSquare aria-hidden="true" className="inlineIcon" />{item.comment_count}</>}
      </small>
    </span>
    <span className="workRowMeta"><DueLabel item={item} /><StatusBadge status={item.status} /></span>
  </button></li>;
}

function WorkList({ scope, selected, onOpen, onCreate }: { scope: 'mine' | 'all'; selected: string | null; onOpen(id: string): void; onCreate(): void }) {
  const [status, setStatus] = useState('open');
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [sort, setSort] = useState('due');
  const path = useCallback((offset: number) => workQuery({ scope, status, q: submitted, sort, offset }), [scope, status, submitted, sort]);
  const { state, load, loadMore, more } = usePagedWork(path);
  return <section className="surface teamPanel" aria-labelledby="work-list-title">
    <div className="sectionHeader"><h2 id="work-list-title">{t('team.tab.mine')}</h2>{state.kind === 'ready' && <span className="muted">{t('team.shownOf', { shown: state.items.length, total: state.page.total })}</span>}</div>
    <form className="teamFilters" onSubmit={(e) => { e.preventDefault(); setSubmitted(query); }} role="search">
      <label className="field"><span>{t('common.search')}</span><input type="search" value={query} onChange={(e) => setQuery(e.target.value)} onBlur={() => setSubmitted(query)} /></label>
      <label className="field"><span>{t('team.field.status')}</span>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="open">{t('team.filter.open')}</option><option value="all">{t('team.filter.all')}</option>
          {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select></label>
      <label className="field"><span>{t('team.sort')}</span>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="due">{t('team.sort.due')}</option><option value="priority">{t('team.sort.priority')}</option><option value="updated">{t('team.sort.updated')}</option>
        </select></label>
    </form>
    {state.kind === 'loading' && <p className="muted" role="status">{t('team.loading')}</p>}
    {state.kind === 'error' && <ListError detail={state.detail} denied={state.denied} onRetry={() => void load()} />}
    {state.kind === 'ready' && !state.items.length && <Empty icon={<ClipboardList aria-hidden="true" />} title={submitted || status !== 'open' ? t('team.empty.filtered') : t('team.empty.mine')} text={t('team.empty.mineText')} action={<div className="actions"><button type="button" onClick={onCreate}><Plus aria-hidden="true" />{t('team.newWork')}</button></div>} />}
    {state.kind === 'ready' && state.items.length > 0 && <ul className="workList">{state.items.map((item) => <WorkRow key={item.id} item={item} selected={selected === item.id} onOpen={onOpen} />)}</ul>}
    {state.kind === 'ready' && state.page.next_offset !== null && <div className="actions"><button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{t('team.loadMore')}</button></div>}
  </section>;
}

function BoardColumn({ status, assignee, selected, onOpen }: { status: WorkStatus; assignee: string; selected: string | null; onOpen(id: string): void }) {
  const parsed = parseAssignee(assignee);
  const path = useCallback((offset: number) => workQuery({ status, sort: status === 'done' ? 'updated' : 'due', limit: 25, offset, assigneeType: parsed?.type, assigneeId: parsed?.id }), [status, parsed?.type, parsed?.id]);
  const { state, load, loadMore, more } = usePagedWork(path);
  return <section className="boardColumn" aria-labelledby={`col-${status}`}>
    <h3 id={`col-${status}`}>{statusLabel(status)}{state.kind === 'ready' && <span className="muted"> {state.page.total}</span>}</h3>
    {state.kind === 'loading' && <p className="muted">{t('team.loading')}</p>}
    {state.kind === 'error' && <ListError detail={state.detail} denied={state.denied} onRetry={() => void load()} />}
    {state.kind === 'ready' && !state.items.length && <p className="muted boardEmpty">{t('team.empty.column')}</p>}
    {state.kind === 'ready' && <ul className="workList">{state.items.filter((i) => boardColumn(i.status) === status).map((item) => <WorkRow key={item.id} item={item} selected={selected === item.id} onOpen={onOpen} />)}</ul>}
    {state.kind === 'ready' && state.page.next_offset !== null && <button type="button" className="secondary loadMore" disabled={more} onClick={() => void loadMore()}>{t('team.loadMore')}</button>}
  </section>;
}

function Board({ workload, onRetryWorkload, selected, onOpen, onCreate }: { workload: Load<Workload>; onRetryWorkload(): void; selected: string | null; onOpen(id: string): void; onCreate(): void }) {
  const dir = useDirectory();
  const [assignee, setAssignee] = useState('');
  return <section className="surface teamPanel teamDashboard" aria-labelledby="board-title">
    <div className="sectionHeader"><h2 id="board-title">{t('team.tab.board')}</h2>
      <div className="boardTools">
        <label className="field"><span>{t('team.field.assignee')}</span>
          <select value={assignee} onChange={(e) => setAssignee(e.target.value)}><option value="">{t('team.everyone')}</option>{dir.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
      </div>
    </div>
    <WorkloadPanel workload={workload} onRetry={onRetryWorkload} selected={assignee} onPick={(value) => setAssignee((current) => (current === value ? '' : value))} onCreate={onCreate} />
    <h3 className="boardHeading">{assignee ? t('pages2.team.boardFor', { name: dir.options.find((o) => o.value === assignee)?.label ?? assignee }) : t('pages2.team.boardAll')}</h3>
    <div className="board">{BOARD_COLUMNS.map((s) => <BoardColumn key={`${s}-${assignee}`} status={s} assignee={assignee} selected={selected} onOpen={onOpen} />)}</div>
  </section>;
}

// ------------------------------------------------------------------------- workload and activity

function WorkloadPanel({ workload, onRetry, selected, onPick, onCreate }: { workload: Load<Workload>; onRetry(): void; selected: string; onPick(value: string): void; onCreate(): void }) {
  if (workload.state === 'error' || workload.state === 'denied') return <ListError detail={workload.message} denied={workload.state === 'denied'} onRetry={onRetry} />;
  if (workload.state === 'loading') return <p className="muted" role="status">{t('team.loading')}</p>;
  const data = workload.value;
  return <div className="workloadBlock" aria-labelledby="workload-title">
    <div className="workloadHead"><h3 id="workload-title">{t('team.tab.workload')}</h3>{data.unassigned_open > 0 && <span className="statusBadge warning">{t('team.unassignedOpen', { count: data.unassigned_open })}</span>}<span className="muted">{t('pages2.team.workloadHint')}</span></div>
    {!data.rows.length ? <Empty icon={<Gauge aria-hidden="true" />} title={t('team.empty.workload')} text={t('team.empty.workloadText')} action={<div className="actions"><button type="button" onClick={onCreate}><Plus aria-hidden="true" />{t('team.newWork')}</button></div>} /> :
      <div className="tableScroll"><table className="workloadTable">
        <thead><tr><th scope="col">{t('team.field.assignee')}</th><th scope="col">{t('team.load.open')}</th><th scope="col">{t('team.status.in_progress')}</th><th scope="col">{t('team.status.blocked')}</th><th scope="col">{t('team.load.overdue')}</th><th scope="col">{t('team.load.week')}</th><th scope="col">{t('team.load.capacity')}</th></tr></thead>
        <tbody>{data.rows.map((r) => {
          const value = assigneeValue(r.assignee_type, r.assignee_id);
          return <tr key={value} className={selected === value ? "selected" : undefined}>
            <th scope="row"><button type="button" className="cellButton" aria-pressed={selected === value} title={t('pages2.team.filterBy', { name: r.name })} onClick={() => onPick(value)}><strong>{r.assignee_type !== 'user' && <Bot aria-hidden="true" className="inlineIcon" />}{r.name}</strong></button></th>
            <td>{r.open}</td><td>{r.in_progress}</td><td>{r.blocked}</td><td className={r.overdue ? 'overdueCell' : ''}>{r.overdue}</td><td>{r.due_this_week}</td>
            <td>{r.load_percent === null ? <span className="muted">{t('team.load.noCapacity', { hours: r.estimate_hours })}</span> :
              <span className={`loadBar ${loadTone(r.load_percent)}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(r.load_percent, 100)} aria-label={t('team.load.meter', { name: r.name, percent: r.load_percent })}>
                <span style={{ inlineSize: `${Math.min(r.load_percent, 100)}%` }} /><em>{t('team.load.hours', { hours: r.estimate_hours, capacity: r.capacity_hours ?? 0 })}</em></span>}</td>
          </tr>;
        })}</tbody>
      </table></div>}
  </div>;
}

function ActivityFeed({ onOpen }: { onOpen(id: string): void }) {
  const [items, setItems] = useState<ActivityEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'more'>('loading');
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (after: string | null) => {
    setState(after ? 'more' : 'loading');
    try { const page = await team.getActivity(after); setItems((old) => after ? [...old, ...page.items] : page.items); setCursor(page.next_cursor); setError(null); }
    catch (e) { setError(describeError(e)); } finally { setState('ready'); }
  }, []);
  useEffect(() => { void load(null); }, [load]);
  if (error && !items.length) return <ListError detail={error} denied={false} onRetry={() => void load(null)} />;
  return <section className="surface teamPanel" aria-labelledby="activity-title">
    <h2 id="activity-title">{t('team.tab.activity')}</h2>
    {state === 'loading' && <p className="muted" role="status">{t('team.loading')}</p>}
    {state !== 'loading' && !items.length && <Empty icon={<History aria-hidden="true" />} title={t('team.empty.activity')} text={t('team.empty.activityText')} />}
    <ol className="activityList">{items.map((a) => <li key={a.id} className={`activity ${a.actor_type}`}>
      <span className="activityIcon" aria-hidden="true">{a.actor_type === 'user' ? <UserRound /> : <Bot />}</span>
      <span><span className="srOnly">{t(`team.actor.${a.actor_type}` as MessageKey)}: </span>{a.summary}<small>{formatDateTime(a.created_at)}</small></span>
      {a.work_item_id && a.action !== 'work_item.delete' && <button type="button" className="secondary linkButton" onClick={() => onOpen(a.work_item_id!)}>{t('team.openItem')}</button>}
    </li>)}</ol>
    {error && <Notice error>{error}</Notice>}
    {cursor && <div className="actions"><button type="button" className="secondary" disabled={state === 'more'} onClick={() => void load(cursor)}>{t('team.loadMore')}</button></div>}
  </section>;
}

// ------------------------------------------------------------------------- people

function PeoplePanel({ people, onChanged }: { people: TeamPerson[]; onChanged(): void }) {
  const me = currentUserId();
  const [editing, setEditing] = useState<string | null>(null);
  if (!people.length) return <section className="surface teamPanel"><Empty icon={<Users aria-hidden="true" />} title={t('team.empty.people')} text={t('team.empty.peopleText')} /></section>;
  return <section className="surface teamPanel" aria-labelledby="people-title">
    <h2 id="people-title">{t('team.tab.people')}</h2>
    <p className="muted">{t('team.peopleHelp')}</p>
    <ul className="peopleList">{people.map((p) => <li key={p.user_id} className="person">
      <div><strong>{p.display_name}</strong><small>{[t(`team.role.${p.role}` as MessageKey), p.job_title, p.department ? t(`team.dept.${p.department}` as MessageKey) : null, p.email].filter(Boolean).join(' · ')}</small>
        {!p.has_profile && <small className="warnText">{t('team.noProfile')}</small>}</div>
      <div className="personActions">
        {p.user_id !== me && <CallButtons person={p} />}
        <button type="button" className="secondary" onClick={() => setEditing(editing === p.user_id ? null : p.user_id)} aria-expanded={editing === p.user_id}>{p.user_id === me ? t('team.editMine') : t('team.edit')}</button>
      </div>
      {editing === p.user_id && <ProfileForm person={p} onDone={() => { setEditing(null); onChanged(); }} />}
    </li>)}</ul>
  </section>;
}

function ProfileForm({ person, onDone }: { person: TeamPerson; onDone(): void }) {
  const [name, setName] = useState(person.has_profile ? person.display_name : '');
  const [email, setEmail] = useState(person.email ?? '');
  const [title, setTitle] = useState(person.job_title ?? '');
  const [capacity, setCapacity] = useState(person.capacity_hours === null ? '' : String(person.capacity_hours));
  const [zone, setZone] = useState(person.timezone || team.browserTimeZone() || 'UTC');
  const [department, setDepartment] = useState(person.department ?? '');
  const [flash, setFlash] = useState<Flash>(null);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) { setFlash({ text: t('team.error.nameRequired'), error: true }); return; }
    const hours = capacity.trim() ? Number(capacity) : null;
    if (hours !== null && (!Number.isInteger(hours) || hours < 0 || hours > 168)) { setFlash({ text: t('team.error.capacity'), error: true }); return; }
    try {
      await team.saveProfile(person.user_id, { display_name: name.trim(), email: email.trim() || null, job_title: title.trim() || null, capacity_hours: hours, timezone: zone.trim() || 'UTC', department: (department || null) as TeamPerson['department'] });
      onDone();
    } catch (e) { setFlash({ text: isPermissionDenied(e) ? t('team.denied', { message: describeError(e) }) : describeError(e), error: true }); }
  };
  return <form className="profileForm" onSubmit={save}>
    <div className="formGrid">
      <label className="field"><span>{t('team.profile.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required /></label>
      <label className="field"><span>{t('team.profile.email')}</span><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} /></label>
      <label className="field"><span>{t('team.profile.jobTitle')}</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} /></label>
      <label className="field"><span>{t('team.profile.department')}</span><select value={department} onChange={(e) => setDepartment(e.target.value)}><option value="">{t('team.none')}</option>{(['sales', 'support', 'operations', 'finance', 'tyre_engineering', 'custom'] as const).map((d) => <option key={d} value={d}>{t(`team.dept.${d}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('team.profile.capacity')}</span><input inputMode="numeric" value={capacity} onChange={(e) => setCapacity(e.target.value)} /></label>
      <label className="field"><span>{t('team.profile.timezone')}</span><input value={zone} onChange={(e) => setZone(e.target.value)} maxLength={64} /></label>
    </div>
    <div className="actions"><button type="submit">{t('team.save')}</button><button type="button" className="secondary" onClick={onDone}>{t('common.cancel')}</button></div>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
  </form>;
}

// ------------------------------------------------------------------------- detail

function WorkDetail({ itemId, options, onClose, onChanged }: { itemId: string; options: ReturnType<typeof assigneeOptions>; onClose(): void; onChanged(): void }) {
  const [item, setItem] = useState<WorkItemDetail | null>(null);
  const [error, setError] = useState<{ text: string; missing?: boolean } | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState('');
  const [newCheck, setNewCheck] = useState('');
  const [files, setFiles] = useState<FileRecord[] | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const load = useCallback(async () => {
    try { setItem(await team.getWork(itemId)); setError(null); }
    catch (e) { setError({ text: describeError(e), missing: (e as { status?: number }).status === 404 }); }
  }, [itemId]);
  useEffect(() => { void load(); }, [load]);
  const loadedId = item?.id;
  useEffect(() => { if (loadedId) headingRef.current?.focus(); }, [loadedId]);

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setFlash(null);
    try { await fn(); await load(); onChanged(); if (done) setFlash({ text: done }); }
    catch (e) {
      const status = (e as { status?: number }).status;
      setFlash({ text: status === 409 ? t('team.conflict', { message: describeError(e) }) : isPermissionDenied(e) ? t('team.denied', { message: describeError(e) }) : describeError(e), error: true });
      if (status === 409) await load();
    } finally { setBusy(false); }
  };
  const patch = (body: Omit<Parameters<typeof team.updateWork>[1], 'version'>) => item && act(() => team.updateWork(item.id, { version: item.version, ...body }));

  if (error) return <aside className="surface workDetail" aria-label={t('team.detail')}>
    <button type="button" className="secondary backButton" onClick={onClose}><ArrowLeft aria-hidden="true" />{t('team.back')}</button>
    <Notice error>{error.missing ? t('team.missing') : t('team.loadFailed', { message: error.text })}</Notice>
    {!error.missing && <div className="actions"><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></div>}
  </aside>;
  if (!item) return <aside className="surface workDetail" aria-busy="true" aria-label={t('team.detail')}><p className="muted" role="status">{t('team.loading')}</p></aside>;
  const editable = item.can_edit && !busy;
  const toAnum = item.assignee_type === 'anum' || item.assignee_type === 'assistant';
  return <aside className="surface workDetail" aria-labelledby="work-detail-title">
    <div className="sectionHeader">
      <button type="button" className="secondary backButton" onClick={onClose}><ArrowLeft aria-hidden="true" />{t('team.back')}</button>
      <StatusBadge status={item.status} />
    </div>
    <h2 id="work-detail-title" tabIndex={-1} ref={headingRef}>{item.title}</h2>
    <p className="muted">{t('team.createdBy', { who: item.created_by_name ?? item.created_by, when: formatDateTime(item.created_at) })}{item.source !== 'manual' && ` · ${t(`team.source.${item.source}` as MessageKey)}`}</p>
    {!item.can_edit && <Notice>{t('team.readOnly')}</Notice>}
    <div className="formGrid detailFields">
      <label className="field"><span>{t('team.field.status')}</span>
        <select value={item.status} disabled={!editable} onChange={(e) => void patch({ status: e.target.value as WorkStatus })}>{STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}</select></label>
      <label className="field"><span>{t('team.field.priority')}</span>
        <select value={item.priority} disabled={!editable} onChange={(e) => void patch({ priority: e.target.value as WorkPriority })}>{PRIORITIES.map((p) => <option key={p} value={p}>{priorityLabel(p)}</option>)}</select></label>
      <label className="field"><span>{t('team.field.assignee')}</span>
        <select value={assigneeValue(item.assignee_type, item.assignee_id)} disabled={!editable} onChange={(e) => { const next = parseAssignee(e.target.value); void patch(next ? { assignee: next } : { clear_assignee: true }); }}>
          <option value="">{t('team.unassigned')}</option>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          {item.assignee_type && !options.some((o) => o.value === assigneeValue(item.assignee_type, item.assignee_id)) && <option value={assigneeValue(item.assignee_type, item.assignee_id)}>{item.assignee_name ?? item.assignee_id}</option>}
        </select></label>
      <label className="field"><span>{t('team.field.due')}</span>
        <input type="datetime-local" disabled={!editable} defaultValue={isoToLocalInput(item.due_at)} onBlur={(e) => { const next = e.target.value ? localInputToIso(e.target.value) : null; if (next !== item.due_at && (next || item.due_at)) void patch(next ? { due_at: next } : { clear_due: true }); }} /></label>
      <label className="field"><span>{t('team.field.followUp')}</span>
        <input type="datetime-local" disabled={!editable} defaultValue={isoToLocalInput(item.follow_up_at)} onBlur={(e) => { const next = e.target.value ? localInputToIso(e.target.value) : null; if (next !== item.follow_up_at && (next || item.follow_up_at)) void patch(next ? { follow_up_at: next } : { clear_follow_up: true }); }} /></label>
    </div>
    <p className="dueLine"><DueLabel item={item} />{item.reminder_sent_at && <span className="muted"> · {t('team.reminded', { when: formatDateTime(item.reminder_sent_at) })}</span>}</p>
    {item.description && <p className="description">{item.description}</p>}
    {toAnum && item.can_edit && item.status !== 'done' && item.status !== 'cancelled' && <div className="handOff">
      <p>{item.agent_task_id ? t('team.handOff.started') : t('team.handOff.text', { who: item.assignee_name ?? 'ANUM' })}</p>
      <div className="actions"><button type="button" disabled={busy} onClick={() => void act(async () => { const r = await team.handOff(item.id); await apiRequest(`/api/v1/tasks/${encodeURIComponent(r.task.id)}/run`, { method: 'POST' }); }, t('team.handOff.done'))}><Bot aria-hidden="true" />{t('team.handOff.button')}</button></div>
    </div>}

    <section aria-labelledby="checklist-title" className="detailSection">
      <h3 id="checklist-title"><CheckSquare aria-hidden="true" />{t('team.field.checklist')} <span className="muted">{t('team.checklistCount', { done: item.checklist_done, total: item.checklist_total })}</span></h3>
      {!item.checklist.length && <p className="muted">{t('team.empty.checklist')}</p>}
      <ul className="checklist">{item.checklist.map((c) => <li key={c.id}>
        <label><input type="checkbox" checked={c.done} disabled={!editable} onChange={(e) => void act(() => team.setChecklist(item.id, c.id, e.target.checked))} /><span className={c.done ? 'done' : ''}>{c.label}</span></label>
        {item.can_edit && <button type="button" className="iconButton" aria-label={t('team.removeChecklist', { label: c.label })} disabled={busy} onClick={() => void act(() => team.deleteChecklist(item.id, c.id))}><Trash2 aria-hidden="true" /></button>}
      </li>)}</ul>
      {item.can_edit && <form className="inlineForm" onSubmit={(e) => { e.preventDefault(); const label = newCheck.trim(); if (label) void act(async () => { await team.addChecklist(item.id, label); setNewCheck(''); }); }}>
        <label className="field"><span>{t('team.addChecklist')}</span><input value={newCheck} onChange={(e) => setNewCheck(e.target.value)} maxLength={300} /></label>
        <button type="submit" className="secondary" disabled={busy || !newCheck.trim()}><Plus aria-hidden="true" />{t('team.add')}</button>
      </form>}
    </section>

    <section aria-labelledby="attachments-title" className="detailSection">
      <h3 id="attachments-title"><Paperclip aria-hidden="true" />{t('team.attachments')}</h3>
      {!item.attachments.length && <p className="muted">{t('team.empty.attachments')}</p>}
      <ul className="attachmentList">{item.attachments.map((a) => <li key={a.id}>
        {a.available ? <button type="button" className="secondary linkButton" onClick={() => void downloadFile(a.file_id, a.file_name).catch((e) => setFlash({ text: describeError(e), error: true }))}><Download aria-hidden="true" />{a.file_name}</button> : <span className="muted">{t('team.fileGone', { name: a.file_name })}</span>}
        {item.can_edit && <button type="button" className="iconButton" aria-label={t('team.detach', { name: a.file_name })} disabled={busy} onClick={() => void act(() => team.detachFile(item.id, a.id))}><Trash2 aria-hidden="true" /></button>}
      </li>)}</ul>
      {item.can_edit && <div className="attachTools">
        <label className="uploadButton secondaryUpload"><Paperclip aria-hidden="true" />{t('team.uploadAttach')}<input type="file" onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void act(async () => { const record = await uploadFile(file); await team.attachFile(item.id, record.id); }, t('team.attached', { name: file.name })); }} /></label>
        {files === null ? <button type="button" className="secondary" onClick={() => void getFiles().then(setFiles).catch((e) => setFlash({ text: describeError(e), error: true }))}>{t('team.attachExisting')}</button> :
          <label className="field"><span>{t('team.attachExisting')}</span><select defaultValue="" onChange={(e) => { const fileId = e.target.value; if (fileId) void act(() => team.attachFile(item.id, fileId)); }}><option value="">{files.length ? t('team.pickFile') : t('team.noFiles')}</option>{files.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>}
      </div>}
    </section>

    <section aria-labelledby="comments-title" className="detailSection">
      <h3 id="comments-title"><MessageSquare aria-hidden="true" />{t('team.comments')}</h3>
      {!item.comments.length && <p className="muted">{t('team.empty.comments')}</p>}
      <ol className="commentList">{item.comments.map((c) => <li key={c.id} className={`comment ${c.author_type}`}>
        <div><strong>{c.author_name ?? c.author_id}</strong><small>{formatDateTime(c.created_at)}</small></div>
        <p>{c.body}</p>
        {c.author_type === 'user' && c.author_id === currentUserId() && <button type="button" className="secondary linkButton" disabled={busy} onClick={() => void act(() => team.deleteComment(item.id, c.id))}>{t('common.delete')}</button>}
      </li>)}</ol>
      <form className="inlineForm" onSubmit={(e) => { e.preventDefault(); const body = comment.trim(); if (body) void act(async () => { await team.addComment(item.id, body); setComment(''); }); }}>
        <label className="field"><span>{t('team.addComment')}</span><textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={4000} /></label>
        <button type="submit" className="secondary" disabled={busy || !comment.trim()}><Send aria-hidden="true" />{t('team.post')}</button>
      </form>
    </section>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {item.can_delete && <div className="actions dangerZone"><button type="button" className="danger" disabled={busy} onClick={() => { if (window.confirm(t('team.deleteConfirm', { title: item.title }))) void team.deleteWork(item.id).then(() => { onChanged(); onClose(); }).catch((e) => setFlash({ text: describeError(e), error: true })); }}><Trash2 aria-hidden="true" />{t('team.delete')}</button></div>}
  </aside>;
}
