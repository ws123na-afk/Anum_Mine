// Unified inbox (docs/email.md): Gmail, Outlook and Zoho Mail mailboxes connected to this workspace,
// one list with filters and search, the thread with its text and attachments, AI triage and reply
// drafts, and sending only after an owner approves the exact message (payload hash shown here).
// The page: a summary band (GET /email/summary and /email/drafts), the mailbox setup behind a status
// chip, and three panes that fill the window (filters and the drafts waiting, the list, the message).
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  Archive, ArrowLeft, CheckCircle2, CircleDot, Download, Fingerprint, Inbox, LifeBuoy, Lock, Mail, MailOpen, Paperclip, PenLine, SquarePen, Plug, RefreshCw, Search, Send, Sparkles, Trash2, Unplug,
} from 'lucide-react';
import type { Approval, TicketDetail } from '@anum/contracts';
import * as api from './lib/api';
import { expiryLabel, shortHash } from './lib/approvals';
import {
  CATEGORIES, CONNECT_STORAGE_KEY, EMPTY_COMPOSE, EMPTY_FILTERS, MAX_DRAFT_CHARS, MAX_INSTRUCTION_CHARS, MAX_SUBJECT_CHARS, PRIORITIES,
  accountStatus, categoryLabel, composeMailboxes, composeRequest, connectErrorText, draftProvenance, draftStatusLabel, formatSize, hasFilters, isNewEmailDraft, isStale, parseRecipients,
  priorityLabel, priorityTone, senderName, syncedLabel,
  type ComposeForm, type EmailAccount, type EmailDraft, type EmailMessage, type EmailProvider, type EmailSetup, type Filters, type InboxSummary, type MessageDetail,
} from './lib/email';
import {
  analyzeEmail, archiveEmail, completeEmailConnect, createEmailDraft, createNewEmailDraft, disconnectEmailAccount, discardEmailDraft, downloadAttachment, getEmailMessage, getEmailSummary,
  getEmailSetup, listEmailAccounts, listEmailDrafts, listEmailMessages, markEmailRead, requestSendApproval, startEmailConnect, syncEmailAccount, toApproval, updateEmailDraft,
} from './lib/emailApi';
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import { figure, firstUnread, waitingDrafts } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { Drawer } from './Dialog';
import { CreateTicketDialog, TicketCreatedNotice } from './TicketFromMessage';
import { PageHeader, PageSummary, RAIL_QUERY, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import { useIsOwner } from './useIsOwner';
import './inbox.css';
import { HelpLink } from './HelpLink';

type LoadState = 'loading' | 'ready' | 'error' | 'denied';
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error' };

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'} aria-live={error ? undefined : 'polite'}><CircleDot aria-hidden="true" />{children}</p>;
}

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function InboxView({ onOpenBudgets, onOpenApprovals, initialMessageId }: { onOpenBudgets?: () => void; onOpenApprovals?: () => void; initialMessageId?: string | null } = {}) {
  const owner = useIsOwner();
  const wide = useMediaQuery(SPLIT_QUERY);
  const widest = useMediaQuery(RAIL_QUERY);
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
  // A message opened from global search starts selected.
  const [selected, setSelected] = useState<string | null>(initialMessageId ?? null);
  const [picked, setPicked] = useState(() => (initialMessageId ? 1 : 0));
  const [busy, setBusy] = useState<string | null>(null);
  const [privateConnect, setPrivateConnect] = useState(false);
  const [mailboxesOpen, setMailboxesOpen] = useState(false);
  const [summary, setSummary] = useState<Load<InboxSummary>>({ state: 'loading' });
  const [drafts, setDrafts] = useState<Load<EmailDraft[]>>({ state: 'loading' });
  // The compose drawer: open on a blank new email (null draft) or on a new-email draft from the queue.
  const [compose, setCompose] = useState<{ draft: EmailDraft | null } | null>(null);
  const autoSynced = useRef(false);
  const autoPicked = useRef(Boolean(initialMessageId));
  const searchId = useId();

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

  // The summary band: unread and high-priority counts, and the reply drafts still waiting.
  const loadSummary = useCallback(() => {
    getEmailSummary().then((value) => setSummary({ state: 'ready', value }), () => setSummary({ state: 'error' }));
    listEmailDrafts().then((value) => setDrafts({ state: 'ready', value }), () => setDrafts({ state: 'error' }));
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
      if (list.length) loadSummary();
      // Bring stale mailboxes up to date once when the page opens.
      if (autoSynced.current) return;
      autoSynced.current = true;
      const stale = list.filter((account) => isStale(account));
      if (!stale.length) return;
      void Promise.allSettled(stale.map((account) => syncEmailAccount(account.id))).then(async (results) => {
        const fresh = results.flatMap((r) => r.status === 'fulfilled' ? [r.value] : []);
        if (fresh.length) setAccounts((current) => current.map((a) => fresh.find((f) => f.id === a.id) ?? a));
        await loadMessages(EMPTY_FILTERS);
        loadSummary();
      });
    });
  }, [loadAccounts, loadMessages, loadSummary]);

  // On a wide screen the reading pane opens on the first unread message, once.
  useEffect(() => {
    if (!wide || autoPicked.current || selected || listState !== 'ready') return;
    const first = firstUnread(items, (message) => !message.is_read);
    if (!first) return;
    autoPicked.current = true;
    setSelected(first.id);
  }, [wide, selected, listState, items]);

  function applyFilters(next: Filters) {
    setFilters(next);
    setSelected(null);
    autoPicked.current = false;
    void loadMessages(next);
  }

  function search(event: FormEvent) {
    event.preventDefault();
    applyFilters({ ...filters, query: queryDraft });
  }
  function pick(id: string) { setPicked((n) => n + 1); setSelected(id); }

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
      loadSummary();
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
      loadSummary();
    } catch (e) { fail(e, t('email.account.disconnectFailed')); } finally { setBusy(null); }
  }

  const replaceMessage = (message: EmailMessage) => { setItems((current) => current.map((m) => m.id === message.id ? message : m)); loadSummary(); };

  if (state === 'loading' && !setup) return <><PageHeader title={viewLabel('inbox')} subtitle={t('pages.inbox.subtitle')} /><p className="notice" role="status" aria-busy="true"><CircleDot aria-hidden="true" />{t('email.loading')}</p></>;
  if (state === 'denied') return <><PageHeader title={viewLabel('inbox')} subtitle={t('pages.inbox.subtitle')} /><section className="surface inboxPanel" aria-labelledby="inbox-denied"><Lock aria-hidden="true" className="inboxPanelIcon" /><div><h2 id="inbox-denied">{t('email.denied.title')}</h2><p className="muted">{t('email.denied.text')}</p></div></section></>;
  if (state === 'error') return <><PageHeader title={viewLabel('inbox')} subtitle={t('pages.inbox.subtitle')} /><div className="inboxLayout"><Notice error>{t('email.loadFailed', { reason: error ?? '' })}</Notice><div className="actions"><button type="button" onClick={() => { setState('loading'); void loadAccounts().then(() => loadMessages(filters)); }}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div></div></>;

  const canConnect = Boolean(setup?.can_connect);
  const canAct = Boolean(setup?.can_act);
  const attention = accounts.filter((a) => accountStatus(a).tone !== 'success').length;
  const chipTone = !accounts.length ? 'neutral' : attention ? 'warning' : 'success';
  const lastSync = accounts.map((a) => a.last_sync_at).filter((v): v is string => Boolean(v)).sort().at(-1) ?? null;
  const summaryValue = summary.state === 'ready' ? summary.value : null;
  const waiting = drafts.state === 'ready' ? waitingDrafts(drafts.value) : [];
  const pendingApproval = waiting.filter((d) => d.status === 'pending_approval').length;
  const mailboxes = composeMailboxes(accounts);
  const setupPanel = <MailboxSetup accounts={accounts} setup={setup} canConnect={canConnect} busy={busy} privateConnect={privateConnect} onPrivate={setPrivateConnect} onConnect={(p) => void connect(p)} onSync={(a) => void sync(a)} onDisconnect={(a) => void disconnect(a)} />;
  const figures = [
    figure('unread', t('pages.inbox.fig.unread'), summary, () => ({ value: formatNumber(summaryValue!.unread), hint: t('pages.inbox.fig.unreadHint', { count: summaryValue!.accounts }), tone: summaryValue!.unread ? 'accent' : '' })),
    figure('high', t('pages.inbox.fig.high'), summary, () => ({ value: formatNumber(summaryValue!.high_priority_unread), hint: summaryValue!.high_priority_unread ? t('pages.inbox.fig.highHint') : t('pages.inbox.fig.noneHigh'), tone: summaryValue!.high_priority_unread ? 'danger' : '' })),
    figure('customers', t('pages.inbox.fig.customers'), summary, () => ({ value: formatNumber(summaryValue!.by_category.customer_question ?? 0), hint: t('pages.inbox.fig.customersHint', { orders: formatNumber(summaryValue!.by_category.order ?? 0) }) })),
    figure('drafts', t('pages.inbox.fig.drafts'), drafts, () => ({ value: formatNumber(waiting.length), hint: t('pages.inbox.fig.draftsHint', { count: pendingApproval }), tone: pendingApproval ? 'warn' : '' })),
  ];
  const header = <PageHeader title={viewLabel('inbox')} subtitle={t('pages.inbox.subtitle')} actions={<>
    <button type="button" className={`chipButton ${chipTone}`} aria-haspopup="dialog" onClick={() => setMailboxesOpen(true)}>
      <span className="chipDot" aria-hidden="true" /><span>{accounts.length ? t('pages.inbox.mailboxChip', { count: accounts.length, synced: syncedLabel(lastSync) }) : t('pages.inbox.noMailbox')}</span>{attention > 0 && <span className="statusBadge warning">{t('pages.inbox.needsAttention', { count: attention })}</span>}
    </button>
    {canAct && accounts.length > 0 && <button type="button" aria-haspopup="dialog" disabled={!mailboxes.usable.length} title={mailboxes.usable.length ? undefined : t('email.compose.allNeedReconnect')} onClick={() => setCompose({ draft: null })}><SquarePen aria-hidden="true" />{t('email.compose.button')}</button>}
    <button type="button" className="secondary" disabled={!accounts.length || busy !== null} onClick={() => void Promise.all(accounts.map((a) => syncEmailAccount(a.id).catch(() => a))).then((fresh) => { setAccounts(fresh); loadSummary(); return loadMessages(filters); })}><RefreshCw aria-hidden="true" />{t('email.syncAll')}</button>
  </>} />;

  return <div className="inboxLayout">
    {header}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('common.budgetReached', { message: budget })}{onOpenBudgets && <button type="button" className="secondary inboxInlineButton" onClick={onOpenBudgets}>{t('email.openBudgets')}</button>}</p>}
    {mailboxesOpen && <Drawer title={t('email.accounts.title')} onClose={() => setMailboxesOpen(false)}>{setupPanel}</Drawer>}
    {compose && <ComposeDrawer key={compose.draft?.id ?? 'new'} accounts={accounts} initialDraft={compose.draft} owner={owner === true} onClose={() => setCompose(null)} onChanged={loadSummary} onOpenApprovals={onOpenApprovals}
      onDiscarded={() => { setCompose(null); setNotice({ text: t('email.compose.discarded') }); }} />}

    {accounts.length === 0 && <section className="surface inboxStart" aria-labelledby="inbox-start">
      <div className="inboxStartText"><Mail aria-hidden="true" /><h2 id="inbox-start">{t('pages.inbox.startTitle')}</h2><p className="muted">{canConnect ? t('email.accounts.emptyOwner') : t('email.accounts.emptyMember')}</p></div>
      {setupPanel}
    </section>}

    {accounts.length > 0 && <>
      <PageSummary label={t('pages.inbox.summary')} figures={figures} onRetry={() => { setSummary({ state: 'loading' }); setDrafts({ state: 'loading' }); loadSummary(); }} />
      <SplitView className="inboxSplit" show={selected ? 'detail' : 'list'}>
        <aside data-pane="nav" className="pane inboxNav" aria-label={t('email.filters.label')}>
          <form className="inboxFilters" role="search" aria-label={t('email.filters.label')} onSubmit={search}>
            <div className="inboxSearchRow"><label className="inboxSearch" htmlFor={`${searchId}-q`}><span className="visuallyHidden">{t('email.filters.search')}</span></label>
              <input id={`${searchId}-q`} type="search" value={queryDraft} placeholder={t('email.filters.searchPlaceholder')} maxLength={200} onChange={(e) => setQueryDraft(e.target.value)} />
              <button type="submit" className="secondary inboxSearchButton" aria-label={t('common.search')} title={t('common.search')}><Search aria-hidden="true" /></button></div>
            <label><span className="visuallyHidden">{t('email.filters.account')}</span><select value={filters.accountId} onChange={(e) => applyFilters({ ...filters, accountId: e.target.value })}><option value="">{t('email.filters.allAccounts')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.address}</option>)}</select></label>
            <label><span className="visuallyHidden">{t('email.filters.category')}</span><select value={filters.category} onChange={(e) => applyFilters({ ...filters, category: e.target.value as Filters['category'] })}><option value="">{t('email.filters.allCategories')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{summaryValue?.by_category[c] ? t('pages.inbox.categoryUnread', { category: categoryLabel(c), count: summaryValue.by_category[c] }) : categoryLabel(c)}</option>)}</select></label>
            <label><span className="visuallyHidden">{t('email.filters.priority')}</span><select value={filters.priority} onChange={(e) => applyFilters({ ...filters, priority: e.target.value as Filters['priority'] })}><option value="">{t('email.filters.allPriorities')}</option>{PRIORITIES.map((p) => <option key={p} value={p}>{priorityLabel(p)}</option>)}</select></label>
            <div className="inboxChecks">
              <label className="inboxCheck"><input type="checkbox" checked={filters.unread} onChange={(e) => applyFilters({ ...filters, unread: e.target.checked })} />{t('email.filters.unread')}</label>
              <label className="inboxCheck"><input type="checkbox" checked={filters.archived} onChange={(e) => applyFilters({ ...filters, archived: e.target.checked })} />{t('email.filters.archived')}</label>
              {hasFilters(filters) && <button type="button" className="secondary" onClick={() => { setQueryDraft(''); applyFilters(EMPTY_FILTERS); }}>{t('common.clear')}</button>}
            </div>
          </form>
          <DraftsQueue drafts={drafts} waiting={waiting} open={widest} selected={compose?.draft?.id ?? selected} onOpen={(draft) => { if (isNewEmailDraft(draft)) { if (canAct) setCompose({ draft }); } else if (draft.message_id) pick(draft.message_id); }} onRetry={loadSummary} />
        </aside>
        <section data-pane="list" className="pane inboxList" aria-labelledby="inbox-list-title" aria-busy={listState === 'loading'}>
          <div className="paneHead"><h2 id="inbox-list-title">{filters.archived ? t('pages.inbox.archivedTitle') : t('email.list.title')}</h2>{listState === 'ready' && <span className="muted">{t('pages.inbox.shown', { count: items.length })}</span>}</div>
          {listState === 'loading' && <p className="muted inboxPad" role="status">{t('email.list.loading')}</p>}
          {listState === 'error' && <div className="inboxPad"><Notice error>{t('email.list.failed', { reason: listError ?? '' })}</Notice><div className="actions"><button type="button" onClick={() => void loadMessages(filters)}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div></div>}
          {listState === 'ready' && items.length === 0 && <div className="paneEmpty"><Inbox aria-hidden="true" />{hasFilters(filters)
            ? <><h3>{t('email.list.noMatch')}</h3><p>{t('email.list.noMatchText')}</p><HelpLink /><div className="actions"><button type="button" className="secondary" onClick={() => { setQueryDraft(''); applyFilters(EMPTY_FILTERS); }}>{t('email.list.clearFilters')}</button></div></>
            : <><h3>{t('email.list.empty')}</h3><p>{t('email.list.emptyText')}</p></>}</div>}
          {items.length > 0 && <ul className="inboxMessages">{items.map((message) => <li key={message.id}>
            <button type="button" className={`inboxRow ${selected === message.id ? 'selected' : ''} ${message.is_read ? '' : 'unread'}`} aria-current={selected === message.id ? 'true' : undefined} onClick={() => pick(message.id)}>
              <span className="inboxRowTop"><strong>{senderName(message)}</strong><time dateTime={message.received_at}>{when(message.received_at)}</time></span>
              <span className="inboxRowSubject" dir="auto">{!message.is_read && <span className="inboxUnreadDot" aria-label={t('email.list.unreadMark')} />}{message.subject || t('email.noSubject')}</span>
              <span className="inboxRowSnippet" dir="auto">{message.summary || message.snippet}</span>
              <span className="inboxBadges"><span className="statusBadge neutral">{categoryLabel(message.category)}</span>{message.priority && <span className={`statusBadge ${priorityTone(message.priority)}`}>{priorityLabel(message.priority)}</span>}{message.has_attachments && <Paperclip aria-label={t('email.hasAttachments')} />}</span>
            </button>
          </li>)}</ul>}
          {cursor && <div className="actions inboxPad"><button type="button" className="secondary" disabled={busy === 'more'} onClick={() => { setBusy('more'); void loadMessages(filters, cursor).finally(() => setBusy(null)); }}>{t('email.list.more')}</button></div>}
        </section>
        <section data-pane="detail" className="pane inboxDetail" aria-label={t('email.detail.label')}>
          {selected
            ? <MessagePanel key={selected} messageId={selected} canAct={canAct} owner={owner === true} focusSignal={picked} onBack={() => setSelected(null)} onChanged={replaceMessage} onSelect={pick} onBudget={setBudget} onOpenApprovals={onOpenApprovals} onDraftsChanged={loadSummary} />
            : <div className="paneEmpty"><MailOpen aria-hidden="true" /><h3>{t('email.detail.none')}</h3><p>{t('email.detail.noneText')}</p></div>}
        </section>
      </SplitView>
    </>}
  </div>;
}

