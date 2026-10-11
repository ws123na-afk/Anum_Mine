import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { CircleDot, Download, FileText, Lock, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied, ApiError } from './lib/errors';
import { sourceKindLabel } from './lib/retrieval';

type Detail = { state: 'loading' } | { state: 'ready'; file: api.FileRecord } | { state: 'missing' } | { state: 'denied' } | { state: 'error'; message: string };

/**
 * Workspace files: upload, list, download, delete, and a detail panel for one file. "Sources used"
 * on a run opens a file here by id (`focusFileId`); a row's name opens the same detail.
 * A real button opens the (hidden) file picker, so Upload is reachable and operable from the keyboard.
 */
export function FilesView({ focusFileId, onFocusChange }: { focusFileId: string | null; onFocusChange(fileId: string | null): void }) {
  const picker = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<api.FileRecord[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const load = useCallback(() => api.getFiles().then((x) => { setItems(x); setError(null); }).catch((e) => setError(describeError(e))).finally(() => setLoading(false)), []);
  useEffect(() => { void load(); }, [load]);

  const upload = (file: File) => { setNote(null); void api.uploadFile(file).then(() => { setNote(`Uploaded ${file.name}.`); return load(); }).catch((e) => setError(describeError(e))); };
  const remove = (file: api.FileRecord) => api.deleteFile(file.id).then(() => {
    setNote(`Deleted ${file.name}.`);
    if (focusFileId === file.id) onFocusChange(null);
    return load();
  });

  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Workspace storage</p><h2>Files</h2><p>Scoped files with checksum-backed integrity.</p></div>
      <button type="button" className="uploadButton" onClick={() => picker.current?.click()}><Upload aria-hidden="true" />Upload</button>
      <input ref={picker} type="file" hidden aria-label="Upload file" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) upload(f); }} />
    </section>
    {focusFileId && <FileDetail key={focusFileId} fileId={focusFileId} onClose={() => onFocusChange(null)} onDelete={remove} />}
    {loading && <Notice>Loading files...</Notice>}
    {error && <Notice error>{error}</Notice>}
    {note && <Notice>{note}</Notice>}
    <section className="surface memoryTable" aria-label="Files">{items.map((x) => (
      <div className={`memoryRow ${x.id === focusFileId ? 'focusedRow' : ''}`} key={x.id}>
        <div><span className="typeIcon"><FileText /></span><div>
          <button type="button" className="rowLink" aria-label={`Show details of ${x.name}`} aria-current={x.id === focusFileId ? 'true' : undefined} onClick={() => onFocusChange(x.id)}><strong>{x.name}</strong></button>
          <p>{`${sourceKindLabel({ sourceType: 'file', kind: x.content_type })} · ${bytes(x.size_bytes)}`}</p></div></div>
        <code>{x.sha256.slice(0, 16)}...</code>
        <div className="rowActions">
          <button className="iconButton" title="Download" aria-label={`Download ${x.name}`} onClick={() => void api.downloadFile(x.id, x.name).catch((e) => setError(describeError(e)))}><Download aria-hidden="true" /></button>
          <button className="iconButton" title="Delete" aria-label={`Delete ${x.name}`} onClick={() => { if (confirm(`Delete ${x.name}?`)) void remove(x).catch((e) => setError(describeError(e))); }}><Trash2 aria-hidden="true" /></button>
        </div>
      </div>))}
    </section>
    {!loading && !error && !items.length && <div className="emptyState"><Search /><h3>No files</h3><p>Upload a file to this workspace. Text files are indexed so tasks can use them.</p></div>}
  </>;
}

function FileDetail({ fileId, onClose, onDelete }: { fileId: string; onClose(): void; onDelete(file: api.FileRecord): Promise<void> }) {
  const [detail, setDetail] = useState<Detail>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0), [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [actionError, setActionError] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    let live = true;
    setDetail({ state: 'loading' });
    api.getFile(fileId).then(
      (file) => { if (live) setDetail({ state: 'ready', file }); },
      (e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setDetail({ state: 'missing' });
        else if (isPermissionDenied(e)) setDetail({ state: 'denied' });
        else setDetail({ state: 'error', message: describeError(e) });
      },
    );
    return () => { live = false; };
  }, [fileId, attempt]);
  // Bring the panel into view and move focus to it once it has something to say.
  useEffect(() => { if (detail.state !== 'loading') { heading.current?.scrollIntoView({ block: 'nearest' }); heading.current?.focus(); } }, [detail.state]);

  const title = detail.state === 'ready' ? detail.file.name : 'File detail';
  return (
    <section className="surface fileDetail" aria-labelledby="file-detail-heading" aria-busy={detail.state === 'loading'}>
      <div className="sectionHeader">
        <div><p className="eyebrow">File detail</p><h2 id="file-detail-heading" ref={heading} tabIndex={-1}>{title}</h2></div>
        <button type="button" className="iconButton" aria-label="Close file detail" title="Close" onClick={onClose}><X aria-hidden="true" /></button>
      </div>
      {detail.state === 'loading' && <Notice>Loading file details...</Notice>}
      {detail.state === 'missing' && <Notice error>This file no longer exists in this workspace. It may have been deleted; runs that used it still list its id <code>{fileId}</code>.</Notice>}
      {detail.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />Seeing files needs a viewer, member or owner role in this workspace. Ask a workspace owner for access.</p>}
      {detail.state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>Could not load this file: {detail.message} Check the connection to the API, then retry.</span><button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />Retry</button></div>}
      {detail.state === 'ready' && <>
        <dl className="taskMeta">
          <div><dt>Kind</dt><dd>{sourceKindLabel({ sourceType: 'file', kind: detail.file.content_type })} <small className="muted">({detail.file.content_type})</small></dd></div>
          <div><dt>Size</dt><dd>{bytes(detail.file.size_bytes)}</dd></div>
          <div><dt>Uploaded</dt><dd>{date(detail.file.created_at)}</dd></div>
          <div><dt>Uploaded by</dt><dd><code>{detail.file.created_by}</code></dd></div>
          <div><dt>File id</dt><dd><code>{detail.file.id}</code></dd></div>
          <div className="wideMeta"><dt>SHA-256</dt><dd><code>{detail.file.sha256}</code></dd></div>
        </dl>
        {actionError && <Notice error>{actionError}</Notice>}
        <div className="actions">
          <button type="button" onClick={() => { setActionError(null); void api.downloadFile(detail.file.id, detail.file.name).catch((e) => setActionError(describeError(e))); }}><Download aria-hidden="true" />Download</button>
          {confirming
            ? <><button type="button" className="danger" disabled={busy} onClick={() => { setBusy(true); setActionError(null); void onDelete(detail.file).catch((e) => { setActionError(permissionAware(e)); setBusy(false); }); }}>Delete {detail.file.name}</button><button type="button" className="secondary" onClick={() => setConfirming(false)}>Cancel</button></>
            : <button type="button" className="secondary" onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />Delete</button>}
        </div>
      </>}
    </section>
  );
}

function permissionAware(e: unknown): string {
  return isPermissionDenied(e) ? 'Deleting files needs a member or owner role in this workspace.' : describeError(e);
}
function Notice({ children, error }: { children: ReactNode; error?: boolean }) { return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>; }
const bytes = (v: number) => v < 1024 ? `${v} B` : v < 1048576 ? `${(v / 1024).toFixed(1)} KB` : `${(v / 1048576).toFixed(1)} MB`;
const date = (v: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(v));
