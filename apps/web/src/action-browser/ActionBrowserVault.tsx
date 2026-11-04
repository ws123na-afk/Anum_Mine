// Integrations, Action browser: the owner's saved logins and cards (docs/action-browser.md#saved-logins-and-cards).
// Lazy chunk. Lists show the handle, the site and a masked hint; secrets are typed into password
// fields, sent once and never shown again. An edit sends only the parts filled in.
import { useCallback, useEffect, useId, useState } from 'react';
import { CreditCard, KeyRound, Pencil, Plus, Trash2 } from 'lucide-react';
import { useLocale } from '../useLocale';
import { describeError, isPermissionDenied } from '../lib/errors';
import { formatDateTime } from '../lib/i18n';
import { credentialBody, credentialProblem, say, type Credential, type CredentialInput } from '../lib/actionBrowser';
import { createCredential, deleteCredential, listCredentials, updateCredential } from './api';
import './actionBrowser.css';

type Load = { state: 'loading' } | { state: 'ready'; items: Credential[] } | { state: 'denied' } | { state: 'error'; reason: string };
type Editing = { mode: 'new'; kind: 'login' | 'card' } | { mode: 'edit'; item: Credential } | null;

export default function ActionBrowserVault() {
  const locale = useLocale();
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [editing, setEditing] = useState<Editing>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const headingId = useId();

  const refresh = useCallback(async () => {
    try { setLoad({ state: 'ready', items: await listCredentials() }); }
    catch (e) { setLoad(isPermissionDenied(e) ? { state: 'denied' } : { state: 'error', reason: describeError(e) }); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const remove = async (item: Credential) => {
    setBusy(true); setFlash(null);
    try { await deleteCredential(item.id); setConfirm(null); setFlash({ text: say('removed', { handle: item.handle }) }); await refresh(); }
    catch (e) { setFlash({ text: describeError(e), error: true }); }
    finally { setBusy(false); }
  };

  return <section className="abVault" aria-labelledby={headingId} lang={locale} data-action-browser-vault>
    <h3 id={headingId}>{say('vaultTitle')}</h3>
    <p className="abMuted">{say('vaultIntro')}</p>
    {load.state === 'loading' && <p className="abMuted" role="status">{say('loading')}</p>}
    {load.state === 'denied' && <p className="abNote" role="note">{say('ownerOnly')}</p>}
    {load.state === 'error' && <div className="abNote abError" role="alert">{say('loadFailed', { error: load.reason })} <button type="button" className="secondary" onClick={() => void refresh()}>{say('retry')}</button></div>}
    {load.state === 'ready' && <>
      {load.items.length === 0 && !editing && <div className="abEmpty"><p><strong>{say('emptyTitle')}</strong></p><p className="abMuted">{say('emptyText')}</p></div>}
      {load.items.length > 0 && <ul className="abCredList">{load.items.map((item) => <li key={item.id} className="abCred">
        <span className="abCredIcon" aria-hidden="true">{item.kind === 'login' ? <KeyRound /> : <CreditCard />}</span>
        <span className="abCredMain">
          <strong dir="ltr">{item.handle}</strong> <span className="statusBadge neutral">{say(item.kind)}</span>
          <small dir="auto">{item.hint}</small>
          <small className="abMuted">{formatDateTime(item.updated_at)}</small>
        </span>
        <span className="abCredActions">
          {confirm === item.id
            ? <><button type="button" className="danger" disabled={busy} onClick={() => void remove(item)}>{say('confirmRemove', { handle: item.handle })}</button>
              <button type="button" className="secondary" onClick={() => setConfirm(null)}>{say('cancel')}</button></>
            : <><button type="button" className="secondary" aria-label={`${say('edit')} ${item.handle}`} onClick={() => { setFlash(null); setEditing({ mode: 'edit', item }); }}><Pencil aria-hidden="true" />{say('edit')}</button>
              <button type="button" className="secondary" aria-label={`${say('remove')} ${item.handle}`} onClick={() => setConfirm(item.id)}><Trash2 aria-hidden="true" />{say('remove')}</button></>}
        </span>
      </li>)}</ul>}
      {!editing && <div className="abActions">
        <button type="button" onClick={() => { setFlash(null); setEditing({ mode: 'new', kind: 'login' }); }}><Plus aria-hidden="true" />{say('addLogin')}</button>
        <button type="button" className="secondary" onClick={() => { setFlash(null); setEditing({ mode: 'new', kind: 'card' }); }}><Plus aria-hidden="true" />{say('addCard')}</button>
      </div>}
      {editing && <CredentialForm editing={editing} onCancel={() => setEditing(null)} onSaved={(saved) => { setEditing(null); setFlash({ text: say('saved', { handle: saved.handle }) }); void refresh(); }} />}
    </>}
    {flash && <p className={`abNote ${flash.error ? 'abError' : ''}`} role={flash.error ? 'alert' : 'status'}>{flash.text}</p>}
  </section>;
}

function CredentialForm({ editing, onCancel, onSaved }: { editing: NonNullable<Editing>; onCancel(): void; onSaved(item: Credential): void }) {
  const kind = editing.mode === 'new' ? editing.kind : editing.item.kind;
  const existing = editing.mode === 'edit' ? editing.item : null;
  const [value, setValue] = useState<CredentialInput>({ kind, handle: existing?.handle ?? '', site: existing?.site ?? '' });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const id = useId();
  const set = (key: keyof CredentialInput) => (e: { target: { value: string } }) => setValue((old) => ({ ...old, [key]: e.target.value }));
  const submit = async () => {
    const problem = credentialProblem(value, existing !== null);
    if (problem) { setError(problem); return; }
    setSaving(true); setError(null);
    try { onSaved(existing ? await updateCredential(existing.id, credentialBody(value)) : await createCredential(credentialBody(value))); }
    catch (e) { setError(describeError(e)); }
    finally { setSaving(false); }
  };
  const field = (key: keyof CredentialInput, label: string, extra: Record<string, unknown> = {}, help?: string) =>
    <label className="field"><span>{label}</span>
      <input dir="ltr" value={(value[key] as string | undefined) ?? ''} onChange={set(key)} autoComplete="off" spellCheck={false} aria-describedby={help ? `${id}-${key}` : undefined} {...extra} />
      {help && <small id={`${id}-${key}`} className="abMuted">{help}</small>}</label>;
  return <form className="abForm" aria-label={existing ? `${say('edit')} ${existing.handle}` : say(kind === 'login' ? 'addLogin' : 'addCard')} onSubmit={(e) => { e.preventDefault(); void submit(); }}>
    {field('handle', say('handle'), { maxLength: 40, required: true, 'data-autofocus': true }, say('handleHelp'))}
    {kind === 'login' ? <>
      {field('site', say('site'), { maxLength: 300, placeholder: 'example.com' }, say('siteHelp'))}
      {field('username', say('username'), { maxLength: 200, placeholder: existing ? '••••' : '' })}
      {field('password', say('password'), { type: 'password', maxLength: 200, autoComplete: 'new-password', placeholder: existing ? '••••' : '' })}
    </> : <>
      {field('name', say('nameOnCard'), { maxLength: 100, placeholder: existing ? '••••' : '' })}
      {field('number', say('cardNumber'), { type: 'password', inputMode: 'numeric', maxLength: 30, autoComplete: 'off', placeholder: existing ? '••••' : '' })}
      {field('expiry', say('expiry'), { maxLength: 5, placeholder: 'MM/YY' })}
      {field('cvc', say('cvc'), { type: 'password', inputMode: 'numeric', maxLength: 4, autoComplete: 'off', placeholder: existing ? '••••' : '' })}
    </>}
    {existing && <p className="abMuted">{say('keepHint')}</p>}
    {error && <p className="abNote abError" role="alert">{error}</p>}
    <div className="abActions"><button type="submit" disabled={saving}>{saving ? say('saving') : say('save')}</button><button type="button" className="secondary" onClick={onCancel}>{say('cancel')}</button></div>
  </form>;
}
