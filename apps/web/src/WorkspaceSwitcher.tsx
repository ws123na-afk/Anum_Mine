// Top-bar workspace switcher (docs/identity.md, Workspace switcher). Lists the caller's memberships
// from the API plus the workspaces the client knows (src/lib/workspaces.ts), and switches only
// after the API confirms an active membership in the target.
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowRightLeft, Building2, ChevronDown, X } from 'lucide-react';
import type { MyOrganisation } from '@anum/contracts';
import * as api from './lib/api';
import { getMyOrganisations } from './lib/teamApi';
import { describeError } from './lib/errors';
import { membershipLabel, scopeNames, type MembershipEntry } from './lib/workspaces';
import { t } from './lib/i18n';

const sourceLabel = (source: 'member' | 'default' | 'remembered') => t(`switcher.source.${source}`);

type Directory = { state: 'loading' } | { state: 'loaded'; memberships: MembershipEntry[] } | { state: 'unavailable' };

export function WorkspaceSwitcher({ tenantId, workspaceId, onSwitch, onSwitchOrganisation }: { tenantId: string; workspaceId: string; onSwitch: (workspaceId: string) => Promise<void>; onSwitchOrganisation?: (tenantId: string, workspaceId: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  // Other organisations the caller belongs to (GET /api/v1/me/organizations); null until loaded or when unavailable.
  const [organisations, setOrganisations] = useState<MyOrganisation[] | null>(null);
  const [directory, setDirectory] = useState<Directory>({ state: 'loading' });
  const [, setVersion] = useState(0);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();

  // The organisations (with their workspaces' names) load once up front, so the button shows names, not ids.
  useEffect(() => {
    let live = true;
    getMyOrganisations().then((orgs) => { if (live && Array.isArray(orgs)) setOrganisations(orgs); }).catch(() => undefined);
    return () => { live = false; };
  }, [tenantId]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setDirectory({ state: 'loading' });
    api.listMyWorkspaces()
      .then((memberships) => { if (live) setDirectory({ state: 'loaded', memberships }); })
      .catch(() => { if (live) setDirectory({ state: 'unavailable' }); });
    getMyOrganisations().then((orgs) => { if (live && Array.isArray(orgs)) setOrganisations(orgs); }).catch(() => undefined);
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { live = false; document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);

  // Read on every render, so "Forget" (which bumps the version) shows at once.
  const options = api.workspaceOptions(directory.state === 'loaded' ? directory.memberships : null);

  const go = (target: string) => {
    if (target === workspaceId) { setOpen(false); return; }
    setBusy(target);
    setError(null);
    // On success the app reloads into the new workspace; only a refusal comes back here.
    void onSwitch(target).catch((e: unknown) => { setError(describeError(e)); setBusy(null); });
  };
  const submit = (event: FormEvent) => { event.preventDefault(); if (typed.trim()) go(typed.trim()); };
  const goOrganisation = (target: MyOrganisation) => {
    const first = target.workspaces[0];
    if (!first || !onSwitchOrganisation) return;
    setBusy(target.tenant_id);
    setError(null);
    void onSwitchOrganisation(target.tenant_id, first.workspace_id).catch((e: unknown) => { setError(describeError(e)); setBusy(null); });
  };
  const others = (organisations ?? []).filter((o) => o.tenant_id !== tenantId);
  const names = scopeNames(tenantId, workspaceId, organisations, directory.state === 'loaded' ? directory.memberships : null);

  return <div className="workspaceSwitcher" ref={root}>
    <button type="button" className="tenant workspaceSwitch" aria-haspopup="dialog" aria-expanded={open} aria-controls={panelId} title={t('switcher.button')} onClick={() => setOpen(!open)}>
      <Building2 className="switchIcon" aria-hidden="true" /><span dir="auto">{names.organisation} / {names.workspace}</span><ChevronDown className="switchChevron" aria-hidden />
    </button>
    {open && <section className="workspacePanel" id={panelId} role="dialog" aria-label={t('switcher.button')}>
      <div><p className="eyebrow">{t('switcher.eyebrow')}</p><h2>{t('switcher.button')}</h2></div>
      <p className="muted">{t('switcher.intro')}</p>
      <dl className="workspaceIds">
        <div><dt>{t('switcher.organisationId')}</dt><dd dir="ltr"><code>{tenantId}</code></dd></div>
        <div><dt>{t('switcher.workspaceId')}</dt><dd dir="ltr"><code>{workspaceId}</code></dd></div>
      </dl>
      {directory.state === 'loading' && <p className="muted" role="status">{t('switcher.loading')}</p>}
      <ul className="workspaceOptions" aria-label={t('switcher.list')}>
        {options.map((option) => {
          const named = Boolean(option.name && option.name !== option.id);
          const detail = membershipLabel(named ? { ...option, name: null } : option);
          return <li className="workspaceOption" key={option.id}>
            <button type="button" aria-current={option.current} disabled={busy !== null} onClick={() => go(option.id)}>
              <strong dir={named ? 'auto' : 'ltr'}>{named ? option.name : option.id}</strong>
              {named && <small dir="ltr">{option.id}</small>}
              <small>{option.current ? (detail ? t('switcher.currentWith', { detail }) : t('switcher.current')) : busy === option.id ? t('switcher.checking') : detail ?? sourceLabel(option.source)}</small>
            </button>
            {!option.current && option.source === 'remembered'
              ? <button type="button" className="iconButton" title={t('switcher.forget', { id: option.id })} aria-label={t('switcher.forget', { id: option.id })} disabled={busy !== null} onClick={() => { api.forgetWorkspace(option.id); setVersion((v) => v + 1); }}><X aria-hidden="true" /></button>
              : <span />}
          </li>;
        })}
      </ul>
      {onSwitchOrganisation && others.length > 0 && <div className="organisationOptions">
        <h3><Building2 aria-hidden="true" />{t('switcher.organisations')}</h3>
        <ul className="workspaceOptions" aria-label={t('switcher.organisations')}>
          {others.map((org) => <li className="workspaceOption" key={org.tenant_id}>
            <button type="button" disabled={busy !== null || !org.workspaces.length} onClick={() => goOrganisation(org)}>
              <strong>{org.display_name ?? org.name ?? org.tenant_id}</strong>
              <small>{busy === org.tenant_id ? t('switcher.checking') : t('switcher.organisationDetail', { id: org.tenant_id, count: org.workspaces.length })}</small>
            </button><span />
          </li>)}
        </ul>
      </div>}
      <form onSubmit={submit} aria-label={t('switcher.otherForm')}>
        <label className="field"><span>{t('switcher.otherLabel')}</span><input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="workspace_id" autoComplete="off" spellCheck={false} /></label>
        <button type="submit" disabled={busy !== null || !typed.trim()}><ArrowRightLeft aria-hidden="true" />{busy && busy === typed.trim() ? t('switcher.checkingShort') : t('switcher.switch')}</button>
      </form>
      {directory.state === 'unavailable'
        ? <p className="muted">{t('switcher.unavailable')}</p>
        : <p className="muted">{t('switcher.listNote', { tenant: tenantId })}</p>}
      {error && <p className="notice errorNotice" role="alert">{error}</p>}
    </section>}
  </div>;
}
