import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Archive, CircleDot, Download, PackageCheck, Pencil, Plus, RefreshCw, Route, ShieldCheck, Undo2 } from 'lucide-react';
import { apiRequest, authHeaders, getCurrentMembership } from './lib/api';
import { apiErrorFromResponse, describeError, isPermissionDenied } from './lib/errors';
import * as gov from './lib/governanceApi';
import {
  ROLE_TEMPLATE_PERMISSIONS, approvalRuleBody, emptyTargetDraft, packState, regionSummary, roleTemplateBody, routingTargetBody, sortPacks, targetDraft,
  type ApprovalRule, type EnterpriseOperations, type GovernanceSummary, type PolicyPack, type RegionStatus, type RoleTemplate, type RoutingTarget, type TargetDraft,
} from './lib/governance';
import './governance.css';

const base = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

type MarketPackage = { id: string; name: string; kind: string; version: string; verified: boolean; permissions: string[] };
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot />{children}</p>;
}

const failure = (error: unknown) => isPermissionDenied(error) ? `Not allowed: ${describeError(error)}` : describeError(error);

/** Runs one mutation at a time and reports its outcome in place. */
function useAction(reload: () => Promise<void>) {
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const act = async (key: string, action: () => Promise<string>): Promise<boolean> => {
    setBusy(key); setFlash(null);
    try { setFlash({ text: await action() }); await reload(); return true; }
    catch (error) { setFlash({ text: failure(error), error: true }); return false; }
    finally { setBusy(null); }
  };
  return { busy, flash, setFlash, act };
}

function useIsOwner(): boolean | null {
  const [owner, setOwner] = useState<boolean | null>(null);
  useEffect(() => { void getCurrentMembership().then((m) => setOwner(m.role === 'owner' && m.active)).catch(() => setOwner(false)); }, []);
  return owner;
}

