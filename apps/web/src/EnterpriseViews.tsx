import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Archive, CircleDot, Download, Pencil, Plus, RefreshCw, Route, ShieldCheck, Undo2 } from 'lucide-react';
import { authHeaders } from './lib/api';
import { ApprovalRulesPanel } from './ApprovalRulesPanel';
import { MarketplaceCatalog } from './MarketplaceCatalog';
import { useIsOwner } from './useIsOwner';
import { apiErrorFromResponse, describeError, isPermissionDenied } from './lib/errors';
import * as gov from './lib/governanceApi';
import {
  ROLE_TEMPLATE_PERMISSIONS, emptyTargetDraft, enterpriseWriteAccess, organizationPolicyAccess, packState, regionSummary, roleTemplateBody, routingTargetBody, sortPacks, targetDraft,
  type ApprovalRule, type EnterpriseOperations, type GovernanceSummary, type PolicyPack, type RegionStatus, type RoleTemplate, type RoutingTarget, type TargetDraft,
} from './lib/governance';
import { t } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { Modal } from './Dialog';
import { AccessNotice, PageHeader, PageSummary, PaneProblem } from './PageParts';
import './governance.css';
import './adminPages.css';

const base = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';

type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

const failure = (error: unknown) => isPermissionDenied(error) ? t('admin.notAllowed', { message: describeError(error) }) : describeError(error);

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

