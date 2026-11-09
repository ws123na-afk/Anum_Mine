// The business journal (docs/business-books.md#the-journal): Accounts (the chart), Ledger (the journal posted from the source
// documents, what is not posted yet and why, Post now, manual entries and their reversal, the general ledger of an account),
// Statements (trial balance, balance sheet, P&L with its reconciliation, cash flow, changes in equity, comparatives, policy
// notes) and Close (close months and years, reopen with a reason). The API is the authority: it posts, converts with dated
// rates only and refuses anything dated in a closed period; the page shows what it says, in every state.
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import { BookOpen, Download, Lock, Plus, RefreshCw, Trash2, Unlock } from 'lucide-react';
import type {
  BooksSetup, LedgerAccount, LedgerAccountList, LedgerCloseStatus, LedgerEntry, LedgerGeneral, LedgerPnl, LedgerSourceKind, LedgerStatements, LedgerStatus,
  LedgerSubtype,
} from '@anum/contracts';
import { LEDGER_SOURCE_KINDS } from '@anum/contracts';
import * as api from './lib/businessLedgerApi';
import {
  ADDABLE_SUBTYPES, accountName, accountProblem, balanceRows, closingNet, flowLabel, issueText, lastMonthEnd, manualBody, manualProblem, manualTotals,
  pnlRows, sourceHash, sourceLabel, subtypeLabel, type ManualLineDraft, type StatementRow,
} from './lib/businessLedger';
import { PERIOD_PRESETS, isNegative, money, presetPeriod, todayIso, type PeriodPreset } from './lib/businessBooks';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { Badge, Denied, Empty, Failed, Note, Pager, ReasonDialog, day, failure } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';

