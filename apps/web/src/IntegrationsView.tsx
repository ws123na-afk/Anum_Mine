import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Activity, CircleDot, Database, FileText, GitBranch, KeyRound, MemoryStick, Plug, RefreshCw, Search, Settings, Webhook, Workflow, Wrench } from 'lucide-react';
import { describeError, isPermissionDenied } from './lib/errors';
import { integrationBadge, integrationToolNote } from './lib/governance';
import { getIntegrationConfiguration, saveIntegrationConfiguration } from './lib/governanceApi';
import { canSwitchIntegration, credentialBody, credentialSource, integrationCounts, lastErrorNote, sortIntegrations, type IntegrationState } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { formatDateTime, t } from './lib/i18n';
import { useIsOwner } from './useIsOwner';
import './governance.css';
import './catalog.css';

const icons: Record<string, ReactNode> = { postgresql: <Database />, keycloak: <KeyRound />, nats: <GitBranch />, temporal: <Workflow />, valkey: <MemoryStick />, minio: <FileText />, s3: <FileText />, searxng: <Search />, 'external-webhook': <Webhook /> };
const failure = (error: unknown) => isPermissionDenied(error) ? t('integrations.notAllowed', { error: describeError(error) }) : describeError(error);
const when = (iso: string) => formatDateTime(iso);

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
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
    catch (e) { setLoadError(t('integrations.loadFailed', { error: failure(e) })); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const replace = (next: IntegrationState) => setItems((old) => old.map((x) => (x.id === next.id ? next : x)));
  const test = async (item: IntegrationState) => {
    setTesting(item.id); setFlash(null);
    try {
      const result = await catalog.testIntegration(item.id);
      replace(result);
      const text = result.status === 'connected' ? t('integrations.testOk', { name: item.name, ms: result.latency_ms ?? 0 })
        : result.status === 'degraded' ? t('integrations.testDegraded', { name: item.name, detail: result.detail })
          : t('integrations.testUnused', { name: item.name, detail: result.detail });
      setFlash({ id: item.id, text, error: result.status === 'degraded' });
    } catch (e) { setFlash({ id: item.id, text: failure(e), error: true }); } finally { setTesting(null); }
  };
  const counts = integrationCounts(items);
  return <>
    <section className="moduleIntro"><div><p className="eyebrow">{t('integrations.eyebrow')}</p><h2>{t('integrations.title')}</h2><p>{t('integrations.intro')}</p></div><button type="button" onClick={() => void load()} disabled={loading}><RefreshCw aria-hidden="true" />{t('integrations.refresh')}</button></section>
    {loading && !items.length && <Notice>{t('integrations.loading')}</Notice>}
    {loadError && <Notice error>{loadError}</Notice>}
    {owner === false && <Notice>{t('integrations.memberNotice')}</Notice>}
    {items.length > 0 && <p className="catalogSummary" aria-live="polite">{t('integrations.summary', counts)}</p>}
    {!loading && !loadError && !items.length && <div className="emptyState"><Plug aria-hidden="true" /><h3>{t('integrations.emptyTitle')}</h3><p>{t('integrations.emptyText')}</p></div>}
    <div className="integrationGrid">{sortIntegrations(items).map((x) => {
      const badge = integrationBadge(x.status);
      const tool = integrationToolNote(x.kind, x.status);
      const last = lastErrorNote(x, when);
      return <article className="surface integration integrationCard" key={x.id} aria-labelledby={`integration-${x.id}`}>
        <div className="integrationIcon" aria-hidden="true">{icons[x.id] ?? <Plug />}</div>
        <div>
          <h3 id={`integration-${x.id}`}>{x.name}</h3>
          <p dir="ltr" className="catalogLtr">{x.endpoint}</p>
        </div>
        <div className="integrationActions"><span className={`statusBadge ${badge.tone}`}>{badge.label}</span>{owner && <button type="button" className="secondary" aria-expanded={open === x.id} aria-label={t('integrations.configure', { name: x.name })} onClick={() => setOpen(open === x.id ? null : x.id)}><Settings size={15} aria-hidden="true" /></button>}</div>
        <div className="integrationBody">
          <small dir="auto">{x.latency_ms !== null ? t('integrations.latency', { detail: x.detail, ms: x.latency_ms }) : x.detail}</small>
          {tool && <small className={`integrationTool ${tool.available ? 'available' : ''}`}>{tool.text}</small>}
          {x.checked_at && <small>{t('integrations.checked', { time: when(x.checked_at) })}</small>}
          {last && <small className="integrationLastError">{last}</small>}
        </div>
        {x.remediation && <p className="integrationFix"><Wrench aria-hidden="true" /><span><strong>{t('integrations.howToFix')}</strong><span dir="auto">{x.remediation}</span></span></p>}
        {x.accepts_workspace_credential && <p className="integrationCredentialLine"><KeyRound aria-hidden="true" />{credentialSource(x)}</p>}
        {owner && <div className="integrationOwnerRow"><button type="button" className="secondary" disabled={testing !== null || x.status === 'disabled'} title={x.status === 'disabled' ? t('integrations.testDisabled') : undefined} onClick={() => void test(x)}><Activity size={15} aria-hidden="true" />{testing === x.id ? t('integrations.testing') : t('integrations.test')}</button></div>}
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
    return t(saved.enabled ? 'integrations.saved.on' : 'integrations.saved.off', { name: integration.name, workspace: saved.workspace_id });
  });
  const storeSecret = () => {
    const body = credentialBody(secret);
    if (!body.ok) { setFlash({ text: body.error, error: true }); return; }
    void run(async () => { const saved = await catalog.saveIntegrationCredential(integration.id, body.value.secret); setSecret(''); return t('integrations.tokenStored', { hint: saved.hint ?? '' }); });
  };
  const removeSecret = () => void run(async () => { await catalog.deleteIntegrationCredential(integration.id); setConfirmRemove(false); return t('integrations.tokenRemoved'); });
  const stored = integration.workspace_credential?.configured;
  return <div className="integrationConfig">
    <form aria-label={t('integrations.configLabel', { name: integration.name })} onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {switchable
        ? <label className="integrationToggle"><input type="checkbox" checked={enabled} disabled={!ready} onChange={(e) => setEnabled(e.target.checked)} />{t('integrations.useIn', { name: integration.name })}</label>
        : <p className="featureMeta">{integration.environment_variable ? t('integrations.notConfiguredSetting', { setting: integration.environment_variable }) : t('integrations.notConfigured')}</p>}
      {switchable && <div className="actions"><button type="submit" disabled={!ready || busy}>{t('integrations.save')}</button></div>}
    </form>
    {integration.accepts_workspace_credential && <form className="integrationCredential" aria-label={t('integrations.tokenForm', { name: integration.name })} onSubmit={(e) => { e.preventDefault(); storeSecret(); }}>
      <label className="field"><span>{stored ? t('integrations.tokenReplace') : t('integrations.tokenNew')}</span><input type="password" dir="ltr" autoComplete="off" spellCheck={false} value={secret} maxLength={4096} onChange={(e) => setSecret(e.target.value)} aria-describedby={`token-help-${integration.id}`} /></label>
      <p className="featureMeta" id={`token-help-${integration.id}`}>{t('integrations.tokenHelp')}</p>
      <div className="actions">
        <button type="submit" disabled={busy || !secret}>{stored ? t('integrations.tokenReplaceButton') : t('integrations.tokenStore')}</button>
        {stored && (confirmRemove
          ? <span className="featureConfirm"><button type="button" className="danger" disabled={busy} onClick={removeSecret}>{t('integrations.tokenRemove')}</button><button type="button" className="secondary" onClick={() => setConfirmRemove(false)}>{t('common.cancel')}</button></span>
          : <button type="button" className="secondary" disabled={busy} onClick={() => setConfirmRemove(true)}>{t('integrations.tokenRemoveAsk')}</button>)}
      </div>
    </form>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
  </div>;
}
