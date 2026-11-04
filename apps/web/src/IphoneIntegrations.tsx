// Settings, Integrations, iPhone (docs/iphone-integrations.md). Its own lazy chunk: the person's
// devices (token shown once, rotate, pause, revoke, scopes), and one card per integration the API
// has (switch, last connection, failed events, a synthetic test, and the exact Shortcut setup).
// Integrations come from the API; their words come from IPHONE_GUIDES (lib/iphoneIntegrations.ts),
// where a module adds its row. Nothing here records anything in a module.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { CheckCircle2, ClipboardCopy, Pause, Play, Plus, RefreshCw, Smartphone, Trash2, XCircle } from 'lucide-react';
import type { IphoneDevice, IphoneDeviceToken, IphoneEvent, IphoneIntegration, IphoneOverview, IphoneScope, IphoneTestResult } from '@anum/contracts';
import * as api from './lib/iphoneApi';
import { apiBaseUrl } from './lib/api';
import { codeLabel, fullUrl, guideFor, integrationState, integrationTitle, issuableScopes, outcomeLabel, pingBody, scopeLabel } from './lib/iphoneIntegrations';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import './iphoneIntegrations.css';

type Load = { kind: 'loading' } | { kind: 'ready'; value: IphoneOverview } | { kind: 'denied' } | { kind: 'error'; message: string };
const when = (value: string | null | undefined) => (value ? formatDateTime(value) : '-');
const HEADERS = ['Authorization: Bearer <token>', 'Content-Type: application/json'];

export function IphoneIntegrations() {
  const [state, setState] = useState<Load>({ kind: 'loading' });
  const load = useCallback(() => {
    api.overview().then((value) => setState({ kind: 'ready', value }),
      (e) => setState(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: describeError(e) }));
  }, []);
  useEffect(() => { load(); }, [load]);
  return <div className="settingsStack iph">
    <section className="surface iphIntro" aria-labelledby="iph-title">
      <h2 id="iph-title"><Smartphone aria-hidden="true" />{t('iph.title')}</h2>
      <p className="muted">{t('iph.intro')}</p>
    </section>
    {state.kind === 'loading' && <p className="muted" role="status">{t('iph.loading')}</p>}
    {state.kind === 'denied' && <p className="notice" role="status">{t('iph.denied')}</p>}
    {state.kind === 'error' && <div className="notice iphError" role="alert"><p>{t('iph.error.load', { message: state.message })}</p>
      <button type="button" className="secondary" onClick={() => { setState({ kind: 'loading' }); load(); }}>{t('common.retry')}</button></div>}
    {state.kind === 'ready' && <>
      <Devices overview={state.value} onChanged={load} />
      <section className="surface iphPanel" aria-labelledby="iph-integrations">
        <h2 id="iph-integrations">{t('iph.integrations')}</h2>
        {state.value.integrations.length === 0
          ? <p className="muted">{t('iph.integrations.none')}</p>
          : <ul className="iphCards">{state.value.integrations.map((item) => <li key={item.key}>
            <IntegrationCard item={item} pingPath={state.value.ping_path} onChanged={load} /></li>)}</ul>}
      </section>
    </>}
  </div>;
}

// ---- devices ----------------------------------------------------------------------------------

function ScopeBoxes({ scopes, value, onChange, legend }: { scopes: IphoneScope[]; value: IphoneScope[]; onChange: (next: IphoneScope[]) => void; legend: string }) {
  return <fieldset className="iphScopes"><legend>{legend}</legend>
    {scopes.map((scope) => <label key={scope} className="iphCheck"><input type="checkbox" checked={value.includes(scope)}
      onChange={(e) => onChange(e.target.checked ? [...value, scope] : value.filter((s) => s !== scope))} />{scopeLabel(scope)}</label>)}
  </fieldset>;
}

