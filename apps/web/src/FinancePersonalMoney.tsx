// Finance, Lent and borrowed, Payslips, Send home and Fixed costs (docs/finance.md#lent-and-borrowed, #payslips,
// #sending-money-home, #fixed-commitments). One page with four tabs in the Finance shell, each deep-linked
// (#finance-people, #finance-payslips, #finance-remittances, #finance-commitments). Every figure is the API's,
// from the person's own records; a record is linked to the real ledger transaction (a finance link). ANUM only
// records and advises: nothing here moves money.
import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowDownLeft, ArrowUpRight, Banknote, CalendarClock, HandCoins, Link2, Pencil, Plus, Receipt, Search, Send, Trash2, Wallet } from 'lucide-react';
import {
  FINANCE_DEDUCTION_KINDS, type FinanceCommitments, type FinanceDeduction, type FinanceDeductionKind, type FinanceMovement, type FinancePaymentCandidate,
  type FinancePayslip, type FinancePayslipPage, type FinancePersonLoan, type FinancePersonLoanPage, type FinanceRemittance, type FinanceRemittancePage,
} from '@anum/contracts';
import * as pm from './lib/financePersonalApi';
import { formatAmount, isolate, type FinanceTab } from './lib/finance';
import { PERSONAL_PAGE, commitmentGroups, loanTone, pages, previewNet, previewReceived, todayIso } from './lib/financePersonal';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import './financePages.css';
import './financePersonal.css';

export type PersonalTab = 'people' | 'payslips' | 'remittances' | 'commitments';
const TABS: readonly PersonalTab[] = ['people', 'payslips', 'remittances', 'commitments'];
const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const amount = (value: string, currency: string) => isolate(formatAmount(value, currency));

