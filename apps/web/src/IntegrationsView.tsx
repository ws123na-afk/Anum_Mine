import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Activity, CircleDot, Database, FileText, GitBranch, KeyRound, MemoryStick, Plug, RefreshCw, Search, Settings, Webhook, Workflow, Wrench } from 'lucide-react';
import { describeError, isPermissionDenied } from './lib/errors';
import { integrationBadge, integrationToolNote } from './lib/governance';
import { getIntegrationConfiguration, saveIntegrationConfiguration } from './lib/governanceApi';
import { canSwitchIntegration, credentialBody, credentialSource, integrationCounts, lastErrorNote, sortIntegrations, type IntegrationState } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { useIsOwner } from './useIsOwner';
import './governance.css';
import './catalog.css';

const icons: Record<string, ReactNode> = { postgresql: <Database />, keycloak: <KeyRound />, nats: <GitBranch />, temporal: <Workflow />, valkey: <MemoryStick />, minio: <FileText />, s3: <FileText />, searxng: <Search />, 'external-webhook': <Webhook /> };
const failure = (error: unknown) => isPermissionDenied(error) ? `Not allowed: ${describeError(error)} Only workspace owners can change integrations.` : describeError(error);
const when = (iso: string) => new Date(iso).toLocaleString();

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot />{children}</p>;
}

