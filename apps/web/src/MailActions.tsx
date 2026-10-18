// Email Center phase 2 (docs/email.md#email-center): the parts the mail view adds for flags, moves,
// delete for good, search and the background refresh of unread counts.
// - MailBulkBar: select all, then read/unread, star, and the moves the folder allows.
// - MailSearchBar: IMAP search in the open folder, with Unread and Starred filters.
// - DeleteApproval: delete for good waits for an approval of exactly those messages (payload hash).
// - useMailSync: refreshes the folder list (unread counts) every minute while the page is visible.
// - useMailShortcuts: single-key list shortcuts, never while typing.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type RefObject } from 'react';
import {
  Archive, ArchiveRestore, CircleDot, Inbox, Mail, MailOpen, Search, ShieldAlert, ShieldCheck, Star, StarOff, Trash2, X,
} from 'lucide-react';
import type { Approval } from '@anum/contracts';
import * as api from './lib/api';
import { expiryLabel, shortHash } from './lib/approvals';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import {
  NO_FILTERS, filtersActive, foldersWithNewMail, nextSyncDelay, normalizeQuery, selectAllState, shortcutFor,
  type FolderLike, type MailAction, type MailFilters, type MailShortcut,
} from './lib/mailActions';
import './mailCenter.css';

const ICONS: Record<MailAction, typeof Mail> = {
  read: MailOpen, unread: Mail, star: Star, unstar: StarOff, archive: Archive, junk: ShieldAlert, notJunk: ShieldCheck,
  trash: Trash2, restore: ArchiveRestore, toInbox: Inbox, deleteForever: Trash2,
};

export const actionLabel = (action: MailAction): string => t(`mailc.action.${action}` as MessageKey);
/** "Archived 3 messages." after a flag or move the server accepted. */
export const actionDone = (action: Exclude<MailAction, 'deleteForever'>, count: number): string => t(`mailc.done.${action}` as MessageKey, { count });

/** The bar over the list: a tri-state select-all box, the count, and the actions for the selection. */
export function MailBulkBar({ items, selected, moves, busy, onToggleAll, onClear, onAction }: {
  items: { uid: number; seen: boolean; flagged: boolean }[]; selected: ReadonlySet<number>; moves: MailAction[]; busy: boolean;
  onToggleAll: () => void; onClear: () => void; onAction: (action: MailAction) => void;
}) {
  const box = useRef<HTMLInputElement>(null);
  const state = selectAllState(items, selected);
  const count = items.filter((m) => selected.has(m.uid)).length;
  useEffect(() => { if (box.current) box.current.indeterminate = state === 'some'; }, [state]);
  const picked = items.filter((m) => selected.has(m.uid));
  const readAction: MailAction = picked.some((m) => !m.seen) ? 'read' : 'unread';
  const starAction: MailAction = picked.some((m) => !m.flagged) ? 'star' : 'unstar';
  const button = (action: MailAction) => {
    const Icon = ICONS[action];
    return <button key={action} type="button" className={`secondary mailcAction ${action === 'deleteForever' ? 'danger' : ''}`} title={actionLabel(action)} disabled={busy} onClick={() => onAction(action)}>
      <Icon aria-hidden="true" /><span>{actionLabel(action)}</span>
    </button>;
  };
  return <div className="mailcBulk" role="toolbar" aria-label={t('mailc.bulk.label')}>
    <label className="mailcCheck"><input ref={box} type="checkbox" checked={state === 'all'} disabled={!items.length || busy} onChange={onToggleAll} /><span className="visuallyHidden">{t('mailc.bulk.selectAll')}</span></label>
    {count > 0 ? <>
      <span className="mailcCount" aria-live="polite">{t('mailc.bulk.selected', { count })}</span>
      <div className="mailcActions">
        {button(readAction)}
        {button(starAction)}
        {moves.map(button)}
        <button type="button" className="secondary mailcIcon" aria-label={t('mailc.bulk.clear')} title={t('mailc.bulk.clear')} disabled={busy} onClick={onClear}><X aria-hidden="true" /></button>
      </div>
    </> : <span className="mailcCount muted">{t('mailc.bulk.selectAll')}</span>}
  </div>;
}

