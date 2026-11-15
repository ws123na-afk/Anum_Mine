// Lent and borrowed with people from Contacts (docs/finance.md#people-from-contacts): pick the person from the
// Contacts directory on the loan form (or add them there when the caller may), and see each person's loans and
// repayments together. Contacts are read and created only through the directory's own API, so its permissions
// apply; a loan keeps just the contact's id and the name typed on it.
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';
import { BookUser, HandCoins, Search, UserPlus, Users, X } from 'lucide-react';
import type { FinanceLoanPerson, FinanceLoanPersonDetail, FinanceLoanPersonPage, FinanceLoanContactState } from '@anum/contracts';
import * as pm from './lib/financePersonalApi';
import { createPerson, listPeople } from './lib/contactsApi';
import type { PersonView } from './lib/contacts';
import { contactNote, hasExactName, loanName, personHint } from './lib/financeLoanContacts';
import { formatAmount, isolate } from './lib/finance';
import { PERSONAL_PAGE, loanTone, pages } from './lib/financePersonal';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinCard, StatusChip } from './FinanceShell';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const SUGGESTIONS = 6;

export interface PickedContact { id: string | null; name: string | null; state: FinanceLoanContactState | null }

/** Under the person's name on a loan: the typed name kept as history, or that Contacts no longer has the person. */
export function ContactNote({ loan }: { loan: Parameters<typeof contactNote>[0] }) {
  const note = contactNote(loan);
  if (!note) return null;
  const text = note.kind === 'typed' ? t('fpm.contact.typedAs', { name: note.name }) : t(note.kind === 'deleted' ? 'fpm.contact.deleted' : 'fpm.contact.hidden');
  return <small className="muted fpmBlock" dir="auto">{text}</small>;
}

type Search = { kind: 'idle' } | { kind: 'loading' } | { kind: 'ready'; people: PersonView[]; canWrite: boolean } | { kind: 'denied' } | { kind: 'error'; message: string };

/**
 * The person on the loan form: the typed name doubles as the search of Contacts. Picking a person links them;
 * "Add to Contacts" appears only when the server says the caller may create people and nobody has that name.
 */
