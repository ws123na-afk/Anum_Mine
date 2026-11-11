// Business, Accounts (docs/business.md#accounts-and-contacts): the client accounts, searched, sorted and paged,
// exported as CSV, created, edited and deleted here; for one account its facts, the do-not-contact mark with its
// reason, its contacts (added, edited, marked do not contact, deleted) and the customer portal block
// (docs/business-portal.md#giving-access). Every change goes to the API, which checks the caller's role: owners and
// members write, owners delete and clear a mark; a refusal shows the server's own sentence.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { ArrowDownUp, Download, Pencil, Plus, Search, Trash2 } from 'lucide-react';
import type { BusinessAccountDetail, BusinessContact, BusinessSetup } from '@anum/contracts';
import { t, type MessageKey } from './lib/i18n';
import {
  ACCOUNT_SORTS, DEFAULT_ORDER, accountBody, accountDraftOf, accountPlace, ariaSort, contactBody, contactDraftOf, emptyAccountDraft, emptyContactDraft, nextOrder,
  type AccountDraft, type AccountOrder, type AccountSort, type ContactDraft,
} from './lib/accounts';
import { CONSENT_BASES, PAGE_SIZE, consentLabel } from './lib/business';
import * as b from './lib/businessApi';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Empty, Note, Pager, ReasonDialog, failure } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';
import { PortalAccessBlock } from './BusinessPortalAdmin';

const SORT_COLUMN: Record<AccountSort, MessageKey> = { name: 'acc.col.name', place: 'acc.col.place', industry: 'acc.facts.industry', updated: 'acc.col.updated' };