export function PersonalMoneyPage({ tab, base, onOpen }: { tab: PersonalTab; base: string; onOpen: (tab: FinanceTab) => void }) {
  // On a phone the tab strip scrolls: keep the open tab in view.
  useEffect(() => { document.getElementById(`fpm-tab-${tab}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }, [tab]);
  const head = <FinancePageHead title={t(`finance.tab.${tab}` as MessageKey)} subtitle={t(`fpm.${tab}.subtitle` as MessageKey)}
    tabs={<FinanceTabs label={t('fpm.tabs')} idPrefix="fpm" value={tab} onChange={(next) => onOpen(next)}
      items={TABS.map((id) => ({ id, label: t(`finance.tab.${id}` as MessageKey) }))} />} />;
  return <div className="finPage fpmPage">
    {head}
    <div {...tabPanel('fpm', tab)}>
      {tab === 'people' && <PeopleTab base={base} />}
      {tab === 'payslips' && <PayslipsTab base={base} />}
      {tab === 'remittances' && <RemittancesTab base={base} onOpen={onOpen} />}
      {tab === 'commitments' && <CommitmentsTab onOpen={onOpen} />}
    </div>
  </div>;
}

function Pager({ total, offset, onOffset }: { total: number; offset: number; onOffset: (next: number) => void }) {
  const count = pages(total), current = Math.floor(offset / PERSONAL_PAGE) + 1;
  if (count <= 1) return null;
  return <div className="actions fpmPager">
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - PERSONAL_PAGE))}>{t('fpm.previous')}</button>
    <span>{t('fpm.page', { page: current, pages: count })}</span>
    <button type="button" className="secondary" disabled={current >= count} onClick={() => onOffset(offset + PERSONAL_PAGE)}>{t('fpm.next')}</button>
  </div>;
}

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

// -- linking a record to its ledger transaction ------------------------------------------------------------

function LinkDialog({ title, kind, direction, currency, around, expected, onLink, onClose }: {
  title: string; kind: pm.PersonalKind; direction: 'in' | 'out'; currency: string; around: string | null; expected: string | null;
  onLink: (canonicalId: string) => Promise<void>; onClose: () => void;
}) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<Load<FinancePaymentCandidate[]>>({ kind: 'loading' });
  const [busy, setBusy] = useState<string | null>(null), [error, setError] = useState<string | null>(null);
  const searchId = useId();
  const load = useCallback(() => {
    setRows({ kind: 'loading' });
    pm.candidates(kind, direction, { around, amount: expected, currency, q: q || null }).then((r) => setRows({ kind: 'ready', value: r.items })).catch((e) => setRows(failed(e)));
  }, [kind, direction, around, expected, currency, q]);
  useEffect(() => { load(); }, [load]);
  const link = async (canonical: string) => {
    setBusy(canonical); setError(null);
    try { await onLink(canonical); onClose(); } catch (e) { setError(describeError(e)); } finally { setBusy(null); }
  };
  return <Modal title={title} onClose={onClose} className="fpmDialog">
    <p className="muted">{t(direction === 'in' ? 'fpm.link.helpIn' : 'fpm.link.helpOut')}</p>
    <label className="field" htmlFor={searchId}><span>{t('fpm.link.search')}</span>
      <input id={searchId} value={q} onChange={(e) => setQ(e.target.value)} dir="auto" /></label>
    {error && <Notice error>{error}</Notice>}
    <States state={rows} retry={load}>{(items) => items.length === 0 ? <p className="muted">{t('fpm.link.none')}</p>
      : <ul className="finMoveList fpmCandidates">{items.map((c) => <li key={`${c.canonical_id}-${c.currency}`}>
        <div><span dir="auto">{c.merchant}</span><small>{day(c.posted_on)} · {t('fpm.link.left', { amount: amount(c.remaining, c.currency) })}</small></div>
        <Money value={c.amount} currency={c.currency} />
        <button type="button" className="secondary" disabled={busy !== null} onClick={() => void link(c.canonical_id)}
          aria-label={t('fpm.link.useAria', { name: c.merchant, date: day(c.posted_on) })}>{t('fpm.link.use')}</button>
      </li>)}</ul>}</States>
    <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('fpm.close')}</button></div>
  </Modal>;
}

function Movements({ rows, onUnlink }: { rows: FinanceMovement[]; onUnlink: (linkId: string) => void }) {
  if (rows.length === 0) return <p className="muted">{t('fpm.movements.none')}</p>;
  return <ul className="finMoveList">{rows.map((m) => <li key={m.link.id}>
    <div><span dir="auto">{m.description ?? '-'}</span><small>{m.posted_on ? day(m.posted_on) : '-'} · {t(`fpm.role.${m.link.role}` as MessageKey)}
      {m.link.status !== 'active' ? ` · ${t('fpm.movements.review')}` : ''}</small></div>
    {m.link.amount && m.link.currency ? <Money value={m.link.amount} currency={m.link.currency} /> : <span>-</span>}
    <button type="button" className="secondary" onClick={() => onUnlink(m.link.id)}>{t('fpm.movements.unlink')}</button>
  </li>)}</ul>;
}

function RowActions({ name, onOpen, onEdit, onDelete }: { name: string; onOpen: () => void; onEdit: () => void; onDelete: () => void }) {
  return <div className="fpmRowActions">
    <button type="button" className="secondary" onClick={onOpen} aria-label={t('fpm.openAria', { name })}><Link2 aria-hidden="true" />{t('fpm.open')}</button>
    <button type="button" className="secondary iconOnly" onClick={onEdit} aria-label={t('fpm.editAria', { name })}><Pencil aria-hidden="true" /></button>
    <button type="button" className="secondary iconOnly" onClick={onDelete} aria-label={t('fpm.deleteAria', { name })}><Trash2 aria-hidden="true" /></button>
  </div>;
}

// -- lent and borrowed ------------------------------------------------------------------------------------

function PeopleTab({ base }: { base: string }) {
  const [q, setQ] = useState(''), [direction, setDirection] = useState(''), [status, setStatus] = useState('all'), [offset, setOffset] = useState(0);
  const [page, reload] = useLoad<FinancePersonLoanPage>(() => pm.listLoans({ q, direction, status, limit: PERSONAL_PAGE, offset }), [q, direction, status, offset]);
  const [editing, setEditing] = useState<FinancePersonLoan | 'new' | null>(null);
  const [open, setOpen] = useState<FinancePersonLoan | null>(null);
  const [removing, setRemoving] = useState<FinancePersonLoan | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const searchId = useId();
  const done = (text: string | null) => { setEditing(null); if (text) { setNotice({ text }); reload(); } };
  const remove = async () => {
    if (!removing) return;
    try { await pm.deleteLoan(removing.id); setNotice({ text: t('fpm.deleted', { name: removing.person }) }); reload(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setRemoving(null); }
  };
  const data = page.kind === 'ready' ? page.value : null;
  return <>
    <FinBanner tone="info" icon={<HandCoins />} title={t('fpm.people.bannerTitle')}>{t('fpm.people.banner')}</FinBanner>
    {data && <KpiRow label={t('fpm.people.kpis')}>
      <KpiCard icon={<ArrowDownLeft />} label={t('fpm.people.theyOwe')} value={data.owed.length ? data.owed.map((o) => formatAmount(o.they_owe_me, o.currency)).join(' · ') : '-'} />
      <KpiCard icon={<ArrowUpRight />} label={t('fpm.people.iOwe')} value={data.owed.length ? data.owed.map((o) => formatAmount(o.i_owe, o.currency)).join(' · ') : '-'} />
      <KpiCard icon={<CalendarClock />} label={t('fpm.people.overdue')} value={String(data.overdue)} tone={data.overdue ? 'bad' : ''} />
    </KpiRow>}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <FinCard title={t('fpm.people.listTitle')} icon={<HandCoins />} actions={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('fpm.people.add')}</button>}>
      <div className="fpmFilters">
        <label className="field" htmlFor={searchId}><span><Search aria-hidden="true" />{t('fpm.search')}</span>
          <input id={searchId} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} dir="auto" /></label>
        <label className="field"><span>{t('fpm.people.direction')}</span><select value={direction} onChange={(e) => { setDirection(e.target.value); setOffset(0); }}>
          <option value="">{t('fpm.all')}</option><option value="lent">{t('fpm.people.lent')}</option><option value="borrowed">{t('fpm.people.borrowed')}</option></select></label>
        <label className="field"><span>{t('fpm.status')}</span><select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
          {['all', 'open', 'overdue', 'settled'].map((s) => <option key={s} value={s}>{t(`fpm.people.filter.${s}` as MessageKey)}</option>)}</select></label>
      </div>
      <States state={page} retry={reload}>{(value) => value.total === 0
        ? <Empty icon={<HandCoins aria-hidden="true" />} title={t('fpm.people.emptyTitle')} text={t('fpm.people.empty')}
          action={<button type="button" onClick={() => setEditing('new')}>{t('fpm.people.add')}</button>} />
        : <>
          <div className="finTableWrap" tabIndex={0}><table className="finGrid">
            <thead><tr><th scope="col">{t('fpm.people.person')}</th><th scope="col">{t('fpm.people.direction')}</th><th scope="col" className="num">{t('fpm.amount')}</th>
              <th scope="col" className="num">{t('fpm.people.repaid')}</th><th scope="col" className="num">{t('fpm.people.left')}</th><th scope="col">{t('fpm.people.due')}</th>
              <th scope="col">{t('fpm.status')}</th><th scope="col"><span className="visuallyHidden">{t('fpm.actions')}</span></th></tr></thead>
            <tbody>{value.items.map((l) => <tr key={l.id}>
              <th scope="row" dir="auto">{l.person}</th>
              <td>{t(l.direction === 'lent' ? 'fpm.people.lentShort' : 'fpm.people.borrowedShort')}</td>
              <td className="num"><Money value={l.amount} currency={l.currency} /></td>
              <td className="num"><Money value={l.repaid} currency={l.currency} /></td>
              <td className="num"><Money value={l.outstanding} currency={l.currency} /></td>
              <td>{l.due_on ? day(l.due_on) : '-'}</td>
              <td><StatusChip tone={loanTone(l.status)}>{t(`fpm.people.status.${l.status}` as MessageKey)}</StatusChip></td>
              <td><RowActions name={l.person} onOpen={() => setOpen(l)} onEdit={() => setEditing(l)} onDelete={() => setRemoving(l)} /></td>
            </tr>)}</tbody>
          </table></div>
          <Pager total={value.total} offset={offset} onOffset={setOffset} />
        </>}</States>
    </FinCard>
    {editing && <Modal title={editing === 'new' ? t('fpm.people.add') : t('fpm.people.edit')} onClose={() => setEditing(null)}>
      <LoanForm loan={editing === 'new' ? null : editing} base={base} onDone={done} /></Modal>}
    {open && <LoanDetail loan={open} onClose={() => { setOpen(null); reload(); }} />}
    {removing && <ConfirmDialog title={t('fpm.deleteTitle', { name: removing.person })} confirmLabel={t('fpm.delete')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('fpm.deleteText')}</p></ConfirmDialog>}
  </>;
}

function LoanForm({ loan, base, onDone }: { loan: FinancePersonLoan | null; base: string; onDone: (text: string | null) => void }) {
  const [direction, setDirection] = useState(loan?.direction ?? 'lent'), [person, setPerson] = useState(loan?.person ?? '');
  const [value, setValue] = useState(loan?.amount ?? ''), [currency, setCurrency] = useState(loan?.currency ?? base);
  const [lentOn, setLentOn] = useState(loan?.lent_on ?? todayIso()), [dueOn, setDueOn] = useState(loan?.due_on ?? ''), [notes, setNotes] = useState(loan?.notes ?? '');
  const [remind, setRemind] = useState(loan?.remind ?? true), [daysBefore, setDaysBefore] = useState(loan?.remind_days_before ?? 3);
  const [closed, setClosed] = useState(Boolean(loan?.closed_on)), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    const body = { direction, person: person.trim(), amount: value.trim(), currency, lent_on: lentOn, due_on: dueOn || null, notes: notes.trim() || null,
      remind, remind_days_before: daysBefore, closed };
    try { if (loan) await pm.updateLoan(loan.id, body); else await pm.createLoan(body); onDone(t('fpm.saved', { name: body.person })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <fieldset className="fpmChoice"><legend>{t('fpm.people.direction')}</legend>
      <label><input type="radio" name="direction" checked={direction === 'lent'} onChange={() => setDirection('lent')} />{t('fpm.people.lent')}</label>
      <label><input type="radio" name="direction" checked={direction === 'borrowed'} onChange={() => setDirection('borrowed')} />{t('fpm.people.borrowed')}</label>
    </fieldset>
    <div className="cmFormGrid">
      <label className="field"><span>{t('fpm.people.person')}</span><input value={person} onChange={(e) => setPerson(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('fpm.amount')}</span><input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('fpm.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('fpm.people.lentOn')}</span><input type="date" value={lentOn} onChange={(e) => setLentOn(e.target.value)} required /></label>
      <label className="field"><span>{t('fpm.people.due')}</span><input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} /></label>
      <label className="field"><span>{t('fpm.people.remindDays')}</span><input type="number" min={0} max={30} value={daysBefore} onChange={(e) => setDaysBefore(Number(e.target.value))} disabled={!remind} /></label>
    </div>
    <label className="field"><span>{t('fpm.notes')}</span><textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} rows={2} dir="auto" /></label>
    <label className="finAdvisorCheck"><input type="checkbox" checked={remind} onChange={(e) => setRemind(e.target.checked)} />{t('fpm.people.remind')}</label>
    {loan && <label className="finAdvisorCheck"><input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} />{t('fpm.people.closed')}</label>}
    {error && <p id={errorId} className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{t('fpm.save')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button></div>
  </form>;
}

function LoanDetail({ loan, onClose }: { loan: FinancePersonLoan; onClose: () => void }) {
  const [state, reload] = useLoad(() => pm.getLoan(loan.id), [loan.id]);
  const [linking, setLinking] = useState<'handed' | 'repayment' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const directionOf = (kind: 'handed' | 'repayment'): 'in' | 'out' => (loan.direction === 'lent') === (kind === 'handed') ? 'out' : 'in';
  return <Modal title={t('fpm.people.detail', { name: loan.person })} onClose={onClose} className="fpmDialog">
    <States state={state} retry={reload}>{(l) => <>
      <dl className="finStatement">
        <div><dt>{t('fpm.amount')}</dt><dd><Money value={l.amount} currency={l.currency} /></dd></div>
        <div><dt>{t('fpm.people.handed')}</dt><dd><Money value={l.handed} currency={l.currency} /></dd></div>
        <div><dt>{t('fpm.people.repaid')}</dt><dd><Money value={l.repaid} currency={l.currency} /></dd></div>
        <div><dt>{t('fpm.people.left')}</dt><dd><Money value={l.outstanding} currency={l.currency} /></dd></div>
      </dl>
      {error && <Notice error>{error}</Notice>}
      <h3 className="finAdvisorSub">{t('fpm.movements.title')}</h3>
      <Movements rows={l.movements} onUnlink={(id) => { pm.unlinkLoan(l.id, id).then(reload).catch((e) => setError(describeError(e))); }} />
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setLinking('handed')}>{t(l.direction === 'lent' ? 'fpm.people.linkLent' : 'fpm.people.linkBorrowed')}</button>
        <button type="button" onClick={() => setLinking('repayment')} disabled={l.status === 'repaid'}>{t('fpm.people.linkRepayment')}</button>
      </div>
      {linking && <LinkDialog title={t('fpm.link.title')} kind="person_loan" direction={directionOf(linking)} currency={l.currency} around={linking === 'handed' ? l.lent_on : null}
        expected={linking === 'repayment' ? l.outstanding : l.amount} onClose={() => setLinking(null)}
        onLink={async (canonical) => { await pm.linkLoan(l.id, canonical, linking); reload(); }} />}
    </>}</States>
  </Modal>;
}

// -- payslips ---------------------------------------------------------------------------------------------

function PayslipsTab({ base }: { base: string }) {
  const [year, setYear] = useState<number | null>(null), [offset, setOffset] = useState(0);
  const [page, reload] = useLoad<FinancePayslipPage>(() => pm.listPayslips({ year, limit: PERSONAL_PAGE, offset }), [year, offset]);
  const [editing, setEditing] = useState<FinancePayslip | 'new' | null>(null), [linking, setLinking] = useState<FinancePayslip | null>(null);
  const [removing, setRemoving] = useState<FinancePayslip | null>(null), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const thisYear = Number(todayIso().slice(0, 4));
  const remove = async () => {
    if (!removing) return;
    try { await pm.deletePayslip(removing.id); setNotice({ text: t('fpm.deleted', { name: removing.employer }) }); reload(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setRemoving(null); }
  };
  return <>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <States state={page} retry={reload}>{(value) => <>
      {value.months.length > 0 && <FinCard title={t('fpm.pay.monthsTitle')} icon={<Banknote />} subtitle={t('fpm.pay.monthsSub')}>
        <div className="finTableWrap" tabIndex={0}><table className="finGrid">
          <thead><tr><th scope="col">{t('fpm.pay.month')}</th><th scope="col" className="num">{t('fpm.pay.gross')}</th><th scope="col" className="num">{t('fpm.pay.deductions')}</th>
            <th scope="col" className="num">{t('fpm.pay.net')}</th><th scope="col" className="num">{t('fpm.pay.received')}</th><th scope="col">{t('fpm.pay.breakdown')}</th></tr></thead>
          <tbody>{value.months.map((m) => <tr key={`${m.month}-${m.currency}`}>
            <th scope="row">{m.month}</th><td className="num"><Money value={m.gross} currency={m.currency} /></td>
            <td className="num"><Money value={m.deductions} currency={m.currency} /></td><td className="num"><Money value={m.net} currency={m.currency} /></td>
            <td className="num"><Money value={m.received} currency={m.currency} /></td>
            <td>{Object.entries(m.by_kind).map(([k, v]) => `${t(`fpm.deduction.${k}` as MessageKey)} ${formatAmount(v, m.currency)}`).join(' · ') || '-'}</td>
          </tr>)}</tbody>
        </table></div>
      </FinCard>}
      <FinCard title={t('fpm.pay.listTitle')} icon={<Receipt />} actions={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('fpm.pay.add')}</button>}>
        <div className="fpmFilters"><label className="field"><span>{t('fpm.pay.year')}</span>
          <select value={year ?? ''} onChange={(e) => { setYear(e.target.value ? Number(e.target.value) : null); setOffset(0); }}>
            <option value="">{t('fpm.all')}</option>{[0, 1, 2, 3].map((n) => <option key={n} value={thisYear - n}>{thisYear - n}</option>)}</select></label></div>
        {value.total === 0 ? <Empty icon={<Receipt aria-hidden="true" />} title={t('fpm.pay.emptyTitle')} text={t('fpm.pay.empty')}
          action={<button type="button" onClick={() => setEditing('new')}>{t('fpm.pay.add')}</button>} /> : <>
          <div className="finTableWrap" tabIndex={0}><table className="finGrid">
            <thead><tr><th scope="col">{t('fpm.pay.month')}</th><th scope="col">{t('fpm.pay.employer')}</th><th scope="col" className="num">{t('fpm.pay.gross')}</th>
              <th scope="col" className="num">{t('fpm.pay.net')}</th><th scope="col">{t('fpm.pay.check')}</th><th scope="col"><span className="visuallyHidden">{t('fpm.actions')}</span></th></tr></thead>
            <tbody>{value.items.map((s) => <tr key={s.id}>
              <th scope="row">{s.month}</th><td dir="auto">{s.employer}</td><td className="num"><Money value={s.gross} currency={s.currency} /></td>
              <td className="num"><Money value={s.net} currency={s.currency} /></td>
              <td><StatusChip tone={s.status === 'matched' ? 'ok' : s.status === 'differs' ? 'warn' : 'muted'}>{s.status === 'differs'
                ? t('fpm.pay.differs', { amount: amount(s.difference, s.currency) }) : t(`fpm.pay.status.${s.status}` as MessageKey)}</StatusChip></td>
              <td><RowActions name={`${s.employer} ${s.month}`} onOpen={() => setLinking(s)} onEdit={() => setEditing(s)} onDelete={() => setRemoving(s)} /></td>
            </tr>)}</tbody>
          </table></div>
          <Pager total={value.total} offset={offset} onOffset={setOffset} />
        </>}
      </FinCard>
    </>}</States>
    {editing && <Modal title={editing === 'new' ? t('fpm.pay.add') : t('fpm.pay.edit')} onClose={() => setEditing(null)} className="fpmDialog">
      <PayslipForm slip={editing === 'new' ? null : editing} base={base} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); reload(); } }} /></Modal>}
    {linking && <PayslipDetail slip={linking} onClose={() => { setLinking(null); reload(); }} />}
    {removing && <ConfirmDialog title={t('fpm.deleteTitle', { name: removing.employer })} confirmLabel={t('fpm.delete')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('fpm.deleteText')}</p></ConfirmDialog>}
  </>;
}

function PayslipForm({ slip, base, onDone }: { slip: FinancePayslip | null; base: string; onDone: (text: string | null) => void }) {
  const [employer, setEmployer] = useState(slip?.employer ?? ''), [month, setMonth] = useState(slip?.month ?? todayIso().slice(0, 7));
  const [currency, setCurrency] = useState(slip?.currency ?? base), [gross, setGross] = useState(slip?.gross ?? ''), [paidOn, setPaidOn] = useState(slip?.paid_on ?? '');
  const [rows, setRows] = useState<FinanceDeduction[]>(slip?.deductions ?? [{ kind: 'social_insurance', amount: '', label: null }]);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const net = previewNet(gross, rows, currency);
  const setRow = (index: number, patch: Partial<FinanceDeduction>) => setRows(rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    const body = { employer: employer.trim(), month, currency, gross: gross.trim(), paid_on: paidOn || null,
      deductions: rows.filter((r) => r.amount.trim()).map((r) => ({ kind: r.kind, amount: r.amount.trim(), label: r.label?.trim() || null })) };
    try { if (slip) await pm.updatePayslip(slip.id, body); else await pm.createPayslip(body); onDone(t('fpm.saved', { name: body.employer })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('fpm.pay.employer')}</span><input value={employer} onChange={(e) => setEmployer(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('fpm.pay.month')}</span><input type="month" value={month} onChange={(e) => setMonth(e.target.value)} required /></label>
      <label className="field"><span>{t('fpm.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('fpm.pay.gross')}</span><input value={gross} onChange={(e) => setGross(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('fpm.pay.paidOn')}</span><input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} /></label>
    </div>
    <fieldset className="fpmDeductions"><legend>{t('fpm.pay.deductions')}</legend>
      {rows.map((row, index) => <div key={index} className="fpmDeduction">
        <label className="field"><span>{t('fpm.pay.kind')}</span><select value={row.kind} onChange={(e) => setRow(index, { kind: e.target.value as FinanceDeductionKind })}>
          {FINANCE_DEDUCTION_KINDS.map((k) => <option key={k} value={k}>{t(`fpm.deduction.${k}` as MessageKey)}</option>)}</select></label>
        <label className="field"><span>{t('fpm.amount')}</span><input value={row.amount} onChange={(e) => setRow(index, { amount: e.target.value })} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('fpm.pay.label')}</span><input value={row.label ?? ''} onChange={(e) => setRow(index, { label: e.target.value })} maxLength={80} dir="auto" /></label>
        <button type="button" className="secondary iconOnly" onClick={() => setRows(rows.filter((_, i) => i !== index))} aria-label={t('fpm.pay.removeDeduction')}><Trash2 aria-hidden="true" /></button>
      </div>)}
      <button type="button" className="secondary" onClick={() => setRows([...rows, { kind: 'other', amount: '', label: null }])} disabled={rows.length >= 20}><Plus aria-hidden="true" />{t('fpm.pay.addDeduction')}</button>
    </fieldset>
    <p className="fpmNet" aria-live="polite">{net !== null ? t('fpm.pay.netPreview', { amount: amount(net, currency) }) : t('fpm.pay.netUnknown')}</p>
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{t('fpm.save')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button></div>
  </form>;
}

function PayslipDetail({ slip, onClose }: { slip: FinancePayslip; onClose: () => void }) {
  const [state, reload] = useLoad(() => pm.getPayslip(slip.id), [slip.id]);
  const [linking, setLinking] = useState(false), [error, setError] = useState<string | null>(null);
  return <Modal title={t('fpm.pay.detail', { name: slip.employer, month: slip.month })} onClose={onClose} className="fpmDialog">
    <States state={state} retry={reload}>{(s) => <>
      <dl className="finStatement">
        <div><dt>{t('fpm.pay.gross')}</dt><dd><Money value={s.gross} currency={s.currency} /></dd></div>
        <div><dt>{t('fpm.pay.deductions')}</dt><dd><Money value={s.total_deductions} currency={s.currency} /></dd></div>
        <div><dt>{t('fpm.pay.net')}</dt><dd><Money value={s.net} currency={s.currency} /></dd></div>
        <div><dt>{t('fpm.pay.received')}</dt><dd><Money value={s.received} currency={s.currency} /></dd></div>
      </dl>
      <p className={s.status === 'differs' ? 'finAdvisorBad' : 'muted'}>{s.status === 'differs' ? t('fpm.pay.differsText', { amount: amount(s.difference, s.currency) })
        : t(`fpm.pay.status.${s.status}Text` as MessageKey)}</p>
      {error && <Notice error>{error}</Notice>}
      <h3 className="finAdvisorSub">{t('fpm.pay.credits')}</h3>
      <Movements rows={s.movements} onUnlink={(id) => { pm.unlinkPayslip(s.id, id).then(reload).catch((e) => setError(describeError(e))); }} />
      <div className="actions"><button type="button" onClick={() => setLinking(true)}>{t('fpm.pay.link')}</button></div>
      {linking && <LinkDialog title={t('fpm.pay.link')} kind="payslip" direction="in" currency={s.currency} around={s.paid_on ?? `${s.month}-28`} expected={s.net}
        onClose={() => setLinking(false)} onLink={async (canonical) => { await pm.linkPayslip(s.id, canonical); reload(); }} />}
    </>}</States>
  </Modal>;
}

// -- sending money home -----------------------------------------------------------------------------------

function RemittancesTab({ base, onOpen }: { base: string; onOpen: (tab: FinanceTab) => void }) {
  const [q, setQ] = useState(''), [offset, setOffset] = useState(0);
  const [page, reload] = useLoad<FinanceRemittancePage>(() => pm.listRemittances({ q, limit: PERSONAL_PAGE, offset }), [q, offset]);
  const [editing, setEditing] = useState<FinanceRemittance | 'new' | null>(null), [linking, setLinking] = useState<FinanceRemittance | null>(null);
  const [removing, setRemoving] = useState<FinanceRemittance | null>(null), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const searchId = useId();
  const remove = async () => {
    if (!removing) return;
    try { await pm.deleteRemittance(removing.id); setNotice({ text: t('fpm.deleted', { name: removing.recipient }) }); reload(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setRemoving(null); }
  };
  return <>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <States state={page} retry={reload}>{(value) => <>
      <div className="finReportGrid fpmGrid">
        <FinCard title={t('fpm.remit.monthsTitle')} icon={<CalendarClock />}>
          {value.months.length === 0 ? <p className="muted">{t('fpm.remit.monthsNone')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
            <thead><tr><th scope="col">{t('fpm.pay.month')}</th><th scope="col" className="num">{t('fpm.remit.sent')}</th><th scope="col" className="num">{t('fpm.remit.fees')}</th>
              <th scope="col" className="num">{t('fpm.remit.count')}</th><th scope="col">{t('fpm.remit.arrived')}</th></tr></thead>
            <tbody>{value.months.slice(0, 6).map((m) => <tr key={`${m.month}-${m.currency}`}><th scope="row">{m.month}</th>
              <td className="num"><Money value={m.sent} currency={m.currency} /></td><td className="num"><Money value={m.fees} currency={m.currency} /></td>
              <td className="num">{m.count}</td><td>{Object.entries(m.received).map(([c, v]) => formatAmount(v, c)).join(' · ') || '-'}</td></tr>)}</tbody>
          </table></div>}
        </FinCard>
        <FinCard title={t('fpm.remit.providersTitle')} icon={<Send />} subtitle={t('fpm.remit.providersSub')}>
          {value.providers.length === 0 ? <p className="muted">{t('fpm.remit.providersNone')}</p> : <ul className="finMoveList">{value.providers.map((p, i) => <li key={`${p.provider}-${p.corridor}`}>
            <div><span dir="auto">{p.provider}</span><small dir="ltr">{p.corridor} · {t('fpm.remit.transfers', { count: p.transfers })}</small></div>
            <span>{p.average_fee_percent !== null ? t('fpm.remit.feePct', { percent: isolate(p.average_fee_percent) }) : '-'}</span>
            <span dir="ltr">{p.latest_effective_rate ?? '-'}</span>
            {i === 0 && <StatusChip tone="ok">{t('fpm.remit.cheapest')}</StatusChip>}
          </li>)}</ul>}
          <button type="button" className="secondary" onClick={() => onOpen('advisor')}>{t('fpm.remit.compareLive')}</button>
        </FinCard>
      </div>
      <FinCard title={t('fpm.remit.listTitle')} icon={<Send />} actions={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('fpm.remit.add')}</button>}>
        <div className="fpmFilters"><label className="field" htmlFor={searchId}><span><Search aria-hidden="true" />{t('fpm.search')}</span>
          <input id={searchId} value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} dir="auto" /></label></div>
        {value.total === 0 ? <Empty icon={<Send aria-hidden="true" />} title={t('fpm.remit.emptyTitle')} text={t('fpm.remit.empty')}
          action={<button type="button" onClick={() => setEditing('new')}>{t('fpm.remit.add')}</button>} /> : <>
          <div className="finTableWrap" tabIndex={0}><table className="finGrid">
            <thead><tr><th scope="col">{t('fpm.remit.date')}</th><th scope="col">{t('fpm.remit.recipient')}</th><th scope="col">{t('fpm.remit.provider')}</th>
              <th scope="col" className="num">{t('fpm.remit.sent')}</th><th scope="col" className="num">{t('fpm.remit.fee')}</th><th scope="col" className="num">{t('fpm.remit.arrived')}</th>
              <th scope="col"><span className="visuallyHidden">{t('fpm.actions')}</span></th></tr></thead>
            <tbody>{value.items.map((r) => <tr key={r.id}>
              <th scope="row">{day(r.sent_on)}{r.regular ? <> <StatusChip tone="info">{t('fpm.remit.regular')}</StatusChip></> : null}</th>
              <td dir="auto">{r.recipient}</td><td dir="auto">{r.provider}</td>
              <td className="num"><Money value={r.amount} currency={r.currency} /></td><td className="num"><Money value={r.fee} currency={r.currency} /></td>
              <td className="num">{r.received_amount && r.received_currency ? <Money value={r.received_amount} currency={r.received_currency} /> : '-'}</td>
              <td><RowActions name={`${r.recipient} ${day(r.sent_on)}`} onOpen={() => setLinking(r)} onEdit={() => setEditing(r)} onDelete={() => setRemoving(r)} /></td>
            </tr>)}</tbody>
          </table></div>
          <Pager total={value.total} offset={offset} onOffset={setOffset} />
        </>}
      </FinCard>
    </>}</States>
    {editing && <Modal title={editing === 'new' ? t('fpm.remit.add') : t('fpm.remit.edit')} onClose={() => setEditing(null)} className="fpmDialog">
      <RemittanceForm item={editing === 'new' ? null : editing} base={base} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); reload(); } }} /></Modal>}
    {linking && <RemittanceDetail item={linking} onClose={() => { setLinking(null); reload(); }} />}
    {removing && <ConfirmDialog title={t('fpm.deleteTitle', { name: removing.recipient })} confirmLabel={t('fpm.delete')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('fpm.deleteText')}</p></ConfirmDialog>}
  </>;
}

function RemittanceForm({ item, base, onDone }: { item: FinanceRemittance | null; base: string; onDone: (text: string | null) => void }) {
  const [sentOn, setSentOn] = useState(item?.sent_on ?? todayIso()), [value, setValue] = useState(item?.amount ?? ''), [currency, setCurrency] = useState(item?.currency ?? base);
  const [fee, setFee] = useState(item?.fee ?? '0'), [rate, setRate] = useState(item?.fx_rate ?? ''), [received, setReceived] = useState(item?.received_amount ?? '');
  const [receivedCurrency, setReceivedCurrency] = useState(item?.received_currency ?? ''), [recipient, setRecipient] = useState(item?.recipient ?? '');
  const [provider, setProvider] = useState(item?.provider ?? ''), [regular, setRegular] = useState(item?.regular ?? false), [notes, setNotes] = useState(item?.notes ?? '');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const preview = !received && receivedCurrency ? previewReceived(value, rate, receivedCurrency) : null;
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    const body = { sent_on: sentOn, amount: value.trim(), currency, fee: fee.trim() || '0', fx_rate: rate.trim() || null, received_amount: received.trim() || null,
      received_currency: receivedCurrency || null, recipient: recipient.trim(), provider: provider.trim(), regular, notes: notes.trim() || null };
    try { if (item) await pm.updateRemittance(item.id, body); else await pm.createRemittance(body); onDone(t('fpm.saved', { name: body.recipient })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('fpm.remit.recipient')}</span><input value={recipient} onChange={(e) => setRecipient(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('fpm.remit.provider')}</span><input value={provider} onChange={(e) => setProvider(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('fpm.remit.date')}</span><input type="date" value={sentOn} onChange={(e) => setSentOn(e.target.value)} required /></label>
      <label className="field"><span>{t('fpm.remit.sent')}</span><input value={value} onChange={(e) => setValue(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('fpm.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('fpm.remit.fee')}</span><input value={fee} onChange={(e) => setFee(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('fpm.remit.rate')}</span><input value={rate} onChange={(e) => setRate(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('fpm.remit.arrived')}</span><input value={received} onChange={(e) => setReceived(e.target.value)} inputMode="decimal" dir="ltr" placeholder={preview ?? undefined} /></label>
      <label className="field"><span>{t('fpm.remit.arrivedCurrency')}</span><input value={receivedCurrency} onChange={(e) => setReceivedCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" /></label>
    </div>
    {preview && <p className="muted" aria-live="polite">{t('fpm.remit.preview', { amount: amount(preview, receivedCurrency) })}</p>}
    <label className="field"><span>{t('fpm.notes')}</span><textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} rows={2} dir="auto" /></label>
    <label className="finAdvisorCheck"><input type="checkbox" checked={regular} onChange={(e) => setRegular(e.target.checked)} />{t('fpm.remit.regularLabel')}</label>
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{t('fpm.save')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button></div>
  </form>;
}

function RemittanceDetail({ item, onClose }: { item: FinanceRemittance; onClose: () => void }) {
  const [state, reload] = useLoad(() => pm.getRemittance(item.id), [item.id]);
  const [linking, setLinking] = useState(false), [error, setError] = useState<string | null>(null);
  return <Modal title={t('fpm.remit.detail', { name: item.recipient })} onClose={onClose} className="fpmDialog">
    <States state={state} retry={reload}>{(r) => <>
      <dl className="finStatement">
        <div><dt>{t('fpm.remit.total')}</dt><dd><Money value={r.total_paid} currency={r.currency} /></dd></div>
        <div><dt>{t('fpm.remit.feeShare')}</dt><dd>{r.fee_percent !== null ? t('fpm.remit.feePct', { percent: isolate(r.fee_percent) }) : '-'}</dd></div>
        <div><dt>{t('fpm.remit.effective')}</dt><dd dir="ltr">{r.effective_rate ?? '-'}</dd></div>
        <div><dt>{t('fpm.remit.linked')}</dt><dd><Money value={r.linked} currency={r.currency} /></dd></div>
      </dl>
      {error && <Notice error>{error}</Notice>}
      <h3 className="finAdvisorSub">{t('fpm.movements.title')}</h3>
      <Movements rows={r.movements} onUnlink={(id) => { pm.unlinkRemittance(r.id, id).then(reload).catch((e) => setError(describeError(e))); }} />
      <div className="actions"><button type="button" onClick={() => setLinking(true)}>{t('fpm.remit.link')}</button></div>
      {linking && <LinkDialog title={t('fpm.remit.link')} kind="remittance" direction="out" currency={r.currency} around={r.sent_on} expected={r.total_paid}
        onClose={() => setLinking(false)} onLink={async (canonical) => { await pm.linkRemittance(r.id, canonical); reload(); }} />}
    </>}</States>
  </Modal>;
}

// -- fixed commitments ------------------------------------------------------------------------------------

function CommitmentsTab({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [state, reload] = useLoad<FinanceCommitments>(() => pm.getCommitments(), []);
  return <States state={state} retry={reload}>{(value) => value.items.length === 0 && value.not_counted.length === 0
    ? <FinCard title={t('fpm.fixed.title')} icon={<Wallet />}><Empty icon={<Wallet aria-hidden="true" />} title={t('fpm.fixed.emptyTitle')} text={t('fpm.fixed.empty')}
      action={<div className="finCardButtons"><button type="button" onClick={() => onOpen('bills')}>{t('fpm.fixed.addBill')}</button>
        <button type="button" className="secondary" onClick={() => onOpen('debts')}>{t('fpm.fixed.addDebt')}</button></div>} /></FinCard>
    : <>
      <KpiRow label={t('fpm.fixed.kpis')}>{value.totals.map((row) => <KpiCard key={row.currency} icon={<Wallet />} label={t('fpm.fixed.total', { currency: row.currency })}
        value={<Money value={row.amount} currency={row.currency} />} hint={t('fpm.fixed.month', { month: value.month })} />)}</KpiRow>
      {value.totals.map((row) => <FinCard key={row.currency} title={t('fpm.fixed.byKind', { currency: row.currency })} icon={<Wallet />} subtitle={t('fpm.fixed.note')}>
        {commitmentGroups(value.items, row.currency).map((group) => <section key={group.kind} className="fpmGroup" aria-label={t(`fpm.kind.${group.kind}` as MessageKey)}>
          <h3 className="finAdvisorSub"><span>{t(`fpm.kind.${group.kind}` as MessageKey)}</span><Money value={group.total} currency={row.currency} /></h3>
          <ul className="finMoveList">{group.items.map((item) => <li key={`${item.source}-${item.ref_id}`}>
            <div><span dir="auto">{item.name}</span>{item.note && <small>{t(`fpm.fixed.note.${item.note}` as MessageKey)}</small>}</div>
            <Money value={item.monthly} currency={item.currency} /></li>)}</ul>
        </section>)}
      </FinCard>)}
      {value.not_counted.length > 0 && <FinCard title={t('fpm.fixed.notCounted')} icon={<Wallet />}>
        <ul className="finMoveList">{value.not_counted.map((item) => <li key={`${item.source}-${item.ref_id}`}>
          <div><span dir="auto">{item.name}</span><small>{t(`fpm.fixed.note.${item.note ?? 'once'}` as MessageKey)}</small></div>
          <Money value={item.monthly} currency={item.currency} /></li>)}</ul>
      </FinCard>}
    </>}</States>;
}