const label = (key: string) => t(key as MessageKey);
const dayText = (value: string | null | undefined) => (value ? day(value) : '-');
const key = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(16).slice(2)}`);
type Load<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'denied'; message: string } | { state: 'error'; message: string };
const loadError = <T,>(error: unknown): Load<T> => (isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) });

function useLoad<T>(fetch: () => Promise<T>, deps: unknown[]): [Load<T>, () => Promise<void>] {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' });
  const run = useCallback(async () => {
    setLoad({ state: 'loading' });
    try { setLoad({ state: 'ready', value: await fetch() }); } catch (error) { setLoad(loadError<T>(error)); }
  }, deps);
  useEffect(() => { void run(); }, [run]);
  return [load, run];
}

function Amount({ value, currency }: { value: string; currency: string }) {
  return <span className={isNegative(value) ? 'bkNeg' : undefined}>{money(value, currency)}</span>;
}

// ---- chart of accounts ---------------------------------------------------------------------------------------------------

export function AccountsSection({ setup }: { setup: BooksSetup }) {
  const [archived, setArchived] = useState(false), [tick, setTick] = useState(0);
  const [editing, setEditing] = useState<LedgerAccount | 'new' | null>(null), [posting, setPosting] = useState<{ text: string; error?: boolean } | null>(null);
  const [load, reload] = useLoad<LedgerAccountList>(() => api.listAccounts(archived), [archived, tick]);
  async function makeChart() {
    setPosting(null);
    try { await api.postNow(); setPosting({ text: t('ledger.posted') }); setTick((n) => n + 1); } catch (error) { setPosting({ text: failure(error, t('ledger.postNow')), error: true }); }
  }
  if (load.state === 'loading') return <Note>{t('books.reading')}</Note>;
  if (load.state === 'denied') return <Denied text={load.message} />;
  if (load.state === 'error') return <Failed text={load.message} onRetry={() => void reload()} />;
  const list = load.value;
  const edit = list.can_edit;
  return <>
    <p className="muted bzHint">{t('ledger.accounts.help')}</p>
    <div className="bzToolbar">
      <label className="bzCheck"><input type="checkbox" checked={archived} onChange={(event) => setArchived(event.target.checked)} />{t('ledger.accounts.showArchived')}</label>
      <div className="bzToolbarEnd">{edit && list.items.length > 0 && <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('ledger.accounts.new')}</button>}</div>
    </div>
    {!edit && <Note>{t('ledger.accountantsOnly')}</Note>}
    {posting && <Note error={posting.error}>{posting.text}</Note>}
    {list.items.length === 0 ? <Empty title={t('ledger.accounts.emptyTitle')} text={t('ledger.accounts.emptyText')}
      action={setup.permissions.write ? <button type="button" onClick={() => void makeChart()}><RefreshCw aria-hidden="true" />{t('ledger.postNow')}</button> : undefined} />
      : <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('ledger.accounts.caption')}</caption>
        <thead><tr><th scope="col">{t('ledger.col.code')}</th><th scope="col">{t('ledger.col.account')}</th><th scope="col" className="bzHideMd">{t('ledger.col.kind')}</th>
          <th scope="col" className="bzHideMd">{t('books.col.category')}</th>{edit && <th scope="col"><span className="visuallyHidden">{t('books.col.actions')}</span></th>}</tr></thead>
        <tbody>{list.items.map((account) => <tr key={account.id}>
          <td className="nowrap"><bdi>{account.code}</bdi></td>
          <th scope="row"><span dir="auto">{accountName(account)}</span>{account.system_key && <small>{t('ledger.accounts.system')}</small>}
            {account.archived && <> <Badge tone="neutral">{t('ledger.accounts.archived')}</Badge></>}</th>
          <td className="bzHideMd">{subtypeLabel(account.subtype)}</td><td className="bzHideMd">{account.category ?? '-'}</td>
          {edit && <td><button type="button" className="secondary" onClick={() => setEditing(account)} aria-label={t('ledger.accounts.editLabel', { code: account.code })}>{t('ledger.accounts.edit')}</button></td>}
        </tr>)}</tbody></table></div>}
    {editing && <AccountDialog account={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setTick((n) => n + 1); }} />}
  </>;
}

function AccountDialog({ account, onClose, onSaved }: { account: LedgerAccount | null; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ code: account?.code ?? '', name: account ? accountName(account) : '', subtype: (account?.subtype ?? '') as LedgerSubtype | '',
    category: account?.category ?? '', archived: account?.archived ?? false });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const ids = { code: useId(), name: useId(), subtype: useId(), category: useId() };
  async function save(event: FormEvent) {
    event.preventDefault();
    const wrong = accountProblem(form);
    if (wrong) { setProblem(t(wrong)); return; }
    setBusy(true); setProblem(null);
    try {
      const body = { code: form.code.trim(), name: form.name.trim(), category: form.category.trim() || null };
      if (account) await api.updateAccount(account.id, { ...body, archived: form.archived, version: account.version });
      else await api.createAccount({ ...body, subtype: form.subtype as LedgerSubtype });
      onSaved();
    } catch (error) { setProblem(failure(error, t('ledger.accounts.save'))); } finally { setBusy(false); }
  }
  const subtypes = account ? [account.subtype] : ADDABLE_SUBTYPES;
  return <Modal title={account ? t('ledger.accounts.editTitle', { code: account.code }) : t('ledger.accounts.new')} onClose={onClose}>
    <form onSubmit={(event) => void save(event)} noValidate>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.subtype}><span>{t('ledger.col.kind')}</span><select id={ids.subtype} value={form.subtype} disabled={Boolean(account)}
          onChange={(event) => setForm({ ...form, subtype: event.target.value as LedgerSubtype })}>
          <option value="">{t('ledger.accounts.chooseKind')}</option>{subtypes.map((item) => <option key={item} value={item}>{subtypeLabel(item)}</option>)}</select></label>
        <label className="field" htmlFor={ids.code}><span>{t('ledger.col.code')}</span><input id={ids.code} inputMode="numeric" maxLength={6} value={form.code}
          onChange={(event) => setForm({ ...form, code: event.target.value.replace(/\D/g, '') })} aria-describedby={`${ids.code}-help`} /></label>
        <label className="field" htmlFor={ids.name}><span>{t('ledger.col.account')}</span><input id={ids.name} maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        {(['cost_of_sales', 'operating_expense'] as string[]).includes(form.subtype) && <label className="field" htmlFor={ids.category}><span>{t('ledger.accounts.category')}</span>
          <input id={ids.category} maxLength={40} value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} placeholder="office_rent" /></label>}
      </div>
      <p className="muted bzHint" id={`${ids.code}-help`}>{t('ledger.accounts.codeHelp')}</p>
      {account && !account.system_key && <label className="bzCheck"><input type="checkbox" checked={form.archived} onChange={(event) => setForm({ ...form, archived: event.target.checked })} />{t('ledger.accounts.archive')}</label>}
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
        <button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('ledger.accounts.save')}</button></div>
    </form>
  </Modal>;
}

// ---- the journal ----------------------------------------------------------------------------------------------------------

export function LedgerSection({ setup }: { setup: BooksSetup }) {
  const [tick, setTick] = useState(0);
  const [status, reloadStatus] = useLoad<LedgerStatus>(() => api.getStatus(), [tick]);
  const [source, setSource] = useState<LedgerSourceKind | ''>('');
  const [span, setSpan] = useState<{ from: string; to: string }>({ from: '', to: '' });
  const [posting, setPosting] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false), [reversing, setReversing] = useState<LedgerEntry | null>(null), [general, setGeneral] = useState(false);
  const list = usePaged((offset) => api.listJournal(span, source, offset, 15), [source, span.from, span.to, tick]);
  const ids = { source: useId(), from: useId(), to: useId() };
  const canAccount = status.state === 'ready' ? status.value.can_account : Boolean(setup.permissions.account);
  async function postNow() {
    setBusy(true); setPosting(null);
    try {
      const result = await api.postNow();
      setPosting({ text: t('ledger.postResult', { posted: result.posted, reversed: result.reversed, issues: result.issues }) });
      setTick((n) => n + 1);
    } catch (error) { setPosting({ text: failure(error, t('ledger.postNow')), error: true }); } finally { setBusy(false); }
  }
  const filtered = Boolean(source || span.from || span.to);
  const base = setup.settings?.base_currency ?? 'SAR';
  return <>
    <p className="muted bzHint">{t('ledger.help')}</p>
    <section className="surface bzCard" aria-labelledby="bk-ledger-status"><h2 id="bk-ledger-status">{t('ledger.status.title')}</h2>
      {status.state === 'loading' && <Note>{t('books.reading')}</Note>}
      {status.state === 'denied' && <Denied text={status.message} />}
      {status.state === 'error' && <Failed text={status.message} onRetry={() => void reloadStatus()} />}
      {status.state === 'ready' && <>
        {status.value.complete ? <Note>{t('ledger.status.complete')}</Note>
          : <Note error>{t('ledger.status.pending', { posts: status.value.to_post + status.value.to_reverse, issues: status.value.issues })}</Note>}
        {status.value.truncated && <Note error>{t('ledger.status.truncated')}</Note>}
        {status.value.scheduled > 0 && <p className="muted bzHint">{t('ledger.status.scheduled', { count: status.value.scheduled })}</p>}
        {status.value.closed_through && <p className="muted bzHint"><Lock aria-hidden="true" className="bkInlineIcon" /> {t('ledger.closedThrough', { day: dayText(status.value.closed_through) })}</p>}
        {status.value.issue_rows.length > 0 && <ul className="bkList" aria-label={t('ledger.status.issues')}>{status.value.issue_rows.map((issue) => <li key={issue.event_key}>
          <span><span dir="auto">{sourceLabel(issue.source_kind)}: {issue.description}</span><small>{issueText(issue)}</small></span>
          <span className="nowrap">{dayText(issue.event_date)}</span></li>)}</ul>}
        {status.value.issues > 0 && <p className="muted bzHint">{t('ledger.status.fixRates')}</p>}
        <div className="bzInline">
          {status.value.can_post && <button type="button" disabled={busy} onClick={() => void postNow()}><RefreshCw aria-hidden="true" />{busy ? t('dialog.working') : t('ledger.postNow')}</button>}
          <button type="button" className="secondary" onClick={() => setGeneral(true)}><BookOpen aria-hidden="true" />{t('ledger.general.open')}</button>
        </div>
        {posting && <Note error={posting.error}>{posting.text}</Note>}
      </>}
    </section>
    <div className="bzToolbar">
      <label className="bzCompact" htmlFor={ids.source}><span>{t('ledger.col.source')}</span>
        <select id={ids.source} value={source} onChange={(event) => setSource(event.target.value as LedgerSourceKind | '')}>
          <option value="">{t('ledger.allSources')}</option>{LEDGER_SOURCE_KINDS.map((item) => <option key={item} value={item}>{sourceLabel(item)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.from}><span>{t('books.from')}</span><input id={ids.from} type="date" value={span.from} max={span.to || undefined}
        onChange={(event) => setSpan({ ...span, from: event.target.value })} /></label>
      <label className="bzCompact" htmlFor={ids.to}><span>{t('books.to')}</span><input id={ids.to} type="date" value={span.to} min={span.from || undefined}
        onChange={(event) => setSpan({ ...span, to: event.target.value })} /></label>
      <div className="bzToolbarEnd">{canAccount && <button type="button" onClick={() => setManual(true)}><Plus aria-hidden="true" />{t('ledger.manual.new')}</button>}</div>
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setSource(''); setSpan({ from: '', to: '' }); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('ledger.emptyTitle')} text={t('ledger.emptyText')}
      action={setup.permissions.write ? <button type="button" onClick={() => void postNow()}><RefreshCw aria-hidden="true" />{t('ledger.postNow')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <ol className="bkEntries" aria-label={t('ledger.entries')}>{list.items.map((entry) => <li key={entry.id} className="surface bkEntry">
        <div className="bkEntryHead">
          <span><strong dir="auto">{entry.description}</strong>
            <small>{sourceLabel(entry.source_kind)} · {dayText(entry.entry_date)}{entry.late ? ` · ${t('ledger.lateText', { day: dayText(entry.event_date) })}` : ''}</small></span>
          <span className="bzInline">
            {entry.status === 'reversed' && <Badge tone="neutral">{t('ledger.reversed')}</Badge>}
            {entry.reverses_id && <Badge tone="neutral">{t('ledger.reversal')}</Badge>}
            {entry.late && <Badge tone="warning">{t('ledger.late')}</Badge>}
            {sourceHash(entry.source_kind, entry.source_id) && <a className="button secondary" href={`#${sourceHash(entry.source_kind, entry.source_id)}`}>{t('ledger.openSource')}</a>}
            {entry.can_reverse && canAccount && <button type="button" className="secondary" onClick={() => setReversing(entry)}
              aria-label={t('ledger.reverseLabel', { what: entry.description })}>{t('ledger.reverse')}</button>}
          </span>
        </div>
        <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('ledger.linesOf', { what: entry.description })}><table className="dataTable bkLines">
          <caption className="visuallyHidden">{t('ledger.linesOf', { what: entry.description })}</caption>
          <thead><tr><th scope="col">{t('ledger.col.account')}</th><th scope="col" className="num">{t('ledger.col.debit')}</th><th scope="col" className="num">{t('ledger.col.credit')}</th></tr></thead>
          <tbody>{entry.lines.map((line) => <tr key={line.id}><th scope="row"><bdi>{line.account_code}</bdi> <span dir="auto">{accountName({ system_key: line.account_key ?? null, name: line.account_name ?? '' })}</span>
            {line.rate && <small>{t('ledger.converted', { amount: money(line.amount, line.currency), rate: line.rate, day: dayText(line.rate_date), source: line.rate_source ?? '' })}</small>}</th>
            <td className="num">{line.debit !== '0.00' ? money(line.debit, base) : ''}</td><td className="num">{line.credit !== '0.00' ? money(line.credit, base) : ''}</td></tr>)}</tbody>
        </table></div>
      </li>)}</ol>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {manual && <ManualDialog setup={setup} onClose={() => setManual(false)} onSaved={() => { setManual(false); setTick((n) => n + 1); }} />}
    {reversing && <ReasonDialog title={t('ledger.reverseTitle')} label={t('books.reason')} confirm={t('ledger.reverse')}
      onConfirm={async (reason) => { await api.reverseEntry(reversing.id, reason); setReversing(null); setTick((n) => n + 1); }} onCancel={() => setReversing(null)} />}
    {general && <GeneralLedgerDrawer setup={setup} onClose={() => setGeneral(false)} />}
  </>;
}