/** Connected mailboxes and the connect buttons: in the page when none is connected, else behind the status chip. */
function MailboxSetup({ accounts, setup, canConnect, busy, privateConnect, onPrivate, onConnect, onSync, onDisconnect }: {
  accounts: EmailAccount[]; setup: EmailSetup | null; canConnect: boolean; busy: string | null; privateConnect: boolean; onPrivate: (value: boolean) => void;
  onConnect: (provider: EmailProvider) => void; onSync: (account: EmailAccount) => void; onDisconnect: (account: EmailAccount) => void;
}) {
  const connectable = setup?.providers ?? [];
  return <div className="inboxAccounts">
    {busy === 'connect' && <span className="statusBadge neutral">{t('email.connect.finishing')}</span>}
    {accounts.length > 0 && <ul className="inboxAccountList">{accounts.map((account) => {
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
          {account.status === 'reauth_required' && canConnect ? <button type="button" className="secondary" disabled={busy !== null} onClick={() => onConnect(account.provider)}><Plug aria-hidden="true" />{t('email.account.reconnect')}</button>
            : <button type="button" className="secondary" disabled={busy !== null} aria-label={t('email.account.syncLabel', { address: account.address })} onClick={() => onSync(account)}><RefreshCw aria-hidden="true" />{busy === `sync:${account.id}` ? t('email.account.syncingNow') : t('email.account.sync')}</button>}
          {account.can_manage && <button type="button" className="secondary" disabled={busy !== null} aria-label={t('email.account.disconnectLabel', { address: account.address })} onClick={() => onDisconnect(account)}><Unplug aria-hidden="true" />{t('email.account.disconnect')}</button>}
        </div>
      </li>;
    })}</ul>}
    {canConnect ? <div className="inboxConnect">
      <div className="inboxConnectButtons">{connectable.map((provider) => provider.configured && provider.enabled_here
        ? <button key={provider.provider} type="button" disabled={busy !== null} onClick={() => onConnect(provider.provider)}><Plug aria-hidden="true" />{t('email.connect.button', { provider: provider.name })}</button>
        : <p key={provider.provider} className="muted inboxSetupNote"><strong>{provider.name}:</strong> {provider.configured ? t('email.connect.offHere') : t('email.connect.notSetUp', { settings: provider.missing.join(', ') })}</p>)}</div>
      <label className="inboxCheck"><input type="checkbox" checked={privateConnect} onChange={(e) => onPrivate(e.target.checked)} />{t('email.connect.private')}</label>
      <small className="muted">{t('email.connect.help')}</small>
    </div> : accounts.length > 0 ? null : <p className="muted">{t('email.connect.ownerOnly')}</p>}
  </div>;
}

