import { Fragment, lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { CircleDot, Download, FileText, Image as ImageIcon, Lock, Pencil, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied, ApiError } from './lib/errors';
import {
  SORT_OPTIONS, canReindex, fileTypeLabel, formatBytes, indexBadge, mergePage, parseMarkdown, previewMode, progressText,
  renameProblem, uploadProblem, uploaderLabel, usageMeter, PAGE_SIZE, type ApiFile, type ApiFileUsage, type Block, type Inline, type SortValue,
} from './lib/files';
import './admin.css';
import './files.css';

const PdfPreview = lazy(() => import('./PdfPreview'));

type Detail = { state: 'loading' } | { state: 'ready'; file: ApiFile } | { state: 'missing' } | { state: 'denied' } | { state: 'error'; message: string };
type List = { state: 'loading' } | { state: 'ready' } | { state: 'denied' } | { state: 'error'; message: string };
interface Upload { key: string; name: string; size: number; loaded: number; state: 'uploading' | 'failed' | 'cancelled'; error?: string; abort: AbortController }

const READ_DENIED = 'Seeing files needs a viewer, member or owner role in this workspace. Ask a workspace owner for access.';
const MANAGE_NOTE = 'Only the person who uploaded this file or a workspace owner can rename or delete it.';

/**
 * Workspace files (docs/files.md): upload with progress and cancel, search, sort and keyset paging,
 * usage against the workspace quota, and a detail panel with preview, rename, re-index, download and
 * delete. "Sources used" on a run opens a file here by id (`focusFileId`).
 */
export function FilesView({ focusFileId, onFocusChange }: { focusFileId: string | null; onFocusChange(fileId: string | null): void }) {
  const picker = useRef<HTMLInputElement>(null);
  const searchId = useId();
  const [items, setItems] = useState<ApiFile[]>([]);
  const [list, setList] = useState<List>({ state: 'loading' });
  const [more, setMore] = useState(false), [paging, setPaging] = useState(false);
  const [usage, setUsage] = useState<ApiFileUsage | null>(null);
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [sort, setSort] = useState<SortValue>('created_at:desc');
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const me = api.currentUserId();

  const loadUsage = useCallback(() => api.getFileUsage().then(setUsage, () => setUsage(null)), []);
  const load = useCallback(async () => {
    setList((current) => current.state === 'ready' ? current : { state: 'loading' });
    try {
      const page = await api.getFiles({ query, sort });
      setItems(page); setMore(page.length === PAGE_SIZE); setList({ state: 'ready' });
    } catch (e) {
      setList(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) });
    }
  }, [query, sort]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadUsage(); }, [loadUsage]);

  async function loadMore() {
    const last = items.at(-1);
    if (!last) return;
    setPaging(true);
    try {
      const page = await api.getFiles({ query, sort, cursor: last.id });
      setItems((current) => mergePage(current, page)); setMore(page.length === PAGE_SIZE);
    } catch (e) {
      // A cursor whose file was deleted meanwhile: start the list again.
      if (e instanceof ApiError && e.status === 400) { setNote('The list changed while you were reading it, so it was reloaded.'); await load(); }
      else setProblem(`Could not load more files: ${describeError(e)}`);
    } finally { setPaging(false); }
  }

  async function startUploads(files: File[]) {
    setNote(null); setProblem(null);
    // The limits decide what is refused before sending; read them if they have not arrived yet.
    let limits = usage ?? await api.getFileUsage().catch(() => null);
    for (const file of files) {
      const refusal = uploadProblem(file, limits);
      const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`;
      if (refusal) { setUploads((u) => [...u, { key, name: file.name, size: file.size, loaded: 0, state: 'failed', error: refusal, abort: new AbortController() }]); continue; }
      const abort = new AbortController();
      setUploads((u) => [...u, { key, name: file.name, size: file.size, loaded: 0, state: 'uploading', abort }]);
      try {
        const created = await api.uploadFile(file, (loaded) => setUploads((u) => u.map((x) => x.key === key ? { ...x, loaded } : x)), abort.signal);
        setUploads((u) => u.filter((x) => x.key !== key));
        setNote(`Uploaded ${created.name}. ${indexBadge(created.index).detail}`);
        if (limits) limits = { ...limits, used_bytes: limits.used_bytes + created.size_bytes, file_count: limits.file_count + 1 };
        await Promise.all([load(), loadUsage()]);
      } catch (e) {
        const cancelled = e instanceof DOMException && e.name === 'AbortError';
        setUploads((u) => u.map((x) => x.key === key ? { ...x, state: cancelled ? 'cancelled' : 'failed', error: cancelled ? 'Upload cancelled; nothing was stored.' : uploadError(e) } : x));
      }
    }
  }

  async function remove(file: ApiFile) {
    await api.deleteFile(file.id);
    setConfirming(null);
    setNote(`Deleted ${file.name}.`);
    if (focusFileId === file.id) onFocusChange(null);
    await Promise.all([load(), loadUsage()]);
  }

  function replaceFile(file: ApiFile) { setItems((current) => current.map((x) => x.id === file.id ? file : x)); }

  const canUpload = usage?.can_upload ?? true;
  const meter = usage ? usageMeter(usage) : null;
  const searching = query.trim().length > 0;
  const submitSearch = (event: FormEvent) => { event.preventDefault(); setQuery(draft.trim()); };

  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Workspace storage</p><h2>Files</h2><p>Documents your agents can read. Text files are indexed so tasks can use them.</p></div>
      <button type="button" className="uploadButton" disabled={!canUpload} aria-describedby={!canUpload ? 'files-upload-denied' : undefined} onClick={() => picker.current?.click()}><Upload aria-hidden="true" />Upload</button>
      <input ref={picker} type="file" multiple hidden aria-label="Upload files" accept={usage?.accepted_extensions.join(',')} onChange={(e) => { const chosen = [...(e.target.files ?? [])]; e.target.value = ''; if (chosen.length) void startUploads(chosen); }} />
    </section>
    {!canUpload && <p className="notice" id="files-upload-denied" role="status"><Lock aria-hidden="true" />Uploading needs a member or owner role in this workspace. You can still open and download files.</p>}
    {usage && meter && <section className="surface filesUsage" aria-label="Storage">
      <div className="filesUsageTop"><strong>Storage</strong><span>{meter.text}</span></div>
      <div className={`meterTrack ${meter.tone === 'ok' ? '' : meter.tone}`} role="progressbar" aria-label="Workspace storage used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(meter.percent)} aria-valuetext={meter.text}><span style={{ width: `${meter.percent}%` }} /></div>
      <small className="muted">Up to {formatBytes(usage.max_upload_bytes)} per file. Accepted: {usage.accepted}.</small>
    </section>}
    {uploads.length > 0 && <section className="surface uploadQueue" aria-label="Uploads">
      {uploads.map((u) => <div className="uploadItem" key={u.key}>
        <div className="uploadItemTop"><strong>{u.name}</strong>
          {u.state === 'uploading'
            ? <button type="button" className="secondary" aria-label={`Cancel upload of ${u.name}`} onClick={() => u.abort.abort()}>Cancel</button>
            : <button type="button" className="iconButton" aria-label={`Dismiss ${u.name}`} title="Dismiss" onClick={() => setUploads((all) => all.filter((x) => x.key !== u.key))}><X aria-hidden="true" /></button>}
        </div>
        {u.state === 'uploading'
          ? <><div className="meterTrack" role="progressbar" aria-label={`Uploading ${u.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={u.size ? Math.round((u.loaded / u.size) * 100) : 0}><span style={{ width: `${u.size ? Math.min(100, (u.loaded / u.size) * 100) : 0}%` }} /></div><small className="muted" aria-live="polite">{progressText(u.loaded, u.size)}</small></>
          : <p className={`notice ${u.state === 'failed' ? 'errorNotice' : ''}`} role={u.state === 'failed' ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{u.error}</p>}
      </div>)}
    </section>}
    {focusFileId && <FileDetail key={focusFileId} fileId={focusFileId} me={me} canUpload={canUpload} onClose={() => onFocusChange(null)} onDelete={remove} onChange={replaceFile} />}
    {note && <Notice>{note}</Notice>}
    {problem && <Notice error>{problem}</Notice>}
    {list.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{READ_DENIED}</p>}
    {list.state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>Could not load files: {list.message} Check the connection to the API, then retry.</span><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />Retry</button></div>}
    {list.state !== 'denied' && (items.length > 0 || searching) && <form className="actions filesToolbar" role="search" onSubmit={submitSearch}>
      <label className="visuallyHidden" htmlFor={`${searchId}-q`}>Search files by name</label>
      <input id={`${searchId}-q`} type="search" value={draft} placeholder="Search files by name" maxLength={255} onChange={(e) => { setDraft(e.target.value); if (!e.target.value) setQuery(''); }} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />Search</button>
      <label className="visuallyHidden" htmlFor={`${searchId}-sort`}>Sort files</label>
      <select id={`${searchId}-sort`} value={sort} onChange={(e) => setSort(e.target.value as SortValue)}>{SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
    </form>}
    {list.state === 'loading' && <Notice>Loading files...</Notice>}
    {items.length > 0 && <section className="surface memoryTable filesTable" aria-label="Files">{items.map((x) => {
      const badge = indexBadge(x.index);
      return <div className={`memoryRow ${x.id === focusFileId ? 'focusedRow' : ''}`} key={x.id}>
        <div><span className="typeIcon">{x.kind === 'image' ? <ImageIcon aria-hidden="true" /> : <FileText aria-hidden="true" />}</span><div>
          <button type="button" className="rowLink" aria-label={`Show details of ${x.name}`} aria-current={x.id === focusFileId ? 'true' : undefined} onClick={() => onFocusChange(x.id)}><strong>{x.name}</strong></button>
          <p>{`${fileTypeLabel(x.content_type)} · ${formatBytes(x.size_bytes)} · ${uploaderLabel(x.created_by, me)} · `}<time dateTime={x.created_at}>{date(x.created_at)}</time></p></div></div>
        <span className={`statusBadge ${badge.tone}`} title={badge.detail}>{badge.label}</span>
        <div className="rowActions">
          {confirming === x.id
            ? <><span className="visuallyHidden" role="status">Delete {x.name}? This cannot be undone.</span><button type="button" className="danger" aria-label={`Confirm delete of ${x.name}`} onClick={() => void remove(x).catch((e) => { setConfirming(null); setProblem(isPermissionDenied(e) ? MANAGE_NOTE : `Could not delete ${x.name}: ${describeError(e)}`); })}>Delete</button><button type="button" className="secondary" aria-label={`Keep ${x.name}`} onClick={() => setConfirming(null)}>Keep</button></>
            : <>
              <button type="button" className="iconButton" title="Download" aria-label={`Download ${x.name}`} onClick={() => void api.downloadFile(x.id, x.name).catch((e) => setProblem(`Could not download ${x.name}: ${describeError(e)}`))}><Download aria-hidden="true" /></button>
              {x.can_manage && <button type="button" className="iconButton" title="Delete" aria-label={`Delete ${x.name}`} onClick={() => setConfirming(x.id)}><Trash2 aria-hidden="true" /></button>}
            </>}
        </div>
      </div>;
    })}
    </section>}
    {more && items.length > 0 && <div className="actions"><button type="button" className="secondary" onClick={() => void loadMore()} disabled={paging}>{paging ? 'Loading...' : 'Load more files'}</button></div>}
    {list.state === 'ready' && !items.length && (searching
      ? <div className="emptyState"><Search aria-hidden="true" /><h3>No files match "{query}"</h3><p>Try another part of the name.</p><div className="actions"><button type="button" className="secondary" onClick={() => { setDraft(''); setQuery(''); }}>Clear search</button></div></div>
      : <div className="emptyState"><Upload aria-hidden="true" /><h3>No files yet</h3><p>{canUpload ? 'Upload documents, data or images. Text files are indexed so tasks can use them.' : 'Nobody has uploaded a file to this workspace yet.'}</p>{canUpload && <div className="actions"><button type="button" onClick={() => picker.current?.click()}><Upload aria-hidden="true" />Upload a file</button></div>}</div>)}
  </>;
}

