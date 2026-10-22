/** Executive 360 command centre (master plan F10, docs/ANUM_MINE_EXECUTIVE_PA.md).
 *
 * One desk for the workspaces the person may see, kept apart. Numbers come only from the
 * grant-checked `GET /api/v1/executive/overview`; a workspace without a grant shows nothing
 * and opens only by an explicit workspace switch. Grants are managed in
 * ExecutiveCommandCenterGrants.tsx (owner only, approved by exact payload hash).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Building2, Link2Off, LockKeyhole, RefreshCw, ShieldCheck } from 'lucide-react';
import { apiRequest, apiRequestVoid, currentScope } from './lib/api';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t } from './lib/i18n';
import { createExecutiveClient } from './lib/executiveClient';
import { createPaClient, splitOverview, summaryFigures, type Figures } from './lib/executivePa';
import type { ExecutiveGrantedWorkspace, ExecutiveNotGrantedWorkspace, ExecutiveOverview, ExecutiveSummary, ExecutiveUnavailableWorkspace } from '@anum/contracts';
import { ExecutivePanel } from './ExecutivePanel';
import { ExecutiveGrants } from './ExecutiveCommandCenterGrants';
import { PageHeader, LoadingSkeleton } from './PageParts';
import './executivePanel.css';

type Load = { state: 'loading' } | { state: 'ready'; overview: ExecutiveOverview; own: ExecutiveSummary | null }
  | { state: 'denied' } | { state: 'error'; message: string } | { state: 'notConnected'; own: ExecutiveSummary | null };

function FigureList({ figures }: { figures: Figures }) {
  const none = <span className="muted">{t('executive.fig.notShared')}</span>;
  const num = (v: number | null) => (v === null ? none : <strong>{formatNumber(v)}</strong>);
  const rows: [string, string, React.ReactNode][] = [
    ['tasks', t('executive.fig.tasks'), num(figures.tasks)],
    ['blocked', t('executive.fig.blocked'), num(figures.blocked)],
    ['approvals', t('executive.fig.approvals'), num(figures.approvals)],
    ['costs', t('executive.fig.costs'), figures.costUsd === null ? none : <strong>{t('executive.cost', { amount: formatNumber(Number(figures.costUsd)) })}</strong>],
    ['meetings', t('executive.fig.meetings'), num(figures.meetings)],
  ];
  if (figures.counts) { rows.push(['agents', t('executive.fig.agents'), num(figures.counts.agents)], ['rooms', t('executive.fig.rooms'), num(figures.counts.rooms)]); }
  return <dl className="exFigures">{rows.map(([id, label, value]) => <div key={id}><dt>{label}</dt><dd dir="auto">{value}</dd></div>)}</dl>;
}

export function ExecutiveCommandCenter({ workspaceId, tenantId, owner, onGo, onSwitchWorkspace }: {
  workspaceId: string; tenantId: string; owner: boolean | null;
  onGo(view: never): void;
  onSwitchWorkspace(workspaceId: string): Promise<void>;
}) {
  const desk = useMemo(() => createExecutiveClient(apiRequest, apiRequestVoid, currentScope), []);
  const pa = useMemo(() => createPaClient(apiRequest), []);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const go = onGo as (view: string) => void;

  const ownSummary = useCallback(async (): Promise<ExecutiveSummary | null> => {
    try { return (await pa.invoke('summarise_workspace', {}, 'typed')).summary ?? null; } catch { return null; }
  }, [pa]);

  const refresh = useCallback(async () => {
    setLoad({ state: 'loading' });
    try {
      const [overview, own] = await Promise.all([desk.overview(), ownSummary()]);
      setLoad({ state: 'ready', overview, own });
    } catch (error) {
      if (isPermissionDenied(error)) { setLoad({ state: 'denied' }); return; }
      if (error instanceof ApiError && error.status === 503) { setLoad({ state: 'notConnected', own: await ownSummary() }); return; }
      setLoad({ state: 'error', message: t('executive.loadFailed', { message: describeError(error) }) });
    }
  }, [desk, ownSummary]);
  useEffect(() => { void refresh(); }, [refresh, workspaceId, tenantId]);

  const doSwitch = async (id: string, name: string) => {
    setBusy(id); setNote({ text: t('executive.switching') });
    try { await onSwitchWorkspace(id); setNote({ text: t('executive.switchedTo', { name }) }); }
    catch (error) { setNote({ text: t('executive.switchFailed', { message: describeError(error) }), error: true }); }
    finally { setBusy(null); }
  };

  const switchBlock = (id: string, name: string) => id === workspaceId
    ? <div className="exActions"><button type="button" className="secondary" onClick={() => go('tasks')}>{t('executive.openIn')}</button></div>
    : <><div className="exActions"><button type="button" disabled={busy !== null} onClick={() => void doSwitch(id, name)}>{t('executive.switch', { name })}</button></div><p className="muted exSwitchNote">{t('executive.switchNote')}</p></>;

  const granted = (w: ExecutiveGrantedWorkspace) => <li key={w.workspace_id} className="exCard" data-state="granted" data-current={w.workspace_id === workspaceId || undefined}>
    <div className="exCardHead"><h4 dir="auto">{w.name}</h4><span className="exBadge granted">{w.workspace_id === workspaceId ? t('executive.state.current') : t('executive.state.granted')}</span></div>
    <FigureList figures={summaryFigures(w.summary)} />
    <p className="muted exSwitchNote">{t('executive.asOf', { time: formatDateTime(w.as_of) })}{w.expires_at ? ` | ${t('executive.expires', { time: formatDateTime(w.expires_at) })}` : ''}</p>
    {switchBlock(w.workspace_id, w.name)}
  </li>;

  const notGranted = (w: ExecutiveNotGrantedWorkspace, own: ExecutiveSummary | null) => {
    const current = w.workspace_id === workspaceId;
    const name = w.name ?? w.workspace_id;
    return <li key={w.workspace_id} className="exCard" data-state={current ? 'own' : 'not_granted'} data-current={current || undefined}>
      <div className="exCardHead"><h4 dir="auto">{name}</h4><span className={`exBadge ${current ? 'own' : 'not_granted'}`}>{current ? t('executive.state.current') : w.reason === 'grant_ended' ? t('executive.state.grantEnded') : t('executive.state.notGranted')}</span></div>
      {current && own ? <FigureList figures={summaryFigures(own)} /> : <p className="muted exEmpty"><LockKeyhole aria-hidden="true" />{t('executive.notGrantedText')}</p>}
      {w.open_with === 'workspace_switch' || current ? switchBlock(w.workspace_id, name) : null}
    </li>;
  };

  const unavailable = (w: ExecutiveUnavailableWorkspace) => <li key={w.workspace_id} className="exCard" data-state="not_connected">
    <div className="exCardHead"><h4 dir="auto">{w.workspace_id}</h4><span className="exBadge">{t('executive.state.unavailable')}</span></div>
    <p className="muted exEmpty"><Link2Off aria-hidden="true" />{t('executive.unavailableText')}</p>
  </li>;

  const group = (id: string, title: string, items: React.ReactNode[]) => items.length === 0 ? null
    : <section className="exGroup" aria-labelledby={`ex-g-${id}`}><h3 id={`ex-g-${id}`}><ShieldCheck aria-hidden="true" />{title} <small>{formatNumber(items.length)}</small></h3><ul className="exCards">{items}</ul></section>;

  return <div className="exPage">
    <PageHeader title={t('nav.executive')} subtitle={t('executive.subtitle')}
      actions={<button type="button" className="secondary" onClick={() => void refresh()} disabled={load.state === 'loading'}><RefreshCw aria-hidden="true" />{t('executive.refresh')}</button>} />
    {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
    <div className="exLayout">
      <section className="exDesk" aria-labelledby="ex-desk-title">
        <h2 id="ex-desk-title" className="visuallyHidden">{t('executive.group.granted')}</h2>
        {load.state === 'loading' && <LoadingSkeleton label={t('executive.loading')} rows={4} />}
        {load.state === 'error' && <div className="notice errorNotice" role="alert"><span>{load.message}</span><button type="button" className="secondary" onClick={() => void refresh()}>{t('common.retry')}</button></div>}
        {load.state === 'denied' && <div className="exState"><LockKeyhole aria-hidden="true" /><h3>{t('executive.deniedTitle')}</h3><p>{t('executive.deniedText')}</p></div>}
        {load.state === 'notConnected' && <>
          <div className="exState"><Link2Off aria-hidden="true" /><h3>{t('executive.notConnectedTitle')}</h3><p>{t('executive.notConnectedText')}</p></div>
          <ul className="exCards"><li className="exCard" data-state="own" data-current>
            <div className="exCardHead"><h4>{t('executive.state.current')}</h4><span className="exBadge own">{t('executive.state.current')}</span></div>
            {load.own ? <FigureList figures={summaryFigures(load.own)} /> : <p className="muted exEmpty">{t('executive.fig.notShared')}</p>}
            <div className="exActions"><button type="button" className="secondary" onClick={() => go('tasks')}>{t('executive.openIn')}</button></div>
          </li></ul>
        </>}
        {load.state === 'ready' && load.overview.workspaces.length === 0 && <div className="exState"><Building2 aria-hidden="true" /><h3>{t('executive.emptyTitle')}</h3><p>{t('executive.emptyText')}</p><button type="button" onClick={() => go('settings')}>{t('executive.emptyAction')}</button></div>}
        {load.state === 'ready' && (() => {
          const split = splitOverview(load.overview.workspaces);
          return <>
            {group('granted', t('executive.group.granted'), split.granted.map(granted))}
            {group('notGranted', t('executive.group.notGranted'), split.notGranted.map((w) => notGranted(w, load.own)))}
            {group('unavailable', t('executive.group.unavailable'), split.unavailable.map(unavailable))}
          </>;
        })()}
        <ExecutiveGrants client={desk} owner={owner} workspaceId={workspaceId} />
      </section>
      <aside className="exSide"><ExecutivePanel onOpenView={(view) => go(view)} /></aside>
    </div>
  </div>;
}
