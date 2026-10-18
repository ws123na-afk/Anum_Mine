// WhatsApp inbox (docs/whatsapp.md): conversations from the WhatsApp Cloud API webhook, the
// 24-hour customer-service window, AI triage and drafts (labelled with where they came from),
// approve-to-send and message templates. Owners connect the number under Setup; members read
// and ask to send; owners approve sends with the payload hash shown here.
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, CheckCircle2, CircleDot, Clock, Copy, FileText, Fingerprint, Globe, Inbox, Lock, MessageCircle, Paperclip, Plug, RefreshCw, Search, Send, ShieldAlert, ShieldCheck, Sparkles, Trash2, XCircle } from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { mapApproval, type ApiApproval } from './lib/api';
import { argumentRows, expiryLabel, shortHash } from './lib/approvals';
import { describeError, isBudgetExceeded, isPermissionDenied, budgetMessage, ApiError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import {
  FILTERS, MAX_TEXT, PAGE, accountBody, categoryLabel, contactLabel, emptyForm, fillTemplate, filterLabel, mergeMessages, opensInline,
  provenanceLabel, RETENTION_CHOICES, randomVerifyToken, retentionLabel, sendableTemplates, statusLabel, statusTone, templateKey, webhookUrl, windowLabel, windowState,
  type AccountForm, type ConversationFilter, type WaAccount, type WaConversation, type WaMessage, type WaSuggestion, type WaTemplate,
} from './lib/whatsapp';
import * as wa from './lib/whatsappApi';
import { useIsOwner } from './useIsOwner';
import { firstUnread } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { PageHeader, RAIL_QUERY, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import './whatsapp.css';
import { HelpLink } from './HelpLink';

type Tab = 'inbox' | 'templates' | 'setup';
type Load = { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; message: string } | { kind: 'ready'; account: WaAccount };

const time = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function WhatsAppView({ onOpenApprovals, onOpenBudgets, focusConversationId = null }: { onOpenApprovals?: () => void; onOpenBudgets?: () => void; focusConversationId?: string | null } = {}) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [tab, setTab] = useState<Tab>('inbox');
  const owner = useIsOwner();

  const refresh = useCallback(async () => {
    try {
      const account = await wa.getAccount();
      setLoad({ kind: 'ready', account });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'denied' } : { kind: 'error', message: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const header = <PageHeader title={viewLabel('whatsapp')} subtitle={t('pages.wa.subtitle')} actions={load.kind === 'ready' ? <StatusChip account={load.account} onSetup={() => setTab('setup')} /> : undefined} />;
  if (load.kind === 'loading') return <>{header}<p className="notice" role="status"><CircleDot aria-hidden="true" />{t('wa.loading')}</p></>;
  if (load.kind === 'denied') return <>{header}<section className="surface waPanel" aria-labelledby="wa-denied">
    <Lock aria-hidden="true" className="waPanelIcon" />
    <div><h2 id="wa-denied">{t('wa.denied.title')}</h2><p className="muted">{t('wa.denied.text')}</p></div>
  </section></>;
  if (load.kind === 'error') return <>{header}<div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('wa.loadFailed', { message: load.message })}</span><span className="actions waInline"><button type="button" className="secondary" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}>{t('common.tryAgain')}</button></span></div></>;

  const { account } = load;
  const tabs: Tab[] = ['inbox', 'templates', 'setup'];
  return <div className="waLayout">
    {header}
    <nav className="settingsTabs waTabs" aria-label={t('wa.sections')}>
      {tabs.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>{t(`wa.tab.${id}`)}</button>)}
    </nav>
    {tab === 'inbox' && (account.configured
      ? <InboxView account={account} owner={owner === true} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} onOpenTemplates={() => setTab('templates')} focusConversationId={focusConversationId} />
      : <NotConnected account={account} onSetup={() => setTab('setup')} />)}
    {tab === 'templates' && (account.configured
      ? <TemplatesView account={account} onSaved={(next) => setLoad({ kind: 'ready', account: next })} />
      : <NotConnected account={account} onSetup={() => setTab('setup')} />)}
    {tab === 'setup' && <SetupView account={account} onSaved={(next) => setLoad({ kind: 'ready', account: next })} onDisconnected={() => void refresh()} />}
  </div>;
}

/** The number's connection state; opens Setup (where owners connect, check and change it). */
function StatusChip({ account, onSetup }: { account: WaAccount; onSetup?: () => void }) {
  const tone = !account.configured ? 'neutral' : account.status === 'connected' ? 'success' : account.status === 'error' ? 'dangerTone' : 'warning';
  if (!onSetup) return <span className={`statusBadge ${tone}`}>{account.configured ? t(`wa.state.${account.status ?? 'unverified'}`) : t('wa.state.notConnected')}{account.display_phone_number && <> · <bdi dir="ltr">{account.display_phone_number}</bdi></>}</span>;
  return <button type="button" className={`chipButton ${tone} waTabStatus`} onClick={onSetup} aria-label={t('pages.wa.chipLabel', { state: account.configured ? t(`wa.state.${account.status ?? 'unverified'}`) : t('wa.state.notConnected') })}>
    <span className="chipDot" aria-hidden="true" />
    <span>{account.configured ? t(`wa.state.${account.status ?? 'unverified'}`) : t('wa.state.notConnected')}{account.display_phone_number && <> · <bdi dir="ltr">{account.display_phone_number}</bdi></>}</span>
  </button>;
}

