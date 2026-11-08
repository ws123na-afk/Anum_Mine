// My tasks and Team tasks on the Tasks page (docs/tasks.md#my-tasks-and-team-tasks): the people
// work items of Team work (docs/team-work.md) as the owner's mockup shows them. Figures for
// overdue, due today, in progress and completed this week; search and a priority filter; the items
// grouped Overdue, Due today, In progress, Blocked, To do and Completed this week, each row with
// a Mark done box; the selected item's detail beside the table (status, priority, assignee, dates,
// checklist, files and comments, from TeamView); New task and the Ask ANUM quick create.
// Everything is the live API; who may change what is the API's answer.
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { AlertTriangle, CalendarClock, CheckCircle2, CheckSquare, ChevronDown, ClipboardList, Loader2, MessageSquare, Plus, RefreshCw, Search, Timer } from 'lucide-react';
import type { WorkItem, WorkItemPage, WorkPriority } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { PRIORITIES, workQuery } from './lib/team';
import { doneWeekComplete, dueIn, groupWork, WORK_GROUPS, type WorkGroup } from './lib/workTasks';
import { Modal } from './Dialog';
import { KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { PaneDenied, PaneProblem, SPLIT_QUERY, useMediaQuery } from './PageParts';
import { CommandBox, useDirectory, WorkDetail, WorkForm } from './TeamView';
import './team.css';
import './workTasks.css';

type Scope = 'mine' | 'all';
type Lists = { state: 'loading' } | { state: 'ready'; open: WorkItemPage; openItems: WorkItem[]; done: WorkItem[]; doneComplete: boolean } | { state: 'error' | 'denied'; message: string };
const PRIORITY_TONE: Record<WorkPriority, 'bad' | 'warn' | 'info' | 'muted'> = { urgent: 'bad', high: 'warn', normal: 'info', low: 'muted' };
/** At most this many pages of 200 done items are read for Completed this week; past that the figure shows a +. */
const DONE_PAGES = 5;
const GROUP_TONE: Record<WorkGroup, string> = { overdue: 'bad', today: 'warn', in_progress: 'ok', blocked: 'bad', todo: 'muted', done_week: 'ok' };

export function WorkTasks({ scope, live = 0, focusItemId = null }: { scope: Scope; live?: number; focusItemId?: string | null }) {
  const dir = useDirectory();
  const wide = useMediaQuery(SPLIT_QUERY);
  const ids = useId();
  const [typed, setTyped] = useState('');
  const [q, setQ] = useState('');
  const [priority, setPriority] = useState<WorkPriority | ''>('');
  const [lists, setLists] = useState<Lists>({ state: 'loading' });
  const [moreBusy, setMoreBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(focusItemId);
  const [creating, setCreating] = useState(false);
  const [closed, setClosed] = useState<Set<WorkGroup>>(new Set());
  const [rowBusy, setRowBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [revision, setRevision] = useState(0);
  const changed = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => { const timer = setTimeout(() => setQ(typed.trim()), 300); return () => clearTimeout(timer); }, [typed]);
  useEffect(() => { if (live) changed(); }, [live, changed]);

  useEffect(() => {
    let alive = true;
    setLists((old) => (old.state === 'ready' ? old : { state: 'loading' }));
    // Completed this week: page the done list (newest update first) until it reaches last week.
    const doneThisWeek = async () => {
      const items: WorkItem[] = [];
      for (let offset = 0, pages = 0; pages < DONE_PAGES; pages += 1) {
        const page = await team.listWork(workQuery({ scope, status: 'done', q, sort: 'updated', limit: 200, offset }));
        items.push(...page.items);
        if (doneWeekComplete(page, new Date())) return { items, complete: true };
        offset = page.next_offset ?? offset + page.items.length;
      }
      return { items, complete: false };
    };
    Promise.all([
      team.listWork(workQuery({ scope, status: 'open', q, sort: 'due', limit: 200 })),
      doneThisWeek(),
    ]).then(([open, done]) => { if (alive) setLists({ state: 'ready', open, openItems: open.items, done: done.items, doneComplete: done.complete }); },
      (e: unknown) => { if (alive) setLists({ state: isPermissionDenied(e) ? 'denied' : 'error', message: describeError(e) }); });
    return () => { alive = false; };
  }, [scope, q, revision]);

  async function loadMore() {
    if (lists.state !== 'ready' || lists.open.next_offset === null) return;
    setMoreBusy(true);
    try {
      const page = await team.listWork(workQuery({ scope, status: 'open', q, sort: 'due', limit: 200, offset: lists.open.next_offset }));
      setLists({ ...lists, open: page, openItems: [...lists.openItems, ...page.items] });
    } catch (e) { setFlash({ text: t('team.loadFailed', { message: describeError(e) }), error: true }); } finally { setMoreBusy(false); }
  }

  async function markDone(item: WorkItem, done: boolean) {
    setRowBusy(item.id); setFlash(null);
    try {
      await team.updateWork(item.id, { version: item.version, status: done ? 'done' : 'todo' });
      setFlash({ text: done ? t('wt.markedDone', { title: item.title }) : t('wt.reopened', { title: item.title }) });
      changed();
    } catch (e) {
      const status = (e as { status?: number }).status;
      setFlash({ text: status === 409 ? t('team.conflict', { message: describeError(e) }) : isPermissionDenied(e) ? t('team.denied', { message: describeError(e) }) : describeError(e), error: true });
      if (status === 409) changed();
    } finally { setRowBusy(null); }
  }

  const now = new Date();
  const groups = useMemo(() => (lists.state === 'ready' ? groupWork([...lists.openItems, ...lists.done], new Date(), priority) : null), [lists, priority]);
  const total = groups ? WORK_GROUPS.reduce((sum, g) => sum + groups[g].length, 0) : 0;
  const partial = lists.state === 'ready' && lists.open.next_offset !== null;
  const plus = partial ? '+' : '';
  const toggle = (g: WorkGroup) => setClosed((old) => { const next = new Set(old); if (next.has(g)) next.delete(g); else next.add(g); return next; });
  const showDetail = Boolean(selected);
  const showList = wide || !showDetail;
  const filtered = Boolean(q || priority);

  if (lists.state === 'denied') return <section className="finCard"><PaneDenied title={t('wt.denied')} text={lists.message} /></section>;

  return <div className="wtPage">
    <KpiRow label={scope === 'mine' ? t('wt.kpis.mine') : t('wt.kpis.team')}>
      <KpiCard icon={<AlertTriangle />} label={t('wt.group.overdue')} value={groups ? `${formatNumber(groups.overdue.length)}${plus}` : '-'} hint={groups ? (groups.overdue.length ? t('wt.kpi.overdueHint') : t('wt.kpi.overdueNone')) : lists.state === 'error' ? t('page.figure.unavailable') : undefined} tone={groups?.overdue.length ? 'bad' : ''} />
      <KpiCard icon={<CalendarClock />} label={t('wt.group.today')} value={groups ? `${formatNumber(groups.today.length)}${plus}` : '-'} hint={groups ? t('wt.kpi.todayHint') : undefined} tone={groups?.today.length ? 'warn' : ''} />
      <KpiCard icon={<RefreshCw />} label={t('wt.group.in_progress')} value={groups ? `${formatNumber(groups.in_progress.length)}${plus}` : '-'} hint={groups ? t('wt.kpi.blocked', { count: groups.blocked.length }) : undefined} />
      <KpiCard icon={<CheckCircle2 />} label={t('wt.group.done_week')} value={groups && lists.state === 'ready' ? `${formatNumber(groups.done_week.length)}${lists.doneComplete ? '' : '+'}` : '-'} hint={groups ? t('wt.kpi.doneHint') : undefined} tone={groups?.done_week.length ? 'good' : ''} />
    </KpiRow>
    <div className={`wtLayout ${showDetail ? 'withDetail' : ''}`}>
      {showList && <section className="finCard wtTable" aria-labelledby={`${ids}-title`} aria-busy={lists.state === 'loading'}>
        <div className="wtToolbar">
          <h2 id={`${ids}-title`}>{scope === 'mine' ? t('wt.title.mine') : t('wt.title.team')}{groups && <small className="finCardSub">{t('wt.count', { count: total })}{plus}</small>}</h2>
          <div className="wtTools" role="search">
            <label className="visuallyHidden" htmlFor={`${ids}-q`}>{t('wt.search')}</label>
            <span className="wtSearch"><Search aria-hidden="true" /><input id={`${ids}-q`} type="search" value={typed} maxLength={200} placeholder={t('wt.search')} onChange={(e) => setTyped(e.target.value)} /></span>
            <label className="wtSelect"><span>{t('team.field.priority')}</span>
              <select value={priority} onChange={(e) => setPriority(e.target.value as WorkPriority | '')}>
                <option value="">{t('wt.priority.all')}</option>
                {PRIORITIES.map((p) => <option key={p} value={p}>{t(`team.priority.${p}` as MessageKey)}</option>)}
              </select></label>
            {filtered && <button type="button" className="secondary" onClick={() => { setTyped(''); setQ(''); setPriority(''); }}>{t('wt.clear')}</button>}
            <button type="button" onClick={() => setCreating(true)} aria-haspopup="dialog"><Plus aria-hidden="true" />{t('wt.create')}</button>
          </div>
        </div>
        {dir.error && <p className="notice errorNotice" role="alert">{t('team.directoryFailed', { message: dir.error })}</p>}
        {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}>{flash.text}</p>}
        {lists.state === 'loading' && <p className="notice" role="status"><Loader2 aria-hidden="true" className="spin" />{t('team.loading')}</p>}
        {lists.state === 'error' && <PaneProblem text={t('team.loadFailed', { message: lists.message })} onRetry={changed} />}
        {groups && total === 0 && <div className="paneEmpty"><ClipboardList aria-hidden="true" />
          <h3>{filtered ? t('team.empty.filtered') : scope === 'mine' ? t('wt.empty.mine') : t('wt.empty.team')}</h3>
          <p>{filtered ? t('wt.empty.filteredText') : t('wt.empty.text')}</p>
          <div className="actions">{filtered ? <button type="button" className="secondary" onClick={() => { setTyped(''); setQ(''); setPriority(''); }}>{t('wt.clear')}</button>
            : <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('wt.create')}</button>}</div></div>}
        {groups && total > 0 && <div className="finTableWrap" tabIndex={0} aria-label={t('wt.tableScroll')}>
          <table className="finGrid wtGrid">
            <caption className="visuallyHidden">{scope === 'mine' ? t('wt.title.mine') : t('wt.title.team')}</caption>
            <thead><tr>
              <th scope="col" className="wtCheck"><span className="visuallyHidden">{t('wt.col.done')}</span></th>
              <th scope="col">{t('wt.col.task')}</th><th scope="col">{t('team.field.priority')}</th><th scope="col">{t('wt.col.owner')}</th>
              <th scope="col">{t('wt.col.due')}</th><th scope="col">{t('wt.col.sla')}</th>
            </tr></thead>
            {WORK_GROUPS.filter((g) => groups[g].length).map((g) => <tbody key={g} className={`wtGroup ${GROUP_TONE[g]}`}>
              <tr className="wtGroupRow"><th scope="colgroup" colSpan={6}>
                <button type="button" aria-expanded={!closed.has(g)} onClick={() => toggle(g)}><ChevronDown aria-hidden="true" className="wtChevron" />{t(`wt.group.${g}` as MessageKey)} <span>({formatNumber(groups[g].length)})</span></button>
              </th></tr>
              {!closed.has(g) && groups[g].map((item) => <tr key={item.id} className={selected === item.id ? 'selected' : undefined}>
                <td className="wtCheck"><input type="checkbox" checked={item.status === 'done'} disabled={rowBusy !== null} aria-label={item.status === 'done' ? t('wt.reopen', { title: item.title }) : t('wt.markDone', { title: item.title })}
                  onChange={(e) => void markDone(item, e.target.checked)} /></td>
                <th scope="row"><button type="button" className="wtTitle" aria-current={selected === item.id ? 'true' : undefined} onClick={() => setSelected(item.id)}>
                  <strong dir="auto">{item.title}</strong>
                  {(item.checklist_total > 0 || item.comment_count > 0) && <small>
                    {item.checklist_total > 0 && <span><CheckSquare aria-hidden="true" />{t('team.checklistCount', { done: item.checklist_done, total: item.checklist_total })}</span>}
                    {item.comment_count > 0 && <span><MessageSquare aria-hidden="true" />{t('wt.comments', { count: item.comment_count })}</span>}
                  </small>}
                </button></th>
                <td><StatusChip tone={PRIORITY_TONE[item.priority]}>{t(`team.priority.${item.priority}` as MessageKey)}</StatusChip></td>
                <td dir="auto">{item.assignee_name ?? <span className="muted">{t('team.unassigned')}</span>}</td>
                <td className={g === 'overdue' ? 'wtLate' : undefined}>{item.due_at ? formatDateTime(item.due_at, { day: 'numeric', month: 'short', year: 'numeric' }) : <span className="muted">-</span>}</td>
                <td><Sla item={item} now={now} /></td>
              </tr>)}
            </tbody>)}
          </table>
        </div>}
        {partial && <div className="actions"><button type="button" className="secondary" disabled={moreBusy} onClick={() => void loadMore()}>{moreBusy ? t('team.loading') : t('team.loadMore')}</button></div>}
      </section>}
      {showDetail && selected && <div className="wtDetail"><WorkDetail key={selected} itemId={selected} options={dir.options} onClose={() => setSelected(null)} onChanged={changed} /></div>}
    </div>
    {showList && <div className="wtQuick"><CommandBox options={dir.options} onCreated={(item) => { changed(); setSelected(item.id); }} /></div>}
    {creating && <Modal title={t('wt.create')} className="formDialog workDialog" onClose={() => setCreating(false)}>
      <WorkForm options={dir.options} onCancel={() => setCreating(false)} onCreated={(item) => { setCreating(false); changed(); setSelected(item.id); }} />
    </Modal>}
  </div>;
}

function Sla({ item, now }: { item: WorkItem; now: Date }) {
  if (item.status === 'done') return <StatusChip tone="ok"><CheckCircle2 aria-hidden="true" />{t('wt.sla.done')}</StatusChip>;
  if (item.status === 'blocked') return <StatusChip tone="bad">{t('team.status.blocked')}</StatusChip>;
  if (!item.due_at) return <span className="muted">{t('team.noDue')}</span>;
  const left = dueIn(item.due_at, now);
  const words = t(`wt.sla.${left.late ? 'late' : 'in'}.${left.unit}` as MessageKey, { count: left.count });
  return <StatusChip tone={left.late ? 'bad' : left.unit === 'days' ? 'muted' : 'warn'}><Timer aria-hidden="true" />{words}</StatusChip>;
}
