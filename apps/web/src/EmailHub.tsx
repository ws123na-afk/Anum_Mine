// One Email module (docs/email.md#one-email-module). One sidebar entry over both mail back ends: a
// mailbox list with every connected mailbox (Gmail, Outlook and Zoho through OAuth, and mailboxes on
// the workspace's own mail server) with a source badge, one "All inboxes" view over all of them, one
// reader pane, one Write that offers every address the person may send from, and one Connections page.
// Reading, replies, Email AI and sending stay with the back end that owns the mailbox: the reader and
// composer are that back end's own (InboxView.tsx for providers, EmailView.tsx for the own server), so
// permissions, tenant scoping and the approval of every send are exactly as before.
import { listTime } from './lib/listTime';
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { AlertTriangle, AtSign, CheckCircle2, CircleDot, Inbox, Lock, Mail, MailOpen, Paperclip, PenLine, Plug, RefreshCw, Search, Server, Settings, Share2, ShieldCheck } from 'lucide-react';
import { FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { Composer, EmailView, Notice, Panel, Reader, mailError } from './EmailView';
import { InboxView, MessagePanel } from './InboxView';
import { Drawer } from './Dialog';
import { PageHeader, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import { useIsOwner } from './useIsOwner';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import { viewLabel } from './lib/shell';
import {
  HUB_FOLDERS_SLOT, appendUnique, backendNotes, findMailbox, landingPane, needsAttention, paneFromHash, paneHash, senders, sourceKey, totalUnread,
  type HubPane, type UnifiedMailbox, type UnifiedMailboxes, type UnifiedMessage, type UnreadableMailbox,
} from './lib/emailHub';
import { listUnifiedMailboxes, listUnifiedMessages } from './lib/emailHubApi';
import type { MailDraft, MailDraftKind, MailSetup } from './lib/mail';
import { createMailDraft, getMailSetup } from './lib/mailApi';
import { setMailFlag } from './lib/mailActionsApi';
import type { MailAction } from './lib/mailActions';
import { inboxFolder } from './lib/mailAllInboxes';
import './emailHub.css';

type Load = { state: 'loading' } | { state: 'ready'; value: UnifiedMailboxes } | { state: 'error'; reason: string } | { state: 'denied' };
type Note = { text: string; error?: boolean } | null;
const PAGE = 20;
const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function SourceBadge({ source }: { source: UnifiedMailbox['source'] }) {
  return <span className={`hubSource hubSource-${source}`}>{source === 'mailserver' ? <Server aria-hidden="true" /> : <AtSign aria-hidden="true" />}{t(sourceKey(source))}</span>;
}

export function EmailHub({ initialPane, initialMessageId, onOpenApprovals, onOpenBudgets }: {
  initialPane: HubPane | null; initialMessageId?: string | null; onOpenApprovals?: () => void; onOpenBudgets?: () => void;
}) {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  // A provider message opened from search or chat opens in the provider inbox, as before.
  const [pane, setPane] = useState<HubPane | null>(initialMessageId ? { kind: 'provider' } : initialPane);
  const [extra, setExtra] = useState<{ composeFrom?: string; draftId?: string; settings?: boolean; mailboxes?: boolean }>({});
  const [writeOpen, setWriteOpen] = useState(false);
  const [note, setNote] = useState<Note>(null);

  const reload = useCallback(async () => {
    try {
      const value = await listUnifiedMailboxes();
      setLoad({ state: 'ready', value });
      return value;
    } catch (e) {
      if (isPermissionDenied(e)) { setLoad({ state: 'denied' }); return null; }
      setLoad({ state: 'error', reason: describeError(e) });
      return null;
    }
  }, []);
  useEffect(() => {
    void reload().then((value) => setPane((current) => current ?? landingPane(value)));
  }, [reload]);
  // A link to another pane while the page is open (#email/server, #email/connections) switches to it.
  useEffect(() => {
    const follow = () => { const next = paneFromHash(location.hash); if (next) { setPane(next); setExtra({}); } };
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);

  const found = load.state === 'ready' ? load.value : null;
  const go = (next: HubPane, more: typeof extra = {}) => {
    setPane(next); setExtra(more); setNote(null);
    try { history.replaceState(null, '', paneHash(next)); } catch { /* the address bar is a convenience */ }
  };

  async function writeFrom(mailbox: UnifiedMailbox) {
    setWriteOpen(false); setNote(null);
    if (mailbox.backend === 'provider') { go({ kind: 'mailbox', ref: mailbox.ref }, { composeFrom: mailbox.id }); return; }
    try {
      const draft = await createMailDraft({ account_id: mailbox.id, kind: 'new' });
      go({ kind: 'mailbox', ref: mailbox.ref }, { draftId: draft.id });
    } catch (e) { setNote({ text: t('mail.composer.startFailed', { reason: mailError(e) }), error: true }); }
  }

  const title = viewLabel('email');
  // Viewers: the back end's own page says who may read mail (the provider inbox or the Email Center).
  if (load.state === 'denied') {
    return pane?.kind === 'provider' ? <InboxView title={title} subtitle={t('emailHub.provider.subtitle')} /> : <EmailView />;
  }
  const canWrite = senders(found).length > 0;
  const write = canWrite ? <button type="button" aria-haspopup="dialog" onClick={() => setWriteOpen(true)}><PenLine aria-hidden="true" />{t('emailHub.write')}</button> : null;
  const mailbox = pane?.kind === 'mailbox' ? findMailbox(found, pane.ref) : null;

  let main: ReactNode = <p className="notice" role="status" aria-busy="true"><CircleDot aria-hidden="true" />{t('emailHub.loading')}</p>;
  if (pane?.kind === 'all') main = <HubAllInboxes found={found} write={write} onOpenApprovals={onOpenApprovals} onOpenBudgets={onOpenBudgets}
    onOpenMailbox={(ref) => go({ kind: 'mailbox', ref })} onOpenConnections={() => go({ kind: 'connections' })} />;
  else if (pane?.kind === 'connections') main = <HubConnections found={found} load={load} write={write} onRetry={() => void reload()}
    onManageProviders={() => go({ kind: 'provider' }, { mailboxes: true })} onConnectServer={() => go({ kind: 'server' }, { settings: true })}
    onOpenMailbox={(ref, settings) => go({ kind: 'mailbox', ref }, { settings, mailboxes: settings })} />;
  else if (pane?.kind === 'provider') main = <InboxView key={`provider-${initialMessageId ?? ''}`} initialMessageId={initialMessageId} title={title} subtitle={t('emailHub.provider.subtitle')}
    openMailboxes={extra.mailboxes} onOpenBudgets={onOpenBudgets} onOpenApprovals={onOpenApprovals} />;
  else if (pane?.kind === 'server') main = <EmailView key="server" openSettings={extra.settings} onOpenApprovals={onOpenApprovals} onSetupChanged={() => void reload()} />;
  else if (pane?.kind === 'mailbox' && mailbox?.backend === 'provider') main = <InboxView key={`${mailbox.ref}-${extra.composeFrom ?? ''}`} accountId={mailbox.id} title={title}
    subtitle={mailbox.address} composeFrom={extra.composeFrom} openMailboxes={extra.mailboxes} onOpenBudgets={onOpenBudgets} onOpenApprovals={onOpenApprovals} />;
  else if (pane?.kind === 'mailbox' && mailbox?.backend === 'server') main = <EmailView key={`${mailbox.ref}-${extra.draftId ?? ''}`} accountId={mailbox.id} initialDraftId={extra.draftId}
    openSettings={extra.settings} onOpenApprovals={onOpenApprovals} onSetupChanged={() => void reload()} />;
  else if (pane?.kind === 'mailbox' && load.state === 'ready') main = <><PageHeader title={title} subtitle={t('emailHub.subtitle')} />
    <Notice note={{ text: t('emailHub.mailboxGone'), error: true }} />
    <div className="actions"><button type="button" onClick={() => go({ kind: 'all' })}>{t('emailHub.openAll')}</button></div></>;
  else if (pane?.kind === 'mailbox' && load.state === 'error') main = <><PageHeader title={title} subtitle={t('emailHub.subtitle')} />
    <Notice note={{ text: t('emailHub.loadFailed', { reason: load.reason }), error: true }} />
    <div className="actions"><button type="button" onClick={() => void reload()}><RefreshCw aria-hidden="true" />{t('emailHub.retry')}</button></div></>;

  return <div className="emailHub">
    <HubRail load={load} pane={pane} onPick={go} onRetry={() => void reload()} />
    <div className="emailHubMain">
      <Notice note={note} />
      {main}
    </div>
    {writeOpen && <Drawer title={t('emailHub.writeFrom.title')} onClose={() => setWriteOpen(false)}>
      <WriteFrom options={senders(found)} onPick={(m) => void writeFrom(m)} />
    </Drawer>}
  </div>;
}

/**
 * A mailbox's spoken name: every word its rail item shows, in that order (WCAG 2.5.3), then its unread count in words. The count badge
 * itself is drawn from data-count (emailHub.css), so the digits it shows are never missing from the name.
 */
function mailboxName(m: UnifiedMailbox): string {
  return [
    t('emailHub.rail.mailbox', { address: m.address, source: t(sourceKey(m.source)) }),
    m.access === 'read' || m.access === 'send' ? t('emailHub.shared') : null,
    needsAttention(m) ? t('emailHub.attention') : null,
    (m.unread ?? 0) > 0 ? t('emailHub.unreadCount', { count: m.unread ?? 0 }) : null,
  ].filter(Boolean).join('. ');
}

/** The one mailbox list: All inboxes, the open mailbox's folders, every connected mailbox with its source badge, then Manage connections. */
function HubRail({ load, pane, onPick, onRetry }: { load: Load; pane: HubPane | null; onPick: (pane: HubPane) => void; onRetry: () => void }) {
  const found = load.state === 'ready' ? load.value : null;
  const current = (p: HubPane) => pane !== null && pane.kind === p.kind && (p.kind !== 'mailbox' || (pane.kind === 'mailbox' && pane.ref === p.ref));
  const unread = totalUnread(found);
  return <nav className="emailHubRail" aria-label={t('emailHub.rail.label')}>
    <ul className="hubRailList">
      <li><button type="button" className="hubRailItem hubRailAll" aria-current={current({ kind: 'all' }) ? 'page' : undefined} onClick={() => onPick({ kind: 'all' })}>
        <Inbox aria-hidden="true" /><span className="hubRailLabel">{t('emailHub.all')}</span>
        {unread > 0 ? <span className="hubCount" aria-label={t('emailHub.unreadCount', { count: unread })}>{formatNumber(unread)}</span> : null}</button></li>
    </ul>
    <div id={HUB_FOLDERS_SLOT} className="hubFoldersSlot" />
    {(found?.items.length ?? 0) > 0 && <h2 className="hubRailHeading">{t('emailHub.rail.connected')}</h2>}
    <ul className="hubRailList hubRailMailboxes">
      {found?.items.map((m) => <li key={m.ref}>
        <button type="button" className="hubRailItem hubRailMailbox" aria-current={current({ kind: 'mailbox', ref: m.ref }) ? 'page' : undefined} onClick={() => onPick({ kind: 'mailbox', ref: m.ref })}
          aria-label={mailboxName(m)}>
          <span className={`hubRailMark hubRailMark-${m.source}`} aria-hidden="true">{m.source === 'mailserver' ? <Server /> : <AtSign />}</span>
          <span className="hubRailLabel"><span dir="ltr" className="hubAddress">{m.address}</span>{' '}<SourceBadge source={m.source} />{' '}
            {m.access === 'read' || m.access === 'send' ? <span className="hubShared"><Share2 aria-hidden="true" />{t('emailHub.shared')}</span> : null}</span>{' '}
          {needsAttention(m) && <span className="statusBadge warning">{t('emailHub.attention')}</span>}
          {(m.unread ?? 0) > 0 && <span className="hubCount" aria-hidden="true" data-count={formatNumber(m.unread ?? 0)} />}
        </button>
      </li>)}
      <li className="hubRailManage"><button type="button" className="hubRailItem hubRailConnections" aria-current={current({ kind: 'connections' }) ? 'page' : undefined} onClick={() => onPick({ kind: 'connections' })}>
        <Settings aria-hidden="true" /><span className="hubRailLabel">{t('emailHub.rail.manage')}</span></button></li>
    </ul>
    {load.state === 'loading' && <p className="muted hubRailNote" role="status">{t('emailHub.rail.loading')}</p>}
    {load.state === 'error' && <div className="hubRailNote"><p className="muted" role="alert">{t('emailHub.rail.failed')}</p>
      <button type="button" className="secondary" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('emailHub.retry')}</button></div>}
    {found && found.items.length === 0 && <p className="muted hubRailNote">{t('emailHub.rail.none')}</p>}
  </nav>;
}

function WriteFrom({ options, onPick }: { options: UnifiedMailbox[]; onPick: (mailbox: UnifiedMailbox) => void }) {
  const [chosen, setChosen] = useState(options[0]?.ref ?? '');
  const id = useId();
  const pick = options.find((o) => o.ref === chosen) ?? null;
  return <form className="hubWriteFrom" onSubmit={(e) => { e.preventDefault(); if (pick) onPick(pick); }}>
    <p className="muted">{t('emailHub.writeFrom.text')}</p>
    <fieldset><legend className="visuallyHidden">{t('emailHub.writeFrom.title')}</legend>
      {options.map((o) => <label key={o.ref} className="hubWriteOption" htmlFor={`${id}-${o.ref}`}>
        <input id={`${id}-${o.ref}`} type="radio" name={`${id}-from`} value={o.ref} checked={chosen === o.ref} onChange={() => setChosen(o.ref)} />
        <span dir="ltr" className="hubAddress">{o.address}</span><SourceBadge source={o.source} />
      </label>)}
    </fieldset>
    <p className="muted">{t('emailHub.writeFrom.approval')}</p>
    <div className="actions"><button type="submit" disabled={!pick}><PenLine aria-hidden="true" />{t('emailHub.writeFrom.go')}</button></div>
  </form>;
}

type Selected = { kind: 'message'; ref: string } | { kind: 'draft'; id: string; accountId: string } | null;

/** All inboxes over both back ends; a message opens in the reader of the back end that owns it. */
function HubAllInboxes({ found, write, onOpenMailbox, onOpenConnections, onOpenApprovals, onOpenBudgets }: {
  found: UnifiedMailboxes | null; write: ReactNode; onOpenMailbox: (ref: string) => void; onOpenConnections: () => void; onOpenApprovals?: () => void; onOpenBudgets?: () => void;
}) {
  const owner = useIsOwner() === true;
  const wide = useMediaQuery(SPLIT_QUERY);
  const [query, setQuery] = useState('');
  const [draftQuery, setDraftQuery] = useState('');
  const [unread, setUnread] = useState(false);
  const [items, setItems] = useState<UnifiedMessage[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [unreadable, setUnreadable] = useState<UnreadableMailbox[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [error, setError] = useState('');
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selected>(null);
  const [setup, setSetup] = useState<MailSetup | null>(null);
  const [picked, setPicked] = useState(0);
  const [budget, setBudget] = useState<string | null>(null);
  const listTitle = useId();
  const searchId = useId();

  const loadPage = useCallback(async (q: string, onlyUnread: boolean, after: string | null = null) => {
    if (!after) setState('loading');
    try {
      const page = await listUnifiedMessages({ q, unread: onlyUnread, cursor: after, limit: PAGE });
      setItems((current) => after ? appendUnique(current, page.items) : page.items);
      setCursor(page.next_cursor); setUnreadable(page.unreadable); setState('ready'); setError('');
    } catch (e) {
      if (after) { setNote({ text: t('emailHub.moreFailed', { reason: describeError(e) }), error: true }); return; }
      if (isPermissionDenied(e)) { setState('denied'); return; }
      setError(describeError(e)); setState('error');
    }
  }, []);
  useEffect(() => { void loadPage(query, unread); }, [query, unread, loadPage]);

  // The own server's reader needs its mailbox records; read once when such a message opens.
  const open = selected?.kind === 'message' ? items.find((m) => m.ref === selected.ref) ?? null : null;
  useEffect(() => {
    if (setup || !(open?.backend === 'server' || selected?.kind === 'draft')) return;
    getMailSetup().then(setSetup, (e) => setNote({ text: t('emailHub.readerFailed', { reason: mailError(e) }), error: true }));
  }, [open, selected, setup]);
  const serverAccount = open?.backend === 'server' ? setup?.accounts.find((a) => a.id === open.mailbox_id) ?? null : null;
  const draftAccount = selected?.kind === 'draft' ? setup?.accounts.find((a) => a.id === selected.accountId) ?? null : null;
  const canActServer = Boolean(setup?.can_act) && open?.read_only === false;
  const filtered = Boolean(query || unread);
  const notes = useMemo(() => backendNotes(found).filter((n) => n.info.state === 'error'), [found]);

  function openMessage(m: UnifiedMessage) {
    setSelected({ kind: 'message', ref: m.ref }); setPicked((n) => n + 1); setNote(null);
  }
  function search(event: FormEvent) { event.preventDefault(); setSelected(null); setQuery(draftQuery.trim()); }
  function clear() { setDraftQuery(''); setQuery(''); setUnread(false); setSelected(null); }

  async function flag(m: UnifiedMessage, action: MailAction) {
    if (m.backend !== 'server' || m.uid === null || !m.folder) return;
    const which = action === 'star' || action === 'unstar' ? 'flagged' : 'seen';
    const value = action === 'star' || action === 'read';
    setBusy('flag');
    try {
      await setMailFlag(m.mailbox_id, m.folder, [m.uid], which, value);
      setItems((list) => list.map((item) => item.ref === m.ref ? { ...item, ...(which === 'flagged' ? { flagged: value } : { unread: !value }) } : item));
    } catch (e) { setNote({ text: t('mailc.failed', { reason: mailError(e) }), error: true }); } finally { setBusy(null); }
  }
  async function compose(m: UnifiedMessage, kind: MailDraftKind) {
    if (m.uid === null || !m.folder) return;
    setBusy('compose');
    try {
      const draft = await createMailDraft({ account_id: m.mailbox_id, kind, folder: m.folder, uid: m.uid });
      setSelected({ kind: 'draft', id: draft.id, accountId: m.mailbox_id });
    } catch (e) { setNote({ text: t('mail.composer.startFailed', { reason: mailError(e) }), error: true }); } finally { setBusy(null); }
  }

  const header = <PageHeader title={viewLabel('email')} subtitle={t('emailHub.subtitle')} actions={write} />;
  if (state === 'denied') return <>{header}<Panel icon={<Lock />} title={t('emailHub.denied.title')} text={t('emailHub.denied.text')} /></>;

  return <>
    {header}
    <Notice note={note} />
    {budget && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('common.budgetReached', { message: budget })}{onOpenBudgets && <button type="button" className="secondary inboxInlineButton" onClick={onOpenBudgets}>{t('email.openBudgets')}</button>}</p>}
    {notes.map((n) => <Notice key={n.backend} note={{ text: n.info.detail ?? t('emailHub.backendFailed'), error: true }} />)}
    <SplitView className="mailSplit hubSplit" layout="two" show={selected ? 'detail' : 'list'} label={t('emailHub.all')}>
      <section data-pane="list" className="pane mailList" aria-labelledby={listTitle} aria-busy={state === 'loading'}>
        <div className="paneHead"><h2 id={listTitle}><Inbox aria-hidden="true" className="mailAllIcon" />{t('emailHub.all')}</h2>
          {state === 'ready' && <span className="muted" aria-live="polite">{t('emailHub.shown', { count: items.length })}</span>}</div>
        <form className="hubFilters" role="search" aria-label={t('emailHub.search.label')} onSubmit={search}>
          <label className="visuallyHidden" htmlFor={searchId}>{t('emailHub.search.label')}</label>
          <input id={searchId} type="search" value={draftQuery} maxLength={200} placeholder={t('emailHub.search.placeholder')} onChange={(e) => setDraftQuery(e.target.value)} />
          <button type="submit" className="secondary" aria-label={t('emailHub.search.button')} title={t('emailHub.search.button')}><Search aria-hidden="true" /></button>
          <label className="inboxCheck"><input type="checkbox" checked={unread} onChange={(e) => { setSelected(null); setUnread(e.target.checked); }} />{t('emailHub.unreadOnly')}</label>
          {filtered && <button type="button" className="linkButton" onClick={clear}>{t('emailHub.clear')}</button>}
        </form>
        {unreadable.length > 0 && <div className="mailPad">{unreadable.map((p) => <Notice key={p.ref} note={{ text: t('maila.unreadable', { address: p.address, reason: p.reason ?? '' }), error: true }} />)}
          <div className="actions"><button type="button" className="secondary" onClick={() => void loadPage(query, unread)}><RefreshCw aria-hidden="true" />{t('emailHub.retry')}</button></div></div>}
        {state === 'loading' && <p className="muted mailPad" role="status">{t('emailHub.listLoading')}</p>}
        {state === 'error' && <div className="mailPad"><Notice note={{ text: t('emailHub.listFailed', { reason: error }), error: true }} />
          <div className="actions"><button type="button" onClick={() => void loadPage(query, unread)}><RefreshCw aria-hidden="true" />{t('emailHub.retry')}</button></div></div>}
        {state === 'ready' && items.length === 0 && <div className="paneEmpty"><Mail aria-hidden="true" />
          {filtered ? <><h3>{t('emailHub.noMatch.title')}</h3><p>{t('emailHub.noMatch.text')}</p><div className="actions"><button type="button" className="secondary" onClick={clear}>{t('emailHub.clear')}</button></div></>
            : <><h3>{t('emailHub.empty.title')}</h3><p>{found && found.items.length ? t('emailHub.empty.text') : t('emailHub.empty.none')}</p><div className="actions">
              <button type="button" className="secondary" onClick={() => void loadPage(query, unread)}><RefreshCw aria-hidden="true" />{t('emailHub.checkAgain')}</button>
              <button type="button" className="secondary" onClick={onOpenConnections}><Plug aria-hidden="true" />{t('emailHub.connections')}</button>
            </div></>}
        </div>}
        {items.length > 0 && <ul className="mailRows">{items.map((m) => {
          const active = selected?.kind === 'message' && selected.ref === m.ref;
          const subject = m.subject || t('emailHub.noSubject');
          return <li key={m.ref} className={`mailRow hubRow ${m.unread ? 'unread' : ''} ${active ? 'selected' : ''}`}>
            <button type="button" className="mailRowOpen" aria-current={active ? 'true' : undefined} onClick={() => openMessage(m)}>
              <span className="mailRowTop"><strong dir="auto">{m.from_name || m.from_address || t('emailHub.unknownSender')}</strong><time dateTime={m.received_at} title={when(m.received_at)}>{listTime(m.received_at)}</time></span>
              <span className="mailRowSubject" dir="auto">{m.unread && <span className="mailUnreadDot" role="img" aria-label={t('emailHub.unread')} />}{subject}{m.has_attachments && <Paperclip className="mailClip" role="img" aria-label={t('emailHub.attachment')} />}</span>
              {m.snippet && <span className="hubRowSnippet" dir="auto">{m.snippet}</span>}
              <span className="hubRowMeta"><SourceBadge source={m.source} /><span dir="ltr" className="hubAddress">{m.mailbox_address}</span></span>
            </button>
          </li>;
        })}</ul>}
        {state === 'ready' && cursor !== null && <div className="actions mailPad"><button type="button" className="secondary" disabled={busy === 'more'}
          onClick={() => { setBusy('more'); void loadPage(query, unread, cursor).finally(() => setBusy(null)); }}>{t('emailHub.more')}</button></div>}
      </section>

      <section data-pane="detail" className="pane mailDetail hubReader" aria-label={t('emailHub.reader.label')} tabIndex={0}>
        {open && <div className="mailAllContext">
          <SourceBadge source={open.source} /><span dir="ltr" className="hubAddress">{open.mailbox_address}</span>
          <button type="button" className="linkButton" onClick={() => onOpenMailbox(`${open.backend}:${open.mailbox_id}`)}>{t('emailHub.openMailbox', { mailbox: open.mailbox_address })}</button>
        </div>}
        {open?.backend === 'provider' && open.message_id && <MessagePanel key={open.ref} messageId={open.message_id} canAct={Boolean(found?.can_act)} owner={owner} focusSignal={picked}
          onBack={() => setSelected(null)} onChanged={(changed) => setItems((list) => list.map((item) => item.ref === open.ref ? { ...item, unread: !changed.is_read } : item))}
          onSelect={() => undefined} onBudget={setBudget}
          onOpenApprovals={onOpenApprovals} onDraftsChanged={() => undefined} />}
        {open?.backend === 'server' && open.server_message && serverAccount && <>
          {open.read_only && <p className="notice mailsShared" role="note"><Share2 aria-hidden="true" />{t('maila.readOnly')}</p>}
          <Reader key={open.ref} account={serverAccount} folder={inboxFolder()} uid={open.uid ?? 0} message={open.server_message} canAct={canActServer} busy={busy !== null}
            moves={[]} onBack={() => setSelected(null)} onAction={(action) => void flag(open, action)} onCompose={(kind) => void compose(open, kind)}
            onOpenApprovals={onOpenApprovals} onDrafted={(draft: MailDraft) => setSelected({ kind: 'draft', id: draft.id, accountId: draft.account_id })} />
        </>}
        {open?.backend === 'server' && !serverAccount && <p className="muted mailPad" role="status">{t('emailHub.readerLoading')}</p>}
        {selected?.kind === 'draft' && draftAccount && setup && <Composer key={selected.id} draftId={selected.id} account={draftAccount} setup={setup} owner={owner} onOpenApprovals={onOpenApprovals}
          onBack={() => setSelected(null)} onChanged={() => undefined} onClosed={() => setSelected(null)} onSent={() => undefined} />}
        {selected?.kind === 'message' && !open && <div className="mailPad"><Notice note={{ text: t('emailHub.gone'), error: true }} /></div>}
        {!selected && wide && <div className="paneEmpty"><MailOpen aria-hidden="true" /><h3>{t('emailHub.reader.empty')}</h3><p className="muted">{t('emailHub.reader.text')}</p></div>}
      </section>
    </SplitView>
  </>;
}

/** Every connection on one page: figures, one table of every mailbox (provider and own server) with its actions, and the send controls. */
function HubConnections({ found, load, write, onRetry, onManageProviders, onConnectServer, onOpenMailbox }: {
  found: UnifiedMailboxes | null; load: Load; write: ReactNode; onRetry: () => void; onManageProviders: () => void; onConnectServer: () => void;
  onOpenMailbox: (ref: string, settings: boolean) => void;
}) {
  const header = <PageHeader title={viewLabel('email')} subtitle={t('emailHub.connections.subtitle')} actions={write} />;
  if (load.state === 'loading') return <>{header}<p className="notice" role="status" aria-busy="true"><CircleDot aria-hidden="true" />{t('emailHub.loading')}</p></>;
  if (load.state === 'error' || !found) return <>{header}<Notice note={{ text: t('emailHub.loadFailed', { reason: load.state === 'error' ? load.reason : '' }), error: true }} />
    <div className="actions"><button type="button" onClick={onRetry}><RefreshCw aria-hidden="true" />{t('emailHub.retry')}</button></div></>;
  const providers = found.items.filter((m) => m.backend === 'provider');
  const servers = found.items.filter((m) => m.backend === 'server');
  const attention = found.items.filter(needsAttention).length;
  const notes = (['provider', 'server'] as const).map((backend) => ({ backend, info: backend === 'provider' ? found.provider : found.server }))
    .filter(({ info }) => info.state !== 'ok' && info.state !== 'none');
  const connect = <div className="hubConnectButtons">
    <button type="button" className="secondary" onClick={onManageProviders}><Plug aria-hidden="true" />{found.can_manage ? t('emailHub.connections.connectProvider') : t('emailHub.connections.manageProvider')}</button>
    {found.server.state === 'ok' && found.can_act && <button type="button" className="secondary" onClick={onConnectServer}><Plug aria-hidden="true" />{t('emailHub.connections.connectServer')}</button>}
  </div>;
  return <>
    {header}
    <KpiRow label={t('emailHub.conn.summary')}>
      <KpiCard icon={<Mail />} label={t('emailHub.conn.kpi.mailboxes')} value={formatNumber(found.items.length)} hint={t('emailHub.conn.kpi.mailboxesHint')} />
      <KpiCard icon={<AtSign />} label={t('emailHub.conn.kpi.providers')} value={formatNumber(providers.length)} hint={t('emailHub.conn.kpi.providersHint')} />
      <KpiCard icon={<Server />} label={t('emailHub.conn.kpi.server')} value={formatNumber(servers.length)} hint={t('emailHub.conn.kpi.serverHint')} />
      <KpiCard icon={<AlertTriangle />} label={t('emailHub.conn.kpi.attention')} value={formatNumber(attention)} tone={attention ? 'warn' : ''}
        hint={attention ? t('emailHub.conn.kpi.attentionHint') : t('emailHub.conn.kpi.allWell')} />
    </KpiRow>
    {notes.map(({ backend, info }) => <p key={backend} className={`notice ${info.state === 'error' ? 'errorNotice' : ''}`} role={info.state === 'error' ? 'alert' : 'note'}><CircleDot aria-hidden="true" />{info.detail ?? t('emailHub.backendFailed')}</p>)}
    <div className="hubConnBoard">
      <FinCard title={t('emailHub.conn.table', { count: found.items.length })} icon={<Mail />} actions={connect} className="hubConnTableCard">
        {found.items.length === 0 ? <div className="paneEmpty"><Mail aria-hidden="true" /><h3>{t('emailHub.conn.emptyTitle')}</h3><p>{t('emailHub.connections.none')}</p></div>
          : <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('emailHub.conn.tableLabel')}><table className="finGrid hubConnTable">
            <thead><tr><th scope="col">{t('emailHub.conn.col.mailbox')}</th><th scope="col">{t('emailHub.conn.col.connection')}</th><th scope="col">{t('emailHub.conn.col.access')}</th><th scope="col"><span className="visuallyHidden">{t('emailHub.conn.col.actions')}</span></th></tr></thead>
            <tbody>{found.items.map((m) => <tr key={m.ref} className="hubConnRow">
              <th scope="row"><span className="hubConnWho"><span className={`hubRailMark hubRailMark-${m.source}`} aria-hidden="true">{m.source === 'mailserver' ? <Server /> : <AtSign />}</span>
                <span className="hubConnText"><strong dir="ltr" className="hubAddress">{m.address}</strong><SourceBadge source={m.source} /></span></span></th>
              <td><StatusChip tone={needsAttention(m) ? 'warn' : 'ok'}>{needsAttention(m) ? t('emailHub.attention') : t('emailHub.connected')}</StatusChip></td>
              <td>{t(`emailHub.access.${m.access}` as Parameters<typeof t>[0])}</td>
              <td><div className="actions hubConnActions">
                <button type="button" className="secondary" onClick={() => onOpenMailbox(m.ref, false)} aria-label={t('emailHub.connections.openLabel', { address: m.address })}><Mail aria-hidden="true" />{t('emailHub.connections.open')}</button>
                <button type="button" className="secondary" onClick={() => onOpenMailbox(m.ref, true)} aria-label={t('emailHub.connections.settingsLabel', { address: m.address })}><Settings aria-hidden="true" />{t('emailHub.connections.settings')}</button>
              </div></td>
            </tr>)}</tbody>
          </table></div>}
      </FinCard>
      <FinCard title={t('emailHub.conn.controls')} icon={<ShieldCheck />} className="hubControls">
        <ul className="hubControlList">
          <li><CheckCircle2 aria-hidden="true" /><div><strong>{t('emailHub.conn.ctl.approval')}</strong><p>{t('emailHub.conn.ctl.approvalText')}</p></div></li>
          <li><CheckCircle2 aria-hidden="true" /><div><strong>{t('emailHub.conn.ctl.secrets')}</strong><p>{t('emailHub.conn.ctl.secretsText')}</p></div></li>
          <li><CheckCircle2 aria-hidden="true" /><div><strong>{t('emailHub.conn.ctl.reading')}</strong><p>{t('emailHub.conn.ctl.readingText')}</p></div></li>
          <li><CheckCircle2 aria-hidden="true" /><div><strong>{t('emailHub.conn.ctl.sharing')}</strong><p>{t('emailHub.conn.ctl.sharingText')}</p></div></li>
        </ul>
      </FinCard>
    </div>
  </>;
}
