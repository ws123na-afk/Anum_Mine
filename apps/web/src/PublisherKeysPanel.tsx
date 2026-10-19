// Trusted skill publisher keys (docs/skills.md#signing). Every member sees the public keys; the
// API's `can_manage` (organization administrators) unlocks adding, rotating and revoking. Each
// state is handled: loading, load failure with retry, empty with a next action, refused writes
// that say who can act, search, status filter and paging for long lists.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CircleDot, KeyRound, Plus, RefreshCw, RotateCw, Search, ShieldOff } from 'lucide-react';
import { Dialog } from './SkillsView';
import { pageOf } from './lib/catalog';
import * as catalog from './lib/catalogApi';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { emptyKeyDraft, filterKeys, keyBody, keyCounts, revokeBody, rotateBody, type KeyDraft, type KeyFilter, type PublisherKey, type PublisherKeyList, type SigningSettings } from './lib/skillSigning';

const PAGE = 20;
const refused = (e: unknown) => isPermissionDenied(e) ? t('signing.notAllowed', { error: describeError(e) }) : describeError(e);
const statusTone = (k: PublisherKey) => k.status === 'active' ? 'success' : k.status === 'rotated' ? 'neutral' : 'dangerTone';

function KeyForm({ title, taken, onClose, onSaved }: { title: string; taken: string[]; onClose(): void; onSaved(text: string): void }) {
  const [draft, setDraft] = useState<KeyDraft>(emptyKeyDraft());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof KeyDraft) => (e: { target: { value: string } }) => setDraft({ ...draft, [key]: e.target.value });
  const submit = async () => {
    const body = keyBody(draft, taken);
    if (!body.ok) { setError(body.error); return; }
    setBusy(true); setError(null);
    try { const key = await catalog.addPublisherKey(body.value); onSaved(t('keys.added', { id: key.id })); }
    catch (e) { setError(refused(e)); } finally { setBusy(false); }
  };
  return <Dialog title={title} onClose={onClose} wide>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <label className="field"><span>{t('keys.field.id')}</span><input dir="ltr" value={draft.id} onChange={set('id')} placeholder="acme-2026" autoComplete="off" /></label>
      <label className="field"><span>{t('keys.field.publisher')}</span><input dir="auto" value={draft.publisher} maxLength={160} onChange={set('publisher')} /></label>
      <label className="field catalogFormWide"><span>{t('keys.field.publicKey')}</span><input dir="ltr" className="keyMono" value={draft.publicKey} onChange={set('publicKey')} spellCheck={false} autoComplete="off" /></label>
      <p className="featureMeta catalogFormWide">{t('keys.intro')}</p>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button><button type="submit" disabled={busy}><KeyRound aria-hidden="true" />{t('keys.addButton')}</button></div>
    </form>
  </Dialog>;
}

function RotateForm({ target, taken, onClose, onSaved }: { target: PublisherKey; taken: string[]; onClose(): void; onSaved(text: string): void }) {
  const [newId, setNewId] = useState(''), [publicKey, setPublicKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const body = rotateBody(newId, publicKey, taken);
    if (!body.ok) { setError(body.error); return; }
    setBusy(true); setError(null);
    try { const key = await catalog.rotatePublisherKey(target.id, body.value); onSaved(t('keys.rotatedNote', { id: target.id, newId: key.id })); }
    catch (e) { setError(refused(e)); } finally { setBusy(false); }
  };
  return <Dialog title={t('keys.rotateTitle', { id: target.id })} onClose={onClose} wide>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <p className="featureMeta catalogFormWide">{t('keys.rotateHelp', { id: target.id })}</p>
      <label className="field"><span>{t('keys.field.newId')}</span><input dir="ltr" value={newId} onChange={(e) => setNewId(e.target.value)} autoComplete="off" /></label>
      <label className="field catalogFormWide"><span>{t('keys.field.newPublicKey')}</span><input dir="ltr" className="keyMono" value={publicKey} onChange={(e) => setPublicKey(e.target.value)} spellCheck={false} autoComplete="off" /></label>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button><button type="submit" disabled={busy}><RotateCw aria-hidden="true" />{t('keys.rotateButton')}</button></div>
    </form>
  </Dialog>;
}