export function GovernanceView() {
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [packs, setPacks] = useState<PolicyPack[]>([]);
  const [templates, setTemplates] = useState<RoleTemplate[]>([]);
  const [rules, setRules] = useState<ApprovalRule[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const load = useCallback(async () => {
    try {
      const [s, p, roleTemplates, r] = await Promise.all([gov.getGovernanceSummary(), gov.getPolicyPacks(), gov.getRoleTemplates(), gov.getApprovalRules()]);
      setSummary(s); setPacks(p); setTemplates(roleTemplates); setRules(r); setLoadError(null);
    } catch (error) { setLoadError(failure(error)); } finally { setLoaded(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const { busy, flash, act } = useAction(load);
  // Tenant-wide controls: editable only for an organization administrator (the API's answer).
  const access = organizationPolicyAccess(summary);
  const { canManage } = access;
  const addBaseline = () => act('baseline', async () => {
    const pack = await gov.createPolicyPack({ name: 'Organization baseline', description: 'Approval-first enterprise defaults', rules: [{ action: 'integration.write', effect: 'require_approval', conditions: { risk: 'high' } }] });
    return t('gov.packCreated', { name: pack.name, version: pack.version });
  });
  const exportAudit = () => act('audit', async () => {
    const response = await fetch(`${base}/api/v1/audit/export?format=json`, { headers: await authHeaders() });
    if (!response.ok) throw await apiErrorFromResponse(response, t('gov.exportFailed'));
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url; link.download = 'audit-export.json'; link.click();
    URL.revokeObjectURL(url);
    return t('gov.exported');
  });
  return <>
    <section className="metrics"><Metric label={t('gov.metric.packs')} value={String(summary?.policy_packs ?? 0)} detail={t('gov.metric.packsDetail')} /><Metric label={t('gov.metric.active')} value={String(summary?.active_policy_packs ?? 0)} detail={t('gov.metric.activeDetail')} /><Metric label={t('gov.metric.templates')} value={String(summary?.role_templates ?? 0)} detail={t('gov.metric.templatesDetail')} /><Metric label={t('gov.metric.rules')} value={String(summary?.approval_rules ?? 0)} detail={t('gov.metric.rulesDetail')} /></section>
    <section className="moduleIntro"><div><p>{t('gov.intro')}</p></div><div className="actions">{canManage && <button type="button" disabled={busy !== null} onClick={() => void addBaseline()}><ShieldCheck size={17} aria-hidden="true" />{t('gov.addBaseline')}</button>}<button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportAudit()}><Download size={17} aria-hidden="true" />{t('gov.exportAudit')}</button></div></section>
    {!loaded && <Notice>{t('gov.loading')}</Notice>}
    {loadError && <Notice error>{loadError}</Notice>}
    <AccessNotice notice={access.notice} howTo={access.howTo} />
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <section className="surface govSection" aria-label={t('gov.packs.title')}>
      <div className="sectionHeader"><div><p className="eyebrow">{t('gov.packs.eyebrow')}</p><h2>{t('gov.packs.title')}</h2></div></div>
      {loaded && packs.length === 0 && <p className="muted">{t('gov.packs.empty')}</p>}
      {sortPacks(packs).map((pack) => {
        const state = packState(pack);
        return <article className="govRow" key={pack.id}>
          <ShieldCheck aria-hidden="true" />
          <div><strong dir="auto">{pack.name}</strong> <span className="govVersion" dir="ltr">v{pack.version}</span><p>{pack.description ? <bdi>{pack.description}</bdi> : t('gov.packs.noDescription')} · {t('gov.packs.rules', { count: pack.rules.length })}</p><div className="tagRow">{pack.rules.map((rule) => <span key={`${rule.action}-${rule.effect}`} dir="ltr">{rule.action}: {rule.effect}</span>)}</div></div>
          <span className={`statusBadge ${state.tone}`}>{state.label}</span>
          {canManage && <button type="button" className="secondary" disabled={busy !== null} aria-label={t(state.action === 'activate' ? 'gov.packs.activateLabel' : 'gov.packs.archiveLabel', { name: pack.name, version: pack.version }, { isolate: false })} onClick={() => void act(pack.id, async () => {
            const next = await gov.setPolicyPackState(pack.id, state.action);
            return t(next.active ? 'gov.packs.activated' : 'gov.packs.archivedDone', { name: next.name, version: next.version });
          })}>{state.action === 'activate' ? <><Undo2 size={16} aria-hidden="true" />{t('gov.packs.activate')}</> : <><Archive size={16} aria-hidden="true" />{t('gov.packs.archive')}</>}</button>}
        </article>;
      })}
    </section>
    <ApprovalRulesPanel rules={rules} canManage={canManage} busy={busy} act={act} />
    <RoleTemplates templates={templates} canManage={canManage} busy={busy} act={act} />
  </>;
}

type ActProps = { canManage: boolean; busy: string | null; act: (key: string, action: () => Promise<string>) => Promise<boolean> };

function RoleTemplates({ templates, canManage, busy, act }: { templates: RoleTemplate[] } & ActProps) {
  const [name, setName] = useState('');
  const [perms, setPerms] = useState<string[]>(['task:read']);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    const body = roleTemplateBody(name, perms);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    if (await act('role-template', async () => t('gov.templates.created', { name: (await gov.createRoleTemplate(body.value)).name }))) { setName(''); setPerms(['task:read']); }
  };
  return <section className="surface govSection" aria-label={t('gov.templates.title')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('gov.templates.eyebrow')}</p><h2>{t('gov.templates.title')}</h2></div></div>
    {templates.length === 0 && <p className="muted">{t('gov.templates.empty')}</p>}
    {templates.map((template) => <div className="govItem" key={template.id}><strong dir="auto">{template.name}</strong><div className="tagRow">{template.permissions.map((p) => <span key={p} dir="ltr">{p}</span>)}</div></div>)}
    {canManage && <form className="govForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label className="field"><span>{t('gov.templates.name')}</span><input dir="auto" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} /></label>
      <fieldset className="govPermissions"><legend>{t('gov.templates.permissions')}</legend>{ROLE_TEMPLATE_PERMISSIONS.map((p) => <label key={p}><input type="checkbox" checked={perms.includes(p)} onChange={(e) => setPerms((old) => e.target.checked ? [...old, p] : old.filter((x) => x !== p))} /><span dir="ltr">{p}</span></label>)}</fieldset>
      {error && <Notice error>{error}</Notice>}
      <div className="actions"><button type="submit" disabled={busy !== null}><Plus size={16} aria-hidden="true" />{t('gov.templates.add')}</button></div>
    </form>}
  </section>;
}

export function EnterpriseView() {
  const [ops, setOps] = useState<EnterpriseOperations | null>(null);
  const [targets, setTargets] = useState<RoutingTarget[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState<{ draft: TargetDraft; isNew: boolean } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  // Installing a catalog package is a workspace-owner action; the catalog and routing
  // targets themselves are tenant-wide and need the organization administrator role.
  const owner = useIsOwner();
  const load = useCallback(async () => {
    // The capability check is separate so a failure there still shows the data, read only.
    const capability = gov.getGovernanceSummary()
      .then((s) => { setSummary(s); setCapabilityError(null); })
      .catch((error: unknown) => { setSummary(null); setCapabilityError(failure(error)); });
    try {
      const [o, routing] = await Promise.all([gov.getEnterpriseOperations(), gov.getRoutingTargets()]);
      setOps(o); setTargets(routing); setLoadError(null);
    } catch (error) { setLoadError(failure(error)); } finally { await capability; setLoaded(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const { busy, flash, act } = useAction(load);
  const access = enterpriseWriteAccess(summary);
  const { canManage } = access;
  const regions = regionSummary(ops, targets);
  const save = async () => {
    if (!editing) return;
    const body = routingTargetBody(editing.draft);
    if (!body.ok) { setFormError(body.error); return; }
    if (editing.isNew && targets.some((target) => target.id === body.value.id)) { setFormError(t('ent.exists', { id: body.value.id })); return; }
    setFormError(null);
    if (await act('target', async () => t('ent.saved', { id: (await gov.saveRoutingTarget(body.value)).id }))) setEditing(null);
  };
  const set = (key: keyof TargetDraft, value: string) => editing && setEditing({ ...editing, draft: { ...editing.draft, [key]: value } });
  const input = (key: Exclude<keyof TargetDraft, 'status'>, label: string, disabled = false) => <label className="field"><span>{label}</span><input dir="ltr" value={editing?.draft[key] ?? ''} disabled={disabled} onChange={(e) => set(key, e.target.value)} /></label>;
  const opsLoad = { state: loadError ? 'error' as const : ops ? 'ready' as const : 'loading' as const };
  const figures: Figure[] = [
    figure('regions', t('ent.metric.regions'), opsLoad, () => ({ value: regions.regions, hint: t('pages2.ent.fig.targets', { count: targets.length }) })),
    figure('healthy', t('ent.metric.healthy'), opsLoad, () => ({ value: String(ops!.healthy_targets), hint: ops!.degraded_targets ? t('pages2.ent.fig.degraded', { count: ops!.degraded_targets }) : t('pages2.ent.fig.noneDegraded'), tone: ops!.degraded_targets ? 'warn' : '' })),
    figure('packages', t('ent.metric.packages'), opsLoad, () => ({ value: String(ops!.installed_packages), hint: t('pages2.ent.fig.packagesHint') })),
    figure('failover', t('ent.metric.failover'), opsLoad, () => ({ value: regions.failover, hint: regions.detail })),
  ];
  const newTarget = () => { setFormError(null); setEditing({ draft: emptyTargetDraft(), isNew: true }); };
  return <>
    <PageHeader title={viewLabel('enterprise')} subtitle={t('ent.intro')} actions={<>
      {canManage && <button type="button" aria-haspopup="dialog" onClick={newTarget}><Plus size={17} aria-hidden="true" />{t('ent.addTarget')}</button>}
      <button type="button" className="secondary" onClick={() => void load()}><RefreshCw size={17} aria-hidden="true" />{t('ent.refresh')}</button>
    </>} />
    <PageSummary label={t('pages2.ent.summary')} figures={figures} onRetry={() => void load()} />
    {capabilityError && <Notice error>{t('ent.capabilityError', { message: capabilityError })}</Notice>}
    <AccessNotice notice={access.notice} howTo={access.howTo} />
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {editing && canManage && <Modal title={editing.isNew ? t('ent.form.new') : t('ent.form.edit', { id: editing.draft.id })} className="formDialog entDialog" onClose={() => setEditing(null)}>
      <form className="govForm" aria-label={t('ent.form.label')} onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <div className="govGrid">
          {input('id', t('ent.field.id'), !editing.isNew)}{input('region', t('ent.field.region'))}{input('provider', t('ent.field.provider'))}{input('model', t('ent.field.model'))}
          <label className="field"><span>{t('ent.field.status')}</span><select value={editing.draft.status} onChange={(e) => set('status', e.target.value as RegionStatus)}>{(['unverified', 'healthy', 'degraded', 'offline'] as const).map((s) => <option key={s} value={s}>{t(`ent.status.${s}`)}</option>)}</select></label>
          {input('modalities', t('ent.field.modalities'))}{input('sensitivity', t('ent.field.sensitivity'))}{input('cost', t('ent.field.cost'))}{input('latency', t('ent.field.latency'))}
        </div>
        <p className="muted">{t('ent.noDelete')}</p>
        {formError && <Notice error>{formError}</Notice>}
        <div className="actions"><button type="submit" disabled={busy !== null}>{t('ent.save')}</button><button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
      </form>
    </Modal>}
    <section className="pane entTargets" aria-labelledby="ent-targets-title">
      <div className="paneHead"><h2 id="ent-targets-title">{t('pages2.ent.targets')}</h2>{loaded && <span className="muted">{t('pages2.ent.fig.targets', { count: targets.length })}</span>}</div>
      {!loaded && <div className="paneBody"><Notice>{t('ent.loading')}</Notice></div>}
      {loadError && <div className="paneBody"><PaneProblem text={loadError} onRetry={() => void load()} /></div>}
      {loaded && !loadError && targets.length === 0 && <div className="paneEmpty"><Route aria-hidden="true" /><h3>{t('pages2.ent.noTargets')}</h3><p>{t('ent.empty')}</p>{canManage && <div className="actions"><button type="button" onClick={newTarget}><Plus aria-hidden="true" />{t('ent.addTarget')}</button></div>}</div>}
      {targets.length > 0 && <div className="dataTableWrap"><table className="dataTable">
        <thead><tr><th scope="col">{t('ent.field.id')}</th><th scope="col" className="entWide">{t('ent.field.region')}</th><th scope="col">{t('pages2.ent.col.model')}</th><th scope="col" className="optional">{t('ent.field.modalities')}</th><th scope="col">{t('ent.field.status')}</th><th scope="col" className="optional">{t('pages2.ent.col.costLatency')}</th><th scope="col"><span className="visuallyHidden">{t('pages2.assistants.col.actions')}</span></th></tr></thead>
        <tbody>{targets.map((target) => <tr key={target.id}>
          <th scope="row"><code dir="ltr">{target.id}</code><small className="entNarrow" dir="ltr">{target.region}</small></th>
          <td className="entWide" dir="ltr">{target.region}</td>
          <td dir="ltr">{target.provider}/{target.model}</td>
          <td className="optional" dir="ltr">{target.modalities.join(', ')}<small>{target.sensitivity.join(', ')}</small></td>
          <td><span className={`statusBadge ${target.status === 'healthy' ? 'success' : target.status === 'offline' ? 'neutral' : 'warning'}`}>{t(`ent.status.${target.status}`)}</span></td>
          <td className="optional">{target.status === 'unverified' ? <span className="muted">{t('ent.notMeasured')}</span> : t('ent.costLatency', { cost: String(target.cost_per_1k_tokens), latency: target.latency_ms })}</td>
          <td><div className="cellActions">{canManage && <button type="button" className="iconButton" aria-label={t('ent.editLabel', { id: target.id }, { isolate: false })} title={t('ent.edit')} onClick={() => { setFormError(null); setEditing({ draft: targetDraft(target), isNew: false }); }}><Pencil aria-hidden="true" /></button>}</div></td>
        </tr>)}</tbody>
      </table></div>}
    </section>
    <MarketplaceCatalog owner={owner} canManage={canManage} onChanged={() => void load()} />
  </>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}
