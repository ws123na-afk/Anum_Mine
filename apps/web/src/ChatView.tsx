import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { ArrowDown, CircleDot, MessageSquarePlus, RefreshCw, RotateCcw, Send, Sparkles, Square, Trash2 } from 'lucide-react';
import { Answer } from './AnswerView';
import {
  applyAskEvent, canRetry, conversationTitle, endAsk, fromStored, isNearBottom, retryTarget, startAsk,
  type Conversation, type UiTurn,
} from './lib/chat';
import { askInConversation, createConversation, deleteConversation, getConversation, listConversations, type AskBody } from './lib/chatApi';
import { ApiError, budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { parseAnswer } from './lib/tasks';
import { useDeveloperAssist } from './DeveloperAssist';
import './chat.css';

const SUGGESTIONS = ['What is waiting for my approval?', 'Draft a short status update for my team', 'Explain how approvals protect this workspace'];
const NO_PERMISSION = 'Your role cannot use the assistant in this workspace. Ask a workspace owner or admin to give you the member role.';

/** What went wrong, in words that say how to fix it. */
function explain(error: unknown, action: string): string {
  if (isPermissionDenied(error)) return NO_PERMISSION;
  return `${action}: ${describeError(error)}`;
}

/**
 * The Assistant: a streamed chat that remembers the conversation (docs/conversations.md).
 * Replies render through the safe answer renderer (text nodes only); Stop aborts the request,
 * which stops the model on the server.
 */
export function ChatView({ onOpenBudgets, onOpenDeveloper, initialConversationId = null }: { onOpenBudgets: () => void; onOpenDeveloper?: () => void; initialConversationId?: string | null }) {
  // "build ..." / "developer, ..." goes to developer mode after a confirmation (docs/developer-mode.md).
  const developer = useDeveloperAssist({ source: 'text', onOpenDeveloper });
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<UiTurn[]>([]);
  const [draft, setDraft] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [listState, setListState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [listError, setListError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const controller = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLTextAreaElement | null>(null);
  const stick = useRef(true);

  const open = useCallback(async (id: string | null) => {
    controller.current?.abort();
    setActiveId(id);
    setNotice(null);
    setBudget(null);
    stick.current = true;
    setAtBottom(true);
    if (!id) { setTurns([]); return; }
    setOpening(true);
    try { setTurns(fromStored((await getConversation(id)).turns)); }
    catch (e) {
      setTurns([]);
      if (e instanceof ApiError && e.status === 404) {
        // A link from the Memory view to a chat that was deleted since: its memories stay.
        setActiveId(null);
        setNotice('That chat was deleted. Memories remembered from it stay in Memory until you delete them there.');
      } else setNotice(explain(e, 'This conversation could not be loaded'));
    }
    finally { setOpening(false); }
  }, []);

  const loadList = useCallback(async (openFirst: boolean, target: string | null = null) => {
    setListState('loading');
    setListError(null);
    try {
      const items = await listConversations();
      setConversations(items);
      setListState('ready');
      // Opened from the Memory view: that conversation, even when it is older than the list.
      if (target) await open(target);
      else if (openFirst && items[0]) await open(items[0].id);
    } catch (e) {
      setListState('error');
      setListError(explain(e, 'Conversations could not be loaded'));
    }
  }, [open]);

  useEffect(() => {
    void loadList(true, initialConversationId);
    return () => controller.current?.abort();
  }, [loadList, initialConversationId]);

  // Follow the reply only while the reader is at the bottom; scrolling up to read stops it.
  useLayoutEffect(() => {
    const box = log.current;
    if (box && stick.current) box.scrollTop = box.scrollHeight;
  }, [turns]);
  const onScroll = () => {
    const box = log.current;
    if (!box) return;
    stick.current = isNearBottom(box);
    setAtBottom(stick.current);
  };
  const jumpToLatest = () => {
    stick.current = true;
    setAtBottom(true);
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: 'smooth' });
  };

  async function refreshList() {
    try { setConversations(await listConversations()); } catch { /* the list keeps its last good state */ }
  }

  async function ask(body: AskBody) {
    if (streaming) return;
    setNotice(null);
    setBudget(null);
    stick.current = true;
    setAtBottom(true);
    setTurns((current) => startAsk(current, 'content' in body ? body.content : null));
    setStreaming(true);
    const abort = new AbortController();
    controller.current = abort;
    let ended = false;
    try {
      let id = activeId;
      if (!id) {
        const created = await createConversation();
        id = created.id;
        setActiveId(id);
        setConversations((current) => [created, ...current]);
      }
      await askInConversation(id, body, abort.signal, (event) => {
        if (event.event === 'done' || event.event === 'error') ended = true;
        const data = event.data as { code?: string; message?: string } | null;
        if (event.event === 'error' && data?.code === 'model_budget_exceeded') setBudget(data.message ?? 'Monthly model budget reached.');
        setTurns((current) => applyAskEvent(current, event));
      });
      if (!ended) setTurns((current) => endAsk(current, { stopped: false }));
    } catch (e) {
      if (abort.signal.aborted) setTurns((current) => endAsk(current, { stopped: true }));
      else if (isBudgetExceeded(e)) {
        setBudget(budgetMessage(e));
        setTurns((current) => endAsk(current, { stopped: false, error: 'Not sent: the monthly model budget is used up.', errorCode: 'model_budget_exceeded' }));
      } else setTurns((current) => endAsk(current, { stopped: false, error: explain(e, 'Not answered') }));
    } finally {
      if (controller.current === abort) controller.current = null;
      setStreaming(false);
      void refreshList();
      input.current?.focus();
    }
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || streaming) return;
    setDraft('');
    void developer.intercept(content).then((handled) => { if (handled === null) void ask({ content }); });
  }
  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); }
  }
  function retry() {
    const target = retryTarget(turns);
    if (target) void ask({ retry_turn_id: target.id });
  }
  async function remove(conversation: Conversation) {
    if (!confirm(`Delete "${conversationTitle(conversation)}" and all its messages?`)) return;
    try {
      await deleteConversation(conversation.id);
      const rest = conversations.filter((c) => c.id !== conversation.id);
      setConversations(rest);
      if (activeId === conversation.id) await open(rest[0]?.id ?? null);
    } catch (e) { setNotice(explain(e, 'The conversation could not be deleted')); }
  }
  function startNew() {
    void open(null);
    input.current?.focus();
  }

  const active = conversations.find((c) => c.id === activeId) ?? null;
  const showEmpty = turns.length === 0 && !opening && listState !== 'loading';
  return <div className="chatLayout">
    <aside className="surface chatList" aria-label="Conversations">
      <div className="chatListHeader">
        <h2>Conversations</h2>
        <button type="button" className="secondary" onClick={startNew} disabled={streaming}><MessageSquarePlus aria-hidden="true" />New</button>
      </div>
      {listState === 'loading' && <p className="chatListState" role="status">Loading conversations...</p>}
      {listState === 'error' && <div className="chatListState" role="alert"><p>{listError}</p><button type="button" className="secondary" onClick={() => void loadList(!activeId)}><RefreshCw aria-hidden="true" />Try again</button></div>}
      {listState === 'ready' && conversations.length === 0 && <p className="chatListState">No conversations yet. Ask a question to start one.</p>}
      {conversations.length > 0 && <ul>{conversations.map((c) => <li key={c.id} className={c.id === activeId ? 'active' : ''}>
        <button type="button" className="chatListItem" aria-current={c.id === activeId ? 'true' : undefined} onClick={() => void open(c.id)} disabled={streaming && c.id !== activeId}>
          <span>{conversationTitle(c)}</span><time dateTime={c.updated_at}>{when(c.updated_at)}</time>
        </button>
        {c.id === activeId && <button type="button" className="iconButton chatDelete" title="Delete conversation" aria-label={`Delete ${conversationTitle(c)}`} onClick={() => void remove(c)} disabled={streaming}><Trash2 aria-hidden="true" /></button>}
      </li>)}</ul>}
    </aside>
    <section className="surface chatPanel" aria-label="Assistant chat">
      <header className="chatHeader"><Sparkles aria-hidden="true" /><h2>{active ? conversationTitle(active) : 'New conversation'}</h2>{streaming && <span className="statusBadge warning">answering</span>}</header>
      <div className="chatLog" ref={log} onScroll={onScroll} role="log" aria-live="polite" aria-busy={streaming || opening} tabIndex={0} aria-label="Messages">
        {(opening || (listState === 'loading' && turns.length === 0)) && <p className="chatLoading" role="status">Loading messages...</p>}
        {showEmpty && <div className="chatEmpty">
          <p>Ask anything. Answers stream in as they are written, and follow-ups remember this conversation. To have something done, ask for a task: tasks run under this workspace's approvals.</p>
          <div className="chatSuggestions">{SUGGESTIONS.map((s) => <button type="button" className="secondary" key={s} onClick={() => void ask({ content: s })} disabled={streaming}>{s}</button>)}</div>
        </div>}
        {turns.map((turn) => <article key={turn.id} className={`chatTurn ${turn.role} ${turn.status}`} aria-label={turn.role === 'user' ? 'You' : 'Assistant'}>
          {turn.role === 'user'
            ? <><p className="chatQuestion">{turn.content}</p>{turn.status === 'failed' && <p className="chatMeta chatError">Not sent.</p>}</>
            : <>
              {turn.content ? <Answer blocks={parseAnswer(turn.content)} /> : turn.status === 'streaming' ? <p className="chatThinking" role="status" aria-label="Thinking"><span /><span /><span /></p> : null}
              {turn.status === 'streaming' && turn.content && <span className="chatCaret" aria-hidden="true" />}
              {turn.status === 'cancelled' && <p className="chatMeta">Stopped{turn.content ? '' : ' before any answer'}.</p>}
              {turn.status === 'failed' && <p className="chatMeta chatError">{turn.error ?? 'No answer.'}</p>}
            </>}
        </article>)}
      </div>
      {!atBottom && <button type="button" className="secondary chatJump" onClick={jumpToLatest}><ArrowDown aria-hidden="true" />Jump to latest</button>}
      {developer.panel}
      {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{budget}</span><button type="button" className="secondary" onClick={onOpenBudgets}>Open Budgets</button></p>}
      {notice && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{notice}</span></p>}
      <form className="chatComposer" onSubmit={submit}>
        <textarea ref={input} value={draft} rows={1} placeholder="Ask the assistant..." aria-label="Message the assistant" onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} maxLength={8000} />
        <div className="chatActions">
          {!streaming && canRetry(turns) && <button type="button" className="secondary" onClick={retry}><RotateCcw aria-hidden="true" />Retry</button>}
          {streaming
            ? <button type="button" className="chatStop" onClick={() => controller.current?.abort()}><Square aria-hidden="true" />Stop</button>
            : <button type="submit" className="chatSend" disabled={!draft.trim()}><Send aria-hidden="true" />Send</button>}
        </div>
      </form>
    </section>
  </div>;
}

function when(value: string): string {
  const date = new Date(value);
  return date.toDateString() === new Date().toDateString()
    ? new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(date)
    : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}