/** Drafts still waiting for a person (GET /email/drafts): a reply opens on its message, a new email in the compose drawer. */
function DraftsQueue({ drafts, waiting, open, selected, onOpen, onRetry }: { drafts: Load<EmailDraft[]>; waiting: EmailDraft[]; open: boolean; selected: string | null; onOpen: (draft: EmailDraft) => void; onRetry: () => void }) {
  return <details className="inboxQueue" open={open || undefined}>
    <summary><PenLine aria-hidden="true" />{t('pages.inbox.queueTitle')}{drafts.state === 'ready' && <span className="inboxQueueCount">{formatNumber(waiting.length)}</span>}</summary>
    {drafts.state === 'loading' && <p className="muted">{t('pages.inbox.queueLoading')}</p>}
    {drafts.state === 'error' && <p className="muted">{t('pages.inbox.queueFailed')} <button type="button" className="linkButton" onClick={onRetry}>{t('pages.inbox.queueRetry')}</button></p>}
    {drafts.state === 'ready' && waiting.length === 0 && <p className="muted">{t('pages.inbox.queueEmpty')}</p>}
    {waiting.length > 0 && <ul className="railList">{waiting.map((draft) => <li key={draft.id}>
      <button type="button" className="railItem" aria-current={selected !== null && (draft.message_id === selected || draft.id === selected) ? 'true' : undefined} onClick={() => onOpen(draft)}>
        <strong dir="auto">{isNewEmailDraft(draft) && <span className="statusBadge neutral inboxKind">{t('email.compose.kindNew')}</span>}{draft.subject || t('email.noSubject')}</strong>
        <small><span className={`statusBadge ${draft.status === 'failed' ? 'dangerTone' : draft.status === 'pending_approval' ? 'warning' : 'neutral'}`}>{draftStatusLabel(draft.status)}</span> {draft.to.join(', ') || t('pages.inbox.noRecipient')}</small>
      </button>
    </li>)}</ul>}
  </details>;
}

