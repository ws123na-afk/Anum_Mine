import { useCallback, useEffect, useId, useState } from 'react';
import { CircleDot, Lock, RefreshCw, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { bytesLeft, formatBytes, kindLabel, usageMeter, type ApiStorage } from './lib/files';
import { formatNumber, t } from './lib/i18n';

type Load = { state: 'loading' } | { state: 'ready'; value: ApiStorage } | { state: 'denied' } | { state: 'error'; message: string };

/**
 * The per-workspace storage screen (docs/files.md#storage-screen): usage against the quota, what
 * older versions take, the split by kind and the files that take the most space. The limit is the
 * operator's; the server enforces it on every upload and new version with a clear error.
 */
export function FileStorage({ refreshKey, onOpenFile, onClose }: { refreshKey: number; onOpenFile(fileId: string): void; onClose(): void }) {
  const headingId = useId();
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const reload = useCallback(async () => {
    setLoad((value) => value.state === 'ready' ? value : { state: 'loading' });
    try { setLoad({ state: 'ready', value: await api.getFileStorage() }); }
    catch (e) { setLoad(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', message: describeError(e) }); }
  }, []);
  useEffect(() => { void reload(); }, [reload, refreshKey]);

  const report = load.state === 'ready' ? load.value : null;
  const meter = report ? usageMeter(report.usage) : null;
  const empty = report !== null && report.usage.file_count === 0;
  return <section className="surface fileStorage" aria-labelledby={headingId}>
    <div className="fileStorageHead">
      <h2 id={headingId}>{t('files.stor.title')}</h2>
      <button type="button" className="iconButton small" aria-label={t('files.stor.close')} title={t('files.stor.close')} onClick={onClose}><X aria-hidden="true" /></button>
    </div>
    {load.state === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('files.stor.loading')}</p>}
    {load.state === 'denied' && <p className="notice" role="status"><Lock aria-hidden="true" />{t('files.stor.denied')}</p>}
    {load.state === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('files.stor.failed', { message: load.message })}</span>
      <button type="button" className="secondary" onClick={() => void reload()}><RefreshCw aria-hidden="true" />{t('files.stor.retry')}</button></div>}
    {report && meter && <>
      <div className="fileStorageUse">
        <strong>{t('files.stor.used', { used: formatBytes(report.usage.used_bytes), quota: formatBytes(report.usage.quota_bytes) })}</strong>
        <span className="muted">{t('files.stor.left', { left: formatBytes(bytesLeft(report.usage)) })}</span>
      </div>
      <div className={`meterTrack ${meter.tone === 'ok' ? '' : meter.tone}`} role="progressbar" aria-label={t('files.storageMeter')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(meter.percent)} aria-valuetext={meter.text}><span style={{ width: `${meter.percent}%` }} /></div>
      {(report.usage.version_count ?? 0) > 0 && <p className="muted">{t('files.stor.versions', { size: formatBytes(report.usage.version_bytes ?? 0), count: report.usage.version_count ?? 0 })}</p>}
      {meter.tone === 'stop' && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{meter.percent >= 100 ? t('files.stor.full') : t('files.stor.nearLimit')}</p>}
      {meter.tone === 'warn' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('files.stor.nearLimit')}</p>}
      <p className="muted"><small>{t('files.stor.limitNote')}</small></p>
      {empty && <div className="fileVersionsEmpty"><strong>{t('files.stor.empty')}</strong><p className="muted">{t('files.stor.emptyText')}</p></div>}
      {!empty && <div className="fileStorageGrid">
        <div className="dataTableWrap"><table className="dataTable">
          <caption>{t('files.stor.byKind')}</caption>
          <thead><tr><th scope="col">{t('files.stor.colKind')}</th><th scope="col" className="num">{t('files.stor.colFiles')}</th><th scope="col" className="num">{t('files.stor.colSize')}</th></tr></thead>
          <tbody>{report.by_kind.map((row) => <tr key={row.kind}><th scope="row">{kindLabel(row.kind)}</th><td className="num">{formatNumber(row.file_count)}</td><td className="num"><bdi>{formatBytes(row.bytes)}</bdi></td></tr>)}</tbody>
        </table></div>
        <div className="dataTableWrap"><table className="dataTable">
          <caption>{t('files.stor.largest')}</caption>
          <thead><tr><th scope="col">{t('files.stor.colFile')}</th><th scope="col" className="num">{t('files.stor.colVersions')}</th><th scope="col" className="num">{t('files.stor.colTotal')}</th></tr></thead>
          <tbody>{report.largest.map((row) => <tr key={row.file.id}>
            <th scope="row"><button type="button" className="cellButton" aria-label={t('files.stor.openFile', { name: row.file.name })} onClick={() => onOpenFile(row.file.id)}><strong dir="auto">{row.file.name}</strong></button></th>
            <td className="num"><bdi>{row.version_count ? t('files.stor.versionsCell', { count: row.version_count, size: formatBytes(row.version_bytes) }) : t('files.stor.versionsCell', { count: 0, size: '' })}</bdi></td>
            <td className="num"><bdi>{formatBytes(row.total_bytes)}</bdi></td>
          </tr>)}</tbody>
        </table></div>
        <p className="muted"><small>{t('files.stor.largestHelp')}</small></p>
      </div>}
    </>}
  </section>;
}