export function AccountsTab({ setup, openId, onOpen }: { setup: BusinessSetup; openId: string | null; onOpen: (id: string | null) => void }) {
  const [typed, setTyped] = useState(''), [search, setSearch] = useState(''), [tick, setTick] = useState(0);
  const [order, setOrder] = useState<AccountOrder>(DEFAULT_ORDER);
  const [creating, setCreating] = useState(false), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null), [exporting, setExporting] = useState(false);
  const list = usePaged((offset) => b.listAccounts({ q: search || undefined, sort: order.sort, desc: order.desc, limit: PAGE_SIZE, offset }), [search, order, tick]);
  const searchId = useId(), sortId = useId();
  const filtered = search !== '';
  const write = setup.permissions.write;
  const empty = list.load.kind === 'ready' && list.items.length === 0;
  const refresh = () => setTick((value) => value + 1);
  async function exportCsv() {
    setExporting(true); setNotice(null);
    try { setNotice({ text: t('acc.exported', { name: await b.exportAccounts({ q: search || undefined, sort: order.sort, desc: order.desc }) }) }); }
    catch (error) { setNotice({ text: failure(error, t('acc.export')), error: true }); } finally { setExporting(false); }
  }
  const head = (sort: AccountSort, className?: string) => <th scope="col" className={className} aria-sort={ariaSort(order, sort)}>
    <button type="button" className="bzSortButton" onClick={() => setOrder((current) => nextOrder(current, sort))} aria-label={t('acc.sortBy', { column: t(SORT_COLUMN[sort]) })}>
      {t(SORT_COLUMN[sort])}<ArrowDownUp aria-hidden="true" /></button></th>;
  const newButton = <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('acc.new')}</button>;
  return <section className="bzSection" aria-label={t('biz.tab.accounts')}>
    <p className="muted bzHint">{t('acc.help')}</p>
    {!write && <Note>{t('acc.readOnly')}</Note>}
    <div className="bzToolbar">
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setSearch(typed.trim()); refresh(); }}>
        <label className="visuallyHidden" htmlFor={searchId}>{t('acc.search')}</label>
        <input id={searchId} type="search" value={typed} placeholder={t('acc.search')} dir="auto" onChange={(event) => { setTyped(event.target.value); if (!event.target.value) setSearch(''); }} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
      {/* Below 1024 px the Place column (and its sort button) is hidden, so narrow screens sort here. */}
      <div className="bzSortSelect bzShowMd">
        <label htmlFor={sortId}>{t('acc.sortLabel')}</label>
        <select id={sortId} value={order.sort} onChange={(event) => setOrder({ sort: event.target.value as AccountSort, desc: false })}>
          {ACCOUNT_SORTS.map((sort) => <option key={sort} value={sort}>{t(SORT_COLUMN[sort])}</option>)}
        </select>
        <button type="button" className="secondary" aria-pressed={order.desc} onClick={() => setOrder((current) => ({ ...current, desc: !current.desc }))}>
          <ArrowDownUp aria-hidden="true" />{t('acc.reverse')}</button>
      </div>
      <div className="bzToolbarEnd">
        {list.load.kind === 'ready' && list.total > 0 && <button type="button" className="secondary" disabled={exporting} title={t('acc.exportHelp')} onClick={() => void exportCsv()}>
          <Download aria-hidden="true" />{exporting ? t('acc.exporting') : t('acc.export')}</button>}
        {write && newButton}
      </div>
    </div>
    {notice && <Note error={notice.error}>{notice.text}</Note>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setTyped(''); setSearch(''); }} />
    {empty && !filtered && <Empty title={t('acc.emptyTitle')} text={write ? t('acc.emptyText') : t('acc.emptyMember')} action={write ? newButton : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <p className="bzCount" aria-live="polite">{t('acc.count', { count: list.total })}. {t('acc.sortedBy', { column: t(SORT_COLUMN[order.sort]), direction: t(order.desc ? 'acc.descending' : 'acc.ascending') })}</p>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('acc.title')}</caption>
        <thead><tr>{head('name')}{head('place', 'bzHideMd')}<th scope="col" className="num">{t('acc.col.contacts')}</th><th scope="col" className="num bzHideMd">{t('acc.col.leads')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(item.id)}><span dir="auto">{item.name}</span></button>
            {item.do_not_contact && <Badge tone="dangerTone">{t('acc.dnc')}</Badge>}<small dir="auto">{item.industry}</small></th>
          <td className="bzHideMd" dir="auto">{accountPlace(item) || '-'}</td>
          <td className="num">{item.contacts}</td>
          <td className="num bzHideMd">{item.open_leads}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {creating && <AccountForm onClose={() => setCreating(false)} onSaved={(account) => { setCreating(false); setNotice({ text: t('acc.created', { name: account.name }) }); refresh(); onOpen(account.id); }} />}
    {openId && <AccountDrawer setup={setup} id={openId} onClose={() => onOpen(null)} onChanged={refresh}
      onDeleted={(name) => { onOpen(null); setNotice({ text: t('acc.deleted', { name }) }); refresh(); }} />}
  </section>;
}

type Asking = { kind: 'edit' } | { kind: 'delete' } | { kind: 'dnc' } | { kind: 'addContact' } | { kind: 'editContact'; contact: BusinessContact }
  | { kind: 'deleteContact'; contact: BusinessContact } | { kind: 'contactDnc'; contact: BusinessContact } | null;
type Message = { text: string; error?: boolean } | null;

