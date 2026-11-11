// The Contacts directory (docs/contacts.md#web): one person per human in the workspace over the Phone
// calls, Business and WhatsApp contact lists. People (search, filters, sort, paging, export), the person
// page (linked records, who may contact them per channel, do-not-contact controls, merge, edit, delete) and
// the possible duplicates a person decides. Members read and write; owners clear marks, delete and export;
// viewers get the API's 403, explained in place.
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowLeft, AtSign, BellOff, BookUser, Check, Download, ExternalLink, FileUp, GitMerge, Handshake, Lock, MessageCircle, Pencil, Phone, PhoneOutgoing, Plus, RefreshCw, RotateCcw, Search, Trash2, Upload, Users, X } from 'lucide-react';
import { ConfirmDialog, Modal } from './Dialog';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { describeError } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { viewLabel } from './lib/shell';
import {
  CHANNELS, CONTACT_TABS, IMPORT_MAX_ROWS, IMPORT_PAGE, IMPORT_STATUSES, emptyMapping, importActionLabel, importDecisions, importFieldLabel, importFileProblem,
  importIssues, importRowsShown, importStatusLabel, importTotals,
  type ImportAction, type ImportChoice, type ImportMapping, type ImportPreview, type ImportResult, type ImportRow, type ImportStatus, DNC_FILTERS, NO_FILTERS, PAGE_SIZE, SORTS, SOURCES, blockedChannels, channelLabel, contactsHash, dncFilterLabel, draftOf,
  emptyPersonDraft, factText, filtered, linkKindLabel, parseContactsHash, personBody, reasonLabel, reasonText, sortLabel, sourceLabel, tabLabel,
  type Channel, type ChannelSummary, type ContactChannel, type ContactTab, type PeopleFilters, type PersonDetail, type PersonDraft, type PersonView, type ProposalView,
} from './lib/contacts';
import * as api from './lib/contactsApi';
import './admin.css';
import './commerce.css';
import './contacts.css';

type Flash = { text: string; error?: boolean } | null;
const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });
const SOURCE_ICON: Record<string, ReactNode> = { call_contact: <PhoneOutgoing aria-hidden="true" />, business_contact: <Handshake aria-hidden="true" />, whatsapp_contact: <MessageCircle aria-hidden="true" /> };
const CHANNEL_ICON: Record<ContactChannel, ReactNode> = { phone: <Phone aria-hidden="true" />, email: <AtSign aria-hidden="true" />, whatsapp: <MessageCircle aria-hidden="true" /> };
const MODULE_OF: Record<string, MessageKey> = { call_contact: 'nav.calls', business_contact: 'nav.business', whatsapp_contact: 'nav.whatsapp' };

function useContactsHash(): [ContactTab, string | null, (tab: ContactTab, person?: string | null) => void] {
  const [state, setState] = useState(() => parseContactsHash(typeof location === 'undefined' ? '' : location.hash));
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#contacts')) setState(parseContactsHash(location.hash)); };
    window.addEventListener('popstate', sync); window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);
  const go = (tab: ContactTab, person: string | null = null) => {
    setState({ tab, person });
    const next = contactsHash(tab, person);
    if (location.hash !== next) history.pushState(null, '', next);
  };
  return [state.tab, state.person, go];
}

export function ContactsDenied({ detail }: { detail: string }) {
  return <section className="surface adminForbidden" aria-label={t('ct.deniedTitle')}>
    <span className="adminIcon"><Lock aria-hidden="true" /></span>
    <div><h2>{t('ct.deniedTitle')}</h2><p>{t('ct.deniedText')}</p><p className="muted">{t('ct.apiAnswered', { message: detail })}</p></div>
  </section>;
}

function Identifiers({ person }: { person: PersonView }) {
  if (!person.phones.length && !person.emails.length) return <span className="muted">-</span>;
  return <ul className="ctIdentifiers">
    {person.phones.map((i) => <li key={`p-${i.value}`} dir="ltr"><Phone aria-hidden="true" />{i.value}</li>)}
    {person.emails.map((i) => <li key={`e-${i.value}`} dir="ltr"><AtSign aria-hidden="true" />{i.value}</li>)}
  </ul>;
}

function SourceChips({ person }: { person: PersonView }) {
  const kinds = person.sources.length ? person.sources : [];
  return <div className="tagRow ctSources">
    {kinds.map((kind) => <span key={kind} className="ctSource">{SOURCE_ICON[kind]}{linkKindLabel(kind)}</span>)}
    {!kinds.length && <span className="ctSource"><Pencil aria-hidden="true" />{sourceLabel(person.source)}</span>}
  </div>;
}

function StatusChips({ person }: { person: PersonView }) {
  const blocked = blockedChannels(person.do_not_contact);
  return <div className="ctStatus">
    {blocked.length > 0 && <StatusChip tone="bad">{t('ct.dncShort', { channels: blocked.map(channelLabel).join(', ') })}</StatusChip>}
    {person.open_proposals > 0 && <StatusChip tone="warn">{t('ct.proposalsShort', { count: person.open_proposals })}</StatusChip>}
    {!blocked.length && !person.open_proposals && <span className="muted">-</span>}
  </div>;
}

// ---------------------------------------------------------------- the person form

