// The owner's side of the customer portal (docs/business-portal.md#giving-access): who has a link,
// minting one (owners only, shown once with a copy button), turning one off, and what a customer
// decided on a proposal or a change order. The link is a credential: it is held in this component's
// state until it is closed and is never written to storage or the address bar.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { Copy, KeyRound } from 'lucide-react';
import type { BusinessContact, BusinessSetup, PortalAccessCreated, PortalAccessItem, PortalDecisionSummary } from '@anum/contracts';
import { t } from './lib/i18n';
import { copyText } from './lib/clipboard';
import { accessLabel, accessState, accessTone, buildPortalLink, decisionLine } from './lib/portal';
import * as b from './lib/businessApi';
import * as inv from './lib/businessInvoicesApi';
import { ConfirmDialog, Modal } from './Dialog';
import { Badge, Note, failure, when } from './BusinessParts';

export function PortalAccessBlock({ setup, accountId }: { setup: BusinessSetup; accountId: string }) {
  const [contacts, setContacts] = useState<BusinessContact[]>([]), [items, setItems] = useState<PortalAccessItem[]>([]);
  const [load, setLoad] = useState<'loading' | 'ready' | { error: string }>('loading');
  const [creating, setCreating] = useState(false), [created, setCreated] = useState<PortalAccessCreated | null>(null);
  const [revoking, setRevoking] = useState<PortalAccessItem | null>(null), [problem, setProblem] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
  const heading = useId();
  const manage = setup.permissions.manage;
  const reload = useCallback(async () => {
    setLoad('loading');
    try {
      const [account, access] = await Promise.all([b.getAccount(accountId), inv.listPortalAccess(accountId)]);
      setContacts(account.contacts); setItems(access); setLoad('ready');
    } catch (error) { setLoad({ error: failure(error, t('inv.portal.title')) }); }
  }, [accountId]);
  useEffect(() => { void reload(); }, [reload]);
  async function revoke(item: PortalAccessItem) {
    setProblem(null);
    try { const next = await inv.revokePortalAccess(item.id); setItems((old) => old.map((entry) => (entry.id === next.id ? next : entry))); setRevoking(null); setNotice(t('inv.portal.revoked', { name: item.contact_name })); }
    catch (error) { setRevoking(null); setProblem(failure(error, t('inv.portal.revoke'))); }
  }
  return <section className="bzBlock" aria-labelledby={heading}>
    <div className="bzInline"><h3 id={heading}>{t('inv.portal.title')}</h3></div>
    <p className="muted bzHint">{t('inv.portal.help')}</p>
    {load === 'loading' && <Note>{t('biz.loading')}</Note>}
    {typeof load === 'object' && <Note error>{load.error} <button type="button" className="secondary" onClick={() => void reload()}>{t('common.tryAgain')}</button></Note>}
    <div aria-live="polite">{notice && <Note>{notice}</Note>}</div>
    {problem && <Note error>{problem}</Note>}
    {load === 'ready' && <>
      {items.length === 0 ? <p className="muted">{t('inv.portal.none')}</p> : <ul className="bzTrail invAccess">{items.map((item) => {
        const state = accessState(item);
        return <li key={item.id}>
          <div className="bzInline"><strong dir="auto">{item.contact_name}</strong><Badge tone={accessTone(state)}>{accessLabel(state)}</Badge>
            <span className="muted">{item.can_decide ? t('inv.portal.canApprove') : t('inv.portal.readOnly')}</span>
            {manage && state === 'active' && <button type="button" className="secondary" onClick={() => setRevoking(item)}>{t('inv.portal.revoke')}</button>}</div>
          <small>{t('inv.portal.expires', { when: when(item.expires_at) })}{' / '}{item.last_used_at ? t('inv.portal.lastUsed', { when: when(item.last_used_at) }) : t('inv.portal.neverUsed')}</small>
        </li>;
      })}</ul>}
      {manage
        ? <div className="bzInline"><button type="button" className="secondary" disabled={contacts.length === 0} onClick={() => { setCreated(null); setNotice(null); setCreating(true); }}><KeyRound aria-hidden="true" />{t('inv.portal.create')}</button>
          {contacts.length === 0 && <span className="muted bzHint">{t('inv.portal.noContacts')}</span>}</div>
        : <p className="muted bzHint">{t('inv.portal.ownersOnly')}</p>}
    </>}
    {creating && <CreateLinkDialog accountId={accountId} contacts={contacts} onClose={() => setCreating(false)} onCreated={(link) => { setCreating(false); setCreated(link); void reload(); }} />}
    {created && <LinkShown link={created} onClose={() => setCreated(null)} />}
    {revoking && <ConfirmDialog title={t('inv.portal.revokeTitle', { name: revoking.contact_name })} confirmLabel={t('inv.portal.revoke')} danger onCancel={() => setRevoking(null)} onConfirm={() => void revoke(revoking)}>
      <p>{t('inv.portal.revokeText')}</p></ConfirmDialog>}
  </section>;
}

