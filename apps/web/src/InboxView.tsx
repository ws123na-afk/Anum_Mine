// Unified inbox (docs/email.md): Gmail, Outlook and Zoho Mail mailboxes connected to this workspace,
// one list with filters and search, the thread with its text and attachments, AI triage and reply
// drafts, and sending only after an owner approves the exact message (payload hash shown here).
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  Archive, ArrowLeft, CheckCircle2, CircleDot, Download, Fingerprint, Inbox, Lock, Mail, MailOpen, Paperclip, Plug, RefreshCw, Search, Send, Sparkles, Trash2, Unplug,
} from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { expiryLabel, shortHash } from './lib/approvals';
import {
  CATEGORIES, CONNECT_STORAGE_KEY, EMPTY_FILTERS, MAX_DRAFT_CHARS, MAX_INSTRUCTION_CHARS, PRIORITIES,
  accountStatus, categoryLabel, connectErrorText, draftProvenance, draftStatusLabel, formatSize, hasFilters, isStale, parseRecipients,
  priorityLabel, priorityTone, senderName, syncedLabel,
  type EmailAccount, type EmailDraft, type EmailMessage, type EmailProvider, type EmailSetup, type Filters, type MessageDetail,
} from './lib/email';
import {
  analyzeEmail, archiveEmail, completeEmailConnect, createEmailDraft, disconnectEmailAccount, discardEmailDraft, downloadAttachment, getEmailMessage,
  getEmailSetup, listEmailAccounts, listEmailMessages, markEmailRead, requestSendApproval, startEmailConnect, syncEmailAccount, toApproval, updateEmailDraft,
} from './lib/emailApi';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { useIsOwner } from './useIsOwner';
import './inbox.css';

