// All inboxes (docs/email.md#all-inboxes): one list, newest first, over the inbox of every mailbox the
// person may read (their own and those shared with them; the API decides). Each row carries its
// mailbox badge; filters by mailbox, unread, starred and received date, server-side search and paging.
// A message opens in its own mailbox: the reader and composer are the Email page's own, given that
// mailbox, so a reply is a draft of that mailbox, sent from its address after an owner approves it.
import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { CircleDot, Inbox, Lock, Mail, MailOpen, Paperclip, PenLine, RefreshCw, Share2 } from 'lucide-react';
import { Composer, Notice, Panel, Reader, mailError } from './EmailView';
import { isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import { listPerson, personLabel, type MailDraft, type MailDraftKind, type MailSetup } from './lib/mail';
import {
  ALL_PAGE, NO_ALL_FILTERS, accountOf, allFiltersActive, allInboxesQuery, appendPage, badDateRange, inboxFolder, mailboxBadge, messageKey, setFlag, unreadable,
  type AllInboxFilters, type AllInboxMailbox, type AllInboxMessage,
} from './lib/mailAllInboxes';
import { createMailDraft, listAllInboxes } from './lib/mailApi';
import { setMailFlag } from './lib/mailActionsApi';
import type { MailAction, MailFilters } from './lib/mailActions';
import { canSendFrom, senderLabel } from './lib/mailDirectory';
import { MailSearchBar, StarButton, actionDone } from './MailActions';
import { SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';

type Note = { text: string; error?: boolean } | null;
type Pane = { kind: 'message'; key: string } | { kind: 'draft'; id: string; accountId: string } | null;

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function AllInboxes({ setup, owner, header, onOpenMailbox, onOpenApprovals }: {
  setup: MailSetup; owner: boolean; header: (actions?: ReactNode) => ReactNode; onOpenMailbox: (accountId: string) => void; onOpenApprovals?: () => void;
}) {
  const wide = useMediaQuery(SPLIT_QUERY);
  const [filters, setFilters] = useState<AllInboxFilters>(NO_ALL_FILTERS);
  const [items, setItems] = useState<AllInboxMessage[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [problems, setProblems] = useState<AllInboxMailbox[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [error, setError] = useState('');
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pane, setPane] = useState<Pane>(null);
  const listTitleId = useId();
  const ids = { mailbox: useId(), since: useId(), until: useId() };

  const load = useCallback(async (next: AllInboxFilters, after: string | null = null) => {
    if (badDateRange(next)) return;
    if (!after) setState('loading');
    try {
      const page = await listAllInboxes(allInboxesQuery(next, after, ALL_PAGE));
      setItems((current) => after ? appendPage(current, page.items) : page.items);
      if (!after) setTotal(page.total);
      setCursor(page.next_cursor); setProblems(unreadable(page)); setState('ready'); setError('');
    } catch (e) {
      if (after) { setNote({ text: t('maila.moreFailed', { reason: mailError(e) }), error: true }); return; }
      if (isPermissionDenied(e)) { setState('denied'); return; }
      setError(mailError(e)); setState('error');
    }
  }, []);
  useEffect(() => { void load(filters); }, [filters, load]);

  function apply(next: AllInboxFilters) { setFilters(next); setPane(null); setNote(null); }
  const searchFilters: MailFilters = { q: filters.q, unread: filters.unread, flagged: filters.flagged };
  const open = pane?.kind === 'message' ? items.find((m) => messageKey(m) === pane.key) ?? null : null;
  const openAccount = open ? accountOf(setup.accounts, open) : null;
  const draftAccount = pane?.kind === 'draft' ? setup.accounts.find((a) => a.id === pane.accountId) ?? null : null;
  const writableAccount = useMemo(() => setup.can_act ? setup.accounts.find((a) => canSendFrom(a)) ?? null : null, [setup]);
  const rangeBad = badDateRange(filters);

  function openMessage(m: AllInboxMessage) {
    setPane({ kind: 'message', key: messageKey(m) }); setNote(null);
    const account = accountOf(setup.accounts, m);
    // The API marks it read when the person may change the mailbox (read shares change nothing).
    if (!m.seen && account && setup.can_act && canSendFrom(account)) setItems((list) => setFlag(list, messageKey(m), 'seen', true));
  }

  async function flag(m: AllInboxMessage, action: MailAction) {
    const which = action === 'star' || action === 'unstar' ? 'flagged' : action === 'read' || action === 'unread' ? 'seen' : null;
    if (!which) return;
    const value = action === 'star' || action === 'read';
    setBusy('flag'); setNote(null);
    try {
      await setMailFlag(m.account_id, m.folder, [m.uid], which, value);
      setItems((list) => setFlag(list, messageKey(m), which, value));
      setNote({ text: actionDone(action as Exclude<MailAction, 'deleteForever'>, 1) });
    } catch (e) { setNote({ text: t('mailc.failed', { reason: mailError(e) }), error: true }); }
    finally { setBusy(null); }
  }

  async function compose(accountId: string, kind: MailDraftKind, m?: AllInboxMessage) {
    setBusy('compose'); setNote(null);
    try {
      const draft = await createMailDraft({ account_id: accountId, kind, ...(m && kind !== 'new' ? { folder: m.folder, uid: m.uid } : {}) });
      setPane({ kind: 'draft', id: draft.id, accountId });
    } catch (e) { setNote({ text: t('mail.composer.startFailed', { reason: mailError(e) }), error: true }); }
    finally { setBusy(null); }
  }

  if (state === 'denied') return <>{header()}<Panel icon={<Lock />} title={t('maila.denied.title')} text={t('maila.denied.text')} /></>;

  const filtered = allFiltersActive(filters);
  return <>
    {header(writableAccount && <button type="button" disabled={busy !== null} onClick={() => void compose(writableAccount.id, 'new')}><PenLine aria-hidden="true" />{t('mail.compose')}</button>)}
    <Notice note={note} />
    <SplitView className="mailSplit mailAll" layout="two" show={pane ? 'detail' : 'list'} label={t('maila.title')}>
      <section data-pane="list" className="pane mailList" aria-labelledby={listTitleId} aria-busy={state === 'loading'}>
        <div className="paneHead"><h2 id={listTitleId}><Inbox aria-hidden="true" className="mailAllIcon" />{t('maila.title')}</h2>
          {state === 'ready' && <span className="muted" aria-live="polite">{filtered ? t('maila.results', { count: total }) : t('maila.shown', { shown: formatNumber(items.length), total: formatNumber(total) })}</span>}
        </div>
        <div className="mailAllFilters">
          <MailSearchBar filters={searchFilters} busy={state === 'loading'} onApply={(next) => apply({ ...filters, ...next })} />
          <fieldset className="mailAllFields">
            <legend className="visuallyHidden">{t('maila.filters')}</legend>
            <label className="field" htmlFor={ids.mailbox}><span>{t('maila.mailbox')}</span>
              <select id={ids.mailbox} value={filters.accountId} onChange={(e) => apply({ ...filters, accountId: e.target.value })}>
                <option value="">{t('maila.mailboxAll')}</option>
                {setup.accounts.map((a) => <option key={a.id} value={a.id}>{a.access && a.access !== 'owner' ? t('mails.mailbox.sharedOption', { address: a.address }) : a.address}</option>)}
              </select>
            </label>
            <label className="field" htmlFor={ids.since}><span>{t('maila.since')}</span>
              <input id={ids.since} type="date" value={filters.since} max={filters.until || undefined} aria-invalid={rangeBad || undefined} onChange={(e) => apply({ ...filters, since: e.target.value })} />
            </label>
            <label className="field" htmlFor={ids.until}><span>{t('maila.until')}</span>
              <input id={ids.until} type="date" value={filters.until} min={filters.since || undefined} aria-invalid={rangeBad || undefined} onChange={(e) => apply({ ...filters, until: e.target.value })} />
            </label>
          </fieldset>
          {rangeBad && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('maila.badRange')}</p>}
          {filtered && <div className="actions"><button type="button" className="linkButton" onClick={() => apply(NO_ALL_FILTERS)}>{t('maila.clear')}</button></div>}
        </div>
        {problems.length > 0 && <div className="mailPad mailAllProblems">{problems.map((p) => <Notice key={p.account_id} note={{ text: t('maila.unreadable', { address: p.address, reason: p.error ?? '' }), error: true }} />)}
          <div className="actions"><button type="button" className="secondary" onClick={() => void load(filters)}><RefreshCw aria-hidden="true" />{t('maila.retry')}</button></div>
        </div>}
        {state === 'loading' && <p className="muted mailPad" role="status">{t('maila.loading')}</p>}
        {state === 'error' && <div className="mailPad"><Notice note={{ text: t('maila.failed', { reason: error }), error: true }} /><div className="actions"><button type="button" onClick={() => void load(filters)}><RefreshCw aria-hidden="true" />{t('maila.retry')}</button></div></div>}
        {state === 'ready' && items.length === 0 && <div className="paneEmpty"><Mail aria-hidden="true" />
          {filtered ? <><h3>{t('maila.noMatch.title')}</h3><p>{t('maila.noMatch.text')}</p><div className="actions"><button type="button" className="secondary" onClick={() => apply(NO_ALL_FILTERS)}>{t('maila.clear')}</button></div></>
            : <><h3>{t('maila.empty.title')}</h3><p>{t('maila.empty.text')}</p><div className="actions">
              <button type="button" className="secondary" onClick={() => void load(filters)}><RefreshCw aria-hidden="true" />{t('maila.empty.check')}</button>
              {writableAccount && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void compose(writableAccount.id, 'new')}><PenLine aria-hidden="true" />{t('mail.compose')}</button>}
            </div></>}
        </div>}
        {state === 'ready' && items.length > 0 && <ul className="mailRows">{items.map((m) => {
          const key = messageKey(m);
          const subject = m.subject || t('mail.list.noSubject');
          const active = pane?.kind === 'message' && pane.key === key;
          const account = accountOf(setup.accounts, m);
          const canFlag = Boolean(setup.can_act && account && canSendFrom(account));
          const mailbox = mailboxBadge(m);
          const shared = m.access !== 'owner';
          return <li key={key} className={`mailRow mailAllRow ${m.seen ? '' : 'unread'} ${active ? 'selected' : ''}`}>
            {canFlag && <StarButton flagged={m.flagged} subject={subject} disabled={busy !== null} onToggle={() => void flag(m, m.flagged ? 'unstar' : 'star')} />}
            <button type="button" className="mailRowOpen" aria-current={active ? 'true' : undefined} onClick={() => openMessage(m)}>
              <span className="mailRowTop"><strong dir="auto">{personLabel(listPerson(m, 'inbox')) || t('mail.draft.noRecipient')}</strong><time dateTime={m.date}>{when(m.date)}</time></span>
              <span className="mailRowSubject" dir="auto">{!m.seen && <span className="mailUnreadDot" role="img" aria-label={t('mail.list.unread')} />}{subject}{m.maybe_attachments && <Paperclip className="mailClip" role="img" aria-label={t('mail.list.attachment')} />}</span>
              <span className="mailAllBadge" title={m.account_address}>
                {shared && <Share2 aria-hidden="true" />}<span dir="auto" aria-hidden="true">{mailbox}</span>
                <span className="visuallyHidden">{shared ? t('maila.badgeShared', { mailbox }) : t('maila.badge', { mailbox })}</span>
              </span>
            </button>
          </li>;
        })}</ul>}
        {state === 'ready' && cursor !== null && <div className="actions mailPad"><button type="button" className="secondary" disabled={busy === 'more'} onClick={() => { setBusy('more'); void load(filters, cursor).finally(() => setBusy(null)); }}>{t('maila.more')}</button></div>}
      </section>

      <section data-pane="detail" className="pane mailDetail" aria-label={t('maila.reader.empty')}>
        {pane?.kind === 'message' && open && openAccount && <>
          <div className="mailAllContext">
            <span className="mailAllBadge"><Inbox aria-hidden="true" /><span dir="auto">{senderLabel(openAccount.display_name, openAccount.address)}</span></span>
            <button type="button" className="linkButton" onClick={() => onOpenMailbox(openAccount.id)}>{t('maila.openMailbox', { mailbox: openAccount.address })}</button>
          </div>
          {!canSendFrom(openAccount) && <p className="notice mailsShared" role="note"><Share2 aria-hidden="true" />{t('maila.readOnly')}</p>}
          <Reader key={pane.key} account={openAccount} folder={inboxFolder()} uid={open.uid} message={open} canAct={setup.can_act && canSendFrom(openAccount)} busy={busy !== null}
            moves={[]} onBack={() => setPane(null)} onAction={(action) => void flag(open, action)}
            onCompose={(kind) => void compose(openAccount.id, kind, open)} onOpenApprovals={onOpenApprovals}
            onDrafted={(draft: MailDraft) => setPane({ kind: 'draft', id: draft.id, accountId: draft.account_id })} />
        </>}
        {pane?.kind === 'message' && (!open || !openAccount) && <div className="mailPad"><Notice note={{ text: t('maila.gone'), error: true }} /><div className="actions"><button type="button" onClick={() => { setPane(null); void load(filters); }}><RefreshCw aria-hidden="true" />{t('maila.retry')}</button></div></div>}
        {pane?.kind === 'draft' && draftAccount && <Composer key={pane.id} draftId={pane.id} account={draftAccount} setup={setup} owner={owner} onOpenApprovals={onOpenApprovals}
          onBack={() => setPane(null)} onChanged={() => undefined} onClosed={() => setPane(null)} onSent={() => undefined} />}
        {!pane && wide && <div className="paneEmpty"><MailOpen aria-hidden="true" /><h3>{t('maila.reader.empty')}</h3><p className="muted">{t('maila.reader.text')}</p></div>}
      </section>
    </SplitView>
  </>;
}
