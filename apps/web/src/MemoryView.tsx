import { useCallback, useEffect, useState } from 'react';
import { Brain, CircleDot, FileText, MessageSquare, RefreshCw, Search, Trash2 } from 'lucide-react';
import type { Task } from '@anum/contracts';
import * as api from './lib/api';
import { indexSummary, memoryIndexState, memorySource, type RetrievalIndexStatus } from './lib/capabilities';
import { deletedMessage } from './lib/chatMemory';
import { describeError } from './lib/errors';
import { ChatMemorySetting } from './ChatMemorySetting';
import './features.css';

// A memory added here belongs to a task (POST /api/v1/memories needs task_id) and has no update
// endpoint, so it is changed by deleting and re-adding it. Memories the Assistant remembered from
// the caller's own chats have no task: they link back to their conversation (docs/memory.md#from-conversations).
export function MemoryView({ tasks, onOpenConversation, initialOnlyChats = false }: { tasks: Task[]; onOpenConversation: (conversationId: string) => void; initialOnlyChats?: boolean }) {
  const [items, setItems] = useState<api.MemoryNote[]>([]);
  const [index, setIndex] = useState<RetrievalIndexStatus | null>(null);
  const [query, setQuery] = useState(''), [applied, setApplied] = useState('');
  const [content, setContent] = useState(''), [taskId, setTaskId] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  // "From my chats" keeps the caller's own memories remembered from their chats (source_type=conversation).
  const [onlyChats, setOnlyChats] = useState(initialOnlyChats), [confirmAll, setConfirmAll] = useState(false);

  const load = useCallback(async (q: string, chats: boolean) => {
    setLoading(true);
    try {
      const [notes, status] = await Promise.all([api.searchMemories(q, chats), api.getRetrievalStatus().catch(() => null)]);
      setItems(notes); setIndex(status); setError(null);
    } catch (e) { setError(describeError(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(applied, onlyChats); }, [load, applied, onlyChats]);
  useEffect(() => { if (!taskId && tasks[0]) setTaskId(tasks[0].id); }, [tasks, taskId]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNote(null);
    try { await fn(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  const add = () => act(async () => { await api.createMemory(taskId, content.trim()); setContent(''); setNote('Memory added.'); await load(applied, onlyChats); });
  const remove = (id: string) => act(async () => { await api.deleteMemory(id); setConfirming(null); setNote('Memory deleted.'); await load(applied, onlyChats); });
  const removeAllChats = () => act(async () => { const r = await api.deleteMyChatMemories(); setConfirmAll(false); setNote(deletedMessage(r.deleted)); await load(applied, onlyChats); });
  const showChats = (chats: boolean) => { setOnlyChats(chats); setConfirmAll(false); setConfirming(null); };
  const chatCount = items.filter(x => memorySource(x).kind === 'chat').length;
  const reindex = () => act(async () => { const r = await api.reindexRetrieval(); setIndex(r.status); setNote(`Reindexed: ${r.indexed} indexed, ${r.reused} unchanged${r.failed ? `, ${r.failed} failed` : ''}.`); });

  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Workspace knowledge</p><h2>Agent memory</h2><p>Live notes with provenance and retention, indexed for retrieval.</p></div>
      <button disabled={busy} onClick={() => void reindex()}><RefreshCw />Reindex</button></section>
    <ChatMemorySetting reviewLabel="Show memories from my chats" onReview={() => { showChats(true); document.getElementById('memory-list')?.scrollIntoView({ block: 'start' }); }} />
    <form className="surface featureSearch" role="search" onSubmit={e => { e.preventDefault(); setApplied(query.trim()); }}>
      <label className="field"><span>Search memory</span><input aria-label="Search memory" value={query} onChange={e => setQuery(e.target.value)} placeholder="Find notes by text" /></label>
      <div className="memoryFilter" role="radiogroup" aria-labelledby="memory-filter-label"><span id="memory-filter-label">Show</span>
        <label><input type="radio" name="memory-filter" checked={!onlyChats} onChange={() => showChats(false)} />All memories</label>
        <label><input type="radio" name="memory-filter" checked={onlyChats} onChange={() => showChats(true)} />From my chats</label>
      </div>
      <div className="actions"><button type="submit"><Search />Search</button>{applied && <button type="button" className="secondary" onClick={() => { setQuery(''); setApplied(''); }}>Clear</button>}</div>
      <p className="featureMeta">{indexSummary(index)}</p>
    </form>
    {/* Adding is for task notes; the chat filter lists only what the Assistant remembered. */}
    {!onlyChats && <section className="surface composerSurface"><div className="formGrid">
      <label className="field"><span>Task</span><select value={taskId} onChange={e => setTaskId(e.target.value)}><option value="">Select task</option>{tasks.map(t => <option value={t.id} key={t.id}>{t.title}</option>)}</select></label>
      <label className="field"><span>Memory</span><input value={content} onChange={e => setContent(e.target.value)} placeholder="Add durable context" maxLength={16000} /></label>
    </div><p className="featureMeta">Memories belong to a task; the API does not accept workspace-wide notes or edits.</p>
      <div className="actions"><button disabled={busy || !taskId || !content.trim()} onClick={() => void add()}><Brain />Add memory</button></div></section>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot />{error}</p>}
    {note && <p className="notice" role="status"><CircleDot />{note}</p>}
    {loading && <p className="notice"><CircleDot />Loading memory...</p>}
    {onlyChats && !loading && !error && chatCount > 0 && <div className="memoryBulk" role="group" aria-label="Delete memories from my chats">
      <p className="featureMeta">{applied ? 'Deleting removes every memory from your chats in this workspace, not only the ones matching your search.' : 'Only you can see these. Deleting them also removes them from search and from the Assistant\'s answers.'}</p>
      <div className="actions">{confirmAll
        ? <><button type="button" className="danger" disabled={busy} onClick={() => void removeAllChats()}>Delete all</button><button type="button" className="secondary" onClick={() => setConfirmAll(false)}>Cancel</button></>
        : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirmAll(true)}><Trash2 />Delete all from my chats</button>}</div>
    </div>}
    <section className="surface memoryTable" id="memory-list" aria-label={onlyChats ? 'Memories from my chats' : 'Memories'}>{items.map(x => {
      const idx = memoryIndexState(index, x.id), source = memorySource(x);
      return <div className="memoryRow" key={x.id}>
        <div><span className="typeIcon">{source.kind === 'chat' ? <MessageSquare /> : <FileText />}</span><div><strong>{source.label}</strong><p>{x.content}</p>
          {source.kind === 'chat' && <small className="memorySource">Private to you · from “{source.title}”</small>}</div></div>
        <span className={`featureChip ${idx.state}`} title={idx.error ?? undefined}>{idx.state.replace('_', ' ')}</span>
        {source.kind === 'chat'
          ? <button type="button" className="secondary memoryChatLink" onClick={() => onOpenConversation(source.conversationId)} aria-label={`Open chat ${source.title}`}><MessageSquare />Open chat</button>
          : <code>{source.kind === 'task' ? source.taskId : ''}</code>}
        {confirming === x.id
          ? <span className="featureConfirm"><button className="danger" disabled={busy} onClick={() => void remove(x.id)}>Delete</button><button className="secondary" onClick={() => setConfirming(null)}>Cancel</button></span>
          : <button className="iconButton" aria-label="Delete memory" onClick={() => setConfirming(x.id)}><Trash2 /></button>}
      </div>;
    })}</section>
    {!loading && !error && !items.length && <div className="emptyState"><Search /><h3>{applied ? 'No matching memories' : onlyChats ? 'No memories from your chats' : 'No memories'}</h3><p>{applied ? `Nothing matched “${applied}”.` : onlyChats ? 'With chat memory on, lasting facts you state in an Assistant chat are remembered here, visible only to you.' : 'Add context associated with a task, or tell the Assistant about yourself: facts you state in a chat are remembered here.'}</p>{onlyChats && !applied && <button type="button" className="secondary" onClick={() => showChats(false)}>Show all memories</button>}</div>}
  </>;
}
