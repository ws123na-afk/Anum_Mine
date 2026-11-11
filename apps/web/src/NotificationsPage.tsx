// The Notifications page (docs/team-work.md#notifications-page): every in-app notification of the
// caller, filtered by kind and read state, paged with Previous and Next over the API's cursor, with
// mark read or unread, mark all read and delete. Opened from the bell's "See all notifications".
// Only the recipient can read or change a notification; the API (RLS on user) decides that.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Bell, BellRing, CheckCheck, ExternalLink, Mail, MailOpen, Trash2 } from 'lucide-react';
import type { NotificationKindCount, UserNotification } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { viewLabel } from './lib/shell';
import { NOTIFICATIONS_PAGE, currentCursor, firstPage, kindLabel, nextPage, notificationHash, pageRows, previousPage, type CursorTrail } from './lib/notificationCenter';
import { Empty, LoadingSkeleton, Notice, PageHeader, Pager, PaneProblem } from './PageParts';
import { ConfirmDialog } from './Dialog';
import './library.css';
import './notificationCenter.css';

type ReadFilter = 'all' | 'unread' | 'read';
type Load = { kind: 'loading' } | { kind: 'ready'; items: UserNotification[]; next: string | null; unread: number } | { kind: 'error'; detail: string };

export function NotificationsPage({ onOpenWork, onChanged, signal }: { onOpenWork: (itemId: string) => void; onChanged: () => void; signal?: unknown }) {
  const [state, setState] = useState<ReadFilter>('all');
  const [kind, setKind] = useState<string | null>(null);
  const [kinds, setKinds] = useState<NotificationKindCount[] | null>(null);
  const [trail, setTrail] = useState<CursorTrail>(firstPage);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [deleting, setDeleting] = useState<UserNotification | null>(null);

  const fetchKinds = useCallback(() => team.getNotificationKinds().then((page) => setKinds(page.items), () => setKinds([])), []);
  const fetchPage = useCallback(async (quiet = false) => {
    if (!quiet) setLoad({ kind: 'loading' });
    try {
      const page = await team.listNotifications({ cursor: currentCursor(trail), limit: NOTIFICATIONS_PAGE, kind, state: state === 'all' ? null : state });
      setLoad({ kind: 'ready', items: page.items, next: page.next_cursor, unread: page.unread });
    } catch (e) { setLoad({ kind: 'error', detail: describeError(e) }); }
  }, [trail, kind, state]);
  useEffect(() => { void fetchPage(); }, [fetchPage]);
  useEffect(() => { void fetchKinds(); }, [fetchKinds]);
  // A new notification for this person (the shell's live signal) refreshes the list in place.
  const seenSignal = useRef(signal);
  useEffect(() => {
    if (!signal || signal === seenSignal.current) return;
    seenSignal.current = signal;
    void fetchPage(true); void fetchKinds();
  }, [signal, fetchPage, fetchKinds]);
  // Deleting the last row of a later page steps back instead of showing an empty page.
  useEffect(() => { if (load.kind === 'ready' && load.items.length === 0 && trail.trail.length > 1) setTrail(previousPage(trail)); }, [load, trail]);

  const filter = (next: { state?: ReadFilter; kind?: string | null }) => {
    if (next.state !== undefined) setState(next.state);
    if (next.kind !== undefined) setKind(next.kind);
    setTrail(firstPage());
  };
  async function act(work: () => Promise<unknown>, done: string) {
    setBusy(true); setFlash(null);
    try { await work(); setFlash({ text: done }); await Promise.all([fetchPage(true), fetchKinds()]); onChanged(); }
    catch (e) { setFlash({ text: t('ntf.failed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); }
  }
  async function open(n: UserNotification) {
    if (!n.read_at) { try { await team.readNotification(n.id); onChanged(); } catch { /* still open it */ } }
    if (n.work_item_id) { onOpenWork(n.work_item_id); return; }
    const hash = notificationHash(n);
    if (hash) location.hash = hash;
    else void fetchPage(true);
  }

  const ready = load.kind === 'ready' ? load : null;
  const rows = ready ? pageRows(trail, ready.items.length) : { from: 0, to: 0 };
  const filtered = state !== 'all' || kind !== null;
  const title = viewLabel('notificationCenter');
  return <>
    <PageHeader title={title} subtitle={t('ntf.subtitle')} crumbs={[{ label: viewLabel('home'), href: '#home' }, { label: title }]}
      actions={<button type="button" className="secondary" disabled={busy || !ready || ready.unread === 0} onClick={() => void act(() => team.readAllNotifications(), t('ntf.allRead'))}>
        <CheckCheck aria-hidden="true" />{t('ntf.readAll')}</button>} />
    <div aria-live="polite">{flash && !flash.error && <Notice>{flash.text}</Notice>}</div>
    {flash?.error && <Notice error>{flash.text}</Notice>}
    <section className="pane ntfPane" aria-labelledby="ntf-list" aria-busy={load.kind === 'loading'}>
      <div className="paneHead ntfHead">
        <h2 id="ntf-list">{ready ? t('ntf.unreadCount', { count: ready.unread }) : t('ntf.listTitle')}</h2>
        <div className="ntfFilters">
          <div className="libraryChips" role="group" aria-label={t('ntf.filter.state')}>
            {(['all', 'unread', 'read'] as const).map((value) => <button type="button" key={value} className={`libChip ${state === value ? 'active' : ''}`} aria-pressed={state === value} onClick={() => filter({ state: value })}>{t(`ntf.state.${value}`)}</button>)}
          </div>
          {kinds && kinds.length > 0 && <div className="libraryChips" role="group" aria-label={t('ntf.filter.kind')}>
            <button type="button" className={`libChip ${kind === null ? 'active' : ''}`} aria-pressed={kind === null} onClick={() => filter({ kind: null })}>{t('ntf.kind.all')}</button>
            {kinds.map((row) => <button type="button" key={row.kind} className={`libChip ${kind === row.kind ? 'active' : ''}`} aria-pressed={kind === row.kind} onClick={() => filter({ kind: row.kind })}>
              {t('ntf.kindChip', { kind: kindLabel(row.kind), count: row.total })}</button>)}
          </div>}
        </div>
      </div>
      {load.kind === 'loading' && <div className="paneBody"><LoadingSkeleton label={t('ntf.loading')} rows={4} /></div>}
      {load.kind === 'error' && <div className="paneBody"><PaneProblem text={t('ntf.loadFailed', { message: load.detail })} onRetry={() => void fetchPage()} /></div>}
      {ready && ready.items.length === 0 && <Empty icon={<Bell aria-hidden="true" />}
        title={filtered ? t('ntf.emptyFiltered') : t('ntf.empty')} text={filtered ? t('ntf.emptyFilteredText') : t('ntf.emptyText')}
        action={filtered ? <button type="button" className="secondary" onClick={() => filter({ state: 'all', kind: null })}>{t('ntf.showAll')}</button>
          : <button type="button" className="secondary" onClick={() => { location.hash = '#notifications'; }}>{t('ntf.openSettings')}</button>} />}
      {ready && ready.items.length > 0 && <ul className="libraryList ntfList">{ready.items.map((n) => <li key={n.id} className={`libraryRow ntfRow ${n.read_at ? '' : 'unread'}`}>
        <span className="ntfMark" aria-hidden="true">{n.read_at ? <MailOpen /> : <BellRing />}</span>
        <div className="libraryMain">
          <button type="button" className="libraryTitle cellButton" dir="auto" onClick={() => void open(n)}>
            {n.title}<ExternalLink aria-hidden="true" /></button>
          {n.body && <p className="ntfBody" dir="auto">{n.body}</p>}
          <small><span className="statusBadge neutral">{kindLabel(n.kind)}</span><span>{formatDateTime(n.created_at)}</span>
            {!n.read_at && <span className="statusBadge warning">{t('ntf.unread')}</span>}
            {n.email_status === 'sent' && <span>{t('bell.emailed')}</span>}{n.email_status === 'failed' && <span>{t('bell.emailFailed')}</span>}</small>
        </div>
        <div className="cellActions">
          {n.read_at
            ? <button type="button" className="iconButton" disabled={busy} aria-label={t('ntf.markUnreadOf', { title: n.title })} title={t('ntf.markUnread')} onClick={() => void act(() => team.unreadNotification(n.id), t('ntf.markedUnread'))}><Mail aria-hidden="true" /></button>
            : <button type="button" className="iconButton" disabled={busy} aria-label={t('ntf.markReadOf', { title: n.title })} title={t('ntf.markRead')} onClick={() => void act(() => team.readNotification(n.id), t('ntf.markedRead'))}><MailOpen aria-hidden="true" /></button>}
          <button type="button" className="iconButton" disabled={busy} aria-label={t('ntf.deleteOf', { title: n.title })} title={t('ntf.delete')} onClick={() => setDeleting(n)}><Trash2 aria-hidden="true" /></button>
        </div>
      </li>)}</ul>}
      {ready && <Pager label={t('ntf.pages')} from={rows.from} to={rows.to} hasPrev={trail.trail.length > 1} hasNext={ready.next !== null} busy={busy}
        onPrev={() => setTrail(previousPage(trail))} onNext={() => { if (ready.next) setTrail(nextPage(trail, ready.next)); }} />}
    </section>
    {deleting && <ConfirmDialog title={t('ntf.deleteTitle')} confirmLabel={t('ntf.delete')} danger busy={busy} onCancel={() => setDeleting(null)}
      onConfirm={() => { const target = deleting; setDeleting(null); void act(() => team.deleteNotification(target.id), t('ntf.deleted')); }}>
      <p dir="auto">{t('ntf.deleteText', { title: deleting.title })}</p></ConfirmDialog>}
  </>;
}