/** The star in a list row: its own button, so it never opens the message. */
export function StarButton({ flagged, subject, disabled, onToggle }: { flagged: boolean; subject: string; disabled?: boolean; onToggle: () => void }) {
  return <button type="button" className={`mailcStar ${flagged ? 'on' : ''}`} aria-pressed={flagged} disabled={disabled}
    aria-label={flagged ? t('mailc.unstarLabel', { subject }) : t('mailc.starLabel', { subject })} onClick={(e) => { e.stopPropagation(); onToggle(); }}>
    <Star aria-hidden="true" />
  </button>;
}

/** Search in the open folder (the mail server's IMAP SEARCH) with the Unread and Starred filters. */
export function MailSearchBar({ filters, busy, inputRef, onApply }: { filters: MailFilters; busy?: boolean; inputRef?: RefObject<HTMLInputElement | null>; onApply: (next: MailFilters) => void }) {
  const [text, setText] = useState(filters.q);
  const id = useId();
  useEffect(() => { setText(filters.q); }, [filters.q]);
  function submit(event: FormEvent) {
    event.preventDefault();
    onApply({ ...filters, q: normalizeQuery(text) });
  }
  return <form className="mailcSearch" role="search" aria-label={t('mailc.search.label')} onSubmit={submit}>
    <div className="mailcSearchRow">
      <label htmlFor={id} className="visuallyHidden">{t('mailc.search.label')}</label>
      <input id={id} ref={inputRef} type="search" value={text} maxLength={200} placeholder={t('mailc.search.placeholder')} onChange={(e) => setText(e.target.value)} />
      <button type="submit" className="secondary mailcIcon" aria-label={t('mailc.search.submit')} title={t('mailc.search.submit')} disabled={busy}><Search aria-hidden="true" /></button>
    </div>
    <fieldset className="mailcFilters">
      <legend className="visuallyHidden">{t('mailc.filter.label')}</legend>
      <label className="mailcChip"><input type="checkbox" checked={filters.unread} onChange={(e) => onApply({ ...filters, q: normalizeQuery(text), unread: e.target.checked })} />{t('mailc.filter.unread')}</label>
      <label className="mailcChip"><input type="checkbox" checked={filters.flagged} onChange={(e) => onApply({ ...filters, q: normalizeQuery(text), flagged: e.target.checked })} />{t('mailc.filter.starred')}</label>
      {filtersActive(filters) && <button type="button" className="linkButton" onClick={() => { setText(''); onApply(NO_FILTERS); }}>{t('mailc.search.clear')}</button>}
    </fieldset>
  </form>;
}

/**
 * Delete for good: the API answered with a paused call bound to exactly these messages. Owners
 * approve here (the payload hash goes back with the decision); others are told who decides.
 */
export function DeleteApproval({ approval, folderName, count, owner, onDeleted, onDismiss, onOpenApprovals }: {
  approval: Approval; folderName: string; count: number; owner: boolean; onDeleted: () => void; onDismiss: () => void; onOpenApprovals?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  async function approve() {
    setBusy(true); setError(null);
    try {
      const result = await api.approveTask(approval);
      if (result.run && result.run.status === 'failed') { setError(result.run.result ?? t('mailc.failed', { reason: '' })); return; }
      onDeleted();
    } catch (e) { setError(t('mailc.failed', { reason: describeError(e) })); } finally { setBusy(false); }
  }
  return <section className="mailcApproval" role="group" aria-labelledby={titleId}>
    <p className="mailcApprovalTitle"><ShieldAlert aria-hidden="true" /><strong id={titleId}>{t('mailc.delete.title')}</strong></p>
    <p>{t('mailc.delete.text', { count, folder: folderName })}</p>
    <dl className="mailcMeta">
      <div><dt>{t('mailc.delete.hash')}</dt><dd><code title={approval.payloadHash ?? ''}>{shortHash(approval.payloadHash)}</code></dd></div>
      <div><dt>{t('mailc.delete.expires')}</dt><dd>{expiryLabel(approval) || '-'}</dd></div>
    </dl>
    <p className="muted" role="status">{t('mailc.delete.waiting')}</p>
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    <div className="actions">
      {owner ? <button type="button" className="danger" disabled={busy} onClick={() => void approve()}><Trash2 aria-hidden="true" />{busy ? t('mailc.delete.approving') : t('mailc.delete.approve')}</button>
        : <p className="muted">{t('mailc.delete.ownerOnly')}</p>}
      {onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('mailc.delete.openApprovals')}</button>}
      <button type="button" className="secondary" disabled={busy} onClick={onDismiss}>{t('mailc.delete.dismiss')}</button>
    </div>
  </section>;
}