function MessagePanel({ messageId, canAct, owner, focusSignal, onBack, onChanged, onSelect, onBudget, onOpenApprovals, onDraftsChanged }: {
  messageId: string; canAct: boolean; owner: boolean; focusSignal: number; onBack: () => void; onChanged: (message: EmailMessage) => void; onSelect: (id: string) => void;
  onBudget: (message: string) => void; onOpenApprovals?: () => void; onDraftsChanged: () => void;
}) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [suggested, setSuggested] = useState<{ reply: string; model: string } | null>(null);
  const [instructions, setInstructions] = useState('');
  const [manual, setManual] = useState(false);
  const [ticketOpen, setTicketOpen] = useState(false);
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const instructionsId = useId();

  const load = useCallback(async () => {
    setState('loading');
    try { const found = await getEmailMessage(messageId); setDetail(found); setState('ready'); setError(null); }
    catch (e) { setState('error'); setError(describeError(e)); }
  }, [messageId]);
  useEffect(() => { void load(); }, [load]);
  // Focus moves to the message when a person opened it (not when the pane opens on the first unread one).
  useEffect(() => { if (state === 'ready' && focusSignal > 0) heading.current?.focus({ preventScroll: false }); }, [state, focusSignal]);

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
  const setDrafts = (fn: (drafts: EmailDraft[]) => EmailDraft[]) => { setDetail((current) => current ? { ...current, drafts: fn(current.drafts) } : current); onDraftsChanged(); };
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
        <button type="button" className="secondary" aria-haspopup="dialog" disabled={busy !== null} onClick={() => setTicketOpen(true)}><LifeBuoy aria-hidden="true" />{t('tfm.button')}</button>
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
    {ticket && <TicketCreatedNotice ticket={ticket} />}
    {ticketOpen && <CreateTicketDialog source="email" refId={message.id} defaultTitle={message.subject} onClose={() => setTicketOpen(false)} onCreated={(created) => { setTicket(created); setTicketOpen(false); }} />}

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
  const savedBcc = (draft.bcc ?? []).join(', ');
  const [to, setTo] = useState(draft.to.join(', '));
  const [cc, setCc] = useState(draft.cc.join(', '));
  const [bcc, setBcc] = useState(savedBcc);
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const ids = { to: useId(), cc: useId(), bcc: useId(), subject: useId(), body: useId() };
  const isNew = isNewEmailDraft(draft);
  const editable = draft.status === 'draft' || draft.status === 'failed' || draft.status === 'pending_approval';
  const dirty = to !== draft.to.join(', ') || cc !== draft.cc.join(', ') || bcc !== savedBcc || subject !== draft.subject || body !== draft.body;
  const approval: Approval | null = useMemo(() => draft.approval ? toApproval(draft.approval) : null, [draft.approval]);
  const list = (value: unknown) => Array.isArray(value) ? value.map(String) : [];
  const approvalCc = list(approval?.arguments?.cc), approvalBcc = list(approval?.arguments?.bcc);

  async function run(kind: string, fn: () => Promise<void>) {
    setBusy(kind); setNotice(null);
    try { await fn(); } catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }
  // A new email may be saved without a recipient yet; a reply always has one, and asking to send needs one.
  async function save(forSending: boolean): Promise<EmailDraft | null> {
    const toList = parseRecipients(to), ccList = parseRecipients(cc), bccList = parseRecipients(bcc);
    if (!toList.ok) { setNotice({ text: toList.error, error: true }); return null; }
    if (!ccList.ok) { setNotice({ text: ccList.error, error: true }); return null; }
    if (!bccList.ok) { setNotice({ text: bccList.error, error: true }); return null; }
    if (!toList.value.length && (forSending || !isNew)) { setNotice({ text: t('email.draft.needRecipient'), error: true }); return null; }
    const saved = await updateEmailDraft(draft.id, { to: toList.value, cc: ccList.value, bcc: bccList.value, subject, body });
    onChange(saved);
    return saved;
  }
  async function ask() {
    const current = dirty ? await save(true) : draft;
    if (!current) return;
    if (!current.to.length) { setNotice({ text: t('email.draft.needRecipient'), error: true }); return; }
    const result = await requestSendApproval(current.id);
    onChange(result.draft);
  }

  if (draft.status === 'sent') return <div className="inboxDraftCard sent"><p><CheckCircle2 aria-hidden="true" /> {t('email.draft.sentTo', { to: draft.to.join(', '), when: draft.sent_at ? when(draft.sent_at) : '' })}</p><div className="inboxBodyText" dir="auto">{draft.body}</div></div>;

  return <div className="inboxDraftCard">
    <div className="sectionHeader"><span className="statusBadge neutral">{draftStatusLabel(draft.status)}</span><small className="muted">{draftProvenance(draft)}{draft.saved_in_provider ? ` · ${t('email.draft.savedInProvider')}` : ''}</small></div>
    {draft.error && <Notice error>{t('email.draft.lastError', { reason: draft.error })}</Notice>}
    {draft.approval_note && <Notice>{draft.approval_note}</Notice>}
    <div className="inboxDraftFields">
      <label className="field" htmlFor={ids.to}><span>{t('email.draft.to')}</span><input id={ids.to} inputMode="email" autoComplete="off" value={to} disabled={!editable} onChange={(e) => setTo(e.target.value)} /></label>
      <label className="field" htmlFor={ids.cc}><span>{t('email.draft.cc')}</span><input id={ids.cc} inputMode="email" autoComplete="off" value={cc} disabled={!editable} onChange={(e) => setCc(e.target.value)} /></label>
      <label className="field" htmlFor={ids.bcc}><span>{t('email.draft.bcc')}</span><input id={ids.bcc} inputMode="email" autoComplete="off" value={bcc} disabled={!editable} onChange={(e) => setBcc(e.target.value)} /></label>
      <label className="field inboxWide" htmlFor={ids.subject}><span>{t('email.draft.subject')}</span><input id={ids.subject} dir="auto" value={subject} maxLength={MAX_SUBJECT_CHARS} disabled={!editable} onChange={(e) => setSubject(e.target.value)} /></label>
      <label className="field inboxWide" htmlFor={ids.body}><span>{t('email.draft.body')}</span><textarea id={ids.body} dir="auto" value={body} maxLength={MAX_DRAFT_CHARS} disabled={!editable} onChange={(e) => setBody(e.target.value)} /></label>
    </div>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {approval && approval.status === 'pending' ? <div className="inboxApproval" role="group" aria-label={t('email.approval.label')}>
      <p><Send aria-hidden="true" /> <strong>{t('email.approval.title')}</strong></p>
      <dl className="inboxMeta">
        <div><dt>{t('email.detail.from')}</dt><dd>{String(approval.arguments?.from ?? '')}</dd></div>
        <div><dt>{t('email.detail.to')}</dt><dd>{list(approval.arguments?.to).join(', ')}</dd></div>
        {approvalCc.length > 0 && <div><dt>{t('email.detail.cc')}</dt><dd>{approvalCc.join(', ')}</dd></div>}
        {approvalBcc.length > 0 && <div><dt>{t('email.draft.bcc')}</dt><dd>{approvalBcc.join(', ')}</dd></div>}
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
      <button type="button" className="secondary" disabled={busy !== null || !dirty} onClick={() => void run('save', async () => { if (await save(false)) setNotice({ text: t('email.draft.saved') }); })}>{busy === 'save' ? t('email.compose.saving') : t('email.draft.save')}</button>
      {(!approval || approval.status !== 'pending' || dirty) && <button type="button" disabled={busy !== null || !body.trim()} onClick={() => void run('ask', ask)}><Send aria-hidden="true" />{busy === 'ask' ? t('email.compose.asking') : t('email.draft.askToSend')}</button>}
      <button type="button" className="secondary danger" disabled={busy !== null} onClick={() => { if (!window.confirm(t('email.draft.discardConfirm'))) return; void run('discard', async () => { await discardEmailDraft(draft.id); onChange(null); }); }}><Trash2 aria-hidden="true" />{t('email.draft.discard')}</button>
    </div>}
  </div>;
}