type LoadState = 'loading' | 'ready' | 'error' | 'denied';

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'} aria-live={error ? undefined : 'polite'}><CircleDot aria-hidden="true" />{children}</p>;
}

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function InboxView({ onOpenBudgets, onOpenApprovals }: { onOpenBudgets?: () => void; onOpenApprovals?: () => void } = {}) {
  const owner = useIsOwner();
  const [setup, setSetup] = useState<EmailSetup | null>(null);
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [budget, setBudget] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [queryDraft, setQueryDraft] = useState('');
  const [items, setItems] = useState<EmailMessage[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [listState, setListState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [listError, setListError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [privateConnect, setPrivateConnect] = useState(false);
  const autoSynced = useRef(false);

  const fail = useCallback((e: unknown, prefix?: string) => {
    if (isBudgetExceeded(e)) { setBudget(budgetMessage(e)); return; }
    const text = describeError(e);
    setNotice({ text: prefix ? `${prefix} ${text}` : text, error: true });
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([getEmailSetup(), listEmailAccounts()]);
      setSetup(s); setAccounts(list); setState('ready'); setError(null);
      return list;
    } catch (e) {
      if (isPermissionDenied(e)) { setState('denied'); return []; }
      setState('error'); setError(describeError(e));
      return [];
    }
  }, []);

  const loadMessages = useCallback(async (next: Filters, before: string | null = null) => {
    if (!before) setListState('loading');
    try {
      const page = await listEmailMessages(next, before);
      setItems((current) => before ? [...current, ...page.items] : page.items);
      setCursor(page.next_cursor);
      setListState('ready'); setListError(null);
    } catch (e) {
      if (before) { fail(e, t('email.list.moreFailed')); return; }
      setListState('error'); setListError(describeError(e));
    }
  }, [fail]);

  // A connection the provider just sent the browser back with (captured in main.tsx).
  useEffect(() => {
    let raw: string | null = null;
    try { raw = sessionStorage.getItem(CONNECT_STORAGE_KEY); sessionStorage.removeItem(CONNECT_STORAGE_KEY); } catch { raw = null; }
    if (!raw) return;
    const pending = JSON.parse(raw) as { connection: string | null; error: string | null };
    if (pending.error) { setNotice({ text: connectErrorText(pending.error), error: true }); return; }
    if (!pending.connection) return;
    setBusy('connect');
    void completeEmailConnect(pending.connection)
      .then((account) => { setNotice({ text: t('email.connect.done', { address: account.address }) }); return loadAccounts().then(() => loadMessages(EMPTY_FILTERS)); })
      .catch((e) => fail(e, t('email.connect.failed')))
      .finally(() => setBusy(null));
  }, [fail, loadAccounts, loadMessages]);

  useEffect(() => {
    void loadAccounts().then((list) => {
      void loadMessages(EMPTY_FILTERS);
      // Bring stale mailboxes up to date once when the page opens.
      if (autoSynced.current) return;
      autoSynced.current = true;
      const stale = list.filter((account) => isStale(account));
      if (!stale.length) return;
      void Promise.allSettled(stale.map((account) => syncEmailAccount(account.id))).then(async (results) => {
        const fresh = results.flatMap((r) => r.status === 'fulfilled' ? [r.value] : []);
        if (fresh.length) setAccounts((current) => current.map((a) => fresh.find((f) => f.id === a.id) ?? a));
        await loadMessages(EMPTY_FILTERS);
      });
    });
  }, [loadAccounts, loadMessages]);

  function applyFilters(next: Filters) {
    setFilters(next);
    setSelected(null);
    void loadMessages(next);
  }

  function search(event: FormEvent) {
    event.preventDefault();
    applyFilters({ ...filters, query: queryDraft });
  }

  async function connect(provider: EmailProvider) {
    setBusy(`connect:${provider}`); setNotice(null);
    try {
      const started = await startEmailConnect(provider, privateConnect ? 'private' : 'workspace');
      window.location.assign(started.authorization_url);
    } catch (e) { fail(e, t('email.connect.failed')); setBusy(null); }
  }

  async function sync(account: EmailAccount) {
    setBusy(`sync:${account.id}`); setNotice(null);
    try {
      const updated = await syncEmailAccount(account.id);
      setAccounts((current) => current.map((a) => a.id === updated.id ? updated : a));
      setNotice(updated.last_sync_status === 'error' ? { text: t('email.account.syncFailed', { address: updated.address, reason: updated.last_error ?? '' }), error: true } : { text: t('email.account.synced', { address: updated.address }) });
      await loadMessages(filters);
    } catch (e) { fail(e, t('email.account.syncFailedShort')); } finally { setBusy(null); }
  }

  async function disconnect(account: EmailAccount) {
    if (!window.confirm(t('email.account.disconnectConfirm', { address: account.address }))) return;
    setBusy(`disconnect:${account.id}`); setNotice(null);
    try {
      const result = await disconnectEmailAccount(account.id);
      setNotice({ text: result.detail });
      setSelected(null);
      await loadAccounts();
      await loadMessages(filters);
    } catch (e) { fail(e, t('email.account.disconnectFailed')); } finally { setBusy(null); }
  }

  const replaceMessage = (message: EmailMessage) => setItems((current) => current.map((m) => m.id === message.id ? message : m));

  if (state === 'loading' && !setup) return <p className="notice" role="status" aria-busy="true"><CircleDot aria-hidden="true" />{t('email.loading')}</p>;
  if (state === 'denied') return <section className="surface inboxPanel" aria-labelledby="inbox-denied"><Lock aria-hidden="true" className="inboxPanelIcon" /><div><h2 id="inbox-denied">{t('email.denied.title')}</h2><p className="muted">{t('email.denied.text')}</p></div></section>;
  if (state === 'error') return <div className="inboxLayout"><Notice error>{t('email.loadFailed', { reason: error ?? '' })}</Notice><div className="actions"><button type="button" onClick={() => { setState('loading'); void loadAccounts().then(() => loadMessages(filters)); }}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div></div>;

  const canConnect = Boolean(setup?.can_connect);
  const canAct = Boolean(setup?.can_act);
  const connectable = setup?.providers ?? [];
  const showDetail = selected !== null;

  return <div className="inboxLayout">
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('email.eyebrow')}</p><h2>{t('email.title')}</h2><p>{t('email.intro')}</p></div>
      <div className="actions"><button type="button" className="secondary" disabled={!accounts.length || busy !== null} onClick={() => void Promise.all(accounts.map((a) => syncEmailAccount(a.id).catch(() => a))).then((fresh) => { setAccounts(fresh); return loadMessages(filters); })}><RefreshCw aria-hidden="true" />{t('email.syncAll')}</button></div>
    </section>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('common.budgetReached', { message: budget })}{onOpenBudgets && <button type="button" className="secondary inboxInlineButton" onClick={onOpenBudgets}>{t('email.openBudgets')}</button>}</p>}

    <section className="surface inboxAccounts" aria-labelledby="inbox-accounts">
      <div className="sectionHeader"><h2 id="inbox-accounts">{t('email.accounts.title')}</h2>{busy === 'connect' && <span className="statusBadge neutral">{t('email.connect.finishing')}</span>}</div>
      {accounts.length === 0 ? <p className="muted">{canConnect ? t('email.accounts.emptyOwner') : t('email.accounts.emptyMember')}</p> :
        <ul className="inboxAccountList">{accounts.map((account) => {
          const status = accountStatus(account);
          return <li key={account.id} className="inboxAccount">
            <Mail aria-hidden="true" />
            <div className="inboxAccountText">
              <strong>{account.address}</strong>
              <small>{account.provider_name} · {account.visibility === 'private' ? t('email.account.private') : t('email.account.shared')} · {syncedLabel(account.last_sync_at)} · {t('email.account.unread', { count: account.unread_count })}</small>
              {status.detail && <small className="inboxAccountError">{status.detail}</small>}
            </div>
            <span className={`statusBadge ${status.tone}`}>{account.syncing ? t('email.account.syncing') : status.label}</span>
            <div className="inboxAccountActions">
              {account.status === 'reauth_required' && canConnect ? <button type="button" className="secondary" disabled={busy !== null} onClick={() => void connect(account.provider)}><Plug aria-hidden="true" />{t('email.account.reconnect')}</button>
                : <button type="button" className="secondary" disabled={busy !== null} aria-label={t('email.account.syncLabel', { address: account.address })} onClick={() => void sync(account)}><RefreshCw aria-hidden="true" />{busy === `sync:${account.id}` ? t('email.account.syncingNow') : t('email.account.sync')}</button>}
              {account.can_manage && <button type="button" className="secondary" disabled={busy !== null} aria-label={t('email.account.disconnectLabel', { address: account.address })} onClick={() => void disconnect(account)}><Unplug aria-hidden="true" />{t('email.account.disconnect')}</button>}
            </div>
          </li>;
        })}</ul>}
      {canConnect ? <div className="inboxConnect">
        <div className="inboxConnectButtons">{connectable.map((provider) => provider.configured && provider.enabled_here
          ? <button key={provider.provider} type="button" disabled={busy !== null} onClick={() => void connect(provider.provider)}><Plug aria-hidden="true" />{t('email.connect.button', { provider: provider.name })}</button>
          : <p key={provider.provider} className="muted inboxSetupNote"><strong>{provider.name}:</strong> {provider.configured ? t('email.connect.offHere') : t('email.connect.notSetUp', { settings: provider.missing.join(', ') })}</p>)}</div>
        <label className="inboxCheck"><input type="checkbox" checked={privateConnect} onChange={(e) => setPrivateConnect(e.target.checked)} />{t('email.connect.private')}</label>
        <small className="muted">{t('email.connect.help')}</small>
      </div> : accounts.length > 0 ? null : <p className="muted">{t('email.connect.ownerOnly')}</p>}
    </section>

    {accounts.length > 0 && <>
      <form className="surface inboxFilters" role="search" aria-label={t('email.filters.label')} onSubmit={search}>
        <label className="inboxSearch"><span className="visuallyHidden">{t('email.filters.search')}</span><input type="search" value={queryDraft} placeholder={t('email.filters.searchPlaceholder')} maxLength={200} onChange={(e) => setQueryDraft(e.target.value)} /></label>
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
        <label><span className="visuallyHidden">{t('email.filters.account')}</span><select value={filters.accountId} onChange={(e) => applyFilters({ ...filters, accountId: e.target.value })}><option value="">{t('email.filters.allAccounts')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.address}</option>)}</select></label>
        <label><span className="visuallyHidden">{t('email.filters.category')}</span><select value={filters.category} onChange={(e) => applyFilters({ ...filters, category: e.target.value as Filters['category'] })}><option value="">{t('email.filters.allCategories')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
        <label><span className="visuallyHidden">{t('email.filters.priority')}</span><select value={filters.priority} onChange={(e) => applyFilters({ ...filters, priority: e.target.value as Filters['priority'] })}><option value="">{t('email.filters.allPriorities')}</option>{PRIORITIES.map((p) => <option key={p} value={p}>{priorityLabel(p)}</option>)}</select></label>
        <label className="inboxCheck"><input type="checkbox" checked={filters.unread} onChange={(e) => applyFilters({ ...filters, unread: e.target.checked })} />{t('email.filters.unread')}</label>
        <label className="inboxCheck"><input type="checkbox" checked={filters.archived} onChange={(e) => applyFilters({ ...filters, archived: e.target.checked })} />{t('email.filters.archived')}</label>
        {hasFilters(filters) && <button type="button" className="secondary" onClick={() => { setQueryDraft(''); applyFilters(EMPTY_FILTERS); }}>{t('common.clear')}</button>}
      </form>

      <div className={`inboxSplit ${showDetail ? 'showDetail' : ''}`}>
        <section className="surface inboxList" aria-labelledby="inbox-list-title" aria-busy={listState === 'loading'}>
          <h2 id="inbox-list-title" className="visuallyHidden">{t('email.list.title')}</h2>
          {listState === 'loading' && <p className="muted inboxPad" role="status">{t('email.list.loading')}</p>}
          {listState === 'error' && <div className="inboxPad"><Notice error>{t('email.list.failed', { reason: listError ?? '' })}</Notice><div className="actions"><button type="button" onClick={() => void loadMessages(filters)}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div></div>}
          {listState === 'ready' && items.length === 0 && <div className="emptyState"><Inbox aria-hidden="true" />{hasFilters(filters)
            ? <><h3>{t('email.list.noMatch')}</h3><p>{t('email.list.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={() => { setQueryDraft(''); applyFilters(EMPTY_FILTERS); }}>{t('email.list.clearFilters')}</button></div></>
            : <><h3>{t('email.list.empty')}</h3><p>{t('email.list.emptyText')}</p></>}</div>}
          {items.length > 0 && <ul className="inboxMessages">{items.map((message) => <li key={message.id}>
            <button type="button" className={`inboxRow ${selected === message.id ? 'selected' : ''} ${message.is_read ? '' : 'unread'}`} aria-current={selected === message.id ? 'true' : undefined} onClick={() => setSelected(message.id)}>
              <span className="inboxRowTop"><strong>{senderName(message)}</strong><time dateTime={message.received_at}>{when(message.received_at)}</time></span>
              <span className="inboxRowSubject" dir="auto">{!message.is_read && <span className="inboxUnreadDot" aria-label={t('email.list.unreadMark')} />}{message.subject || t('email.noSubject')}</span>
              <span className="inboxRowSnippet" dir="auto">{message.summary || message.snippet}</span>
              <span className="inboxBadges"><span className="statusBadge neutral">{categoryLabel(message.category)}</span>{message.priority && <span className={`statusBadge ${priorityTone(message.priority)}`}>{priorityLabel(message.priority)}</span>}{message.has_attachments && <Paperclip aria-label={t('email.hasAttachments')} />}</span>
            </button>
          </li>)}</ul>}
          {cursor && <div className="actions inboxPad"><button type="button" className="secondary" disabled={busy === 'more'} onClick={() => { setBusy('more'); void loadMessages(filters, cursor).finally(() => setBusy(null)); }}>{t('email.list.more')}</button></div>}
        </section>
        <section className="surface inboxDetail" aria-label={t('email.detail.label')}>
          {selected
            ? <MessagePanel key={selected} messageId={selected} canAct={canAct} owner={owner === true} onBack={() => setSelected(null)} onChanged={replaceMessage} onSelect={setSelected} onBudget={setBudget} onOpenApprovals={onOpenApprovals} />
            : <div className="emptyState"><MailOpen aria-hidden="true" /><h3>{t('email.detail.none')}</h3><p>{t('email.detail.noneText')}</p></div>}
        </section>
      </div>
    </>}
  </div>;
}

function MessagePanel({ messageId, canAct, owner, onBack, onChanged, onSelect, onBudget, onOpenApprovals }: {
  messageId: string; canAct: boolean; owner: boolean; onBack: () => void; onChanged: (message: EmailMessage) => void; onSelect: (id: string) => void;
  onBudget: (message: string) => void; onOpenApprovals?: () => void;
}) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [suggested, setSuggested] = useState<{ reply: string; model: string } | null>(null);
  const [instructions, setInstructions] = useState('');
  const [manual, setManual] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const instructionsId = useId();

  const load = useCallback(async () => {
    setState('loading');
    try { const found = await getEmailMessage(messageId); setDetail(found); setState('ready'); setError(null); }
    catch (e) { setState('error'); setError(describeError(e)); }
  }, [messageId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (state === 'ready') heading.current?.focus({ preventScroll: false }); }, [state]);

  async function act(kind: string, fn: () => Promise<void>) {
    setBusy(kind); setNotice(null);
    try { await fn(); }
    catch (e) { if (isBudgetExceeded(e)) onBudget(budgetMessage(e)); else setNotice({ text: isPermissionDenied(e) ? t('email.detail.notAllowed', { reason: describeError(e) }) : describeError(e), error: true }); }
    finally { setBusy(null); }
  }

  if (state === 'loading' && !detail) return <p className="muted inboxPad" role="status">{t('email.detail.loading')}</p>;
  if (state === 'error' || !detail) return <div className="inboxPad"><Notice error>{t('email.detail.failed', { reason: error ?? '' })}</Notice><div className="actions"><button type="button" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button><button type="button" className="secondary" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('email.detail.back')}</button></div></div>;

  const message = detail.message;
  const update = (next: EmailMessage) => { setDetail((current) => current ? { ...current, message: next } : current); onChanged(next); };
  const setDrafts = (fn: (drafts: EmailDraft[]) => EmailDraft[]) => setDetail((current) => current ? { ...current, drafts: fn(current.drafts) } : current);
  const actionResult = (result: { status: string; summary: string; message: EmailMessage | null }) => {
    if (result.message) update(result.message);
    if (result.status === 'waiting_approval') setNotice({ text: t('email.detail.waitingApproval') });
    else if (result.status === 'failed') setNotice({ text: result.summary, error: true });
  };

  return <article className="inboxMessage" aria-labelledby="inbox-message-subject">
    <div className="inboxMessageBar">
      <button type="button" className="secondary inboxBack" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('email.detail.back')}</button>
      {canAct && <div className="inboxMessageActions">
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('read', async () => actionResult(await markEmailRead(message.id, !message.is_read)))}>{message.is_read ? <Mail aria-hidden="true" /> : <MailOpen aria-hidden="true" />}{message.is_read ? t('email.detail.markUnread') : t('email.detail.markRead')}</button>
        {!message.is_archived && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('archive', async () => actionResult(await archiveEmail(message.id)))}><Archive aria-hidden="true" />{t('email.detail.archive')}</button>}
      </div>}
    </div>
    <header className="inboxMessageHeader">
      <h2 id="inbox-message-subject" ref={heading} tabIndex={-1} dir="auto">{message.subject || t('email.noSubject')}</h2>
      <dl className="inboxMeta">
        <div><dt>{t('email.detail.from')}</dt><dd>{message.from_name ? `${message.from_name} <${message.from_address}>` : message.from_address}</dd></div>
        <div><dt>{t('email.detail.to')}</dt><dd>{message.to.join(', ') || message.account_address}</dd></div>
        {message.cc.length > 0 && <div><dt>{t('email.detail.cc')}</dt><dd>{message.cc.join(', ')}</dd></div>}
        <div><dt>{t('email.detail.received')}</dt><dd><time dateTime={message.received_at}>{when(message.received_at)}</time></dd></div>
      </dl>
      <div className="inboxBadges"><span className="statusBadge neutral">{categoryLabel(message.category)}</span>{message.priority && <span className={`statusBadge ${priorityTone(message.priority)}`}>{priorityLabel(message.priority)}</span>}<small className="muted">{message.triage_source === 'model' ? t('email.detail.triageModel', { model: message.triage_model ?? '' }) : t('email.detail.triageRules')}</small></div>
    </header>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}

    {message.summary && <section className="inboxSummary" aria-label={t('email.detail.summaryLabel')}><Sparkles aria-hidden="true" /><div><strong>{t('email.detail.summary')}</strong><p dir="auto">{message.summary}</p></div></section>}

    <section className="inboxBody" aria-label={t('email.detail.bodyLabel')}>
      <p className="inboxUntrusted"><Fingerprint aria-hidden="true" />{t('email.detail.untrusted')}</p>
      {detail.body_error ? <><Notice error>{t('email.detail.bodyFailed', { reason: detail.body_error })}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div></>
        : <div className="inboxBodyText" dir="auto">{detail.body || t('email.detail.emptyBody')}</div>}
      {detail.body_truncated && <p className="muted">{t('email.detail.truncated')}</p>}
      {detail.attachments.length > 0 && <ul className="inboxAttachments" aria-label={t('email.detail.attachments')}>{detail.attachments.map((file) => <li key={file.id}>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act(`file:${file.id}`, () => downloadAttachment(message.id, file.id, file.filename))}><Download aria-hidden="true" />{file.filename}{file.size ? ` · ${formatSize(file.size)}` : ''}</button>
      </li>)}</ul>}
    </section>

    {detail.thread.length > 1 && <section className="inboxThread" aria-labelledby="inbox-thread-title"><h3 id="inbox-thread-title">{t('email.detail.thread', { count: detail.thread.length })}</h3><ol>{detail.thread.map((item) => <li key={item.id}>
      {item.id === message.id ? <span className="inboxThreadCurrent">{senderName(item)} · {when(item.received_at)}</span> : <button type="button" className="inboxThreadLink" onClick={() => onSelect(item.id)}>{senderName(item)} · {when(item.received_at)}</button>}
    </li>)}</ol></section>}

    {canAct && <section className="inboxReply" aria-labelledby="inbox-reply-title">
      <h3 id="inbox-reply-title">{t('email.reply.title')}</h3>
      <div className="actions inboxReplyActions">
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('analyze', async () => { const result = await analyzeEmail(message.id); update(result.message); setSuggested({ reply: result.suggested_reply, model: result.model }); })}><Sparkles aria-hidden="true" />{busy === 'analyze' ? t('email.reply.analyzing') : t('email.reply.analyze')}</button>
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => setManual(true)}>{t('email.reply.writeOwn')}</button>
      </div>
      {suggested && <div className="inboxSuggestion"><p className="muted">{t('email.reply.suggestedBy', { model: suggested.model })}</p>{suggested.reply ? <><div className="inboxBodyText" dir="auto">{suggested.reply}</div><div className="actions"><button type="button" disabled={busy !== null} onClick={() => void act('use', async () => { const result = await createEmailDraft(message.id, { body: suggested.reply }); if (result.draft) setDrafts((d) => [result.draft!, ...d]); setNotice(result.status === 'waiting_approval' ? { text: t('email.detail.waitingApproval') } : result.warning ? { text: result.warning } : null); setSuggested(null); })}>{t('email.reply.useSuggestion')}</button></div></> : <p>{t('email.reply.noReplyNeeded')}</p>}</div>}
      <form className="inboxDraftForm" onSubmit={(e) => { e.preventDefault(); void act('draft', async () => { const result = await createEmailDraft(message.id, instructions.trim() ? { instructions: instructions.trim() } : {}); if (result.draft) setDrafts((d) => [result.draft!, ...d]); setInstructions(''); setNotice(result.status === 'waiting_approval' ? { text: t('email.detail.waitingApproval') } : result.warning ? { text: result.warning } : null); }); }}>
        <label className="field" htmlFor={instructionsId}><span>{t('email.reply.instructions')}</span></label>
        <div className="inboxDraftRow"><input id={instructionsId} value={instructions} maxLength={MAX_INSTRUCTION_CHARS} placeholder={t('email.reply.instructionsPlaceholder')} onChange={(e) => setInstructions(e.target.value)} /><button type="submit" disabled={busy !== null}><Sparkles aria-hidden="true" />{busy === 'draft' ? t('email.reply.drafting') : t('email.reply.draftWithAi')}</button></div>
      </form>
      {manual && <NewDraft messageId={message.id} onCancel={() => setManual(false)} onCreated={(draft, warning) => { setDrafts((d) => [draft, ...d]); setManual(false); if (warning) setNotice({ text: warning }); }} onError={(e) => setNotice({ text: describeError(e), error: true })} />}
      {detail.drafts.length === 0 && !manual && <p className="muted">{t('email.reply.noDrafts')}</p>}
      <ul className="inboxDrafts">{detail.drafts.map((draft) => <li key={draft.id}><DraftCard draft={draft} owner={owner} onChange={(next) => setDrafts((d) => next ? d.map((x) => x.id === next.id ? next : x) : d.filter((x) => x.id !== draft.id))} onSent={() => void load()} onOpenApprovals={onOpenApprovals} /></li>)}</ul>
    </section>}
  </article>;
}

