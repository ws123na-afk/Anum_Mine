import { Fragment, lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { CircleDot, Copy, Download, FileText, Image as ImageIcon, Link2, Lock, Pencil, RefreshCw, Search, Trash2, Upload, X } from 'lucide-react';
import * as api from './lib/api';
import type { RetrievalIndexStatus } from './lib/capabilities';
import { describeError, isPermissionDenied, ApiError } from './lib/errors';
import {
  sortOptions, canReindex, fileBadge, fileTypeLabel, formatBytes, indexBadge, isQuarantined, mergePage, parseMarkdown, previewMode, progressText,
  renameProblem, uploadProblem, uploaderLabel, usageMeter, PAGE_SIZE, type ApiFile, type ApiFileLink, type ApiFileUsage, type Block, type Inline, type SortValue,
} from './lib/files';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { FILE_INDEX_FILTERS, FILE_TYPE_FILTERS, figure, filterFiles, indexCounts, type FileIndexFilter, type FileTypeFilter } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { copyText } from './lib/clipboard';
import { PageHeader, PageSummary, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import { rich } from './rich';
import './admin.css';
import './files.css';
import { HelpLink } from './HelpLink';

const PdfPreview = lazy(() => import('./PdfPreview'));

type Detail = { state: 'loading' } | { state: 'ready'; file: ApiFile } | { state: 'missing' } | { state: 'denied' } | { state: 'error'; message: string };
type List = { state: 'loading' } | { state: 'ready' } | { state: 'denied' } | { state: 'error'; message: string };
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'error' };
interface Upload { key: string; name: string; size: number; loaded: number; state: 'uploading' | 'failed' | 'cancelled'; error?: string; abort: AbortController }


/**
 * Documents (docs/files.md): the summary band (files, storage, indexed, failed), the file list with
 * search, sort, type and index-state filters and keyset paging next to a preview pane (preview,
 * rename, re-index, share link, download and delete), and uploads with progress and cancel.
 * "Sources used" on a run opens a file here by id (`focusFileId`).
 */
export function FilesView({ focusFileId, onFocusChange }: { focusFileId: string | null; onFocusChange(fileId: string | null): void }) {
  const picker = useRef<HTMLInputElement>(null);
  const searchId = useId();
  const wide = useMediaQuery(SPLIT_QUERY);
  const [items, setItems] = useState<ApiFile[]>([]);
  const [list, setList] = useState<List>({ state: 'loading' });
  const [more, setMore] = useState(false), [paging, setPaging] = useState(false);
  const [usage, setUsage] = useState<Load<ApiFileUsage>>({ state: 'loading' });
  const [index, setIndex] = useState<Load<RetrievalIndexStatus>>({ state: 'loading' });
  const [draft, setDraft] = useState(''), [query, setQuery] = useState(''), [sort, setSort] = useState<SortValue>('created_at:desc');
  const [typeFilter, setTypeFilter] = useState<FileTypeFilter>(''), [indexFilter, setIndexFilter] = useState<FileIndexFilter>('');
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [note, setNote] = useState<string | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  // Moves focus into the preview pane only when a person picked the file (not the default pick).
  const [picked, setPicked] = useState(() => (focusFileId ? 1 : 0));
  // A file opened by link ("Sources used") counts as the pick: closing it never reopens the first file.
  const autoPicked = useRef(Boolean(focusFileId));
  const me = api.currentUserId();

  const loadUsage = useCallback(() => api.getFileUsage().then((value) => setUsage({ state: 'ready', value }), () => setUsage({ state: 'error' })), []);
  const loadIndex = useCallback(() => api.getRetrievalStatus().then((value) => setIndex({ state: 'ready', value }), () => setIndex({ state: 'error' })), []);
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
  useEffect(() => { void loadUsage(); void loadIndex(); }, [loadUsage, loadIndex]);

  const shown = filterFiles(items, { type: typeFilter, index: indexFilter });
  // On a wide screen the preview pane opens on the first file, once.
  useEffect(() => {
    if (!wide || autoPicked.current || focusFileId || list.state !== 'ready' || !shown[0]) return;
    autoPicked.current = true;
    onFocusChange(shown[0].id);
  }, [wide, focusFileId, list.state, shown, onFocusChange]);

  async function loadMore() {
    const last = items.at(-1);
    if (!last) return;
    setPaging(true);
    try {
      const page = await api.getFiles({ query, sort, cursor: last.id });
      setItems((current) => mergePage(current, page)); setMore(page.length === PAGE_SIZE);
    } catch (e) {
      // A cursor whose file was deleted meanwhile: start the list again.
      if (e instanceof ApiError && e.status === 400) { setNote(t('files.listChanged')); await load(); }
      else setProblem(t('files.loadMoreFailed', { message: describeError(e) }));
    } finally { setPaging(false); }
  }

  async function startUploads(files: File[]) {
    setNote(null); setProblem(null);
    // The limits decide what is refused before sending; read them if they have not arrived yet.
    let limits = usage.state === 'ready' ? usage.value : await api.getFileUsage().catch(() => null);
    for (const file of files) {
      const refusal = uploadProblem(file, limits);
      const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`;
      if (refusal) { setUploads((u) => [...u, { key, name: file.name, size: file.size, loaded: 0, state: 'failed', error: refusal, abort: new AbortController() }]); continue; }
      const abort = new AbortController();
      setUploads((u) => [...u, { key, name: file.name, size: file.size, loaded: 0, state: 'uploading', abort }]);
      try {
        const created = await api.uploadFile(file, (loaded) => setUploads((u) => u.map((x) => x.key === key ? { ...x, loaded } : x)), abort.signal);
        setUploads((u) => u.filter((x) => x.key !== key));
        setNote(t('files.uploaded', { name: created.name, detail: indexBadge(created.index).detail }));
        if (limits) limits = { ...limits, used_bytes: limits.used_bytes + created.size_bytes, file_count: limits.file_count + 1 };
        await Promise.all([load(), loadUsage(), loadIndex()]);
      } catch (e) {
        const cancelled = e instanceof DOMException && e.name === 'AbortError';
        setUploads((u) => u.map((x) => x.key === key ? { ...x, state: cancelled ? 'cancelled' : 'failed', error: cancelled ? t('files.uploadCancelled') : uploadError(e) } : x));
      }
    }
  }

  async function remove(file: ApiFile) {
    await api.deleteFile(file.id);
    setConfirming(null);
    setNote(t('files.deleted', { name: file.name }));
    if (focusFileId === file.id) onFocusChange(null);
    await Promise.all([load(), loadUsage(), loadIndex()]);
  }

  function replaceFile(file: ApiFile) { setItems((current) => current.map((x) => x.id === file.id ? file : x)); void loadIndex(); }
  function open(id: string) { setPicked((n) => n + 1); onFocusChange(id); }

  const usageValue = usage.state === 'ready' ? usage.value : null;
  const canUpload = usageValue?.can_upload ?? true;
  const meter = usageValue ? usageMeter(usageValue) : null;
  const searching = query.trim().length > 0;
  const filtered = Boolean(typeFilter || indexFilter);
  const counts = index.state === 'ready' ? indexCounts(index.value.sources, 'file') : null;
  const submitSearch = (event: FormEvent) => { event.preventDefault(); setQuery(draft.trim()); };
  const clearAll = () => { setDraft(''); setQuery(''); setTypeFilter(''); setIndexFilter(''); };
  const figures = [
    figure('files', t('pages.files.fig.files'), usage, () => ({ value: formatNumber(usageValue!.file_count), hint: t('pages.files.fig.filesHint', { size: formatBytes(usageValue!.max_upload_bytes) }) })),
    figure('storage', t('pages.files.fig.storage'), usage, () => ({ value: formatBytes(usageValue!.used_bytes), hint: t('pages.files.fig.storageHint', { quota: formatBytes(usageValue!.quota_bytes), percent: Math.round(meter!.percent) }), tone: meter!.tone === 'stop' ? 'danger' : meter!.tone === 'warn' ? 'warn' : '' })),
    figure('indexed', t('pages.files.fig.indexed'), index, () => ({ value: formatNumber(counts!.indexed), hint: t('pages.files.fig.indexedHint', { count: counts!.skipped }), tone: counts!.indexed ? 'ok' : '' })),
    figure('failed', t('pages.files.fig.failed'), index, () => ({ value: formatNumber(counts!.failed), hint: counts!.failed ? t('pages.files.fig.failedHint') : t('pages.files.fig.noneFailed'), tone: counts!.failed ? 'danger' : '' })),
  ];

  return <>
    <PageHeader title={viewLabel('files')} subtitle={t('files.intro.text')} actions={<>
      <button type="button" className="uploadButton" disabled={!canUpload} aria-describedby={!canUpload ? 'files-upload-denied' : undefined} onClick={() => picker.current?.click()}><Upload aria-hidden="true" />{t('files.upload')}</button>
    </>} />
    <input ref={picker} type="file" multiple hidden aria-label={t('files.uploadInput')} accept={usageValue?.accepted_extensions.join(',')} onChange={(e) => { const chosen = [...(e.target.files ?? [])]; e.target.value = ''; if (chosen.length) void startUploads(chosen); }} />
    {!canUpload && <p className="notice" id="files-upload-denied" role="status"><Lock aria-hidden="true" />{t('files.uploadDeniedNote')}</p>}
    {list.state !== 'denied' && <PageSummary label={t('pages.files.summary')} figures={figures} onRetry={() => { setUsage({ state: 'loading' }); setIndex({ state: 'loading' }); void loadUsage(); void loadIndex(); }}
      footer={usageValue && meter ? <div className="filesUsageLine">
        <div className={`meterTrack ${meter.tone === 'ok' ? '' : meter.tone}`} role="progressbar" aria-label={t('files.storageMeter')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(meter.percent)} aria-valuetext={meter.text}><span style={{ width: `${meter.percent}%` }} /></div>
        <small>{t('files.limits', { size: formatBytes(usageValue.max_upload_bytes), accepted: usageValue.accepted })}</small>
      </div> : undefined} />}
    {uploads.length > 0 && <section className="surface uploadQueue" aria-label={t('files.uploads')}>
      {uploads.map((u) => <div className="uploadItem" key={u.key}>
        <div className="uploadItemTop"><strong dir="auto">{u.name}</strong>
          {u.state === 'uploading'
            ? <button type="button" className="secondary" aria-label={t('files.cancelUploadOf', { name: u.name })} onClick={() => u.abort.abort()}>{t('common.cancel')}</button>
            : <button type="button" className="iconButton" aria-label={t('files.dismissOf', { name: u.name })} title={t('files.dismiss')} onClick={() => setUploads((all) => all.filter((x) => x.key !== u.key))}><X aria-hidden="true" /></button>}
        </div>
        {u.state === 'uploading'
          ? <><div className="meterTrack" role="progressbar" aria-label={t('files.uploadingOf', { name: u.name })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={u.size ? Math.round((u.loaded / u.size) * 100) : 0}><span style={{ width: `${u.size ? Math.min(100, (u.loaded / u.size) * 100) : 0}%` }} /></div><small className="muted" aria-live="polite">{progressText(u.loaded, u.size)}</small></>
          : <p className={`notice ${u.state === 'failed' ? 'errorNotice' : ''}`} role={u.state === 'failed' ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{u.error}</p>}
      </div>)}
    </section>}
    {note && <Notice>{note}</Notice>}
    {problem && <Notice error>{problem}</Notice>}
    {list.state === 'denied' && <section className="surface emptyState" aria-labelledby="files-denied"><Lock aria-hidden="true" /><h3 id="files-denied">{t('pages.files.deniedTitle')}</h3><p>{t('files.readDenied')}</p></section>}
    {list.state !== 'denied' && <SplitView className="filesSplit" show={focusFileId ? 'detail' : 'list'}>
      <section data-pane="list" className="pane filesListPane" aria-label={t('files.list')} aria-busy={list.state === 'loading'}>
        <form className="paneHead toolbar filesToolbar" role="search" onSubmit={submitSearch}>
          <label className="visuallyHidden" htmlFor={`${searchId}-q`}>{t('files.searchLabel')}</label>
          <input id={`${searchId}-q`} type="search" required value={draft} placeholder={t('files.searchLabel')} maxLength={255} onChange={(e) => { setDraft(e.target.value); if (!e.target.value) setQuery(''); }} />
          <button type="submit" className="secondary iconOnly" aria-label={t('common.search')} title={t('common.search')}><Search aria-hidden="true" /></button>
          <label className="visuallyHidden" htmlFor={`${searchId}-sort`}>{t('files.sortLabel')}</label>
          <select id={`${searchId}-sort`} value={sort} onChange={(e) => setSort(e.target.value as SortValue)}>{sortOptions().map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
          <label className="visuallyHidden" htmlFor={`${searchId}-type`}>{t('pages.files.typeFilter')}</label>
          <select id={`${searchId}-type`} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as FileTypeFilter)}><option value="">{t('pages.files.anyType')}</option>{FILE_TYPE_FILTERS.map((value) => <option key={value} value={value}>{t(`pages.files.type.${value}` as MessageKey)}</option>)}</select>
          <label className="visuallyHidden" htmlFor={`${searchId}-index`}>{t('pages.files.indexFilter')}</label>
          <select id={`${searchId}-index`} value={indexFilter} onChange={(e) => setIndexFilter(e.target.value as FileIndexFilter)}><option value="">{t('pages.files.anyIndex')}</option>{FILE_INDEX_FILTERS.map((value) => <option key={value} value={value}>{t(`pages.files.index.${value}` as MessageKey)}</option>)}</select>
          {filtered && <p className="toolbarNote">{t('pages.files.filterNote', { shown: shown.length, loaded: items.length })}</p>}
        </form>
        {list.state === 'error' && <div className="paneBody"><div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('files.loadFailed', { message: list.message })}</span><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div></div>}
        {list.state === 'loading' && <div className="paneBody"><Notice>{t('files.loading')}</Notice></div>}
        {shown.length > 0 && <div className="dataTableWrap"><table className="dataTable filesTable">
          <caption className="visuallyHidden">{t('files.list')}</caption>
          <thead><tr><th scope="col">{t('pages.files.col.name')}</th><th scope="col" className="num">{t('pages.files.col.size')}</th><th scope="col">{t('pages.files.col.index')}</th><th scope="col"><span className="visuallyHidden">{t('pages.files.col.actions')}</span></th></tr></thead>
          <tbody>{shown.map((x) => {
            const badge = fileBadge(x);
            return <tr key={x.id} className={x.id === focusFileId ? 'selected' : undefined}>
              <th scope="row"><button type="button" className="cellButton" aria-label={t('files.showDetails', { name: x.name })} aria-current={x.id === focusFileId ? 'true' : undefined} onClick={() => open(x.id)}>
                <strong dir="auto">{x.kind === 'image' ? <ImageIcon aria-hidden="true" className="cellIcon" /> : <FileText aria-hidden="true" className="cellIcon" />}{x.name}</strong>
                <small>{fileTypeLabel(x.content_type)} · {uploaderLabel(x.created_by, me)} · <time dateTime={x.created_at}>{formatDateTime(x.created_at, { dateStyle: 'medium' })}</time></small>
              </button></th>
              <td className="num muted"><bdi>{formatBytes(x.size_bytes)}</bdi></td>
              <td><span className={`statusBadge ${badge.tone}`} title={badge.detail}>{badge.label}</span></td>
              <td><div className="cellActions">
                {confirming === x.id
                  ? <><span className="visuallyHidden" role="status">{t('files.confirmDeletePrompt', { name: x.name })}</span><button type="button" className="danger" aria-label={t('files.confirmDeleteOf', { name: x.name })} onClick={() => void remove(x).catch((e) => { setConfirming(null); setProblem(isPermissionDenied(e) ? t('files.manageNote') : t('files.deleteFailed', { name: x.name, message: describeError(e) })); })}>{t('common.delete')}</button><button type="button" className="secondary" aria-label={t('files.keepOf', { name: x.name })} onClick={() => setConfirming(null)}>{t('files.keep')}</button></>
                  : <>
                    {!isQuarantined(x) && <button type="button" className="iconButton" title={t('files.download')} aria-label={t('files.downloadOf', { name: x.name })} onClick={() => void api.downloadFile(x.id, x.name).catch((e) => setProblem(t('files.downloadFailed', { name: x.name, message: describeError(e) })))}><Download aria-hidden="true" /></button>}
                    {x.can_manage && <button type="button" className="iconButton" title={t('common.delete')} aria-label={t('files.deleteOf', { name: x.name })} onClick={() => setConfirming(x.id)}><Trash2 aria-hidden="true" /></button>}
                  </>}
              </div></td>
            </tr>;
          })}</tbody>
        </table></div>}
        {more && items.length > 0 && <div className="paneBody filesMore"><button type="button" className="secondary" onClick={() => void loadMore()} disabled={paging}>{paging ? t('common.loading') : t('files.loadMore')}</button></div>}
        {list.state === 'ready' && !shown.length && (searching || filtered
          ? <div className="paneEmpty"><Search aria-hidden="true" /><h3>{searching ? t('files.noMatch', { query }) : t('pages.files.noFilterMatch')}</h3><p>{t('files.noMatchText')}</p><div className="actions"><button type="button" className="secondary" onClick={clearAll}>{t('files.clearSearch')}</button></div></div>
          : <div className="paneEmpty"><Upload aria-hidden="true" /><h3>{t('files.empty')}</h3><p>{canUpload ? t('files.emptyUpload') : t('files.emptyReadOnly')}</p><HelpLink />{canUpload && <div className="actions"><button type="button" onClick={() => picker.current?.click()}><Upload aria-hidden="true" />{t('files.uploadFirst')}</button></div>}</div>)}
      </section>
      <section data-pane="detail" className="pane filesDetailPane" aria-label={focusFileId ? undefined : t('files.detail.title')}>
        {focusFileId
          ? <FileDetail key={focusFileId} fileId={focusFileId} me={me} canUpload={canUpload} linkTtl={usageValue?.link_ttl_seconds ?? null} focusSignal={picked} onClose={() => onFocusChange(null)} onDelete={remove} onChange={replaceFile} />
          : <div className="paneEmpty"><FileText aria-hidden="true" /><h3>{t('pages.files.pickTitle')}</h3><p>{items.length ? t('pages.files.pickText') : t('pages.files.pickEmpty')}</p></div>}
      </section>
    </SplitView>}
  </>;
}

function uploadError(e: unknown): string {
  if (isPermissionDenied(e)) return t('files.uploadRoleDenied');
  if (e instanceof ApiError && e.status === 413 && !e.detail) return t('files.uploadTooLargeServer');
  return describeError(e);
}

function FileDetail({ fileId, me, canUpload, linkTtl, focusSignal, onClose, onDelete, onChange }: {
  fileId: string; me: string | null; canUpload: boolean; linkTtl: number | null; focusSignal: number; onClose(): void; onDelete(file: ApiFile): Promise<void>; onChange(file: ApiFile): void;
}) {
  const [detail, setDetail] = useState<Detail>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0), [confirming, setConfirming] = useState(false), [busy, setBusy] = useState<null | 'delete' | 'rename' | 'reindex' | 'link'>(null);
  const [actionError, setActionError] = useState<string | null>(null), [actionNote, setActionNote] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [link, setLink] = useState<ApiFileLink | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const renameId = useId();
  const linkId = useId();
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
  // Move focus to the pane once it has something to say, when a person opened it (not the default pick).
  useEffect(() => {
    if (detail.state === 'loading' || focusSignal === 0) return;
    heading.current?.scrollIntoView({ block: 'nearest' });
    heading.current?.focus({ preventScroll: true });
  }, [detail.state, focusSignal]);

  const update = (file: ApiFile) => { setDetail({ state: 'ready', file }); onChange(file); };
  async function act(kind: 'rename' | 'reindex', work: () => Promise<ApiFile>, done: (file: ApiFile) => string) {
    setBusy(kind); setActionError(null); setActionNote(null);
    try { const file = await work(); update(file); setActionNote(done(file)); setRenaming(null); }
    catch (e) { setActionError(isPermissionDenied(e) ? (kind === 'rename' ? t('files.manageNote') : t('files.detail.reindexDenied')) : describeError(e)); }
    finally { setBusy(null); }
  }
  async function share(file: ApiFile) {
    setBusy('link'); setActionError(null); setActionNote(null);
    try { setLink(await api.createFileLink(file.id, 'attachment')); }
    catch (e) { setActionError(isPermissionDenied(e) ? t('pages.files.share.denied') : t('pages.files.share.failed', { message: describeError(e) })); }
    finally { setBusy(null); }
  }

  const title = detail.state === 'ready' ? detail.file.name : t('files.detail.title');
  const file = detail.state === 'ready' ? detail.file : null;
  const badge = file ? indexBadge(file.index) : null;
  const renameIssue = file && renaming !== null ? renameProblem(file.name, renaming) : null;
  return (
    <section className="fileDetail" aria-labelledby="file-detail-heading" aria-busy={detail.state === 'loading'}>
      <div className="paneHead">
        <h2 id="file-detail-heading" ref={heading} tabIndex={-1} dir="auto">{title}</h2>
        <button type="button" className="iconButton small" aria-label={t('files.detail.close')} title={t('files.detail.close')} onClick={onClose}><X aria-hidden="true" /></button>
      </div>
      <div className="paneBody">
        {detail.state === 'loading' && <Notice>{t('files.detail.loading')}</Notice>}
        {detail.state === 'missing' && <Notice error>{rich('files.detail.missing', { id: <code dir="ltr">{fileId}</code> })}</Notice>}
        {detail.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{t('files.readDenied')}</p>}
        {detail.state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('files.detail.loadFailed', { message: detail.message })}</span><button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>}
        {file && badge && <>
          <dl className="taskMeta">
            <div><dt>{t('files.detail.kind')}</dt><dd>{fileTypeLabel(file.content_type)} <small className="muted">({file.content_type})</small></dd></div>
            <div><dt>{t('files.detail.size')}</dt><dd><bdi>{formatBytes(file.size_bytes)}</bdi></dd></div>
            <div><dt>{t('files.detail.uploaded')}</dt><dd><time dateTime={file.created_at}>{date(file.created_at)}</time></dd></div>
            <div><dt>{t('files.detail.uploadedBy')}</dt><dd>{file.created_by === me ? t('files.you') : <code dir="ltr">{file.created_by}</code>}</dd></div>
            {file.updated_at && file.updated_by && <div><dt>{t('files.detail.renamed')}</dt><dd>{rich('files.detail.renamedBy', { when: <time dateTime={file.updated_at}>{date(file.updated_at)}</time>, who: file.updated_by === me ? t('files.you') : <code dir="ltr">{file.updated_by}</code> })}</dd></div>}
            <div className="wideMeta"><dt>{t('files.detail.retrieval')}</dt><dd><span className={`statusBadge ${badge.tone}`}>{badge.label}</span> <small className="muted">{badge.detail}</small></dd></div>
            <div><dt>{t('files.detail.fileId')}</dt><dd><code dir="ltr">{file.id}</code></dd></div>
            <div className="wideMeta"><dt>{t('files.detail.sha')}</dt><dd><code dir="ltr">{file.sha256}</code></dd></div>
          </dl>
          {isQuarantined(file) && <Notice error>{t('files.quarantinedDetail', { signature: file.scan_signature || t('files.quarantinedUnknown') })}</Notice>}
          {actionError && <Notice error>{actionError}</Notice>}
          {actionNote && <Notice>{actionNote}</Notice>}
          {renaming !== null
            ? <form className="renameForm" onSubmit={(e) => { e.preventDefault(); if (!renameIssue) void act('rename', () => api.renameFile(file.id, renaming.trim()), (f) => t('files.detail.renamedTo', { name: f.name })); }}>
              <label className="field"><span>{t('files.detail.renameField')}</span><input id={renameId} value={renaming} maxLength={255} autoFocus aria-invalid={Boolean(renameIssue)} aria-describedby={`${renameId}-hint`} onChange={(e) => setRenaming(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') setRenaming(null); }} /></label>
              <small id={`${renameId}-hint`} className={renameIssue ? 'fieldError' : 'muted'}>{renameIssue ?? t('files.detail.renameHint')}</small>
              <div className="actions"><button type="submit" disabled={Boolean(renameIssue) || busy !== null}>{busy === 'rename' ? t('files.detail.saving') : t('files.detail.saveName')}</button><button type="button" className="secondary" onClick={() => setRenaming(null)}>{t('common.cancel')}</button></div>
            </form>
            : <div className="actions fileActions">
              {!isQuarantined(file) && <><button type="button" onClick={() => { setActionError(null); void api.downloadFile(file.id, file.name).catch((e) => setActionError(t('files.detail.downloadFailed', { message: describeError(e) }))); }}><Download aria-hidden="true" />{t('files.download')}</button>
              <button type="button" className="secondary" disabled={busy !== null} onClick={() => void share(file)}><Link2 aria-hidden="true" />{busy === 'link' ? t('pages.files.share.creating') : link ? t('pages.files.share.again') : t('pages.files.share.create')}</button></>}
              {file.can_manage && <button type="button" className="secondary" onClick={() => { setRenaming(file.name); setConfirming(false); }}><Pencil aria-hidden="true" />{t('files.detail.rename')}</button>}
              {canUpload && !isQuarantined(file) && canReindex(file.index) && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void act('reindex', () => api.reindexFile(file.id), (f) => indexBadge(f.index).detail)}><RefreshCw aria-hidden="true" />{busy === 'reindex' ? t('files.detail.reindexing') : t('files.detail.reindex')}</button>}
              {file.can_manage && (confirming
                ? <><button type="button" className="danger" disabled={busy !== null} onClick={() => { setBusy('delete'); setActionError(null); void onDelete(file).catch((e) => { setActionError(isPermissionDenied(e) ? t('files.manageNote') : describeError(e)); setBusy(null); }); }} aria-label={t('files.confirmDeleteOf', { name: file.name })}>{busy === 'delete' ? t('files.detail.deleting') : t('files.detail.deletePermanently')}</button><button type="button" className="secondary" onClick={() => setConfirming(false)}>{t('files.keep')}</button></>
                : <button type="button" className="secondary dangerText" onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />{t('files.detail.delete')}</button>)}
            </div>}
          {link && <div className="fileShare" role="group" aria-labelledby={`${linkId}-label`}>
            <label id={`${linkId}-label`} htmlFor={linkId}>{t('pages.files.share.label')}</label>
            <div className="fileShareRow"><input id={linkId} readOnly dir="ltr" value={link.url} onFocus={(e) => e.currentTarget.select()} /><button type="button" className="secondary" onClick={() => void copyText(link.url).then((ok) => setActionNote(ok ? t('pages.files.share.copied') : t('pages.files.share.copyFailed')))}><Copy aria-hidden="true" />{t('pages.files.share.copy')}</button></div>
            <small className="muted">{t('pages.files.share.expires', { when: formatDateTime(link.expires_at, { timeStyle: 'short' }), minutes: Math.max(1, Math.round((linkTtl ?? 300) / 60)) })}</small>
          </div>}
          {!file.can_manage && <p className="muted filesNote"><Lock aria-hidden="true" />{t('files.manageNote')}</p>}
          <FilePreview key={`${file.id}-${file.sha256}`} file={file} />
        </>}
      </div>
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

  if (!mode) return <section className="filePreview" aria-label={t('files.preview.title')}><h3>{t('files.preview.title')}</h3><p className="muted">{t('files.preview.none', { type: fileTypeLabel(file.content_type) })}</p></section>;
  const failed = (text.state === 'error' && (mode === 'text' || mode === 'markdown')) || (bytes.state === 'error' && (mode === 'image' || mode === 'pdf'));
  const failure = text.state === 'error' ? text.message : bytes.state === 'error' ? bytes.message : '';
  return (
    <section className="filePreview" aria-label={t('files.preview.title')}>
      <div className="filePreviewHead"><h3>{t('files.preview.title')}</h3>{mode === 'markdown' && text.state === 'ready' && <span className="actions"><button type="button" className="secondary" aria-pressed={source} onClick={() => setSource((v) => !v)}>{source ? t('files.preview.showFormatted') : t('files.preview.showSource')}</button></span>}</div>
      {failed && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('files.preview.failed', { message: failure })}</span><button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>}
      {!failed && (mode === 'text' || mode === 'markdown') && text.state === 'loading' && <Notice>{t('files.preview.loading')}</Notice>}
      {!failed && (mode === 'image' || mode === 'pdf') && bytes.state === 'loading' && <Notice>{t('files.preview.loading')}</Notice>}
      {text.state === 'ready' && (mode === 'text' || (mode === 'markdown' && source)) && <pre className="filePreviewText" tabIndex={0} aria-label={t('files.preview.textOf', { name: file.name })} dir="auto">{text.value.text}</pre>}
      {text.state === 'ready' && mode === 'markdown' && !source && <div className="filePreviewDoc" tabIndex={0} aria-label={t('files.preview.formattedOf', { name: file.name })} dir="auto"><MarkdownBlocks blocks={parseMarkdown(text.value.text)} /></div>}
      {text.state === 'ready' && text.value.truncated && <p className="muted">{t('files.preview.truncated')}</p>}
      {mode === 'image' && imageUrl && <img className="filePreviewImage" src={imageUrl} alt={t('files.preview.imageOf', { name: file.name })} />}
      {mode === 'pdf' && pdf && <Suspense fallback={<Notice>{t('files.pdf.opening')}</Notice>}><PdfPreview data={pdf} name={file.name} /></Suspense>}
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
const date = (v: string) => formatDateTime(v);
