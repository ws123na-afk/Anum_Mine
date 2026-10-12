import { useCallback, useEffect, useRef, useState } from 'react';
import { Brain, CircleDot, FileText, Link2, MessageSquare, RefreshCw, Search, Trash2 } from 'lucide-react';
import type { Task } from '@anum/contracts';
import * as api from './lib/api';
import { indexSummary, memoryIndexState, memorySource, type RetrievalIndexStatus } from './lib/capabilities';
import { deletedMessage } from './lib/chatMemory';
import { describeError } from './lib/errors';
import { ChatMemorySetting } from './ChatMemorySetting';
import { t } from './lib/i18n';
import { indexStateLabel } from './lib/labels';
import type { MemoryTarget } from './SourcesUsed';
import './features.css';

// A memory added here belongs to a task (POST /api/v1/memories needs task_id) and has no update
// endpoint, so it is changed by deleting and re-adding it. Memories the Assistant remembered from
// the caller's own chats have no task: they link back to their conversation (docs/memory.md#from-conversations).
// `focus` is a memory a run used ("Sources used" on a task): the view scrolls to it and marks it.
export function MemoryView({ tasks, onOpenConversation, initialOnlyChats = false, focus = null }: { tasks: Task[]; onOpenConversation: (conversationId: string) => void; initialOnlyChats?: boolean; focus?: MemoryTarget | null }) {
  const [items, setItems] = useState<api.MemoryNote[]>([]);
  const [index, setIndex] = useState<RetrievalIndexStatus | null>(null);
  const [query, setQuery] = useState(''), [applied, setApplied] = useState('');
  const [content, setContent] = useState(''), [taskId, setTaskId] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  // "From my chats" keeps the caller's own memories remembered from their chats (source_type=conversation).
  const [onlyChats, setOnlyChats] = useState(initialOnlyChats), [confirmAll, setConfirmAll] = useState(false);
  const focusedRow = useRef<HTMLDivElement>(null);

  const load = useCallback(async (q: string, chats: boolean) => {
    setLoading(true);
    try {
      const [notes, status] = await Promise.all([api.searchMemories(q, chats), api.getRetrievalStatus().catch(() => null)]);
      setItems(notes); setIndex(status); setError(null);
    } catch (e) { setError(describeError(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(applied, onlyChats); }, [load, applied, onlyChats]);
  useEffect(() => { if (!taskId && tasks[0]) setTaskId(tasks[0].id); }, [tasks, taskId]);
  const focusListed = !!focus && items.some(x => x.id === focus.id);
  // The lookup shortens long first lines with an ellipsis, which the text search would not match.
  const focusSearch = (focus?.title ?? '').replace(/\u2026$/, '').trim();
  // Bring the memory the run used into view once the list holds it, and move focus to it.
  useEffect(() => { if (focusListed && !loading) { focusedRow.current?.scrollIntoView({ block: 'center' }); focusedRow.current?.focus({ preventScroll: true }); } }, [focusListed, loading]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNote(null);
    try { await fn(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const add = () => act(async () => { await api.createMemory(taskId, content.trim()); setContent(''); setNote(t('memory.added')); await load(applied, onlyChats); });
  const remove = (id: string) => act(async () => { await api.deleteMemory(id); setConfirming(null); setNote(t('memory.deleted')); await load(applied, onlyChats); });
  const removeAllChats = () => act(async () => { const r = await api.deleteMyChatMemories(); setConfirmAll(false); setNote(deletedMessage(r.deleted)); await load(applied, onlyChats); });
  const showChats = (chats: boolean) => { setOnlyChats(chats); setConfirmAll(false); setConfirming(null); };
  const chatCount = items.filter(x => memorySource(x).kind === 'chat').length;
  const reindex = () => act(async () => { const r = await api.reindexRetrieval(); setIndex(r.status); setNote(r.failed ? t('memory.reindexedFailed', { indexed: r.indexed, reused: r.reused, failed: r.failed }) : t('memory.reindexed', { indexed: r.indexed, reused: r.reused })); });

  return <>
    <section className="moduleIntro"><div><p className="eyebrow">{t('memory.eyebrow')}</p><h2>{t('memory.title')}</h2><p>{t('memory.intro')}</p></div>
      <button disabled={busy} onClick={() => void reindex()}><RefreshCw aria-hidden="true" />{t('memory.reindex')}</button></section>
    <ChatMemorySetting reviewLabel={t('memory.showFromChats')} onReview={() => { showChats(true); document.getElementById('memory-list')?.scrollIntoView({ block: 'start' }); }} />
    <form className="surface featureSearch" role="search" onSubmit={e => { e.preventDefault(); setApplied(query.trim()); }}>
      <label className="field"><span>{t('memory.search')}</span><input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder={t('memory.searchPlaceholder')} /></label>
      <div className="memoryFilter" role="radiogroup" aria-labelledby="memory-filter-label"><span id="memory-filter-label">{t('memory.show')}</span>
        <label><input type="radio" name="memory-filter" checked={!onlyChats} onChange={() => showChats(false)} />{t('memory.all')}</label>
        <label><input type="radio" name="memory-filter" checked={onlyChats} onChange={() => showChats(true)} />{t('memory.fromChats')}</label>
      </div>
      <div className="actions"><button type="submit"><Search aria-hidden="true" />{t('common.search')}</button>{applied && <button type="button" className="secondary" onClick={() => { setQuery(''); setApplied(''); }}>{t('common.clear')}</button>}</div>
      <p className="featureMeta">{indexSummary(index)}</p>
    </form>
    {/* Adding is for task notes; the chat filter lists only what the Assistant remembered. */}
    {!onlyChats && <section className="surface composerSurface"><div className="formGrid">
      <label className="field"><span>{t('memory.task')}</span><select value={taskId} onChange={e => setTaskId(e.target.value)}><option value="">{t('memory.selectTask')}</option>{tasks.map(t => <option value={t.id} key={t.id}>{t.title}</option>)}</select></label>
      <label className="field"><span>{t('memory.memory')}</span><input value={content} onChange={e => setContent(e.target.value)} placeholder={t('memory.addPlaceholder')} maxLength={16000} /></label>
    </div><p className="featureMeta">{t('memory.taskOnly')}</p>
      <div className="actions"><button disabled={busy || !taskId || !content.trim()} onClick={() => void add()}><Brain aria-hidden="true" />{t('memory.add')}</button></div></section>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status"><CircleDot aria-hidden="true" />{note}</p>}
    {loading && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('memory.loading')}</p>}
    {focus && !focusListed && !loading && !error && <div className="notice memoryFocusMissing" role="status"><Link2 aria-hidden="true" />
      <span>{applied ? t('memory.focusNoMatch', { query: applied }) : t('memory.focusMissing')}</span>
      {focusSearch && applied !== focusSearch && <button type="button" className="secondary" onClick={() => { setQuery(focusSearch); setApplied(focusSearch); showChats(false); }}><Search aria-hidden="true" />{t('memory.searchForIt')}</button>}</div>}
    {onlyChats && !loading && !error && chatCount > 0 && <div className="memoryBulk" role="group" aria-label={t('memory.bulkLabel')}>
      <p className="featureMeta">{applied ? t('memory.bulkSearchNote') : t('memory.bulkNote')}</p>
      <div className="actions">{confirmAll
        ? <><button type="button" className="danger" disabled={busy} onClick={() => void removeAllChats()}>{t('memory.deleteAll')}</button><button type="button" className="secondary" onClick={() => setConfirmAll(false)}>{t('common.cancel')}</button></>
        : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirmAll(true)}><Trash2 aria-hidden="true" />{t('memory.deleteAllFromChats')}</button>}</div>
    </div>}
    <section className="surface memoryTable" id="memory-list" aria-label={onlyChats ? t('memory.listChats') : t('memory.list')}>{items.map(x => {
      const idx = memoryIndexState(index, x.id), source = memorySource(x);
      const focused = focus?.id === x.id;
      return <div className={`memoryRow ${focused ? 'focusedRow' : ''}`} key={x.id} id={`memory-${x.id}`} ref={focused ? focusedRow : undefined} tabIndex={focused ? -1 : undefined} aria-current={focused ? 'true' : undefined} aria-describedby={focused ? 'memory-focus-label' : undefined}>
        <div><span className="typeIcon" aria-hidden="true">{source.kind === 'chat' ? <MessageSquare /> : <FileText />}</span><div>{focused && <span className="featureChip focusChip" id="memory-focus-label"><Link2 aria-hidden="true" />{t('memory.usedByRun')}</span>}<strong dir="auto">{source.label}</strong><p dir="auto">{x.content}</p>
          {source.kind === 'chat' && <small className="memorySource">{t('memory.privateFrom', { title: source.title })}</small>}</div></div>
        <span className={`featureChip ${idx.state}`} title={idx.error ?? undefined}>{indexStateLabel(idx.state)}</span>
        {source.kind === 'chat'
          ? <button type="button" className="secondary memoryChatLink" onClick={() => onOpenConversation(source.conversationId)} aria-label={t('memory.openChatNamed', { title: source.title })}><MessageSquare aria-hidden="true" />{t('memory.openChat')}</button>
          : <code>{source.kind === 'task' ? source.taskId : ''}</code>}
        {confirming === x.id
          ? <span className="featureConfirm"><button className="danger" disabled={busy} onClick={() => void remove(x.id)}>{t('common.delete')}</button><button className="secondary" onClick={() => setConfirming(null)}>{t('common.cancel')}</button></span>
          : <button className="iconButton" aria-label={t('memory.deleteMemory')} title={t('memory.deleteMemory')} onClick={() => setConfirming(x.id)}><Trash2 aria-hidden="true" /></button>}
      </div>;
    })}</section>
    {!loading && !error && !items.length && <div className="emptyState"><Search aria-hidden="true" /><h3>{applied ? t('memory.empty.noMatch') : onlyChats ? t('memory.empty.noChats') : t('memory.empty.none')}</h3><p>{applied ? t('memory.empty.noMatchText', { query: applied }) : onlyChats ? t('memory.empty.noChatsText') : t('memory.empty.noneText')}</p>{onlyChats && !applied && <button type="button" className="secondary" onClick={() => showChats(false)}>{t('memory.showAll')}</button>}</div>}
  </>;
}