function PersonForm({ title, initial, busy, error, onSubmit, onClose }: {
  title: string; initial: PersonDraft; busy: boolean; error: string | null; onSubmit: (draft: PersonDraft) => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const ids = { name: useId(), org: useId(), notes: useId(), phones: useId(), emails: useId(), phonesHint: useId(), emailsHint: useId() };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body = personBody(draft);
    if (!body.ok) { setProblem(body.error); return; }
    setProblem(null); onSubmit(draft);
  };
  const shown = problem ?? error;
  return <Modal title={title} className="ctFormDialog" onClose={onClose}>
    <form className="ctForm" noValidate onSubmit={submit}>
      <label className="field" htmlFor={ids.name}><span>{t('ct.form.name')}</span><input id={ids.name} dir="auto" value={draft.name} maxLength={200} required data-autofocus onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
      <label className="field" htmlFor={ids.org}><span>{t('ct.form.organisation')}</span><input id={ids.org} dir="auto" value={draft.organisation} maxLength={200} onChange={(e) => setDraft({ ...draft, organisation: e.target.value })} /></label>
      <div className="ctPair">
        <label className="field" htmlFor={ids.phones}><span>{t('ct.form.phones')}</span><textarea id={ids.phones} dir="ltr" rows={3} value={draft.phones} aria-describedby={ids.phonesHint} onChange={(e) => setDraft({ ...draft, phones: e.target.value })} /></label>
        <label className="field" htmlFor={ids.emails}><span>{t('ct.form.emails')}</span><textarea id={ids.emails} dir="ltr" rows={3} value={draft.emails} aria-describedby={ids.emailsHint} onChange={(e) => setDraft({ ...draft, emails: e.target.value })} /></label>
      </div>
      <div className="ctPair ctHints"><small className="muted" id={ids.phonesHint}>{t('ct.form.phonesHint')}</small><small className="muted" id={ids.emailsHint}>{t('ct.form.emailsHint')}</small></div>
      <label className="field" htmlFor={ids.notes}><span>{t('ct.form.notes')}</span><textarea id={ids.notes} dir="auto" rows={3} value={draft.notes} maxLength={4000} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} /></label>
      {shown && <Notice error>{shown}</Notice>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('ct.form.saving') : t('ct.form.save')}</button><button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button></div>
    </form>
  </Modal>;
}

// ---------------------------------------------------------------- the merge picker