function NotConnected({ account, onSetup }: { account: WaAccount; onSetup: () => void }) {
  return <section className="surface emptyState">
    <Plug aria-hidden="true" />
    <h3>{t('wa.empty.notConnected')}</h3>
    <p>{account.can_manage ? t('wa.empty.notConnectedOwner') : t('wa.empty.notConnectedMember')}</p>
    {account.can_manage && <div className="actions waCenter"><button type="button" onClick={onSetup}><Plug aria-hidden="true" />{t('wa.empty.connect')}</button></div>}
  </section>;
}

// ---------------------------------------------------------------- Inbox

function InboxView({ account, owner, onOpenApprovals, onOpenBudgets, onOpenTemplates, focusConversationId = null }: { account: WaAccount; owner: boolean; onOpenApprovals?: () => void; onOpenBudgets?: () => void; onOpenTemplates: () => void; focusConversationId?: string | null }) {
  const [items, setItems] = useState<WaConversation[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ConversationFilter>('all');
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Opened from an Assistant card: that conversation, even before it is in the loaded page.
  const [selected, setSelected] = useState<string | null>(focusConversationId);
  const searchId = useId();
  const wide = useMediaQuery(SPLIT_QUERY);
  const autoPicked = useRef(Boolean(focusConversationId));

  const load = useCallback(async () => {
    try {
      const page = await wa.listConversations({ limit: PAGE, filter, query: applied });
      setItems((current) => {
        // Keep older pages the person already loaded; the first page is replaced.
        const ids = new Set(page.map((item) => item.id));
        const older = current.length > PAGE ? current.slice(PAGE).filter((item) => !ids.has(item.id)) : [];
        return [...page, ...older];
      });
      setMore((current) => current || page.length === PAGE);
      setState('ready');
      setError(null);
    } catch (e) {
      setState('error');
      setError(describeError(e));
    }
  }, [filter, applied]);

  useEffect(() => { setItems([]); setMore(false); setState('loading'); void load(); }, [filter, applied]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 20_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function loadMore() {
    const last = items[items.length - 1];
    if (!last) return;
    setLoadingMore(true);
    try {
      const page = await wa.listConversations({ limit: PAGE, filter, query: applied, before: last.id });
      setItems((current) => [...current, ...page.filter((item) => !current.some((c) => c.id === item.id))]);
      setMore(page.length === PAGE);
    } catch (e) { setError(describeError(e)); } finally { setLoadingMore(false); }
  }

  // On a wide screen the thread opens on the first unread conversation, once.
  useEffect(() => {
    if (!wide || autoPicked.current || selected || state !== 'ready') return;
    const first = firstUnread(items, (item) => item.unread_count > 0);
    if (!first) return;
    autoPicked.current = true;
    setSelected(first.id);
  }, [wide, selected, state, items]);

  const replace = (next: WaConversation) => setItems((current) => current.map((item) => item.id === next.id ? next : item));
  const chosen = items.find((item) => item.id === selected) ?? (selected !== null && selected === focusConversationId ? { id: selected } : null);

  return <SplitView layout="three" className={`waInbox ${selected ? 'showThread' : 'showList'}`} show={selected ? 'detail' : 'list'}>
    <section data-pane="list" className="pane waList" aria-labelledby="wa-list-heading">
      <h2 id="wa-list-heading" className="visuallyHidden">{t('wa.list.heading')}</h2>
      <form className="waSearch" role="search" onSubmit={(e) => { e.preventDefault(); setApplied(query); }}>
        <label className="visuallyHidden" htmlFor={searchId}>{t('wa.list.search')}</label>
        <input id={searchId} type="search" value={query} placeholder={t('wa.list.search')} onChange={(e) => setQuery(e.target.value)} />
        <button type="submit" className="secondary iconOnly" aria-label={t('common.search')} title={t('common.search')}><Search aria-hidden="true" /></button>
      </form>
      <div className="waFilters" role="group" aria-label={t('wa.list.filters')}>
        {FILTERS.map((id) => <button key={id} type="button" className="waChip" aria-pressed={filter === id} onClick={() => setFilter(id)}>{filterLabel(id)}</button>)}
      </div>
      {state === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('wa.list.loading')}</p>}
      {state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('wa.list.failed', { message: error ?? '' })}</span><span className="actions waInline"><button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></span></div>}
      {state === 'ready' && items.length === 0 && <div className="emptyState waListEmpty">
        <Inbox aria-hidden="true" />
        <h3>{applied || filter !== 'all' ? t('wa.list.noMatch') : t('wa.list.empty')}</h3>
        <p>{applied || filter !== 'all' ? t('wa.list.noMatchText') : t('wa.list.emptyText', { number: account.display_phone_number ?? account.phone_number_id ?? '' })}</p><HelpLink />
        {(applied || filter !== 'all') && <div className="actions waCenter"><button type="button" className="secondary" onClick={() => { setQuery(''); setApplied(''); setFilter('all'); }}>{t('wa.list.showAll')}</button></div>}
      </div>}
      {items.length > 0 && <ul className="waConversations" aria-label={t('wa.list.heading')}>
        {items.map((item) => <li key={item.id}>
          <button type="button" className="waConversation" aria-current={selected === item.id ? 'true' : undefined} onClick={() => setSelected(item.id)}>
            <span className="waRowTop">
              <strong><bdi>{contactLabel(item)}</bdi></strong>
              <time dateTime={item.last_message_at}>{time(item.last_message_at)}</time>
            </span>
            <span className="waPreview" dir="auto">{item.last_direction === 'outbound' ? `${t('wa.list.you')} ` : ''}{item.last_message_preview || '…'}</span>
            <span className="waRowMeta">
              {item.category && <span className={`waCategory ${item.category}`}>{categoryLabel(item.category)}</span>}
              <span className={`waWindowDot ${item.window_open ? 'open' : 'closed'}`}>{item.window_open ? t('wa.window.short.open') : t('wa.window.short.closed')}</span>
              {item.awaiting_approval > 0 && <span className="statusBadge warning">{t('wa.list.awaiting', { count: item.awaiting_approval })}</span>}
              {item.unread_count > 0 && <span className="waUnread" aria-label={t('wa.list.unread', { count: item.unread_count })}>{item.unread_count}</span>}
            </span>
          </button>
        </li>)}
      </ul>}
      {more && items.length > 0 && <div className="actions waCenter"><button type="button" className="secondary" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? t('common.loading') : t('wa.list.more')}</button></div>}
    </section>
    {chosen
      ? <ThreadView key={chosen.id} conversationId={chosen.id} owner={owner} onBack={() => setSelected(null)} onChanged={replace} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets} onOpenTemplates={onOpenTemplates} />
      : <><section data-pane="detail" className="pane waThread waThreadEmpty" aria-label={t('wa.thread.none')}><MessageCircle aria-hidden="true" /><p>{t('wa.thread.none')}</p></section>
        <aside data-pane="rail" className="pane waRail" aria-label={t('pages.wa.railLabel')}><div className="paneEmpty"><p>{t('pages.wa.railEmpty')}</p></div></aside></>}
  </SplitView>;
}

