// Email Center phase 3 part A (docs/email.md#email-center): the parts the mail view adds for several
// mailboxes, sharing, sender identities and contacts.
// - MailboxPicker: switch between the caller's own mailboxes and those shared with them.
// - SharedNotice / LeaveShared: what a shared mailbox lets the caller do, and leaving it.
// - SharingPanel: the owner shares the mailbox with members to read or to send.
// - IdentitiesPanel: the owner's "send as" addresses with a name and signature.
// - FromPicker: the composer's sender, from those identities.
// - RecipientField: To, Cc and Bcc with suggestions from the address book.
// - ContactsPanel: the person's own address book (search, paging, add, edit, delete, write to).
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { CircleDot, Contact, LogOut, PenLine, Plus, RefreshCw, Search, Share2, Trash2, UserPlus } from 'lucide-react';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatNumber, t } from './lib/i18n';
import type { MailAccount, MailSetup } from './lib/mail';
import {
  EMPTY_CONTACT, SUGGEST_MIN_CHARS, accessOf, appendContacts, contactInput, contactLabel, contactProblem, insertRecipient, personName, recipientToken,
  senderLabel, suggestionsFor, typedAddresses,
  type MailContact, type MailContactInput, type MailGrant, type MailGrantAccess, type MailIdentity, type MailShareCandidate,
} from './lib/mailDirectory';
import {
  createMailContact, createMailIdentity, deleteMailContact, deleteMailGrant, deleteMailIdentity, listMailContacts, listMailGrants, listMailIdentities,
  listShareCandidates, shareMailbox, updateMailContact, updateMailGrant, updateMailIdentity,
} from './lib/mailDirectoryApi';
import './mailDirectory.css';

type Note = { text: string; error?: boolean } | null;

function Notice({ note }: { note: Note }) {
  if (!note) return null;
  return <p className={`notice ${note.error ? 'errorNotice' : ''}`} role={note.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{note.text}</p>;
}

function useRun() {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);
  const run = useCallback(async (kind: string, fn: () => Promise<void>) => {
    setBusy(kind); setNote(null);
    try { await fn(); } catch (e) { setNote({ text: describeError(e), error: true }); } finally { setBusy(null); }
  }, []);
  return { busy, note, setNote, run };
}

// -- mailboxes ------------------------------------------------------------------------------------

/** One select for the caller's mailboxes; own first, shared ones say so. Shown only with two or more. */
export function MailboxPicker({ accounts, currentId, onPick }: { accounts: MailAccount[]; currentId: string; onPick: (id: string) => void }) {
  if (accounts.length < 2) return null;
  return <label className="mailsPicker"><span className="visuallyHidden">{t('mails.mailbox.pick')}</span>
    <select value={currentId} onChange={(e) => onPick(e.target.value)}>
      {accounts.map((a) => <option key={a.id} value={a.id}>{accessOf(a) === 'owner' ? a.address : t('mails.mailbox.sharedOption', { address: a.address })}</option>)}
    </select>
  </label>;
}

/** What a mailbox shared with the caller lets them do (nothing for their own). */
export function SharedNotice({ account }: { account: MailAccount }) {
  const access = accessOf(account);
  if (access === 'owner') return null;
  const owner = account.owner_user_id ?? '';
  return <p className="notice mailsShared" role="note"><Share2 aria-hidden="true" />{access === 'read' ? t('mails.sharedRead', { owner }) : t('mails.sharedSend', { owner })}</p>;
}

/** The settings of a mailbox shared with the caller: who owns it, and leaving it. */
export function LeaveShared({ account, onLeft }: { account: MailAccount; onLeft: () => void }) {
  const { busy, note, run } = useRun();
  // The setup names the caller's own share of each mailbox shared with them.
  const grantId = account.grant_id ?? null;
  const id = useId();
  return <section className="mailsSection" aria-labelledby={id}>
    <h3 id={id}>{t('mails.leave.title')}</h3>
    <SharedNotice account={account} />
    <p className="muted">{t('mails.ownerOnly')}</p>
    <Notice note={note} />
    {grantId && <div className="actions"><button type="button" className="secondary danger" disabled={busy !== null} onClick={() => {
      if (!window.confirm(t('mails.leave.confirm', { address: account.address }))) return;
      void run('leave', async () => { await deleteMailGrant(grantId); onLeft(); });
    }}><LogOut aria-hidden="true" />{t('mails.leave')}</button></div>}
  </section>;
}