function MergeDialog({ keep, busy, error, onMerge, onClose }: { keep: PersonView; busy: boolean; error: string | null; onMerge: (other: PersonView) => void; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<Load<PersonView[]>>({ kind: 'ready', value: [] });
  const [picked, setPicked] = useState<PersonView | null>(null);
  const searchId = useId();
  useEffect(() => {
    const q = query.trim();
    if (!q) { setFound({ kind: 'ready', value: [] }); return; }
    let live = true;
    setFound({ kind: 'loading' });
    const timer = window.setTimeout(() => {
      api.listPeople({ ...NO_FILTERS, q }, 0, 10).then((page) => { if (live) setFound({ kind: 'ready', value: page.items.filter((p) => p.id !== keep.id) }); })
        .catch((e) => { if (live) setFound(failed(e)); });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query, keep.id]);
  return <Modal title={t('ct.merge.title', { name: keep.name })} className="ctFormDialog" onClose={onClose}>
    <p className="muted">{t('ct.merge.body', { name: keep.name })}</p>
    <label className="field" htmlFor={searchId}><span>{t('ct.merge.search')}</span><input id={searchId} type="search" dir="auto" value={query} data-autofocus onChange={(e) => { setQuery(e.target.value); setPicked(null); }} /></label>
    {found.kind === 'loading' && <Notice busy>{t('common.loading')}</Notice>}
    {found.kind === 'error' && <Notice error>{found.message}</Notice>}
    {found.kind === 'ready' && query.trim() && !found.value.length && <p className="muted">{t('ct.merge.none')}</p>}
    {found.kind === 'ready' && found.value.length > 0 && <ul className="ctPickList" role="listbox" aria-label={t('ct.merge.search')}>
      {found.value.map((p) => <li key={p.id}><button type="button" role="option" aria-selected={picked?.id === p.id} className={picked?.id === p.id ? 'ctPicked' : undefined} onClick={() => setPicked(p)}>
        <strong dir="auto">{p.name}</strong><small>{[p.organisation, ...p.phones.map((i) => i.value), ...p.emails.map((i) => i.value)].filter(Boolean).join(' · ')}</small>
      </button></li>)}
    </ul>}
    {error && <Notice error>{error}</Notice>}
    <div className="actions">
      <button type="button" disabled={busy || !picked} onClick={() => picked && onMerge(picked)}><GitMerge aria-hidden="true" />{picked ? t('ct.merge.pick', { other: picked.name, name: keep.name }) : t('ct.merge.confirm')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={onClose}>{t('common.cancel')}</button>
    </div>
  </Modal>;
}

// ---------------------------------------------------------------- the person page

function ChannelCard({ summary, canWrite, canManage, busy, onMark, onClear }: {
  summary: ChannelSummary; canWrite: boolean; canManage: boolean; busy: boolean; onMark: (channel: Channel) => void; onClear: (channel: Channel) => void;
}) {
  const mark = summary.do_not_contact;
  return <li className={`ctChannel ${summary.can_contact ? '' : 'ctBlocked'}`}>
    <div className="ctChannelHead">
      <span className="ctChannelIcon">{CHANNEL_ICON[summary.channel]}</span>
      <h3>{channelLabel(summary.channel)}</h3>
      <StatusChip tone={summary.can_contact ? 'ok' : 'bad'}>{summary.can_contact ? t('ct.person.canContact') : t('ct.person.cannotContact')}</StatusChip>
    </div>
    {summary.identifiers.length ? <ul className="ctIdentifiers">{summary.identifiers.map((v) => <li key={v} dir="ltr">{v}</li>)}</ul> : <p className="muted">{t('ct.person.noIdentifiers', { channel: channelLabel(summary.channel) })}</p>}
    {summary.facts.length > 0 && <ul className="ctFacts">{summary.facts.map((fact, i) => <li key={i} className={fact.state === 'blocked' || fact.state === 'withdrawn' ? 'ctFactBad' : fact.state === 'granted' || fact.state === 'customer_wrote' ? 'ctFactOk' : ''}>{factText(fact)}{fact.at && <small> · {when(fact.at)}</small>}</li>)}</ul>}
    {mark && <p className="ctDncLine">{t('ct.person.dncLine', { channel: channelLabel(mark.channel), reason: mark.reason, who: mark.set_by, when: when(mark.set_at) })}</p>}
    <div className="actions">
      {!mark && canWrite && <button type="button" className="secondary" disabled={busy} onClick={() => onMark(summary.channel)}><BellOff aria-hidden="true" />{t('ct.person.markDnc')}</button>}
      {mark && (canManage
        ? <button type="button" className="secondary" disabled={busy} onClick={() => onClear(mark.channel)}><Check aria-hidden="true" />{t('ct.person.clearDnc')}</button>
        : <small className="muted">{t('ct.dnc.ownersOnly')}</small>)}
    </div>
  </li>;
}

function ProposalRow({ proposal, canWrite, busy, onKeep, onDismiss, onOpen, currentId }: {
  proposal: ProposalView; canWrite: boolean; busy: boolean; onKeep: (keep: PersonView, other: PersonView) => void; onDismiss: (proposal: ProposalView) => void; onOpen: (id: string) => void;
  /** The person whose page shows this row: named, not linked to itself. */
  currentId?: string;
}) {
  const { a, b } = proposal;
  return <li className="ctProposal">
    <div className="ctProposalHead"><StatusChip tone="warn">{reasonLabel(proposal.reason)}</StatusChip><span dir="ltr" className="ctShared">{proposal.matched_value}</span><small className="muted">{when(proposal.created_at)}</small></div>
    {a && b ? <div className="ctPairCards">
      {[a, b].map((p) => <div key={p.id} className="ctPairCard">
        {p.id === currentId ? <strong dir="auto">{p.name}</strong> : <button type="button" className="ctLinkButton" onClick={() => onOpen(p.id)}><strong dir="auto">{p.name}</strong></button>}
        {p.organisation && <small dir="auto">{p.organisation}</small>}
        <Identifiers person={p} /><SourceChips person={p} />
        {canWrite && <button type="button" className="secondary" disabled={busy} title={t('ct.proposals.keepHint')} onClick={() => onKeep(p, p.id === a.id ? b : a)}><GitMerge aria-hidden="true" />{t(p.id === a.id ? 'ct.proposals.keepA' : 'ct.proposals.keepB', { name: p.name })}</button>}
      </div>)}
    </div> : <p className="muted">{t('ct.proposals.gone')}</p>}
    {canWrite && <div className="actions"><button type="button" className="secondary" disabled={busy} onClick={() => onDismiss(proposal)}><X aria-hidden="true" />{t('ct.proposals.dismiss')}</button></div>}
  </li>;
}

function PersonPage({ id, onBack, onOpen, onChanged, flash }: { id: string; onBack: () => void; onOpen: (id: string) => void; onChanged: () => void; flash: (f: Flash) => void }) {
  const [load, setLoad] = useState<Load<PersonDetail>>({ kind: 'loading' });
  const [dialog, setDialog] = useState<null | { kind: 'edit' } | { kind: 'merge' } | { kind: 'delete' } | { kind: 'mark'; channel: Channel } | { kind: 'clear'; channel: Channel }>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [markChannel, setMarkChannel] = useState<Channel>('all');
  const reasonId = useId();
  const channelId = useId();
  const reload = useCallback(() => {
    setLoad({ kind: 'loading' });
    api.getPerson(id).then((detail) => setLoad({ kind: 'ready', value: detail })).catch((e) => setLoad(failed(e)));
  }, [id]);
  useEffect(reload, [reload]);
  const close = () => { setDialog(null); setDialogError(null); setReason(''); };
  const act = async (work: () => Promise<void>, done: string, after: 'reload' | 'back' = 'reload') => {
    setBusy(true); setDialogError(null);
    try { await work(); close(); flash({ text: done }); onChanged(); if (after === 'back') onBack(); else reload(); }
    catch (e) { setDialogError(describeError(e)); }
    finally { setBusy(false); }
  };
  if (load.kind === 'loading') return <Notice busy>{t('ct.person.loading')}</Notice>;
  if (load.kind === 'denied') return <ContactsDenied detail={load.message} />;
  if (load.kind === 'error') return <LoadError message={load.message} retry={reload} />;
  const { person, links, channels, proposals, mail_contacts: mail, can_write: canWrite, can_manage: canManage } = load.value;
  return <article className="ctPerson" aria-labelledby="ct-person-name">
    <button type="button" className="secondary ctBack" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('ct.person.back')}</button>
    <header className="ctPersonHead surface">
      <div className="ctPersonTitle">
        <h2 id="ct-person-name" dir="auto">{person.name}</h2>
        {person.organisation && <p dir="auto">{person.organisation}</p>}
        <SourceChips person={person} />
        <small className="muted">{t('ct.person.addedBy', { when: when(person.created_at), who: person.created_by })} · {t('ct.person.updated', { when: when(person.updated_at) })}</small>
      </div>
      {canWrite && <div className="actions ctPersonActions">
        <button type="button" className="secondary" onClick={() => setDialog({ kind: 'edit' })}><Pencil aria-hidden="true" />{t('ct.person.edit')}</button>
        <button type="button" className="secondary" onClick={() => setDialog({ kind: 'merge' })}><GitMerge aria-hidden="true" />{t('ct.person.merge')}</button>
        {canManage && <button type="button" className="secondary danger" onClick={() => setDialog({ kind: 'delete' })}><Trash2 aria-hidden="true" />{t('ct.person.delete')}</button>}
      </div>}
    </header>
    {person.notes && <section className="surface ctNotes" aria-label={t('ct.person.notes')}><h3>{t('ct.person.notes')}</h3><p dir="auto">{person.notes}</p></section>}
    <div className="ctPersonGrid">
      <section className="surface ctLinks" aria-labelledby="ct-links"><h3 id="ct-links">{t('ct.person.linksTitle')}</h3>
        {!links.length && !mail.length && <p className="muted">{t('ct.person.linksEmpty')}</p>}
        {links.length > 0 && <ul className="ctLinkList">{links.map((link) => <li key={link.id}>
          <span className="ctSource">{SOURCE_ICON[link.kind]}{linkKindLabel(link.kind)}</span>
          {link.found ? <><strong dir="auto">{link.title}</strong>{link.detail && <small dir="auto">{link.detail}</small>}
            <a href={link.href}><ExternalLink aria-hidden="true" />{t('ct.link.open', { module: t(MODULE_OF[link.kind]) })}</a></>
            : <small className="muted">{t('ct.link.gone')}</small>}
        </li>)}</ul>}
        {mail.length > 0 && <><h4>{t('ct.person.mailTitle')}</h4><ul className="ctLinkList">{mail.map((m) => <li key={m.id}><span className="ctSource"><AtSign aria-hidden="true" />{t('ct.link.mail_contact')}</span><strong dir="ltr">{m.name || m.email}</strong><a href={m.href}><ExternalLink aria-hidden="true" />{t('ct.link.open', { module: t('nav.email') })}</a></li>)}</ul></>}
        <small className="muted">{t('ct.person.mailNote')}</small>
      </section>
      <section className="ctChannels" aria-labelledby="ct-channels"><h3 id="ct-channels">{t('ct.person.channelsTitle')}</h3>
        <ul>{channels.map((summary) => <ChannelCard key={summary.channel} summary={summary} canWrite={canWrite} canManage={canManage} busy={busy}
          onMark={(channel) => { setMarkChannel(channel); setDialog({ kind: 'mark', channel }); }} onClear={(channel) => setDialog({ kind: 'clear', channel })} />)}</ul>
      </section>
    </div>
    {proposals.length > 0 && <section className="surface ctProposalsBox" aria-labelledby="ct-person-proposals"><h3 id="ct-person-proposals">{t('ct.person.proposalsTitle')}</h3>
      <ul className="ctProposals">{proposals.map((p) => <ProposalRow key={p.id} proposal={p} canWrite={canWrite} busy={busy} onOpen={onOpen} currentId={id}
        onKeep={(keep, other) => void act(() => api.mergePeople(keep.id, other.id).then(() => undefined), t('ct.merge.done', { other: other.name, name: keep.name }), keep.id === id ? 'reload' : 'back')}
        onDismiss={(proposal) => void act(() => api.dismissProposal(proposal.id).then(() => undefined), t('ct.proposals.dismissed'))} />)}</ul>
    </section>}

    {dialog?.kind === 'edit' && <PersonForm title={t('ct.form.editTitle', { name: person.name })} initial={draftOf(person)} busy={busy} error={dialogError} onClose={close}
      onSubmit={(draft) => { const body = personBody(draft); if (body.ok) void act(() => api.updatePerson(id, body.value).then(() => undefined), t('ct.form.saved')); }} />}
    {dialog?.kind === 'merge' && <MergeDialog keep={person} busy={busy} error={dialogError} onClose={close}
      onMerge={(other) => void act(() => api.mergePeople(id, other.id).then(() => undefined), t('ct.merge.done', { other: other.name, name: person.name }))} />}
    {dialog?.kind === 'delete' && <ConfirmDialog title={t('ct.delete.title', { name: person.name })} confirmLabel={t('ct.delete.confirm')} danger busy={busy} onCancel={close}
      onConfirm={() => void act(() => api.deletePerson(id), t('ct.delete.done', { name: person.name }), 'back')}>
      <p>{t('ct.delete.body')}</p>{dialogError && <Notice error>{dialogError}</Notice>}
    </ConfirmDialog>}
    {dialog?.kind === 'mark' && <ConfirmDialog title={t('ct.dnc.title', { name: person.name })} confirmLabel={t('ct.dnc.confirm')} danger busy={busy} onCancel={close}
      onConfirm={() => { const r = reasonText(reason); if (!r.ok) { setDialogError(r.error); return; } void act(() => api.setDoNotContact(id, markChannel, r.value), t('ct.dnc.done', { channel: channelLabel(markChannel) })); }}>
      <p>{t('ct.dnc.body')}</p>
      <label className="field" htmlFor={channelId}><span>{t('ct.dnc.channel')}</span><select id={channelId} value={markChannel} onChange={(e) => setMarkChannel(e.target.value as Channel)}>{CHANNELS.map((c) => <option key={c} value={c}>{channelLabel(c)}</option>)}</select></label>
      <label className="field" htmlFor={reasonId}><span>{t('ct.dnc.reason')}</span><input id={reasonId} dir="auto" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></label>
      {dialogError && <Notice error>{dialogError}</Notice>}
    </ConfirmDialog>}
    {dialog?.kind === 'clear' && <ConfirmDialog title={t('ct.dnc.clearTitle', { channel: channelLabel(dialog.channel) })} confirmLabel={t('ct.dnc.clearConfirm')} busy={busy} onCancel={close}
      onConfirm={() => { const r = reasonText(reason); if (!r.ok) { setDialogError(r.error); return; } const channel = dialog.channel; void act(() => api.clearDoNotContact(id, channel, r.value), t('ct.dnc.cleared', { channel: channelLabel(channel) })); }}>
      <p>{t('ct.dnc.clearBody')}</p>
      <label className="field" htmlFor={reasonId}><span>{t('ct.dnc.reason')}</span><input id={reasonId} dir="auto" value={reason} maxLength={300} data-autofocus onChange={(e) => setReason(e.target.value)} /></label>
      {dialogError && <Notice error>{dialogError}</Notice>}
    </ConfirmDialog>}
  </article>;
}

// ---------------------------------------------------------------- the people list

function PeopleList({ tick, onOpen, canWrite }: { tick: number; onOpen: (id: string) => void; canWrite: boolean }) {
  const [filters, setFilters] = useState<PeopleFilters>(NO_FILTERS);
  const [search, setSearch] = useState<PeopleFilters>(NO_FILTERS);
  const [items, setItems] = useState<PersonView[]>([]);
  const [total, setTotal] = useState(0);
  const [load, setLoad] = useState<Load<true>>({ kind: 'loading' });
  const [more, setMore] = useState<'idle' | 'loading' | { error: string }>('idle');
  const request = useRef(0);
  const fetchFirst = useCallback(async (next: PeopleFilters) => {
    const id = ++request.current;
    setLoad({ kind: 'loading' }); setMore('idle');
    try {
      const page = await api.listPeople(next, 0, PAGE_SIZE);
      if (id !== request.current) return;
      setItems(page.items); setTotal(page.total); setLoad({ kind: 'ready', value: true });
    } catch (e) { if (id === request.current) setLoad(failed(e)); }
  }, []);
  useEffect(() => { void fetchFirst(filters); }, [fetchFirst, filters, tick]);
  const loadMore = async () => {
    setMore('loading');
    try { const page = await api.listPeople(filters, items.length, PAGE_SIZE); setItems((c) => [...c, ...page.items]); setTotal(page.total); setMore('idle'); }
    catch (e) { setMore({ error: describeError(e) }); }
  };
  // A new object even when nothing changed, so Find always reads the list again.
  const find = (event: FormEvent) => { event.preventDefault(); setFilters({ ...search }); };
  const clear = () => { setSearch(NO_FILTERS); setFilters(NO_FILTERS); };
  const isFiltered = filtered(filters);
  return <section className="surface ctList" aria-label={t('ct.tab.people')}>
    <form className="ctFilters" role="search" onSubmit={find}>
      <label className="field ctSearch"><span>{t('ct.search')}</span><input type="search" dir="auto" value={search.q} maxLength={320} placeholder={t('ct.searchPlaceholder')} onChange={(e) => setSearch({ ...search, q: e.target.value })} /></label>
      <label className="field"><span>{t('ct.filter.source')}</span><select value={search.source} onChange={(e) => setSearch({ ...search, source: e.target.value as PeopleFilters['source'] })}><option value="">{t('ct.filter.anySource')}</option>{SOURCES.map((s) => <option key={s} value={s}>{sourceLabel(s)}</option>)}</select></label>
      <label className="field"><span>{t('ct.filter.dnc')}</span><select value={search.dnc} onChange={(e) => setSearch({ ...search, dnc: e.target.value as PeopleFilters['dnc'] })}>{DNC_FILTERS.map((d) => <option key={d || 'all'} value={d}>{dncFilterLabel(d)}</option>)}</select></label>
      <label className="field"><span>{t('ct.filter.sort')}</span><select value={search.sort} onChange={(e) => setSearch({ ...search, sort: e.target.value as PeopleFilters['sort'] })}>{SORTS.map((s) => <option key={s} value={s}>{sortLabel(s)}</option>)}</select></label>
      <div className="actions"><button type="submit" className="secondary" disabled={load.kind === 'loading'}><Search aria-hidden="true" />{t('ct.find')}</button>{isFiltered && <button type="button" className="secondary" onClick={clear}><RotateCcw aria-hidden="true" />{t('ct.clear')}</button>}</div>
    </form>
    {load.kind === 'loading' && <Notice busy>{t('ct.loading')}</Notice>}
    {load.kind === 'denied' && <ContactsDenied detail={load.message} />}
    {load.kind === 'error' && <LoadError message={load.message} retry={() => void fetchFirst(filters)} />}
    {load.kind === 'ready' && <>
      <h3 className="ctCount">{t(isFiltered ? 'ct.countFiltered' : 'ct.count', { count: total })}</h3>
      {!items.length && (isFiltered
        ? <Empty icon={<Search aria-hidden="true" />} title={t('ct.noMatchTitle')} text={t('ct.noMatchText')} action={<button type="button" className="secondary" onClick={clear}>{t('ct.clear')}</button>} />
        : <Empty icon={<Users aria-hidden="true" />} title={t('ct.emptyTitle')} text={t('ct.emptyText')} />)}
      {items.length > 0 && <div className="ctTableWrap"><table className="ctTable">
        <thead><tr><th scope="col">{t('ct.col.name')}</th><th scope="col">{t('ct.col.contact')}</th><th scope="col">{t('ct.col.sources')}</th><th scope="col">{t('ct.col.status')}</th></tr></thead>
        <tbody>{items.map((p) => <tr key={p.id} className="ctRow">
          <td><button type="button" className="ctLinkButton" aria-label={t('ct.open', { name: p.name }, { isolate: false })} onClick={() => onOpen(p.id)}><strong dir="auto">{p.name}</strong></button>{p.organisation && <small dir="auto">{p.organisation}</small>}</td>
          <td><Identifiers person={p} /></td>
          <td><SourceChips person={p} /></td>
          <td><StatusChips person={p} /></td>
        </tr>)}</tbody>
      </table></div>}
      {items.length < total && <div className="actions ctMore"><span className="muted">{t('ct.showing', { shown: formatNumber(items.length), total: formatNumber(total) })}</span><button type="button" className="secondary" disabled={more === 'loading'} onClick={() => void loadMore()}>{more === 'loading' ? t('common.loading') : t('ct.more')}</button></div>}
      {typeof more === 'object' && <Notice error>{more.error}</Notice>}
      {!canWrite && <small className="muted">{t('ct.dnc.ownersOnly')}</small>}
    </>}
  </section>;
}

// ---------------------------------------------------------------- possible duplicates

function DuplicatesTab({ tick, onOpen, onChanged, flash }: { tick: number; onOpen: (id: string) => void; onChanged: () => void; flash: (f: Flash) => void }) {
  const [items, setItems] = useState<ProposalView[]>([]);
  const [total, setTotal] = useState(0);
  const [canWrite, setCanWrite] = useState(false);
  const [load, setLoad] = useState<Load<true>>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const fetchFirst = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try { const page = await api.listProposals(0, PAGE_SIZE); setItems(page.items); setTotal(page.total); setCanWrite(page.can_write); setLoad({ kind: 'ready', value: true }); }
    catch (e) { setLoad(failed(e)); }
  }, []);
  useEffect(() => { void fetchFirst(); }, [fetchFirst, tick]);
  const act = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try { await work(); flash({ text: done }); onChanged(); await fetchFirst(); } catch (e) { flash({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  return <section className="surface ctList" aria-label={t('ct.tab.duplicates')}>
    <p className="muted">{t('ct.proposals.intro')}</p>
    {load.kind === 'loading' && <Notice busy>{t('ct.proposals.loading')}</Notice>}
    {load.kind === 'denied' && <ContactsDenied detail={load.message} />}
    {load.kind === 'error' && <LoadError message={load.message} retry={() => void fetchFirst()} />}
    {load.kind === 'ready' && <>
      <h3 className="ctCount">{t('ct.proposals.count', { count: total })}</h3>
      {!items.length && <Empty icon={<GitMerge aria-hidden="true" />} title={t('ct.proposals.emptyTitle')} text={t('ct.proposals.emptyText')} />}
      {items.length > 0 && <ul className="ctProposals">{items.map((p) => <ProposalRow key={p.id} proposal={p} canWrite={canWrite} busy={busy} onOpen={onOpen}
        onKeep={(keep, other) => void act(() => api.mergePeople(keep.id, other.id), t('ct.merge.done', { other: other.name, name: keep.name }))}
        onDismiss={(proposal) => void act(() => api.dismissProposal(proposal.id), t('ct.proposals.dismissed'))} />)}</ul>}
      {items.length < total && <div className="actions ctMore"><button type="button" className="secondary" onClick={() => void fetchFirst()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    </>}
  </section>;
}

// ---------------------------------------------------------------- import from a CSV file

const STATUS_TONE: Record<ImportStatus, 'ok' | 'info' | 'warn' | 'bad' | 'muted'> = { new: 'ok', match: 'info', possible_duplicate: 'warn', duplicate_in_file: 'muted', invalid: 'bad' };
type MappingSlot = 'name' | 'lastName' | 'organisation' | 'notes' | 'phone1' | 'phone2' | 'email1' | 'email2' | 'dnc';
const SLOTS: MappingSlot[] = ['name', 'lastName', 'organisation', 'notes', 'phone1', 'phone2', 'email1', 'email2', 'dnc'];
function slotValue(mapping: ImportMapping, slot: MappingSlot): string {
  switch (slot) {
    case 'name': return mapping.name[0] ?? '';
    case 'lastName': return mapping.name[1] ?? '';
    case 'organisation': return mapping.organisation ?? '';
    case 'notes': return mapping.notes ?? '';
    case 'phone1': return mapping.phones[0] ?? '';
    case 'phone2': return mapping.phones[1] ?? '';
    case 'email1': return mapping.emails[0] ?? '';
    case 'email2': return mapping.emails[1] ?? '';
    case 'dnc': return mapping.do_not_contact ?? '';
  }
}
function withSlot(mapping: ImportMapping, slot: MappingSlot, column: string): ImportMapping {
  const pair = (list: string[], index: number) => { const next = [list[0] ?? '', list[1] ?? '']; next[index] = column; return next.filter(Boolean); };
  switch (slot) {
    case 'name': return { ...mapping, name: pair(mapping.name, 0) };
    case 'lastName': return { ...mapping, name: pair(mapping.name, 1) };
    case 'organisation': return { ...mapping, organisation: column || null };
    case 'notes': return { ...mapping, notes: column || null };
    case 'phone1': return { ...mapping, phones: pair(mapping.phones, 0) };
    case 'phone2': return { ...mapping, phones: pair(mapping.phones, 1) };
    case 'email1': return { ...mapping, emails: pair(mapping.emails, 0) };
    case 'email2': return { ...mapping, emails: pair(mapping.emails, 1) };
    case 'dnc': return { ...mapping, do_not_contact: column || null };
  }
}

function ImportRowView({ row, choice, busy, onChoose }: { row: ImportRow; choice: ImportChoice | undefined; busy: boolean; onChoose: (choice: ImportChoice) => void }) {
  const action = choice && row.actions.includes(choice.action) ? choice.action : row.action;
  const target = choice?.target_id ?? row.target_id ?? row.matches[0]?.id ?? null;
  return <tr className="ctImportRow">
    <td><bdi>{formatNumber(row.row)}</bdi></td>
    <td>
      <strong dir="auto">{row.name || '-'}</strong>
      {row.organisation && <small dir="auto">{row.organisation}</small>}
      {[...row.phones, ...row.emails].map((value) => <small key={value} dir="ltr" className="ctValue">{value}</small>)}
    </td>
    <td>
      <StatusChip tone={STATUS_TONE[row.status]}>{importStatusLabel(row.status)}</StatusChip>
      {importIssues(row).map((problem) => <small key={problem} className="ctImportProblem" dir="auto">{problem}</small>)}
      {row.duplicate_of !== null && <small>{t('ct.import.repeats', { row: row.duplicate_of })}</small>}
      {row.matches.map((match) => <span key={match.id} className="ctImportMatch">
        <small dir="auto">{t('ct.import.matches', { shared: match.shared.join(', '), name: match.name })}</small>
        {match.do_not_contact.length > 0 && <small dir="auto">{t('ct.import.matchMarked', { name: match.name, channels: match.do_not_contact.map(channelLabel).join(', ') })}</small>}
      </span>)}
      {row.do_not_contact.length > 0 && <small>{t('ct.import.marks', { channels: row.do_not_contact.map(channelLabel).join(', ') })}</small>}
    </td>
    <td>
      <label className="field ctImportChoice"><span className="visuallyHidden">{t('ct.import.actionFor', { row: row.row })}</span>
        <select value={action} disabled={busy || row.actions.length < 2} onChange={(e) => onChoose({ action: e.target.value as ImportAction, target_id: target })}>
          {row.actions.map((option) => <option key={option} value={option}>{importActionLabel(option)}</option>)}
        </select></label>
      {action === 'update' && row.matches.length > 1 && <label className="field ctImportChoice"><span className="visuallyHidden">{t('ct.import.targetFor', { row: row.row })}</span>
        <select value={target ?? ''} disabled={busy} onChange={(e) => onChoose({ action, target_id: e.target.value })}>
          {row.matches.map((match) => <option key={match.id} value={match.id}>{match.name}</option>)}
        </select></label>}
    </td>
  </tr>;
}

function ImportTab({ canImport, onImported, go }: { canImport: boolean; onImported: () => void; go: (tab: ContactTab) => void }) {
  const fileId = useId();
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<ImportMapping>(emptyMapping());
  const [choices, setChoices] = useState<Record<number, ImportChoice>>({});
  const [status, setStatus] = useState<ImportStatus | ''>('');
  const [query, setQuery] = useState('');
  const [shown, setShown] = useState(IMPORT_PAGE);
  const [busy, setBusy] = useState<'reading' | 'checking' | 'importing' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const input = useRef<HTMLInputElement>(null);
  if (!canImport) return <section className="surface ctList" aria-label={t('ct.tab.import')}><Notice>{t('ct.import.ownersOnly')}</Notice></section>;

  const check = async (text: string, name: string, chosen: ImportMapping | null) => {
    setBusy(chosen ? 'checking' : 'reading'); setError(null); setResult(null);
    try {
      const next = await api.previewImport(text, name, chosen);
      setPreview(next); setMapping(next.mapping); setChoices({}); setShown(IMPORT_PAGE);
    } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  const choose = async (picked: File | undefined) => {
    setError(null); setResult(null); setPreview(null);
    if (!picked) return;
    const problem = importFileProblem(picked);
    if (problem) { setError(problem); return; }
    setBusy('reading');
    let text: string;
    try { text = await picked.text(); } catch (e) { setBusy(null); setError(t('ct.import.err.read', { error: describeError(e) })); return; }
    setFile({ name: picked.name, text });
    await check(text, picked.name, null);
  };
  const reset = () => { setFile(null); setPreview(null); setError(null); setResult(null); setChoices({}); if (input.current) input.current.value = ''; };
  const decisions = preview ? importDecisions(preview.rows, choices) : [];
  const totals = importTotals(decisions);
  const rows = preview ? importRowsShown(preview.rows, status, query) : [];
  const run = async () => {
    if (!preview || !file) return;
    setBusy('importing'); setError(null);
    try {
      const done = await api.applyImport(file.text, file.name, preview, decisions);
      setConfirming(false); setResult(done); setPreview(null); setFile(null); onImported();
    } catch (e) { setConfirming(false); setError(describeError(e)); } finally { setBusy(null); }
  };
  return <section className="surface ctList ctImport" aria-labelledby={`${fileId}-title`}>
    <h3 id={`${fileId}-title`}>{t('ct.import.title')}</h3>
    <p className="muted">{t('ct.import.intro')}</p>
    <p className="muted">{t('ct.import.limits', { rows: formatNumber(IMPORT_MAX_ROWS) })}</p>
    {!file && <label className="field ctImportFile" htmlFor={fileId}><span>{t('ct.import.choose')}</span>
      <input id={fileId} ref={input} type="file" accept=".csv,text/csv" disabled={busy !== null} onChange={(e) => void choose(e.target.files?.[0])} /></label>}
    {file && <div className="actions ctImportFileName"><span dir="auto"><FileUp aria-hidden="true" />{t('ct.import.file', { name: file.name })}</span>
      <button type="button" className="secondary" disabled={busy !== null} onClick={reset}>{t('ct.import.another')}</button></div>}
    {busy === 'reading' && <Notice busy>{t('ct.import.reading')}</Notice>}
    {error && <Notice error>{error}</Notice>}
    {result && <div className="ctImportDone">
      <Notice>{t('ct.import.done', { created: result.created, updated: result.updated, skipped: result.skipped, marks: result.marks_added, proposals: result.proposals_created })}</Notice>
      <div className="actions">
        <button type="button" onClick={() => go('people')}><Users aria-hidden="true" />{t('ct.import.openPeople')}</button>
        {result.proposals_created > 0 && <button type="button" className="secondary" onClick={() => go('duplicates')}><GitMerge aria-hidden="true" />{t('ct.import.openDuplicates')}</button>}
        <button type="button" className="secondary" onClick={reset}>{t('ct.import.another')}</button>
      </div>
    </div>}
    {preview && file && <>
      <fieldset className="ctImportMapping" disabled={busy !== null}>
        <legend>{t('ct.import.mapping')}</legend>
        <p className="muted">{preview.rows.length ? t('ct.import.mappingIntro') : t('ct.import.mappingNone')}</p>
        <div className="ctImportSlots">
          {SLOTS.map((slot) => <label key={slot} className="field"><span>{importFieldLabel(slot)}</span>
            <select value={slotValue(mapping, slot)} onChange={(e) => setMapping(withSlot(mapping, slot, e.target.value))}>
              <option value="">{t('ct.import.none')}</option>
              {preview.columns.filter(Boolean).map((column) => <option key={column} value={column}>{column}</option>)}
            </select></label>)}
        </div>
        <div className="actions"><button type="button" className="secondary" onClick={() => void check(file.text, file.name, mapping)}>
          <RefreshCw aria-hidden="true" />{busy === 'checking' ? t('ct.import.checking') : t('ct.import.check')}</button></div>
      </fieldset>
      {preview.rows.length > 0 && <>
        <h3 className="ctCount">{t('ct.import.summary')}: {t('ct.import.rows', { count: preview.counts.rows })}</h3>
        <ul className="ctImportCounts">{IMPORT_STATUSES.map((s) => <li key={s}><StatusChip tone={STATUS_TONE[s]}>{importStatusLabel(s)}: <bdi>{formatNumber(preview.counts[s])}</bdi></StatusChip></li>)}</ul>
        <form className="ctFilters" role="search" onSubmit={(e) => e.preventDefault()}>
          <label className="field ctSearch"><span>{t('ct.import.search')}</span><input type="search" dir="auto" value={query} placeholder={t('ct.import.searchPlaceholder')} onChange={(e) => { setQuery(e.target.value); setShown(IMPORT_PAGE); }} /></label>
          <label className="field"><span>{t('ct.import.filterStatus')}</span><select value={status} onChange={(e) => { setStatus(e.target.value as ImportStatus | ''); setShown(IMPORT_PAGE); }}>
            <option value="">{t('ct.import.allRows')}</option>{IMPORT_STATUSES.map((s) => <option key={s} value={s}>{importStatusLabel(s)}</option>)}</select></label>
        </form>
        {!rows.length && <Empty icon={<Search aria-hidden="true" />} title={t('ct.import.noMatch')} text={t('ct.noMatchText')} />}
        {rows.length > 0 && <div className="ctTableWrap"><table className="ctTable ctImportTable">
          <thead><tr><th scope="col">{t('ct.import.col.row')}</th><th scope="col">{t('ct.import.col.person')}</th><th scope="col">{t('ct.import.col.check')}</th><th scope="col">{t('ct.import.col.action')}</th></tr></thead>
          <tbody>{rows.slice(0, shown).map((row) => <ImportRowView key={row.row} row={row} choice={choices[row.row]} busy={busy !== null}
            onChoose={(next) => setChoices((current) => ({ ...current, [row.row]: next }))} />)}</tbody>
        </table></div>}
        {rows.length > shown && <div className="actions ctMore"><span className="muted">{t('ct.import.showing', { shown: formatNumber(shown), total: formatNumber(rows.length) })}</span>
          <button type="button" className="secondary" onClick={() => setShown((n) => n + IMPORT_PAGE)}>{t('ct.import.more')}</button></div>}
        <div className="actions ctImportApply">
          <span>{t('ct.import.totals', { create: totals.create, update: totals.update, skip: totals.skip })}</span>
          <button type="button" disabled={busy !== null || totals.create + totals.update === 0} onClick={() => setConfirming(true)}><Upload aria-hidden="true" />{t('ct.import.apply')}</button>
        </div>
        {totals.create + totals.update === 0 && <small className="muted">{t('ct.import.nothing')}</small>}
      </>}
    </>}
    {confirming && <ConfirmDialog title={t('ct.import.confirmTitle')} confirmLabel={busy === 'importing' ? t('ct.import.applying') : t('ct.import.apply')} busy={busy === 'importing'}
      onConfirm={() => void run()} onCancel={() => setConfirming(false)}>
      <p>{t('ct.import.confirmText', { create: totals.create, update: totals.update, skip: totals.skip })}</p>
    </ConfirmDialog>}
  </section>;
}

// ---------------------------------------------------------------- the page

export function ContactsView() {
  const [tab, personId, go] = useContactsHash();
  const [tick, setTick] = useState(0);
  const [summary, setSummary] = useState<Load<Awaited<ReturnType<typeof api.listPeople>>>>({ kind: 'loading' });
  const [busy, setBusy] = useState<'rebuild' | 'export' | 'add' | null>(null);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const refresh = () => setTick((n) => n + 1);
  useEffect(() => { api.listPeople(NO_FILTERS, 0, 1).then((page) => setSummary({ kind: 'ready', value: page })).catch((e) => setSummary(failed(e))); }, [tick]);
  const page = summary.kind === 'ready' ? summary.value : null;
  const rebuild = async () => {
    setBusy('rebuild'); setFlash(null);
    try {
      const report = await api.rebuildDirectory();
      setFlash({ text: report.complete ? t('ct.rebuilt', { linked: report.linked, people: report.people_created, proposals: report.proposals_created }) : t('ct.rebuiltMore', { linked: report.linked }) });
      refresh();
    } catch (e) { setFlash({ text: describeError(e), error: true }); } finally { setBusy(null); }
  };
  const exportCsv = async () => {
    setBusy('export'); setFlash(null);
    try { setFlash({ text: t('ct.exported', { name: await api.downloadPeople() }) }); } catch (e) { setFlash({ text: describeError(e), error: true }); } finally { setBusy(null); }
  };
  const add = async (draft: PersonDraft) => {
    const body = personBody(draft);
    if (!body.ok) { setAddError(body.error); return; }
    setBusy('add'); setAddError(null);
    try {
      const detail = await api.createPerson(body.value);
      setAdding(false);
      setFlash({ text: detail.person.open_proposals ? t('ct.form.proposalNote', { count: detail.person.open_proposals }) : t('ct.form.saved') });
      refresh(); go('people', detail.person.id);
    } catch (e) { setAddError(describeError(e)); } finally { setBusy(null); }
  };
  const canWrite = page?.can_write ?? false;
  const crumbs = [{ label: viewLabel('home'), href: '#home' }, ...(personId ? [{ label: viewLabel('contacts'), href: '#contacts' }] : [])];
  const bySource = page?.counts.by_source ?? {};
  return <div className="ctPage">
    <FinancePageHead icon={<BookUser />} title={viewLabel('contacts')} subtitle={t('ct.subtitle')} crumbs={crumbs}
      actions={page && <>
        {canWrite && <button type="button" onClick={() => { setAddError(null); setAdding(true); }}><Plus aria-hidden="true" />{t('ct.add')}</button>}
        {canWrite && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void rebuild()}><RefreshCw aria-hidden="true" />{busy === 'rebuild' ? t('ct.rebuilding') : t('ct.rebuild')}</button>}
        {page.can_export && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void exportCsv()}><Download aria-hidden="true" />{busy === 'export' ? t('ct.exporting') : t('ct.export')}</button>}
      </>}
      tabs={!personId && <FinanceTabs label={viewLabel('contacts')} idPrefix="ct" value={tab} onChange={(next) => go(next)}
        items={CONTACT_TABS.filter((id) => id !== 'import' || page?.can_export || tab === 'import').map((id) => ({ id, label: tabLabel(id), badge: id === 'duplicates' && page?.counts.open_proposals ? page.counts.open_proposals : undefined }))} />} />
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    {personId
      ? <PersonPage key={personId} id={personId} onBack={() => go(tab)} onOpen={(id) => go(tab, id)} onChanged={refresh} flash={setFlash} />
      : <>
        {page && <KpiRow label={viewLabel('contacts')} className="ctKpis">
          <KpiCard icon={<Users />} label={t('ct.kpi.people')} value={formatNumber(page.counts.people)} />
          <KpiCard icon={<BellOff />} label={t('ct.kpi.dnc')} value={formatNumber(page.counts.do_not_contact)} tone={page.counts.do_not_contact ? 'warn' : ''} />
          <KpiCard icon={<GitMerge />} label={t('ct.kpi.proposals')} value={formatNumber(page.counts.open_proposals)} tone={page.counts.open_proposals ? 'warn' : ''} />
          <KpiCard icon={<BookUser />} label={t('ct.kpi.sources')} value={formatNumber((bySource.calls ?? 0) + (bySource.business ?? 0) + (bySource.whatsapp ?? 0))}
            hint={t('ct.kpi.sourcesHint', { calls: bySource.calls ?? 0, business: bySource.business ?? 0, whatsapp: bySource.whatsapp ?? 0 })} />
        </KpiRow>}
        {page && canWrite && <p className="muted ctNote">{t('ct.rebuildNote')}</p>}
        {page && !page.can_export && <p className="muted ctNote">{t('ct.exportOwners')}</p>}
        <div {...tabPanel('ct', tab)}>
          {tab === 'people' && <PeopleList tick={tick} canWrite={canWrite} onOpen={(id) => go('people', id)} />}
          {tab === 'duplicates' && <DuplicatesTab tick={tick} onOpen={(id) => go('duplicates', id)} onChanged={refresh} flash={setFlash} />}
          {tab === 'import' && (page ? <ImportTab canImport={page.can_export} onImported={refresh} go={(next) => go(next)} />
            : summary.kind === 'denied' ? <ContactsDenied detail={summary.message} />
            : summary.kind === 'error' ? <LoadError message={summary.message} retry={refresh} />
            : <Notice busy>{t('ct.loading')}</Notice>)}
        </div>
      </>}
    {adding && <PersonForm title={t('ct.form.addTitle')} initial={emptyPersonDraft()} busy={busy === 'add'} error={addError} onSubmit={(d) => void add(d)} onClose={() => setAdding(false)} />}
  </div>;
}
