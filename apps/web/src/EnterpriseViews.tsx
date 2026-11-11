import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { Activity, Archive, ChevronDown, ChevronUp, CircleDot, Download, GitCompare, Pencil, Plus, Power, RefreshCw, Route, ShieldCheck, Trash2, Undo2, Users } from 'lucide-react';
import { authHeaders } from './lib/api';
import { ApprovalRulesPanel } from './ApprovalRulesPanel';
import { MarketplaceCatalog } from './MarketplaceCatalog';
import { MemoryGovernanceCard } from './MemoryGovernanceCard';
import { CompareVersions, PackEditor } from './PolicyPackEditor';
import { useIsOwner } from './useIsOwner';
import { apiErrorFromResponse, describeError, isPermissionDenied } from './lib/errors';
import * as gov from './lib/governanceApi';
import {
  ROLE_TEMPLATE_PERMISSIONS, deleteBlocker, emptyTargetDraft, enterpriseWriteAccess, filterPacks, filterTemplates, organizationPolicyAccess, packGroups, packState, regionSummary, roleTemplateBody, routingTargetBody, ruleLabel, targetDraft, targetHealth,
  type ApprovalRule, type EnterpriseOperations, type GovernanceSummary, type PackGroup, type PolicyPack, type RoleTemplate, type RoutingTarget, type TargetDraft,
} from './lib/governance';
import { pageOf } from './lib/catalog';
import { formatDateTime, t } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { ConfirmDialog, Modal } from './Dialog';
import { AccessNotice, LoadingSkeleton, PageHeader, PageSummary, PaneProblem } from './PageParts';
import './governance.css';
import './adminPages.css';

const base = import.meta.env.VITE_ANUM_API_URL ?? 'http://localhost:8000';
const PAGE = 10;

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

/** A search field over a list, with the "N of M shown" count read out as it changes. */
function ListSearch({ label, placeholder, value, count, onChange }: { label: string; placeholder: string; value: string; count: string; onChange: (value: string) => void }) {
  const id = useId();
  return <div className="govToolbar" role="search">
    <label className="field" htmlFor={id}><span>{label}</span><input id={id} type="search" dir="auto" value={value} placeholder={placeholder} autoComplete="off" onChange={(e) => onChange(e.target.value)} /></label>
    <p className="muted" aria-live="polite">{count}</p>
  </div>;
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
  const [editor, setEditor] = useState<{ basis: PolicyPack | null; nextVersion: number } | null>(null);
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
    <section className="moduleIntro"><div><p>{t('gov.intro')}</p></div><div className="actions">{canManage && <button type="button" disabled={busy !== null} aria-haspopup="dialog" onClick={() => setEditor({ basis: null, nextVersion: 1 })}><ShieldCheck size={17} aria-hidden="true" />{t('gov.newPack')}</button>}<button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportAudit()}><Download size={17} aria-hidden="true" />{t('gov.exportAudit')}</button></div></section>
    {!loaded && <LoadingSkeleton label={t('gov.loading')} />}
    {loadError && <PaneProblem text={loadError} onRetry={() => void load()} />}
    <AccessNotice notice={access.notice} howTo={access.howTo} />
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {editor && canManage && <PackEditor basis={editor.basis} nextVersion={editor.nextVersion} busy={busy} act={act} onClose={() => setEditor(null)} />}
    {loaded && <PolicyPacks packs={packs} canManage={canManage} busy={busy} act={act} onNewVersion={(group) => setEditor({ basis: group.versions[0], nextVersion: group.versions[0].version + 1 })} onNew={() => setEditor({ basis: null, nextVersion: 1 })} />}
    {loaded && <ApprovalRulesPanel rules={rules} canManage={canManage} busy={busy} act={act} />}
    {loaded && <RoleTemplates templates={templates} canManage={canManage} busy={busy} act={act} />}
    {loaded && <MemoryGovernanceCard canManage={canManage} busy={busy} act={act} />}
  </>;
}

type ActProps = { canManage: boolean; busy: string | null; act: (key: string, action: () => Promise<string>) => Promise<boolean> };

