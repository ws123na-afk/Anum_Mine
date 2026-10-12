import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import {
  ArrowDown, CircleDot, Download, FileText, MessageSquarePlus, Pencil, Pin, PinOff, RefreshCw, RotateCcw, Search, Send, Sparkles, Square, Trash2, WifiOff, X,
} from 'lucide-react';
import { AnswerFeedback, type Rating } from './AnswerFeedback';
import { getRatings } from './lib/learningApi';
import {
  applyAskEvent, canRegenerate, canRetry, conversationMarkdown, conversationTitle, deletionSpan, endAsk, exportFileName, fromStored, isNearBottom, isStored,
  laterTurns, nextCursor, orderConversations, retryTarget, startAsk, type Conversation, type UiTurn,
} from './lib/chat';
import {
  askInConversation, CONVERSATION_PAGE, createConversation, deleteConversation, deleteTurn, getConversation, listConversations, rateTurn, updateConversation, type AskBody,
} from './lib/chatApi';
import { ApiError, budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { markdownToPlainText } from './lib/markdown';
import { copyText, downloadText } from './lib/clipboard';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { useDeveloperAssist } from './DeveloperAssist';
import { FavoritesStrip, StarButton, useStars } from './Favorites';
import { ReportAnswerCard } from './ReportAnswerCard';
import { visibleReply } from './lib/library';
import type { Favorite } from '@anum/contracts';
import { ChatCardView, type ChatTarget } from './ChatCardView';
import { ConfirmDialog } from './Dialog';
import { MessageMarkdown } from './MessageMarkdown';
import { ActionMenu, CopyAction, MessageActions, useToast, type MenuItem } from './MessageActions';
import './chat.css';

const SUGGESTIONS: MessageKey[] = ['chat.suggestion.1', 'chat.suggestion.2', 'chat.suggestion.3'];
/** The API's limit on one question (anum_api/conversations.py MAX_QUESTION_CHARS). */
export const MAX_QUESTION_CHARS = 8000;
/** The character count shows once this few are left. */
const COUNT_FROM = 1000;

/** What went wrong, in words that say how to fix it. `action` names the failed step and takes the reason. */
function explain(error: unknown, action: MessageKey): string {
  if (isPermissionDenied(error)) return t('chat.noPermission');
  return t(action, { reason: describeError(error) });
}

type Pending =
  | { kind: 'deleteConversation'; conversation: Conversation }
  | { kind: 'deleteTurn'; turn: UiTurn; count: number }
  | { kind: 'rename'; conversation: Conversation; title: string };

function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine !== false));
  useEffect(() => {
    const update = () => setOnline(navigator.onLine !== false);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update); };
  }, []);
  return online;
}

/**
 * The Assistant: a streamed chat that remembers the conversation (docs/conversations.md).
 * Replies render through the safe Markdown renderer (text nodes only); Stop aborts the
 * request, which stops the model on the server. Every message has its actions: copy,
 * regenerate, edit and resend, rate, delete (docs/conversations.md#message-actions).
 */