function RevokeForm({ target, onClose, onSaved }: { target: PublisherKey; onClose(): void; onSaved(text: string): void }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const body = revokeBody(reason);
    if (!body.ok) { setError(body.error); return; }
    setBusy(true); setError(null);
    try {
      const result = await catalog.revokePublisherKey(target.id, body.value.reason);
      const note = t('keys.revokedNote', { id: target.id, versions: result.affected_versions, installs: result.flagged_installations });
      onSaved(result.auto_disable ? `${note} ${t('keys.disabledNote', { count: result.disabled_installations ?? 0 })}` : note);
    } catch (e) { setError(refused(e)); } finally { setBusy(false); }
  };
  return <Dialog title={t('keys.revokeTitle', { id: target.id })} onClose={onClose}>
    <form className="catalogForm" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <p className="notice errorNotice catalogFormWide" role="note"><ShieldOff aria-hidden="true" />{t('keys.revokeHelp')}</p>
      <label className="field catalogFormWide"><span>{t('keys.field.reason')}</span><textarea dir="auto" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      {error && <p className="notice errorNotice catalogFormWide" role="alert"><CircleDot aria-hidden="true" />{error}</p>}
      <div className="actions catalogFormWide"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button><button type="submit" className="danger" disabled={busy}><ShieldOff aria-hidden="true" />{t('keys.revokeButton')}</button></div>
    </form>
  </Dialog>;
}

/** "Disable flagged installs automatically": organization administrators switch it; everyone sees it. */
function AutoDisableSetting() {
  const [value, setValue] = useState<SigningSettings | null>(null);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => catalog.getSigningSettings().then((v) => { setValue(v); setError(null); }).catch((e) => setError(t('keys.settingFailed', { error: describeError(e) }))), []);
  useEffect(() => { void load(); }, [load]);
  const change = async (next: boolean) => {
    const before = value;
    // Optimistic, so the switch follows the click; restored if the API refuses.
    if (before) setValue({ ...before, disable_flagged: next });
    setBusy(true); setError(null); setNote(null);
    try { const saved = await catalog.saveSigningSettings(next); setValue(saved); setNote(t(next ? 'keys.settingOn' : 'keys.settingOff')); }
    catch (e) { setValue(before); setError(refused(e)); } finally { setBusy(false); }
  };
  return <section className="surface publisherKeySetting" aria-labelledby="auto-disable-h">
    <h4 id="auto-disable-h">{t('keys.settingTitle')}</h4>
    <p className="featureMeta">{t('keys.settingHelp')}</p>
    {!value && !error && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('keys.settingLoading')}</p>}
    {value && <label className="integrationToggle"><input type="checkbox" checked={value.disable_flagged} disabled={busy || !value.can_manage} onChange={(e) => void change(e.target.checked)} />{t('keys.settingLabel')}</label>}
    {value && !value.can_manage && <p className="featureMeta">{t('keys.settingReadOnly')}</p>}
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error} <button type="button" className="linkButton" onClick={() => void load()}>{t('keys.retry')}</button></p>}
    {note && <p className="notice" role="status"><CircleDot aria-hidden="true" />{note}</p>}
  </section>;
}

function KeyRow({ k, manage, onRotate, onRevoke }: { k: PublisherKey; manage: boolean; onRotate?(): void; onRevoke?(): void }) {
  return <li className="surface publisherKey">
    <span className="integrationIcon" aria-hidden="true"><KeyRound /></span>
    <div className="publisherKeyBody">
      <h4><span dir="ltr">{k.id}</span> <span className={`statusBadge ${statusTone(k)}`}>{t(`keys.status.${k.status}`)}</span></h4>
      <p dir="auto">{k.publisher}</p>
      <small className="keyMono" dir="ltr">{t('keys.meta', { fingerprint: k.fingerprint, user: k.created_by, time: formatDateTime(k.created_at) })}</small>
      {k.status === 'rotated' && k.rotated_at && <small>{t('keys.rotatedTo', { time: formatDateTime(k.rotated_at), id: k.replaced_by ?? '-' })}</small>}
      {k.status === 'revoked' && k.revoked_at && <small dir="auto">{t('keys.revokedBecause', { user: k.revoked_by ?? '-', time: formatDateTime(k.revoked_at), reason: k.revoke_reason ?? '-' })}</small>}
    </div>
    {/* Rotate only an active key; revoke a rotated one too (its old private key may leak later). */}
    {manage && k.status !== 'revoked' && <div className="publisherKeyActions">
      {k.status === 'active' && <button type="button" className="secondary" aria-label={t('keys.rotateNamed', { id: k.id })} onClick={onRotate}><RotateCw size={15} aria-hidden="true" />{t('keys.rotate')}</button>}
      <button type="button" className="secondary dangerText" aria-label={t('keys.revokeNamed', { id: k.id })} onClick={onRevoke}><ShieldOff size={15} aria-hidden="true" />{t('keys.revoke')}</button>
    </div>}
  </li>;
}

