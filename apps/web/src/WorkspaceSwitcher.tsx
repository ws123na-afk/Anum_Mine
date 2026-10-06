// Top-bar workspace switcher (docs/identity.md, Workspace switcher). Lists the workspaces the client
// knows (src/lib/workspaces.ts), and switches only after the API confirms an active membership.
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowRightLeft, ChevronDown, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError } from './lib/errors';

const sourceLabel = { default: 'Default from sign-in or configuration', remembered: 'Joined or used in this browser' } as const;

export function WorkspaceSwitcher({ tenantId, workspaceId, onSwitch }: { tenantId: string; workspaceId: string; onSwitch: (workspaceId: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState(api.workspaceOptions);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    setOptions(api.workspaceOptions());
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);

  const go = (target: string) => {
    if (target === workspaceId) { setOpen(false); return; }
    setBusy(target);
    setError(null);
    // On success the app reloads into the new workspace; only a refusal comes back here.
    void onSwitch(target).catch((e: unknown) => { setError(describeError(e)); setBusy(null); });
  };
  const submit = (event: FormEvent) => { event.preventDefault(); if (typed.trim()) go(typed.trim()); };

  return <div className="workspaceSwitcher" ref={root}>
    <button type="button" className="tenant workspaceSwitch" aria-haspopup="dialog" aria-expanded={open} aria-controls={panelId} title="Switch workspace" onClick={() => setOpen(!open)}>
      <span>{tenantId} / {workspaceId}</span><ChevronDown aria-hidden />
    </button>
    {open && <section className="workspacePanel" id={panelId} role="dialog" aria-label="Switch workspace">
      <div><p className="eyebrow">Workspace</p><h2>Switch workspace</h2></div>
      <p className="muted">Every request names the selected workspace. Your role and the data you see follow it.</p>
      <ul className="workspaceOptions">
        {options.map((option) => <li className="workspaceOption" key={option.id}>
          <button type="button" aria-current={option.current} disabled={busy !== null} onClick={() => go(option.id)}>
            <strong>{option.id}</strong>
            <small>{option.current ? 'Current workspace' : busy === option.id ? 'Checking membership...' : sourceLabel[option.source]}</small>
          </button>
          {!option.current && option.source === 'remembered'
            ? <button type="button" className="iconButton" title={`Forget ${option.id}`} aria-label={`Forget ${option.id}`} disabled={busy !== null} onClick={() => { api.forgetWorkspace(option.id); setOptions(api.workspaceOptions()); }}><X /></button>
            : <span />}
        </li>)}
      </ul>
      <form onSubmit={submit} aria-label="Switch to another workspace">
        <label className="field"><span>Another workspace ID</span><input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="workspace_id" autoComplete="off" spellCheck={false} /></label>
        <button type="submit" disabled={busy !== null || !typed.trim()}><ArrowRightLeft />{busy && busy === typed.trim() ? 'Checking...' : 'Switch'}</button>
      </form>
      <p className="muted">ANUM cannot list every workspace you belong to yet. Type the ID of any other workspace you are a member of; the API checks your membership before the switch.</p>
      {error && <p className="notice errorNotice" role="alert">{error}</p>}
    </section>}
  </div>;
}
