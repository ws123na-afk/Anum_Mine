import { useCallback, useEffect, useRef, useState } from 'react';
import { Brain, CircleDot, FileText, Globe2, Link2, MessageSquare, Pencil, RefreshCw, Search, Trash2 } from 'lucide-react';
import type { Task } from '@anum/contracts';
import * as api from './lib/api';
import { memoryIndexState, memorySource, type RetrievalIndexStatus } from './lib/capabilities';
import { chatMemoryStatus, deletedMessage, type UserPreferences } from './lib/chatMemory';
import { describeError, isPermissionDenied } from './lib/errors';
import { ChatMemorySetting } from './ChatMemorySetting';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { indexStateLabel } from './lib/labels';
import { figure, groupBy, indexCounts, type MemoryGroupId } from './lib/pageSummary';
import { DialogButton, PageSummary } from './PageParts';
import { Modal } from './Dialog';
import type { MemoryTarget } from './SourcesUsed';
import { useIsOwner } from './useIsOwner';
import './features.css';

type Status = { state: 'loading' } | { state: 'ready'; value: RetrievalIndexStatus } | { state: 'error' };

/**
 * The Knowledge Base summary band (docs/knowledge-base.md): index state per source type from
 * GET /api/v1/retrieval/status. Shared by the Memories and Collections tabs.
 */
export function KnowledgeSummary({ status, onRetry }: { status: Status; onRetry: () => void }) {
  const value = status.state === 'ready' ? status.value : null;
  const memories = value ? indexCounts(value.sources, 'memory') : null;
  const files = value ? indexCounts(value.sources, 'file') : null;
  const failed = (memories?.failed ?? 0) + (files?.failed ?? 0);
  const figures = [
    figure('memories', t('pages.kb.fig.memories'), status, () => ({ value: formatNumber(memories!.indexed), hint: t('pages.kb.fig.ofTotal', { total: formatNumber(memories!.total) }) })),
    figure('files', t('pages.kb.fig.files'), status, () => ({ value: formatNumber(files!.indexed), hint: t('pages.kb.fig.filesHint', { total: formatNumber(files!.total), skipped: formatNumber(files!.skipped) }) })),
    figure('failed', t('pages.kb.fig.failed'), status, () => ({ value: formatNumber(failed), hint: failed ? t('pages.kb.fig.failedHint') : t('pages.kb.fig.noneFailed'), tone: failed ? 'danger' : '' })),
    figure('chunks', t('pages.kb.fig.chunks'), status, () => ({ value: formatNumber(value!.chunks), hint: value!.stale ? t('pages.kb.fig.stale', { count: value!.stale }) : t('pages.kb.fig.model', { model: value!.embedding_model }), tone: value!.stale ? 'warn' : '' })),
  ];
  return <PageSummary label={t('pages.kb.summary')} figures={figures} onRetry={onRetry} />;
}

/** The retrieval status as a load state, for the summary band. */
export function useRetrievalStatus(): { status: Status; reload: () => void; set: (value: RetrievalIndexStatus) => void } {
  const [status, setStatus] = useState<Status>({ state: 'loading' });
  const reload = useCallback(() => { setStatus({ state: 'loading' }); api.getRetrievalStatus().then((value) => setStatus({ state: 'ready', value }), () => setStatus({ state: 'error' })); }, []);
  useEffect(() => { reload(); }, [reload]);
  return { status, reload, set: (value) => setStatus({ state: 'ready', value }) };
}

type Show = 'all' | 'chats' | 'tasks' | 'workspace';
const GROUP_LABEL: Record<MemoryGroupId, MessageKey> = { chat: 'pages.kb.group.chat', task: 'pages.kb.group.task', workspace: 'pages.kb.group.workspace', other: 'pages.kb.group.other' };
/** Memories per page (`GET /api/v1/memories?limit=&offset=`, docs/memory.md#api); Load more fetches the next older page. */
export const MEMORY_PAGE = 50;
/** Days a workspace memory may be kept for, when not until deleted. */
const KEEP_DAYS = [7, 30, 90, 365] as const;