// ---------------------------------------------------------------- Thread

function ThreadView({ conversationId, owner, onBack, onChanged, onOpenApprovals, onOpenBudgets, onOpenTemplates }: {
  conversationId: string; owner: boolean; onBack: () => void; onChanged: (c: WaConversation) => void;
  onOpenApprovals?: () => void; onOpenBudgets?: () => void; onOpenTemplates: () => void;
}) {
  const [conversation, setConversation] = useState<WaConversation | null>(null);
  const [messages, setMessages] = useState<WaMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [suggestion, setSuggestion] = useState<WaSuggestion | null>(null);
  const [mode, setMode] = useState<'text' | 'template'>('text');
  const heading = useRef<HTMLHeadingElement | null>(null);
  const composer = useRef<HTMLTextAreaElement | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const lastId = messages[messages.length - 1]?.id;
  // From 1280px the AI suggestion sits in the rail next to the thread; narrower, inside the thread.
  const railShown = useMediaQuery(RAIL_QUERY);
  const draftId = useId();
  const now = new Date();

  const load = useCallback(async (first = false) => {
    try {
      const page = await wa.getThread(conversationId, { limit: PAGE });
      setConversation(page.conversation);
      setSuggestion((current) => current ?? page.conversation.suggestion);
      setMessages((current) => first ? page.messages : mergeMessages(current, page.messages));
      if (first) setHasMore(page.has_more);
      setState('ready');
      setError(null);
      onChanged(page.conversation);
      if (first) {
        if (!page.conversation.window_open) setMode('template');
        if (page.conversation.unread_count > 0) void wa.markRead(conversationId).then(onChanged).catch(() => undefined);
      }
    } catch (e) {
      setState('error');
      setError(describeError(e));
    }
  }, [conversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void load(true).then(() => heading.current?.focus()); }, [load]);
  // Newest message in view: on open and whenever a newer one arrives (not when older pages load).
  useEffect(() => { const box = scroller.current; if (box) box.scrollTop = box.scrollHeight; }, [lastId]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 15_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function older() {
    const first = messages[0];
    if (!first) return;
    setBusy('older');
    try {
      const page = await wa.getThread(conversationId, { limit: PAGE, before: first.id });
      setMessages((current) => mergeMessages(current, page.messages));
      setHasMore(page.has_more);
    } catch (e) { setNotice({ tone: 'error', text: describeError(e) }); } finally { setBusy(null); }
  }

  async function makeDraft() {
    setBusy('draft'); setNotice(null); setBudget(null);
    try {
      const next = await wa.draftReply(conversationId);
      setSuggestion(next);
    } catch (e) {
      if (isBudgetExceeded(e)) setBudget(budgetMessage(e));
      else setNotice({ tone: 'error', text: t('wa.draft.failed', { message: describeError(e) }) });
    } finally { setBusy(null); }
  }

  function afterSend(result: Awaited<ReturnType<typeof wa.sendText>>) {
    setMessages((current) => mergeMessages(current, [result.message]));
    setNotice({ tone: result.outcome === 'failed' ? 'error' : 'ok', text: result.outcome === 'sent' ? t('wa.send.sent') : result.outcome === 'awaiting_approval' ? (owner ? t('wa.send.awaitingOwner') : t('wa.send.awaitingMember')) : t('wa.send.failed', { message: result.detail }) });
    void load();
  }

  async function sendText(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setBusy('send'); setNotice(null);
    try {
      afterSend(await wa.sendText(conversationId, text));
      setDraft('');
    } catch (e) {
      setNotice({ tone: 'error', text: isPermissionDenied(e) ? t('wa.send.denied') : t('wa.send.failed', { message: describeError(e) }) });
      void load();
    } finally { setBusy(null); }
  }

  async function decide(message: WaMessage, approval: Approval, decision: 'approve' | 'reject') {
    setBusy(`${decision}:${message.id}`); setNotice(null);
    try {
      const result = decision === 'approve' ? await api.approveTask(approval) : await api.rejectTask(approval);
      setNotice({ tone: 'ok', text: decision === 'approve' ? (result.run?.status === 'failed' ? t('wa.send.failed', { message: result.run.result ?? '' }) : t('wa.approve.done')) : t('wa.approve.rejected') });
      await load();
    } catch (e) {
      setNotice({ tone: 'error', text: isPermissionDenied(e) ? t('wa.approve.denied', { message: describeError(e) }) : describeError(e) });
    } finally { setBusy(null); }
  }

  const emptyRail = <aside data-pane="rail" className="pane waRail" aria-label={t('pages.wa.railLabel')} />;
  if (state === 'loading' && !conversation) return <><section data-pane="detail" className="pane waThread"><p className="notice" role="status"><CircleDot aria-hidden="true" />{t('wa.thread.loading')}</p></section>{emptyRail}</>;
  if (!conversation) return <><section data-pane="detail" className="pane waThread"><div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('wa.thread.failed', { message: error ?? '' })}</span><span className="actions waInline"><button type="button" className="secondary" onClick={onBack}>{t('wa.thread.back')}</button><button type="button" className="secondary" onClick={() => void load(true)}>{t('common.tryAgain')}</button></span></div></section>{emptyRail}</>;

  const replyWindow = windowState(conversation, now);
  const suggestionPanel = <SuggestionPanel conversation={conversation} suggestion={suggestion} busy={busy === 'draft'} onDraft={() => void makeDraft()} onUse={(text) => { setDraft(text); setMode('text'); composer.current?.focus(); }} canUse={replyWindow.open} />;
  return <><section data-pane="detail" className="pane waThread" aria-labelledby="wa-thread-title">
    <header className="waThreadHeader">
      <button type="button" className="secondary iconOnly waBack" onClick={onBack} aria-label={t('wa.thread.back')} title={t('wa.thread.back')}><ArrowLeft aria-hidden="true" className="flipRtl" /></button>
      <div className="waThreadWho">
        <h2 id="wa-thread-title" tabIndex={-1} ref={heading}><bdi>{contactLabel(conversation)}</bdi></h2>
        <small className="muted"><bdi dir="ltr">+{conversation.wa_id}</bdi>{conversation.category && <> · {categoryLabel(conversation.category)}</>}</small>
      </div>
      <span className={`waWindow ${replyWindow.open ? 'open' : 'closed'}`} role="status"><Clock aria-hidden="true" />{windowLabel(conversation, now)}</span>
    </header>
    {state === 'error' && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('wa.thread.refreshFailed', { message: error ?? '' })}</p>}

    {!railShown && suggestionPanel}
    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{budget}</span>{onOpenBudgets && <span className="actions waInline"><button type="button" className="secondary" onClick={onOpenBudgets}>{t('wa.openBudgets')}</button></span>}</p>}

    <div className="waMessages" ref={scroller} role="region" tabIndex={0} aria-label={t('wa.thread.messages')}>
      {hasMore && <div className="actions waCenter"><button type="button" className="secondary" disabled={busy === 'older'} onClick={() => void older()}>{busy === 'older' ? t('common.loading') : t('wa.thread.older')}</button></div>}
      {messages.length === 0 && <p className="muted waCenterText">{t('wa.thread.noMessages')}</p>}
      <ol className="waBubbles">
        {messages.map((message) => <li key={message.id} className={`waBubble ${message.direction}`}>
          <MessageBubble message={message} owner={owner} busy={busy} onDecide={(approval, decision) => void decide(message, approval, decision)} onOpenApprovals={onOpenApprovals} />
        </li>)}
      </ol>
    </div>

    {notice && <p className={`notice ${notice.tone === 'error' ? 'errorNotice' : ''}`} role={notice.tone === 'error' ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{notice.text}</p>}

    <div className="waComposerTabs" role="group" aria-label={t('wa.compose.mode')}>
      <button type="button" className="waChip" aria-pressed={mode === 'text'} onClick={() => setMode('text')}>{t('wa.compose.text')}</button>
      <button type="button" className="waChip" aria-pressed={mode === 'template'} onClick={() => setMode('template')}>{t('wa.compose.template')}</button>
    </div>
    {mode === 'text' && (replyWindow.open
      ? <form className="waComposer" onSubmit={(e) => void sendText(e)}>
        <label className="field" htmlFor={draftId}><span>{t('wa.compose.label')}</span>
          <textarea id={draftId} ref={composer} dir="auto" rows={3} maxLength={MAX_TEXT} value={draft} placeholder={t('wa.compose.placeholder')} onChange={(e) => setDraft(e.target.value)} />
        </label>
        <div className="actions waComposerFooter">
          <small className="muted">{t('wa.compose.approvalNote')}</small>
          <button type="submit" disabled={busy === 'send' || !draft.trim()}><Send aria-hidden="true" className="flipRtl" />{busy === 'send' ? t('wa.compose.sending') : t('wa.compose.request')}</button>
        </div>
      </form>
      : <div className="waClosed" role="note">
        <Lock aria-hidden="true" />
        <p>{t('wa.compose.closed')}</p>
        <button type="button" className="secondary" onClick={() => setMode('template')}>{t('wa.compose.useTemplate')}</button>
      </div>)}
    {mode === 'template' && <TemplateComposer conversation={conversation} onSent={afterSend} onManage={onOpenTemplates} />}
  </section>
  <aside data-pane="rail" className="pane waRail" aria-label={t('pages.wa.railLabel')}>
    <section className="railSection" aria-labelledby="wa-rail-contact">
      <h3 id="wa-rail-contact">{t('pages.wa.contact')}</h3>
      <dl className="railFacts">
        <dt>{t('pages.wa.name')}</dt><dd><bdi>{contactLabel(conversation)}</bdi></dd>
        <dt>{t('pages.wa.number')}</dt><dd><bdi dir="ltr">+{conversation.wa_id}</bdi></dd>
        <dt>{t('pages.wa.topic')}</dt><dd>{conversation.category ? categoryLabel(conversation.category) : t('pages.wa.noTopic')}</dd>
        <dt>{t('pages.wa.window')}</dt><dd>{windowLabel(conversation, now)}</dd>
        <dt>{t('pages.wa.lastMessage')}</dt><dd><time dateTime={conversation.last_message_at}>{time(conversation.last_message_at)}</time></dd>
        <dt>{t('pages.wa.awaiting')}</dt><dd>{conversation.awaiting_approval ? <>{t('wa.list.awaiting', { count: conversation.awaiting_approval })}{onOpenApprovals && <> · <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('wa.approve.openApprovals')}</button></>}</> : t('pages.wa.noneAwaiting')}</dd>
      </dl>
    </section>
    {railShown && <div className="railSection">{suggestionPanel}</div>}
  </aside></>;
}