function ManualDialog({ setup, onClose, onSaved }: { setup: BooksSetup; onClose: () => void; onSaved: () => void }) {
  const [accounts, reload] = useLoad(() => api.listAccounts(false), []);
  const empty = (): ManualLineDraft => ({ account_id: '', debit: '', credit: '' });
  const [form, setForm] = useState({ entry_date: todayIso(), description: '', lines: [empty(), empty()] });
  const [busy, setBusy] = useState(false), [problem, setProblem] = useState<string | null>(null);
  const [idem] = useState(key);
  const ids = { date: useId(), words: useId() };
  const base = setup.settings?.base_currency ?? 'SAR';
  const totals = manualTotals(form.lines);
  async function save(event: FormEvent) {
    event.preventDefault();
    const wrong = manualProblem(form);
    if (wrong) { setProblem(t(wrong)); return; }
    setBusy(true); setProblem(null);
    try { await api.createManual(manualBody(form), idem); onSaved(); } catch (error) { setProblem(failure(error, t('ledger.manual.new'))); } finally { setBusy(false); }
  }
  const setLine = (index: number, change: Partial<ManualLineDraft>) => setForm({ ...form, lines: form.lines.map((line, i) => (i === index ? { ...line, ...change } : line)) });
  return <Modal title={t('ledger.manual.new')} className="bkWideDialog" onClose={onClose}>
    {accounts.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {(accounts.state === 'error' || accounts.state === 'denied') && <Failed text={accounts.message} onRetry={() => void reload()} />}
    {accounts.state === 'ready' && <form onSubmit={(event) => void save(event)} noValidate>
      <p className="muted bzHint">{t('ledger.manual.help', { currency: base })}</p>
      <div className="bzGrid2">
        <label className="field" htmlFor={ids.date}><span>{t('books.col.date')}</span><input id={ids.date} type="date" value={form.entry_date} max={todayIso()}
          onChange={(event) => setForm({ ...form, entry_date: event.target.value })} /></label>
        <label className="field" htmlFor={ids.words}><span>{t('ledger.manual.words')}</span><input id={ids.words} maxLength={300} value={form.description}
          onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      </div>
      <fieldset className="bkFieldset"><legend>{t('ledger.manual.lines')}</legend>
        {form.lines.map((line, index) => <div key={index} className="bkManualRow">
          <label className="field"><span>{t('ledger.manual.lineAccount', { n: index + 1 })}</span><select value={line.account_id} onChange={(event) => setLine(index, { account_id: event.target.value })}>
            <option value="">{t('ledger.manual.chooseAccount')}</option>{accounts.value.items.map((account) => <option key={account.id} value={account.id}>{account.code} {accountName(account)}</option>)}</select></label>
          <label className="field"><span>{t('ledger.col.debit')}</span><input inputMode="decimal" value={line.debit} onChange={(event) => setLine(index, { debit: event.target.value })} /></label>
          <label className="field"><span>{t('ledger.col.credit')}</span><input inputMode="decimal" value={line.credit} onChange={(event) => setLine(index, { credit: event.target.value })} /></label>
          <button type="button" className="secondary iconText" disabled={form.lines.length <= 2} onClick={() => setForm({ ...form, lines: form.lines.filter((_, i) => i !== index) })}
            aria-label={t('ledger.manual.removeLine', { n: index + 1 })}><Trash2 aria-hidden="true" /></button>
        </div>)}
        <div className="bzInline"><button type="button" className="secondary" disabled={form.lines.length >= 50} onClick={() => setForm({ ...form, lines: [...form.lines, empty()] })}>
          <Plus aria-hidden="true" />{t('ledger.manual.addLine')}</button></div>
        <p className={`bkTotal ${totals.debit !== totals.credit ? 'bkNeg' : ''}`} role="status">{t('ledger.manual.totals', {
          debit: money((totals.debit / 1000).toFixed(3), base), credit: money((totals.credit / 1000).toFixed(3), base) })}</p>
      </fieldset>
      {problem && <Note error>{problem}</Note>}
      <div className="actions"><button type="button" className="secondary" onClick={onClose}>{t('common.cancel')}</button>
        <button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('ledger.manual.post')}</button></div>
    </form>}
  </Modal>;
}