export function PublisherKeysPanel({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<PublisherKeyList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null), [note, setNote] = useState<string | null>(null);
  const [query, setQuery] = useState(''), [filter, setFilter] = useState<KeyFilter>('all'), [pages, setPages] = useState(1);
  const [adding, setAdding] = useState(false);
  const [rotating, setRotating] = useState<PublisherKey | null>(null), [revoking, setRevoking] = useState<PublisherKey | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await catalog.getPublisherKeys()); setError(null); }
    catch (e) { setError(t('keys.loadFailed', { error: describeError(e) })); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => setPages(1), [query, filter]);
  const keys = data?.keys ?? [];
  const taken = useMemo(() => [...keys, ...(data?.platform_keys ?? [])].map((k) => k.id), [keys, data]);
  const filtered = filterKeys(keys, query, filter);
  const { shown, remaining } = pageOf(filtered, pages, PAGE);
  const counts = keyCounts(keys);
  const manage = data?.can_manage === true;
  const saved = (text: string) => { setAdding(false); setRotating(null); setRevoking(null); setNote(text); void load(); onChanged?.(); };
  return <section className="publisherKeys" aria-labelledby="publisher-keys-h">
    <div className="publisherKeysHead">
      <div><h3 id="publisher-keys-h">{t('keys.title')}</h3><p className="featureMeta">{t('keys.intro')}</p></div>
      <div className="actions">
        {manage && <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('keys.add')}</button>}
        <button type="button" className="secondary" onClick={() => void load()} disabled={loading}><RefreshCw aria-hidden="true" />{t('keys.refresh')}</button>
      </div>
    </div>
    {error && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{error} <button type="button" className="linkButton" onClick={() => void load()}>{t('keys.retry')}</button></p>}
    {note && <p className="notice" role="status"><CircleDot aria-hidden="true" />{note}</p>}
    {loading && !data && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('keys.loading')}</p>}
    {data && !manage && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('keys.readOnly')}</p>}
    {data && <AutoDisableSetting />}
    {data && keys.length > 0 && <div className="surface catalogToolbar" role="search">
      <label className="field"><span>{t('keys.search')}</span><input type="search" value={query} placeholder={t('keys.searchPlaceholder')} onChange={(e) => setQuery(e.target.value)} /></label>
      <fieldset className="catalogSegments"><legend>{t('keys.show')}</legend>{(['all', 'active', 'rotated', 'revoked'] as const).map((f) => <label key={f}><input type="radio" name="key-filter" checked={filter === f} onChange={() => setFilter(f)} />{t(`keys.filter.${f}`)}</label>)}</fieldset>
      <p className="featureMeta" aria-live="polite">{t('keys.count', { shown: filtered.length, count: keys.length })}. {t('keys.counts', counts)}</p>
    </div>}
    {shown.length > 0 && <ul className="publisherKeyList">{shown.map((k) => <KeyRow key={k.id} k={k} manage={manage} onRotate={() => setRotating(k)} onRevoke={() => setRevoking(k)} />)}</ul>}
    {remaining > 0 && <div className="catalogMore"><button type="button" className="secondary" onClick={() => setPages(pages + 1)}>{t('keys.showMore', { count: Math.min(remaining, PAGE) })}</button></div>}
    {data && keys.length === 0 && <div className="emptyState"><KeyRound aria-hidden="true" /><h3>{t('keys.emptyTitle')}</h3>
      {manage ? <><p>{t('keys.emptyAdmin')}</p><button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('keys.add')}</button></> : <p>{t('keys.emptyOther')}</p>}</div>}
    {keys.length > 0 && filtered.length === 0 && <div className="emptyState"><Search aria-hidden="true" /><h3>{t('keys.noMatchTitle')}</h3><p>{t('keys.noMatchText', { query })}</p><button type="button" className="secondary" onClick={() => { setQuery(''); setFilter('all'); }}>{t('keys.clear')}</button></div>}
    {data && <section className="publisherKeysPlatform" aria-labelledby="platform-keys-h">
      <h4 id="platform-keys-h">{t('keys.platformTitle')}</h4>
      <p className="featureMeta">{data.platform_keys.length ? t('keys.platformNote') : t('keys.platformNone')}</p>
      {data.platform_keys.length > 0 && <ul className="publisherKeyList">{data.platform_keys.map((k) => <KeyRow key={k.id} k={k} manage={false} />)}</ul>}
    </section>}
    {adding && <KeyForm title={t('keys.addTitle')} taken={taken} onClose={() => setAdding(false)} onSaved={saved} />}
    {rotating && <RotateForm target={rotating} taken={taken} onClose={() => setRotating(null)} onSaved={saved} />}
    {revoking && <RevokeForm target={revoking} onClose={() => setRevoking(null)} onSaved={saved} />}
  </section>;
}