// -- sharing --------------------------------------------------------------------------------------

export function SharingPanel({ account, onCount }: { account: MailAccount; onCount: (count: number) => void }) {
  const [grants, setGrants] = useState<MailGrant[] | null>(null);
  const [candidates, setCandidates] = useState<MailShareCandidate[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [person, setPerson] = useState('');
  const [access, setAccess] = useState<MailGrantAccess>('read');
  const { busy, note, setNote, run } = useRun();
  const ids = { title: useId(), person: useId(), access: useId() };
  const count = useRef(onCount);
  count.current = onCount;

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [g, c] = await Promise.all([listMailGrants(account.id), listShareCandidates(account.id)]);
      setGrants(g); setCandidates(c); count.current(g.length);
    } catch (e) { setLoadError(describeError(e)); }
  }, [account.id]);
  useEffect(() => { void load(); }, [load]);

  const open = candidates.filter((c) => !c.grant_id);
  const nameOf = (userId: string) => {
    const known = candidates.find((c) => c.user_id === userId);
    return known ? personName(known) : userId;
  };
  async function add(event: FormEvent) {
    event.preventDefault();
    if (!person) return;
    await run('add', async () => {
      await shareMailbox(account.id, person, access);
      setNote({ text: t('mails.share.done', { person: nameOf(person) }) });
      setPerson('');
      await load();
    });
  }

  return <section className="mailsSection" aria-labelledby={ids.title}>
    <h3 id={ids.title}><Share2 aria-hidden="true" />{t('mails.share.title')}</h3>
    <p className="muted">{t('mails.share.text')}</p>
    {loadError && <><Notice note={{ text: t('mails.share.failed', { reason: loadError }), error: true }} /><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div></>}
    {!grants && !loadError && <p className="muted" role="status">{t('mails.share.loading')}</p>}
    {grants && <>
      {grants.length === 0 ? <p className="muted">{t('mails.share.none')}</p> : <>
        <p className="muted" aria-live="polite">{t('mails.share.count', { count: grants.length })}</p>
        <ul className="mailsList">{grants.map((g) => {
          const who = personName(g);
          return <li key={g.id} className="mailsRow">
            <span className="mailsWho" dir="auto">{who}</span>
            <label className="mailsInline"><span className="visuallyHidden">{t('mails.share.accessFor', { person: who })}</span>
              <select value={g.access} disabled={busy !== null} onChange={(e) => {
                const next = e.target.value as MailGrantAccess;
                void run(`access:${g.id}`, async () => { const saved = await updateMailGrant(g.id, next); setGrants((list) => (list ?? []).map((x) => x.id === saved.id ? saved : x)); setNote({ text: t('mails.share.changed', { person: who }) }); });
              }}>
                <option value="read">{t('mails.share.read')}</option>
                <option value="send">{t('mails.share.send')}</option>
              </select>
            </label>
            <button type="button" className="secondary danger" disabled={busy !== null} aria-label={t('mails.share.removeFor', { person: who })} onClick={() => {
              if (!window.confirm(t('mails.share.removeConfirm', { address: account.address, person: who }))) return;
              void run(`remove:${g.id}`, async () => { await deleteMailGrant(g.id); setNote({ text: t('mails.share.removed', { person: who }) }); await load(); });
            }}><Trash2 aria-hidden="true" /><span>{t('mails.share.remove')}</span></button>
          </li>;
        })}</ul>
      </>}
      {open.length === 0 ? <p className="muted">{t('mails.share.noCandidates')}</p>
        : <form className="mailsShareForm" onSubmit={(e) => void add(e)}>
          <label className="field" htmlFor={ids.person}><span>{t('mails.share.person')}</span>
            <select id={ids.person} value={person} required onChange={(e) => setPerson(e.target.value)}>
              <option value="">{t('mails.share.pick')}</option>
              {open.map((c) => <option key={c.user_id} value={c.user_id}>{personName(c)}</option>)}
            </select>
          </label>
          <label className="field" htmlFor={ids.access}><span>{t('mails.share.access')}</span>
            <select id={ids.access} value={access} onChange={(e) => setAccess(e.target.value as MailGrantAccess)}>
              <option value="read">{t('mails.share.read')}</option>
              <option value="send">{t('mails.share.send')}</option>
            </select>
          </label>
          <div className="actions"><button type="submit" disabled={busy !== null || !person}><UserPlus aria-hidden="true" />{busy === 'add' ? t('mails.share.adding') : t('mails.share.add')}</button></div>
        </form>}
    </>}
    <Notice note={note} />
  </section>;
}

