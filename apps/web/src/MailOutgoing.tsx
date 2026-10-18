// Email Center outgoing mail (docs/email.md#attachments, docs/email.md#scheduled-send): the parts the
// composer and the folder rail add for attached files and scheduled send.
// - ComposerFiles: attach files (checked and scanned by the API), see their size and scan state,
//   download or remove them. A change withdraws a pending approval or a schedule.
// - SchedulePicker: pick a time in the person's own zone (or a quick pick), or see and cancel the
//   time a message is scheduled for.
// - ScheduledRail: the person's scheduled messages, soonest first, in the folder rail.
import { useEffect, useId, useRef, useState } from 'react';
import { CalendarClock, CircleDot, Download, Paperclip, RefreshCw, ShieldAlert, X } from 'lucide-react';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { draftTone, formatBytes, type MailDraft, type MailOutgoingFile, type MailSetup } from './lib/mail';
import { fileProblems, fromLocalInput, quickTimes, scheduleProblem, scheduledFor, toLocalInput, totalBytes } from './lib/mailOutgoing';
import { downloadOutgoingAttachment, listScheduledMail, removeMailAttachment, uploadMailAttachment } from './lib/mailOutgoingApi';
import './mailOutgoing.css';

type Note = { text: string; error?: boolean } | null;
const when = (value: string | Date) => formatDateTime(value, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function Notice({ note }: { note: Note }) {
  if (!note) return null;
  return <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{note.text}</p>;
}

type Upload = { key: number; name: string; loaded: number; total: number; abort: AbortController };

/** The files on a draft. `onRefresh` reloads the draft (status and files) without touching typed text. */
export function ComposerFiles({ draft, setup, editable, onRefresh }: { draft: MailDraft; setup: MailSetup; editable: boolean; onRefresh: () => Promise<void> }) {
  const files = draft.attachments ?? [];
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [notes, setNotes] = useState<{ text: string; error?: boolean }[]>([]);
  const [removing, setRemoving] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const counter = useRef(0);
  const hasLimits = setup.attachment_max_bytes !== undefined;

  async function attach(chosen: File[]) {
    const { ok, problems } = fileProblems(chosen, files, setup);
    const next: { text: string; error?: boolean }[] = problems.map((p) => ({
      error: true,
      text: t(`mailo.files.check.${p.key}` as MessageKey, {
        name: p.name, size: formatBytes(setup.attachment_max_bytes ?? 0), count: setup.attachments_per_message ?? 0, total: formatBytes(setup.attachments_total_bytes ?? 0),
      }),
    }));
    setNotes(next);
    let added = 0;
    for (const file of ok) {
      const upload: Upload = { key: (counter.current += 1), name: file.name, loaded: 0, total: file.size, abort: new AbortController() };
      setUploads((list) => [...list, upload]);
      try {
        await uploadMailAttachment(draft.id, file, (loaded, total) => setUploads((list) => list.map((u) => u.key === upload.key ? { ...u, loaded, total } : u)), upload.abort.signal);
        added += 1;
      } catch (e) {
        if (!(e instanceof DOMException && e.name === 'AbortError')) next.push({ error: true, text: t('mailo.files.failed', { name: file.name, reason: describeError(e) }) });
      } finally {
        setUploads((list) => list.filter((u) => u.key !== upload.key));
      }
    }
    if (added) next.unshift({ text: t('mailo.files.added', { count: added }) });
    setNotes([...next]);
    if (added || next.length) await onRefresh().catch(() => undefined);
  }

  async function remove(file: MailOutgoingFile) {
    setRemoving(file.id); setNotes([]);
    try { await removeMailAttachment(draft.id, file.id); setNotes([{ text: t('mailo.files.removed', { name: file.filename }) }]); await onRefresh(); }
    catch (e) { setNotes([{ error: true, text: t('mailo.files.removeFailed', { name: file.filename, reason: describeError(e) }) }]); }
    finally { setRemoving(null); }
  }

  if (!editable && files.length === 0) return null;
  const busy = uploads.length > 0 || removing !== null;
  return <section className="mailoFiles" aria-labelledby={titleId}>
    <div className="mailoFilesHead">
      <h3 id={titleId}><Paperclip aria-hidden="true" />{t('mailo.files.title')}</h3>
      {files.length > 0 && <span className="muted">{t('mailo.files.summary', { count: files.length, size: formatBytes(totalBytes(files)) })}</span>}
    </div>
    {draft.attachment_note && <Notice note={{ text: t('mailo.files.carried', { note: draft.attachment_note }) }} />}
    {(files.length > 0 || uploads.length > 0) && <ul className="mailoFileList">
      {files.map((file) => <li key={file.id} className="mailoFile">
        <span className="mailoFileName" dir="auto">{file.filename}</span>
        <small className="muted">{formatBytes(file.size)}</small>
        {!file.scanned && <span className="statusBadge neutral" title={t('mailo.files.notScannedHelp')}><ShieldAlert aria-hidden="true" />{t('mailo.files.notScanned')}</span>}
        <span className="mailoFileActions">
          <button type="button" className="secondary mailoIcon" aria-label={t('mailo.files.download', { name: file.filename })} title={t('mailo.files.download', { name: file.filename })}
            onClick={() => { downloadOutgoingAttachment(draft.id, file).catch((e) => setNotes([{ error: true, text: describeError(e) }])); }}><Download aria-hidden="true" /></button>
          {editable && <button type="button" className="secondary mailoIcon" disabled={busy} aria-label={t('mailo.files.remove', { name: file.filename })} title={t('mailo.files.remove', { name: file.filename })}
            onClick={() => void remove(file)}><X aria-hidden="true" /></button>}
        </span>
      </li>)}
      {uploads.map((u) => <li key={u.key} className="mailoFile uploading">
        <span className="mailoFileName" dir="auto">{u.name}</span>
        <progress max={u.total || 1} value={u.loaded} aria-label={t('mailo.files.uploading', { name: u.name })} />
        <button type="button" className="secondary mailoIcon" aria-label={t('mailo.files.cancel', { name: u.name })} title={t('mailo.files.cancel', { name: u.name })} onClick={() => u.abort.abort()}><X aria-hidden="true" /></button>
      </li>)}
    </ul>}
    {files.some((f) => !f.scanned) && <p className="muted mailoSmall">{t('mailo.files.notScannedHelp')}</p>}
    {notes.map((n, i) => <Notice key={i} note={n} />)}
    {editable && <div className="mailoFilesAttach">
      <input ref={picker} type="file" multiple hidden aria-label={t('mailo.files.input')} accept={setup.attachment_extensions?.join(',')}
        onChange={(e) => { const chosen = [...(e.target.files ?? [])]; e.target.value = ''; if (chosen.length) void attach(chosen); }} />
      <button type="button" className="secondary" disabled={busy} onClick={() => picker.current?.click()}><Paperclip aria-hidden="true" />{t('mailo.files.attach')}</button>
      {hasLimits && <small className="muted">{t('mailo.files.limits', { count: setup.attachments_per_message ?? 0, size: formatBytes(setup.attachment_max_bytes ?? 0), total: formatBytes(setup.attachments_total_bytes ?? 0), accepted: (setup.attachment_extensions ?? []).join(', ') })}</small>}
      {(draft.status === 'pending_approval' || draft.status === 'scheduled') && <small className="muted">{t('mailo.files.withdraws')}</small>}
    </div>}
  </section>;
}

/** Pick a send time, or show the time a message is scheduled for with Cancel schedule. */
export function SchedulePicker({ draft, setup, disabled, busy, onSchedule, onCancel }: {
  draft: MailDraft; setup: MailSetup; disabled: boolean; busy: boolean; onSchedule: (iso: string) => Promise<boolean>; onCancel: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const ids = { input: useId(), zone: useId(), title: useId() };
  const maxDays = setup.schedule_max_days ?? 30;
  const scheduled = scheduledFor(draft);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  if (scheduled) {
    return <div className="mailoSchedule current" role="status">
      <CalendarClock aria-hidden="true" />
      <p>{draft.status === 'scheduled' ? t('mailo.schedule.scheduled', { when: when(scheduled) }) : t('mailo.schedule.waiting', { when: when(scheduled) })}</p>
      <button type="button" className="secondary" disabled={disabled} onClick={() => void onCancel()}>{t('mailo.schedule.cancel')}</button>
    </div>;
  }
  if (!open) {
    return <button type="button" className="secondary" disabled={disabled} aria-expanded="false" onClick={() => {
      const now = new Date();
      setValue(toLocalInput(draft.send_at && new Date(draft.send_at).getTime() > now.getTime() + 60_000 ? draft.send_at : quickTimes(now)[0].when));
      setProblem(null); setOpen(true);
    }}><CalendarClock aria-hidden="true" />{t('mailo.schedule.open')}</button>;
  }
  const now = new Date();
  function submit(picked: string) {
    const at = fromLocalInput(picked);
    const found = scheduleProblem(at, new Date(), maxDays);
    if (found || !at) { setProblem(t(`mailo.schedule.problem.${found ?? 'empty'}` as MessageKey, { days: maxDays })); return; }
    setProblem(null);
    void onSchedule(at.toISOString()).then((done) => { if (done) setOpen(false); });
  }
  const time = (d: Date) => formatDateTime(d, { hour: '2-digit', minute: '2-digit' });
  return <div className="mailoSchedule" role="group" aria-labelledby={ids.title}>
    <p className="mailoScheduleTitle" id={ids.title}><CalendarClock aria-hidden="true" /><strong>{t('mailo.schedule.title')}</strong></p>
    <div className="mailoQuick">{quickTimes(now).map((q) => <button key={q.key} type="button" className="secondary" disabled={busy} onClick={() => { setValue(toLocalInput(q.when)); submit(toLocalInput(q.when)); }}>
      {t(`mailo.schedule.${q.key}` as MessageKey, { time: time(q.when) })}
    </button>)}</div>
    <label className="field" htmlFor={ids.input}><span>{t('mailo.schedule.at')}</span>
      <input id={ids.input} type="datetime-local" value={value} min={toLocalInput(new Date(now.getTime() + 60_000))} max={toLocalInput(new Date(now.getTime() + maxDays * 86_400_000))}
        aria-describedby={ids.zone} aria-invalid={problem ? true : undefined} onChange={(e) => { setValue(e.target.value); setProblem(null); }} />
    </label>
    <small id={ids.zone} className="muted">{t('mailo.schedule.zone', { zone })} {t('mailo.schedule.help')}</small>
    {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    {draft.send_at && draft.status === 'draft' && <small className="muted">{t('mailo.schedule.lastPicked', { when: when(draft.send_at) })}</small>}
    <div className="actions">
      <button type="button" disabled={busy || disabled} onClick={() => submit(value)}><CalendarClock aria-hidden="true" />{busy ? t('mailo.schedule.preparing') : t('mailo.schedule.submit')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={() => setOpen(false)}>{t('mailo.schedule.close')}</button>
    </div>
  </div>;
}

/** The scheduled messages in the folder rail; reloads when `version` changes. */
export function ScheduledRail({ accountId, version, open, current, onOpen }: { accountId: string; version: unknown; open: boolean; current: string | null; onOpen: (draftId: string) => void }) {
  const [items, setItems] = useState<MailDraft[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    listScheduledMail(accountId).then((value) => { if (live) setItems(value); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [accountId, version, reload]);
  return <details className="mailOutbox mailoRail" open={open || undefined}>
    <summary><CalendarClock aria-hidden="true" />{t('mailo.rail.title')}{items && <span className="mailOutboxCount">{formatNumber(items.length)}</span>}</summary>
    <p className="muted">{t('mailo.rail.hint')}</p>
    {!items && !failed && <p className="muted" role="status">{t('mailo.rail.loading')}</p>}
    {failed && <p className="muted">{t('mailo.rail.failed')} <button type="button" className="linkButton" onClick={() => setReload((n) => n + 1)}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></p>}
    {items && items.length === 0 && <p className="muted">{t('mailo.rail.empty')}</p>}
    {items && items.length > 0 && <ul className="railList">{items.map((d) => <li key={d.id}>
      <button type="button" className="railItem" aria-current={current === d.id ? 'true' : undefined} onClick={() => onOpen(d.id)}>
        <strong dir="auto">{d.subject || t('mail.list.noSubject')}</strong>
        <small><span className={`statusBadge ${draftTone(d.status)}`}>{t(`mail.draft.status.${d.status}` as MessageKey)}</span> {d.send_at ? <time dateTime={d.send_at}>{when(d.send_at)}</time> : null}</small>
      </button>
    </li>)}</ul>}
  </details>;
}