function uploadError(e: unknown): string {
  if (isPermissionDenied(e)) return 'Uploading needs a member or owner role in this workspace.';
  if (e instanceof ApiError && e.status === 413 && !e.detail) return 'The file is larger than the server accepts.';
  return describeError(e);
}

function FileDetail({ fileId, me, canUpload, onClose, onDelete, onChange }: { fileId: string; me: string | null; canUpload: boolean; onClose(): void; onDelete(file: ApiFile): Promise<void>; onChange(file: ApiFile): void }) {
  const [detail, setDetail] = useState<Detail>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0), [confirming, setConfirming] = useState(false), [busy, setBusy] = useState<null | 'delete' | 'rename' | 'reindex'>(null);
  const [actionError, setActionError] = useState<string | null>(null), [actionNote, setActionNote] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const renameId = useId();
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

  const update = (file: ApiFile) => { setDetail({ state: 'ready', file }); onChange(file); };
  async function act(kind: 'rename' | 'reindex', work: () => Promise<ApiFile>, done: (file: ApiFile) => string) {
    setBusy(kind); setActionError(null); setActionNote(null);
    try { const file = await work(); update(file); setActionNote(done(file)); setRenaming(null); }
    catch (e) { setActionError(isPermissionDenied(e) ? (kind === 'rename' ? MANAGE_NOTE : 'Re-indexing needs a member or owner role in this workspace.') : describeError(e)); }
    finally { setBusy(null); }
  }

  const title = detail.state === 'ready' ? detail.file.name : 'File detail';
  const file = detail.state === 'ready' ? detail.file : null;
  const badge = file ? indexBadge(file.index) : null;
  const renameIssue = file && renaming !== null ? renameProblem(file.name, renaming) : null;
  return (
    <section className="surface fileDetail" aria-labelledby="file-detail-heading" aria-busy={detail.state === 'loading'}>
      <div className="sectionHeader">
        <div><p className="eyebrow">File detail</p><h2 id="file-detail-heading" ref={heading} tabIndex={-1}>{title}</h2></div>
        <button type="button" className="iconButton" aria-label="Close file detail" title="Close" onClick={onClose}><X aria-hidden="true" /></button>
      </div>
      {detail.state === 'loading' && <Notice>Loading file details...</Notice>}
      {detail.state === 'missing' && <Notice error>This file no longer exists in this workspace. It may have been deleted; runs that used it still list its id <code>{fileId}</code>.</Notice>}
      {detail.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{READ_DENIED}</p>}
      {detail.state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>Could not load this file: {detail.message} Check the connection to the API, then retry.</span><button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />Retry</button></div>}
      {file && badge && <>
        <dl className="taskMeta">
          <div><dt>Kind</dt><dd>{fileTypeLabel(file.content_type)} <small className="muted">({file.content_type})</small></dd></div>
          <div><dt>Size</dt><dd>{formatBytes(file.size_bytes)}</dd></div>
          <div><dt>Uploaded</dt><dd><time dateTime={file.created_at}>{date(file.created_at)}</time></dd></div>
          <div><dt>Uploaded by</dt><dd>{uploaderLabel(file.created_by, me) === 'You' ? 'You' : <code>{file.created_by}</code>}</dd></div>
          {file.updated_at && file.updated_by && <div><dt>Renamed</dt><dd><time dateTime={file.updated_at}>{date(file.updated_at)}</time> by {uploaderLabel(file.updated_by, me) === 'You' ? 'you' : <code>{file.updated_by}</code>}</dd></div>}
          <div className="wideMeta"><dt>Retrieval</dt><dd><span className={`statusBadge ${badge.tone}`}>{badge.label}</span> <small className="muted">{badge.detail}</small></dd></div>
          <div><dt>File id</dt><dd><code>{file.id}</code></dd></div>
          <div className="wideMeta"><dt>SHA-256</dt><dd><code>{file.sha256}</code></dd></div>
        </dl>
        {actionError && <Notice error>{actionError}</Notice>}
        {actionNote && <Notice>{actionNote}</Notice>}
        {renaming !== null
          ? <form className="renameForm" onSubmit={(e) => { e.preventDefault(); if (!renameIssue) void act('rename', () => api.renameFile(file.id, renaming.trim()), (f) => `Renamed to ${f.name}.`); }}>
            <label className="field"><span>New name</span><input id={renameId} value={renaming} maxLength={255} autoFocus aria-invalid={Boolean(renameIssue)} aria-describedby={`${renameId}-hint`} onChange={(e) => setRenaming(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') setRenaming(null); }} /></label>
            <small id={`${renameId}-hint`} className={renameIssue ? 'fieldError' : 'muted'}>{renameIssue ?? 'The extension stays the same; the file and its index are kept.'}</small>
            <div className="actions"><button type="submit" disabled={Boolean(renameIssue) || busy !== null}>{busy === 'rename' ? 'Saving...' : 'Save name'}</button><button type="button" className="secondary" onClick={() => setRenaming(null)}>Cancel</button></div>
          </form>
          : <div className="actions">
            <button type="button" onClick={() => { setActionError(null); void api.downloadFile(file.id, file.name).catch((e) => setActionError(`Could not download: ${describeError(e)}`)); }}><Download aria-hidden="true" />Download</button>
            {file.can_manage && <button type="button" className="secondary" onClick={() => { setRenaming(file.name); setConfirming(false); }}><Pencil aria-hidden="true" />Rename</button>}
            {canUpload && canReindex(file.index) && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('reindex', () => api.reindexFile(file.id), (f) => indexBadge(f.index).detail)}><RefreshCw aria-hidden="true" />{busy === 'reindex' ? 'Re-indexing...' : 'Re-index'}</button>}
            {file.can_manage && (confirming
              ? <><button type="button" className="danger" disabled={busy !== null} onClick={() => { setBusy('delete'); setActionError(null); void onDelete(file).catch((e) => { setActionError(isPermissionDenied(e) ? MANAGE_NOTE : describeError(e)); setBusy(null); }); }} aria-label={`Confirm delete of ${file.name}`}>{busy === 'delete' ? 'Deleting...' : 'Delete permanently'}</button><button type="button" className="secondary" onClick={() => setConfirming(false)}>Keep</button></>
              : <button type="button" className="secondary" onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />Delete</button>)}
          </div>}
        {!file.can_manage && <p className="muted filesNote"><Lock aria-hidden="true" />{MANAGE_NOTE}</p>}
        <FilePreview key={`${file.id}-${file.sha256}`} file={file} />
      </>}
    </section>
  );
}

