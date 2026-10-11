// Top-bar workspace switcher (docs/identity.md, Workspace switcher). Lists the caller's memberships
// from the API plus the workspaces the client knows (src/lib/workspaces.ts), and switches only
// after the API confirms an active membership in the target.
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowRightLeft, ChevronDown, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError } from './lib/errors';
import { membershipLabel, type MembershipEntry } from './lib/workspaces';
import { t } from './lib/i18n';

const sourceLabel = (source: 'member' | 'default' | 'remembered') => t(`switcher.source.${source}`);

type Directory = { state: 'loading' } | { state: 'loaded'; memberships: MembershipEntry[] } | { state: 'unavailable' };

export function WorkspaceSwitcher({ tenantId, workspaceId, onSwitch }: { tenantId: string; workspaceId: string; onSwitch: (workspaceId: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [directory, setDirectory] = useState<Directory>({ state: 'loading' });
  const [, setVersion] = useState(0);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    let live = true;
    setDirectory({ state: 'loading' });
    api.listMyWorkspaces()
      .then((memberships) => { if (live) setDirectory({ state: 'loaded', memberships }); })
      .catch(() => { if (live) setDirectory({ state: 'unavailable' }); });
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

  return <div className="workspaceSwitcher" ref={root}>
    <button type="button" className="tenant workspaceSwitch" aria-haspopup="dialog" aria-expanded={open} aria-controls={panelId} title={t('switcher.button')} onClick={() => setOpen(!open)}>
      <span dir="ltr">{tenantId} / {workspaceId}</span><ChevronDown aria-hidden />
    </button>
    {open && <section className="workspacePanel" id={panelId} role="dialog" aria-label={t('switcher.button')}>
      <div><p className="eyebrow">{t('switcher.eyebrow')}</p><h2>{t('switcher.button')}</h2></div>
      <p className="muted">{t('switcher.intro')}</p>
      {directory.state === 'loading' && <p className="muted" role="status">{t('switcher.loading')}</p>}
      <ul className="workspaceOptions" aria-label={t('switcher.list')}>
        {options.map((option) => {
          const detail = membershipLabel(option);
          return <li className="workspaceOption" key={option.id}>
            <button type="button" aria-current={option.current} disabled={busy !== null} onClick={() => go(option.id)}>
              <strong dir="ltr">{option.id}</strong>
              <small>{option.current ? (detail ? t('switcher.currentWith', { detail }) : t('switcher.current')) : busy === option.id ? t('switcher.checking') : detail ?? sourceLabel(option.source)}</small>
            </button>
            {!option.current && option.source === 'remembered'
              ? <button type="button" className="iconButton" title={t('switcher.forget', { id: option.id })} aria-label={t('switcher.forget', { id: option.id })} disabled={busy !== null} onClick={() => { api.forgetWorkspace(option.id); setVersion((v) => v + 1); }}><X aria-hidden="true" /></button>
              : <span />}
          </li>;
        })}
      </ul>
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