function AccountDrawer({ setup, id, onClose, onChanged, onDeleted }: { setup: BusinessSetup; id: string; onClose: () => void; onChanged: () => void; onDeleted: (name: string) => void }) {
  const [account, setAccount] = useState<BusinessAccountDetail | null>(null), [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState<Asking>(null), [busy, setBusy] = useState(false), [message, setMessage] = useState<Message>(null);
  const write = setup.permissions.write, manage = setup.permissions.manage;
  const reload = useCallback(async () => {
    setProblem(null);
    try { setAccount(await b.getAccount(id)); } catch (error) { setProblem(failure(error, t('acc.load'))); }
  }, [id]);
  useEffect(() => { setAccount(null); void reload(); }, [reload]);
  /** Runs one change; the server's refusal (who may do it, or why a record is kept) becomes the message. */
  async function act(what: string, work: () => Promise<void>, done: string) {
    setBusy(true); setMessage(null);
    try { await work(); setAsking(null); setMessage({ text: done }); await reload(); onChanged(); } catch (error) { setMessage({ text: failure(error, what), error: true }); } finally { setBusy(false); }
  }
  async function remove() {
    if (!account) return;
    setBusy(true); setMessage(null);
    try { await b.deleteAccount(account.id); onDeleted(account.name); } catch (error) { setAsking(null); setMessage({ text: failure(error, t('acc.delete')), error: true }); } finally { setBusy(false); }
  }
  if (!account) {
    return <Drawer title={t('acc.detail')} onClose={onClose}>
      {problem ? <><Note error>{problem}</Note><div className="bzInline"><button type="button" className="secondary" onClick={() => void reload()}>{t('common.tryAgain')}</button></div></> : <Note>{t('acc.load')}</Note>}
    </Drawer>;
  }
  const dncLabel = (on: boolean) => (on ? t('acc.clearDnc') : t('acc.markDnc'));
  return <Drawer title={account.name} onClose={onClose}>
    <div className="bzDrawer">
      {message && <Note error={message.error}>{message.text}</Note>}
      <dl className="bzFacts">
        <div><dt>{t('acc.facts.website')}</dt><dd>{account.website ? <a href={account.website} target="_blank" rel="noopener noreferrer" dir="ltr">{account.domain ?? account.website}</a> : t('biz.unknown')}</dd></div>
        <div><dt>{t('acc.facts.industry')}</dt><dd dir="auto">{account.industry || t('biz.unknown')}</dd></div>
        <div><dt>{t('acc.facts.place')}</dt><dd dir="auto">{accountPlace(account) || t('biz.unknown')}</dd></div>
        {account.notes && <div><dt>{t('acc.facts.notes')}</dt><dd dir="auto" className="invAddress">{account.notes}</dd></div>}
      </dl>
      {account.do_not_contact && <p className="bzBlocked" role="note"><span dir="auto">{t('acc.dncReason', { reason: account.dnc_reason || '-' })}</span></p>}
      {write && <div className="bzInline" role="group" aria-label={t('acc.actions')}>
        <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'edit' })}><Pencil aria-hidden="true" />{t('acc.edit')}</button>
        {(!account.do_not_contact || manage) && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'dnc' })}>{dncLabel(account.do_not_contact)}</button>}
        {manage && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'delete' })}><Trash2 aria-hidden="true" />{t('acc.delete')}</button>}
      </div>}
      {write && !manage && <p className="muted bzHint">{t('acc.ownersOnly')}</p>}
      <section className="bzBlock" aria-labelledby={`ac-${account.id}`}>
        <div className="bzInline"><h3 id={`ac-${account.id}`}>{t('acc.contacts')}</h3>
          {write && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'addContact' })}><Plus aria-hidden="true" />{t('acc.contact.add')}</button>}</div>
        {account.contacts.length === 0 ? <p className="muted">{t('acc.noContacts')}</p>
          : <ul className="bzTrail">{account.contacts.map((contact) => <li key={contact.id}>
            <div className="bzInline"><strong dir="auto">{contact.name}</strong>{contact.role_title && <span className="muted" dir="auto">{contact.role_title}</span>}
              {contact.do_not_contact && <Badge tone="dangerTone">{t('acc.dnc')}</Badge>}</div>
            <small dir="ltr">{[contact.email, contact.phone].filter(Boolean).join(' / ') || t('acc.noChannel')}</small>
            <small dir="auto">{consentLabel(contact.consent_basis)}{contact.do_not_contact && contact.dnc_reason ? `. ${contact.dnc_reason}` : contact.block_reason ? `. ${contact.block_reason}` : ''}</small>
            {write && <div className="bzInline" role="group" aria-label={t('acc.contact.actions', { name: contact.name })}>
              <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'editContact', contact })}><Pencil aria-hidden="true" />{t('acc.contact.edit')}</button>
              {(!contact.do_not_contact || manage) && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'contactDnc', contact })}>{dncLabel(contact.do_not_contact)}</button>}
              {manage && <button type="button" className="secondary" disabled={busy} onClick={() => setAsking({ kind: 'deleteContact', contact })}><Trash2 aria-hidden="true" />{t('acc.contact.delete')}</button>}
            </div>}
          </li>)}</ul>}
      </section>
      <PortalAccessBlock setup={setup} accountId={account.id} />
    </div>
    {asking?.kind === 'edit' && <AccountForm account={account} onClose={() => setAsking(null)} onSaved={() => { setAsking(null); setMessage({ text: t('acc.saved') }); void reload(); onChanged(); }} />}
    {asking?.kind === 'delete' && <ConfirmDialog title={t('acc.delete')} confirmLabel={t('common.delete')} danger busy={busy} onCancel={() => setAsking(null)} onConfirm={() => void remove()}>
      <p>{t('acc.deleteConfirm', { name: account.name })}</p>
    </ConfirmDialog>}
    {asking?.kind === 'dnc' && <ReasonDialog title={dncLabel(account.do_not_contact)} label={t('acc.dncWhy')} confirm={dncLabel(account.do_not_contact)} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => {
        const on = !account.do_not_contact;
        await b.accountDoNotContact(account.id, on, reason);
        setAsking(null); setMessage({ text: t(on ? 'acc.dncOn' : 'acc.dncOff', { name: account.name }) }); await reload(); onChanged();
      }} />}
    {asking?.kind === 'addContact' && <ContactForm accountId={account.id} onClose={() => setAsking(null)} onSaved={(name) => { setAsking(null); setMessage({ text: t('acc.contact.added', { name }) }); void reload(); onChanged(); }} />}
    {asking?.kind === 'editContact' && <ContactForm accountId={account.id} contact={asking.contact} onClose={() => setAsking(null)} onSaved={() => { setAsking(null); setMessage({ text: t('acc.contact.saved') }); void reload(); }} />}
    {asking?.kind === 'deleteContact' && <ConfirmDialog title={t('acc.contact.delete')} confirmLabel={t('common.delete')} danger busy={busy} onCancel={() => setAsking(null)}
      onConfirm={() => void act(t('acc.contact.delete'), async () => { await b.deleteContact(asking.contact.id); }, t('acc.contact.deleted'))}>
      <p>{t('acc.contact.deleteConfirm', { name: asking.contact.name })}</p>
    </ConfirmDialog>}
    {asking?.kind === 'contactDnc' && <ReasonDialog title={dncLabel(asking.contact.do_not_contact)} label={t('acc.dncWhy')} confirm={dncLabel(asking.contact.do_not_contact)} onCancel={() => setAsking(null)}
      onConfirm={async (reason) => {
        const contact = asking.contact, on = !contact.do_not_contact;
        await b.contactDoNotContact(contact.id, on, reason);
        setAsking(null); setMessage({ text: t(on ? 'acc.contact.dncOn' : 'acc.contact.dncOff', { name: contact.name }) }); await reload(); onChanged();
      }} />}
  </Drawer>;
}