function Devices({ overview, onChanged }: { overview: IphoneOverview; onChanged: () => void }) {
  const issuable = issuableScopes(overview.integrations);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<IphoneScope[]>(issuable.slice(0, 2));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<IphoneDeviceToken | null>(null);
  const [revoking, setRevoking] = useState<IphoneDevice | null>(null);
  const [editing, setEditing] = useState<{ id: string; scopes: IphoneScope[] } | null>(null);
  const nameId = useId();
  const live = overview.devices.filter((d) => d.status !== 'revoked');
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await work(); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const add = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => { setIssued(await api.createDevice(name.trim() || 'iPhone', scopes)); setName(''); });
  };
  return <section className="surface iphPanel" aria-labelledby="iph-devices">
    <h2 id="iph-devices">{t('iph.devices')}</h2>
    {overview.devices.length === 0
      ? <div className="iphEmpty"><Smartphone aria-hidden="true" /><div><strong>{t('iph.devices.none')}</strong><p className="muted">{t('iph.devices.noneText')}</p></div></div>
      : <ul className="iphDevices">{overview.devices.map((d) => <li key={d.id} className={`iphDevice ${d.status}`}>
        <div className="iphDeviceText">
          <strong dir="auto">{d.name}</strong>
          <small>{t(`iph.device.${d.status}` as MessageKey)} · <span dir="ltr">{t('iph.device.token', { hint: d.token_hint })}</span></small>
          <small>{t('iph.device.lastSeen')}: {when(d.last_seen_at)} · {t('iph.device.lastError')}: {d.last_error ? `${codeLabel(d.last_error)} (${when(d.last_error_at)})` : '-'}</small>
          {editing?.id === d.id
            ? <div className="iphEditScopes"><ScopeBoxes scopes={issuable} value={editing.scopes} legend={t('iph.device.scopes')} onChange={(next) => setEditing({ id: d.id, scopes: next })} />
              <div className="actions">
                <button type="button" disabled={busy || editing.scopes.length === 0} onClick={() => void run(async () => { await api.setScopes(d.id, [...editing.scopes, ...d.scopes.filter((s) => !issuable.includes(s))]); setEditing(null); })}>{t('iph.device.saveScopes')}</button>
                <button type="button" className="secondary" onClick={() => setEditing(null)}>{t('iph.device.cancel')}</button></div></div>
            : <ul className="iphChips" aria-label={t('iph.device.scopes')}>{d.scopes.map((s) => <li key={s}>{scopeLabel(s)}</li>)}</ul>}
        </div>
        {d.status !== 'revoked' && editing?.id !== d.id && <div className="actions iphDeviceActions">
          {d.status === 'active'
            ? <button type="button" className="secondary" disabled={busy} onClick={() => void run(() => api.changeDevice(d.id, 'pause'))}><Pause aria-hidden="true" />{t('iph.device.pause')}</button>
            : <button type="button" className="secondary" disabled={busy} onClick={() => void run(() => api.changeDevice(d.id, 'resume'))}><Play aria-hidden="true" />{t('iph.device.resume')}</button>}
          <button type="button" className="secondary" disabled={busy} onClick={() => void run(async () => setIssued(await api.rotateDevice(d.id)))}><RefreshCw aria-hidden="true" />{t('iph.device.rotate')}</button>
          <button type="button" className="secondary" disabled={busy || issuable.length === 0} onClick={() => setEditing({ id: d.id, scopes: d.scopes.filter((s) => issuable.includes(s)) })}>{t('iph.device.editScopes')}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => setRevoking(d)}><Trash2 aria-hidden="true" />{t('iph.device.revoke')}</button>
        </div>}
      </li>)}</ul>}
    {issuable.length === 0
      ? <p className="muted">{t('iph.devices.noScopes')}</p>
      : <form className="iphAdd" onSubmit={add}>
        <div className="iphAddName"><label htmlFor={nameId}>{t('iph.device.name')}</label>
          <input id={nameId} value={name} maxLength={60} placeholder="iPhone" onChange={(e) => setName(e.target.value)} /></div>
        <ScopeBoxes scopes={issuable} value={scopes} legend={t('iph.device.scopes')} onChange={setScopes} />
        <div className="actions"><button type="submit" disabled={busy || scopes.length === 0 || live.length >= overview.max_devices}><Plus aria-hidden="true" />{t('iph.device.add')}</button>
          <small className="muted">{t('iph.device.limit', { count: overview.max_devices })}</small></div>
      </form>}
    {error && <p className="cmFieldError" role="alert" id="iph-device-error">{error}</p>}
    {issued && <TokenDialog issued={issued} onClose={() => setIssued(null)} />}
    {revoking && <ConfirmDialog title={t('iph.device.revokeTitle')} confirmLabel={t('iph.device.revoke')} danger busy={busy}
      onConfirm={() => void run(() => api.changeDevice(revoking.id, 'revoke')).then(() => setRevoking(null))} onCancel={() => setRevoking(null)}>
      <p>{t('iph.device.revokeText', { name: revoking.name })}</p></ConfirmDialog>}
  </section>;
}

