/** Executive 360 command centre (master plan F10, docs/ANUM_MINE_EXECUTIVE_PA.md).
 *
 * One desk that lists every workspace the person may see, kept apart by kind, with only the
 * minimum summary each owner granted. It never reads another workspace by itself: a row's
 * numbers come from the server's grant-checked overview, and confidential content opens only
 * after an explicit workspace switch. Grants are made, widened and revoked per workspace.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Building2, Link2Off, LockKeyhole, RefreshCw, ShieldCheck } from 'lucide-react';
import { apiRequest, apiRequestVoid } from './lib/api';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import {
  GRANT_SCOPES, createDeskClient, createPaClient, groupByKind, needsSwitch,
  type DeskWorkspace, type Grant, type GrantScope, type WorkSummary,
} from './lib/executivePa';
import { ExecutivePanel } from './ExecutivePanel';
import { PageHeader, LoadingSkeleton } from './PageParts';
import './executivePanel.css';

type Load = { state: 'loading' } | { state: 'ready'; rows: DeskWorkspace[] } | { state: 'denied' } | { state: 'error'; message: string }
  | { state: 'notConnected'; own: WorkSummary | null };

const mk = (key: string, vars?: Record<string, string | number>) => t(key as MessageKey, vars);

function Figures({ summary }: { summary: WorkSummary }) {
  const rows: [string, string, number | null | undefined, boolean][] = [
    ['tasks', 'executive.fig.tasks', summary.tasks_total, false],
    ['blocked', 'executive.fig.blocked', summary.tasks_blocked, false],
    ['approvals', 'executive.fig.approvals', summary.approvals_pending, false],
    ['costs', 'executive.fig.costs', summary.cost_usd_month, true],
    ['meetings', 'executive.fig.meetings', summary.meetings_active, false],
  ];
  return <dl className="exFigures">
    {rows.map(([id, label, value, money]) => <div key={id}>
      <dt>{mk(label)}</dt>
      <dd dir="auto">{value === undefined || value === null ? <span className="muted">{t('executive.fig.unavailable')}</span> : <strong>{money ? t('executive.cost', { amount: formatNumber(value) }) : formatNumber(value)}</strong>}</dd>
    </div>)}
  </dl>;
}

export function ExecutiveCommandCenter({ workspaceId, tenantId, owner, onGo, onSwitchWorkspace, onSwitchOrganisation }: {
  workspaceId: string; tenantId: string; owner: boolean | null;
  onGo(view: never): void;
  onSwitchWorkspace(workspaceId: string): Promise<void>;
  onSwitchOrganisation(tenantId: string, workspaceId: string): Promise<void>;
}) {
  const desk = useMemo(() => createDeskClient(apiRequest, apiRequestVoid), []);
  const pa = useMemo(() => createPaClient(apiRequest), []);
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [grants, setGrants] = useState<Grant[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [scopes, setScopes] = useState<GrantScope[]>(['tasks']);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; error?: boolean; approvals?: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const go = onGo as (view: string) => void;

  const refresh = useCallback(async () => {
    setLoad({ state: 'loading' });
    try {
      const overview = await desk.overview();
      setLoad({ state: 'ready', rows: overview.workspaces });
      desk.grants().then(setGrants).catch(() => setGrants([]));
    } catch (error) {
      if (isPermissionDenied(error)) { setLoad({ state: 'denied' }); return; }
      if (error instanceof ApiError && [404, 501, 503].includes(error.status ?? 0)) {
        // The grant system is not installed: show the current workspace honestly, from its own APIs.
        let own: WorkSummary | null = null;
        try { own = (await pa.invoke('summarise_workspace', {}, 'typed')).summary ?? null; } catch { own = null; }
        setLoad({ state: 'notConnected', own });
        return;
      }
      setLoad({ state: 'error', message: t('executive.loadFailed', { message: describeError(error) }) });
    }
  }, [desk, pa]);
  useEffect(() => { void refresh(); }, [refresh, workspaceId]);

  const doSwitch = async (row: DeskWorkspace) => {
    setBusy(row.workspace_id); setNote({ text: t('executive.switching') });
    try {
      if (row.tenant_id !== tenantId) await onSwitchOrganisation(row.tenant_id, row.workspace_id); else await onSwitchWorkspace(row.workspace_id);
      setNote({ text: t('executive.switchedTo', { name: row.name }) });
    } catch (error) { setNote({ text: t('executive.switchFailed', { message: describeError(error) }), error: true }); }
    finally { setBusy(null); }
  };

  const startEdit = (row: DeskWorkspace) => {
    const existing = grants.find((g) => g.workspace_id === row.workspace_id && g.status !== 'revoked');
    setScopes((existing?.scopes.filter((s): s is GrantScope => (GRANT_SCOPES as readonly string[]).includes(s))) ?? ['tasks']);
    setEditing(row.workspace_id); setConfirmRevoke(null);
  };

  const failGrant = (error: unknown) => setNote({ text: isPermissionDenied(error) ? t('executive.grantDenied') : t('executive.grantFailed', { message: describeError(error) }), error: true });

  const saveGrant = async (row: DeskWorkspace) => {
    if (scopes.length === 0) return;
    setBusy(row.workspace_id);
    try {
      const outcome = row.grant_id ? await desk.widen(row.grant_id, scopes) : await desk.grant(row.workspace_id, scopes);
      setNote(outcome.kind === 'pending_approval' ? { text: t('executive.grantPending'), approvals: true } : { text: t('executive.granted', { name: row.name }) });
      setEditing(null);
      await refresh();
    } catch (error) { failGrant(error); } finally { setBusy(null); }
  };

  const revoke = async (row: DeskWorkspace) => {
    if (!row.grant_id) return;
    setBusy(row.workspace_id);
    try { await desk.revoke(row.grant_id); setNote({ text: t('executive.revoked', { name: row.name }) }); setConfirmRevoke(null); await refresh(); }
    catch (error) { failGrant(error); } finally { setBusy(null); }
  };

  const card = (row: DeskWorkspace) => {
    const current = !needsSwitch(row, { tenantId, workspaceId });
    const manage = row.grant_state !== 'own';
    return <li key={row.tenant_id + row.workspace_id} className="exCard" data-state={row.grant_state} data-current={current || undefined}>
      <div className="exCardHead">
        <h4 dir="auto">{row.name}</h4>
        <span className={`exBadge ${row.grant_state}`}>{current && row.grant_state !== 'own' ? t('executive.state.own') : mk(`executive.state.${row.grant_state}`)}</span>
      </div>
      {row.summary ? <Figures summary={row.summary} />
        : <p className="muted exEmpty">{row.grant_state === 'not_connected' ? <><Link2Off aria-hidden="true" />{t('executive.notConnectedRow')}</> : <><LockKeyhole aria-hidden="true" />{t('executive.notGrantedText')}</>}</p>}
      <div className="exActions">
        {!current && <button type="button" disabled={busy !== null} onClick={() => void doSwitch(row)}>{t('executive.switch', { name: row.name })}</button>}
        {current && <button type="button" className="secondary" onClick={() => go('tasks')}>{t('executive.openIn')}</button>}
        {manage && row.grant_state !== 'not_connected' && <>
          <button type="button" className="secondary" disabled={owner === false || busy !== null} onClick={() => startEdit(row)}>{row.grant_id ? t('executive.widen') : t('executive.grant')}</button>
          {row.grant_id && (confirmRevoke === row.workspace_id
            ? <button type="button" className="danger" disabled={busy !== null} onClick={() => void revoke(row)}>{t('executive.revoke')}: {row.name}</button>
            : <button type="button" className="secondary" disabled={owner === false || busy !== null} onClick={() => { setConfirmRevoke(row.workspace_id); setEditing(null); }}>{t('executive.revoke')}</button>)}
        </>}
      </div>
      {!current && <p className="muted exSwitchNote">{t('executive.switchNote')}</p>}
      {editing === row.workspace_id && <form className="exGrantForm" aria-label={t('executive.grantTitle', { name: row.name })} onSubmit={(e) => { e.preventDefault(); void saveGrant(row); }}>
        <fieldset>
          <legend>{t('executive.scopesLegend')}</legend>
          <p className="muted">{t('executive.grantExplain')}</p>
          {GRANT_SCOPES.map((scope) => <label key={scope} className="checkRow">
            <input type="checkbox" checked={scopes.includes(scope)} onChange={(e) => setScopes((old) => e.target.checked ? [...old, scope] : old.filter((s) => s !== scope))} />{mk(`executive.scope.${scope}`)}
          </label>)}
          {scopes.length === 0 && <p className="notice errorNotice" role="alert">{t('executive.pickOne')}</p>}
        </fieldset>
        <div className="exActions">
          <button type="submit" disabled={scopes.length === 0 || busy !== null}>{t('executive.save')}</button>
          <button type="button" className="secondary" onClick={() => setEditing(null)}>{t('executive.cancel')}</button>
        </div>
      </form>}
    </li>;
  };

  return <div className="exPage">
    <PageHeader title={t('nav.executive')} subtitle={t('executive.subtitle')}
      actions={<button type="button" className="secondary" onClick={() => void refresh()} disabled={load.state === 'loading'}><RefreshCw aria-hidden="true" />{t('executive.refresh')}</button>} />
    {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}{note.approvals && <button type="button" className="secondary" onClick={() => go('approvals')}>{t('executive.openApprovals')}</button>}</p>}
    <div className="exLayout">
      <section className="exDesk" aria-labelledby="ex-desk-title">
        <h2 id="ex-desk-title" className="visuallyHidden">{t('executive.workspaces')}</h2>
        {load.state === 'loading' && <LoadingSkeleton label={t('executive.loading')} rows={4} />}
        {load.state === 'error' && <div className="notice errorNotice" role="alert"><span>{load.message}</span><button type="button" className="secondary" onClick={() => void refresh()}>{t('common.retry')}</button></div>}
        {load.state === 'denied' && <div className="exState"><LockKeyhole aria-hidden="true" /><h3>{t('executive.deniedTitle')}</h3><p>{t('executive.deniedText')}</p></div>}
        {load.state === 'notConnected' && <>
          <div className="exState"><Link2Off aria-hidden="true" /><h3>{t('executive.notConnectedTitle')}</h3><p>{t('executive.notConnectedText')}</p></div>
          <ul className="exCards"><li className="exCard" data-state="own" data-current>
            <div className="exCardHead"><h4>{t('executive.state.own')}</h4><span className="exBadge own">{t('executive.state.own')}</span></div>
            {load.own ? <Figures summary={load.own} /> : <p className="muted exEmpty">{t('executive.fig.unavailable')}</p>}
            <div className="exActions"><button type="button" className="secondary" onClick={() => go('tasks')}>{t('executive.openIn')}</button></div>
          </li></ul>
        </>}
        {load.state === 'ready' && load.rows.length === 0 && <div className="exState"><Building2 aria-hidden="true" /><h3>{t('executive.emptyTitle')}</h3><p>{t('executive.emptyText')}</p><button type="button" onClick={() => go('settings')}>{t('executive.emptyAction')}</button></div>}
        {load.state === 'ready' && groupByKind(load.rows).map((group) => <section key={group.kind} className="exGroup" aria-labelledby={`ex-g-${group.kind}`}>
          <h3 id={`ex-g-${group.kind}`}><ShieldCheck aria-hidden="true" />{mk(`executive.kind.${group.kind}`)} <small>{formatNumber(group.rows.length)}</small></h3>
          <ul className="exCards">{group.rows.map(card)}</ul>
        </section>)}
      </section>
      <aside className="exSide"><ExecutivePanel onOpenView={(view) => go(view)} /></aside>
    </div>
  </div>;
}
