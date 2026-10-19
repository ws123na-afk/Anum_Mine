// Settings > Workspace: the sales demo workspace (docs/demo-workspace.md). An owner creates or
// resets a "Demo" workspace filled with labelled sample data, then switches to it for a demo.
// Nothing is sent from the Demo workspace; the API enforces it.
import { useCallback, useEffect, useState } from 'react';
import { ArrowRightLeft, CircleDot, Lock, Presentation, RefreshCw, ShieldCheck } from 'lucide-react';
import { ConfirmDialog } from './Dialog';
import { HelpLink } from './HelpLink';
import { getDemoStatus, resetDemo, type DemoStatus } from './lib/demo';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';

type Load = { kind: 'loading' } | { kind: 'denied' } | { kind: 'error'; detail: string } | { kind: 'ready'; status: DemoStatus };

export function DemoWorkspaceCard({ onSwitchWorkspace }: { onSwitchWorkspace?: (workspaceId: string) => Promise<void> }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);

  const refresh = useCallback(async () => {
    try {
      setLoad({ kind: 'ready', status: await getDemoStatus() });
    } catch (error) {
      setLoad(isPermissionDenied(error) ? { kind: 'denied' } : { kind: 'error', detail: describeError(error) });
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function fill() {
    setBusy(true);
    setFlash(null);
    try {
      const status = await resetDemo();
      setLoad({ kind: 'ready', status });
      setFlash({ text: t('demo.done', { count: status.records }) });
      setConfirm(false);
    } catch (error) {
      setFlash({ text: isPermissionDenied(error) ? t('demo.ownerOnly') : t('demo.error', { reason: describeError(error) }), error: true });
    } finally {
      setBusy(false);
    }
  }

  return <section className="surface workspaceAdmin demoCard" aria-labelledby="demo-title" data-testid="demo-workspace">
    <div className="sectionHeader">
      <div><h2 id="demo-title">{t('demo.title')}</h2><p className="muted">{t('demo.lead')}</p></div>
    </div>
    {load.kind === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('demo.loading')}</p>}
    {load.kind === 'denied' && <p className="notice" role="note"><Lock aria-hidden="true" />{t('demo.ownerOnly')}</p>}
    {load.kind === 'error' && <>
      <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('demo.error', { reason: load.detail })}</p>
      <div className="actions"><button type="button" className="secondary" onClick={() => void refresh()}><RefreshCw aria-hidden="true" />{t('shell.refresh')}</button></div>
    </>}
    {load.kind === 'ready' && <>
      <p className="muted">{load.status.exists
        ? <>{t('demo.ready', { count: load.status.records })} {load.status.filledAt && t('demo.filledAt', { when: formatDateTime(load.status.filledAt) })}</>
        : t('demo.none')}</p>
      <p className="notice" role="note"><ShieldCheck aria-hidden="true" />{t('demo.sendsOff')}</p>
      {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
      <div className="actions">
        {load.status.exists
          ? <button type="button" className="secondary" disabled={busy} onClick={() => setConfirm(true)}><RefreshCw aria-hidden="true" />{t('demo.reset')}</button>
          : <button type="button" disabled={busy} onClick={() => void fill()}><Presentation aria-hidden="true" />{busy ? t('demo.working') : t('demo.create')}</button>}
        {load.status.exists && onSwitchWorkspace && <button type="button" disabled={busy} onClick={() => void onSwitchWorkspace(load.status.workspaceId).catch((error: unknown) => setFlash({ text: describeError(error), error: true }))}><ArrowRightLeft aria-hidden="true" />{t('demo.open')}</button>}
      </div>
      <HelpLink article="demo" />
    </>}
    {confirm && <ConfirmDialog title={t('demo.resetTitle')} confirmLabel={t('demo.reset')} busy={busy} onCancel={() => setConfirm(false)} onConfirm={() => void fill()}>
      <p>{t('demo.resetText')}</p>
    </ConfirmDialog>}
  </section>;
}