export function GovernanceView() {
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [packs, setPacks] = useState<PolicyPack[]>([]);
  const [templates, setTemplates] = useState<RoleTemplate[]>([]);
  const [rules, setRules] = useState<ApprovalRule[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const owner = useIsOwner();
  const load = useCallback(async () => {
    try {
      const [s, p, t, r] = await Promise.all([gov.getGovernanceSummary(), gov.getPolicyPacks(), gov.getRoleTemplates(), gov.getApprovalRules()]);
      setSummary(s); setPacks(p); setTemplates(t); setRules(r); setLoadError(null);
    } catch (error) { setLoadError(failure(error)); } finally { setLoaded(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const { busy, flash, act } = useAction(load);
  const addBaseline = () => act('baseline', async () => {
    const pack = await gov.createPolicyPack({ name: 'Organization baseline', description: 'Approval-first enterprise defaults', rules: [{ action: 'integration.write', effect: 'require_approval', conditions: { risk: 'high' } }] });
    return `Policy pack ${pack.name} v${pack.version} created.`;
  });
  const exportAudit = () => act('audit', async () => {
    const response = await fetch(`${base}/api/v1/audit/export?format=json`, { headers: await authHeaders() });
    if (!response.ok) throw await apiErrorFromResponse(response, 'Audit export failed');
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = 'audit-export.json'; link.click();
    URL.revokeObjectURL(url);
    return 'Audit export downloaded.';
  });
  return <>
    <section className="metrics"><Metric label="Policy packs" value={String(summary?.policy_packs ?? 0)} /><Metric label="Active policies" value={String(summary?.active_policy_packs ?? 0)} /><Metric label="Role templates" value={String(summary?.role_templates ?? 0)} /><Metric label="Approval rules" value={String(summary?.approval_rules ?? 0)} /></section>
    <section className="moduleIntro"><div><p className="eyebrow">Organization controls</p><h2>Governance center</h2><p>Versioned policy, retention, roles, approvals, and immutable audit export.</p></div><div className="actions">{owner && <button type="button" disabled={busy !== null} onClick={() => void addBaseline()}><ShieldCheck size={17} />Add baseline</button>}<button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportAudit()}><Download size={17} />Export audit</button></div></section>
    {!loaded && <Notice>Loading governance controls...</Notice>}
    {loadError && <Notice error>{loadError}</Notice>}
    {owner === false && <Notice>Changing policy packs, role templates and approval rules needs the owner role. You can read them here.</Notice>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <section className="surface govSection" aria-label="Policy packs">
      <div className="sectionHeader"><div><p className="eyebrow">Versioned policy</p><h2>Policy packs</h2></div></div>
      {loaded && packs.length === 0 && <p className="muted">No policy packs yet.</p>}
      {sortPacks(packs).map((pack) => {
        const state = packState(pack);
        return <article className="govRow" key={pack.id}>
          <ShieldCheck />
          <div><strong>{pack.name}</strong> <span className="govVersion">v{pack.version}</span><p>{pack.description || 'No description'} · {pack.rules.length} {pack.rules.length === 1 ? 'rule' : 'rules'}</p><div className="tagRow">{pack.rules.map((rule) => <span key={`${rule.action}-${rule.effect}`}>{rule.action}: {rule.effect}</span>)}</div></div>
          <span className={`statusBadge ${state.tone}`}>{state.label}</span>
          {owner && <button type="button" className="secondary" disabled={busy !== null} aria-label={`${state.action === 'activate' ? 'Activate' : 'Archive'} ${pack.name} v${pack.version}`} onClick={() => void act(pack.id, async () => {
            const next = await gov.setPolicyPackState(pack.id, state.action);
            return `${next.name} v${next.version} ${next.active ? 'activated' : 'archived'}.`;
          })}>{state.action === 'activate' ? <><Undo2 size={16} />Activate</> : <><Archive size={16} />Archive</>}</button>}
        </article>;
      })}
    </section>
    <div className="govColumns">
      <RoleTemplates templates={templates} owner={owner === true} busy={busy} act={act} />
      <ApprovalRules rules={rules} owner={owner === true} busy={busy} act={act} />
    </div>
  </>;
}

type ActProps = { owner: boolean; busy: string | null; act: (key: string, action: () => Promise<string>) => Promise<boolean> };

function RoleTemplates({ templates, owner, busy, act }: { templates: RoleTemplate[] } & ActProps) {
  const [name, setName] = useState('');
  const [perms, setPerms] = useState<string[]>(['task:read']);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    const body = roleTemplateBody(name, perms);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    if (await act('role-template', async () => `Role template ${(await gov.createRoleTemplate(body.value)).name} created.`)) { setName(''); setPerms(['task:read']); }
  };
  return <section className="surface govSection" aria-label="Role templates">
    <div className="sectionHeader"><div><p className="eyebrow">Access</p><h2>Role templates</h2></div></div>
    {templates.length === 0 && <p className="muted">No role templates yet.</p>}
    {templates.map((t) => <div className="govItem" key={t.id}><strong>{t.name}</strong><div className="tagRow">{t.permissions.map((p) => <span key={p}>{p}</span>)}</div></div>)}
    {owner && <form className="govForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label className="field"><span>Template name</span><input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} /></label>
      <fieldset className="govPermissions"><legend>Permissions</legend>{ROLE_TEMPLATE_PERMISSIONS.map((p) => <label key={p}><input type="checkbox" checked={perms.includes(p)} onChange={(e) => setPerms((old) => e.target.checked ? [...old, p] : old.filter((x) => x !== p))} />{p}</label>)}</fieldset>
      {error && <Notice error>{error}</Notice>}
      <div className="actions"><button type="submit" disabled={busy !== null}><Plus size={16} />Add role template</button></div>
    </form>}
  </section>;
}

function ApprovalRules({ rules, owner, busy, act }: { rules: ApprovalRule[] } & ActProps) {
  const [draft, setDraft] = useState({ name: '', actionPattern: '', minimumApprovers: '1', requiredRoles: 'owner' });
  const [error, setError] = useState<string | null>(null);
  const field = (key: keyof typeof draft, label: string) => <label className="field"><span>{label}</span><input value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} /></label>;
  const submit = async () => {
    const body = approvalRuleBody(draft);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    if (await act('approval-rule', async () => `Approval rule ${(await gov.createApprovalRule(body.value)).name} created.`)) setDraft({ name: '', actionPattern: '', minimumApprovers: '1', requiredRoles: 'owner' });
  };
  return <section className="surface govSection" aria-label="Approval rules">
    <div className="sectionHeader"><div><p className="eyebrow">Organization approvals</p><h2>Approval rules</h2></div></div>
    {rules.length === 0 && <p className="muted">No organization approval rules yet.</p>}
    {rules.map((r) => <div className="govItem" key={r.id}><strong>{r.name}</strong><p>{r.action_pattern} · {r.minimum_approvers} approver{r.minimum_approvers === 1 ? '' : 's'} · {r.required_roles.join(', ')}{r.enabled ? '' : ' · disabled'}</p></div>)}
    {owner && <form className="govForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      {field('name', 'Rule name')}{field('actionPattern', 'Action pattern')}{field('minimumApprovers', 'Minimum approvers')}{field('requiredRoles', 'Required roles (comma separated)')}
      {error && <Notice error>{error}</Notice>}
      <div className="actions"><button type="submit" disabled={busy !== null}><Plus size={16} />Add approval rule</button></div>
    </form>}
  </section>;
}

export function EnterpriseView() {
  const [ops, setOps] = useState<EnterpriseOperations | null>(null);
  const [packages, setPackages] = useState<MarketPackage[]>([]);
  const [targets, setTargets] = useState<RoutingTarget[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState<{ draft: TargetDraft; isNew: boolean } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const owner = useIsOwner();
  const load = useCallback(async () => {
    try {
      const [o, p, t] = await Promise.all([gov.getEnterpriseOperations(), apiRequest<MarketPackage[]>('/api/v1/marketplace/packages', { method: 'GET' }), gov.getRoutingTargets()]);
      setOps(o); setPackages(p); setTargets(t); setLoadError(null);
    } catch (error) { setLoadError(failure(error)); } finally { setLoaded(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const { busy, flash, act } = useAction(load);
  const regions = regionSummary(ops, targets);
  const save = async () => {
    if (!editing) return;
    const body = routingTargetBody(editing.draft);
    if (!body.ok) { setFormError(body.error); return; }
    if (editing.isNew && targets.some((t) => t.id === body.value.id)) { setFormError(`A routing target named ${body.value.id} already exists; edit it instead.`); return; }
    setFormError(null);
    if (await act('target', async () => `Routing target ${(await gov.saveRoutingTarget(body.value)).id} saved.`)) setEditing(null);
  };
  const set = (key: keyof TargetDraft, value: string) => editing && setEditing({ ...editing, draft: { ...editing.draft, [key]: value } });
  const input = (key: Exclude<keyof TargetDraft, 'status'>, label: string, disabled = false) => <label className="field"><span>{label}</span><input value={editing?.draft[key] ?? ''} disabled={disabled} onChange={(e) => set(key, e.target.value)} /></label>;
  return <>
    <section className="metrics"><Metric label="Regions" value={regions.regions} /><Metric label="Healthy targets" value={String(ops?.healthy_targets ?? 0)} /><Metric label="Installed packages" value={String(ops?.installed_packages ?? 0)} /><Metric label="Failover" value={regions.failover} detail={regions.detail} /></section>
    <section className="moduleIntro"><div><p className="eyebrow">Scale and ecosystem</p><h2>Enterprise operations</h2><p>Marketplace distribution, regional placement and constrained routing. ANUM reports only the regions you configure.</p></div><div className="actions">{owner && <button type="button" onClick={() => { setFormError(null); setEditing({ draft: emptyTargetDraft(), isNew: true }); }}><Plus size={17} />Add routing target</button>}<button type="button" className="secondary" onClick={() => void load()}><RefreshCw size={17} />Refresh</button></div></section>
    {!loaded && <Notice>Loading enterprise control plane...</Notice>}
    {loadError && <Notice error>{loadError}</Notice>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {editing && <form className="surface govSection govForm" aria-label="Routing target" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <div className="sectionHeader"><div><p className="eyebrow">Routing</p><h2>{editing.isNew ? 'New routing target' : `Edit ${editing.draft.id}`}</h2></div></div>
      <div className="govGrid">
        {input('id', 'Target id', !editing.isNew)}{input('region', 'Region')}{input('provider', 'Provider')}{input('model', 'Model')}
        <label className="field"><span>Status</span><select value={editing.draft.status} onChange={(e) => set('status', e.target.value as RegionStatus)}>{(['unverified', 'healthy', 'degraded', 'offline'] as const).map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        {input('modalities', 'Modalities (comma separated)')}{input('sensitivity', 'Sensitivity (comma separated)')}{input('cost', 'Cost per 1k tokens (USD)')}{input('latency', 'Latency (ms)')}
      </div>
      <p className="muted">The API has no delete for routing targets: set a target offline to take it out of routing.</p>
      {formError && <Notice error>{formError}</Notice>}
      <div className="actions"><button type="submit" disabled={busy !== null}>Save target</button><button type="button" className="secondary" onClick={() => setEditing(null)}>Cancel</button></div>
    </form>}
    {loaded && targets.length === 0 && <p className="muted">No routing targets are configured. This deployment runs in a single region on the workspace model configuration.</p>}
    <div className="integrationGrid">{targets.map((target) => <article className="surface integration govTarget" key={target.id}><div className="integrationIcon"><Route /></div><div><h3>{target.id}</h3><p>{target.region} · {target.provider}/{target.model}</p><small>{target.status === 'unverified' ? 'Cost and latency not measured yet' : `$${target.cost_per_1k_tokens}/1k · ${target.latency_ms} ms`}</small></div><span className={`statusBadge ${target.status === 'healthy' ? 'success' : target.status === 'offline' ? 'neutral' : 'warning'}`}>{target.status}</span>{owner && <button type="button" className="secondary" aria-label={`Edit ${target.id}`} onClick={() => { setFormError(null); setEditing({ draft: targetDraft(target), isNew: false }); }}><Pencil size={15} /></button>}</article>)}</div>
    <section className="surface approvalTable enterpriseCatalog"><div className="sectionHeader"><div><p className="eyebrow">Verified distribution</p><h2>Marketplace catalog</h2></div></div>{packages.length === 0 && <p className="muted">No packages have been published to this marketplace yet.</p>}{packages.map((item) => <div className="catalogRow" key={item.id}><PackageCheck /><div><strong>{item.name}</strong><p>{item.kind} · v{item.version} · {item.permissions.join(', ')}</p></div><button type="button" disabled={busy !== null} onClick={() => void act(`install-${item.id}`, async () => { await apiRequest(`/api/v1/marketplace/packages/${encodeURIComponent(item.id)}/install`, { method: 'POST', body: '{}' }); return `${item.name} installed.`; })}>Install</button></div>)}</section>
  </>;
}

function Metric({ label, value, detail = 'Tenant control plane' }: { label: string; value: string; detail?: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}
