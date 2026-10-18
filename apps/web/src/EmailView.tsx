// Email Center (docs/email.md#email-center): the person's own mailbox on the workspace mail server.
// Folders with unread counts, a paged list with search and filters, the message in a sandboxed frame
// (sanitized on the server, nothing remote loads), reply, reply all, forward and new messages written
// as drafts, and sending only after an owner approves the exact message (payload hash shown). Flags,
// moves, and delete for good (an approval too) act on one message or a selection. Phase 3: several
// mailboxes (own and shared), sharing, sender identities and contacts (MailDirectory.tsx).
import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowLeft, AtSign, CircleDot, Contact, Download, FileText, Forward, Lock, Mail, MailOpen, Paperclip, PenLine, Plug, RefreshCw, Reply, ReplyAll, Send, Settings, ShieldCheck, Trash2, Unplug,
} from 'lucide-react';
import * as api from './lib/api';
import { shortHash } from './lib/approvals';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import {
  defaultFolder, draftTone, formatBytes, isEditable, joinRecipients, listPerson, newSendKey, parseRecipients, personLabel, readerDocument, sortFolders,
  type MailAccount, type MailDetail, type MailDraft, type MailDraftKind, type MailFolder, type MailMessage, type MailSetup,
} from './lib/mail';
import {
  checkMailbox, connectMailbox, createMailDraft, deleteMailDraft, disconnectMailbox, downloadMailAttachment, getMailDraft, getMailSetup,
  listMail, listMailDrafts, listMailFolders, readMail, sendMailDraft, toApproval, updateMailDraft, updateMailboxPassword,
} from './lib/mailApi';
import {
  NO_FILTERS, adjustFolders, applyFlag, availableMoves, badgeText, filtersActive, flagFor, inboxUnread, moveTarget, neighbour, nextAfterRemoval,
  pruneSelection, removeUids, toggleAll, toggleSelection, type MailAction, type MailFilters,
} from './lib/mailActions';
import { moveMail, requestMailDelete, setMailFlag } from './lib/mailActionsApi';
import { approvalSendAt } from './lib/mailOutgoing';
import { cancelMailSchedule } from './lib/mailOutgoingApi';
import { ComposerFiles, ScheduledRail, SchedulePicker } from './MailOutgoing';
import { publishMailUnread } from './lib/mailUnread';
import { viewLabel } from './lib/shell';
import { Drawer } from './Dialog';
import { MailAssist } from './MailAssist';
import { MailListTools, RowLabels, useMailLabels } from './MailListAi';
import { DeleteApproval, MailBulkBar, MailSearchBar, StarButton, SyncStatus, actionDone, actionLabel, useMailShortcuts, useMailSync } from './MailActions';
import { ForwardingRulesPanel, RetentionPanel, RulesOwnerOnly } from './MailAutomation';
import { ContactsPanel, FromPicker, IdentitiesPanel, LeaveShared, MailboxPicker, RecipientField, SharedNotice, SharingPanel } from './MailDirectory';
import { canSendFrom, ownsMailbox, pickAccount, senderLabel, type MailContact } from './lib/mailDirectory';
import { PageHeader, RAIL_QUERY, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import { useIsOwner } from './useIsOwner';
import './email.css';

type Note = { text: string; error?: boolean } | null;
type Pane = { kind: 'message'; uid: number } | { kind: 'draft'; id: string } | null;
type Approval = ReturnType<typeof toApproval>;

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });
const PAGE = 25;
const MAILBOX_KEY = 'anum.mail.mailbox';

/** The mailbox this viewer last opened (a per-browser convenience; storage may be unavailable). */
function rememberedMailbox(): string | null {
  try { return window.localStorage.getItem(MAILBOX_KEY); } catch { return null; }
}
function rememberMailbox(id: string): void {
  try { window.localStorage.setItem(MAILBOX_KEY, id); } catch { /* private mode: not remembered */ }
}

function Notice({ note }: { note: Note }) {
  if (!note) return null;
  return <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{note.text}</p>;
}

/** Problems with the mail server read as the fix, not as a status code. */
function mailError(e: unknown): string {
  if (e instanceof ApiError && e.status === 503) return t('mail.offline');
  return describeError(e);
}

export function folderLabel(folder: MailFolder): string {
  return folder.role ? t(`mail.folder.${folder.role}` as MessageKey) : folder.name;
}