/** New or edited account. The server keeps one account per website domain (its 409 names the other account). */
function AccountForm({ account, onClose, onSaved }: { account?: BusinessAccountDetail; onClose: () => void; onSaved: (saved: BusinessAccountDetail) => void }) {
  const [draft, setDraft] = useState<AccountDraft>(() => (account ? accountDraftOf(account) : emptyAccountDraft()));
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const title = account ? t('acc.edit') : t('acc.new');
  const set = (key: keyof AccountDraft) => (event: { target: { value: string } }) => setDraft((current) => ({ ...current, [key]: event.target.value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = accountBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try { onSaved(account ? await b.updateAccount(account.id, body.value) : await b.createAccount(body.value)); }
    catch (error) { setProblem(failure(error, title)); setBusy(false); }
  }
  return <Modal title={title} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={title} className="bzForm">
      <div className="bzGrid2">
        <label className="field"><span>{t('biz.field.company')}</span><input value={draft.name} onChange={set('name')} required maxLength={200} dir="auto" data-autofocus /></label>
        <label className="field"><span>{t('biz.field.website')}</span><input value={draft.website} onChange={set('website')} inputMode="url" placeholder="https://" dir="ltr" maxLength={500} /></label>
        <label className="field"><span>{t('biz.field.industry')}</span><input value={draft.industry} onChange={set('industry')} maxLength={120} dir="auto" /></label>
        <label className="field"><span>{t('biz.field.city')}</span><input value={draft.city} onChange={set('city')} maxLength={80} dir="auto" /></label>
        <label className="field"><span>{t('acc.form.country')}</span><input value={draft.country} onChange={set('country')} maxLength={80} dir="auto" /></label>
      </div>
      <label className="field"><span>{t('acc.facts.notes')}</span><textarea value={draft.notes} onChange={set('notes')} rows={3} maxLength={4000} dir="auto" /></label>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : (account ? t('acc.form.save') : t('acc.form.add'))}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

/** New or edited contact. One contact per e-mail address in the workspace (409); consent `unknown` blocks drafts. */
function ContactForm({ accountId, contact, onClose, onSaved }: { accountId: string; contact?: BusinessContact; onClose: () => void; onSaved: (name: string) => void }) {
  const [draft, setDraft] = useState<ContactDraft>(() => (contact ? contactDraftOf(contact) : emptyContactDraft()));
  const [problem, setProblem] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const title = contact ? t('acc.contact.edit') : t('acc.contact.add');
  const set = (key: keyof ContactDraft) => (event: { target: { value: string } }) => setDraft((current) => ({ ...current, [key]: event.target.value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = contactBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    setBusy(true); setProblem(null);
    try {
      if (contact) await b.updateContact(contact.id, body.value); else await b.addContact(accountId, body.value);
      onSaved(String(body.value.name));
    } catch (error) { setProblem(failure(error, title)); setBusy(false); }
  }
  return <Modal title={title} className="formDialog bzWideDialog" onClose={onClose}>
    <form onSubmit={(event) => void submit(event)} aria-label={title} className="bzForm">
      <div className="bzGrid2">
        <label className="field"><span>{t('biz.field.contactName')}</span><input value={draft.name} onChange={set('name')} required maxLength={160} dir="auto" data-autofocus /></label>
        <label className="field"><span>{t('acc.form.role')}</span><input value={draft.roleTitle} onChange={set('roleTitle')} maxLength={160} dir="auto" /></label>
        <label className="field"><span>{t('biz.field.email')}</span><input value={draft.email} onChange={set('email')} type="email" maxLength={254} dir="ltr" /></label>
        <label className="field"><span>{t('biz.field.phone')}</span><input value={draft.phone} onChange={set('phone')} inputMode="tel" maxLength={40} dir="ltr" /></label>
        <label className="field"><span>{t('acc.form.profile')}</span><input value={draft.profileUrl} onChange={set('profileUrl')} inputMode="url" placeholder="https://" maxLength={500} dir="ltr" /></label>
        <label className="field"><span>{t('biz.field.consent')}</span><select value={draft.consent} onChange={set('consent')}>{CONSENT_BASES.map((basis) => <option key={basis} value={basis}>{consentLabel(basis)}</option>)}</select></label>
      </div>
      <label className="field"><span>{t('acc.form.consentNote')}</span><input value={draft.consentNote} onChange={set('consentNote')} maxLength={500} dir="auto" /></label>
      <p className="muted bzHint">{t('acc.form.consentHint')}</p>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : (contact ? t('acc.form.save') : t('acc.form.add'))}</button><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}