export interface MailSyncState { checking: boolean; checkedAt: string | null; error: string | null; retryMinutes: number }

/**
 * Background refresh of the folder list (and so the unread counts) while the page is visible and the
 * browser is online: every minute, backing off to five minutes after failures. `onNewMail` hears which
 * folders gained unread mail; the caller decides whether to reload the open list.
 */
export function useMailSync<F extends FolderLike>({ enabled, load, onFolders, onNewMail }: {
  enabled: boolean; load: () => Promise<F[]>; onFolders: (folders: F[]) => void; onNewMail?: (folderIds: string[], folders: F[], before: F[]) => void;
}): MailSyncState & { checkNow: () => void } {
  const [state, setState] = useState<MailSyncState>({ checking: false, checkedAt: null, error: null, retryMinutes: 1 });
  const failures = useRef(0);
  const last = useRef<F[] | null>(null);
  const timer = useRef<number | null>(null);
  const running = useRef(false);
  const handlers = useRef({ load, onFolders, onNewMail });
  handlers.current = { load, onFolders, onNewMail };

  const run = useCallback(async () => {
    if (running.current) return;
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return;
    running.current = true;
    setState((s) => ({ ...s, checking: true }));
    try {
      const folders = await handlers.current.load();
      const before = last.current;
      last.current = folders;
      handlers.current.onFolders(folders);
      if (before) {
        const fresh = foldersWithNewMail(before, folders);
        if (fresh.length) handlers.current.onNewMail?.(fresh, folders, before);
      }
      failures.current = 0;
      setState({ checking: false, checkedAt: new Date().toISOString(), error: null, retryMinutes: 1 });
    } catch (e) {
      failures.current += 1;
      setState((s) => ({ ...s, checking: false, error: describeError(e), retryMinutes: Math.round(nextSyncDelay(failures.current) / 60_000) }));
    } finally { running.current = false; }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    const schedule = () => {
      if (stopped) return;
      timer.current = window.setTimeout(async () => { await run(); schedule(); }, nextSyncDelay(failures.current));
    };
    schedule();
    const wake = () => { if (document.visibilityState === 'visible') void run(); };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    return () => {
      stopped = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('online', wake);
    };
  }, [enabled, run]);

  return { ...state, checkNow: () => { void run(); } };
}

/** A short line under the folder list: when mail was last checked, or why it could not be. */
export function SyncStatus({ state, onCheckNow }: { state: MailSyncState; onCheckNow: () => void }) {
  return <p className="mailcSync" role="status" aria-live="polite">
    <span className={state.error ? 'mailcSyncError' : 'muted'}>
      {state.checking ? t('mailc.sync.checking')
        : state.error ? t('mailc.sync.failed', { reason: state.error, minutes: formatNumber(state.retryMinutes) })
          : state.checkedAt ? t('mailc.sync.checked', { when: formatDateTime(state.checkedAt, { timeStyle: 'short' }) }) : ''}
    </span>
    <button type="button" className="linkButton" disabled={state.checking} onClick={onCheckNow}>{t('mailc.sync.now')}</button>
  </p>;
}

/** Single-key shortcuts on the mail page, skipped while typing or inside a dialog. */
export function useMailShortcuts(enabled: boolean, handler: (shortcut: MailShortcut, event: KeyboardEvent) => void) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    if (!enabled) return undefined;
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = Boolean(target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.closest('[role="dialog"]')));
      // Enter and arrow keys keep their normal meaning on buttons and links.
      const onControl = Boolean(target && /^(BUTTON|A|SUMMARY)$/.test(target.tagName));
      const shortcut = shortcutFor(event.key, { ctrl: event.ctrlKey, meta: event.metaKey, alt: event.altKey, typing });
      if (!shortcut) return;
      if (onControl && (shortcut === 'open' || event.key.startsWith('Arrow'))) return;
      event.preventDefault();
      ref.current(shortcut, event);
    };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [enabled]);
}
