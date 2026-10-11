import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { CircleDot, Database, FileText, GitBranch, KeyRound, MemoryStick, Plug, RefreshCw, Settings, Workflow } from 'lucide-react';
import { getCurrentMembership, getIntegrations, type IntegrationHealth } from './lib/api';
import { describeError, isPermissionDenied } from './lib/errors';
import { integrationBadge, integrationConfigBody } from './lib/governance';
import { getIntegrationConfiguration, saveIntegrationConfiguration } from './lib/governanceApi';
import './governance.css';

const icons: Record<string, ReactNode> = { postgresql: <Database />, keycloak: <KeyRound />, nats: <GitBranch />, temporal: <Workflow />, valkey: <MemoryStick />, minio: <FileText />, s3: <FileText /> };
const failure = (error: unknown) => isPermissionDenied(error) ? `Not allowed: ${describeError(error)}` : describeError(error);

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot />{children}</p>;
}

/** Live integration health, with a per-integration configuration panel for workspace owners. */
export function IntegrationsView() {
  const [items, setItems] = useState<IntegrationHealth[]>([]);
  const [state, setState] = useState<{ text: string; error?: boolean } | null>({ text: 'Loading integrations...' });
  const [owner, setOwner] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => getIntegrations().then((x) => { setItems(x); setState(null); }).catch((e) => setState({ text: failure(e), error: true })), []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void getCurrentMembership().then((m) => setOwner(m.role === 'owner' && m.active)).catch(() => setOwner(false)); }, []);
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">Connected systems</p><h2>Integrations</h2><p>Live service health and connectivity. Owners can turn an integration off or record another endpoint for this workspace; health checks always probe the operator-configured endpoint.</p></div><button onClick={() => void load()}><RefreshCw />Refresh</button></section>
    {state && <Notice error={state.error}>{state.text}</Notice>}
    <div className="integrationGrid">{items.map((x) => {
      const badge = integrationBadge(x.status);
      return <article className="surface integration integrationCard" key={x.id}>
        <div className="integrationIcon">{icons[x.id] ?? <Plug />}</div>
        <div><h3>{x.name}</h3><p>{x.endpoint}</p><small>{x.detail}{x.latency_ms !== null ? ` · ${x.latency_ms} ms` : ''}</small></div>
        <div className="integrationActions"><span className={`statusBadge ${badge.tone}`}>{badge.label}</span>{owner && <button type="button" className="secondary" aria-expanded={open === x.id} aria-label={`Configure ${x.name}`} onClick={() => setOpen(open === x.id ? null : x.id)}><Settings size={15} /></button>}</div>
        {owner && open === x.id && <ConfigurePanel integration={x} onSaved={() => void load()} />}
      </article>;
    })}</div>
  </>;
}

function ConfigurePanel({ integration, onSaved }: { integration: IntegrationHealth; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(true);
  const [endpoint, setEndpoint] = useState('');
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  useEffect(() => {
    let live = true;
    getIntegrationConfiguration(integration.id)
      .then((c) => { if (live) { setEnabled(c.enabled); setEndpoint(c.endpoint ?? ''); setReady(true); } })
      .catch((e) => { if (live) setFlash({ text: failure(e), error: true }); });
    return () => { live = false; };
  }, [integration.id]);
  const save = async () => {
    const body = integrationConfigBody(enabled, endpoint);
    if (!body.ok) { setFlash({ text: body.error, error: true }); return; }
    setBusy(true); setFlash(null);
    try {
      const saved = await saveIntegrationConfiguration(integration.id, body.value);
      setEnabled(saved.enabled); setEndpoint(saved.endpoint ?? '');
      setFlash({ text: `${integration.name} ${saved.enabled ? 'enabled' : 'turned off'} for workspace ${saved.workspace_id}.` });
      onSaved();
    } catch (e) { setFlash({ text: failure(e), error: true }); } finally { setBusy(false); }
  };
  return <form className="integrationConfig" aria-label={`${integration.name} configuration`} onSubmit={(e) => { e.preventDefault(); void save(); }}>
    <label className="integrationToggle"><input type="checkbox" checked={enabled} disabled={!ready} onChange={(e) => setEnabled(e.target.checked)} />Use {integration.name} in this workspace</label>
    <label className="field"><span>Endpoint (leave empty for the deployment default)</span><input value={endpoint} maxLength={500} disabled={!ready} placeholder={integration.endpoint} onChange={(e) => setEndpoint(e.target.value)} /></label>
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <div className="actions"><button type="submit" disabled={!ready || busy}>Save configuration</button></div>
  </form>;
}