export function EmailView({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const owner = useIsOwner() === true;
  const [setup, setSetup] = useState<MailSetup | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [loadError, setLoadError] = useState('');
  const [note, setNote] = useState<Note>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [contactsOpen, setContactsOpen] = useState(false);
  const [accountId, setAccountId] = useState<string | null>(() => rememberedMailbox());
  const [openDraft, setOpenDraft] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState('loading');
    try { setSetup(await getMailSetup()); setState('ready'); }
    catch (e) { if (isPermissionDenied(e)) setState('denied'); else { setLoadError(describeError(e)); setState('error'); } }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const title = viewLabel('email');
  const head = (actions?: ReactNode) => <PageHeader title={title} subtitle={t('mail.subtitle')} actions={actions} />;
  if (state === 'loading' && !setup) return <>{head()}<p className="notice" role="status" aria-busy="true"><CircleDot aria-hidden="true" />{t('mail.loading')}</p></>;
  if (state === 'denied') return <>{head()}<Panel icon={<Lock />} title={t('mail.denied.title')} text={t('mail.denied.text')} /></>;
  if (state === 'error' || !setup) return <>{head()}<Notice note={{ text: t('mail.loadFailed', { reason: loadError }), error: true }} /><div className="actions"><button type="button" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div></>;
  if (!setup.enabled) return <>{head()}<Panel icon={<Plug />} title={t('mail.off.title')} text={t('mail.off.text')} /></>;
  if (!setup.configured) return <>{head()}<Panel icon={<Plug />} title={t('mail.notConfigured.title')} text={t('mail.notConfigured.text')} /></>;
  if (!setup.tenant_allowed) return <>{head()}<Panel icon={<Lock />} title={t('mail.tenant.title')} text={t('mail.tenant.text')} /></>;

  const account = pickAccount(setup.accounts, accountId);
  if (!account) {
    return <>{head()}<Notice note={note} />
      {setup.can_act ? <ConnectForm setup={setup} onConnected={(a) => { setNote({ text: t('mail.connect.done', { address: a.address }) }); void load(); }} />
        : <Panel icon={<Lock />} title={t('mail.connect.title')} text={t('mail.connect.readOnly')} />}
    </>;
  }

  const pick = (id: string) => { setAccountId(id); rememberMailbox(id); setNote(null); };
  const writable = setup.can_act && canSendFrom(account);
  async function writeTo(contact: MailContact) {
    if (!account) return;
    setContactsOpen(false); setNote(null);
    try {
      const draft = await createMailDraft({ account_id: account.id, kind: 'new' });
      await updateMailDraft(draft.id, { to: [contact.email] });
      setOpenDraft(draft.id);
    } catch (e) { setNote({ text: t('mail.composer.startFailed', { reason: mailError(e) }), error: true }); }
  }

  return <div className="mailLayout">
    <Mailbox key={account.id} setup={setup} account={account} owner={owner} note={note} setNote={setNote} onOpenApprovals={onOpenApprovals}
      openDraft={openDraft} onDraftOpened={() => setOpenDraft(null)}
      header={(actions) => head(<>
        <MailboxPicker accounts={setup.accounts} currentId={account.id} onPick={pick} />
        <button type="button" className={`chipButton ${account.status === 'connected' ? 'success' : 'warning'}`} aria-haspopup="dialog" onClick={() => setSettingsOpen(true)}>
          {/* With the mailbox picker beside it, the chip names the status; otherwise the address. */}
          <span className="chipDot" aria-hidden="true" />{setup.accounts.length > 1
            ? <span>{t(`mail.status.${account.status}` as MessageKey)}</span>
            : <><span>{account.address}</span><span className="visuallyHidden">{t(`mail.status.${account.status}` as MessageKey)}</span></>}
        </button>
        <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setContactsOpen(true)}><Contact aria-hidden="true" />{t('mails.contacts.open')}</button>
        <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => setSettingsOpen(true)}><Settings aria-hidden="true" />{t('mail.settings')}</button>
        {actions}
      </>)} />
    {settingsOpen && <Drawer title={t('mail.settings')} onClose={() => setSettingsOpen(false)}>
      {ownsMailbox(account) ? <>
        <MailboxSettings setup={setup} account={account} onChanged={(next) => setSetup((s) => s ? { ...s, accounts: s.accounts.map((a) => a.id === next.id ? { ...a, ...next } : a) } : s)}
          onDisconnected={() => { setSettingsOpen(false); setNote({ text: t('mail.settings.disconnected', { address: account.address }) }); publishMailUnread(0); void load(); }} />
        {setup.can_act && <SharingPanel account={account} onCount={(count) => setSetup((s) => s ? { ...s, accounts: s.accounts.map((a) => a.id === account.id ? { ...a, shared_with: count } : a) } : s)} />}
        {setup.can_act && <IdentitiesPanel account={account} setup={setup} />}
        <RetentionPanel account={account} owner={owner} onOpenApprovals={onOpenApprovals} />
        {setup.can_act && <ForwardingRulesPanel account={account} owner={owner} onOpenApprovals={onOpenApprovals} />}
      </> : <>
        <LeaveShared account={account} onLeft={() => { setSettingsOpen(false); setNote({ text: t('mails.leave.done', { address: account.address }) }); setAccountId(null); void load(); }} />
        <RetentionPanel account={account} owner={owner} onOpenApprovals={onOpenApprovals} />
        <RulesOwnerOnly />
      </>}
      {setup.can_act && <details className="mailsAdd"><summary><Plug aria-hidden="true" />{t('mails.mailbox.add')}</summary>
        <p className="muted">{t('mails.mailbox.addText')}</p>
        <ConnectForm setup={setup} onConnected={(a) => { setSettingsOpen(false); pick(a.id); setNote({ text: t('mail.connect.done', { address: a.address }) }); void load(); }} />
      </details>}
    </Drawer>}
    {contactsOpen && <Drawer title={t('mails.contacts.title')} onClose={() => setContactsOpen(false)}>
      <ContactsPanel canAct={setup.can_act} onWrite={writable ? (c) => void writeTo(c) : undefined} />
    </Drawer>}
  </div>;
}

function Panel({ icon, title, text, children }: { icon: ReactNode; title: string; text: string; children?: ReactNode }) {
  const id = useId();
  return <section className="surface mailPanel" aria-labelledby={id}><span className="mailPanelIcon" aria-hidden="true">{icon}</span><div><h2 id={id}>{title}</h2><p className="muted">{text}</p>{children}</div></section>;
}

function ConnectForm({ setup, onConnected }: { setup: MailSetup; onConnected: (account: MailAccount) => void }) {
  const [address, setAddress] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { a: useId(), p: useId(), n: useId(), title: useId() };
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    try { const account = await connectMailbox({ address: address.trim(), password, ...(name.trim() ? { display_name: name.trim() } : {}) }); setPassword(''); onConnected(account); }
    catch (e) { setError(t('mail.connect.failed', { reason: mailError(e) })); }
    finally { setBusy(false); }
  }
  return <section className="surface mailConnect" aria-labelledby={ids.title}>
    <div className="mailConnectText"><AtSign aria-hidden="true" /><h2 id={ids.title}>{t('mail.connect.title')}</h2><p className="muted">{t('mail.connect.text', { domains: setup.allowed_domains.join(', ') })}</p></div>
    <form className="mailConnectForm" onSubmit={(e) => void submit(e)}>
      <label className="field" htmlFor={ids.a}><span>{t('mail.connect.address')}</span><input id={ids.a} type="email" autoComplete="username" required maxLength={320} value={address} onChange={(e) => setAddress(e.target.value)} /></label>
      <label className="field" htmlFor={ids.p}><span>{t('mail.connect.password')}</span><input id={ids.p} type="password" autoComplete="current-password" required maxLength={500} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      <label className="field" htmlFor={ids.n}><span>{t('mail.connect.name')}</span><input id={ids.n} autoComplete="name" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></label>
      {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions"><button type="submit" disabled={busy || !address.trim() || !password}><Plug aria-hidden="true" />{busy ? t('mail.connect.checking') : t('mail.connect.submit')}</button></div>
    </form>
  </section>;
}