function NewDraft({ messageId, onCancel, onCreated, onError }: { messageId: string; onCancel: () => void; onCreated: (draft: EmailDraft, warning: string | null) => void; onError: (e: unknown) => void }) {
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const id = useId();
  return <form className="inboxDraftCard" onSubmit={(e) => { e.preventDefault(); if (!body.trim()) return; setBusy(true); void createEmailDraft(messageId, { body }).then((result) => { if (result.draft) onCreated(result.draft, result.warning); }).catch(onError).finally(() => setBusy(false)); }}>
    <label className="field" htmlFor={id}><span>{t('email.reply.yourReply')}</span></label>
    <textarea id={id} dir="auto" value={body} maxLength={MAX_DRAFT_CHARS} onChange={(e) => setBody(e.target.value)} />
    <div className="actions"><button type="submit" disabled={busy || !body.trim()}>{t('email.reply.saveDraft')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
  </form>;
}

function DraftCard({ draft, owner, onChange, onSent, onOpenApprovals }: { draft: EmailDraft; owner: boolean; onChange: (draft: EmailDraft | null) => void; onSent: () => void; onOpenApprovals?: () => void }) {
  const [to, setTo] = useState(draft.to.join(', '));
  const [cc, setCc] = useState(draft.cc.join(', '));
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const ids = { to: useId(), cc: useId(), subject: useId(), body: useId() };
  const editable = draft.status === 'draft' || draft.status === 'failed' || draft.status === 'pending_approval';
  const dirty = to !== draft.to.join(', ') || cc !== draft.cc.join(', ') || subject !== draft.subject || body !== draft.body;
  const approval: Approval | null = useMemo(() => draft.approval ? toApproval(draft.approval) : null, [draft.approval]);

  async function run(kind: string, fn: () => Promise<void>) {
    setBusy(kind); setNotice(null);
    try { await fn(); } catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }
  async function save(): Promise<EmailDraft | null> {
    const toList = parseRecipients(to), ccList = parseRecipients(cc);
    if (!toList.ok) { setNotice({ text: toList.error, error: true }); return null; }
    if (!ccList.ok) { setNotice({ text: ccList.error, error: true }); return null; }
    if (!toList.value.length) { setNotice({ text: t('email.draft.needRecipient'), error: true }); return null; }
    const saved = await updateEmailDraft(draft.id, { to: toList.value, cc: ccList.value, subject, body });
    onChange(saved);
    return saved;
  }

  if (draft.status === 'sent') return <div className="inboxDraftCard sent"><p><CheckCircle2 aria-hidden="true" /> {t('email.draft.sentTo', { to: draft.to.join(', '), when: draft.sent_at ? when(draft.sent_at) : '' })}</p><div className="inboxBodyText" dir="auto">{draft.body}</div></div>;

  return <div className="inboxDraftCard">
    <div className="sectionHeader"><span className="statusBadge neutral">{draftStatusLabel(draft.status)}</span><small className="muted">{draftProvenance(draft)}{draft.saved_in_provider ? ` · ${t('email.draft.savedInProvider')}` : ''}</small></div>
    {draft.error && <Notice error>{t('email.draft.lastError', { reason: draft.error })}</Notice>}
    {draft.approval_note && <Notice>{draft.approval_note}</Notice>}
    <div className="inboxDraftFields">
      <label className="field" htmlFor={ids.to}><span>{t('email.draft.to')}</span><input id={ids.to} value={to} disabled={!editable} onChange={(e) => setTo(e.target.value)} /></label>
      <label className="field" htmlFor={ids.cc}><span>{t('email.draft.cc')}</span><input id={ids.cc} value={cc} disabled={!editable} onChange={(e) => setCc(e.target.value)} /></label>
      <label className="field inboxWide" htmlFor={ids.subject}><span>{t('email.draft.subject')}</span><input id={ids.subject} dir="auto" value={subject} maxLength={500} disabled={!editable} onChange={(e) => setSubject(e.target.value)} /></label>
      <label className="field inboxWide" htmlFor={ids.body}><span>{t('email.draft.body')}</span><textarea id={ids.body} dir="auto" value={body} maxLength={MAX_DRAFT_CHARS} disabled={!editable} onChange={(e) => setBody(e.target.value)} /></label>
    </div>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {approval && approval.status === 'pending' ? <div className="inboxApproval" role="group" aria-label={t('email.approval.label')}>
      <p><Send aria-hidden="true" /> <strong>{t('email.approval.title')}</strong></p>
      <dl className="inboxMeta">
        <div><dt>{t('email.detail.from')}</dt><dd>{String(approval.arguments?.from ?? '')}</dd></div>
        <div><dt>{t('email.detail.to')}</dt><dd>{(approval.arguments?.to as string[] | undefined)?.join(', ')}</dd></div>
        <div><dt>{t('approvals.payloadHash')}</dt><dd><code title={approval.payloadHash ?? ''}>{shortHash(approval.payloadHash)}</code></dd></div>
        <div><dt>{t('email.approval.expires')}</dt><dd>{expiryLabel(approval) || '-'}</dd></div>
      </dl>
      {dirty && <p className="muted">{t('email.approval.dirty')}</p>}
      <div className="actions">
        {owner ? <button type="button" disabled={busy !== null || dirty} onClick={() => void run('approve', async () => { const result = await api.approveTask(approval); onSent(); setNotice(result.run?.result ? { text: result.run.result } : null); })}><Send aria-hidden="true" />{busy === 'approve' ? t('email.approval.sending') : t('email.approval.approve')}</button>
          : <p className="muted">{t('email.approval.ownerOnly')}</p>}
        {onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('email.approval.openApprovals')}</button>}
      </div>
    </div> : null}
    {editable && <div className="actions">
      <button type="button" className="secondary" disabled={busy !== null || !dirty} onClick={() => void run('save', async () => { if (await save()) setNotice({ text: t('email.draft.saved') }); })}>{t('email.draft.save')}</button>
      {(!approval || approval.status !== 'pending' || dirty) && <button type="button" disabled={busy !== null || !body.trim()} onClick={() => void run('ask', async () => { const current = dirty ? await save() : draft; if (!current) return; const result = await requestSendApproval(current.id); onChange(result.draft); })}><Send aria-hidden="true" />{t('email.draft.askToSend')}</button>}
      <button type="button" className="secondary danger" disabled={busy !== null} onClick={() => { if (!window.confirm(t('email.draft.discardConfirm'))) return; void run('discard', async () => { await discardEmailDraft(draft.id); onChange(null); }); }}><Trash2 aria-hidden="true" />{t('email.draft.discard')}</button>
    </div>}
  </div>;
}