// -- identities -----------------------------------------------------------------------------------

type IdentityDraft = { address: string; name: string; signature: string; isDefault: boolean };
const NO_IDENTITY: IdentityDraft = { address: '', name: '', signature: '', isDefault: false };

export function IdentitiesPanel({ account, setup }: { account: MailAccount; setup: MailSetup }) {
  const [items, setItems] = useState<MailIdentity[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<IdentityDraft>(NO_IDENTITY);
  const [editing, setEditing] = useState<string | null>(null);
  const { busy, note, setNote, run } = useRun();
  const ids = { title: useId(), address: useId(), name: useId(), signature: useId(), def: useId() };

  const load = useCallback(async () => {
    setLoadError(null);
    try { setItems(await listMailIdentities(account.id)); } catch (e) { setLoadError(describeError(e)); }
  }, [account.id]);
  useEffect(() => { void load(); }, [load]);

  function edit(identity: MailIdentity | null) {
    setEditing(identity?.id ?? null);
    setForm(identity ? { address: identity.address, name: identity.display_name ?? '', signature: identity.signature, isDefault: identity.is_default } : NO_IDENTITY);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    await run('save', async () => {
      if (editing) {
        const saved = await updateMailIdentity(editing, { display_name: form.name.trim() || null, signature: form.signature, is_default: form.isDefault });
        setNote({ text: t('mails.ident.saved', { address: saved.address }) });
      } else {
        const saved = await createMailIdentity(account.id, { address: form.address.trim(), display_name: form.name.trim() || undefined, signature: form.signature, is_default: form.isDefault });
        setNote({ text: t('mails.ident.added', { address: saved.address }) });
      }
      edit(null);
      await load();
    });
  }

  return <section className="mailsSection" aria-labelledby={ids.title}>
    <h3 id={ids.title}><PenLine aria-hidden="true" />{t('mails.ident.title')}</h3>
    <p className="muted">{t('mails.ident.text', { domains: setup.allowed_domains.join(', ') })}</p>
    {loadError && <><Notice note={{ text: t('mails.ident.failed', { reason: loadError }), error: true }} /><div className="actions"><button type="button" className="secondary" onClick={() => void load()}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div></>}
    {!items && !loadError && <p className="muted" role="status">{t('mails.ident.loading')}</p>}
    {items && (items.length === 0 ? <p className="muted">{t('mails.ident.none', { address: account.address })}</p>
      : <ul className="mailsList">{items.map((i) => <li key={i.id} className="mailsRow">
        <span className="mailsWho" dir="auto">{senderLabel(i.display_name, i.address)}{i.is_default && <span className="statusBadge neutral">{t('mails.ident.isDefault')}</span>}</span>
        <button type="button" className="secondary" disabled={busy !== null} aria-label={t('mails.ident.edit', { address: i.address })} onClick={() => edit(i)}>{t('mails.contacts.edit')}</button>
        <button type="button" className="secondary danger" disabled={busy !== null} aria-label={t('mails.ident.remove', { address: i.address })} onClick={() => {
          if (!window.confirm(t('mails.ident.removeConfirm', { address: i.address }))) return;
          void run(`remove:${i.id}`, async () => { await deleteMailIdentity(i.id); setNote({ text: t('mails.ident.removed', { address: i.address }) }); if (editing === i.id) edit(null); await load(); });
        }}><Trash2 aria-hidden="true" /></button>
      </li>)}</ul>)}
    {items && <form className="mailForm mailsIdentityForm" onSubmit={(e) => void submit(e)}>
      <label className="field" htmlFor={ids.address}><span>{t('mails.ident.address')}</span>
        <input id={ids.address} type="email" dir="ltr" required maxLength={320} value={form.address} disabled={editing !== null} onChange={(e) => setForm((f) => ({ ...f, address: e.target.value }))} />
      </label>
      <label className="field" htmlFor={ids.name}><span>{t('mails.ident.name')}</span>
        <input id={ids.name} dir="auto" maxLength={200} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
      </label>
      <label className="field" htmlFor={ids.signature}><span>{t('mails.ident.signature')}</span>
        <textarea id={ids.signature} dir="auto" rows={3} maxLength={2000} value={form.signature} onChange={(e) => setForm((f) => ({ ...f, signature: e.target.value }))} />
      </label>
      <label className="mailsCheck" htmlFor={ids.def}><input id={ids.def} type="checkbox" checked={form.isDefault} onChange={(e) => setForm((f) => ({ ...f, isDefault: e.target.checked }))} /><span>{t('mails.ident.default')}</span></label>
      <div className="actions">
        <button type="submit" className="secondary" disabled={busy !== null || !form.address.trim()}>{editing ? t('mails.ident.save') : <><Plus aria-hidden="true" />{busy === 'save' ? t('mails.ident.adding') : t('mails.ident.add')}</>}</button>
        {editing && <button type="button" className="secondary" onClick={() => edit(null)}>{t('mails.ident.cancel')}</button>}
      </div>
    </form>}
    <Notice note={note} />
  </section>;
}

/** The composer's From: the mailbox address or one of its identities. Hidden while there are none. */
export function FromPicker({ account, value, disabled, onChange, fallback }: {
  account: MailAccount; value: string | null; disabled: boolean; onChange: (identityId: string | null) => void; fallback: ReactNode;
}) {
  const [items, setItems] = useState<MailIdentity[] | null>(null);
  const id = useId();
  useEffect(() => {
    let live = true;
    listMailIdentities(account.id).then((found) => { if (live) setItems(found); }, () => { if (live) setItems([]); });
    return () => { live = false; };
  }, [account.id]);
  if (!items || items.length === 0) return <>{fallback}</>;
  return <label className="field mailsFrom" htmlFor={id}><span>{t('mail.composer.from')}</span>
    <select id={id} dir="ltr" value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{t('mails.from.mailbox', { sender: senderLabel(account.display_name, account.address) })}</option>
      {items.map((i) => <option key={i.id} value={i.id}>{senderLabel(i.display_name ?? account.display_name, i.address)}</option>)}
    </select>
  </label>;
}

// -- recipients with suggestions ------------------------------------------------------------------

/**
 * A recipients input (To, Cc, Bcc) that suggests contacts for the address being typed: an ARIA 1.2
 * combobox with a listbox; arrows move, Enter or Tab picks, Escape closes. Suggestions are a help:
 * when the address book cannot be read, the field still works as plain text.
 */
export function RecipientField({ label, value, disabled, describedBy, onChange }: {
  label: string; value: string; disabled: boolean; describedBy?: string; onChange: (value: string) => void;
}) {
  const id = useId();
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [options, setOptions] = useState<MailContact[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const [caret, setCaret] = useState(0);
  const token = recipientToken(value, caret);

  useEffect(() => {
    if (!open || token.query.length < SUGGEST_MIN_CHARS) { setOptions([]); return; }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      listMailContacts({ q: token.query, limit: 8 }, controller.signal).then(
        (page) => { setOptions(suggestionsFor(page.items, typedAddresses(value))); setActive(0); },
        () => setOptions([]),
      );
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, token.query, value]);

  function pick(contact: MailContact) {
    const next = insertRecipient(value, token, contact.email);
    onChange(next.text);
    setOptions([]); setOpen(false);
    requestAnimationFrame(() => { input.current?.setSelectionRange(next.caret, next.caret); setCaret(next.caret); });
  }
  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (!options.length) return;
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive((i) => (i + 1) % options.length); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive((i) => (i - 1 + options.length) % options.length); }
    else if (event.key === 'Enter' || (event.key === 'Tab' && !event.shiftKey)) { event.preventDefault(); pick(options[active]); }
    else if (event.key === 'Escape') { event.preventDefault(); setOptions([]); setOpen(false); }
  }
  const shown = open && options.length > 0;
  return <div className="field mailsRecipient">
    <label htmlFor={id}>{label}</label>
    <input
      ref={input} id={id} dir="ltr" value={value} disabled={disabled} autoComplete="off" role="combobox" aria-autocomplete="list"
      aria-expanded={shown} aria-controls={listId} aria-activedescendant={shown ? `${listId}-${active}` : undefined} aria-describedby={describedBy}
      onChange={(e) => { onChange(e.target.value); setCaret(e.target.selectionStart ?? e.target.value.length); setOpen(true); }}
      onKeyDown={onKey} onClick={(e) => setCaret(e.currentTarget.selectionStart ?? 0)} onBlur={() => setOpen(false)}
    />
    <ul id={listId} role="listbox" aria-label={t('mails.suggest.label')} className="mailsSuggest" hidden={!shown}>
      {options.map((c, index) => <li key={c.id} id={`${listId}-${index}`} role="option" aria-selected={index === active}
        className={index === active ? 'active' : undefined} onMouseDown={(e) => { e.preventDefault(); pick(c); }}>
        <strong dir="auto">{c.name || c.email}</strong>{c.name && <small dir="ltr">{c.email}</small>}
      </li>)}
    </ul>
  </div>;
}