function SuggestionPanel({ conversation, suggestion, busy, onDraft, onUse, canUse }: { conversation: WaConversation; suggestion: WaSuggestion | null; busy: boolean; onDraft: () => void; onUse: (text: string) => void; canUse: boolean }) {
  return <section className="waSuggestion" aria-label={t('wa.draft.heading')}>
    <div className="waSuggestionHead">
      <h3><Sparkles aria-hidden="true" />{t('wa.draft.heading')}</h3>
      <button type="button" className="secondary" disabled={busy} onClick={onDraft}><RefreshCw aria-hidden="true" />{busy ? t('wa.draft.working') : suggestion ? t('wa.draft.again') : t('wa.draft.make')}</button>
    </div>
    {(suggestion?.summary || conversation.summary) && <p className="waSummary" dir="auto"><strong>{t('wa.draft.summary')}</strong> <bdi>{suggestion?.summary ?? conversation.summary}</bdi></p>}
    {suggestion
      ? <>
        <blockquote className="waDraftText" dir="auto">{suggestion.text}</blockquote>
        <p className="waProvenance"><ShieldAlert aria-hidden="true" />{provenanceLabel(suggestion)} · {t('wa.draft.check')}</p>
        <div className="actions waInlineActions">
          <button type="button" disabled={!canUse} onClick={() => onUse(suggestion.text)}>{t('wa.draft.use')}</button>
          {!canUse && <small className="muted">{t('wa.draft.closedNote')}</small>}
        </div>
      </>
      : <p className="muted">{t('wa.draft.none')}</p>}
  </section>;
}

