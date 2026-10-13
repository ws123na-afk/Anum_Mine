// Customer organisations (docs/customer-organisations.md): the platform operator's console (onboard
// a customer company, list the organisations they run with counts and health only, change the plan,
// reissue the first admin's invitation) and the organisation's own branding (name and logo).
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Building2, CircleDot, Copy, ImageUp, Lock, Mail, Plus, RefreshCw, Trash2 } from 'lucide-react';
import type { CreatedOrganisation, OrganisationBranding, OrganisationPlan, OrganisationSummary, ReissuedInvitation } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { EMAIL_PATTERN, TENANT_ID_PATTERN, healthTone, organisationInvitationLink, organisationMatches, organisationTotals, slugify } from './lib/team';
import { figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { Modal } from './Dialog';
import { PageHeader, PageSummary, PaneProblem } from './PageParts';
import './team.css';

const PLANS: OrganisationPlan[] = ['trial', 'standard', 'enterprise'];
type Flash = { text: string; error?: boolean } | null;

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

async function copy(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

function webAppUrl(): string { return `${location.origin}${location.pathname}`; }

export function OrganisationsView() {
  const [orgs, setOrgs] = useState<OrganisationSummary[] | null>(null);
  const [state, setState] = useState<{ kind: 'ok' } | { kind: 'denied' | 'error'; detail: string }>({ kind: 'ok' });
  const [creating, setCreating] = useState(false);
  const [invite, setInvite] = useState<{ name: string; token: string; workspaceId: string; tenantId: string; expires: string } | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const load = useCallback(async () => {
    try { setOrgs(await team.getOperatedOrganisations()); setState({ kind: 'ok' }); }
    catch (e) { setState({ kind: isPermissionDenied(e) ? 'denied' : 'error', detail: describeError(e) }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const created = (result: CreatedOrganisation) => {
    setCreating(false);
    setInvite({ name: result.organisation.display_name, token: result.invitation_token, workspaceId: result.workspace_id, tenantId: result.organisation.tenant_id, expires: result.invitation_expires_at });
    void load();
  };
  const reissue = async (org: OrganisationSummary) => {
    try {
      const r: ReissuedInvitation = await team.reissueInvitation(org.tenant_id);
      setInvite({ name: org.display_name, token: r.invitation_token, workspaceId: r.workspace_id, tenantId: org.tenant_id, expires: r.invitation_expires_at });
      void load();
    } catch (e) { setFlash({ text: describeError(e), error: true }); }
  };
  const setPlan = async (org: OrganisationSummary, plan: OrganisationPlan) => {
    try { await team.setOrganisationPlan(org.tenant_id, plan); setFlash({ text: t('orgs.planSaved', { name: org.display_name }) }); void load(); }
    catch (e) { setFlash({ text: describeError(e), error: true }); }
  };

  const [query, setQuery] = useState('');
  const [health, setHealth] = useState<'' | OrganisationSummary['health']>('');
  const header = <PageHeader title={viewLabel('organisations')} subtitle={t('orgs.intro')} actions={state.kind !== 'denied' && <button type="button" aria-haspopup="dialog" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('orgs.onboard')}</button>} />;
  if (state.kind === 'denied') return <>{header}<section className="surface adminForbidden teamPanel" aria-labelledby="orgs-denied">
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h2 id="orgs-denied">{t('orgs.deniedTitle')}</h2><p>{t('orgs.deniedText')}</p><p className="muted">{t('orgs.apiAnswered', { message: state.detail })}</p></div>
  </section></>;
  const totals = orgs ? organisationTotals(orgs) : null;
  const load_ = { state: orgs ? 'ready' as const : state.kind === 'error' ? 'error' as const : 'loading' as const };
  const figures = [
    figure('orgs', t('pages2.orgs.fig.orgs'), load_, () => ({ value: String(totals!.count), hint: t('pages2.orgs.fig.workspaces', { count: totals!.workspaces }) })),
    figure('attention', t('pages2.orgs.fig.attention'), load_, () => ({ value: String(totals!.attention), hint: totals!.attention ? t('pages2.orgs.fig.attentionHint') : t('pages2.orgs.fig.allHealthy'), tone: totals!.attention ? 'danger' : 'ok' })),
    figure('members', t('orgs.count.members'), load_, () => ({ value: String(totals!.members), hint: t('pages2.orgs.fig.invitations', { count: totals!.invitations }) })),
    figure('overdue', t('orgs.count.overdue'), load_, () => ({ value: String(totals!.overdue), hint: t('pages2.orgs.fig.openWork', { count: totals!.openWork }), tone: totals!.overdue ? 'warn' : '' })),
  ];
  const shown = (orgs ?? []).filter((org) => (!health || org.health === health) && organisationMatches(org, query));
  return <div className="orgsView">
    {header}
    <PageSummary label={t('pages2.orgs.summary')} figures={figures} onRetry={() => void load()} />
    {creating && <Modal title={t('orgs.onboard')} className="formDialog orgDialog" onClose={() => setCreating(false)}><OnboardForm onCancel={() => setCreating(false)} onCreated={created} /></Modal>}
    {invite && <InvitationReveal {...invite} onDone={() => setInvite(null)} />}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {state.kind === 'error' && <PaneProblem text={t('orgs.loadFailed', { message: state.detail })} onRetry={() => void load()} />}
    {orgs === null && state.kind === 'ok' && <p className="muted" role="status">{t('team.loading')}</p>}
    {orgs && <section className="pane orgsTablePane" aria-labelledby="orgs-table-title">
      <div className="paneHead">
        <h2 id="orgs-table-title">{t('pages2.orgs.tableTitle', { count: orgs.length })}</h2>
        {orgs.length > 0 && <div className="toolbar orgsToolbar" role="search">
          <label className="visuallyHidden" htmlFor="orgs-search">{t('pages2.orgs.search')}</label>
          <input id="orgs-search" type="search" value={query} placeholder={t('pages2.orgs.search')} onChange={(e) => setQuery(e.target.value)} />
          <label className="visuallyHidden" htmlFor="orgs-health">{t('pages2.orgs.healthFilter')}</label>
          <select id="orgs-health" value={health} onChange={(e) => setHealth(e.target.value as typeof health)}>
            <option value="">{t('pages2.orgs.anyHealth')}</option>
            {(['attention', 'setting_up', 'active', 'idle'] as const).map((h) => <option key={h} value={h}>{t(`orgs.health.${h}` as MessageKey)}</option>)}
          </select>
        </div>}
      </div>
      {!orgs.length && <div className="paneEmpty"><Building2 aria-hidden="true" /><h3>{t('orgs.empty')}</h3><p>{t('orgs.emptyText')}</p><div className="actions"><button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('orgs.onboard')}</button></div></div>}
      {orgs.length > 0 && !shown.length && <div className="paneEmpty"><Building2 aria-hidden="true" /><h3>{t('pages2.orgs.noMatch')}</h3><div className="actions"><button type="button" className="secondary" onClick={() => { setQuery(''); setHealth(''); }}>{t('common.clear')}</button></div></div>}
      {shown.length > 0 && <div className="dataTableWrap"><table className="dataTable orgsTable">
        <thead><tr>
          <th scope="col">{t('pages2.orgs.col.organisation')}</th><th scope="col">{t('pages2.orgs.col.health')}</th>
          <th scope="col" className="num">{t('orgs.count.members')}</th><th scope="col" className="num optional">{t('orgs.count.workspaces')}</th><th scope="col" className="num optional">{t('orgs.count.invitations')}</th>
          <th scope="col" className="num">{t('orgs.count.work')}</th><th scope="col" className="num">{t('orgs.count.overdue')}</th><th scope="col" className="num optional">{t('orgs.count.tasks')}</th>
          <th scope="col" className="optional">{t('orgs.lastActivity')}</th><th scope="col">{t('orgs.plan')}</th>
        </tr></thead>
        <tbody>{shown.map((org) => <tr key={org.tenant_id}>
          <th scope="row"><strong dir="auto">{org.display_name}</strong><small dir="ltr">{org.tenant_id} · {t('pages2.orgs.since', { when: formatDateTime(org.created_at, { dateStyle: 'medium' }) })}</small></th>
          <td><span className={`statusBadge ${healthTone(org.health)}`} title={t(`orgs.healthHelp.${org.health}` as MessageKey)}>{t(`orgs.health.${org.health}` as MessageKey)}</span></td>
          <td className="num">{org.active_members}</td><td className="num optional">{org.workspaces}</td><td className="num optional">{org.pending_invitations}</td>
          <td className="num">{org.open_work_items}</td><td className={`num ${org.overdue_work_items ? 'overdueCell' : ''}`}>{org.overdue_work_items}</td><td className="num optional">{org.open_agent_tasks}</td>
          <td className="optional nowrap">{org.last_activity_at ? formatDateTime(org.last_activity_at) : t('orgs.never')}</td>
          <td><div className="orgPlanCell">
            <label className="visuallyHidden" htmlFor={`plan-${org.tenant_id}`}>{t('pages2.orgs.planFor', { name: org.display_name })}</label>
            <select id={`plan-${org.tenant_id}`} value={org.plan} onChange={(e) => void setPlan(org, e.target.value as OrganisationPlan)}>{PLANS.map((p) => <option key={p} value={p}>{t(`orgs.plan.${p}` as MessageKey)}</option>)}</select>
            {org.active_members === 0 && <button type="button" className="secondary" onClick={() => void reissue(org)}><Mail aria-hidden="true" />{t('orgs.reinvite')}</button>}
          </div></td>
        </tr>)}</tbody>
      </table></div>}
    </section>}
    <p className="muted orgPrivacy">{t('orgs.privacyNote')}</p>
  </div>;
}

function OnboardForm({ onCancel, onCreated }: { onCancel(): void; onCreated(result: CreatedOrganisation): void }) {
  const [name, setName] = useState('');
  const [tenantId, setTenantId] = useState('');
  const [idTouched, setIdTouched] = useState(false);
  const [workspace, setWorkspace] = useState('Main');
  const [email, setEmail] = useState('');
  const [plan, setPlan] = useState<OrganisationPlan>('trial');
  const [errors, setErrors] = useState<Record<string, MessageKey>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found: Record<string, MessageKey> = {};
    if (!name.trim()) found.name = 'orgs.error.name';
    if (!TENANT_ID_PATTERN.test(tenantId)) found.id = 'orgs.error.id';
    if (!workspace.trim()) found.workspace = 'orgs.error.workspace';
    if (!EMAIL_PATTERN.test(email.trim())) found.email = 'orgs.error.email';
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true); setProblem(null);
    try { onCreated(await team.createOrganisation({ tenant_id: tenantId, display_name: name.trim(), workspace_name: workspace.trim(), first_admin_email: email.trim(), plan })); }
    catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  };
  const err = (key: string, id: string) => errors[key] ? <small id={id} className="fieldError">{t(errors[key])}</small> : null;
  return <form className="settingsForm onboardForm" onSubmit={submit} noValidate>
    <p className="muted">{t('orgs.onboardHelp')}</p>
    <div className="formGrid">
      <div><label className="field"><span>{t('orgs.field.name')}</span><input value={name} maxLength={160} aria-invalid={Boolean(errors.name)} aria-describedby="org-name-error" onChange={(e) => { setName(e.target.value); if (!idTouched) setTenantId(slugify(e.target.value)); }} /></label>{err('name', 'org-name-error')}</div>
      <div><label className="field"><span>{t('orgs.field.id')}</span><input dir="ltr" value={tenantId} maxLength={80} aria-invalid={Boolean(errors.id)} aria-describedby="org-id-help" onChange={(e) => { setIdTouched(true); setTenantId(e.target.value.toLowerCase()); }} /></label><small id="org-id-help" className={errors.id ? 'fieldError' : 'muted'}>{t(errors.id ?? 'orgs.idHelp')}</small></div>
      <div><label className="field"><span>{t('orgs.field.workspace')}</span><input value={workspace} maxLength={160} aria-invalid={Boolean(errors.workspace)} aria-describedby="org-ws-error" onChange={(e) => setWorkspace(e.target.value)} /></label>{err('workspace', 'org-ws-error')}</div>
      <div><label className="field"><span>{t('orgs.field.email')}</span><input type="email" dir="ltr" value={email} maxLength={254} aria-invalid={Boolean(errors.email)} aria-describedby="org-email-error" onChange={(e) => setEmail(e.target.value)} /></label>{err('email', 'org-email-error')}</div>
      <div><label className="field"><span>{t('orgs.plan')}</span><select value={plan} onChange={(e) => setPlan(e.target.value as OrganisationPlan)} aria-describedby="plan-help">{PLANS.map((p) => <option key={p} value={p}>{t(`orgs.plan.${p}` as MessageKey)}</option>)}</select></label><small id="plan-help" className="muted">{t('orgs.planHelp')}</small></div>
    </div>
    <div className="actions"><button type="submit" disabled={busy}><Building2 aria-hidden="true" />{t('orgs.create')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    {problem && <Notice error>{problem}</Notice>}
  </form>;
}

function InvitationReveal({ name, token, workspaceId, tenantId, expires, onDone }: { name: string; token: string; workspaceId: string; tenantId: string; expires: string; onDone(): void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const link = organisationInvitationLink(webAppUrl(), token, workspaceId, tenantId);
  return <section className="surface settingsForm inviteReveal" aria-labelledby="invite-reveal-title">
    <h2 id="invite-reveal-title">{t('orgs.inviteTitle', { name })}</h2>
    <Notice>{t('orgs.inviteOnce', { when: formatDateTime(expires) })}</Notice>
    <label className="field"><span>{t('orgs.inviteLink')}</span><input dir="ltr" readOnly value={link} onFocus={(e) => e.target.select()} /></label>
    <label className="field"><span>{t('orgs.inviteToken')}</span><input dir="ltr" readOnly value={token} onFocus={(e) => e.target.select()} /></label>
    <p className="muted">{t('orgs.inviteSteps', { tenant: tenantId })}</p>
    <div className="actions">
      <button type="button" className="secondary" onClick={() => void copy(link).then((ok) => setCopied(ok ? t('orgs.copiedLink') : t('orgs.copyFailed')))}><Copy aria-hidden="true" />{t('orgs.copyLink')}</button>
      <button type="button" onClick={onDone}>{t('orgs.done')}</button>
    </div>
    {copied && <Notice>{copied}</Notice>}
  </section>;
}

/** Settings > Workspace: this organisation's name and logo (the org_admin changes them). */
export function BrandingSettings({ onChanged }: { onChanged?(): void }) {
  const [branding, setBranding] = useState<OrganisationBranding | null>(null);
  const [name, setName] = useState('');
  const [flash, setFlash] = useState<Flash>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const b = await team.getBranding();
      setBranding(b); setName(b.display_name);
      setPreview(b.has_logo ? await team.logoObjectUrl() : null);
    } catch (e) { setFlash({ text: describeError(e), error: true }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);
  const fail = (e: unknown) => setFlash({ text: isPermissionDenied(e) ? t('orgs.brandingDenied', { message: describeError(e) }) : describeError(e), error: true });
  const done = (text: string) => { setFlash({ text }); void load(); onChanged?.(); };
  return <section className="surface settingsForm brandingSettings" aria-labelledby="branding-title">
    <h2 id="branding-title">{t('orgs.branding')}</h2>
    <p className="muted">{t('orgs.brandingHelp')}</p>
    {!branding && !flash && <p className="muted" role="status">{t('team.loading')}</p>}
    {branding && <form onSubmit={(e) => { e.preventDefault(); if (!name.trim()) { setFlash({ text: t('orgs.error.name'), error: true }); return; } void team.setBrandingName(name.trim()).then(() => done(t('orgs.brandingSaved'))).catch(fail); }}>
      <div className="brandingRow">
        <div className="logoPreview">{preview ? <img src={preview} alt={t('orgs.logoAlt', { name: branding.display_name })} /> : <Building2 aria-hidden="true" />}</div>
        <label className="field"><span>{t('orgs.field.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={160} /></label>
      </div>
      <div className="actions">
        <button type="submit">{t('team.save')}</button>
        <label className="uploadButton secondaryUpload"><ImageUp aria-hidden="true" />{t('orgs.uploadLogo')}<input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; if (!file) return; if (file.size > 256 * 1024) { setFlash({ text: t('orgs.logoTooBig'), error: true }); return; } void team.uploadLogo(file).then(() => done(t('orgs.logoSaved'))).catch(fail); }} /></label>
        {branding.has_logo && <button type="button" className="secondary" onClick={() => void team.deleteLogo().then(() => done(t('orgs.logoRemoved'))).catch(fail)}><Trash2 aria-hidden="true" />{t('orgs.removeLogo')}</button>}
      </div>
    </form>}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
  </section>;
}
