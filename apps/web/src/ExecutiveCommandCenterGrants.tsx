/** Grants of the CURRENT workspace (owner only): request, approve by exact hash, reject,
 * narrow, revoke. Contract: packages/contracts/src/executive.ts, lib/executiveClient.ts. */
import { useCallback, useEffect, useState } from 'react';
import { KeyRound } from 'lucide-react';
import { ApiError, describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import type { createExecutiveClient } from './lib/executiveClient';
import { EXECUTIVE_SCOPES, type ExecutiveGrant, type ExecutiveScope } from '@anum/contracts';

const mk = (key: string, vars?: Record<string, string | number>) => t(key as MessageKey, vars);
const scopesText = (scopes: readonly string[]) => scopes.map((s) => mk(`executive.scope.${s}`)).join(', ');

export function ExecutiveGrants({ client, owner, workspaceId }: {
  client: ReturnType<typeof createExecutiveClient>; owner: boolean | null; workspaceId: string;
}) {
  const [items, setItems] = useState<ExecutiveGrant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [note, setNote] = useState<{ text: string; error?: boolean } | null>(null);
  const [creating, setCreating] = useState(false);
  const [grantee, setGrantee] = useState('');
  const [scopes, setScopes] = useState<ExecutiveScope[]>(['counts']);
  const [expiry, setExpiry] = useState('');
  const [narrowing, setNarrowing] = useState<string | null>(null);
  const [narrowScopes, setNarrowScopes] = useState<ExecutiveScope[]>([]);
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setError(null); setDenied(false);
    try { setItems((await client.listGrants(100)).items); }
    catch (e) { if (isPermissionDenied(e)) setDenied(true); else setError(t('executive.grants.loadFailed', { message: describeError(e) })); setItems([]); }
  }, [client]);
  useEffect(() => { void refresh(); }, [refresh, workspaceId]);

  const act = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try { await work(); setNote({ text: done }); await refresh(); }
    catch (e) { setNote({ text: isPermissionDenied(e) ? t('executive.grants.denied') : t('executive.grants.failed', { message: e instanceof ApiError ? describeError(e) : describeError(e) }), error: true }); }
    finally { setBusy(false); }
  };

  const toggle = (list: ExecutiveScope[], set: (v: ExecutiveScope[]) => void, scope: ExecutiveScope, on: boolean) => set(on ? [...list, scope] : list.filter((s) => s !== scope));
  const scopeBoxes = (list: ExecutiveScope[], set: (v: ExecutiveScope[]) => void, only?: readonly string[]) =>
    EXECUTIVE_SCOPES.filter((s) => !only || only.includes(s)).map((scope) => <label key={scope} className="checkRow">
      <input type="checkbox" checked={list.includes(scope)} onChange={(e) => toggle(list, set, scope, e.target.checked)} />{mk(`executive.scope.${scope}`)}
    </label>);

  const create = () => act(async () => {
    const end = expiry ? new Date(`${expiry}T23:59:59`).toISOString() : undefined;
    await client.createGrant({ grantee_user_id: grantee.trim(), scopes, ...(end ? { expires_at: end } : {}) });
    setCreating(false); setGrantee(''); setExpiry(''); setScopes(['counts']);
  }, t('executive.grants.requested'));

  if (denied || owner === false) return <section className="exGrants" aria-labelledby="ex-grants-title"><h3 id="ex-grants-title"><KeyRound aria-hidden="true" />{t('executive.grants.title')}</h3><p className="muted">{t('executive.grants.ownerOnly')}</p></section>;

  return <section className="exGrants" aria-labelledby="ex-grants-title">
    <h3 id="ex-grants-title"><KeyRound aria-hidden="true" />{t('executive.grants.title')}</h3>
    <p className="muted">{t('executive.grants.subtitle')}</p>
    {note && <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}>{note.text}</p>}
    {error && <div className="notice errorNotice" role="alert"><span>{error}</span><button type="button" className="secondary" onClick={() => void refresh()}>{t('common.retry')}</button></div>}
    {!creating && <div className="exActions"><button type="button" className="secondary" onClick={() => setCreating(true)}>{t('executive.grants.new')}</button></div>}
    {creating && <form className="exGrantForm" aria-label={t('executive.grants.new')} onSubmit={(e) => { e.preventDefault(); void create(); }}>
      <label>{t('executive.grants.grantee')}<input value={grantee} onChange={(e) => setGrantee(e.target.value)} required maxLength={160} autoComplete="off" dir="auto" /></label>
      <fieldset><legend>{t('executive.grants.scopes')}</legend>{scopeBoxes(scopes, setScopes)}{scopes.length === 0 && <p className="notice errorNotice" role="alert">{t('executive.pickOne')}</p>}</fieldset>
      <label>{t('executive.grants.expiry')}<input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></label>
      <div className="exActions"><button type="submit" disabled={busy || scopes.length === 0 || grantee.trim() === ''}>{t('executive.grants.submit')}</button><button type="button" className="secondary" onClick={() => setCreating(false)}>{t('executive.grants.cancel')}</button></div>
    </form>}
    {items !== null && items.length === 0 && !error && <p className="muted">{t('executive.grants.empty')}</p>}
    <ul className="exCards exGrantList">
      {(items ?? []).map((g) => <li key={g.id} className="exCard" data-state={g.status}>
        <div className="exCardHead"><h4 dir="auto">{t('executive.grants.row', { grantee: g.grantee_user_id, scopes: scopesText(g.scopes) })}</h4><span className={`exBadge ${g.status === 'active' ? 'granted' : ''}`}>{mk(`executive.status.${g.status}`)}</span></div>
        <p className="muted exSwitchNote">{t('executive.grants.by', { user: g.requested_by })} | {formatDateTime(g.requested_at)}{g.expires_at ? ` | ${t('executive.expires', { time: formatDateTime(g.expires_at) })}` : ''}</p>
        {g.status === 'pending' && <>
          <p className="exHash"><span>{t('executive.grants.hash')}</span><code>{g.payload_hash}</code></p>
          <p className="muted exSwitchNote">{t('executive.grants.hashNote')}</p>
          <div className="exActions">
            <button type="button" disabled={busy} onClick={() => void act(() => client.approveGrant(g.id, { payload_hash: g.payload_hash }), t('executive.grants.approved'))}>{t('executive.grants.approve')}</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => client.rejectGrant(g.id), t('executive.grants.rejected'))}>{t('executive.grants.reject')}</button>
          </div>
        </>}
        {g.status === 'active' && <>
          <div className="exActions">
            <button type="button" className="secondary" disabled={busy} onClick={() => { setNarrowing(g.id); setNarrowScopes(g.scopes); setConfirmRevoke(null); }}>{t('executive.grants.narrow')}</button>
            {confirmRevoke === g.id
              ? <button type="button" className="danger" disabled={busy} onClick={() => void act(async () => { await client.revokeGrant(g.id); setConfirmRevoke(null); }, t('executive.grants.revoked'))}>{t('executive.grants.revokeConfirm', { grantee: g.grantee_user_id })}</button>
              : <button type="button" className="secondary" disabled={busy} onClick={() => { setConfirmRevoke(g.id); setNarrowing(null); }}>{t('executive.grants.revoke')}</button>}
          </div>
          {narrowing === g.id && <form className="exGrantForm" aria-label={t('executive.grants.narrow')} onSubmit={(e) => { e.preventDefault(); void act(async () => { await client.narrowGrant(g.id, { expected_revision: g.revision, scopes: narrowScopes }); setNarrowing(null); }, t('executive.grants.narrowed')); }}>
            <fieldset><legend>{t('executive.grants.scopes')}</legend><p className="muted">{t('executive.grants.narrowNote')}</p>{scopeBoxes(narrowScopes, setNarrowScopes, g.scopes)}{narrowScopes.length === 0 && <p className="notice errorNotice" role="alert">{t('executive.pickOne')}</p>}</fieldset>
            <div className="exActions"><button type="submit" disabled={busy || narrowScopes.length === 0 || narrowScopes.length === g.scopes.length}>{t('executive.grants.narrowSave')}</button><button type="button" className="secondary" onClick={() => setNarrowing(null)}>{t('executive.grants.cancel')}</button></div>
          </form>}
        </>}
      </li>)}
    </ul>
  </section>;
}