export function ChatView({ onOpenBudgets, onOpenDeveloper, onOpenTarget, initialConversationId = null, onOpenReport, onOpenFavorite, onOpenFavorites, agent }: {
  onOpenBudgets: () => void; onOpenDeveloper?: () => void; initialConversationId?: string | null;
  /** Links on task cards (docs/conversations.md#tools-in-chat). */
  onOpenTarget?: (target: ChatTarget) => void;
  /** "View Full Report" on a structured answer (docs/reports.md). */
  onOpenReport?: (reportId: string) => void;
  /** Quick access to starred items on the empty Assistant home (docs/knowledge-base.md#favorites). */
  onOpenFavorite?: (favorite: Favorite) => void; onOpenFavorites?: () => void;
  /** A thread with this agent (docs/agents.md); its persona answers. */
  agent?: { id: string; name: string };
}) {
  const stars = useStars('conversation');
  // With `agent`: the threads with that agent (docs/agents.md); its persona answers.
  const agentId = agent?.id;
  // "build ..." / "developer, ..." goes to developer mode after a confirmation (docs/developer-mode.md).
  const developer = useDeveloperAssist({ source: 'text', onOpenDeveloper });
  const online = useOnline();
  const { toast, say } = useToast();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<{ before_updated_at: string; before_id: string } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [activeId, setActiveId] = useState<string | null>(null);
  // The open conversation as the API returned it: shown even when a search hides it from the list.
  const [openMeta, setOpenMeta] = useState<Conversation | null>(null);
  const [turns, setTurns] = useState<UiTurn[]>([]);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [listState, setListState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [listError, setListError] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  // The caller's rating of each answer: stored on the turn; older ratings from learning fill the gaps.
  const [ratings, setRatings] = useState<Record<string, Rating>>({});
  const controller = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLTextAreaElement | null>(null);
  const stick = useRef(true);
  // Set once the person asks something: the first list load then no longer opens the newest
  // conversation over their question (that would abort the reply they are waiting for).
  const acted = useRef(false);
  // Each open (and each ask) takes a new token; a conversation that finishes loading after a
  // later open or ask began never replaces what is on screen.
  const openToken = useRef(0);

  const open = useCallback(async (id: string | null) => {
    controller.current?.abort();
    setActiveId(id);
    setNotice(null);
    setBudget(null);
    setEditing(null);
    stick.current = true;
    setAtBottom(true);
    if (!id) { setTurns([]); setOpenMeta(null); return; }
    setOpening(true);
    const token = ++openToken.current;
    try {
      const detail = await getConversation(id);
      if (token !== openToken.current) return;
      const stored = fromStored(detail.turns, detail.reports);
      setTurns(stored);
      setOpenMeta({ id: detail.id, title: detail.title, created_at: detail.created_at, updated_at: detail.updated_at, agent_id: detail.agent_id, pinned: detail.pinned, pinned_at: detail.pinned_at });
      const own = Object.fromEntries(stored.filter((turn) => turn.feedback).map((turn) => [turn.id, turn.feedback as Rating]));
      setRatings(own);
      const unrated = stored.filter((turn) => turn.role === 'assistant' && turn.status === 'complete' && !turn.feedback).map((turn) => turn.id);
      // Ratings given before they were stored on the answer live in learning only.
      if (unrated.length) void getRatings('chat', unrated.slice(-100)).then((r) => setRatings((current) => ({ ...Object.fromEntries(r.ratings.map((x) => [x.subject_id, x.rating])), ...current }))).catch(() => undefined);
    }
    catch (e) {
      if (token !== openToken.current) return;
      setTurns([]);
      setOpenMeta(null);
      if (e instanceof ApiError && e.status === 404) {
        // A link from the Memory view to a chat that was deleted since: its memories stay.
        setActiveId(null);
        setNotice(t('chat.chatDeleted'));
      } else setNotice(explain(e, 'chat.loadConversationFailed'));
    }
    finally { if (token === openToken.current) setOpening(false); }
  }, []);

  const loadList = useCallback(async (openFirst: boolean, target: string | null = null, q = '') => {
    setListState('loading');
    setListError(null);
    try {
      const items = await listConversations({ agent_id: agentId, q: q || undefined, limit: CONVERSATION_PAGE });
      setConversations(orderConversations(items));
      setCursor(nextCursor(items, CONVERSATION_PAGE));
      setListState('ready');
      // Opened from the Memory view: that conversation, even when it is older than the list.
      if (target) await open(target);
      else if (openFirst && items[0] && !acted.current) await open(items[0].id);
    } catch (e) {
      setListState('error');
      setListError(explain(e, 'chat.loadListFailed'));
    }
  }, [open, agentId]);

  useEffect(() => {
    void loadList(true, initialConversationId);
    return () => controller.current?.abort();
  }, [loadList, initialConversationId]);

  // Search as the person types, once they pause (the server matches titles and messages).
  useEffect(() => {
    const next = search.trim();
    if (next === query) return;
    const timer = window.setTimeout(() => { setQuery(next); void loadList(false, null, next); }, 300);
    return () => window.clearTimeout(timer);
  }, [search, query, loadList]);

  async function loadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listConversations({ agent_id: agentId, q: query || undefined, limit: CONVERSATION_PAGE, ...cursor });
      setConversations((current) => orderConversations([...current, ...page]));
      setCursor(nextCursor(page, CONVERSATION_PAGE));
    } catch (e) { setNotice(explain(e, 'chat.loadListFailed')); }
    finally { setLoadingMore(false); }
  }

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

  /** The first page again, merged into what is loaded (later pages stay). */
  async function refreshList() {
    try {
      const page = await listConversations({ agent_id: agentId, q: query || undefined, limit: CONVERSATION_PAGE });
      setConversations((current) => orderConversations([...page, ...current.filter((c) => !page.some((p) => p.id === c.id))]));
    } catch { /* the list keeps its last good state */ }
  }

  async function ask(body: AskBody) {
    if (streaming) return;
    acted.current = true;
    // A conversation still loading would replace this question when it arrives: keep the question.
    openToken.current++;
    setOpening(false);
    if (!online) { setNotice(t('chat.offline')); return; }
    setNotice(null);
    setBudget(null);
    setEditing(null);
    stick.current = true;
    setAtBottom(true);
    setTurns((current) => startAsk(current, 'content' in body ? body.content : null, 'edit_turn_id' in body ? body.edit_turn_id : undefined));
    setStreaming(true);
    const abort = new AbortController();
    controller.current = abort;
    let ended = false;
    try {
      let id = activeId;
      if (!id) {
        const created = await createConversation(agentId);
        id = created.id;
        setActiveId(id);
        setOpenMeta(created);
        setConversations((current) => orderConversations([created, ...current]));
      }
      await askInConversation(id, body, abort.signal, (event) => {
        if (event.event === 'done' || event.event === 'error') ended = true;
        const data = event.data as { code?: string; message?: string } | null;
        if (event.event === 'error' && data?.code === 'model_budget_exceeded') setBudget(data.message ?? t('chat.budgetReached'));
        setTurns((current) => applyAskEvent(current, event));
      });
      if (!ended) setTurns((current) => endAsk(current, { stopped: false }));
    } catch (e) {
      if (abort.signal.aborted) setTurns((current) => endAsk(current, { stopped: true }));
      else if (isBudgetExceeded(e)) {
        setBudget(budgetMessage(e));
        setTurns((current) => endAsk(current, { stopped: false, error: t('chat.notSentBudget'), errorCode: 'model_budget_exceeded' }));
      } else setTurns((current) => endAsk(current, { stopped: false, error: explain(e, 'chat.notAnswered') }));
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
    if (!content || streaming || content.length > MAX_QUESTION_CHARS) return;
    if (!online) { setNotice(t('chat.offline')); return; }
    setDraft('');
    void developer.intercept(content).then((handled) => { if (handled === null) void ask({ content }); });
  }
  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); }
    else if (event.key === 'Escape' && streaming) { event.preventDefault(); controller.current?.abort(); }
  }
  function retry() {
    const target = retryTarget(turns);
    if (target) void ask({ retry_turn_id: target.id });
  }
  function sendEdit(event?: FormEvent) {
    event?.preventDefault();
    if (!editing) return;
    const content = editing.text.trim();
    if (!content || content.length > MAX_QUESTION_CHARS) return;
    void ask({ edit_turn_id: editing.id, content });
  }

  async function confirmPending() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.kind === 'deleteConversation') {
        const { conversation } = pending;
        await deleteConversation(conversation.id);
        const rest = conversations.filter((c) => c.id !== conversation.id);
        setConversations(rest);
        if (activeId === conversation.id) await open(rest[0]?.id ?? null);
        say(t('chat.deletedConversation'));
      } else if (pending.kind === 'deleteTurn') {
        if (!activeId) return;
        const doomed = new Set(deletionSpan(turns, pending.turn.id));
        await deleteTurn(activeId, pending.turn.id);
        setTurns((current) => current.filter((turn) => !doomed.has(turn.id)));
        say(t('message.deleted'));
        input.current?.focus();
      } else {
        const title = pending.title.trim();
        if (!title) return;
        const updated = await updateConversation(pending.conversation.id, { title });
        applyUpdate(updated);
        say(t('chat.renamed'));
      }
      setPending(null);
    } catch (e) {
      setPending(null);
      setNotice(explain(e, pending.kind === 'deleteConversation' ? 'chat.deleteFailed' : pending.kind === 'deleteTurn' ? 'message.deleteFailed' : 'chat.renameFailed'));
    } finally { setBusy(false); }
  }

  function applyUpdate(updated: Conversation) {
    setConversations((current) => orderConversations(current.map((c) => (c.id === updated.id ? updated : c))));
    if (updated.id === activeId) setOpenMeta(updated);
  }

  async function togglePin(conversation: Conversation) {
    try {
      const updated = await updateConversation(conversation.id, { pinned: !conversation.pinned });
      applyUpdate(updated);
      say(updated.pinned ? t('chat.pinnedDone') : t('chat.unpinnedDone'));
    } catch (e) { setNotice(explain(e, 'chat.pinFailed')); }
  }

  async function rate(turnId: string, rating: Rating | null, reason?: string) {
    if (!activeId) throw new Error(t('common.operationFailed'));
    const result = await rateTurn(activeId, turnId, rating, reason);
    setRatings((current) => {
      const next = { ...current };
      if (rating) next[turnId] = rating; else delete next[turnId];
      return next;
    });
    return result;
  }

  const copied = (ok: boolean) => say(ok ? t('message.copied') : t('message.copyFailed'));
  function startNew() {
    void open(null);
    input.current?.focus();
  }

  const active = conversations.find((c) => c.id === activeId) ?? (openMeta?.id === activeId ? openMeta : null);
  const title = active ? conversationTitle(active) : t('chat.newConversation');
  const exportable = turns.some((turn) => turn.content.trim() && isStored(turn));
  const markdown = () => conversationMarkdown(title, turns.filter(isStored), { you: t('chat.you'), assistant: t('chat.assistant') }, formatDateTime(new Date()));
  const conversationMenu: MenuItem[] = active ? [
    { id: 'rename', label: t('chat.rename'), icon: <Pencil aria-hidden="true" />, disabled: streaming, onSelect: () => setPending({ kind: 'rename', conversation: active, title: active.title }) },
    { id: 'pin', label: active.pinned ? t('chat.unpin') : t('chat.pin'), icon: active.pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />, onSelect: () => void togglePin(active) },
    { id: 'copy', label: t('chat.copyConversation'), icon: <FileText aria-hidden="true" />, disabled: !exportable, onSelect: () => void copyText(markdown()).then(copied) },
    { id: 'download', label: t('chat.downloadConversation'), icon: <Download aria-hidden="true" />, disabled: !exportable, onSelect: () => { downloadText(exportFileName(title), markdown()); say(t('chat.downloaded')); } },
    { id: 'delete', label: t('chat.deleteConversation'), icon: <Trash2 aria-hidden="true" />, danger: true, disabled: streaming, onSelect: () => setPending({ kind: 'deleteConversation', conversation: active }) },
  ] : [];
  const showEmpty = turns.length === 0 && !opening && listState !== 'loading';
  const pinned = conversations.filter((c) => c.pinned);
  const recent = conversations.filter((c) => !c.pinned);
  const left = MAX_QUESTION_CHARS - draft.length;

  const row = (c: Conversation) => <li key={c.id} className={c.id === activeId ? 'active' : ''}>
    <button type="button" className="chatListItem" aria-current={c.id === activeId ? 'true' : undefined} onClick={() => void open(c.id)} disabled={streaming && c.id !== activeId}>
      <span dir="auto">{c.pinned && <Pin className="chatPinMark" aria-hidden="true" />}{conversationTitle(c)}</span><time dateTime={c.updated_at}>{when(c.updated_at)}</time>
    </button>
    <ActionMenu className="iconButton chatRowMenu" label={t('chat.actionsFor', { title: conversationTitle(c) })} items={[
      { id: 'rename', label: t('chat.rename'), icon: <Pencil aria-hidden="true" />, disabled: streaming, onSelect: () => setPending({ kind: 'rename', conversation: c, title: c.title }) },
      { id: 'pin', label: c.pinned ? t('chat.unpin') : t('chat.pin'), icon: c.pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />, onSelect: () => void togglePin(c) },
      { id: 'delete', label: t('chat.deleteConversation'), icon: <Trash2 aria-hidden="true" />, danger: true, disabled: streaming, onSelect: () => setPending({ kind: 'deleteConversation', conversation: c }) },
    ]} />
  </li>;

  return <div className="chatLayout">
    <aside className="surface chatList" aria-labelledby="chat-list-heading">
      <div className="chatListHeader">
        <h2 id="chat-list-heading">{t('chat.conversations')}</h2>
        <button type="button" className="secondary" onClick={startNew} disabled={streaming}><MessageSquarePlus aria-hidden="true" />{t('chat.new')}</button>
      </div>
      <div className="chatSearch" role="search">
        <Search aria-hidden="true" />
        <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('chat.searchPlaceholder')} aria-label={t('chat.searchLabel')} maxLength={200} dir="auto" />
        {search && <button type="button" className="chatSearchClear" aria-label={t('chat.clearSearch')} title={t('chat.clearSearch')} onClick={() => setSearch('')}><X aria-hidden="true" /></button>}
      </div>
      {listState === 'loading' && <p className="chatListState" role="status">{query ? t('chat.searching') : t('chat.loadingList')}</p>}
      {listState === 'error' && <div className="chatListState" role="alert"><p>{listError}</p><button type="button" className="secondary" onClick={() => void loadList(!activeId, null, query)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
      {listState === 'ready' && conversations.length === 0 && (query
        ? <div className="chatListState"><p>{t('chat.noMatches', { query })}</p><button type="button" className="secondary" onClick={() => setSearch('')}>{t('chat.clearSearch')}</button></div>
        : <p className="chatListState">{t('chat.emptyList')}</p>)}
      {conversations.length > 0 && <div className="chatListScroll">
        {pinned.length > 0 && <><h3 className="chatListGroup">{t('chat.pinnedGroup')}</h3><ul aria-label={t('chat.pinnedGroup')}>{pinned.map(row)}</ul></>}
        {recent.length > 0 && <>{pinned.length > 0 && <h3 className="chatListGroup">{t('chat.recentGroup')}</h3>}<ul aria-label={pinned.length > 0 ? t('chat.recentGroup') : t('chat.conversations')}>{recent.map(row)}</ul></>}
        {cursor && <button type="button" className="secondary chatMore" onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? t('chat.loadingMore') : t('chat.loadMore')}</button>}
      </div>}
    </aside>
    <section className="surface chatPanel" aria-label={t('chat.panel')}>
      <header className="chatHeader">
        <Sparkles aria-hidden="true" /><h2 dir="auto">{active?.pinned && <><Pin className="chatPinMark" aria-hidden="true" /><span className="visuallyHidden">{t('chat.pinnedMark')}: </span></>}{title}</h2>
        {streaming && <span className="statusBadge warning">{t('chat.answering')}</span>}
        {active && <StarButton starred={stars.ids.has(active.id)} label={conversationTitle(active)} onToggle={() => void stars.toggle(active.id)} />}
        {active && <ActionMenu className="iconButton" label={t('chat.conversationActions')} items={conversationMenu} />}
      </header>
      <div className="chatLog" ref={log} onScroll={onScroll} role="log" aria-live="polite" aria-busy={streaming || opening} tabIndex={0} aria-label={t('chat.messages')}>
        {(opening || (listState === 'loading' && turns.length === 0 && !query)) && <p className="chatLoading" role="status">{t('chat.loadingMessages')}</p>}
        {showEmpty && <div className="chatEmpty">
          <p>{agent ? t('agents.chat.intro', { name: agent.name }) : t('chat.intro')}</p>
          {!agent && onOpenFavorite && onOpenFavorites && <FavoritesStrip onOpen={onOpenFavorite} onOpenAll={onOpenFavorites} />}
          <div className="chatSuggestions">{SUGGESTIONS.map((key) => <button type="button" className="secondary" key={key} onClick={() => void ask({ content: t(key) })} disabled={streaming || !online}>{t(key)}</button>)}</div>
        </div>}
        {turns.map((turn) => {
          const stored = isStored(turn);
          const reply = visibleReply(turn.content);
          const showsText = Boolean(reply) && (!turn.card || turn.card.state === 'results');
          if (turn.role === 'user') {
            const isEditing = editing?.id === turn.id;
            const later = laterTurns(turns, turn.id);
            return <article key={turn.id} className={`chatTurn user ${turn.status}${isEditing ? ' editing' : ''}`} aria-label={t('chat.you')}>
              {isEditing
                ? <form className="chatEdit" onSubmit={sendEdit}>
                  <label className="visuallyHidden" htmlFor={`edit-${turn.id}`}>{t('message.editLabel')}</label>
                  <textarea id={`edit-${turn.id}`} value={editing.text} dir="auto" maxLength={MAX_QUESTION_CHARS} autoFocus
                    onChange={(e) => setEditing({ id: turn.id, text: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') { e.preventDefault(); setEditing(null); }
                      else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendEdit(); }
                    }} />
                  {later > 0 && <p className="chatMeta">{t('message.editReplaces', { count: later })}</p>}
                  <div className="chatEditActions">
                    <button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button>
                    <button type="submit" className="chatSend" disabled={!editing.text.trim() || streaming || !online}><Send aria-hidden="true" />{t('message.saveAndSend')}</button>
                  </div>
                </form>
                : <p className="chatQuestion" dir="auto">{turn.content}</p>}
              {turn.status === 'failed' && <p className="chatMeta chatError">{t('chat.notSent')}</p>}
              {!isEditing && turn.status !== 'sending' && <MessageActions label={t('message.actionsYou')}>
                <CopyAction text={turn.content} label={t('message.copyQuestion')} onCopied={copied} />
                {stored && <button type="button" className="msgAction" aria-label={t('message.edit')} title={t('message.edit')} disabled={streaming} onClick={() => setEditing({ id: turn.id, text: turn.content })}><Pencil aria-hidden="true" /></button>}
                {stored && <button type="button" className="msgAction" aria-label={t('message.deleteQuestion')} title={t('message.deleteQuestion')} disabled={streaming} onClick={() => setPending({ kind: 'deleteTurn', turn, count: deletionSpan(turns, turn.id).length })}><Trash2 aria-hidden="true" /></button>}
              </MessageActions>}
            </article>;
          }
          return <article key={turn.id} className={`chatTurn assistant ${turn.status}`} aria-label={t('chat.assistant')}>
            {/* A card's own words are localized; only a results card shows the written answer above it. */}
            {showsText ? <MessageMarkdown text={reply} onCopied={copied} /> : turn.status === 'streaming' && !turn.card ? <p className="chatThinking" role="status" aria-label={t('chat.thinking')}><span /><span /><span /></p> : null}
            {turn.card && <ChatCardView card={turn.card} live={turn.status === 'streaming'} onOpen={onOpenTarget} onChange={(card) => setTurns((current) => current.map((x) => x.id === turn.id ? { ...x, card } : x))} />}
            {turn.status === 'streaming' && turn.content && (!turn.card || turn.card.state === 'results') && <span className="chatCaret" aria-hidden="true" />}
            {turn.report && <ReportAnswerCard card={turn.report} onOpen={onOpenReport} />}
            {turn.status === 'cancelled' && <p className="chatMeta">{turn.content ? t('chat.stopped') : t('chat.stoppedEarly')}</p>}
            {turn.status === 'failed' && <p className="chatMeta chatError" dir="auto">{turn.error ?? t('chat.noAnswer')}</p>}
            {turn.status !== 'streaming' && (showsText || stored) && <MessageActions label={t('message.actionsAnswer')}>
              {showsText && <CopyAction text={reply} label={t('message.copyAnswer')} onCopied={copied} />}
              {showsText && <ActionMenu label={t('message.moreCopy')} items={[
                { id: 'markdown', label: t('message.copyMarkdown'), onSelect: () => void copyText(reply).then(copied) },
                { id: 'text', label: t('message.copyText'), onSelect: () => void copyText(markdownToPlainText(reply)).then(copied) },
              ]} />}
              {!streaming && canRegenerate(turns, turn) && <button type="button" className="msgAction" aria-label={t('message.regenerate')} title={t('message.regenerate')} disabled={!online} onClick={retry}><RotateCcw aria-hidden="true" /></button>}
              {turn.status === 'complete' && showsText && stored && !streaming && <AnswerFeedback turnId={turn.id} content={turn.content} rating={ratings[turn.id] ?? null} onRate={(rating, reason) => rate(turn.id, rating, reason)} />}
              {stored && <button type="button" className="msgAction" aria-label={t('message.deleteAnswer')} title={t('message.deleteAnswer')} disabled={streaming} onClick={() => setPending({ kind: 'deleteTurn', turn, count: 1 })}><Trash2 aria-hidden="true" /></button>}
            </MessageActions>}
          </article>;
        })}
      </div>
      {!atBottom && <button type="button" className="secondary chatJump" onClick={jumpToLatest}><ArrowDown aria-hidden="true" />{t('chat.jump')}</button>}
      {developer.panel}
      {!online && <p className="notice" role="status"><WifiOff aria-hidden="true" /><span>{t('chat.offline')}</span></p>}
      {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{budget}</span><button type="button" className="secondary" onClick={onOpenBudgets}>{t('chat.openBudgets')}</button></p>}
      {stars.error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{stars.error}</span></p>}
      {notice && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{notice}</span><button type="button" className="iconButton small" aria-label={t('chat.dismiss')} title={t('chat.dismiss')} onClick={() => setNotice(null)}><X aria-hidden="true" /></button></p>}
      <form className="chatComposer" onSubmit={submit}>
        <div className="chatInput">
          <textarea ref={input} value={draft} rows={1} placeholder={t('chat.placeholder')} aria-label={t('chat.inputLabel')} aria-describedby="chat-composer-hint" onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} maxLength={MAX_QUESTION_CHARS} dir="auto" />
          <p id="chat-composer-hint" className={`chatHint${left < 0 ? ' over' : ''}`}>
            <span className="chatKeys">{t('chat.keysHint')}</span>
            {left <= COUNT_FROM && <span aria-live="polite">{t('chat.charsLeft', { count: Math.max(0, left) })}</span>}
          </p>
        </div>
        <div className="chatActions">
          {!streaming && canRetry(turns) && <button type="button" className="secondary" onClick={retry} disabled={!online}><RotateCcw aria-hidden="true" />{t('chat.retry')}</button>}
          {streaming
            ? <button type="button" className="chatStop" onClick={() => controller.current?.abort()}><Square aria-hidden="true" />{t('chat.stop')}</button>
            : <button type="submit" className="chatSend" disabled={!draft.trim() || !online}><Send aria-hidden="true" />{t('chat.send')}</button>}
        </div>
      </form>
      {toast}
    </section>
    {pending?.kind === 'deleteConversation' && <ConfirmDialog title={t('chat.deleteTitle')} confirmLabel={t('chat.deleteConfirm')} danger busy={busy} onConfirm={() => void confirmPending()} onCancel={() => setPending(null)}>
      <p dir="auto">{t('chat.confirmDelete', { title: conversationTitle(pending.conversation) })}</p>
    </ConfirmDialog>}
    {pending?.kind === 'deleteTurn' && <ConfirmDialog title={t('message.deleteTitle')} confirmLabel={t('chat.deleteConfirm')} danger busy={busy} onConfirm={() => void confirmPending()} onCancel={() => setPending(null)}>
      <p>{pending.turn.role === 'user' ? t('message.deleteQuestionBody', { count: pending.count - 1 }) : t('message.deleteAnswerBody')}</p>
    </ConfirmDialog>}
    {pending?.kind === 'rename' && <ConfirmDialog title={t('chat.renameTitle')} confirmLabel={t('chat.renameConfirm')} busy={busy} confirmDisabled={!pending.title.trim()} onConfirm={() => void confirmPending()} onCancel={() => setPending(null)}>
      <label className="chatRenameLabel" htmlFor="chat-rename">{t('chat.renameLabel')}</label>
      <input id="chat-rename" data-autofocus value={pending.title} maxLength={80} dir="auto"
        onChange={(e) => setPending({ ...pending, title: e.target.value })}
        onKeyDown={(e) => { if (e.key === 'Enter' && pending.title.trim()) { e.preventDefault(); void confirmPending(); } }} />
    </ConfirmDialog>}
  </div>;
}

function when(value: string): string {
  const date = new Date(value);
  return date.toDateString() === new Date().toDateString()
    ? formatDateTime(date, { hour: '2-digit', minute: '2-digit' })
    : formatDateTime(date, { month: 'short', day: 'numeric' });
}