function PolicyPacks({ packs, canManage, busy, act, onNewVersion, onNew }: { packs: PolicyPack[]; onNewVersion: (group: PackGroup) => void; onNew: () => void } & ActProps) {
  const [query, setQuery] = useState('');
  const [pages, setPages] = useState(1);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [comparing, setComparing] = useState<PackGroup | null>(null);
  const [deleting, setDeleting] = useState<PolicyPack | null>(null);
  useEffect(() => setPages(1), [query]);
  const groups = packGroups(filterPacks(packs, query));
  const { shown, remaining } = pageOf(groups, pages, PAGE);
  const toggle = (name: string) => setOpen((old) => { const next = new Set(old); if (next.has(name)) next.delete(name); else next.add(name); return next; });
  const remove = async () => {
    if (!deleting) return;
    const pack = deleting;
    if (await act(`delete-${pack.id}`, async () => { await gov.deletePolicyPack(pack.id); return t('gov.packs.deleted', { name: pack.name, version: pack.version }); })) setDeleting(null);
  };
  const stateButton = (pack: PolicyPack) => {
    const state = packState(pack);
    return <button type="button" className="secondary" disabled={busy !== null} aria-label={t(state.action === 'activate' ? 'gov.packs.activateLabel' : 'gov.packs.archiveLabel', { name: pack.name, version: pack.version }, { isolate: false })} onClick={() => void act(pack.id, async () => {
      const next = await gov.setPolicyPackState(pack.id, state.action);
      return t(next.active ? 'gov.packs.activated' : 'gov.packs.archivedDone', { name: next.name, version: next.version });
    })}>{state.action === 'activate' ? <><Undo2 size={16} aria-hidden="true" />{t('gov.packs.activate')}</> : <><Archive size={16} aria-hidden="true" />{t('gov.packs.archive')}</>}</button>;
  };
  return <section className="surface govSection" aria-label={t('gov.packs.title')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('gov.packs.eyebrow')}</p><h2>{t('gov.packs.title')}</h2></div></div>
    {packs.length === 0 && <div className="emptyState"><ShieldCheck aria-hidden="true" /><h3>{t('gov.packs.title')}</h3>
      {canManage ? <><p>{t('gov.packs.emptyAdmin')}</p><button type="button" aria-haspopup="dialog" onClick={onNew}><Plus size={16} aria-hidden="true" />{t('gov.newPack')}</button></> : <p>{t('gov.packs.emptyOther')}</p>}</div>}
    {packs.length > 0 && <ListSearch label={t('gov.packs.search')} placeholder={t('gov.packs.searchPlaceholder')} value={query} count={t('gov.packs.count', { shown: groups.length, count: packGroups(packs).length })} onChange={setQuery} />}
    {packs.length > 0 && groups.length === 0 && <p className="muted">{t('gov.packs.noMatch', { query })}</p>}
    {shown.map((group) => {
      const current = group.active ?? group.versions[0];
      const expanded = open.has(group.name);
      return <article className="govRow govPack" key={group.name}>
        <ShieldCheck aria-hidden="true" />
        <div className="govPackMain">
          <strong dir="auto">{group.name}</strong> <span className="govVersion" dir="ltr">v{current.version}</span>
          <p>{current.description ? <bdi>{current.description}</bdi> : t('gov.packs.noDescription')} · {t('gov.packs.rules', { count: current.rules.length })} · {t('gov.packs.versions', { count: group.versions.length })}</p>
          <div className="tagRow">{current.rules.map((rule, index) => <span key={`${rule.action}-${index}`} dir="ltr">{ruleLabel(rule)}</span>)}</div>
          <div className="govPackActions">
            <button type="button" className="secondary" aria-expanded={expanded} aria-label={t('gov.packs.versionsOf', { versions: t('gov.packs.versions', { count: group.versions.length }), name: group.name }, { isolate: false })} onClick={() => toggle(group.name)}>{expanded ? <ChevronUp size={16} aria-hidden="true" /> : <ChevronDown size={16} aria-hidden="true" />}{t('gov.packs.versions', { count: group.versions.length })}</button>
            {group.versions.length > 1 && <button type="button" className="secondary" aria-haspopup="dialog" aria-label={t('gov.packs.compareLabel', { name: group.name }, { isolate: false })} onClick={() => setComparing(group)}><GitCompare size={16} aria-hidden="true" />{t('gov.packs.compare')}</button>}
            {canManage && <button type="button" className="secondary" aria-haspopup="dialog" disabled={busy !== null} aria-label={t('gov.packs.newVersionLabel', { name: group.name }, { isolate: false })} onClick={() => onNewVersion(group)}><Pencil size={16} aria-hidden="true" />{t('gov.packs.newVersion')}</button>}
          </div>
          {expanded && <ul className="govVersions" aria-label={t('gov.packs.versionsOf', { versions: t('gov.packs.versions', { count: group.versions.length }), name: group.name }, { isolate: false })}>
            {group.versions.map((pack) => <li key={pack.id}>
              <span className="govVersion" dir="ltr">v{pack.version}</span>
              <span className="muted">{t('gov.packs.by', { version: pack.version, when: formatDateTime(pack.created_at) })} · {t('gov.packs.rules', { count: pack.rules.length })}</span>
              <span className={`statusBadge ${packState(pack).tone}`}>{packState(pack).label}</span>
              {canManage && <span className="govVersionActions">{stateButton(pack)}{!pack.active && <button type="button" className="secondary" disabled={busy !== null} aria-label={t('gov.packs.deleteLabel', { name: pack.name, version: pack.version }, { isolate: false })} onClick={() => setDeleting(pack)}><Trash2 size={16} aria-hidden="true" />{t('gov.packs.delete')}</button>}</span>}
            </li>)}
          </ul>}
        </div>
        <span className={`statusBadge ${group.active ? 'success' : 'neutral'}`}>{group.active ? t('gov.packs.activeVersion', { version: group.active.version }) : t('gov.packs.noActive')}</span>
        {canManage && stateButton(current)}
      </article>;
    })}
    {remaining > 0 && <div className="actions"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>{t('gov.packs.showMore', { count: Math.min(remaining, PAGE) })}</button></div>}
    {comparing && <CompareVersions name={comparing.name} versions={comparing.versions} onClose={() => setComparing(null)} />}
    {deleting && <ConfirmDialog title={t('gov.packs.deleteTitle', { name: deleting.name, version: deleting.version })} confirmLabel={t('gov.packs.delete')} danger busy={busy !== null} onConfirm={() => void remove()} onCancel={() => setDeleting(null)}>
      <p>{t('gov.packs.deleteBody')}</p>
    </ConfirmDialog>}
  </section>;
}