function MessageBubble({ message, owner, busy, onDecide, onOpenApprovals }: { message: WaMessage; owner: boolean; busy: string | null; onDecide: (approval: Approval, decision: 'approve' | 'reject') => void; onOpenApprovals?: () => void }) {
  const approval = message.approval ? mapApproval(message.approval as unknown as ApiApproval) : null;
  const [mediaError, setMediaError] = useState<string | null>(null);
  async function open() {
    if (!message.media) return;
    setMediaError(null);
    try { await wa.openMedia(message.id, message.media.filename ?? `${message.kind}-${message.id}`, opensInline(message.media.mime_type)); }
    catch (e) { setMediaError(e instanceof ApiError && e.status === 413 ? t('wa.media.tooLarge') : t('wa.media.failed', { message: describeError(e) })); }
  }
  return <article className="waBubbleBody" aria-label={message.direction === 'inbound' ? t('wa.message.fromCustomer') : t('wa.message.fromYou')}>
    {message.template && <p className="waTemplateTag"><FileText aria-hidden="true" />{t('wa.message.template', { name: message.template.name })}</p>}
    {message.body && <p className="waText" dir="auto">{message.body}</p>}
    {!message.body && !message.media && <p className="waText muted">{t('wa.message.unsupported', { kind: message.kind })}</p>}
    {message.media && <div className="waMedia">
      <button type="button" className="secondary" onClick={() => void open()}><Paperclip aria-hidden="true" />{message.media.filename ?? t(`wa.media.kind.${message.media.voice ? 'voice' : ['image', 'video', 'audio', 'document', 'sticker'].includes(message.kind) ? message.kind : 'file'}` as never)}</button>
      {mediaError && <small className="waError" role="alert">{mediaError}</small>}
    </div>}
    <footer className="waBubbleMeta">
      <time dateTime={message.timestamp}>{time(message.timestamp)}</time>
      {message.direction === 'outbound' && <span className={`statusBadge ${statusTone(message.status)}`}>{statusLabel(message.status)}</span>}
      {message.sent_by === 'whatsapp_business_app' && <small className="muted">{t('wa.message.fromPhone')}</small>}
    </footer>
    {message.error && <p className="waError">{message.error}</p>}
    {approval && message.status === 'awaiting_approval' && <ApprovalCard approval={approval} owner={owner} busy={busy} messageId={message.id} onDecide={onDecide} onOpenApprovals={onOpenApprovals} />}
  </article>;
}

function ApprovalCard({ approval, owner, busy, messageId, onDecide, onOpenApprovals }: { approval: Approval; owner: boolean; busy: string | null; messageId: string; onDecide: (approval: Approval, decision: 'approve' | 'reject') => void; onOpenApprovals?: () => void }) {
  const rows = argumentRows(approval.arguments).filter((row) => !['message_id', 'conversation_id'].includes(row.key));
  const expiry = expiryLabel(approval);
  return <section className="waApproval" aria-label={t('wa.approve.heading')}>
    <h4><ShieldCheck aria-hidden="true" />{t('wa.approve.heading')}</h4>
    {approval.target && <small className="approvalTarget"><Globe aria-hidden size={12} /> {t('wa.approve.target')} <code>{approval.target}</code></small>}
    <dl className="approvalArgs">{rows.map((row) => <div className="approvalArg" key={row.key}><dt dir="ltr">{row.key}</dt><dd><code dir="auto">{row.value}</code></dd></div>)}</dl>
    <small className="approvalHash" title={approval.payloadHash ?? undefined}><Fingerprint aria-hidden size={12} /> {t('wa.approve.hash')} <code>{shortHash(approval.payloadHash)}</code>{expiry && <> · {expiry}</>}</small>
    {owner
      ? <div className="actions waInlineActions">
        <button type="button" disabled={busy !== null || !approval.payloadHash} onClick={() => onDecide(approval, 'approve')}><CheckCircle2 aria-hidden="true" />{busy === `approve:${messageId}` ? t('wa.compose.sending') : t('wa.approve.send')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => onDecide(approval, 'reject')}><XCircle aria-hidden="true" />{t('wa.approve.reject')}</button>
      </div>
      : <p className="muted">{t('wa.approve.ownerOnly')}{onOpenApprovals && <> <button type="button" className="linkButton" onClick={onOpenApprovals}>{t('wa.approve.openApprovals')}</button></>}</p>}
  </section>;
}

