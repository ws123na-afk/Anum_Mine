// Settings > Workspace: export the organisation's data, schedule or cancel deleting the
// organisation, and schedule or cancel deleting this workspace (docs/organisation-lifecycle.md).
// What the caller may do is the API's answer (GET /organization/lifecycle).
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, CircleDot, Download, FileArchive, Lock, RefreshCw, Trash2, Undo2 } from 'lucide-react';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import {
  archiveSize,
  canRequestExport,
  confirmationMatches,
  coolingOffText,
  isOpenDeletion,
  shouldPoll,
  statusLabel,
  statusTone,
  timeLeftText,
  type LifecycleJob,
  type LifecyclePage,
} from './lib/lifecycle';
import * as api from './lib/lifecycleApi';
import { rich } from './rich';
import { ConfirmDialog } from './Dialog';
import { RecordRetentionCard } from './RecordRetentionCard';
import './admin.css';
import './audit.css';
import './lifecycle.css';

type Load = { kind: 'loading' } | { kind: 'ready'; page: LifecyclePage } | { kind: 'error'; detail: string; denied: boolean };
type Flash = { text: string; error?: boolean } | null;
type Confirming = 'organisation' | 'workspace' | null;

const POLL_MS = 5000;
const when = (value: string) => formatDateTime(value);

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`statusBadge ${statusTone(status)}`}>{statusLabel(status)}</span>;
}

/** A sentence for a failed action: the API's own words, with 403 and 410 explained. */
function failure(error: unknown): string {
  if (isPermissionDenied(error)) return t('lifecycle.notAllowed', { message: describeError(error) });
  if (error instanceof ApiError && error.status === 410) return t('lifecycle.linkGone');
  return describeError(error);
}

/** The data lifecycle card and, next to it, how long records are kept (docs/record-retention.md). */
export function DataLifecycle() {
  return <><LifecycleCard /><RecordRetentionCard /></>;
}