function TokenDialog({ issued, onClose }: { issued: IphoneDeviceToken; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(issued.token); setCopied(true); } catch { setCopied(false); } };
  return <Modal title={t('iph.token.title')} onClose={onClose} className="iphToken">
    <p className="notice">{t('iph.token.once')}</p>
    <label className="field"><span>{t('iph.token.label')}</span><textarea readOnly value={issued.token} rows={3} dir="ltr" onFocus={(e) => e.currentTarget.select()} /></label>
    <p className="muted">{issued.device.scopes.map(scopeLabel).join(', ')}</p>
    <h3>{t('iph.token.headers')}</h3>
    <div className="iphCode">{HEADERS.map((line) => <code key={line} dir="ltr">{line}</code>)}</div>
    <div className="actions">
      <button type="button" className="secondary" onClick={() => void copy()}><ClipboardCopy aria-hidden="true" />{copied ? t('iph.token.copied') : t('iph.token.copy')}</button>
      <button type="button" onClick={onClose}>{t('iph.token.done')}</button>
    </div>
  </Modal>;
}

// ---- integrations -------------------------------------------------------------------------------

function IntegrationCard({ item, pingPath, onChanged }: { item: IphoneIntegration; pingPath: string; onChanged: () => void }) {
  const guide = guideFor(item.key);
  const title = integrationTitle(item);
  const state = integrationState(item);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<IphoneTestResult | null>(null);
  const [on, setOn] = useState(item.enabled);
  useEffect(() => { setOn(item.enabled); }, [item.enabled]);
  const switchId = useId();
  const toggle = async (enabled: boolean) => {
    setBusy(true); setError(null); setOn(enabled);
    try { await api.setSwitch(item.key, enabled); onChanged(); } catch (e) { setOn(!enabled); setError(describeError(e)); } finally { setBusy(false); }
  };
  const runTest = async () => {
    setBusy(true); setError(null); setTest(null);
    try { setTest(await api.runTest(item.key)); onChanged(); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const url = fullUrl(apiBaseUrl, item.ingest_path);
  return <article className="iphCard" aria-labelledby={`${switchId}-title`}>
    <header className="iphCardHead">
      <div><h3 id={`${switchId}-title`}>{title}</h3>{guide && <p className="muted">{t(guide.purpose)}</p>}</div>
      <label className="iphSwitch" htmlFor={switchId}>
        <input id={switchId} type="checkbox" role="switch" checked={on} disabled={busy || !item.allowed}
          aria-label={t('iph.switch.label', { name: title })} onChange={(e) => void toggle(e.target.checked)} />
        <span>{t('iph.switch')}</span>
      </label>
    </header>
    <p className={`iphState ${state}`} role="status">{t(`iph.state.${state}` as MessageKey)}</p>
    {!item.allowed && <p className="notice">{t('iph.notAllowed')}</p>}
    {item.needs_safety_check && <p className="notice">{t('iph.safetyCheck')} {guide?.settingsHash && <a href={guide.settingsHash}>{t('iph.openSettings')}</a>}</p>}
    <dl className="iphFacts">
      <div><dt>{t('iph.devicesWithScope')}</dt><dd>{item.devices_with_scope}</dd></div>
      <div><dt>{t('iph.lastConnection')}</dt><dd>{when(item.last_connection_at)}</dd></div>
      <div><dt>{t('iph.lastOk')}</dt><dd>{when(item.last_ok_at)}</dd></div>
      <div><dt>{t('iph.failed7d')}</dt><dd>{item.failed_7d}</dd></div>
      <div><dt>{t('iph.limits')}</dt><dd>{t('iph.limits.value', { minute: item.per_minute, day: item.per_day })}</dd></div>
    </dl>
    <div className="actions">
      <button type="button" className="secondary" disabled={busy || !item.allowed} onClick={() => void runTest()}>{busy && !test ? t('iph.testing') : t('iph.test')}</button>
      {guide?.settingsHash && <a className="iphLink" href={guide.settingsHash}>{t('iph.openSettings')}</a>}
    </div>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
    {test && <div className="iphTest" role="status">
      <p>{test.passed ? t('iph.test.passed') : t('iph.test.failed')}</p>
      <ul className="iphChecks">{test.checks.map((c) => <li key={c.check}>{c.passed ? <CheckCircle2 aria-hidden="true" className="ok" /> : <XCircle aria-hidden="true" className="bad" />}
        <span>{(() => { const key = `iph.check.${c.check}` as MessageKey; const text = t(key); return text === key ? c.check : text; })()}</span></li>)}</ul>
    </div>}
    <details className="iphMore"><summary>{t('iph.events')}{item.failed_7d > 0 ? ` (${item.failed_7d})` : ''}</summary><Events integrationKey={item.key} /></details>
    <details className="iphMore"><summary>{t('iph.docs')}</summary>
      <dl className="iphDocs">
        <div><dt>{t('iph.docs.address')}</dt><dd><code dir="ltr">{url}</code></dd></div>
        <div><dt>{t('iph.docs.method')}</dt><dd><code dir="ltr">{item.method}</code></dd></div>
        <div><dt>{t('iph.token.headers')}</dt><dd className="iphCode">{HEADERS.map((line) => <code key={line} dir="ltr">{line}</code>)}</dd></div>
        <div><dt>{t('iph.docs.body')}</dt><dd className="iphCode">{(guide?.body ?? item.body_fields.map((f) => `${f} = ...`)).map((line) => <code key={line} dir="ltr">{line}</code>)}</dd></div>
      </dl>
      {guide && <><h4>{t('iph.docs.steps')}</h4><ol className="iphSteps">{guide.steps(url).map((step, i) => <li key={i}>{step}</li>)}</ol></>}
      <h4>{t('iph.docs.ping')}</h4>
      <p className="muted">{t('iph.docs.pingText')}</p>
      <div className="iphCode"><code dir="ltr">POST {fullUrl(apiBaseUrl, pingPath)}</code><code dir="ltr">{pingBody(item.key)}</code></div>
      <p className="muted">{t('iph.docs.limits')}</p>
    </details>
  </article>;
}

function Events({ integrationKey }: { integrationKey: string }) {
  const [items, setItems] = useState<IphoneEvent[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const more = useCallback((offset: number) => {
    api.events(integrationKey, offset).then((page) => { setItems((old) => [...(offset ? old ?? [] : []), ...page.items]); setTotal(page.total); },
      (e) => setError(describeError(e)));
  }, [integrationKey]);
  useEffect(() => { more(0); }, [more]);
  if (error) return <p className="cmFieldError" role="alert">{error}</p>;
  if (items === null) return <p className="muted" role="status">{t('iph.events.loading')}</p>;
  if (items.length === 0) return <p className="muted">{t('iph.events.none')}</p>;
  return <>
    <ul className="iphEvents">{items.map((e) => <li key={e.id}><span>{outcomeLabel(e.outcome)}: {codeLabel(e.code)}</span><small>{when(e.created_at)}</small></li>)}</ul>
    {items.length < total && <button type="button" className="secondary" onClick={() => more(items.length)}>{t('iph.events.more')}</button>}
  </>;
}