function CreateLinkDialog({ accountId, contacts, onClose, onCreated }: { accountId: string; contacts: BusinessContact[]; onClose: () => void; onCreated: (link: PortalAccessCreated) => void }) {
  const first = contacts.find((contact) => !contact.do_not_contact);
  const [contactId, setContactId] = useState(first?.id ?? ''), [days, setDays] = useState('90'), [canDecide, setCanDecide] = useState(true);
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const expires = Number(days.trim());
    if (!contactId) { setProblem(t('inv.portal.pickContact')); return; }
    if (!Number.isInteger(expires) || expires < 1 || expires > 365) { setProblem(t('inv.portal.daysInvalid')); return; }
    setBusy(true); setProblem(null);
    try { onCreated(await inv.createPortalAccess({ account_id: accountId, contact_id: contactId, expires_in_days: expires, can_decide: canDecide })); }
    catch (error) { setProblem(failure(error, t('inv.portal.create'))); setBusy(false); }
  }
  return <Modal title={t('inv.portal.create')} className="formDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={t('inv.portal.create')} noValidate>
      <p className="muted bzHint">{t('inv.portal.createHelp')}</p>
      <label className="field"><span>{t('inv.portal.contact')}</span>
        <select value={contactId} onChange={(event) => setContactId(event.target.value)} data-autofocus>{contacts.map((contact) => <option key={contact.id} value={contact.id} disabled={contact.do_not_contact}>
          {contact.name}{contact.do_not_contact ? ` (${t('inv.portal.dnc')})` : ''}</option>)}</select></label>
      <label className="field"><span>{t('inv.portal.days')}</span><input inputMode="numeric" value={days} onChange={(event) => setDays(event.target.value)} /></label>
      <label className="bzCheck"><input type="checkbox" checked={canDecide} onChange={(event) => setCanDecide(event.target.checked)} />{t('inv.portal.allowApprove')}</label>
      <p className="muted bzHint">{canDecide ? t('inv.portal.approveOn') : t('inv.portal.approveOff')}</p>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('inv.portal.createButton')}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

/** The new link, once. Closing it forgets it: only the hash is stored, so it cannot be shown again. */
function LinkShown({ link, onClose }: { link: PortalAccessCreated; onClose: () => void }) {
  const address = buildPortalLink(window.location.origin, link.portal_path);
  const [copied, setCopied] = useState<'idle' | 'yes' | 'no'>('idle');
  const field = useRef<HTMLInputElement>(null);
  const id = useId();
  return <Modal title={t('inv.portal.linkTitle', { name: link.contact_name })} className="formDialog" onClose={onClose}>
    <p className="notice errorNotice" role="note">{t('inv.portal.credential')}</p>
    <label className="field" htmlFor={id}><span>{t('inv.portal.linkLabel')}</span>
      <input id={id} ref={field} readOnly value={address} dir="ltr" onFocus={(event) => event.currentTarget.select()} data-autofocus /></label>
    <div className="bzInline"><button type="button" onClick={() => void copyText(address).then((ok) => setCopied(ok ? 'yes' : 'no'))}><Copy aria-hidden="true" />{t('inv.portal.copy')}</button></div>
    <p role="status" className="muted bzHint">{copied === 'yes' ? t('inv.portal.copied') : copied === 'no' ? t('inv.portal.copyFailed') : ''}</p>
    <p className="muted bzHint">{link.can_decide ? t('inv.portal.sharedApprove') : t('inv.portal.sharedRead')} {t('inv.portal.expires', { when: when(link.expires_at) })}</p>
    <p className="muted bzHint">{t('inv.portal.onlyOnce')}</p>
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('inv.portal.done')}</button></div>
  </Modal>;
}

/** "Approved by Sara in the portal on 3 Nov", with the note, and whether it is about the version waiting. */
export function PortalDecisionNote({ decision }: { decision: PortalDecisionSummary | null }) {
  if (!decision) return null;
  return <div className="bzApproval" role="group" aria-label={t('inv.portal.decisionLabel')}>
    <p className="bzApprovalTitle">{decisionLine(decision, when)}</p>
    {decision.note && <p dir="auto" className="invAddress">{t('inv.portal.decisionNote', { note: decision.note })}</p>}
    <p className="muted bzHint">{decision.current ? t('inv.portal.decisionCurrent') : t('inv.portal.decisionOlder', { version: decision.version ?? 0 })}</p>
    <p className="muted bzHint">{t('inv.portal.decisionRecord')}</p>
  </div>;
}