/**
 * A brand-new email (POST /email/drafts): pick the mailbox, write it, save it as a draft or ask to
 * send. Sending waits for an owner's approval of the exact message (payload hash), like a reply.
 * Once the draft exists the drawer shows it in the same card replies use. No attachments here:
 * this mailbox send path carries text only.
 */
function ComposeDrawer({ accounts, initialDraft, owner, onClose, onChanged, onDiscarded, onOpenApprovals }: {
  accounts: EmailAccount[]; initialDraft: EmailDraft | null; owner: boolean; onClose: () => void; onChanged: () => void; onDiscarded: () => void; onOpenApprovals?: () => void;
}) {
  const { usable, blocked } = composeMailboxes(accounts);
  const [draft, setDraft] = useState<EmailDraft | null>(initialDraft);
  const [form, setForm] = useState<ComposeForm>(() => ({ ...EMPTY_COMPOSE, accountId: usable[0]?.id ?? '' }));
  const [copies, setCopies] = useState(false);
  const [busy, setBusy] = useState<'save' | 'ask' | null>(null);
  const [paused, setPaused] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const ids = { from: useId(), to: useId(), cc: useId(), bcc: useId(), subject: useId(), body: useId(), copies: useId() };
  const set = (patch: Partial<ComposeForm>) => setForm((current) => ({ ...current, ...patch }));
  const typed = Boolean(form.to.trim() || form.cc.trim() || form.bcc.trim() || form.subject.trim() || form.body.trim());
  const close = () => { if (!draft && typed && !paused && !window.confirm(t('email.compose.closeConfirm'))) return; onClose(); };

  async function create(forSending: boolean) {
    const request = composeRequest(form, forSending);
    if (!request.ok) { setNotice({ text: request.error, error: true }); return; }
    setBusy(forSending ? 'ask' : 'save'); setNotice(null);
    try {
      const result = await createNewEmailDraft(request.value);
      onChanged();
      if (!result.draft) {
        // Workspace policy holds drafting until an owner approves it in Approvals.
        if (result.status === 'waiting_approval') { setPaused(true); setNotice({ text: t('email.detail.waitingApproval') }); }
        else setNotice({ text: result.summary || t('common.operationFailed'), error: true });
        return;
      }
      setDraft(result.draft);
      if (forSending) {
        try {
          const asked = await requestSendApproval(result.draft.id);
          setDraft(asked.draft); onChanged();
        } catch (e) { setNotice({ text: t('email.compose.savedNotAsked', { reason: describeError(e) }), error: true }); return; }
      }
      setNotice(result.warning ? { text: result.warning } : { text: forSending ? (owner ? t('email.compose.askedOwner') : t('email.compose.askedMember')) : t('email.draft.saved') });
    } catch (e) {
      setNotice({ text: isPermissionDenied(e) ? t('email.detail.notAllowed', { reason: describeError(e) }) : describeError(e), error: true });
    } finally { setBusy(null); }
  }

  async function refreshAfterSend(id: string) {
    onChanged();
    try {
      const found = (await listEmailDrafts()).find((item) => item.id === id);
      setDraft((current) => found ?? (current ? { ...current, approval: null } : current));
    } catch { setDraft((current) => current ? { ...current, approval: null } : current); }
  }

  const fromAddress = draft ? accounts.find((a) => a.id === draft.account_id)?.address ?? draft.account_id : null;
  return <Drawer title={t('email.compose.title')} onClose={close}>
    <div className="inboxCompose">
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {draft ? <>
        <dl className="inboxMeta"><div><dt>{t('email.detail.from')}</dt><dd>{fromAddress}</dd></div></dl>
        <DraftCard key={draft.id} draft={draft} owner={owner} onOpenApprovals={onOpenApprovals} onSent={() => void refreshAfterSend(draft.id)}
          onChange={(next) => { onChanged(); if (next) setDraft(next); else onDiscarded(); }} />
        <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
      </> : paused ? <div className="actions">
        {onOpenApprovals && <button type="button" onClick={onOpenApprovals}>{t('email.approval.openApprovals')}</button>}
        <button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button>
      </div> : usable.length === 0 ? <>
        <Notice error>{t('email.compose.allNeedReconnect')}</Notice>
        <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('dialog.close')}</button></div>
      </> : <form className="inboxComposeForm" aria-busy={busy !== null} onSubmit={(e) => { e.preventDefault(); void create(true); }}>
        <label className="field" htmlFor={ids.from}><span>{t('email.detail.from')}</span>
          <select id={ids.from} value={form.accountId} onChange={(e) => set({ accountId: e.target.value })}>{usable.map((a) => <option key={a.id} value={a.id}>{a.address}</option>)}</select>
        </label>
        {blocked.length > 0 && <small className="muted">{t('email.compose.skipped', { addresses: blocked.map((a) => a.address).join(', ') })}</small>}
        <label className="field" htmlFor={ids.to}><span>{t('email.draft.to')}</span><input id={ids.to} inputMode="email" autoComplete="off" value={form.to} placeholder={t('email.compose.toPlaceholder')} onChange={(e) => set({ to: e.target.value })} /></label>
        <button type="button" className="linkButton" aria-expanded={copies} aria-controls={ids.copies} onClick={() => setCopies((value) => !value)}>{copies ? t('email.compose.hideCopies') : t('email.compose.showCopies')}</button>
        <div id={ids.copies} className="inboxComposeCopies" hidden={!copies}>
          <label className="field" htmlFor={ids.cc}><span>{t('email.draft.cc')}</span><input id={ids.cc} inputMode="email" autoComplete="off" value={form.cc} onChange={(e) => set({ cc: e.target.value })} /></label>
          <label className="field" htmlFor={ids.bcc}><span>{t('email.draft.bcc')}</span><input id={ids.bcc} inputMode="email" autoComplete="off" value={form.bcc} onChange={(e) => set({ bcc: e.target.value })} /></label>
        </div>
        <label className="field" htmlFor={ids.subject}><span>{t('email.draft.subject')}</span><input id={ids.subject} dir="auto" value={form.subject} maxLength={MAX_SUBJECT_CHARS} onChange={(e) => set({ subject: e.target.value })} /></label>
        <label className="field" htmlFor={ids.body}><span>{t('email.draft.body')}</span><textarea id={ids.body} dir="auto" rows={8} value={form.body} maxLength={MAX_DRAFT_CHARS} onChange={(e) => set({ body: e.target.value })} /></label>
        <small className="muted">{t('email.compose.approvalNote')}</small>
        <small className="muted">{t('email.compose.noAttachments')}</small>
        <div className="actions">
          <button type="submit" disabled={busy !== null}><Send aria-hidden="true" />{busy === 'ask' ? t('email.compose.asking') : t('email.draft.askToSend')}</button>
          <button type="button" className="secondary" disabled={busy !== null} onClick={() => void create(false)}>{busy === 'save' ? t('email.compose.saving') : t('email.compose.saveDraft')}</button>
          <button type="button" className="secondary" disabled={busy !== null} onClick={close}>{t('common.cancel')}</button>
        </div>
      </form>}
    </div>
  </Drawer>;
}