function RoleTemplates({ templates, canManage, busy, act }: { templates: RoleTemplate[] } & ActProps) {
  const [name, setName] = useState('');
  const [perms, setPerms] = useState<string[]>(['task:read']);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [query]);
  const matching = filterTemplates(templates, query);
  const { shown, remaining } = pageOf(matching, pages, PAGE);
  const submit = async () => {
    const body = roleTemplateBody(name, perms);
    if (!body.ok) { setError(body.error); return; }
    setError(null);
    if (await act('role-template', async () => t('gov.templates.created', { name: (await gov.createRoleTemplate(body.value)).name }))) { setName(''); setPerms(['task:read']); }
  };
  return <section className="surface govSection" aria-label={t('gov.templates.title')}>
    <div className="sectionHeader"><div><p className="eyebrow">{t('gov.templates.eyebrow')}</p><h2>{t('gov.templates.title')}</h2><p className="muted">{t('gov.templates.howUsed')}</p></div>
      {templates.length > 0 && <button type="button" className="secondary" onClick={() => { location.hash = '#members'; }}><Users size={16} aria-hidden="true" />{t('gov.templates.assign')}</button>}</div>
    {templates.length === 0 && <p className="muted">{canManage ? t('gov.templates.emptyAdmin') : t('gov.templates.emptyOther')}</p>}
    {templates.length > 0 && <ListSearch label={t('gov.templates.search')} placeholder={t('gov.templates.searchPlaceholder')} value={query} count={t('gov.templates.count', { shown: matching.length, count: templates.length })} onChange={setQuery} />}
    {templates.length > 0 && matching.length === 0 && <p className="muted">{t('gov.templates.noMatch', { query })}</p>}
    {shown.map((template) => <div className="govItem" key={template.id}><strong dir="auto">{template.name}</strong> <span className="muted">{t('gov.templates.permissionsCount', { count: template.permissions.length })}</span><div className="tagRow">{template.permissions.map((p) => <span key={p} dir="ltr">{p}</span>)}</div></div>)}
    {remaining > 0 && <div className="actions"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>{t('gov.templates.showMore', { count: Math.min(remaining, PAGE) })}</button></div>}
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
  const [deleting, setDeleting] = useState<RoutingTarget | null>(null);
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
  const probe = (target: RoutingTarget) => act(`probe-${target.id}`, async () => {
    const probed = await gov.probeRoutingTarget(target.id);
    return t('ent.probed', { id: probed.id, status: targetHealth(probed).label });
  });
  const setEnabled = (target: RoutingTarget, enabled: boolean) => act(`enable-${target.id}`, async () => {
    const saved = await gov.saveRoutingTarget({ ...target, enabled });
    return t(enabled ? 'ent.putBackDone' : 'ent.tookOut', { id: saved.id });
  });
  const remove = async () => {
    if (!deleting) return;
    const target = deleting;
    if (await act(`delete-${target.id}`, async () => { await gov.deleteRoutingTarget(target.id); return t('ent.deleted', { id: target.id }); })) setDeleting(null);
  };
  const set = (key: keyof TargetDraft, value: string | boolean) => editing && setEditing({ ...editing, draft: { ...editing.draft, [key]: value } });
  const input = (key: Exclude<keyof TargetDraft, 'status' | 'enabled'>, label: string, disabled = false) => <label className="field"><span>{label}</span><input dir="ltr" value={editing?.draft[key] ?? ''} disabled={disabled} onChange={(e) => set(key, e.target.value)} /></label>;
  const opsLoad = { state: loadError ? 'error' as const : ops ? 'ready' as const : 'loading' as const };
  const unprobed = ops?.unprobed_targets ?? 0;
  const figures: Figure[] = [
    figure('regions', t('ent.metric.regions'), opsLoad, () => ({ value: regions.regions, hint: t('pages2.ent.fig.targets', { count: targets.length }) })),
    figure('healthy', t('ent.metric.healthy'), opsLoad, () => ({ value: String(ops!.healthy_targets), hint: unprobed ? t('ent.fig.unprobed', { count: unprobed }) : ops!.degraded_targets ? t('pages2.ent.fig.degraded', { count: ops!.degraded_targets }) : t('pages2.ent.fig.noneDegraded'), tone: ops!.degraded_targets || unprobed ? 'warn' : '' })),
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
          {input('modalities', t('ent.field.modalities'))}{input('sensitivity', t('ent.field.sensitivity'))}{input('cost', t('ent.field.cost'))}{input('latency', t('ent.field.latency'))}
        </div>
        <label className="integrationToggle ruleCheck"><input type="checkbox" checked={editing.draft.enabled} onChange={(e) => set('enabled', e.target.checked)} /><span>{t('ent.field.enabled')}</span></label>
        <p className="muted">{t('ent.enabledHint')} {t('ent.statusHint')}</p>
        {formError && <Notice error>{formError}</Notice>}
        <div className="actions"><button type="submit" disabled={busy !== null}>{t('ent.save')}</button><button type="button" className="secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button></div>
      </form>
    </Modal>}
    {deleting && <ConfirmDialog title={t('ent.deleteTitle', { id: deleting.id })} confirmLabel={t('ent.delete')} danger busy={busy !== null} onConfirm={() => void remove()} onCancel={() => setDeleting(null)}>
      <p>{t('ent.deleteBody')}</p>
    </ConfirmDialog>}
    <section className="pane entTargets" aria-labelledby="ent-targets-title">
      <div className="paneHead"><h2 id="ent-targets-title">{t('pages2.ent.targets')}</h2>{loaded && <span className="muted">{t('pages2.ent.fig.targets', { count: targets.length })}</span>}</div>
      {!loaded && <div className="paneBody"><Notice>{t('ent.loading')}</Notice></div>}
      {loadError && <div className="paneBody"><PaneProblem text={loadError} onRetry={() => void load()} /></div>}
      {loaded && !loadError && targets.length === 0 && <div className="paneEmpty"><Route aria-hidden="true" /><h3>{t('pages2.ent.noTargets')}</h3><p>{t('ent.empty')}</p>{canManage && <div className="actions"><button type="button" onClick={newTarget}><Plus aria-hidden="true" />{t('ent.addTarget')}</button></div>}</div>}
      {targets.length > 0 && <div className="dataTableWrap"><table className="dataTable">
        <thead><tr><th scope="col">{t('ent.field.id')}</th><th scope="col" className="entWide">{t('ent.field.region')}</th><th scope="col" className="entWide">{t('pages2.ent.col.model')}</th><th scope="col" className="optional">{t('ent.field.modalities')}</th><th scope="col">{t('ent.col.health')}</th><th scope="col" className="optional">{t('pages2.ent.col.costLatency')}</th><th scope="col"><span className="visuallyHidden">{t('pages2.assistants.col.actions')}</span></th></tr></thead>
        <tbody>{targets.map((target) => {
          const health = targetHealth(target);
          const blocker = deleteBlocker(target);
          return <tr key={target.id}>
            <th scope="row"><code dir="ltr">{target.id}</code><small className="entNarrow" dir="ltr">{target.region}</small><small className="entNarrow" dir="ltr">{target.provider}/{target.model}</small></th>
            <td className="entWide" dir="ltr">{target.region}</td>
            <td className="entWide" dir="ltr">{target.provider}/{target.model}</td>
            <td className="optional" dir="ltr">{target.modalities.join(', ')}<small>{target.sensitivity.join(', ')}</small></td>
            <td><span className={`statusBadge ${health.tone === 'danger' ? 'dangerTone' : health.tone}`}>{health.label}</span><small className="entHealthDetail" dir="auto">{health.detail}</small></td>
            <td className="optional">{target.last_checked_at && target.measured_latency_ms != null ? t('ent.costLatencyMeasured', { cost: String(target.cost_per_1k_tokens), latency: target.measured_latency_ms }) : <span className="muted">{t('ent.notMeasured')}</span>}</td>
            <td><div className="cellActions">
              {canManage && <button type="button" className="iconButton" disabled={busy !== null || !(target.enabled ?? true)} aria-label={t('ent.probeLabel', { id: target.id }, { isolate: false })} title={t('ent.probe')} onClick={() => void probe(target)}><Activity aria-hidden="true" /></button>}
              {canManage && <button type="button" className="iconButton" disabled={busy !== null} aria-label={t((target.enabled ?? true) ? 'ent.takeOutLabel' : 'ent.putBackLabel', { id: target.id }, { isolate: false })} title={(target.enabled ?? true) ? t('ent.takeOut') : t('ent.putBack')} onClick={() => void setEnabled(target, !(target.enabled ?? true))}><Power aria-hidden="true" /></button>}
              {canManage && <button type="button" className="iconButton" aria-label={t('ent.editLabel', { id: target.id }, { isolate: false })} title={t('ent.edit')} onClick={() => { setFormError(null); setEditing({ draft: targetDraft(target), isNew: false }); }}><Pencil aria-hidden="true" /></button>}
              {canManage && <button type="button" className="iconButton" disabled={busy !== null || blocker !== null} aria-label={t('ent.deleteLabel', { id: target.id }, { isolate: false })} title={blocker ?? t('ent.delete')} onClick={() => setDeleting(target)}><Trash2 aria-hidden="true" /></button>}
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}
      {loaded && targets.length > 0 && <p className="muted entProbeNote">{t('ent.probeNote')}</p>}
    </section>
    <MarketplaceCatalog owner={owner} canManage={canManage} onChanged={() => void load()} />
  </>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="metric"><div className="metricTop"><span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}