export function ContactPicker({ person, picked, onPick }: {
  person: string; picked: PickedContact; onPick: (next: PickedContact) => void;
}) {
  const [search, setSearch] = useState<Search>({ kind: 'idle' });
  const [adding, setAdding] = useState(false), [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const listId = useId();
  const needle = person.trim();
  useEffect(() => {
    if (picked.id || needle.length < 2) { setSearch({ kind: 'idle' }); return undefined; }
    let live = true;
    setSearch({ kind: 'loading' });
    const timer = setTimeout(() => {
      listPeople({ q: needle, source: '', dnc: '', sort: 'name' }, 0, SUGGESTIONS)
        .then((page) => { if (live) setSearch({ kind: 'ready', people: page.items, canWrite: page.can_write }); })
        .catch((e) => { if (live) setSearch(isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: describeError(e) }); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [needle, picked.id, attempt]);
  const add = async () => {
    setAdding(true); setError(null);
    try {
      const made = await createPerson({ name: needle, organisation: '', notes: '', phones: [], emails: [] });
      onPick({ id: made.person.id, name: made.person.name, state: 'linked' });
    } catch (e) { setError(describeError(e)); } finally { setAdding(false); }
  };
  if (picked.id) {
    const label = picked.state === 'deleted' ? t('fpm.contact.deleted') : picked.state === 'hidden' ? t('fpm.contact.hidden')
      : t('fpm.contact.linked', { name: picked.name ?? person });
    return <div className="fpmContactChosen" role="group" aria-label={t('fpm.contact.title')}>
      <BookUser aria-hidden="true" /><span dir="auto">{label}</span>
      <button type="button" className="secondary" onClick={() => onPick({ id: null, name: null, state: null })}
        aria-label={t('fpm.contact.unlinkAria', { name: picked.name ?? person })}><X aria-hidden="true" />{t('fpm.contact.unlink')}</button>
    </div>;
  }
  return <div className="fpmContactPick" aria-live="polite">
    {search.kind === 'idle' && <p className="muted fpmContactHelp"><Search aria-hidden="true" />{t('fpm.contact.help')}</p>}
    {search.kind === 'loading' && <p className="muted fpmContactHelp" aria-busy="true">{t('fpm.contact.searching')}</p>}
    {search.kind === 'denied' && <p className="muted fpmContactHelp">{t('fpm.contact.denied')}</p>}
    {search.kind === 'error' && <p className="formError fpmContactHelp">{t('fpm.contact.error', { message: search.message })}
      <button type="button" className="secondary" onClick={() => setAttempt((n) => n + 1)}>{t('fpm.contact.retry')}</button></p>}
    {search.kind === 'ready' && <>
      {search.people.length === 0 ? <p className="muted fpmContactHelp">{t('fpm.contact.none', { name: needle })}</p>
        : <ul className="fpmContactList" id={listId} aria-label={t('fpm.contact.results')}>{search.people.map((p) => <li key={p.id}>
          <button type="button" className="secondary" onClick={() => onPick({ id: p.id, name: p.name, state: 'linked' })}
            aria-label={t('fpm.contact.pickAria', { name: p.name })}>
            <BookUser aria-hidden="true" /><span dir="auto">{p.name}</span>{personHint(p) && <small className="muted" dir="auto">{personHint(p)}</small>}
          </button></li>)}</ul>}
      {search.canWrite && !hasExactName(search.people, needle) && <button type="button" className="secondary" disabled={adding} onClick={() => void add()}>
        <UserPlus aria-hidden="true" />{t('fpm.contact.add', { name: needle })}</button>}
      {!search.canWrite && search.people.length === 0 && <p className="muted fpmContactHelp">{t('fpm.contact.cannotAdd')}</p>}
    </>}
    {error && <p className="formError" role="alert">{error}</p>}
  </div>;
}

// -- by person ---------------------------------------------------------------------------------------------

function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[]): [Load<T>, () => void] {
  const [state, setState] = useState<Load<T>>({ kind: 'loading' });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const load = useCallback(() => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    fetcher().then((value) => setState({ kind: 'ready', value })).catch((e) => setState(failed(e)));
  }, deps);
  useEffect(() => { load(); }, [load]);
  return [state, load];
}

function States<T>({ state, retry, children }: { state: Load<T>; retry: () => void; children: (value: T) => ReactNode }) {
  if (state.kind === 'loading') return <Notice busy>{t('fpm.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('fpm.error', { message: state.message })} retry={retry} />;
  return <>{children(state.value)}</>;
}

const owedText = (group: FinanceLoanPerson, side: 'they_owe_me' | 'i_owe') => {
  const parts = group.owed.filter((o) => Number(o[side]) > 0).map((o) => formatAmount(o[side], o.currency));
  return parts.length ? isolate(parts.join(' · ')) : '-';
};

/** Each person's loans together, with what they owe you and what you owe them per currency. */
export function LoanPeople({ onAdd }: { onAdd: () => void }) {
  const [q, setQ] = useState(''), [offset, setOffset] = useState(0), [open, setOpen] = useState<FinanceLoanPerson | null>(null);
  const [page, reload] = useLoad<FinanceLoanPersonPage>(() => pm.listLoanPeople({ q, limit: PERSONAL_PAGE, offset }), [q, offset]);
  const searchId = useId();
  const count = page.kind === 'ready' ? pages(page.value.total) : 1, current = Math.floor(offset / PERSONAL_PAGE) + 1;
  return <FinCard title={t('fpm.byPerson.title')} icon={<Users />}>
    <div className="fpmFilters">
      <label className="field" htmlFor={searchId}><span><Search aria-hidden="true" />{t('fpm.search')}</span>
        <input id={searchId} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} dir="auto" /></label>
    </div>
    <States state={page} retry={reload}>{(value) => value.total === 0
      ? <Empty icon={<HandCoins aria-hidden="true" />} title={t(q ? 'fpm.byPerson.noMatch' : 'fpm.people.emptyTitle')} text={t('fpm.people.empty')}
        action={<button type="button" onClick={onAdd}>{t('fpm.people.add')}</button>} />
      : <>
        <div className="finTableWrap" tabIndex={0}><table className="finGrid">
          <thead><tr><th scope="col">{t('fpm.people.person')}</th><th scope="col">{t('fpm.byPerson.loans')}</th>
            <th scope="col" className="num">{t('fpm.people.theyOwe')}</th><th scope="col" className="num">{t('fpm.people.iOwe')}</th>
            <th scope="col">{t('fpm.byPerson.last')}</th><th scope="col"><span className="visuallyHidden">{t('fpm.actions')}</span></th></tr></thead>
          <tbody>{value.items.map((g) => <tr key={g.key}>
            <th scope="row"><span dir="auto">{g.name}</span>
              {g.contact_state === 'linked' && <small className="muted fpmBlock">{t('fpm.byPerson.fromContacts')}</small>}
              {g.contact_state === 'deleted' && <small className="muted fpmBlock">{t('fpm.contact.deleted')}</small>}</th>
            <td>{t('fpm.byPerson.loanCount', { open: g.open_loans, count: g.loans })}
              {g.overdue > 0 && <small className="fpmBlock fpmOverdue">{t('fpm.byPerson.overdue', { count: g.overdue })}</small>}</td>
            <td className="num">{owedText(g, 'they_owe_me')}</td>
            <td className="num">{owedText(g, 'i_owe')}</td>
            <td>{day(g.last_on)}</td>
            <td><button type="button" className="secondary" onClick={() => setOpen(g)} aria-label={t('fpm.openAria', { name: g.name })}>{t('fpm.open')}</button></td>
          </tr>)}</tbody>
        </table></div>
        {count > 1 && <div className="actions fpmPager">
          <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PERSONAL_PAGE))}>{t('fpm.previous')}</button>
          <span>{t('fpm.page', { page: current, pages: count })}</span>
          <button type="button" className="secondary" disabled={current >= count} onClick={() => setOffset(offset + PERSONAL_PAGE)}>{t('fpm.next')}</button>
        </div>}
      </>}</States>
    {open && <PersonHistory group={open} onClose={() => { setOpen(null); reload(); }} />}
  </FinCard>;
}

function PersonHistory({ group, onClose }: { group: FinanceLoanPerson; onClose: () => void }) {
  const [state, reload] = useLoad<FinanceLoanPersonDetail>(() => pm.getLoanPerson(group.key), [group.key]);
  return <Modal title={t('fpm.byPerson.detail', { name: group.name })} onClose={onClose} className="fpmDialog">
    <States state={state} retry={reload}>{(d) => <>
      {d.names.length > 1 || (d.contact_state === 'linked' && d.names[0] !== d.name)
        ? <p className="muted" dir="auto">{t('fpm.byPerson.names', { names: d.names.join(', ') })}</p> : null}
      <h3 className="finAdvisorSub">{t('fpm.byPerson.loansTitle')}</h3>
      <div className="finTableWrap" tabIndex={0}><table className="finGrid">
        <thead><tr><th scope="col">{t('fpm.people.lentOn')}</th><th scope="col">{t('fpm.people.direction')}</th><th scope="col" className="num">{t('fpm.amount')}</th>
          <th scope="col" className="num">{t('fpm.people.repaid')}</th><th scope="col" className="num">{t('fpm.people.left')}</th><th scope="col">{t('fpm.status')}</th></tr></thead>
        <tbody>{d.items.map((l) => <tr key={l.id}>
          <th scope="row">{day(l.lent_on)}<small className="muted fpmBlock" dir="auto">{loanName(l) === l.person ? '' : l.person}</small></th>
          <td>{t(l.direction === 'lent' ? 'fpm.people.lentShort' : 'fpm.people.borrowedShort')}</td>
          <td className="num"><Money value={l.amount} currency={l.currency} /></td>
          <td className="num"><Money value={l.repaid} currency={l.currency} /></td>
          <td className="num"><Money value={l.outstanding} currency={l.currency} /></td>
          <td><StatusChip tone={loanTone(l.status)}>{t(`fpm.people.status.${l.status}` as MessageKey)}</StatusChip></td>
        </tr>)}</tbody>
      </table></div>
      <h3 className="finAdvisorSub">{t('fpm.byPerson.history')}</h3>
      {d.history.length === 0 ? <p className="muted">{t('fpm.byPerson.noHistory')}</p>
        : <ul className="finMoveList">{d.history.map((h) => <li key={h.movement.link.id}>
          <div><span dir="auto">{h.movement.description ?? '-'}</span>
            <small>{h.movement.posted_on ? day(h.movement.posted_on) : '-'} · {t(`fpm.role.${h.movement.link.role}` as MessageKey)}
              {h.movement.link.status !== 'active' ? ` · ${t('fpm.movements.review')}` : ''}</small></div>
          {h.movement.link.amount && h.movement.link.currency ? <Money value={h.movement.link.amount} currency={h.movement.link.currency} /> : <span>-</span>}
        </li>)}</ul>}
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('fpm.close')}</button></div>
    </>}</States>
  </Modal>;
}
