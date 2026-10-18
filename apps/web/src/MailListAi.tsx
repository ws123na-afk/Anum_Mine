// AI and approvals on the Email list (docs/email.md#ai-on-email): stored labels as chips on each row,
// "Label all" for the shown page, a tidy-up that selects newsletters and notifications, and bulk
// archive and export of a selection, each through an owner's approval of exactly those messages
// (payload hash shown). An approved export downloads as an mbox file for one hour.
import { useCallback, useEffect, useId, useState } from 'react';
import { Archive, CircleDot, Download, FileDown, ShieldAlert, Sparkles, Tags } from 'lucide-react';
import * as api from './lib/api';
import { expiryLabel, shortHash } from './lib/approvals';
import { describeError } from './lib/errors';
import { t } from './lib/i18n';
import type { MailMessage } from './lib/mail';
import {
  ARCHIVE_MAX, EXPORT_MAX, assistFailure, capSelection, exportFailure, exportFileName, mergeLabels, priorityTone, rowLabelText, tidyCandidates, unlabelled,
  type MailRowLabel,
} from './lib/mailAi';
import { downloadMailExport, getMailAiStatus, labelMailPage, listMailLabels, requestMailArchive, requestMailExport } from './lib/mailAiApi';
import { toApproval } from './lib/mailApi';
import './mailAi.css';

type Labels = ReadonlyMap<number, MailRowLabel>;
type Approval = ReturnType<typeof toApproval>;

/** The stored labels of the shown rows; reloaded when the page's messages change. */
export function useMailLabels(accountId: string, folderId: string | null, items: MailMessage[]) {
  const [labels, setLabels] = useState<Labels>(new Map());
  const key = `${accountId}|${folderId}|${items.map((m) => m.uid).join(',')}`;
  useEffect(() => {
    if (!folderId || !items.length) { setLabels(new Map()); return; }
    let live = true;
    listMailLabels(accountId, folderId, items.map((m) => ({ uid: m.uid, message_id: m.message_id }))).then(
      (found) => { if (live) setLabels(mergeLabels(new Map(), found)); },
      // Labels are an extra: the list works without them.
      () => { if (live) setLabels(new Map()); },
    );
    return () => { live = false; };
  }, [key]);
  const add = useCallback((fresh: MailRowLabel[]) => setLabels((current) => mergeLabels(current, fresh)), []);
  return { labels, add };
}

/** The chips under a row's subject (category, and the priority unless it is normal). */
export function RowLabels({ label }: { label: MailRowLabel | undefined }) {
  if (!label) return null;
  const [category, priority] = rowLabelText(label);
  return <span className="mailAiRowLabels">
    <span className="statusBadge neutral">{category}</span>
    {priority && <span className={`statusBadge ${priorityTone(label.priority)}`}>{priority}</span>}
  </span>;
}

type Pending = { kind: 'archive' | 'export'; approval: Approval; uids: number[]; state: 'waiting' | 'approving' | 'approved' | 'downloading' };

