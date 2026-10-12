// Customer organisations (docs/customer-organisations.md): the platform operator's console (onboard
// a customer company, list the organisations they run with counts and health only, change the plan,
// reissue the first admin's invitation) and the organisation's own branding (name and logo).
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { Building2, CircleDot, Copy, ImageUp, Lock, Mail, Plus, RefreshCw, Trash2 } from 'lucide-react';
import type { CreatedOrganisation, OrganisationBranding, OrganisationPlan, OrganisationSummary, ReissuedInvitation } from '@anum/contracts';
import * as team from './lib/teamApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { EMAIL_PATTERN, TENANT_ID_PATTERN, healthTone, organisationInvitationLink, slugify } from './lib/team';
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

  if (state.kind === 'denied') return <section className="surface adminForbidden teamPanel" aria-labelledby="orgs-denied">
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h3 id="orgs-denied">{t('orgs.deniedTitle')}</h3><p>{t('orgs.deniedText')}</p><p className="muted">{t('orgs.apiAnswered', { message: state.detail })}</p></div>
  </section>;
  return <div className="orgsView">
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('orgs.eyebrow')}</p><h2>{t('orgs.title')}</h2><p>{t('orgs.intro')}</p></div>
      <div className="actions"><button type="button" onClick={() => setCreating((v) => !v)} aria-expanded={creating}><Plus aria-hidden="true" />{t('orgs.onboard')}</button></div>
    </section>
    {creating && <OnboardForm onCancel={() => setCreating(false)} onCreated={created} />}
    {invite && <InvitationReveal {...invite} onDone={() => setInvite(null)} />}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {state.kind === 'error' && <section className="surface teamPanel"><Notice error>{t('orgs.loadFailed', { message: state.detail })}</Notice><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div></section>}
    {orgs === null && state.kind === 'ok' && <p className="muted" role="status">{t('team.loading')}</p>}
    {orgs && !orgs.length && <section className="surface teamPanel"><div className="emptyState"><Building2 aria-hidden="true" /><h3>{t('orgs.empty')}</h3><p>{t('orgs.emptyText')}</p></div></section>}
    {orgs && orgs.length > 0 && <ul className="orgList">{orgs.map((org) => <li key={org.tenant_id} className="surface orgCard">
      <div className="rowTitle"><h3>{org.display_name}</h3><span className={`statusBadge ${healthTone(org.health)}`}>{t(`orgs.health.${org.health}` as MessageKey)}</span></div>
      <p className="muted" dir="ltr">{org.tenant_id}</p>
      <p className="muted">{t(`orgs.healthHelp.${org.health}` as MessageKey)}</p>
      <dl className="orgCounts">
        <div><dt>{t('orgs.count.members')}</dt><dd>{org.active_members}</dd></div>
        <div><dt>{t('orgs.count.workspaces')}</dt><dd>{org.workspaces}</dd></div>
        <div><dt>{t('orgs.count.invitations')}</dt><dd>{org.pending_invitations}</dd></div>
        <div><dt>{t('orgs.count.work')}</dt><dd>{org.open_work_items}</dd></div>
        <div><dt>{t('orgs.count.overdue')}</dt><dd className={org.overdue_work_items ? 'overdueCell' : ''}>{org.overdue_work_items}</dd></div>
        <div><dt>{t('orgs.count.tasks')}</dt><dd>{org.open_agent_tasks}</dd></div>
        <div><dt>{t('orgs.lastActivity')}</dt><dd>{org.last_activity_at ? formatDateTime(org.last_activity_at) : t('orgs.never')}</dd></div>
        <div><dt>{t('orgs.created')}</dt><dd>{formatDateTime(org.created_at, { dateStyle: 'medium' })}</dd></div>
      </dl>
      <div className="orgActions">
        <label className="field"><span>{t('orgs.plan')}</span>
          <select value={org.plan} onChange={(e) => void setPlan(org, e.target.value as OrganisationPlan)}>{PLANS.map((p) => <option key={p} value={p}>{t(`orgs.plan.${p}` as MessageKey)}</option>)}</select></label>
        {org.active_members === 0 && <div className="actions"><button type="button" className="secondary" onClick={() => void reissue(org)}><Mail aria-hidden="true" />{t('orgs.reinvite')}</button></div>}
      </div>
    </li>)}</ul>}
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
  return <form className="surface settingsForm" onSubmit={submit} aria-labelledby="onboard-title" noValidate>
    <h2 id="onboard-title">{t('orgs.onboard')}</h2>
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