// -- contacts -------------------------------------------------------------------------------------

const CONTACT_PAGE = 25;

export function ContactsPanel({ canAct, onWrite }: { canAct: boolean; onWrite?: (contact: MailContact) => void }) {
  const [items, setItems] = useState<MailContact[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error' | 'denied'>('loading');
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [form, setForm] = useState<MailContactInput | null>(null);
  const [editing, setEditing] = useState<MailContact | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const { busy, note, setNote, run } = useRun();
  const ids = { search: useId(), name: useId(), email: useId(), company: useId(), phone: useId(), notes: useId(), form: useId() };

  const load = useCallback(async (q: string, after: string | null = null) => {
    if (!after) setState('loading');
    try {
      const page = await listMailContacts({ q: q || undefined, limit: CONTACT_PAGE, cursor: after });
      setItems((current) => after ? appendContacts(current, page.items) : page.items);
      setTotal(page.total); setCursor(page.next_cursor); setState('ready');
    } catch (e) {
      if (after) { setNote({ text: t('mails.contacts.moreFailed', { reason: describeError(e) }), error: true }); return; }
      if (isPermissionDenied(e)) setState('denied'); else { setLoadError(describeError(e)); setState('error'); }
    }
  }, [setNote]);
  useEffect(() => { void load(applied); }, [load, applied]);

  function startForm(contact: MailContact | null) {
    setEditing(contact); setFormError(null);
    setForm(contact ? contactInput(contact) : { ...EMPTY_CONTACT });
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    const problem = contactProblem(form);
    if (problem) { setFormError(problem === 'email' ? t('mails.contacts.badEmail') : t('mails.contacts.badPhone')); return; }
    setFormError(null);
    await run('save', async () => {
      const body = { ...form, email: form.email.trim(), name: form.name.trim(), company: form.company.trim(), phone: form.phone.trim() };
      const saved = editing ? await updateMailContact(editing.id, body) : await createMailContact(body);
      setNote({ text: t('mails.contacts.saved', { name: contactLabel(saved) }) });
      setForm(null); setEditing(null);
      await load(applied);
    });
  }
  const set = (key: keyof MailContactInput) => (e: { target: { value: string } }) => setForm((f) => f ? { ...f, [key]: e.target.value } : f);

  if (state === 'denied') return <Notice note={{ text: t('mails.contacts.denied'), error: true }} />;
  return <div className="mailsContacts">
    <p className="muted">{t('mails.contacts.text')}</p>
    <form className="mailsSearch" role="search" aria-label={t('mails.contacts.search')} onSubmit={(e) => { e.preventDefault(); setApplied(query.trim()); }}>
      <label className="visuallyHidden" htmlFor={ids.search}>{t('mails.contacts.search')}</label>
      <input id={ids.search} type="search" dir="auto" maxLength={200} value={query} placeholder={t('mails.contacts.search')} onChange={(e) => setQuery(e.target.value)} />
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('mails.contacts.searchButton')}</button>
      {canAct && !form && <button type="button" onClick={() => startForm(null)}><Plus aria-hidden="true" />{t('mails.contacts.add')}</button>}
    </form>
    {!canAct && <p className="muted">{t('mails.contacts.readOnly')}</p>}
    <Notice note={note} />

    {form && <form className="mailForm mailsContactForm" aria-labelledby={ids.form} onSubmit={(e) => void save(e)}>
      <h3 id={ids.form}>{editing ? t('mails.contacts.editTitle', { name: contactLabel(editing) }) : t('mails.contacts.new')}</h3>
      <label className="field" htmlFor={ids.name}><span>{t('mails.contacts.name')}</span><input id={ids.name} dir="auto" maxLength={200} autoComplete="off" value={form.name} onChange={set('name')} /></label>
      <label className="field" htmlFor={ids.email}><span>{t('mails.contacts.email')}</span><input id={ids.email} type="email" dir="ltr" required maxLength={320} autoComplete="off" value={form.email} onChange={set('email')} /></label>
      <label className="field" htmlFor={ids.company}><span>{t('mails.contacts.company')}</span><input id={ids.company} dir="auto" maxLength={200} value={form.company} onChange={set('company')} /></label>
      <label className="field" htmlFor={ids.phone}><span>{t('mails.contacts.phone')}</span><input id={ids.phone} type="tel" dir="ltr" maxLength={60} value={form.phone} onChange={set('phone')} /></label>
      <label className="field" htmlFor={ids.notes}><span>{t('mails.contacts.notes')}</span><textarea id={ids.notes} dir="auto" rows={3} maxLength={2000} value={form.notes} onChange={set('notes')} /></label>
      {formError && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{formError}</p>}
      <div className="actions">
        <button type="submit" disabled={busy !== null || !form.email.trim()}>{busy === 'save' ? t('mails.contacts.saving') : t('mails.contacts.save')}</button>
        <button type="button" className="secondary" onClick={() => { setForm(null); setEditing(null); setFormError(null); }}>{t('mails.contacts.cancel')}</button>
      </div>
    </form>}

    {state === 'loading' && <p className="muted" role="status" aria-busy="true">{t('mails.contacts.loading')}</p>}
    {state === 'error' && <><Notice note={{ text: t('mails.contacts.failed', { reason: loadError }), error: true }} /><div className="actions"><button type="button" className="secondary" onClick={() => void load(applied)}><RefreshCw aria-hidden="true" />{t('mail.retry')}</button></div></>}
    {state === 'ready' && items.length === 0 && <div className="paneEmpty mailsEmpty"><Contact aria-hidden="true" />
      {applied ? <><h3>{t('mails.contacts.noMatch', { q: applied })}</h3><div className="actions"><button type="button" className="secondary" onClick={() => { setQuery(''); setApplied(''); }}>{t('mails.contacts.clear')}</button></div></>
        : <><h3>{t('mails.contacts.empty')}</h3><p className="muted">{t('mails.contacts.emptyText')}</p>{canAct && !form && <div className="actions"><button type="button" onClick={() => startForm(null)}><Plus aria-hidden="true" />{t('mails.contacts.add')}</button></div>}</>}
    </div>}
    {state === 'ready' && items.length > 0 && <>
      <p className="muted" aria-live="polite">{t('mails.contacts.shown', { shown: formatNumber(items.length), total: formatNumber(total) })}</p>
      <ul className="mailsList mailsContactList">{items.map((c) => {
        const name = contactLabel(c);
        return <li key={c.id} className="mailsRow">
          <span className="mailsWho"><strong dir="auto">{c.name || c.email}</strong>{c.name && <small dir="ltr">{c.email}</small>}{(c.company || c.phone) && <small dir="auto">{[c.company, c.phone].filter(Boolean).join(' · ')}</small>}</span>
          {canAct && <span className="mailsRowActions">
            {onWrite && <button type="button" className="secondary" disabled={busy !== null} aria-label={t('mails.contacts.writeTo', { name })} onClick={() => onWrite(c)}><PenLine aria-hidden="true" /><span>{t('mails.contacts.write')}</span></button>}
            <button type="button" className="secondary" disabled={busy !== null} aria-label={t('mails.contacts.editFor', { name })} onClick={() => startForm(c)}>{t('mails.contacts.edit')}</button>
            <button type="button" className="secondary danger" disabled={busy !== null} aria-label={t('mails.contacts.deleteFor', { name })} onClick={() => {
              if (!window.confirm(t('mails.contacts.deleteConfirm', { name }))) return;
              void run(`delete:${c.id}`, async () => { await deleteMailContact(c.id); setNote({ text: t('mails.contacts.deleted', { name }) }); if (editing?.id === c.id) { setForm(null); setEditing(null); } await load(applied); });
            }}><Trash2 aria-hidden="true" /></button>
          </span>}
        </li>;
      })}</ul>
      {cursor && <div className="actions"><button type="button" className="secondary" disabled={busy === 'more'} onClick={() => void run('more', () => load(applied, cursor))}>{t('mails.contacts.more')}</button></div>}
    </>}
  </div>;
}