function GeneralLedgerDrawer({ setup, onClose }: { setup: BooksSetup; onClose: () => void }) {
  const [accounts, reloadAccounts] = useLoad(() => api.listAccounts(true), []);
  const [accountId, setAccountId] = useState('');
  const [period, setPeriod] = useState(() => ({ from: setup.default_period?.from ?? '', to: setup.default_period?.to ?? '' }));
  const [page, setPage] = useState<Load<LedgerGeneral> | null>(null), [offset, setOffset] = useState(0);
  const ids = { account: useId(), from: useId(), to: useId() };
  const fetchPage = useCallback(async (start: number) => {
    if (!accountId) return;
    setPage({ state: 'loading' });
    try { setPage({ state: 'ready', value: await api.accountLines(accountId, period, start, 50) }); setOffset(start); } catch (error) { setPage(loadError<LedgerGeneral>(error)); }
  }, [accountId, period.from, period.to]);
  useEffect(() => { void fetchPage(0); }, [fetchPage]);
  const data = page?.state === 'ready' ? page.value : null;
  return <Drawer title={t('ledger.general.title')} onClose={onClose}>
    <p className="muted bzHint">{t('ledger.general.help')}</p>
    {accounts.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {(accounts.state === 'error' || accounts.state === 'denied') && <Failed text={accounts.message} onRetry={() => void reloadAccounts()} />}
    {accounts.state === 'ready' && <div className="bzGrid2">
      <label className="field" htmlFor={ids.account}><span>{t('ledger.col.account')}</span><select id={ids.account} value={accountId} onChange={(event) => setAccountId(event.target.value)}>
        <option value="">{t('ledger.manual.chooseAccount')}</option>{accounts.value.items.map((account) => <option key={account.id} value={account.id}>{account.code} {accountName(account)}</option>)}</select></label>
      <label className="field" htmlFor={ids.from}><span>{t('books.from')}</span><input id={ids.from} type="date" value={period.from} onChange={(event) => setPeriod({ ...period, from: event.target.value })} /></label>
      <label className="field" htmlFor={ids.to}><span>{t('books.to')}</span><input id={ids.to} type="date" value={period.to} onChange={(event) => setPeriod({ ...period, to: event.target.value })} /></label>
    </div>}
    {!accountId && accounts.state === 'ready' && <Note>{t('ledger.general.choose')}</Note>}
    {page?.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {(page?.state === 'error' || page?.state === 'denied') && <Failed text={page.message} onRetry={() => void fetchPage(offset)} />}
    {data && <>
      <dl className="bkFacts"><div><dt>{t('ledger.general.opening')}</dt><dd><Amount value={data.opening} currency={data.base_currency} /></dd></div>
        <div><dt>{t('ledger.general.closing')}</dt><dd><Amount value={data.closing} currency={data.base_currency} /></dd></div></dl>
      {data.items.length === 0 ? <Empty title={t('ledger.general.emptyTitle')} text={t('ledger.general.emptyText')} /> :
        <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkLines">
          <caption className="visuallyHidden">{t('ledger.general.title')}</caption>
          <thead><tr><th scope="col">{t('books.col.date')}</th><th scope="col">{t('ledger.col.entry')}</th><th scope="col" className="num">{t('ledger.col.debit')}</th>
            <th scope="col" className="num">{t('ledger.col.credit')}</th><th scope="col" className="num">{t('ledger.col.balance')}</th></tr></thead>
          <tbody>{data.items.map((line) => <tr key={line.id}><td className="nowrap">{dayText(line.entry_date)}</td>
            <th scope="row"><span dir="auto">{line.description}</span><small>{sourceLabel(line.source_kind)}{line.late ? ` · ${t('ledger.late')}` : ''}</small>
              {sourceHash(line.source_kind, line.source_id) && <a href={`#${sourceHash(line.source_kind, line.source_id)}`}>{t('ledger.openSource')}</a>}</th>
            <td className="num">{line.debit !== '0.00' ? money(line.debit, data.base_currency) : ''}</td><td className="num">{line.credit !== '0.00' ? money(line.credit, data.base_currency) : ''}</td>
            <td className="num"><Amount value={line.balance} currency={data.base_currency} /></td></tr>)}</tbody></table></div>}
      {data.total > data.limit && <div className="bzInline">
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => void fetchPage(Math.max(0, offset - data.limit))}>{t('ledger.previous')}</button>
        <span className="muted">{t('ledger.pageOf', { from: offset + 1, to: Math.min(offset + data.limit, data.total), total: data.total })}</span>
        <button type="button" className="secondary" disabled={offset + data.limit >= data.total} onClick={() => void fetchPage(offset + data.limit)}>{t('ledger.next')}</button>
      </div>}
    </>}
  </Drawer>;
}

// ---- statements ---------------------------------------------------------------------------------------------------------------

function StatementTable({ id, title, rows, currency, compare }: { id: string; title: string; rows: StatementRow[]; currency: string;
  compare?: { label: string; rows: Map<string, string> }[] }) {
  return <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkStatement">
    <caption className="visuallyHidden">{title}</caption>
    <thead><tr><th scope="col">{t('books.col.line')}</th><th scope="col" className="num">{t('books.col.amount', { currency })}</th>
      {compare?.map((column) => <th key={column.label} scope="col" className="num">{column.label}</th>)}</tr></thead>
    <tbody>{rows.map((row) => <tr key={`${id}-${row.key}`} className={row.kind === 'line' ? 'bk-line' : row.kind === 'total' ? 'bk-total' : 'bk-result'}>
      <th scope="row"><span dir="auto">{row.label}</span></th><td className="num"><Amount value={row.amount} currency={currency} /></td>
      {compare?.map((column) => <td key={column.label} className="num">{column.rows.has(row.key) ? <Amount value={column.rows.get(row.key)!} currency={currency} /> : '-'}</td>)}</tr>)}</tbody>
  </table></div>;
}

const rowMap = (rows: StatementRow[]) => new Map(rows.filter((row) => row.kind !== 'line').map((row) => [row.key, row.amount]));
const pnlCompare = (data: LedgerStatements, pick: (pnl: LedgerPnl) => StatementRow[]) =>
  Object.entries(data.comparatives).map(([name, value]) => ({ label: t(`ledger.compare.${name}` as MessageKey, { from: dayText(value.from), to: dayText(value.to) }), rows: rowMap(pick(value.pnl)) }));

export function StatementsSection({ setup, onSection }: { setup: BooksSetup; onSection: (tab: string) => void }) {
  const settings = setup.settings!;
  const [period, setPeriod] = useState(() => presetPeriod('this_year', new Date(), settings.fiscal_year_start_month));
  const [preset, setPreset] = useState<PeriodPreset | ''>('this_year');
  const [compare, setCompare] = useState(false);
  const [exporting, setExporting] = useState<{ text: string; error?: boolean } | null>(null);
  const [load, reload] = useLoad<LedgerStatements>(() => api.getStatements(period, compare), [period.from, period.to, compare]);
  const ids = { preset: useId(), from: useId(), to: useId() };
  const choose = (value: PeriodPreset | '') => { setPreset(value); if (value) setPeriod(presetPeriod(value, new Date(), settings.fiscal_year_start_month)); };
  async function exportCsv() {
    setExporting(null);
    try { await api.exportStatements(period); setExporting({ text: t('books.exported') }); } catch (error) { setExporting({ text: failure(error, t('books.export')), error: true }); }
  }
  const data = load.state === 'ready' ? load.value : null;
  const base = data?.base_currency ?? settings.base_currency;
  const empty = data && data.trial_balance.rows.length === 0;
  return <>
    <p className="muted bzHint">{t('ledger.statements.help')}</p>
    <div className="bzToolbar bkPeriod">
      <label className="bzCompact" htmlFor={ids.preset}><span>{t('books.period')}</span>
        <select id={ids.preset} value={preset} onChange={(event) => choose(event.target.value as PeriodPreset | '')}>
          <option value="">{t('books.preset.custom')}</option>{PERIOD_PRESETS.map((item) => <option key={item} value={item}>{label(`books.preset.${item}`)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.from}><span>{t('books.from')}</span><input id={ids.from} type="date" value={period.from} max={period.to || undefined}
        onChange={(event) => { setPreset(''); setPeriod({ ...period, from: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.to}><span>{t('books.to')}</span><input id={ids.to} type="date" value={period.to} min={period.from || undefined}
        onChange={(event) => { setPreset(''); setPeriod({ ...period, to: event.target.value }); }} /></label>
      <label className="bzCheck"><input type="checkbox" checked={compare} onChange={(event) => setCompare(event.target.checked)} />{t('ledger.statements.compare')}</label>
      <div className="bzToolbarEnd"><button type="button" className="secondary" disabled={!data} onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('books.export')}</button></div>
    </div>
    {exporting && <Note error={exporting.error}>{exporting.text}</Note>}
    {load.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {load.state === 'denied' && <Denied text={load.message} />}
    {load.state === 'error' && <Failed text={load.message} onRetry={() => void reload()} />}
    {data && !data.complete && <Note error>{t('ledger.statements.incomplete', { posts: data.pending.to_post + data.pending.to_reverse, issues: data.pending.issues })}
      {' '}<button type="button" className="secondary" onClick={() => onSection('ledger')}>{t('ledger.statements.goLedger')}</button></Note>}
    {data && data.late_entries > 0 && <Note>{t('ledger.statements.late', { count: data.late_entries })}</Note>}
    {empty && <Empty title={t('ledger.statements.emptyTitle')} text={t('ledger.statements.emptyText')}
      action={<button type="button" onClick={() => onSection('ledger')}>{t('ledger.statements.goLedger')}</button>} />}
    {data && !empty && <div className="bzOverview bkGrid">
      <section className="surface bzCard" aria-labelledby="bk-bs"><h2 id="bk-bs">{t('ledger.bs.title', { day: dayText(data.balance_sheet.as_of) })}</h2>
        <p className="bzInline">{data.balance_sheet.balanced ? <Badge tone="success">{t('ledger.bs.balanced')}</Badge> : <Badge tone="dangerTone">{t('ledger.bs.notBalanced', { difference: money(data.balance_sheet.difference, base) })}</Badge>}
        {!data.balance_sheet.complete && <Badge tone="warning">{t('ledger.incomplete')}</Badge>}</p>
        <StatementTable id="bs" title={t('ledger.bs.caption')} rows={balanceRows(data.balance_sheet)} currency={base}
          compare={Object.keys(data.comparatives).length ? Object.entries(data.comparatives).map(([name, value]) => ({
            label: t(`ledger.compare.${name}` as MessageKey, { from: dayText(value.from), to: dayText(value.to) }), rows: rowMap(balanceRows(value.balance_sheet)) })) : undefined} />
      </section>
      <section className="surface bzCard" aria-labelledby="bk-jpnl"><h2 id="bk-jpnl">{t('ledger.pnl.title')}</h2>
        <StatementTable id="pnl" title={t('ledger.pnl.title')} rows={pnlRows(data.pnl)} currency={base}
          compare={Object.keys(data.comparatives).length ? pnlCompare(data, pnlRows) : undefined} />
        {data.reconciliation && <>
          <h3 className="bkSub">{t('ledger.recon.title')}</h3>
          <p className="muted bzHint">{data.reconciliation.agrees ? t('ledger.recon.agrees', { day: dayText(data.reconciliation.to) }) : t('ledger.recon.differs')}</p>
          <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkStatement"><caption className="visuallyHidden">{t('ledger.recon.title')}</caption>
            <thead><tr><th scope="col">{t('books.col.line')}</th><th scope="col" className="num">{t('ledger.recon.journal')}</th><th scope="col" className="num">{t('ledger.recon.derived')}</th>
              <th scope="col" className="num">{t('ledger.recon.difference')}</th></tr></thead>
            <tbody>{data.reconciliation.rows.map((row) => <tr key={row.key}><th scope="row">{label(`ledger.pnl.${row.key}`)}</th>
              <td className="num">{money(row.journal, base)}</td><td className="num">{money(row.derived, base)}</td>
              <td className="num"><Amount value={row.difference} currency={base} /></td></tr>)}</tbody></table></div>
        </>}
      </section>
      <section className="surface bzCard" aria-labelledby="bk-cash"><h2 id="bk-cash">{t('ledger.cash.title')}</h2>
        <p className="muted bzHint">{t('ledger.cash.help')}</p>
        {data.cash_flow.by_currency.length === 0 ? <p className="muted">{t('ledger.cash.none')}</p> : data.cash_flow.by_currency.map((item) =>
          <div key={item.currency} className="dataTableWrap" tabIndex={0} role="region" aria-label={t('ledger.cash.in', { currency: item.currency })}><table className="dataTable bkStatement">
            <caption>{t('ledger.cash.in', { currency: item.currency })}</caption>
            <tbody>
              <tr className="bk-total"><th scope="row">{t('ledger.cash.opening')}</th><td className="num"><Amount value={item.opening} currency={item.currency} /></td></tr>
              {(['operating', 'investing', 'financing'] as const).flatMap((activity) => [
                ...item[activity].lines.map((line) => <tr key={`${activity}-${line.key}`} className="bk-line"><th scope="row">{flowLabel(line.key)}</th>
                  <td className="num"><Amount value={line.amount} currency={item.currency} /></td></tr>),
                <tr key={activity} className="bk-total"><th scope="row">{t(`ledger.cash.${activity}` as MessageKey)}</th><td className="num"><Amount value={item[activity].total} currency={item.currency} /></td></tr>])}
              <tr className="bk-result"><th scope="row">{t('ledger.cash.closing')}</th><td className="num"><Amount value={item.closing} currency={item.currency} /></td></tr>
            </tbody></table>
            {!item.reconciles && <Note error>{t('ledger.cash.noReconcile')}</Note>}</div>)}
      </section>
      <section className="surface bzCard bkWide" aria-labelledby="bk-equity"><h2 id="bk-equity">{t('ledger.equity.title')}</h2>
        <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkStatement"><caption className="visuallyHidden">{t('ledger.equity.title')}</caption>
          <thead><tr><th scope="col">{t('books.col.line')}</th>{(['capital', 'drawings', 'retained_earnings', 'result', 'total'] as const).map((column) =>
            <th key={column} scope="col" className="num">{t(`ledger.equity.${column}` as MessageKey)}</th>)}</tr></thead>
          <tbody>{data.equity.rows.map((row) => <tr key={row.key} className={row.key === 'closing' || row.key === 'opening' ? 'bk-total' : 'bk-line'}>
            <th scope="row">{t(`ledger.equity.row.${row.key}` as MessageKey)}</th>
            {(['capital', 'drawings', 'retained_earnings', 'result', 'total'] as const).map((column) => <td key={column} className="num"><Amount value={row[column]} currency={base} /></td>)}</tr>)}</tbody></table></div>
        {!data.equity.balanced && <Note error>{t('ledger.equity.noReconcile')}</Note>}
      </section>
      <section className="surface bzCard bkWide" aria-labelledby="bk-tb"><h2 id="bk-tb">{t('ledger.tb.title')}</h2>
        <p className="bzInline">{data.trial_balance.balanced ? <Badge tone="success">{t('ledger.tb.balanced')}</Badge> : <Badge tone="dangerTone">{t('ledger.tb.notBalanced', { difference: money(data.trial_balance.difference, base) })}</Badge>}</p>
        <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('ledger.tb.title')}</caption>
          <thead><tr><th scope="col">{t('ledger.col.account')}</th><th scope="col" className="num bzHideMd">{t('ledger.tb.opening')}</th><th scope="col" className="num">{t('ledger.col.debit')}</th>
            <th scope="col" className="num">{t('ledger.col.credit')}</th><th scope="col" className="num">{t('ledger.tb.closing')}</th></tr></thead>
          <tbody>{data.trial_balance.rows.map((row) => <tr key={row.account_id}><th scope="row"><bdi>{row.code}</bdi> <span dir="auto">{accountName(row)}</span></th>
            <td className="num bzHideMd">{money((Number(row.opening_debit) - Number(row.opening_credit)).toFixed(2), base)}</td>
            <td className="num">{money(row.debit, base)}</td><td className="num">{money(row.credit, base)}</td><td className="num"><Amount value={closingNet(row)} currency={base} /></td></tr>)}
            <tr className="bk-result"><th scope="row">{t('ledger.tb.totals')}</th><td className="num bzHideMd" /><td className="num">{money(data.trial_balance.totals.debit, base)}</td>
              <td className="num">{money(data.trial_balance.totals.credit, base)}</td><td className="num" /></tr></tbody></table></div>
      </section>
      <section className="surface bzCard bkWide" aria-labelledby="bk-policies"><h2 id="bk-policies">{t('ledger.policies.title')}</h2>
        <ul className="bkNotes">{data.policies.map((item) => <li key={item.key} dir="auto">{item.key === 'owner_note' ? item.text : t(`ledger.policy.${item.key}` as MessageKey, policyValues(setup))}</li>)}</ul>
      </section>
    </div>}
  </>;
}

function policyValues(setup: BooksSetup): Record<string, string | number> {
  const settings = setup.settings!;
  return { basis: label(`books.basis.${settings.basis}`), currency: settings.base_currency, days: settings.rate_max_age_days,
    month: label(`books.month.${['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'][settings.fiscal_year_start_month - 1]}`) };
}

// ---- period close ------------------------------------------------------------------------------------------------------------

export function CloseSection({ onSection }: { setup: BooksSetup; onSection: (tab: string) => void }) {
  const [tick, setTick] = useState(0);
  const [load, reload] = useLoad<LedgerCloseStatus>(() => api.getClose(), [tick]);
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [reopening, setReopening] = useState<string | null>(null);
  if (load.state === 'loading') return <Note>{t('books.reading')}</Note>;
  if (load.state === 'denied') return <Denied text={load.message} />;
  if (load.state === 'error') return <Failed text={load.message} onRetry={() => void reload()} />;
  const value = load.value;
  const next = value.next;
  async function close() {
    setBusy(true); setMessage(null);
    try { const result = await api.closeThrough(next.through); setMessage({ text: t('ledger.close.done', { day: dayText(result.closed_through) }) }); setConfirming(false); setTick((n) => n + 1); }
    catch (error) { setMessage({ text: failure(error, t('ledger.close.action')), error: true }); setConfirming(false); } finally { setBusy(false); }
  }
  const checks: { key: string; count: number; go?: string }[] = [
    { key: 'to_post', count: next.to_post, go: 'ledger' }, { key: 'issues', count: next.issues, go: 'ledger' }, { key: 'drafts', count: next.drafts, go: 'bills' },
    { key: 'allocations_in_review', count: next.allocations_in_review, go: 'allocations' },
  ];
  return <>
    <p className="muted bzHint">{t('ledger.close.help')}</p>
    {message && <Note error={message.error}>{message.text}</Note>}
    <div className="bzOverview bkGrid">
      <section className="surface bzCard" aria-labelledby="bk-close-next"><h2 id="bk-close-next">{t('ledger.close.nextTitle', { day: dayText(next.through) })}</h2>
        <p className="muted">{value.closed_through ? t('ledger.closedThrough', { day: dayText(value.closed_through) }) : t('ledger.close.never')}</p>
        {next.year_end && <Note>{t('ledger.close.yearEnd')}</Note>}
        {!next.ended && <Note>{t('ledger.close.notEnded')}</Note>}
        <ul className="bkList" aria-label={t('ledger.close.checklist')}>{checks.map((item) => <li key={item.key}>
          <span>{t(`ledger.close.check.${item.key}` as MessageKey, { count: item.count })}</span>
          {item.count === 0 ? <Badge tone="success">{t('ledger.close.ok')}</Badge>
            : <button type="button" className="secondary" onClick={() => onSection(item.go!)}>{t('ledger.close.fix')}</button>}</li>)}</ul>
        {value.can_close ? <div className="bzInline"><button type="button" disabled={!next.ready || busy} onClick={() => setConfirming(true)}><Lock aria-hidden="true" />{t('ledger.close.action')}</button></div>
          : <p className="muted">{t('ledger.accountantsOnly')}</p>}
      </section>
      <section className="surface bzCard" aria-labelledby="bk-close-history"><h2 id="bk-close-history">{t('ledger.close.history')}</h2>
        {value.history.length === 0 ? <Empty title={t('ledger.close.emptyTitle')} text={t('ledger.close.emptyText')} /> :
          <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('ledger.close.history')}</caption>
            <thead><tr><th scope="col">{t('ledger.close.period')}</th><th scope="col">{t('books.col.state')}</th>{value.can_reopen && <th scope="col"><span className="visuallyHidden">{t('books.col.actions')}</span></th>}</tr></thead>
            <tbody>{value.history.map((row) => <tr key={row.id}><th scope="row">{dayText(row.period_start)} - {dayText(row.period_end)}
              <small>{row.kind === 'year' ? t('ledger.close.yearRow') : t('ledger.close.monthRow')}</small>
              {row.status === 'reopened' && <small dir="auto">{t('ledger.close.reopenedWhy', { reason: row.reopen_reason, day: dayText(row.reopened_at?.slice(0, 10)) })}</small>}</th>
              <td>{row.status === 'closed' ? <Badge tone="success">{t('ledger.close.closed')}</Badge> : <Badge tone="neutral">{t('ledger.close.reopened')}</Badge>}</td>
              {value.can_reopen && <td>{row.status === 'closed' && <button type="button" className="secondary" onClick={() => setReopening(row.period_start)}
                aria-label={t('ledger.close.reopenLabel', { day: dayText(row.period_end) })}><Unlock aria-hidden="true" />{t('ledger.close.reopen')}</button>}</td>}</tr>)}</tbody></table></div>}
        {!value.can_reopen && value.history.some((row) => row.status === 'closed') && <p className="muted">{t('ledger.close.ownersReopen')}</p>}
      </section>
    </div>
    {confirming && <ConfirmDialog title={t('ledger.close.confirmTitle', { day: dayText(next.through) })} confirmLabel={t('ledger.close.action')} busy={busy}
      onConfirm={() => void close()} onCancel={() => setConfirming(false)}><p>{t('ledger.close.confirmText')}</p></ConfirmDialog>}
    {reopening && <ReasonDialog title={t('ledger.close.reopenTitle', { day: dayText(reopening) })} label={t('books.reason')} confirm={t('ledger.close.reopen')}
      onConfirm={async (reason) => { await api.reopen(reopening, reason); setReopening(null); setMessage({ text: t('ledger.close.reopenedDone') }); setTick((n) => n + 1); }}
      onCancel={() => setReopening(null)} />}
  </>;
}

// ---- accountants (Books, Settings) ----------------------------------------------------------------------------------------------

export function AccountantsCard({ setup }: { setup: BooksSetup }) {
  const [tick, setTick] = useState(0);
  const [load, reload] = useLoad(() => api.listAccountants(), [tick]);
  const manage = setup.permissions.manage;
  const [members, setMembers] = useState<{ user_id: string; role: string; email?: string | null }[] | null>(null);
  const [chosen, setChosen] = useState(''), [busy, setBusy] = useState(false), [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const field = useId();
  useEffect(() => {
    if (!manage) return;
    api.activeMembers().then((rows) => setMembers(rows.filter((row) => row.role === 'owner' || row.role === 'member'))).catch(() => setMembers([]));
  }, [manage, tick]);
  async function add(event: FormEvent) {
    event.preventDefault();
    if (!chosen) { setMessage({ text: t('ledger.accountants.choose'), error: true }); return; }
    setBusy(true); setMessage(null);
    try { await api.addAccountant(chosen); setChosen(''); setTick((n) => n + 1); } catch (error) { setMessage({ text: failure(error, t('ledger.accountants.add')), error: true }); } finally { setBusy(false); }
  }
  async function remove(userId: string) {
    setMessage(null);
    try { await api.removeAccountant(userId); setTick((n) => n + 1); } catch (error) { setMessage({ text: failure(error, t('ledger.accountants.remove')), error: true }); }
  }
  const current = load.state === 'ready' ? load.value.items : [];
  const options = (members ?? []).filter((row) => !current.some((item) => item.user_id === row.user_id));
  return <section className="surface bzCard" aria-labelledby="bk-accountants"><h2 id="bk-accountants">{t('ledger.accountants.title')}</h2>
    <p className="muted bzHint">{t('ledger.accountants.help')}</p>
    {load.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {(load.state === 'error' || load.state === 'denied') && <Failed text={load.message} onRetry={() => void reload()} />}
    {load.state === 'ready' && (current.length === 0 ? <p className="muted">{t('ledger.accountants.none')}</p>
      : <ul className="bkList">{current.map((item) => <li key={item.user_id}><span><bdi>{item.user_id}</bdi><small>{t('ledger.accountants.since', { day: dayText(item.added_at.slice(0, 10)) })}</small></span>
        {manage && <button type="button" className="secondary" onClick={() => void remove(item.user_id)} aria-label={t('ledger.accountants.removeLabel', { user: item.user_id })}>{t('ledger.accountants.remove')}</button>}</li>)}</ul>)}
    {manage ? <form className="bzInline" onSubmit={(event) => void add(event)}>
      <label className="field" htmlFor={field}><span>{t('ledger.accountants.member')}</span><select id={field} value={chosen} onChange={(event) => setChosen(event.target.value)}>
        <option value="">{members === null ? t('books.reading') : t('ledger.accountants.choose')}</option>
        {options.map((row) => <option key={row.user_id} value={row.user_id}>{row.email ? `${row.email} (${row.user_id})` : row.user_id}</option>)}</select></label>
      <button type="submit" disabled={busy || !chosen}><Plus aria-hidden="true" />{t('ledger.accountants.add')}</button>
    </form> : <p className="muted">{t('books.ownersOnly')}</p>}
    {message && <Note error={message.error}>{message.text}</Note>}
  </section>;
}

export { lastMonthEnd };