// A memory added here belongs to a task (kept with it) or to the whole workspace (docs/memory.md#workspace-memories).
// Its author, or a workspace owner, edits it in place (PATCH /api/v1/memories/{id}). Memories the Assistant
// remembered from the caller's own chats have no task: they link back to their conversation
// (docs/memory.md#from-conversations). `focus` is a memory a run used ("Sources used" on a task): the view
// scrolls to it and marks it. The list is paged: the newest MEMORY_PAGE first, older ones on Load more.
export function MemoryView({ tasks, onOpenConversation, initialOnlyChats = false, focus = null }: { tasks: Task[]; onOpenConversation: (conversationId: string) => void; initialOnlyChats?: boolean; focus?: MemoryTarget | null }) {
  const [items, setItems] = useState<api.MemoryNote[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const retrieval = useRetrievalStatus();
  const index = retrieval.status.state === 'ready' ? retrieval.status.value : null;
  const [query, setQuery] = useState(''), [applied, setApplied] = useState('');
  const [loading, setLoading] = useState(true), [loadingMore, setLoadingMore] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; text: string; problem: string | null } | null>(null);
  // "From my chats" keeps the caller's own memories remembered from their chats (source_type=conversation).
  const [show, setShow] = useState<Show>(initialOnlyChats ? 'chats' : 'all'), [confirmAll, setConfirmAll] = useState(false);
  const [prefs, setPrefs] = useState<UserPreferences | null>(null), [settingOpen, setSettingOpen] = useState(false);
  const focusedRow = useRef<HTMLTableRowElement>(null);
  const onlyChats = show === 'chats';
  const owner = useIsOwner();
  const me = api.currentUserId();

  const load = useCallback(async (q: string, chats: boolean) => {
    setLoading(true);
    try { const page = await api.searchMemories(q, chats, { limit: MEMORY_PAGE }); setItems(page.items); setTotal(page.total ?? page.items.length); setError(null); }
    catch (e) { setError(describeError(e)); } finally { setLoading(false); }
  }, []);
  const loadMore = async () => {
    setLoadingMore(true); setError(null);
    try {
      const page = await api.searchMemories(applied, onlyChats, { limit: MEMORY_PAGE, offset: items.length });
      const known = new Set(items.map((x) => x.id));
      // Older pages come before the ones already shown; the groups keep each row in date order.
      setItems([...page.items.filter((x) => !known.has(x.id)), ...items]);
      setTotal(page.total ?? items.length + page.items.length);
    } catch (e) { setError(describeError(e)); } finally { setLoadingMore(false); }
  };
  const loadPrefs = useCallback(() => { api.getMyPreferences().then(setPrefs, () => setPrefs(null)); }, []);
  useEffect(() => { void load(applied, onlyChats); }, [load, applied, onlyChats]);
  useEffect(() => { loadPrefs(); }, [loadPrefs]);
  const shown = show === 'tasks' ? items.filter((x) => memorySource(x).kind === 'task') : show === 'workspace' ? items.filter((x) => memorySource(x).kind === 'workspace') : items;
  const focusListed = !!focus && shown.some(x => x.id === focus.id);
  // The lookup shortens long first lines with an ellipsis, which the text search would not match.
  const focusSearch = (focus?.title ?? '').replace(/…$/, '').trim();
  // Bring the memory the run used into view once the list holds it, and move focus to it.
  useEffect(() => { if (focusListed && !loading) { focusedRow.current?.scrollIntoView({ block: 'center' }); focusedRow.current?.focus({ preventScroll: true }); } }, [focusListed, loading]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNote(null);
    try { await fn(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const remove = (id: string) => act(async () => { await api.deleteMemory(id); setConfirming(null); setNote(t('memory.deleted')); await load(applied, onlyChats); retrieval.reload(); });
  const removeAllChats = () => act(async () => { const r = await api.deleteMyChatMemories(); setConfirmAll(false); setNote(deletedMessage(r.deleted)); await load(applied, onlyChats); retrieval.reload(); });
  const saveEdit = async () => {
    if (!editing || !editing.text.trim()) return;
    setBusy(true);
    try {
      const updated = await api.updateMemory(editing.id, editing.text.trim());
      setItems((list) => list.map((x) => (x.id === updated.id ? updated : x)));
      setEditing(null); setNote(t('memory.edited')); retrieval.reload();
    } catch (e) { setEditing({ ...editing, problem: isPermissionDenied(e) ? t('memory.editDenied') : describeError(e) }); }
    finally { setBusy(false); }
  };
  const pick = (next: Show) => { setShow(next); setConfirmAll(false); setConfirming(null); setEditing(null); };
  const chatCount = items.filter(x => memorySource(x).kind === 'chat').length;
  const reindex = () => act(async () => { const r = await api.reindexRetrieval(); retrieval.set(r.status); setNote(r.failed ? t('memory.reindexedFailed', { indexed: r.indexed, reused: r.reused, failed: r.failed }) : t('memory.reindexed', { indexed: r.indexed, reused: r.reused })); });
  const groups = groupBy(shown, (x) => memorySource(x).kind);
  const chatState = prefs ? chatMemoryStatus(prefs) : null;
  // Who may edit: the author, or an owner for a shared note (a private chat memory only its owner, who is its author).
  const canEdit = (x: api.MemoryNote) => x.provenance.created_by_user_id === me || (owner === true && !x.owner_user_id);
  const editedBy = (x: api.MemoryNote) => { const by = x.provenance.metadata?.last_edited_by; return x.updated_at && typeof by === 'string' ? t('memory.editedBy', { when: formatDateTime(x.updated_at, { dateStyle: 'medium', timeStyle: 'short' }), user: by }) : null; };
  const hasMore = total !== null && items.length < total;

  const row = (x: api.MemoryNote) => {
    const idx = memoryIndexState(index, x.id), source = memorySource(x);
    const focused = focus?.id === x.id;
    const isEditing = editing?.id === x.id;
    const icon = source.kind === 'chat' ? <MessageSquare /> : source.kind === 'workspace' ? <Globe2 /> : <FileText />;
    return <tr className={`memoryRow ${focused ? 'focusedRow' : ''}`} key={x.id} id={`memory-${x.id}`} ref={focused ? focusedRow : undefined} tabIndex={focused ? -1 : undefined} aria-current={focused ? 'true' : undefined} aria-describedby={focused ? 'memory-focus-label' : undefined}>
      <td className="memoryCell">
        {focused && <span className="featureChip focusChip" id="memory-focus-label"><Link2 aria-hidden="true" />{t('memory.usedByRun')}</span>}
        <span className="memoryLabel"><span className="typeIcon small" aria-hidden="true">{icon}</span><strong dir="auto">{source.label}</strong></span>
        {isEditing
          ? <form className="memoryEdit" onSubmit={(e) => { e.preventDefault(); void saveEdit(); }}>
            <label className="visuallyHidden" htmlFor={`memory-edit-${x.id}`}>{t('memory.editLabel')}</label>
            <textarea id={`memory-edit-${x.id}`} value={editing.text} maxLength={16000} dir="auto" rows={3} autoFocus onChange={(e) => setEditing({ ...editing, text: e.target.value })} onKeyDown={(e) => { if (e.key === 'Escape') setEditing(null); }} />
            {editing.problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{editing.problem}</p>}
            <div className="actions"><button type="submit" disabled={busy || !editing.text.trim()}>{busy ? t('admin.saving') : t('memory.saveEdit')}</button><button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
          </form>
          : <p dir="auto">{x.content}</p>}
        {source.kind === 'chat' && <small className="memorySource">{t('memory.privateFrom', { title: source.title })}</small>}
        {editedBy(x) && <small className="memorySource">{editedBy(x)}</small>}
      </td>
      <td>{source.kind === 'chat'
        ? <button type="button" className="secondary memoryChatLink" onClick={() => onOpenConversation(source.conversationId)} aria-label={t('memory.openChatNamed', { title: source.title })}><MessageSquare aria-hidden="true" />{t('memory.openChat')}</button>
        : <code>{source.kind === 'task' ? source.taskId : '-'}</code>}</td>
      <td className="optional nowrap muted"><time dateTime={x.created_at}>{formatDateTime(x.created_at, { dateStyle: 'medium' })}</time></td>
      <td><span className={`featureChip ${idx.state}`} title={idx.error ?? undefined}>{indexStateLabel(idx.state)}</span></td>
      <td><div className="cellActions">{confirming === x.id
        ? <span className="featureConfirm"><button className="danger" disabled={busy} onClick={() => void remove(x.id)}>{t('common.delete')}</button><button className="secondary" onClick={() => setConfirming(null)}>{t('common.cancel')}</button></span>
        : <>
          {canEdit(x) && !isEditing && <button className="iconButton" aria-label={t('memory.edit')} title={t('memory.edit')} onClick={() => { setConfirming(null); setEditing({ id: x.id, text: x.content, problem: null }); }}><Pencil aria-hidden="true" /></button>}
          <button className="iconButton" aria-label={t('memory.deleteMemory')} title={t('memory.deleteMemory')} onClick={() => { setEditing(null); setConfirming(x.id); }}><Trash2 aria-hidden="true" /></button>
        </>}</div></td>
    </tr>;
  };

  return <>
    <KnowledgeSummary status={retrieval.status} onRetry={retrieval.reload} />
    <div className="kbBar">
      <form className="toolbar kbSearch" role="search" onSubmit={e => { e.preventDefault(); setApplied(query.trim()); }}>
        <label className="visuallyHidden" htmlFor="memory-search">{t('memory.search')}</label>
        <input id="memory-search" type="search" required value={query} onChange={e => { setQuery(e.target.value); if (!e.target.value) setApplied(''); }} placeholder={t('memory.searchPlaceholder')} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
        {applied && <button type="button" className="secondary" onClick={() => { setQuery(''); setApplied(''); }}>{t('common.clear')}</button>}
      </form>
      <div className="memoryFilter segmentedRadios" role="radiogroup" aria-labelledby="memory-filter-label"><span id="memory-filter-label" className="visuallyHidden">{t('memory.show')}</span>
        <label><input type="radio" name="memory-filter" checked={show === 'all'} onChange={() => pick('all')} />{t('memory.all')}</label>
        <label><input type="radio" name="memory-filter" checked={show === 'chats'} onChange={() => pick('chats')} />{t('memory.fromChats')}</label>
        <label><input type="radio" name="memory-filter" checked={show === 'tasks'} onChange={() => pick('tasks')} />{t('pages.kb.taskNotes')}</label>
        <label><input type="radio" name="memory-filter" checked={show === 'workspace'} onChange={() => pick('workspace')} />{t('pages.kb.group.workspace')}</label>
      </div>
      <div className="kbBarActions">
        <button type="button" className={`chipButton ${chatState?.tone ?? 'neutral'}`} aria-haspopup="dialog" onClick={() => setSettingOpen(true)}><span className="chipDot" aria-hidden="true" /><span>{chatState ? t('pages.kb.chatMemoryChip', { state: chatState.badge }) : t('pages.kb.chatMemoryChipUnknown')}</span></button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void reindex()}><RefreshCw aria-hidden="true" />{t('memory.reindex')}</button>
        <DialogButton label={t('memory.add')} title={t('pages.kb.addMemoryTitle')} icon={<Brain aria-hidden="true" />}>
          {(close) => <AddMemory tasks={tasks} onCancel={close} onAdded={async () => { close(); setNote(t('memory.added')); if (show === 'chats') pick('all'); else await load(applied, onlyChats); retrieval.reload(); }} />}
        </DialogButton>
      </div>
    </div>
    {settingOpen && <Modal title={t('pages.kb.chatMemoryTitle')} className="formDialog" onClose={() => { setSettingOpen(false); loadPrefs(); }}>
      <ChatMemorySetting reviewLabel={t('memory.showFromChats')} onReview={() => { setSettingOpen(false); loadPrefs(); pick('chats'); }} />
      <div className="actions"><button type="button" className="secondary" onClick={() => { setSettingOpen(false); loadPrefs(); }}>{t('pages.kb.done')}</button></div>
    </Modal>}
    {error && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('pages.kb.memoriesFailed', { message: error })}</span><button type="button" className="secondary" onClick={() => void load(applied, onlyChats)}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>}
    {note && <p className="notice" role="status"><CircleDot aria-hidden="true" />{note}</p>}
    {loading && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('memory.loading')}</p>}
    {focus && !focusListed && !loading && !error && <div className="notice memoryFocusMissing" role="status"><Link2 aria-hidden="true" />
      <span>{applied ? t('memory.focusNoMatch', { query: applied }) : t('memory.focusMissing')}</span>
      {focusSearch && applied !== focusSearch && <button type="button" className="secondary" onClick={() => { setQuery(focusSearch); setApplied(focusSearch); pick('all'); }}><Search aria-hidden="true" />{t('memory.searchForIt')}</button>}</div>}
    {onlyChats && !loading && !error && chatCount > 0 && <div className="memoryBulk" role="group" aria-label={t('memory.bulkLabel')}>
      <p className="featureMeta">{applied ? t('memory.bulkSearchNote') : t('memory.bulkNote')}</p>
      <div className="actions">{confirmAll
        ? <><button type="button" className="danger" disabled={busy} onClick={() => void removeAllChats()}>{t('memory.deleteAll')}</button><button type="button" className="secondary" onClick={() => setConfirmAll(false)}>{t('common.cancel')}</button></>
        : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirmAll(true)}><Trash2 aria-hidden="true" />{t('memory.deleteAllFromChats')}</button>}</div>
    </div>}
    {shown.length > 0 && <section className="pane memoryTable" id="memory-list" aria-label={onlyChats ? t('memory.listChats') : t('memory.list')}>
      <div className="dataTableWrap"><table className="dataTable memoryRows">
        <caption className="visuallyHidden">{onlyChats ? t('memory.listChats') : t('memory.list')}</caption>
        <colgroup><col className="memoryColText" /><col className="memoryColSource" /><col className="optional memoryColAdded" /><col className="memoryColIndex" /><col className="memoryColActions" /></colgroup>
        <thead><tr><th scope="col">{t('pages.kb.col.memory')}</th><th scope="col">{t('pages.kb.col.source')}</th><th scope="col" className="optional">{t('pages.kb.col.added')}</th><th scope="col">{t('pages.kb.col.index')}</th><th scope="col"><span className="visuallyHidden">{t('pages.files.col.actions')}</span></th></tr></thead>
        {groups.map(({ group, items: rows }) => <tbody key={group}>
          <tr className="memoryGroupRow"><th scope="colgroup" colSpan={5}>{t(GROUP_LABEL[group])} <span className="muted">{formatNumber(rows.length)}</span></th></tr>
          {rows.map(row)}
        </tbody>)}
      </table></div>
    </section>}
    {!loading && !error && items.length > 0 && <div className="memoryPaging">
      <p className="featureMeta" role="status">{t('memory.showing', { shown: formatNumber(items.length), count: total ?? items.length })}</p>
      {hasMore && <button type="button" className="secondary" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? t('memory.loadingMore') : t('memory.loadMore')}</button>}
    </div>}
    {!loading && !error && !shown.length && <div className="surface emptyState"><Search aria-hidden="true" /><h3>{applied ? t('memory.empty.noMatch') : onlyChats ? t('memory.empty.noChats') : show === 'tasks' ? t('pages.kb.noTaskNotes') : t('memory.empty.none')}</h3><p>{applied ? t('memory.empty.noMatchText', { query: applied }) : onlyChats ? t('memory.empty.noChatsText') : t('memory.empty.noneText')}</p>{show !== 'all' && !applied && <button type="button" className="secondary" onClick={() => pick('all')}>{t('memory.showAll')}</button>}</div>}
  </>;
}