function Mailbox({ setup, account, owner, note, setNote, header, onOpenApprovals, openDraft, onDraftOpened }: {
  setup: MailSetup; account: MailAccount; owner: boolean; note: Note; setNote: (n: Note) => void; header: (actions: ReactNode) => ReactNode; onOpenApprovals?: () => void;
  openDraft?: string | null; onDraftOpened?: () => void;
}) {
  const wide = useMediaQuery(SPLIT_QUERY);
  const widest = useMediaQuery(RAIL_QUERY);
  // A mailbox shared to read only: nothing here changes it (the API refuses the same).
  const canAct = setup.can_act && canSendFrom(account);
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [foldersState, setFoldersState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [foldersError, setFoldersError] = useState('');
  const [folderId, setFolderId] = useState<string | null>(null);
  const [filters, setFilters] = useState<MailFilters>(NO_FILTERS);
  const [items, setItems] = useState<MailMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [listState, setListState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [listError, setListError] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [pane, setPane] = useState<Pane>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ approval: Approval; count: number; folder: string } | null>(null);
  const [newMail, setNewMail] = useState<number>(0);
  const [drafts, setDrafts] = useState<MailDraft[] | null>(null);
  const [draftsFailed, setDraftsFailed] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const folder = folders.find((f) => f.id === folderId) ?? null;
  const mailLabels = useMailLabels(account.id, folderId, items);

  const loadFolders = useCallback(async () => {
    const found = sortFolders(await listMailFolders(account.id));
    return found;
  }, [account.id]);

  const applyFolders = useCallback((found: MailFolder[]) => {
    setFolders(found); setFoldersState('ready');
    publishMailUnread(inboxUnread(found));
    setFolderId((current) => current && found.some((f) => f.id === current) ? current : defaultFolder(found)?.id ?? null);
  }, []);

  const loadDrafts = useCallback(() => {
    listMailDrafts(account.id).then((value) => { setDrafts(value); setDraftsFailed(false); }, () => setDraftsFailed(true));
  }, [account.id]);

  useEffect(() => {
    loadFolders().then(applyFolders, (e) => { setFoldersError(mailError(e)); setFoldersState('error'); setListState('error'); setListError(mailError(e)); });
    loadDrafts();
  }, [loadFolders, applyFolders, loadDrafts]);

  const loadList = useCallback(async (id: string, next: MailFilters, before: number | null = null) => {
    if (!before) { setListState('loading'); setNewMail(0); }
    try {
      const page = await listMail(account.id, id, { before, limit: PAGE, q: next.q || undefined, unread: next.unread, flagged: next.flagged });
      setItems((current) => before ? [...current, ...page.items.filter((m) => !current.some((c) => c.uid === m.uid))] : page.items);
      setTotal(page.total); setNextBefore(page.next_before); setListState('ready'); setListError('');
      if (!before) setSelected((s) => pruneSelection(page.items, s));
    } catch (e) {
      if (before) { setNote({ text: `${t('mail.list.moreFailed')} ${mailError(e)}`, error: true }); return; }
      setListState('error'); setListError(mailError(e));
    }
  }, [account.id, setNote]);

  useEffect(() => { if (folderId) void loadList(folderId, filters); }, [folderId, filters, loadList]);
  useEffect(() => {
    if (!openDraft) return;
    setPane({ kind: 'draft', id: openDraft }); loadDrafts(); onDraftOpened?.();
  }, [openDraft, loadDrafts, onDraftOpened]);

  const sync = useMailSync({
    enabled: foldersState === 'ready',
    load: loadFolders,
    onFolders: applyFolders,
    onNewMail: (ids, after, before) => {
      if (folderId && ids.includes(folderId)) {
        const gained = (after.find((f) => f.id === folderId)?.unread ?? 0) - (before.find((f) => f.id === folderId)?.unread ?? 0);
        setNewMail((n) => n + Math.max(1, gained));
      }
    },
  });

  function pickFolder(id: string) {
    setFolderId(id); setSelected(new Set()); setAnchor(null); setPane(null); setCursor(null); setPendingDelete(null);
  }
  function open(uid: number) {
    setPane({ kind: 'message', uid }); setCursor(uid);
    const message = items.find((m) => m.uid === uid);
    if (message && !message.seen && canAct && folderId) {
      setItems((list) => applyFlag(list, [uid], 'seen', true));
      setFolders((list) => { const next = adjustFolders(list, { folderId, unreadDelta: -1 }); publishMailUnread(inboxUnread(next)); return next; });
    }
  }

  /** One flag, move or delete for good on `uids` in the open folder. */
  async function act(action: MailAction, uids: number[]) {
    if (!folder || !uids.length) return;
    const picked = items.filter((m) => uids.includes(m.uid));
    if (action === 'deleteForever') {
      if (!window.confirm(t('mailc.delete.confirm', { count: uids.length }))) return;
      setBusy('delete'); setNote(null);
      try {
        const result = await requestMailDelete(account.id, folder.id, uids);
        if (result.approval) setPendingDelete({ approval: toApproval(result.approval), count: uids.length, folder: folderLabel(folder) });
      } catch (e) { setNote({ text: t('mailc.failed', { reason: mailError(e) }), error: true }); } finally { setBusy(null); }
      return;
    }
    setBusy(action); setNote(null);
    try {
      const flag = flagFor(action);
      if (flag) {
        await setMailFlag(account.id, folder.id, uids, flag.flag, flag.value);
        setItems((list) => applyFlag(list, uids, flag.flag, flag.value));
        if (flag.flag === 'seen') {
          const changed = picked.filter((m) => m.seen !== flag.value).length;
          setFolders((list) => { const next = adjustFolders(list, { folderId: folder.id, unreadDelta: flag.value ? -changed : changed }); publishMailUnread(inboxUnread(next)); return next; });
        }
      } else {
        const target = moveTarget(action);
        if (!target) return;
        await moveMail(account.id, folder.id, uids, target);
        const unread = picked.filter((m) => !m.seen).length;
        if (pane?.kind === 'message') {
          const next = nextAfterRemoval(items, uids, pane.uid);
          if (next !== pane.uid) setPane(next !== null && wide ? { kind: 'message', uid: next } : null);
        }
        setItems((list) => removeUids(list, uids));
        setTotal((n) => Math.max(0, n - uids.length));
        setSelected((s) => new Set([...s].filter((u) => !uids.includes(u))));
        setFolders((list) => { const next = adjustFolders(list, { folderId: folder.id, unreadDelta: -unread, totalDelta: -uids.length, targetRole: target, movedUnread: unread, moved: uids.length }); publishMailUnread(inboxUnread(next)); return next; });
      }
      setNote({ text: actionDone(action as Exclude<MailAction, 'deleteForever'>, uids.length) });
    } catch (e) { setNote({ text: t('mailc.failed', { reason: mailError(e) }), error: true }); }
    finally { setBusy(null); }
  }

  async function startDraft(kind: MailDraftKind, uid?: number) {
    setBusy('compose'); setNote(null);
    try {
      const draft = await createMailDraft({ account_id: account.id, kind, ...(kind !== 'new' && folder && uid ? { folder: folder.id, uid } : {}) });
      setDrafts((list) => [draft, ...(list ?? [])]);
      setPane({ kind: 'draft', id: draft.id });
    } catch (e) { setNote({ text: t('mail.composer.startFailed', { reason: mailError(e) }), error: true }); }
    finally { setBusy(null); }
  }

  // Deleting for good is the mailbox owner's alone.
  const moves = folder ? availableMoves(folder.role, folders).filter((m) => m !== 'deleteForever' || ownsMailbox(account)) : [];
  useMailShortcuts(true, (shortcut) => {
    if (shortcut === 'search') { searchRef.current?.focus(); return; }
    if (shortcut === 'next' || shortcut === 'previous') {
      const next = neighbour(items, cursor, shortcut === 'next' ? 1 : -1);
      if (next !== null) { setCursor(next); listRef.current?.querySelector<HTMLElement>(`[data-uid="${next}"]`)?.focus(); }
      return;
    }
    const target = cursor ?? (pane?.kind === 'message' ? pane.uid : null);
    if (target === null) return;
    const message = items.find((m) => m.uid === target);
    if (!message) return;
    if (shortcut === 'open') open(target);
    else if (shortcut === 'select') setSelected((s) => toggleSelection(items, s, target, null, false));
    else if (!canAct) return;
    else if (shortcut === 'archive' && moves.includes('archive')) void act('archive', [target]);
    else if (shortcut === 'trash' && moves.includes('trash')) void act('trash', [target]);
    else if (shortcut === 'star') void act(message.flagged ? 'unstar' : 'star', [target]);
    else if (shortcut === 'toggleRead') void act(message.seen ? 'unread' : 'read', [target]);
  });

  const draftsWaiting = (drafts ?? []).filter((d) => d.status !== 'sent');
  const show = pane ? 'detail' : 'list';
  const listTitleId = useId();
  const emptyText = folder?.role === 'inbox' ? t('mail.list.emptyInbox') : t('mail.list.empty', { folder: folder ? folderLabel(folder) : '' });

  return <>
    {header(<>
      {canAct && <button type="button" disabled={busy !== null} onClick={() => void startDraft('new')}><PenLine aria-hidden="true" />{t('mail.compose')}</button>}
    </>)}
    <Notice note={note} />
    <SharedNotice account={account} />
    {account.status === 'auth_failed' && <Notice note={{ text: t('mail.authFailed'), error: true }} />}
    <SplitView className="mailSplit" layout="three" show={show} label={t('mail.folders')}>
      <aside data-pane="nav" className="pane mailNav" aria-label={t('mail.folders')}>
        <MailSearchBar filters={filters} inputRef={searchRef} busy={listState === 'loading'} onApply={(next) => { setFilters(next); setSelected(new Set()); setPane(null); }} />
        {foldersState === 'loading' && <p className="muted" role="status">{t('mail.list.loading')}</p>}
        {foldersState === 'error' && <div><Notice note={{ text: foldersError, error: true }} /><button type="button" className="secondary" onClick={() => { setFoldersState('loading'); loadFolders().then(applyFolders, (e) => { setFoldersError(mailError(e)); setFoldersState('error'); }); }}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div>}
        {folders.length > 0 && <>
          <label className="mailFolderSelect"><span className="visuallyHidden">{t('mail.folderPick')}</span>
            <select value={folderId ?? ''} onChange={(e) => pickFolder(e.target.value)}>{folders.map((f) => <option key={f.id} value={f.id}>{folderLabel(f)}{f.unread ? ` (${badgeText(f.unread)})` : ''}</option>)}</select>
          </label>
          <nav className="mailFolders" aria-label={t('mail.folders')}><ul>{folders.map((f) => <li key={f.id}>
            <button type="button" className={`mailFolder ${f.id === folderId ? 'active' : ''}`} aria-current={f.id === folderId ? 'true' : undefined} onClick={() => pickFolder(f.id)}>
              <span dir="auto">{folderLabel(f)}</span>
              {f.unread > 0 && f.role !== 'trash' && <span className="mailcBadge" aria-label={t('mail.folder.unread', { count: f.unread })}>{badgeText(f.unread)}</span>}
            </button>
          </li>)}</ul></nav>
        </>}
        <SyncStatus state={sync} onCheckNow={sync.checkNow} />
        <details className="mailOutbox" open={widest || undefined}>
          <summary><FileText aria-hidden="true" />{t('mail.outbox')}{drafts && <span className="mailOutboxCount">{formatNumber(draftsWaiting.length)}</span>}</summary>
          <p className="muted">{t('mail.outboxHint')}</p>
          {draftsFailed && <p className="muted">{t('mail.list.moreFailed')} <button type="button" className="linkButton" onClick={loadDrafts}>{t('mail.retry')}</button></p>}
          {drafts && drafts.length === 0 && <p className="muted">{t('mail.draft.empty')}</p>}
          {drafts && drafts.length > 0 && <ul className="railList">{drafts.slice(0, 20).map((d) => <li key={d.id}>
            <button type="button" className="railItem" aria-current={pane?.kind === 'draft' && pane.id === d.id ? 'true' : undefined} onClick={() => setPane({ kind: 'draft', id: d.id })}>
              <strong dir="auto">{d.subject || t('mail.list.noSubject')}</strong>
              <small><span className={`statusBadge ${draftTone(d.status)}`}>{t(`mail.draft.status.${d.status}` as MessageKey)}</span> {d.to.join(', ') || t('mail.draft.noRecipient')}</small>
            </button>
          </li>)}</ul>}
        </details>
        <ScheduledRail accountId={account.id} version={drafts} open={widest} current={pane?.kind === 'draft' ? pane.id : null} onOpen={(id) => setPane({ kind: 'draft', id })} />
      </aside>

      <section data-pane="list" className="pane mailList" aria-labelledby={listTitleId} aria-busy={listState === 'loading'}>
        <div className="paneHead"><h2 id={listTitleId}>{folder ? folderLabel(folder) : t('mail.folders')}</h2>{listState === 'ready' && <span className="muted" aria-live="polite">{filtersActive(filters) ? t('mailc.search.results', { count: total }) : t('mail.list.shown', { shown: formatNumber(items.length), total: formatNumber(total) })}</span>}</div>
        {canAct && listState === 'ready' && items.length > 0 && <MailBulkBar items={items} selected={selected} moves={moves} busy={busy !== null}
          onToggleAll={() => setSelected((s) => toggleAll(items, s))} onClear={() => setSelected(new Set())}
          onAction={(action) => void act(action, items.filter((m) => selected.has(m.uid)).map((m) => m.uid))} />}
        {pendingDelete && <DeleteApproval approval={pendingDelete.approval} count={pendingDelete.count} folderName={pendingDelete.folder} owner={owner} onOpenApprovals={onOpenApprovals}
          onDismiss={() => setPendingDelete(null)}
          onDeleted={() => { setPendingDelete(null); setSelected(new Set()); setPane(null); setNote({ text: t('mailc.delete.done') }); if (folderId) void loadList(folderId, filters); sync.checkNow(); }} />}
        {folder && <MailListTools accountId={account.id} folderId={folder.id} folderName={folderLabel(folder)} items={items} selected={selected} labels={mailLabels.labels}
          canAct={canAct} owner={owner} onLabels={mailLabels.add} onSelect={(uids) => setSelected(new Set(uids))} onOpenApprovals={onOpenApprovals}
          onArchived={(uids) => { setSelected((s) => new Set([...s].filter((u) => !uids.includes(u)))); if (pane?.kind === 'message' && uids.includes(pane.uid)) setPane(null); if (folderId) void loadList(folderId, filters); sync.checkNow(); }} />}
        {newMail > 0 && folder && <p className="notice mailNewMail" role="status"><CircleDot aria-hidden="true" />{t('mailc.sync.newMail', { count: newMail, folder: folderLabel(folder) })}<button type="button" className="secondary" onClick={() => folderId && void loadList(folderId, filters)}>{t('mailc.sync.showNew')}</button></p>}
        {listState === 'loading' && <p className="muted mailPad" role="status">{t('mail.list.loading')}</p>}
        {listState === 'error' && <div className="mailPad"><Notice note={{ text: t('mail.list.failed', { reason: listError }), error: true }} /><div className="actions"><button type="button" onClick={() => folderId ? void loadList(folderId, filters) : window.location.reload()}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div></div>}
        {listState === 'ready' && items.length === 0 && <div className="paneEmpty"><Mail aria-hidden="true" />
          {filtersActive(filters) ? <><h3>{t('mailc.search.noMatch', { folder: folder ? folderLabel(folder) : '' })}</h3><p>{t('mailc.search.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={() => setFilters(NO_FILTERS)}>{t('mailc.search.clear')}</button></div></>
            : <><h3>{emptyText}</h3>{canAct && folder?.role === 'inbox' && <div className="actions"><button type="button" className="secondary" onClick={() => void startDraft('new')}><PenLine aria-hidden="true" />{t('mail.compose')}</button></div>}</>}
        </div>}
        {listState === 'ready' && items.length > 0 && <ul ref={listRef} className="mailRows">{items.map((m) => {
          const person = listPerson(m, folder?.role ?? null);
          const subject = m.subject || t('mail.list.noSubject');
          const active = pane?.kind === 'message' && pane.uid === m.uid;
          return <li key={m.uid} className={`mailRow ${m.seen ? '' : 'unread'} ${active ? 'selected' : ''} ${selected.has(m.uid) ? 'picked' : ''}`}>
            {canAct && <label className="mailcRowCheck"><input type="checkbox" checked={selected.has(m.uid)} aria-label={t('mailc.select', { sender: personLabel(person), subject })}
              onChange={() => undefined} onClick={(e) => { setSelected((s) => toggleSelection(items, s, m.uid, anchor, e.shiftKey)); setAnchor(m.uid); }} /></label>}
            {canAct && <StarButton flagged={m.flagged} subject={subject} disabled={busy !== null} onToggle={() => void act(m.flagged ? 'unstar' : 'star', [m.uid])} />}
            <button type="button" className="mailRowOpen" data-uid={m.uid} aria-current={active ? 'true' : undefined} onFocus={() => setCursor(m.uid)} onClick={() => open(m.uid)}>
              <span className="mailRowTop"><strong dir="auto">{personLabel(person) || t('mail.draft.noRecipient')}</strong><time dateTime={m.date}>{when(m.date)}</time></span>
              <span className="mailRowSubject" dir="auto">{!m.seen && <span className="mailUnreadDot" role="img" aria-label={t('mail.list.unread')} />}{subject}{m.maybe_attachments && <Paperclip className="mailClip" role="img" aria-label={t('mail.list.attachment')} />}</span>
              <RowLabels label={mailLabels.labels.get(m.uid)} />
            </button>
          </li>;
        })}</ul>}
        {listState === 'ready' && nextBefore !== null && <div className="actions mailPad"><button type="button" className="secondary" disabled={busy === 'more'} onClick={() => { if (!folderId) return; setBusy('more'); void loadList(folderId, filters, nextBefore).finally(() => setBusy(null)); }}>{t('mail.list.more')}</button></div>}
      </section>

      <section data-pane="detail" className="pane mailDetail" aria-label={t('mail.reader.empty')}>
        {pane?.kind === 'message' && folder && <Reader key={`${folder.id}:${pane.uid}`} account={account} folder={folder} uid={pane.uid} canAct={canAct} busy={busy !== null}
          message={items.find((m) => m.uid === pane.uid) ?? null} moves={moves} onBack={() => setPane(null)} onAction={(action) => void act(action, [pane.uid])}
          onCompose={(kind) => void startDraft(kind, pane.uid)} onOpenApprovals={onOpenApprovals}
          onDrafted={(draft) => { setDrafts((list) => [draft, ...(list ?? []).filter((d) => d.id !== draft.id)]); setPane({ kind: 'draft', id: draft.id }); }} />}
        {pane?.kind === 'draft' && <Composer key={pane.id} draftId={pane.id} account={account} setup={setup} owner={owner} onOpenApprovals={onOpenApprovals}
          onBack={() => setPane(null)} onChanged={(d) => setDrafts((list) => d ? (list ?? []).map((x) => x.id === d.id ? d : x) : (list ?? []).filter((x) => x.id !== pane.id))}
          onClosed={() => { setPane(null); loadDrafts(); }} onSent={() => { loadDrafts(); sync.checkNow(); }} />}
        {!pane && <div className="paneEmpty"><MailOpen aria-hidden="true" /><h3>{t('mail.reader.empty')}</h3><p className="muted">{t('mailc.shortcuts.text')}</p></div>}
      </section>
    </SplitView>
  </>;
}

function Reader({ account, folder, uid, message, canAct, busy, moves, onBack, onAction, onCompose, onDrafted, onOpenApprovals }: {
  account: MailAccount; folder: MailFolder; uid: number; message: MailMessage | null; canAct: boolean; busy: boolean; moves: MailAction[];
  onBack: () => void; onAction: (action: MailAction) => void; onCompose: (kind: MailDraftKind) => void; onDrafted: (draft: MailDraft) => void; onOpenApprovals?: () => void;
}) {
  const [detail, setDetail] = useState<MailDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [plain, setPlain] = useState(false);
  const [fileNote, setFileNote] = useState<Note>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const load = useCallback(() => {
    setError(null); setDetail(null);
    readMail(account.id, folder.id, uid).then(setDetail, (e) => setError(mailError(e)));
  }, [account.id, folder.id, uid]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (detail) heading.current?.focus(); }, [detail]);
  const doc = useMemo(() => detail?.html ? readerDocument(detail.html) : null, [detail]);

  if (error) return <div className="mailPad"><Notice note={{ text: t('mail.reader.failed', { reason: error }), error: true }} /><div className="actions"><button type="button" onClick={load}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button><button type="button" className="secondary" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('mail.reader.back')}</button></div></div>;
  if (!detail) return <p className="muted mailPad" role="status" aria-busy="true">{t('mail.reader.loading')}</p>;
  const m = detail.message;
  const flagged = message?.flagged ?? m.flagged;
  const seen = message?.seen ?? true;
  const people = (list: { name: string; address: string }[]) => list.map((p) => p.name ? `${p.name} <${p.address}>` : p.address).join(', ');
  return <article className="mailReader" aria-labelledby="mail-reader-subject">
    <div className="mailReaderBar">
      <button type="button" className="secondary mailBack" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('mail.reader.back')}</button>
      {canAct && <div className="mailReaderActions" role="toolbar" aria-label={t('mailc.bulk.more')}>
        <button type="button" className="secondary" disabled={busy} onClick={() => onCompose('reply')}><Reply aria-hidden="true" />{t('mail.reader.reply')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => onCompose('reply_all')}><ReplyAll aria-hidden="true" />{t('mail.reader.replyAll')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => onCompose('forward')}><Forward aria-hidden="true" />{t('mail.reader.forward')}</button>
        <button type="button" className="secondary" aria-pressed={flagged} disabled={busy} onClick={() => onAction(flagged ? 'unstar' : 'star')}>{actionLabel(flagged ? 'unstar' : 'star')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => onAction(seen ? 'unread' : 'read')}>{actionLabel(seen ? 'unread' : 'read')}</button>
        {moves.map((action) => <button key={action} type="button" className={`secondary ${action === 'deleteForever' ? 'danger' : ''}`} disabled={busy} onClick={() => onAction(action)}>{action === 'deleteForever' && <Trash2 aria-hidden="true" />}{actionLabel(action)}</button>)}
      </div>}
    </div>
    <header className="mailReaderHead">
      <h2 id="mail-reader-subject" ref={heading} tabIndex={-1} dir="auto">{m.subject || t('mail.list.noSubject')}</h2>
      <dl className="mailMeta">
        <div><dt>{t('mail.reader.from')}</dt><dd dir="auto">{m.sender ? people([m.sender]) : ''}</dd></div>
        <div><dt>{t('mail.reader.to')}</dt><dd dir="auto">{people(m.to) || account.address}</dd></div>
        {m.cc.length > 0 && <div><dt>{t('mail.reader.cc')}</dt><dd dir="auto">{people(m.cc)}</dd></div>}
        <div><dt>{t('mail.reader.date')}</dt><dd><time dateTime={m.date}>{when(m.date)}</time></dd></div>
      </dl>
    </header>
    <MailAssist target={{ account_id: account.id, folder: folder.id, uid }} account={account} canAct={canAct} onDrafted={onDrafted} onOpenApprovals={onOpenApprovals} />
    {detail.remote_images_blocked > 0 && <p className="notice mailBlocked" role="status"><ShieldCheck aria-hidden="true" />{t('mail.reader.imagesBlocked', { count: detail.remote_images_blocked })}</p>}
    {detail.inline_images_hidden > 0 && <p className="muted mailSmall">{t('mail.reader.inlineHidden', { count: detail.inline_images_hidden })}</p>}
    {doc && <div className="actions mailBodyToggle"><button type="button" className="linkButton" aria-pressed={plain} onClick={() => setPlain(!plain)}>{plain ? t('mailc.showFormatted') : t('mailc.showPlain')}</button></div>}
    {doc && !plain
      // No scripts and no same origin: the frame cannot run code or reach this page; its own CSP loads nothing.
      ? <iframe className="mailFrame" title={t('mail.reader.frameTitle')} sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={doc} />
      : <div className="mailText" dir="auto">{detail.text}</div>}
    {detail.text_truncated && <p className="muted mailSmall">{t('mail.reader.truncated')}</p>}
    {detail.attachments.length > 0 && <section className="mailFiles" aria-label={t('mail.reader.attachments')}><h3>{t('mail.reader.attachments')}</h3><ul>{detail.attachments.map((a) => <li key={a.index}>
      {a.downloadable
        ? <button type="button" className="secondary" onClick={() => { setFileNote(null); downloadMailAttachment(account.id, folder.id, uid, a.index, a.filename).catch((e) => setFileNote({ text: mailError(e), error: true })); }}><Download aria-hidden="true" /><span dir="auto">{a.filename}</span> <small>{formatBytes(a.size)}</small></button>
        : <p className="muted">{t('mail.reader.tooLarge', { name: a.filename })}</p>}
    </li>)}</ul><Notice note={fileNote} /></section>}
  </article>;
}

