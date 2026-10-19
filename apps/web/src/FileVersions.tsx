import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { CircleDot, Download, Lock, RefreshCw, RotateCcw, Trash2, Upload } from 'lucide-react';
import * as api from './lib/api';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatBytes, mergeVersions, uploaderLabel, versionProblem, type ApiFile, type ApiFileUsage, type ApiFileVersion } from './lib/files';
import { formatDateTime, t } from './lib/i18n';

type Load = { state: 'loading' } | { state: 'ready' } | { state: 'denied' } | { state: 'error'; message: string };

/**
 * The versions of one file (docs/files.md#versions): the current version, a button to upload a new
 * one (the current bytes become an older version), and the older versions with download, restore
 * and delete. `onChange` hands the updated file to the detail pane; `onUsage` refreshes the
 * storage figures, because every version counts against the workspace quota.
 */
export function FileVersions({ file, me, usage, onChange, onUsage }: {
  file: ApiFile; me: string | null; usage: ApiFileUsage | null; onChange(file: ApiFile): void; onUsage(): void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const headingId = useId();
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [items, setItems] = useState<ApiFileVersion[]>([]), [total, setTotal] = useState(0);
  const [busy, setBusy] = useState<string | null>(null), [paging, setPaging] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const current = file.version ?? 1;
  const canChange = file.can_manage === true;

  const reload = useCallback(async () => {
    setLoad((value) => value.state === 'ready' ? value : { state: 'loading' });
    try {
      const page = await api.getFileVersions(file.id, 0);
      setItems(page.items); setTotal(page.total); setLoad({ state: 'ready' });
    } catch (e) {
      setLoad(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) });
    }
  }, [file.id]);
  useEffect(() => { void reload(); }, [reload, current]);

  async function more() {
    setPaging(true); setError(null);
    try {
      const page = await api.getFileVersions(file.id, items.length);
      setItems((all) => mergeVersions(all, page.items)); setTotal(page.total);
    } catch (e) { setError(t('files.ver.loadMoreFailed', { message: describeError(e) })); }
    finally { setPaging(false); }
  }

  async function run(key: string, work: () => Promise<string>, failed: (message: string) => string) {
    setBusy(key); setError(null); setNote(null);
    try { setNote(await work()); onUsage(); await reload(); }
    catch (e) {
      setError(isPermissionDenied(e) ? t('files.ver.manageDenied') : failed(describeError(e)));
      if (e instanceof ApiError && e.status === 409) await reload();
    } finally { setBusy(null); }
  }

  function chosen(files: File[]) {
    const [picked] = files;
    if (!picked) return;
    const refusal = versionProblem(picked, file, usage);
    if (refusal) { setError(refusal); setNote(null); return; }
    void run('upload', async () => {
      const updated = await api.uploadFileVersion(file.id, picked);
      onChange(updated);
      return t('files.ver.uploaded', { version: updated.version ?? current + 1, name: updated.name });
    }, (message) => t('files.ver.uploadFailed', { name: file.name, message }));
  }

  const restore = (item: ApiFileVersion) => run(`restore-${item.id}`, async () => {
    const updated = await api.restoreFileVersion(file.id, item.id);
    onChange(updated);
    return t('files.ver.restored', { version: item.version, now: updated.version ?? current + 1 });
  }, (message) => t('files.ver.restoreFailed', { version: item.version, message }));

  const remove = (item: ApiFileVersion) => run(`delete-${item.id}`, async () => {
    await api.deleteFileVersion(file.id, item.id);
    setConfirming(null);
    return t('files.ver.deleted', { version: item.version });
  }, (message) => { setConfirming(null); return t('files.ver.deleteFailed', { version: item.version, message }); });

  const download = (item: ApiFileVersion) => run(`download-${item.id}`, async () => {
    await api.downloadFileVersion(file.id, item.id, file.name);
    return '';
  }, (message) => t('files.ver.downloadFailed', { version: item.version, message }));

  return <section className="fileVersions" aria-labelledby={headingId}>
    <div className="fileVersionsHead">
      <h3 id={headingId}>{t('files.ver.title')}</h3>
      <span className="statusBadge neutral">{t('files.ver.current', { version: current })}</span>
      {canChange && <>
        <input ref={picker} type="file" hidden aria-label={t('files.ver.uploadInput', { name: file.name })}
          accept={file.name.includes('.') ? file.name.slice(file.name.lastIndexOf('.')) : undefined}
          onChange={(e) => { const picked = [...(e.target.files ?? [])]; e.target.value = ''; chosen(picked); }} />
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => picker.current?.click()}>
          <Upload aria-hidden="true" />{busy === 'upload' ? t('files.ver.uploading') : t('files.ver.upload')}
        </button>
      </>}
    </div>
    <p className="muted">{t('files.ver.help')}</p>
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
    {note && <p className="notice" role="status"><CircleDot aria-hidden="true" />{note}</p>}
    {load.state === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('files.ver.loading')}</p>}
    {load.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{t('files.ver.denied')}</p>}
    {load.state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('files.ver.loadFailed', { message: load.message })}</span>
      <button type="button" className="secondary" onClick={() => void reload()}><RefreshCw aria-hidden="true" />{t('common.retry')}</button></div>}
    {load.state === 'ready' && items.length === 0 && <div className="fileVersionsEmpty">
      <strong>{t('files.ver.empty')}</strong>
      <p className="muted">{canChange ? t('files.ver.emptyText', { version: current }) : t('files.ver.emptyReadOnly')}</p>
    </div>}
    {load.state === 'ready' && items.length > 0 && <ul className="fileVersionList">
      {items.map((item) => <li key={item.id}>
        <div className="fileVersionInfo">
          <strong>{t('files.ver.row', { version: item.version })}</strong>
          <small className="muted">{t('files.ver.meta', {
            size: formatBytes(item.size_bytes), uploaded: formatDateTime(item.created_at, { dateStyle: 'medium', timeStyle: 'short' }),
            who: uploaderLabel(item.created_by, me), replaced: formatDateTime(item.replaced_at, { dateStyle: 'medium', timeStyle: 'short' }),
          })}</small>
          {!item.downloadable && <small className="fieldError">{t('files.ver.quarantined', { signature: item.scan_signature || t('files.quarantinedUnknown') })}</small>}
        </div>
        <div className="cellActions">
          {confirming === item.id
            ? <><span className="visuallyHidden" role="status">{t('files.ver.confirmDelete', { version: item.version })}</span>
              <button type="button" className="danger" disabled={busy !== null} aria-label={t('files.ver.confirmDeleteOf', { version: item.version })} onClick={() => void remove(item)}>{t('files.ver.delete')}</button>
              <button type="button" className="secondary" aria-label={t('files.ver.keepOf', { version: item.version })} onClick={() => setConfirming(null)}>{t('files.ver.keep')}</button></>
            : <>
              {item.downloadable && <button type="button" className="iconButton" title={t('files.ver.download')} aria-label={t('files.ver.downloadOf', { version: item.version, name: file.name })} disabled={busy !== null} onClick={() => void download(item)}><Download aria-hidden="true" /></button>}
              {canChange && item.downloadable && <button type="button" className="secondary" aria-label={t('files.ver.restoreOf', { version: item.version, name: file.name })} disabled={busy !== null} onClick={() => void restore(item)}><RotateCcw aria-hidden="true" />{busy === `restore-${item.id}` ? t('files.ver.restoring') : t('files.ver.restore')}</button>}
              {canChange && <button type="button" className="iconButton" title={t('files.ver.delete')} aria-label={t('files.ver.deleteOf', { version: item.version, name: file.name })} disabled={busy !== null} onClick={() => setConfirming(item.id)}><Trash2 aria-hidden="true" /></button>}
            </>}
        </div>
      </li>)}
    </ul>}
    {load.state === 'ready' && items.length < total && <button type="button" className="secondary" disabled={paging} onClick={() => void more()}>
      {paging ? t('files.ver.loadingMore') : t('files.ver.loadMore', { shown: items.length, total })}
    </button>}
    {!canChange && load.state === 'ready' && <p className="muted filesNote"><Lock aria-hidden="true" />{t('files.ver.manageDenied')}</p>}
  </section>;
}