/** Live integration health with its history, and per-workspace controls for owners. */
export function IntegrationsView() {
  const [items, setItems] = useState<IntegrationState[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ id: string; text: string; error?: boolean } | null>(null);
  const owner = useIsOwner();
  const load = useCallback(async () => {
    setLoading(true);
    try { setItems(await catalog.getIntegrationStates()); setLoadError(null); }
    catch (e) { setLoadError(`Could not load integrations: ${failure(e)} Check that the API is reachable, then Refresh.`); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const replace = (next: IntegrationState) => setItems((old) => old.map((x) => (x.id === next.id ? next : x)));
  const test = async (item: IntegrationState) => {
    setTesting(item.id); setFlash(null);
    try {
      const result = await catalog.testIntegration(item.id);
      replace(result);
      setFlash({ id: item.id, text: result.status === 'connected' ? `Connection to ${item.name} works (${result.latency_ms ?? 0} ms).` : result.status === 'degraded' ? `${item.name} did not answer: ${result.detail}` : `${item.name} is not in use here: ${result.detail}`, error: result.status === 'degraded' });
    } catch (e) { setFlash({ id: item.id, text: failure(e), error: true }); } finally { setTesting(null); }
  };
  const counts = integrationCounts(items);
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Connected systems</p><h2>Integrations</h2><p>Live health of the services this deployment uses. Owners test a connection, turn an integration off for this workspace and store the workspace's own token where an integration accepts one. ANUM always connects to the endpoint the operator configured.</p></div><button type="button" onClick={() => void load()} disabled={loading}><RefreshCw />Refresh</button></section>
    {loading && !items.length && <Notice>Loading integrations...</Notice>}
    {loadError && <Notice error>{loadError}</Notice>}
    {owner === false && <Notice>You can see health here. Only workspace owners can test connections, turn integrations off or store credentials; ask an owner of this workspace.</Notice>}
    {items.length > 0 && <p className="catalogSummary" aria-live="polite">{counts.connected} connected · {counts.degraded} need attention · {counts.unused} not used</p>}
    {!loading && !loadError && !items.length && <div className="emptyState"><Plug /><h3>No integrations reported</h3><p>The API returned no integrations. Check the API version, then Refresh.</p></div>}
    <div className="integrationGrid">{sortIntegrations(items).map((x) => {
      const badge = integrationBadge(x.status);
      const tool = integrationToolNote(x.kind, x.status);
      const last = lastErrorNote(x, when);
      return <article className="surface integration integrationCard" key={x.id} aria-labelledby={`integration-${x.id}`}>
        <div className="integrationIcon">{icons[x.id] ?? <Plug />}</div>
        <div>
          <h3 id={`integration-${x.id}`}>{x.name}</h3>
          <p>{x.endpoint}</p>
        </div>
        <div className="integrationActions"><span className={`statusBadge ${badge.tone}`}>{badge.label}</span>{owner && <button type="button" className="secondary" aria-expanded={open === x.id} aria-label={`Configure ${x.name}`} onClick={() => setOpen(open === x.id ? null : x.id)}><Settings size={15} /></button>}</div>
        <div className="integrationBody">
          <small>{x.detail}{x.latency_ms !== null ? ` · ${x.latency_ms} ms` : ''}</small>
          {tool && <small className={`integrationTool ${tool.available ? 'available' : ''}`}>{tool.text}</small>}
          {x.checked_at && <small>Checked {when(x.checked_at)}</small>}
          {last && <small className="integrationLastError">{last}</small>}
        </div>
        {x.remediation && <p className="integrationFix"><Wrench aria-hidden="true" /><span><strong>How to fix: </strong>{x.remediation}</span></p>}
        {x.accepts_workspace_credential && <p className="integrationCredentialLine"><KeyRound aria-hidden="true" />{credentialSource(x)}</p>}
        {owner && <div className="integrationOwnerRow"><button type="button" className="secondary" disabled={testing !== null || x.status === 'disabled'} title={x.status === 'disabled' ? 'Not in use, so there is nothing to test' : undefined} onClick={() => void test(x)}><Activity size={15} />{testing === x.id ? 'Testing...' : 'Test connection'}</button></div>}
        {flash?.id === x.id && <div className="integrationFlash"><Notice error={flash.error}>{flash.text}</Notice></div>}
        {owner && open === x.id && <ConfigurePanel integration={x} onChanged={() => void load()} />}
      </article>;
    })}</div>
  </>;
}

function ConfigurePanel({ integration, onChanged }: { integration: IntegrationState; onChanged: () => void }) {
  const [enabled, setEnabled] = useState(true);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const switchable = canSwitchIntegration(integration);
  useEffect(() => {
    let live = true;
    getIntegrationConfiguration(integration.id)
      .then((c) => { if (live) { setEnabled(c.enabled); setEndpoint(c.endpoint); setReady(true); } })
      .catch((e) => { if (live) setFlash({ text: failure(e), error: true }); });
    return () => { live = false; };
  }, [integration.id]);
  const run = async (action: () => Promise<string>) => {
    setBusy(true); setFlash(null);
    try { setFlash({ text: await action() }); onChanged(); } catch (e) { setFlash({ text: failure(e), error: true }); } finally { setBusy(false); }
  };
  const save = () => run(async () => {
    // The endpoint is kept as stored: it is informational and never connected to.
    const saved = await saveIntegrationConfiguration(integration.id, { enabled, endpoint });
    return `${integration.name} ${saved.enabled ? 'enabled' : 'turned off'} for workspace ${saved.workspace_id}.`;
  });
  const storeSecret = () => {
    const body = credentialBody(secret);
    if (!body.ok) { setFlash({ text: body.error, error: true }); return; }
    void run(async () => { const saved = await catalog.saveIntegrationCredential(integration.id, body.value.secret); setSecret(''); return `Token ${saved.hint ?? ''} stored encrypted for this workspace.`; });
  };
  const removeSecret = () => void run(async () => { await catalog.deleteIntegrationCredential(integration.id); setConfirmRemove(false); return 'Workspace token removed; the operator token (if any) applies again.'; });
  const stored = integration.workspace_credential?.configured;
  return <div className="integrationConfig">
    <form aria-label={`${integration.name} configuration`} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {switchable
        ? <label className="integrationToggle"><input type="checkbox" checked={enabled} disabled={!ready} onChange={(e) => setEnabled(e.target.checked)} />Use {integration.name} in this workspace</label>
        : <p className="featureMeta">Not configured for this deployment, so there is nothing to switch here. {integration.environment_variable ? `The operator sets ${integration.environment_variable}.` : ''}</p>}
      {switchable && <div className="actions"><button type="submit" disabled={!ready || busy}>Save configuration</button></div>}
    </form>
    {integration.accepts_workspace_credential && <form className="integrationCredential" aria-label={`${integration.name} token`} onSubmit={(e) => { e.preventDefault(); storeSecret(); }}>
      <label className="field"><span>{stored ? 'Replace the workspace token' : 'Workspace token (sent as a bearer token)'}</span><input type="password" autoComplete="off" spellCheck={false} value={secret} maxLength={4096} onChange={(e) => setSecret(e.target.value)} aria-describedby={`token-help-${integration.id}`} /></label>
      <p className="featureMeta" id={`token-help-${integration.id}`}>Stored encrypted and never shown again; only its last four characters are displayed. Agents never see it.</p>
      <div className="actions">
        <button type="submit" disabled={busy || !secret}>{stored ? 'Replace token' : 'Store token'}</button>
        {stored && (confirmRemove
          ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={removeSecret}>Remove token</button><button type="button" className="secondary" onClick={() => setConfirmRemove(false)}>Cancel</button></span>
          : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirmRemove(true)}>Remove token...</button>)}
      </div>
    </form>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
  </div>;
}