function Composer({ draftId, account, setup, owner, onBack, onChanged, onClosed, onSent, onOpenApprovals }: {
  draftId: string; account: MailAccount; setup: MailSetup; owner: boolean; onBack: () => void; onChanged: (draft: MailDraft | null) => void;
  onClosed: () => void; onSent: () => void; onOpenApprovals?: () => void;
}) {
  const [draft, setDraft] = useState<MailDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState({ to: '', cc: '', bcc: '', subject: '', body: '' });
  const [identityId, setIdentityId] = useState<string | null>(null);
  const [showBcc, setShowBcc] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);
  const sendKey = useRef<string | null>(null);
  const ids = { to: useId(), cc: useId(), bcc: useId(), subject: useId(), body: useId(), hint: useId(), title: useId() };

  // The parent's callback changes on every render; reading it through a ref keeps `load` stable, so a
  // parent re-render never reloads the draft over what the person is typing.
  const changed = useRef(onChanged);
  changed.current = onChanged;
  const adopt = useCallback((d: MailDraft) => {
    setDraft(d); changed.current(d);
    setFields({ to: joinRecipients(d.to), cc: joinRecipients(d.cc), bcc: joinRecipients(d.bcc), subject: d.subject, body: d.body });
    setIdentityId(d.identity_id ?? null);
    if (d.bcc.length) setShowBcc(true);
  }, []);
  const load = useCallback(() => { setError(null); getMailDraft(draftId).then(adopt, (e) => setError(mailError(e))); }, [draftId, adopt]);
  // Files and the schedule change the draft's status and files only: what the person typed stays.
  const refreshMeta = useCallback(async () => { const d = await getMailDraft(draftId); setDraft(d); changed.current(d); }, [draftId]);
  const scheduleKey = useRef<{ at: string; key: string } | null>(null);
  useEffect(() => { load(); }, [load]);

  if (error) return <div className="mailPad"><Notice note={{ text: t('mail.composer.startFailed', { reason: error }), error: true }} /><div className="actions"><button type="button" onClick={load}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button><button type="button" className="secondary" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('mail.reader.back')}</button></div></div>;
  if (!draft) return <p className="muted mailPad" role="status" aria-busy="true">{t('mail.composer.starting')}</p>;

  const editable = isEditable(draft);
  const dirty = fields.to !== joinRecipients(draft.to) || fields.cc !== joinRecipients(draft.cc) || fields.bcc !== joinRecipients(draft.bcc) || fields.subject !== draft.subject || fields.body !== draft.body;
  const approval = draft.approval && draft.status === 'pending_approval' ? toApproval(draft.approval) : null;
  const approvalAt = approval ? approvalSendAt(approval.arguments) : null;
  const fileCount = draft.attachments?.length ?? 0;
  const title = draft.kind === 'new' ? t('mail.composer.newTitle') : draft.kind === 'forward' ? t('mail.composer.forwardTitle') : t('mail.composer.replyTitle');

  async function run(kind: string, fn: () => Promise<void>) {
    setBusy(kind); setNote(null);
    try { await fn(); } catch (e) { setNote({ text: mailError(e), error: true }); } finally { setBusy(null); }
  }
  async function save(identity: string | null | undefined = undefined): Promise<MailDraft | null> {
    const parsed = { to: parseRecipients(fields.to), cc: parseRecipients(fields.cc), bcc: parseRecipients(fields.bcc) };
    for (const value of Object.values(parsed)) if (!value.ok) { setNote({ text: t('mail.composer.badAddress', { value: value.bad }), error: true }); return null; }
    const ok = parsed as { [K in keyof typeof parsed]: { ok: true; value: string[] } };
    const saved = await updateMailDraft(draft!.id, { to: ok.to.value, cc: ok.cc.value, bcc: ok.bcc.value, subject: fields.subject, body: fields.body, ...(identity !== undefined ? { identity_id: identity } : {}) });
    sendKey.current = null;
    adopt(saved);
    return saved;
  }
  async function send() {
    const current = dirty ? await save() : draft;
    if (!current) return;
    // One key per attempt: a retry after a network error reuses it, so the API answers with the same approval.
    sendKey.current ??= newSendKey();
    const result = await sendMailDraft(current.id, sendKey.current);
    adopt(result.draft);
  }
  async function schedule(iso: string): Promise<boolean> {
    const current = dirty ? await save() : draft;
    if (!current) return false;
    // One key per picked time: a retry of the same time reuses it; another time asks again.
    if (scheduleKey.current?.at !== iso) scheduleKey.current = { at: iso, key: newSendKey() };
    const result = await sendMailDraft(current.id, scheduleKey.current.key, iso);
    adopt(result.draft);
    return true;
  }
  async function approve(a: Approval) {
    const result = await api.approveTask(a);
    const fresh = await getMailDraft(draft!.id);
    adopt(fresh);
    if (fresh.status === 'sent') { setNote({ text: t('mail.approval.sent', { to: fresh.to.join(', ') }) }); onSent(); }
    // A scheduled message says so in its schedule box (a status region), so no second note.
    else if (fresh.status === 'scheduled') onSent();
    else if (fresh.status === 'failed' || fresh.status === 'unknown') setNote({ text: t('mail.approval.failed', { reason: fresh.error ?? result.run?.result ?? '' }), error: true });
  }

  const set = (key: keyof typeof fields) => (e: { target: { value: string } }) => setFields((f) => ({ ...f, [key]: e.target.value }));
  const setValue = (key: 'to' | 'cc' | 'bcc') => (value: string) => setFields((f) => ({ ...f, [key]: value }));
  const fromText = draft.from_address ? senderLabel(draft.from_name, draft.from_address) : senderLabel(account.display_name, account.address);
  return <article className="mailComposer" aria-labelledby={ids.title}>
    <div className="mailReaderBar">
      <button type="button" className="secondary mailBack" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('mail.reader.back')}</button>
      <span className={`statusBadge ${draftTone(draft.status)}`}>{t(`mail.draft.status.${draft.status}` as MessageKey)}</span>
    </div>
    <h2 id={ids.title}>{title}</h2>
    {draft.error && <Notice note={{ text: t('mail.approval.failed', { reason: draft.error }), error: true }} />}
    {draft.status === 'unknown' && <Notice note={{ text: t('mail.draft.unknownHelp'), error: true }} />}
    {draft.status === 'sent' && draft.saved_to_sent === false && <Notice note={{ text: t('mail.draft.sentCopyMissing') }} />}
    {draft.approval_note && <Notice note={{ text: draft.approval_note }} />}
    <form className="mailForm" onSubmit={(e) => { e.preventDefault(); void run('send', send); }}>
      <FromPicker account={account} value={identityId} disabled={!editable || busy !== null}
        onChange={(next) => void run('identity', async () => { const saved = await save(next); if (saved) setNote({ text: t('mails.from.changed', { address: saved.from_address ?? account.address }) }); })}
        fallback={<p className="mailFrom"><span className="muted">{t('mail.composer.from')}</span> <span dir="auto">{fromText}</span></p>} />
      <RecipientField label={t('mail.composer.to')} value={fields.to} disabled={!editable} describedBy={ids.hint} onChange={setValue('to')} />
      <RecipientField label={t('mail.composer.cc')} value={fields.cc} disabled={!editable} describedBy={ids.hint} onChange={setValue('cc')} />
      {showBcc ? <RecipientField label={t('mail.composer.bcc')} value={fields.bcc} disabled={!editable} describedBy={ids.hint} onChange={setValue('bcc')} />
        : editable && <button type="button" className="linkButton mailBccToggle" onClick={() => setShowBcc(true)}>{t('mail.composer.showBcc')}</button>}
      <small id={ids.hint} className="muted">{t('mail.composer.recipientsHint', { max: setup.max_recipients })}</small>
      <label className="field" htmlFor={ids.subject}><span>{t('mail.composer.subject')}</span><input id={ids.subject} dir="auto" maxLength={500} value={fields.subject} disabled={!editable} onChange={set('subject')} /></label>
      <label className="field" htmlFor={ids.body}><span>{t('mail.composer.body')}</span><textarea id={ids.body} dir="auto" rows={12} maxLength={100_000} value={fields.body} disabled={!editable} onChange={set('body')} /></label>
      <ComposerFiles draft={draft} setup={setup} editable={editable} onRefresh={refreshMeta} />
      <Notice note={note} />
      {approval && <div className="mailApproval" role="group" aria-label={t('mail.approval.title')}>
        <p><Send aria-hidden="true" /> <strong>{t('mail.approval.title')}</strong></p>
        <p className="muted">{approvalAt ? t('mailo.approval.scheduleText', { when: when(approvalAt), from: draft.from_address || account.address, to: draft.to.join(', ') }) : t('mail.approval.text', { from: draft.from_address || account.address, to: draft.to.join(', ') })}{fileCount > 0 ? ` ${t('mailo.approval.files', { count: fileCount })}` : ''}</p>
        <p className="mailHash"><span className="muted">{t('mail.approval.hash')}</span> <code title={approval.payloadHash ?? ''}>{shortHash(approval.payloadHash)}</code></p>
        {dirty && <p className="muted">{t('email.approval.dirty')}</p>}
        <div className="actions">
          {owner ? <button type="button" disabled={busy !== null || dirty} onClick={() => void run('approve', () => approve(approval))}><Send aria-hidden="true" />{busy === 'approve' ? t('mail.approval.sending') : approvalAt ? t('mailo.approval.approveSchedule') : t('mail.approval.approve')}</button>
            : <p className="muted">{t('mail.approval.waitOwner')}</p>}
          {onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('mail.approval.openApprovals')}</button>}
        </div>
      </div>}
      {editable && <div className="actions mailFormActions">
        {(!approval || dirty) && draft.status !== 'scheduled' && <button type="submit" disabled={busy !== null || !fields.to.trim()}><Send aria-hidden="true" />{busy === 'send' ? t('mail.composer.preparing') : t('mail.composer.send')}</button>}
        <SchedulePicker draft={draft} setup={setup} disabled={busy !== null || !fields.to.trim()} busy={busy === 'schedule'}
          onSchedule={async (iso) => { let done = false; await run('schedule', async () => { done = await schedule(iso); }); return done; }}
          onCancel={() => run('cancelSchedule', async () => { const d = await cancelMailSchedule(draft.id); setDraft(d); changed.current(d); setNote({ text: t('mailo.schedule.cancelled') }); })} />
        <button type="button" className="secondary" disabled={busy !== null || !dirty} onClick={() => void run('save', async () => { if (await save()) setNote({ text: t('mail.composer.saved') }); })}>{t('mail.composer.save')}</button>
        <button type="button" className="secondary danger" disabled={busy !== null} onClick={() => { if (!window.confirm(t('mail.composer.discardConfirm'))) return; void run('discard', async () => { await deleteMailDraft(draft.id); onChanged(null); onClosed(); }); }}><Trash2 aria-hidden="true" />{t('mail.composer.discard')}</button>
      </div>}
      {!editable && draft.status !== 'sending' && <div className="actions"><button type="button" className="secondary" disabled={busy !== null} onClick={() => void run('remove', async () => { await deleteMailDraft(draft.id); onChanged(null); onClosed(); })}><Trash2 aria-hidden="true" />{t('mail.draft.remove')}</button></div>}
    </form>
  </article>;
}