function TemplateComposer({ conversation, onSent, onManage }: { conversation: WaConversation; onSent: (result: Awaited<ReturnType<typeof wa.sendTemplate>>) => void; onManage: () => void }) {
  const [templates, setTemplates] = useState<WaTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState('');
  const [parameters, setParameters] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const selectId = useId();

  const load = useCallback(async (refresh = false) => {
    setError(null);
    try { setTemplates(sendableTemplates(await wa.listTemplates(refresh))); } catch (e) { setError(describeError(e)); setTemplates([]); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const template = useMemo(() => templates?.find((item) => templateKey(item) === chosen) ?? null, [templates, chosen]);
  useEffect(() => { setParameters(Array.from({ length: template?.parameter_count ?? 0 }, () => '')); setProblem(null); }, [template]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!template) return;
    if (parameters.some((value) => !value.trim())) { setProblem(t('wa.template.fillAll')); return; }
    setBusy(true); setProblem(null);
    try {
      onSent(await wa.sendTemplate(conversation.id, { name: template.name, language: template.language, parameters: parameters.map((v) => v.trim()) }));
      setChosen('');
    } catch (e) { setProblem(t('wa.send.failed', { message: describeError(e) })); } finally { setBusy(false); }
  }

  if (templates === null) return <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('wa.template.loading')}</p>;
  return <form className="waComposer" onSubmit={(e) => void submit(e)}>
    {error && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('wa.template.failed', { message: error })}</span><span className="actions waInline"><button type="button" className="secondary" onClick={() => void load(true)}>{t('common.tryAgain')}</button></span></div>}
    {!error && templates.length === 0 && <p className="muted">{t('wa.template.none')} <button type="button" className="linkButton" onClick={onManage}>{t('wa.template.manage')}</button></p>}
    {templates.length > 0 && <>
      <label className="field" htmlFor={selectId}><span>{t('wa.template.choose')}</span>
        <select id={selectId} value={chosen} onChange={(e) => setChosen(e.target.value)}>
          <option value="">{t('wa.template.pick')}</option>
          {templates.map((item) => <option key={templateKey(item)} value={templateKey(item)}>{item.name} ({item.language}){item.auto_send ? ` · ${t('wa.template.autoShort')}` : ''}</option>)}
        </select>
      </label>
      {template && <>
        {parameters.map((value, index) => <label className="field" key={index}><span>{t('wa.template.parameter', { number: index + 1 })}</span>
          <input dir="auto" value={value} maxLength={1024} onChange={(e) => setParameters((current) => current.map((item, i) => i === index ? e.target.value : item))} />
        </label>)}
        <div className="waTemplatePreview"><span className="muted">{t('wa.template.preview')}</span><p dir="auto">{fillTemplate(template.body, parameters)}</p></div>
      </>}
      {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
      <div className="actions waComposerFooter">
        <small className="muted">{template?.auto_send ? t('wa.template.autoNote') : t('wa.template.approvalNote')}</small>
        <button type="submit" disabled={!template || busy}><Send aria-hidden="true" className="flipRtl" />{busy ? t('wa.compose.sending') : template?.auto_send ? t('wa.template.sendNow') : t('wa.compose.request')}</button>
      </div>
    </>}
  </form>;
}

// ---------------------------------------------------------------- Templates

function TemplatesView({ account, onSaved }: { account: WaAccount; onSaved: (account: WaAccount) => void }) {
  const [templates, setTemplates] = useState<WaTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const searchId = useId();

  const load = useCallback(async (refresh = false) => {
    setError(null);
    try { setTemplates(await wa.listTemplates(refresh)); } catch (e) { setError(describeError(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function toggle(template: WaTemplate) {
    const next = template.auto_send ? account.auto_send_templates.filter((name) => name !== template.name) : [...account.auto_send_templates, template.name];
    const body = accountBody({ ...emptyForm(account), autoSend: next }, true);
    if (!body.ok) { setNotice(body.error); return; }
    setBusy(template.name); setNotice(null);
    try {
      const saved = await wa.saveAccount(body.value);
      onSaved(saved);
      setTemplates((current) => current?.map((item) => ({ ...item, auto_send: saved.auto_send_templates.includes(item.name) })) ?? null);
      setNotice(template.auto_send ? t('wa.templates.autoOff', { name: template.name }) : t('wa.templates.autoOn', { name: template.name }));
    } catch (e) { setNotice(describeError(e)); } finally { setBusy(null); }
  }

  const shown = (templates ?? []).filter((item) => !query.trim() || item.name.includes(query.trim().toLowerCase()) || item.body.toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="waTemplates">
    <section className="moduleIntro">
      <div><h2>{t('wa.templates.title')}</h2><p>{t('wa.templates.intro')}</p></div>
      <button type="button" className="secondary" onClick={() => void load(true)} disabled={templates === null && !error}><RefreshCw aria-hidden="true" />{t('wa.templates.refresh')}</button>
    </section>
    {templates === null && !error && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('wa.template.loading')}</p>}
    {error && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('wa.template.failed', { message: error })}</span><span className="actions waInline"><button type="button" className="secondary" onClick={() => void load(true)}>{t('common.tryAgain')}</button></span></div>}
    {notice && <p className="notice" role="status"><CircleDot aria-hidden="true" />{notice}</p>}
    {templates && templates.length > 0 && <form className="waSearch" role="search" onSubmit={(e) => e.preventDefault()}>
      <label className="visuallyHidden" htmlFor={searchId}>{t('wa.templates.search')}</label>
      <input id={searchId} type="search" value={query} placeholder={t('wa.templates.search')} onChange={(e) => setQuery(e.target.value)} />
    </form>}
    {templates && templates.length === 0 && <section className="surface emptyState"><FileText aria-hidden="true" /><h3>{t('wa.templates.empty')}</h3><p>{t('wa.templates.emptyText')}</p></section>}
    {templates && templates.length > 0 && shown.length === 0 && <p className="muted">{t('wa.templates.noMatch')}</p>}
    <ul className="waTemplateList">
      {shown.map((item) => <li key={templateKey(item)} className="surface waTemplateCard">
        <header>
          <h3 dir="ltr">{item.name}</h3>
          <span className="muted">{item.language} · {item.category.toLowerCase()}</span>
          <span className={`statusBadge ${item.status === 'APPROVED' ? 'success' : item.status === 'REJECTED' || item.status === 'PAUSED' || item.status === 'DISABLED' ? 'dangerTone' : 'warning'}`}>{item.status.toLowerCase()}</span>
        </header>
        {item.header && <p className="waTemplateHeader" dir="auto">{item.header}</p>}
        <p className="waTemplateBody" dir="auto">{item.body}</p>
        {item.footer && <small className="muted" dir="auto">{item.footer}</small>}
        {account.can_manage && item.status === 'APPROVED' && <label className="waCheck">
          <input type="checkbox" checked={item.auto_send} disabled={busy !== null} onChange={() => void toggle(item)} />
          <span>{t('wa.templates.autoLabel')}</span>
        </label>}
        {!account.can_manage && item.auto_send && <small className="statusBadge neutral">{t('wa.template.autoShort')}</small>}
      </li>)}
    </ul>
    <p className="muted waFootnote">{t('wa.templates.autoExplain')}</p>
  </div>;
}

// ---------------------------------------------------------------- Setup

function SetupView({ account, onSaved, onDisconnected }: { account: WaAccount; onSaved: (account: WaAccount) => void; onDisconnected: () => void }) {
  const [form, setForm] = useState<AccountForm>(() => emptyForm(account));
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const ids = { phone: useId(), waba: useId(), token: useId(), secret: useId(), verify: useId(), test: useId(), retention: useId(), retentionNote: useId() };
  const url = webhookUrl(api.apiBaseUrl, account.webhook_path);
  const set = (patch: Partial<AccountForm>) => setForm((current) => ({ ...current, ...patch }));

  async function run(kind: string, fn: () => Promise<void>) {
    setBusy(kind); setNotice(null);
    try { await fn(); } catch (e) { setNotice({ tone: 'error', text: describeError(e) }); } finally { setBusy(null); }
  }
  const save = (event: FormEvent) => { event.preventDefault(); void run('save', async () => {
    const body = accountBody(form, account.configured);
    if (!body.ok) { setNotice({ tone: 'error', text: body.error }); return; }
    const saved = await wa.saveAccount(body.value);
    onSaved(saved);
    setForm(emptyForm(saved));
    setNotice(saved.status === 'connected' ? { tone: 'ok', text: t('wa.setup.saved', { name: saved.verified_name ?? saved.display_phone_number ?? saved.phone_number_id ?? '' }) } : { tone: 'error', text: t('wa.setup.savedProblem', { message: saved.last_error ?? '' }) });
  }); };
  const check = () => void run('check', async () => {
    const saved = await wa.checkAccount();
    onSaved(saved);
    setNotice(saved.status === 'connected' ? { tone: 'ok', text: t('wa.setup.checkOk') } : { tone: 'error', text: t('wa.setup.savedProblem', { message: saved.last_error ?? '' }) });
  });
  const test = () => void run('test', async () => {
    const result = await wa.sendTest();
    setNotice({ tone: 'ok', text: t('wa.setup.testSent', { number: result.to }) });
  });
  const remove = () => void run('delete', async () => {
    if (!confirm(t('wa.setup.disconnectConfirm'))) return;
    await wa.disconnect();
    onDisconnected();
  });
  async function copy() {
    try { await navigator.clipboard.writeText(url); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { setCopied(false); }
  }

  return <div className="waSetup">
    <section className="surface waSetupStatus" aria-labelledby="wa-setup-status">
      <h2 id="wa-setup-status">{t('wa.setup.statusTitle')}</h2>
      {account.deployment_problems.length > 0 && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('wa.setup.deployment', { names: account.deployment_problems.join(', ') })}</p>}
      <dl className="waFacts">
        <div><dt>{t('wa.setup.state')}</dt><dd><StatusChip account={account} /></dd></div>
        {account.verified_name && <div><dt>{t('wa.setup.name')}</dt><dd dir="auto">{account.verified_name}</dd></div>}
        {account.quality_rating && <div><dt>{t('wa.setup.quality')}</dt><dd>{account.quality_rating.toLowerCase()}</dd></div>}
        <div><dt>{t('wa.setup.lastWebhook')}</dt><dd>{account.last_webhook_at ? time(account.last_webhook_at) : t('wa.setup.never')}</dd></div>
        {account.last_checked_at && <div><dt>{t('wa.setup.lastCheck')}</dt><dd>{time(account.last_checked_at)}</dd></div>}
      </dl>
      {account.last_error && <p className="waError">{account.last_error}</p>}
      <div className="waWebhook">
        <span className="muted">{t('wa.setup.webhookUrl')}</span>
        <code dir="ltr">{url}</code>
        <button type="button" className="secondary" onClick={() => void copy()}><Copy aria-hidden="true" />{copied ? t('wa.setup.copied') : t('wa.setup.copy')}</button>
      </div>
      <p className="muted waFootnote">{t('wa.setup.webhookHelp')}</p>
      {account.configured && account.can_manage && <div className="actions">
        <button type="button" className="secondary" disabled={busy !== null} onClick={check}><RefreshCw aria-hidden="true" />{busy === 'check' ? t('wa.setup.checking') : t('wa.setup.check')}</button>
        <button type="button" className="secondary" disabled={busy !== null || !account.owner_test_number} title={account.owner_test_number ? undefined : t('wa.setup.testNeedsNumber')} onClick={test}><Send aria-hidden="true" className="flipRtl" />{busy === 'test' ? t('wa.compose.sending') : t('wa.setup.test')}</button>
        <button type="button" className="danger" disabled={busy !== null} onClick={remove}><Trash2 aria-hidden="true" />{t('wa.setup.disconnect')}</button>
      </div>}
      {notice && <p className={`notice ${notice.tone === 'error' ? 'errorNotice' : ''}`} role={notice.tone === 'error' ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{notice.text}</p>}
    </section>

    {account.can_manage
      ? <form className="surface waSetupForm" onSubmit={save} aria-labelledby="wa-setup-form">
        <h2 id="wa-setup-form">{account.configured ? t('wa.setup.editTitle') : t('wa.setup.connectTitle')}</h2>
        <p className="muted">{t('wa.setup.intro')}</p>
        <div className="waFormGrid">
          <label className="field" htmlFor={ids.phone}><span>{t('wa.setup.phoneId')}</span><input id={ids.phone} dir="ltr" inputMode="numeric" autoComplete="off" value={form.phoneNumberId} onChange={(e) => set({ phoneNumberId: e.target.value })} required /></label>
          <label className="field" htmlFor={ids.waba}><span>{t('wa.setup.wabaId')}</span><input id={ids.waba} dir="ltr" inputMode="numeric" autoComplete="off" value={form.businessAccountId} onChange={(e) => set({ businessAccountId: e.target.value })} required /></label>
          <label className="field" htmlFor={ids.token}><span>{t('wa.setup.token')}</span><input id={ids.token} dir="ltr" type="password" autoComplete="off" value={form.accessToken} placeholder={account.access_token_hint ? t('wa.setup.keep', { hint: account.access_token_hint }) : ''} onChange={(e) => set({ accessToken: e.target.value })} /></label>
          <label className="field" htmlFor={ids.secret}><span>{t('wa.setup.secret')}</span><input id={ids.secret} dir="ltr" type="password" autoComplete="off" value={form.appSecret} placeholder={account.app_secret_set ? t('wa.setup.keepSaved') : ''} onChange={(e) => set({ appSecret: e.target.value })} /></label>
          <label className="field" htmlFor={ids.verify}><span>{t('wa.setup.verify')}</span>
            <span className="waInputRow">
              <input id={ids.verify} dir="ltr" autoComplete="off" value={form.verifyToken} placeholder={account.verify_token_set ? t('wa.setup.keepSaved') : ''} onChange={(e) => set({ verifyToken: e.target.value })} />
              <button type="button" className="secondary" onClick={() => set({ verifyToken: randomVerifyToken((n) => crypto.getRandomValues(new Uint8Array(n))) })}>{t('wa.setup.generate')}</button>
            </span>
          </label>
          <label className="field" htmlFor={ids.test}><span>{t('wa.setup.testNumber')}</span><input id={ids.test} dir="ltr" inputMode="tel" autoComplete="off" value={form.ownerTestNumber} placeholder="9665XXXXXXXX" onChange={(e) => set({ ownerTestNumber: e.target.value })} /></label>
          <label className="field" htmlFor={ids.retention}><span>{t('wa.setup.retention')}</span>
            <select id={ids.retention} aria-describedby={ids.retentionNote} value={form.retention} onChange={(e) => set({ retention: e.target.value })}>
              <option value="">{t('wa.setup.retention.default', { value: retentionLabel(account.default_message_retention_days ?? 0) })}</option>
              {[...new Set([...RETENTION_CHOICES, ...(form.retention ? [Number(form.retention)] : [])])].sort((a, b) => a - b).map((days) => <option key={days} value={String(days)}>{retentionLabel(days)}</option>)}
            </select>
          </label>
        </div>
        <p className="muted waFootnote" id={ids.retentionNote}>{t('wa.setup.retentionNote')}</p>
        <label className="waCheck"><input type="checkbox" checked={form.triageEnabled} onChange={(e) => set({ triageEnabled: e.target.checked })} /><span>{t('wa.setup.triage')}</span></label>
        <p className="muted waFootnote">{t('wa.setup.secretsNote')}</p>
        <div className="actions"><button type="submit" disabled={busy !== null}><Plug aria-hidden="true" />{busy === 'save' ? t('wa.setup.saving') : account.configured ? t('wa.setup.save') : t('wa.setup.connect')}</button></div>
      </form>
      : <section className="surface waPanel" aria-labelledby="wa-setup-owner"><Lock aria-hidden="true" className="waPanelIcon" /><div><h2 id="wa-setup-owner">{t('wa.setup.ownerOnlyTitle')}</h2><p className="muted">{t('wa.setup.ownerOnly')}</p></div></section>}
  </div>;
}