type Loaded<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error'; message: string };

/** Text and Markdown from the bounded preview route; images and PDFs from a short-lived link. */
function FilePreview({ file }: { file: ApiFile }) {
  const mode = previewMode(file);
  const [attempt, setAttempt] = useState(0);
  const [text, setText] = useState<Loaded<{ text: string; truncated: boolean }>>({ state: 'loading' });
  const [bytes, setBytes] = useState<Loaded<Blob>>({ state: 'loading' });
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [pdf, setPdf] = useState<ArrayBuffer | null>(null);
  const [source, setSource] = useState(false);

  useEffect(() => {
    if (!mode) return;
    let live = true;
    const abort = new AbortController();
    if (mode === 'text' || mode === 'markdown') {
      setText({ state: 'loading' });
      api.getFilePreview(file.id).then((p) => { if (live) setText({ state: 'ready', value: { text: p.text, truncated: p.truncated } }); }, (e) => { if (live) setText({ state: 'error', message: describeError(e) }); });
    } else {
      setBytes({ state: 'loading' });
      api.fetchFileBytes(file.id, abort.signal).then((blob) => { if (live) setBytes({ state: 'ready', value: blob }); }, (e) => { if (live) setBytes({ state: 'error', message: describeError(e) }); });
    }
    return () => { live = false; abort.abort(); };
  }, [file.id, mode, attempt]);

  useEffect(() => {
    if (bytes.state !== 'ready') return;
    if (mode === 'image') {
      const url = URL.createObjectURL(bytes.value);
      setImageUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    if (mode === 'pdf') { void bytes.value.arrayBuffer().then(setPdf); }
    return undefined;
  }, [bytes, mode]);

  if (!mode) return <section className="filePreview" aria-label="Preview"><h3>Preview</h3><p className="muted">{fileTypeLabel(file.content_type)} files have no preview here. Download the file to open it.</p></section>;
  const failed = (text.state === 'error' && (mode === 'text' || mode === 'markdown')) || (bytes.state === 'error' && (mode === 'image' || mode === 'pdf'));
  const failure = text.state === 'error' ? text.message : bytes.state === 'error' ? bytes.message : '';
  return (
    <section className="filePreview" aria-label="Preview">
      <div className="filePreviewHead"><h3>Preview</h3>{mode === 'markdown' && text.state === 'ready' && <span className="actions"><button type="button" className="secondary" aria-pressed={source} onClick={() => setSource((v) => !v)}>{source ? 'Show formatted' : 'Show source'}</button></span>}</div>
      {failed && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>Could not load the preview: {failure}</span><button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />Retry</button></div>}
      {!failed && (mode === 'text' || mode === 'markdown') && text.state === 'loading' && <Notice>Loading preview...</Notice>}
      {!failed && (mode === 'image' || mode === 'pdf') && bytes.state === 'loading' && <Notice>Loading preview...</Notice>}
      {text.state === 'ready' && (mode === 'text' || (mode === 'markdown' && source)) && <pre className="filePreviewText" tabIndex={0} aria-label={`Text of ${file.name}`}>{text.value.text}</pre>}
      {text.state === 'ready' && mode === 'markdown' && !source && <div className="filePreviewDoc" tabIndex={0} aria-label={`Formatted ${file.name}`}><MarkdownBlocks blocks={parseMarkdown(text.value.text)} /></div>}
      {text.state === 'ready' && text.value.truncated && <p className="muted">Showing the first 256 KB. Download the file for the rest.</p>}
      {mode === 'image' && imageUrl && <img className="filePreviewImage" src={imageUrl} alt={`Preview of ${file.name}`} />}
      {mode === 'pdf' && pdf && <Suspense fallback={<Notice>Opening the PDF...</Notice>}><PdfPreview data={pdf} name={file.name} /></Suspense>}
    </section>
  );
}

function MarkdownBlocks({ blocks }: { blocks: Block[] }) {
  return <>{blocks.map((block, i) => {
    switch (block.type) {
      case 'heading': { const Tag = `h${Math.min(6, block.level + 3)}` as 'h4'; return <Tag key={i}><Inlines items={block.children} /></Tag>; }
      case 'paragraph': return <p key={i}><Inlines items={block.children} /></p>;
      case 'quote': return <blockquote key={i}><Inlines items={block.children} /></blockquote>;
      case 'code': return <pre key={i}><code>{block.text}</code></pre>;
      case 'rule': return <hr key={i} />;
      case 'list': { const List = block.ordered ? 'ol' : 'ul'; return <List key={i}>{block.items.map((item, j) => <li key={j}><Inlines items={item} /></li>)}</List>; }
      default: return null;
    }
  })}</>;
}

function Inlines({ items }: { items: Inline[] }) {
  return <>{items.map((item, i) => {
    switch (item.type) {
      case 'text': return <Fragment key={i}>{item.text}</Fragment>;
      case 'code': return <code key={i}>{item.text}</code>;
      case 'strong': return <strong key={i}><Inlines items={item.children} /></strong>;
      case 'em': return <em key={i}><Inlines items={item.children} /></em>;
      case 'link': return <a key={i} href={item.href} target="_blank" rel="noopener noreferrer"><Inlines items={item.children} /></a>;
      default: return null;
    }
  })}</>;
}

function Notice({ children, error }: { children: ReactNode; error?: boolean }) { return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>; }
const date = (v: string) => new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(v));