function MailboxSettings({ setup, account, onChanged, onDisconnected }: { setup: MailSetup; account: MailAccount; onChanged: (a: MailAccount) => void; onDisconnected: () => void }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);
  const id = useId();
  async function run(kind: string, fn: () => Promise<void>) {
    setBusy(kind); setNote(null);
    try { await fn(); } catch (e) { setNote({ text: mailError(e), error: true }); } finally { setBusy(null); }
  }
  const status = t(`mail.status.${account.status}` as MessageKey);
  return <div className="mailSettings">
    <dl className="mailMeta">
      <div><dt>{t('mail.settings.address')}</dt><dd>{account.address}</dd></div>
      <div><dt>{t('mail.settings.server')}</dt><dd>{t('mail.settings.serverValue', { imap: setup.imap_host ?? '', imapPort: setup.imap_port, smtp: setup.smtp_host ?? '', smtpPort: setup.smtp_port, security: setup.smtp_security })}</dd></div>
      <div><dt>{t('mail.settings.status')}</dt><dd><span className={`statusBadge ${account.status === 'connected' ? 'success' : 'warning'}`}>{status}</span>{account.last_checked_at ? ` ${t('mail.status.checked', { when: when(account.last_checked_at) })}` : ''}{account.last_error ? ` ${account.last_error}` : ''}</dd></div>
    </dl>
    <p className="muted">{t('mail.settings.limits', { perHour: setup.send_per_hour, recipients: setup.max_recipients })}</p>
    <Notice note={note} />
    <div className="actions"><button type="button" className="secondary" disabled={busy !== null} onClick={() => void run('check', async () => { const next = await checkMailbox(account.id); onChanged(next); setNote({ text: t('mail.settings.checked', { status: t(`mail.status.${next.status}` as MessageKey) }), error: next.status !== 'connected' }); })}><RefreshCw aria-hidden="true" />{t('mail.settings.check')}</button></div>
    {setup.can_act && <form className="mailForm" onSubmit={(e) => { e.preventDefault(); void run('password', async () => { const next = await updateMailboxPassword(account.id, password); setPassword(''); onChanged(next); setNote({ text: t('mail.settings.passwordSaved') }); }); }}>
      <label className="field" htmlFor={id}><span>{t('mail.settings.password')}</span><input id={id} type="password" autoComplete="new-password" maxLength={500} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      <small className="muted">{t('mail.settings.passwordHint')}</small>
      <div className="actions"><button type="submit" className="secondary" disabled={busy !== null || !password}>{t('mail.settings.passwordSave')}</button></div>
    </form>}
    {setup.can_act && <div className="actions"><button type="button" className="secondary danger" disabled={busy !== null} onClick={() => { if (!window.confirm(t('mail.settings.disconnectConfirm', { address: account.address }))) return; void run('disconnect', async () => { await disconnectMailbox(account.id); onDisconnected(); }); }}><Unplug aria-hidden="true" />{t('mail.settings.disconnect')}</button></div>}
  </div>;
}