function LifecycleCard() {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [typed, setTyped] = useState('');
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const page = await api.getLifecycle();
      if (mounted.current) setLoad({ kind: 'ready', page });
    } catch (error) {
      if (mounted.current) setLoad((current) => current.kind === 'ready' ? current : { kind: 'error', detail: describeError(error), denied: isPermissionDenied(error) });
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => { mounted.current = false; };
  }, [refresh]);

  const polling = load.kind === 'ready' && shouldPoll(load.page);
  useEffect(() => {
    if (!polling) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, refresh]);

  async function act(key: string, action: () => Promise<string>): Promise<boolean> {
    setBusy(key);
    setFlash(null);
    try {
      const text = await action();
      if (mounted.current) setFlash({ text });
      await refresh();
      return true;
    } catch (error) {
      if (mounted.current) setFlash({ text: failure(error), error: true });
      await refresh();
      return false;
    } finally {
      if (mounted.current) setBusy(null);
    }
  }

  const label = t('lifecycle.title');
  if (load.kind === 'loading') return <section className="surface workspaceAdmin lifecycle" aria-label={label}><Notice>{t('lifecycle.loading')}</Notice></section>;
  if (load.kind === 'error') return <section className="surface workspaceAdmin lifecycle" aria-label={label}>
    <h2>{label}</h2>
    <Notice error>{load.denied ? t('lifecycle.denied', { message: load.detail }) : t('lifecycle.loadError', { message: load.detail })}</Notice>
    <div className="actions"><button type="button" onClick={() => { setLoad({ kind: 'loading' }); void refresh(); }}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>
  </section>;

  const { page } = load;
  const orgDeletion = page.organisationDeletion;
  const wsDeletion = page.workspaceDeletion;
  const cooling = coolingOffText(page.coolingOffHours);
  const expected = confirming === 'organisation' ? page.organisationConfirmation : page.workspaceConfirmation;
  const matches = confirmationMatches(typed, expected);

  const open = (which: Exclude<Confirming, null>) => { setTyped(''); setConfirming(which); };
  const confirm = () => {
    if (!matches || !confirming) return;
    const which = confirming;
    void act(`schedule-${which}`, async () => {
      const job = which === 'organisation' ? await api.scheduleOrganisationDeletion(typed.trim()) : await api.scheduleWorkspaceDeletion(typed.trim());
      return t(which === 'organisation' ? 'lifecycle.org.scheduled' : 'lifecycle.ws.scheduled', { when: job.purgeAfter ? when(job.purgeAfter) : '-' });
    }).then(() => { if (mounted.current) setConfirming(null); });
  };
  const cancelOrg = () => void act('cancel-organisation', async () => { await api.cancelOrganisationDeletion(); return t('lifecycle.org.cancelled'); });
  const cancelWs = () => void act('cancel-workspace', async () => { await api.cancelWorkspaceDeletion(); return t('lifecycle.ws.cancelled'); });

  const banner = (job: LifecycleJob, which: 'org' | 'ws', canCancel: boolean, onCancel: () => void) => <div className="notice lifecycleBanner" role="status" key={which}>
    <AlertTriangle aria-hidden="true" />
    <div>
      <strong>{t(`lifecycle.${which}.bannerTitle`)}</strong>
      <p>{job.purgeAfter ? t(`lifecycle.${which}.bannerText`, { when: when(job.purgeAfter), left: timeLeftText(job.purgeAfter) }) : t(`lifecycle.${which}.bannerTextNoDate`)}</p>
      {canCancel
        ? <div className="actions"><button type="button" className="secondary" disabled={busy !== null} onClick={onCancel}><Undo2 aria-hidden="true" />{busy === `cancel-${which === 'org' ? 'organisation' : 'workspace'}` ? t('dialog.working') : t(`lifecycle.${which}.cancel`)}</button></div>
        : <p className="muted">{t(`lifecycle.${which}.cancelWho`)}</p>}
    </div>
  </div>;

  const deletionState = (job: LifecycleJob | null, which: 'org' | 'ws'): ReactNode => {
    if (!job) return null;
    if (job.status === 'scheduled') return <p className="muted">{t('lifecycle.seeBanner')}</p>;
    if (job.status === 'purging') return <Notice>{t(`lifecycle.${which}.purging`)}</Notice>;
    if (job.status === 'failed') return <Notice error>{t('lifecycle.deletionFailed', { message: job.error ?? t('lifecycle.noReason') })}</Notice>;
    return null;
  };

  return <section className="surface workspaceAdmin lifecycle" aria-label={label}>
    <div className="sectionHeader">
      <div><h2>{label}</h2><p className="muted">{t('lifecycle.lead')}</p></div>
    </div>
    {orgDeletion?.status === 'scheduled' && banner(orgDeletion, 'org', page.canManageOrganisation, cancelOrg)}
    {wsDeletion?.status === 'scheduled' && banner(wsDeletion, 'ws', page.canDeleteWorkspace, cancelWs)}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}

    <div className="lifecycleCards">
      <section className="lifecycleCard" aria-label={t('lifecycle.export.title')}>
        <h3><FileArchive aria-hidden="true" />{t('lifecycle.export.title')}</h3>
        <p className="muted">{t('lifecycle.export.text', { period: coolingOffText(page.exportRetentionHours) })}</p>
        <ul className="lifecycleList">
          <li>{t('lifecycle.export.notSecrets')}</li>
          <li>{t('lifecycle.export.notPrivate')}</li>
          <li>{t('lifecycle.export.notAccounts')}</li>
        </ul>
        {page.canManageOrganisation ? <>
          <div className="actions">
            <button type="button" disabled={busy !== null || !canRequestExport(page)} onClick={() => void act('export', async () => { await api.requestExport(); return t('lifecycle.export.requested'); })}>
              <FileArchive aria-hidden="true" />{busy === 'export' ? t('dialog.working') : t('lifecycle.export.button')}
            </button>
          </div>
          {page.exports.length === 0
            ? <p className="muted lifecycleEmpty">{t('lifecycle.export.empty')}</p>
            : <ul className="lifecycleExports" aria-label={t('lifecycle.export.listLabel')}>
              {page.exports.map((job) => <li key={job.id} className="lifecycleExport">
                <div className="lifecycleExportHead">
                  <StatusBadge status={job.status} />
                  <span>{t('lifecycle.export.requestedAt', { when: when(job.requestedAt) })}</span>
                </div>
                <dl className="lifecycleExportFacts">
                  <div><dt>{t('lifecycle.export.size')}</dt><dd>{archiveSize(job.archiveBytes)}</dd></div>
                  {job.rowCount !== null && <div><dt>{t('lifecycle.export.rows')}</dt><dd>{formatNumber(job.rowCount)}</dd></div>}
                  {job.fileCount !== null && <div><dt>{t('lifecycle.export.files')}</dt><dd>{formatNumber(job.fileCount)}</dd></div>}
                  {job.status === 'ready' && job.expiresAt && <div><dt>{t('lifecycle.export.until')}</dt><dd>{when(job.expiresAt)}</dd></div>}
                </dl>
                {(job.status === 'queued' || job.status === 'running') && <p className="muted" role="status">{t(job.status === 'queued' ? 'lifecycle.export.queuedText' : 'lifecycle.export.runningText')}</p>}
                {job.status === 'failed' && <p className="lifecycleError">{t('lifecycle.export.failedText', { message: job.error ?? t('lifecycle.noReason') })}</p>}
                {job.status === 'expired' && <p className="muted">{t('lifecycle.export.expiredText')}</p>}
                {job.status === 'ready' && <div className="actions">
                  <button type="button" disabled={busy !== null} aria-label={t('lifecycle.export.downloadLabel', { when: when(job.requestedAt) })}
                    onClick={() => void act(`download-${job.id}`, async () => { await api.downloadExport(job.id); return t('lifecycle.export.downloading'); })}>
                    <Download aria-hidden="true" />{busy === `download-${job.id}` ? t('dialog.working') : t('lifecycle.export.download')}
                  </button>
                </div>}
              </li>)}
            </ul>}
        </> : <p className="notice" role="note"><Lock aria-hidden="true" />{t('lifecycle.export.who')}</p>}
      </section>

      <section className="lifecycleCard" aria-label={t('lifecycle.org.title')}>
        <h3><Trash2 aria-hidden="true" />{t('lifecycle.org.title')}</h3>
        <p className="muted">{t('lifecycle.org.text', { period: cooling })}</p>
        <p className="muted">{t('lifecycle.org.kept')}</p>
        {deletionState(orgDeletion, 'org')}
        {page.canManageOrganisation
          ? !isOpenDeletion(orgDeletion) && <div className="actions"><button type="button" className="danger" disabled={busy !== null} onClick={() => open('organisation')}><Trash2 aria-hidden="true" />{t('lifecycle.org.button')}</button></div>
          : <p className="notice" role="note"><Lock aria-hidden="true" />{t('lifecycle.org.who')}</p>}
      </section>

      <section className="lifecycleCard" aria-label={t('lifecycle.ws.title')}>
        <h3><Trash2 aria-hidden="true" />{t('lifecycle.ws.title')}</h3>
        <p className="muted">{t('lifecycle.ws.text', { period: cooling })}</p>
        {deletionState(wsDeletion, 'ws')}
        {page.canDeleteWorkspace
          ? !isOpenDeletion(wsDeletion) && <div className="actions"><button type="button" className="danger" disabled={busy !== null} onClick={() => open('workspace')}><Trash2 aria-hidden="true" />{t('lifecycle.ws.button')}</button></div>
          : <p className="notice" role="note"><Lock aria-hidden="true" />{t('lifecycle.ws.who')}</p>}
      </section>
    </div>

    {confirming && <ConfirmDialog
      title={confirming === 'organisation' ? t('lifecycle.org.confirmTitle') : t('lifecycle.ws.confirmTitle', { name: page.workspaceConfirmation })}
      confirmLabel={confirming === 'organisation' ? t('lifecycle.org.button') : t('lifecycle.ws.button')}
      danger busy={busy === `schedule-${confirming}`} confirmDisabled={!matches}
      onCancel={() => setConfirming(null)} onConfirm={confirm}>
      <p>{t(confirming === 'organisation' ? 'lifecycle.org.confirmText' : 'lifecycle.ws.confirmText', { period: cooling })}</p>
      <p>{rich(confirming === 'organisation' ? 'lifecycle.org.typePrompt' : 'lifecycle.ws.typePrompt', { value: <strong dir="auto"><code>{expected}</code></strong> })}</p>
      <label className="field"><span>{t(confirming === 'organisation' ? 'lifecycle.org.typeLabel' : 'lifecycle.ws.typeLabel')}</span>
        <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" spellCheck={false} dir="auto" data-autofocus aria-invalid={typed.length > 0 && !matches} />
      </label>
      {typed.length > 0 && !matches && <p className="muted">{t(confirming === 'organisation' ? 'lifecycle.org.mismatch' : 'lifecycle.ws.mismatch')}</p>}
    </ConfirmDialog>}
  </section>;
}