export function MailListTools({ accountId, folderId, folderName, items, selected, labels, canAct, owner, onLabels, onSelect, onArchived, onOpenApprovals }: {
  accountId: string; folderId: string; folderName: string; items: MailMessage[]; selected: ReadonlySet<number>; labels: Labels; canAct: boolean; owner: boolean;
  onLabels: (fresh: MailRowLabel[]) => void; onSelect: (uids: number[]) => void; onArchived: (uids: number[]) => void; onOpenApprovals?: () => void;
}) {
  const [ai, setAi] = useState<'checking' | 'ready' | 'off'>('checking');
  const [busy, setBusy] = useState<'label' | 'archive' | 'export' | null>(null);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);
  const titleId = useId();

  useEffect(() => { getMailAiStatus().then((s) => setAi(s.available ? 'ready' : 'off'), () => setAi('off')); }, []);
  // A new folder starts clean.
  useEffect(() => { setPending(null); setNote(null); setCardError(null); }, [folderId]);

  const uids = items.map((m) => m.uid);
  const todo = unlabelled(uids, labels);
  const tidy = tidyCandidates(uids, labels);
  const picked = uids.filter((uid) => selected.has(uid));

  async function labelAll() {
    setBusy('label'); setNote(null);
    try {
      const result = await labelMailPage(accountId, folderId, todo);
      onLabels(result.labels);
      setNote({ text: result.failed ? t('mailai.labelAll.partly', { count: result.labelled, failed: result.failed }) : t('mailai.labelAll.done', { count: result.labelled }) });
    } catch (e) {
      const found = assistFailure(e);
      if (found.kind === 'unavailable') setAi('off');
      setNote({ text: found.text, error: true });
    } finally { setBusy(null); }
  }

  async function ask(kind: 'archive' | 'export') {
    const { uids: chosen, dropped } = capSelection(picked, kind === 'export' ? EXPORT_MAX : ARCHIVE_MAX);
    setBusy(kind); setNote(null); setCardError(null);
    try {
      const result = await (kind === 'export' ? requestMailExport : requestMailArchive)(accountId, folderId, chosen);
      setPending({ kind, approval: toApproval(result.approval), uids: chosen, state: 'waiting' });
      if (dropped) setNote({ text: t('mailai.bulk.capped', { count: chosen.length, dropped }) });
    } catch (e) { setNote({ text: assistFailure(e).text, error: true }); } finally { setBusy(null); }
  }

  async function approve() {
    if (!pending) return;
    setPending({ ...pending, state: 'approving' }); setCardError(null);
    try {
      const result = await api.approveTask(pending.approval);
      if (result.run && result.run.status === 'failed') { setCardError(result.run.result ?? t('mailai.failed')); setPending({ ...pending, state: 'waiting' }); return; }
      if (pending.kind === 'archive') { onArchived(pending.uids); setPending(null); setNote({ text: t('mailai.archive.done', { count: pending.uids.length }) }); return; }
      setPending({ ...pending, state: 'approved' });
    } catch (e) { setCardError(describeError(e)); setPending({ ...pending, state: 'waiting' }); }
  }

  async function download() {
    if (!pending) return;
    setPending({ ...pending, state: 'downloading' }); setCardError(null);
    try {
      await downloadMailExport(pending.approval.id, exportFileName());
      setPending({ ...pending, state: 'approved' });
      setNote({ text: t('mailai.export.downloaded', { count: pending.uids.length }) });
    } catch (e) {
      const found = exportFailure(e);
      setCardError(found.text);
      setPending({ ...pending, state: found.kind === 'pending' ? 'waiting' : 'approved' });
    }
  }

  const showBar = (ai === 'ready' && todo.length > 0) || tidy.length > 0 || (canAct && picked.length > 0);
  return <>
    {showBar && <div className="mailAiList" role="toolbar" aria-label={t('mailai.list.label')}>
      {ai === 'ready' && todo.length > 0 && <button type="button" className="secondary" disabled={busy !== null} aria-busy={busy === 'label'} onClick={() => void labelAll()}><Tags aria-hidden="true" />{t('mailai.labelAll', { count: todo.length })}</button>}
      {tidy.length > 0 && canAct && <button type="button" className="secondary" disabled={busy !== null} onClick={() => onSelect(tidy)}><Sparkles aria-hidden="true" />{t('mailai.tidy.select', { count: tidy.length })}</button>}
      {canAct && picked.length > 0 && <>
        <button type="button" className="secondary" disabled={busy !== null || pending !== null} aria-busy={busy === 'archive'} onClick={() => void ask('archive')}><Archive aria-hidden="true" />{t('mailai.bulk.archive', { count: Math.min(picked.length, ARCHIVE_MAX) })}</button>
        <button type="button" className="secondary" disabled={busy !== null || pending !== null} aria-busy={busy === 'export'} onClick={() => void ask('export')}><FileDown aria-hidden="true" />{t('mailai.bulk.export', { count: Math.min(picked.length, EXPORT_MAX) })}</button>
      </>}
    </div>}
    {busy === 'label' && <p className="muted mailAiListNote" role="status" aria-live="polite">{t('mailai.labelAll.working', { count: todo.length })}</p>}
    {note && <p className={`notice mailAiListNote ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{note.text}</p>}
    {pending && <section className="mailcApproval" role="group" aria-labelledby={titleId}>
      <p className="mailcApprovalTitle"><ShieldAlert aria-hidden="true" /><strong id={titleId}>{pending.kind === 'archive' ? t('mailai.archive.title') : t('mailai.export.title')}</strong></p>
      <p>{pending.kind === 'archive' ? t('mailai.archive.text', { count: pending.uids.length, folder: folderName }) : t('mailai.export.text', { count: pending.uids.length, folder: folderName })}</p>
      <dl className="mailcMeta">
        <div><dt>{t('mailc.delete.hash')}</dt><dd><code title={pending.approval.payloadHash ?? ''}>{shortHash(pending.approval.payloadHash)}</code></dd></div>
        {pending.state !== 'approved' && pending.state !== 'downloading' && <div><dt>{t('mailc.delete.expires')}</dt><dd>{expiryLabel(pending.approval) || '-'}</dd></div>}
      </dl>
      {cardError && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{cardError}</p>}
      {(pending.state === 'waiting' || pending.state === 'approving') && <>
        <p className="muted" role="status">{t('mailai.bulk.waiting')}</p>
        <div className="actions">
          {owner ? <button type="button" disabled={pending.state === 'approving'} onClick={() => void approve()}>{pending.kind === 'archive' ? <Archive aria-hidden="true" /> : <FileDown aria-hidden="true" />}{pending.state === 'approving' ? t('mailai.bulk.approving') : pending.kind === 'archive' ? t('mailai.archive.approve') : t('mailai.export.approve')}</button>
            : <p className="muted">{t('mailai.bulk.ownerOnly')}</p>}
          {onOpenApprovals && <button type="button" className="secondary" onClick={onOpenApprovals}>{t('mailc.delete.openApprovals')}</button>}
          <button type="button" className="secondary" disabled={pending.state === 'approving'} onClick={() => setPending(null)}>{t('mailc.delete.dismiss')}</button>
        </div>
      </>}
      {(pending.state === 'approved' || pending.state === 'downloading') && <>
        <p role="status">{t('mailai.export.ready')}</p>
        <div className="actions">
          <button type="button" disabled={pending.state === 'downloading'} aria-busy={pending.state === 'downloading'} onClick={() => void download()}><Download aria-hidden="true" />{pending.state === 'downloading' ? t('mailai.export.downloading') : t('mailai.export.download')}</button>
          <button type="button" className="secondary" onClick={() => setPending(null)}>{t('mailai.export.done')}</button>
        </div>
      </>}
    </section>}
  </>;
}