/** The expiry a "Keep" choice means, as the API wants it (ISO), or null for "until deleted". */
export function keepExpiry(keep: string, now: Date = new Date()): string | null {
  const days = Number(keep);
  if (!Number.isFinite(days) || days <= 0) return null;
  return new Date(now.getTime() + days * 86_400_000).toISOString();
}

/** The "Add memory" dialog: a note on a task, or a workspace memory with its own retention. */
function AddMemory({ tasks, onAdded, onCancel }: { tasks: Task[]; onAdded: () => Promise<void>; onCancel: () => void }) {
  const [taskId, setTaskId] = useState(''), [content, setContent] = useState(''), [keep, setKeep] = useState('indefinite');
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setProblem(null);
    try { await api.createMemory(taskId || null, content.trim(), taskId ? null : keepExpiry(keep)); await onAdded(); }
    catch (e) { setProblem(describeError(e)); setBusy(false); }
  }
  return <form onSubmit={(e) => { e.preventDefault(); if (content.trim()) void submit(); }}>
    <label className="field"><span>{t('memory.task')}</span><select value={taskId} onChange={e => setTaskId(e.target.value)} data-autofocus><option value="">{t('memory.wholeWorkspace')}</option>{tasks.map(task => <option value={task.id} key={task.id}>{task.title}</option>)}</select></label>
    {!taskId && <label className="field"><span>{t('memory.keep')}</span><select value={keep} onChange={e => setKeep(e.target.value)}>
      <option value="indefinite">{t('memory.keep.indefinite')}</option>
      {KEEP_DAYS.map((days) => <option key={days} value={String(days)}>{t('memory.keep.days', { count: days })}</option>)}
    </select></label>}
    <label className="field"><span>{t('memory.memory')}</span><textarea value={content} onChange={e => setContent(e.target.value)} placeholder={t('memory.addPlaceholder')} maxLength={16000} dir="auto" /></label>
    <p className="featureMeta">{taskId ? t('memory.taskOnly') : tasks.length ? t('memory.workspaceHint') : t('pages.kb.noTasks')}</p>
    {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    <div className="actions"><button type="submit" disabled={busy || !content.trim()}><Brain aria-hidden="true" />{t('memory.add')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
  </form>;
}
