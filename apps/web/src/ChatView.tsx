import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { ArrowDown, CircleDot, MessageSquarePlus, RefreshCw, RotateCcw, Send, Sparkles, Square, Trash2 } from 'lucide-react';
import { Answer } from './AnswerView';
import { AnswerFeedback, type Rating } from './AnswerFeedback';
import { getRatings } from './lib/learningApi';
import {
  applyAskEvent, canRetry, conversationTitle, endAsk, fromStored, isNearBottom, retryTarget, startAsk,
  type Conversation, type UiTurn,
} from './lib/chat';
import { askInConversation, createConversation, deleteConversation, getConversation, listConversations, type AskBody } from './lib/chatApi';
import { ApiError, budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { parseAnswer } from './lib/tasks';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { useDeveloperAssist } from './DeveloperAssist';
import { FavoritesStrip, StarButton, useStars } from './Favorites';
import { ReportAnswerCard } from './ReportAnswerCard';
import { visibleReply } from './lib/library';
import type { Favorite } from '@anum/contracts';
import { ChatCardView, type ChatTarget } from './ChatCardView';
import './chat.css';

const SUGGESTIONS: MessageKey[] = ['chat.suggestion.1', 'chat.suggestion.2', 'chat.suggestion.3'];

/** What went wrong, in words that say how to fix it. `action` names the failed step and takes the reason. */
function explain(error: unknown, action: MessageKey): string {
  if (isPermissionDenied(error)) return t('chat.noPermission');
  return t(action, { reason: describeError(error) });
}

/**
 * The Assistant: a streamed chat that remembers the conversation (docs/conversations.md).
 * Replies render through the safe answer renderer (text nodes only); Stop aborts the request,
 * which stops the model on the server.
 */
export function ChatView({ onOpenBudgets, onOpenDeveloper, onOpenTarget, initialConversationId = null, onOpenReport, onOpenFavorite, onOpenFavorites }: {
  onOpenBudgets: () => void; onOpenDeveloper?: () => void; initialConversationId?: string | null;
  /** Links on task cards (docs/conversations.md#tools-in-chat). */
  onOpenTarget?: (target: ChatTarget) => void;
  /** "View Full Report" on a structured answer (docs/reports.md). */
  onOpenReport?: (reportId: string) => void;
  /** Quick access to starred items on the empty Assistant home (docs/knowledge-base.md#favorites). */
  onOpenFavorite?: (favorite: Favorite) => void; onOpenFavorites?: () => void;
}) {
  const stars = useStars('conversation');
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
  // The caller's rating of each answer (docs/learning.md); unknown until the lookup answers.
  const [ratings, setRatings] = useState<Record<string, Rating>>({});
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
    try {
      const detail = await getConversation(id);
      const stored = fromStored(detail.turns, detail.reports);
      setTurns(stored);
      const answers = stored.filter((turn) => turn.role === 'assistant' && turn.status === 'complete').map((turn) => turn.id);
      setRatings({});
      // Thumbs show their saved state; without it (an older server, learning data unreadable) they still work.
      if (answers.length) void getRatings('chat', answers.slice(-100)).then((r) => setRatings(Object.fromEntries(r.ratings.map((x) => [x.subject_id, x.rating])))).catch(() => undefined);
    }
    catch (e) {
      setTurns([]);
      if (e instanceof ApiError && e.status === 404) {
        // A link from the Memory view to a chat that was deleted since: its memories stay.
        setActiveId(null);
        setNotice(t('chat.chatDeleted'));
      } else setNotice(explain(e, 'chat.loadConversationFailed'));
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
      setListError(explain(e, 'chat.loadListFailed'));
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
    if (!confirm(t('chat.confirmDelete', { title: conversationTitle(conversation) }))) return;
    try {
      await deleteConversation(conversation.id);
      const rest = conversations.filter((c) => c.id !== conversation.id);
      setConversations(rest);
      if (activeId === conversation.id) await open(rest[0]?.id ?? null);
    } catch (e) { setNotice(explain(e, 'chat.deleteFailed')); }
  }
  function startNew() {
    void open(null);
    input.current?.focus();
  }

  const active = conversations.find((c) => c.id === activeId) ?? null;
  const showEmpty = turns.length === 0 && !opening && listState !== 'loading';
  return <div className="chatLayout">
    <aside className="surface chatList" aria-labelledby="chat-list-heading">
      <div className="chatListHeader">
        <h2 id="chat-list-heading">{t('chat.conversations')}</h2>
        <button type="button" className="secondary" onClick={startNew} disabled={streaming}><MessageSquarePlus aria-hidden="true" />{t('chat.new')}</button>
      </div>
      {listState === 'loading' && <p className="chatListState" role="status">{t('chat.loadingList')}</p>}
      {listState === 'error' && <div className="chatListState" role="alert"><p>{listError}</p><button type="button" className="secondary" onClick={() => void loadList(!activeId)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
      {listState === 'ready' && conversations.length === 0 && <p className="chatListState">{t('chat.emptyList')}</p>}
      {conversations.length > 0 && <ul>{conversations.map((c) => <li key={c.id} className={c.id === activeId ? 'active' : ''}>
        <button type="button" className="chatListItem" aria-current={c.id === activeId ? 'true' : undefined} onClick={() => void open(c.id)} disabled={streaming && c.id !== activeId}>
          <span dir="auto">{conversationTitle(c)}</span><time dateTime={c.updated_at}>{when(c.updated_at)}</time>
        </button>
        {c.id === activeId && <button type="button" className="iconButton chatDelete" title={t('chat.deleteConversation')} aria-label={t('chat.deleteNamed', { title: conversationTitle(c) })} onClick={() => void remove(c)} disabled={streaming}><Trash2 aria-hidden="true" /></button>}
      </li>)}</ul>}
    </aside>
    <section className="surface chatPanel" aria-label={t('chat.panel')}>
      <header className="chatHeader"><Sparkles aria-hidden="true" /><h2 dir="auto">{active ? conversationTitle(active) : t('chat.newConversation')}</h2>{streaming && <span className="statusBadge warning">{t('chat.answering')}</span>}{active && <StarButton starred={stars.ids.has(active.id)} label={conversationTitle(active)} onToggle={() => void stars.toggle(active.id)} />}</header>
      <div className="chatLog" ref={log} onScroll={onScroll} role="log" aria-live="polite" aria-busy={streaming || opening} tabIndex={0} aria-label={t('chat.messages')}>
        {(opening || (listState === 'loading' && turns.length === 0)) && <p className="chatLoading" role="status">{t('chat.loadingMessages')}</p>}
        {showEmpty && <div className="chatEmpty">
          <p>{t('chat.intro')}</p>
          {onOpenFavorite && onOpenFavorites && <FavoritesStrip onOpen={onOpenFavorite} onOpenAll={onOpenFavorites} />}
          <div className="chatSuggestions">{SUGGESTIONS.map((key) => <button type="button" className="secondary" key={key} onClick={() => void ask({ content: t(key) })} disabled={streaming}>{t(key)}</button>)}</div>
        </div>}
        {turns.map((turn) => <article key={turn.id} className={`chatTurn ${turn.role} ${turn.status}`} aria-label={turn.role === 'user' ? t('chat.you') : t('chat.assistant')}>
          {turn.role === 'user'
            ? <><p className="chatQuestion" dir="auto">{turn.content}</p>{turn.status === 'failed' && <p className="chatMeta chatError">{t('chat.notSent')}</p>}</>
            : <>
              {/* A card's own words are localized; only a results card shows the written answer above it. */}
              {visibleReply(turn.content) && (!turn.card || turn.card.state === 'results') ? <Answer blocks={parseAnswer(visibleReply(turn.content))} /> : turn.status === 'streaming' && !turn.card ? <p className="chatThinking" role="status" aria-label={t('chat.thinking')}><span /><span /><span /></p> : null}
              {turn.card && <ChatCardView card={turn.card} live={turn.status === 'streaming'} onOpen={onOpenTarget} onChange={(card) => setTurns((current) => current.map((x) => x.id === turn.id ? { ...x, card } : x))} />}
              {turn.status === 'streaming' && turn.content && (!turn.card || turn.card.state === 'results') && <span className="chatCaret" aria-hidden="true" />}
              {turn.report && <ReportAnswerCard card={turn.report} onOpen={onOpenReport} />}
              {turn.status === 'cancelled' && <p className="chatMeta">{turn.content ? t('chat.stopped') : t('chat.stoppedEarly')}</p>}
              {turn.status === 'failed' && <p className="chatMeta chatError" dir="auto">{turn.error ?? t('chat.noAnswer')}</p>}
              {turn.status === 'complete' && turn.content && (!turn.card || turn.card.state === 'results') && !turn.id.startsWith('pending') && !turn.id.startsWith('local-') && !streaming && <AnswerFeedback turnId={turn.id} content={turn.content} rating={ratings[turn.id] ?? null} onRated={(rating) => setRatings((current) => ({ ...current, [turn.id]: rating }))} />}
            </>}
        </article>)}
      </div>
      {!atBottom && <button type="button" className="secondary chatJump" onClick={jumpToLatest}><ArrowDown aria-hidden="true" />{t('chat.jump')}</button>}
      {developer.panel}
      {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{budget}</span><button type="button" className="secondary" onClick={onOpenBudgets}>{t('chat.openBudgets')}</button></p>}
      {stars.error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{stars.error}</span></p>}
      {notice && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{notice}</span></p>}
      <form className="chatComposer" onSubmit={submit}>
        <textarea ref={input} value={draft} rows={1} placeholder={t('chat.placeholder')} aria-label={t('chat.inputLabel')} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} maxLength={8000} />
        <div className="chatActions">
          {!streaming && canRetry(turns) && <button type="button" className="secondary" onClick={retry}><RotateCcw aria-hidden="true" />{t('chat.retry')}</button>}
          {streaming
            ? <button type="button" className="chatStop" onClick={() => controller.current?.abort()}><Square aria-hidden="true" />{t('chat.stop')}</button>
            : <button type="submit" className="chatSend" disabled={!draft.trim()}><Send aria-hidden="true" />{t('chat.send')}</button>}
        </div>
      </form>
    </section>
  </div>;
}

function when(value: string): string {
  const date = new Date(value);
  return date.toDateString() === new Date().toDateString()
    ? formatDateTime(date, { hour: '2-digit', minute: '2-digit' })
    : formatDateTime(date, { month: 'short', day: 'numeric' });
}
