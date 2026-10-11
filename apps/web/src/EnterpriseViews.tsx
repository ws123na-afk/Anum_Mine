import { useEffect, useState } from 'react';
import { Download, PackageCheck, RefreshCw, Route, ShieldCheck } from 'lucide-react';
import { apiBaseUrl, apiRequest, authHeaders } from './lib/api';
import { apiErrorFromResponse, describeError } from './lib/errors';

// The shared request surfaces the API's own error message (describeError) instead of a bare status.
const api = <T,>(path: string, init: RequestInit = {}): Promise<T> => apiRequest<T>(path, init);
const failed = (error: unknown) => describeError(error);

type Summary = { policy_packs: number; active_policy_packs: number; role_templates: number; approval_rules: number; memory_governance_configured: boolean };
type Pack = { id: string; name: string; version: number; active: boolean; rules: { action: string; effect: string }[] };
type Ops = { active_regions: number; healthy_targets: number; degraded_targets: number; installed_packages: number; failover_ready: boolean };
type MarketPackage = { id: string; name: string; kind: string; version: string; verified: boolean; permissions: string[] };
type Target = { id: string; region: string; provider: string; model: string; status: string; cost_per_1k_tokens: number; latency_ms: number };

export function GovernanceView() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [packs, setPacks] = useState<Pack[]>([]);
  const [message, setMessage] = useState('Loading governance controls...');
  const load = () => Promise.all([api<Summary>('/api/v1/organization/governance'), api<Pack[]>('/api/v1/policy-packs')])
    .then(([nextSummary, nextPacks]) => { setSummary(nextSummary); setPacks(nextPacks); setMessage('Governance state is current.'); })
    .catch((error) => setMessage(failed(error)));
  useEffect(() => { void load(); }, []);
  // One baseline per organization: the button is disabled once a pack with that name exists.
  const hasBaseline = packs.some((pack) => pack.name === 'Organization baseline');
  const addBaseline = async () => {
    try {
      await api('/api/v1/policy-packs', { method: 'POST', body: JSON.stringify({ name: 'Organization baseline', description: 'Approval-first enterprise defaults', rules: [{ action: 'integration.write', effect: 'require_approval', conditions: { risk: 'high' } }] }) });
      await load();
    } catch (error) { setMessage(failed(error)); }
  };
  const exportAudit = async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/audit/export?format=json`, { headers: await authHeaders() });
      if (!response.ok) throw await apiErrorFromResponse(response, 'Audit export failed');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = 'audit-export.json'; link.click();
      URL.revokeObjectURL(url);
    } catch (error) { setMessage(failed(error)); }
  };
  return <><section className="metrics"><Metric label="Policy packs" value={String(summary?.policy_packs ?? 0)} /><Metric label="Active policies" value={String(summary?.active_policy_packs ?? 0)} /><Metric label="Role templates" value={String(summary?.role_templates ?? 0)} /><Metric label="Approval rules" value={String(summary?.approval_rules ?? 0)} /></section><section className="moduleIntro"><div><p className="eyebrow">Organization controls</p><h2>Governance center</h2><p>Versioned policy, retention, roles, approvals, and immutable audit export.</p></div><div className="actions"><button type="button" onClick={() => void addBaseline()} disabled={hasBaseline} title={hasBaseline ? 'The organization baseline already exists' : undefined}><ShieldCheck size={17}/>Add baseline</button><button type="button" className="secondary" onClick={() => void exportAudit()}><Download size={17}/>Export audit</button></div></section><p className="notice">{message}</p><div className="integrationGrid">{packs.map((pack) => <article className="surface integration" key={pack.id}><div className="integrationIcon"><ShieldCheck/></div><div><h3>{pack.name}</h3><p>Version {pack.version} · {pack.rules.length} rules</p><div className="tagRow">{pack.rules.map((rule) => <span key={`${rule.action}-${rule.effect}`}>{rule.action}: {rule.effect}</span>)}</div></div><span className={`statusBadge ${pack.active ? 'success' : 'neutral'}`}>{pack.active ? 'active' : 'archived'}</span></article>)}</div></>;
}

export function EnterpriseView() {
  const [ops, setOps] = useState<Ops | null>(null);
  const [packages, setPackages] = useState<MarketPackage[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [message, setMessage] = useState('Loading enterprise control plane...');
  const load = () => Promise.all([api<Ops>('/api/v1/enterprise/operations'), api<MarketPackage[]>('/api/v1/marketplace/packages'), api<Target[]>('/api/v1/routing/targets')])
    .then(([nextOps, nextPackages, nextTargets]) => { setOps(nextOps); setPackages(nextPackages); setTargets(nextTargets); setMessage('Routing and marketplace state is current.'); })
    .catch((error) => setMessage(failed(error)));
  useEffect(() => { void load(); }, []);
  const install = async (id: string) => { try { await api(`/api/v1/marketplace/packages/${id}/install`, { method: 'POST', body: '{}' }); await load(); } catch (error) { setMessage(failed(error)); } };
  return <><section className="metrics"><Metric label="Active regions" value={String(ops?.active_regions ?? 0)} /><Metric label="Healthy targets" value={String(ops?.healthy_targets ?? 0)} /><Metric label="Installed packages" value={String(ops?.installed_packages ?? 0)} /><Metric label="Failover" value={ops?.failover_ready ? 'Ready' : 'Pending'} /></section><section className="moduleIntro"><div><p className="eyebrow">Scale and ecosystem</p><h2>Enterprise operations</h2><p>Marketplace distribution, regional placement, constrained routing, and failover.</p></div><button type="button" onClick={() => void load()}><RefreshCw size={17}/>Refresh</button></section><p className="notice">{message}</p><div className="integrationGrid">{targets.map((target) => <article className="surface integration" key={target.id}><div className="integrationIcon"><Route/></div><div><h3>{target.id}</h3><p>{target.region} · {target.provider}/{target.model}</p><small>{target.status === 'unverified' ? 'Cost and latency not measured yet' : `$${target.cost_per_1k_tokens}/1k · ${target.latency_ms} ms`}</small></div><span className={`statusBadge ${target.status === 'healthy' ? 'success' : 'warning'}`}>{target.status}</span></article>)}</div><section className="surface approvalTable enterpriseCatalog"><div className="sectionHeader"><div><p className="eyebrow">Verified distribution</p><h2>Marketplace catalog</h2></div></div>{packages.length === 0 && <p className="muted">No packages have been published to this marketplace yet.</p>}{packages.map((item) => <div className="catalogRow" key={item.id}><PackageCheck/><div><strong>{item.name}</strong><p>{item.kind} · v{item.version} · {item.permissions.join(', ')}</p></div><button type="button" onClick={() => void install(item.id)}>Install</button></div>)}</section></>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span></div><strong>{value}</strong><small>Tenant control plane</small></article>;
}
