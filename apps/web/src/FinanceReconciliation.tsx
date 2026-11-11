// Finance, Reconciliation (docs/finance.md#reconciliation-sessions): per bank or card account and statement period,
// the statement's balances against the book's, the accounting equation, the exceptions and the owner's resolutions,
// plus connection health (the last statement, SMS and Apple Pay record, missing months and the next step). Every
// figure comes from the API. Nothing here moves money or forces a balance: a difference nobody explained keeps the
// session unresolved, and a correcting entry is posted only after the owner approves the legs they were shown.
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { AlertTriangle, ArrowDownUp, BadgeCheck, CalendarClock, Download, FileUp, History, Link2, Pencil, Plus, RotateCcw, Scale, Trash2, Undo2 } from 'lucide-react';
import type {
  FinanceAccount, FinanceImport, FinanceReconCorrectionPreview, FinanceReconDetail, FinanceReconException, FinanceReconHealth,
  FinanceReconHealthPage, FinanceReconPage, FinanceReconQueueItem, FinanceReconQueuePage, FinanceReconSummary,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as rc from './lib/financeReconciliation';
import * as rapi from './lib/financeReconciliationApi';
import { formatAmount, isolate, monthLabel, parseAmount, type FinanceTab } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import './financePersonal.css';
import './financeRecon.css';

const SESSIONS_PAGE = 10;
const day = (value: string | null) => (value ? formatDateTime(`${value.slice(0, 10)}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '-');
const amount = (value: string | null, currency: string) => (value === null ? '-' : isolate(formatAmount(value, currency)));
const SEVERITIES: readonly rc.SeverityFilter[] = ['all', 'blocking', 'warning', 'info'];
const SEVERITY_TONE = { blocking: 'bad', warning: 'warn', info: 'muted' } as const;

export function ReconciliationPage({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [health, setHealth] = useState<Load<FinanceReconHealthPage>>({ kind: 'loading' });
  const [sessions, setSessions] = useState<Load<FinanceReconPage>>({ kind: 'loading' });
  const [offset, setOffset] = useState(0);
  // A link from the suggestion queue opens its session (`#finance-reconciliation?session=<id>`).
  const [selected, setSelected] = useState<string | null>(() => (typeof location === 'undefined' ? null : rc.sessionFromHash(location.hash)));
  const [creating, setCreating] = useState<{ accountId?: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const loadHealth = useCallback(() => {
    setHealth((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    rapi.reconciliationHealth().then((v) => setHealth({ kind: 'ready', value: v })).catch((e) => setHealth(failed(e)));
  }, []);
  const loadSessions = useCallback(() => {
    setSessions((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    rapi.listReconciliations(SESSIONS_PAGE, offset).then((v) => setSessions({ kind: 'ready', value: v })).catch((e) => setSessions(failed(e)));
  }, [offset]);
  useEffect(() => { loadHealth(); }, [loadHealth]);
  useEffect(() => { loadSessions(); }, [loadSessions]);
  const refresh = () => { loadHealth(); loadSessions(); };
  const denied = health.kind === 'denied' || sessions.kind === 'denied';
  const head = <FinancePageHead title={t('finance.tab.reconciliation')} subtitle={t('frec.subtitle')} icon={<Scale />}
    actions={!denied ? <button type="button" onClick={() => setCreating({})}><Plus aria-hidden="true" />{t('frec.new')}</button> : undefined} />;
  if (denied) return <div className="frecPage">{head}<Denied /></div>;
  const ready = sessions.kind === 'ready' ? sessions.value : null;
  const open = ready?.items.filter((s) => s.status === 'open').length ?? 0;
  const changed = ready?.items.filter((s) => s.changed).length ?? 0;
  const missing = health.kind === 'ready' ? health.value.accounts.reduce((n, a) => n + a.missing_months.length, 0) : 0;
  return <div className="frecPage fpmPage">
    {head}
    {notice && <Notice>{notice}</Notice>}
    <KpiRow label={t('frec.kpis')}>
      <KpiCard icon={<Scale />} label={t('frec.kpi.sessions')} value={ready ? ready.total : '-'} hint={t('frec.kpi.sessionsHint')} />
      <KpiCard icon={<AlertTriangle />} label={t('frec.kpi.open')} value={ready ? open : '-'} tone={open ? 'warn' : ''} hint={t('frec.kpi.openHint')} />
      <KpiCard icon={<History />} label={t('frec.kpi.changed')} value={ready ? changed : '-'} tone={changed ? 'bad' : ''} hint={t('frec.kpi.changedHint')} />
      <KpiCard icon={<CalendarClock />} label={t('frec.kpi.missing')} value={health.kind === 'ready' ? missing : '-'} tone={missing ? 'warn' : ''} hint={t('frec.kpi.missingHint')} />
    </KpiRow>
    {selected
      ? <SessionDetail key={selected} id={selected} onBack={() => { setSelected(null); if (rc.sessionFromHash(location.hash)) history.replaceState(null, '', '#finance-reconciliation'); refresh(); }}
        onDeleted={() => { setSelected(null); if (rc.sessionFromHash(location.hash)) history.replaceState(null, '', '#finance-reconciliation'); setNotice(t('frec.done.delete')); refresh(); }} onChanged={refresh} onOpen={onOpen} />
      : <>
        <QueueCard key={`queue-${open}-${changed}`} onPick={setSelected} onOpen={onOpen} />
        <SessionsCard state={sessions} retry={loadSessions} offset={offset} onOffset={setOffset} onPick={setSelected} onNew={() => setCreating({})} />
        <HealthCard state={health} retry={loadHealth} onImport={() => onOpen('import')} onReconcile={(accountId) => setCreating({ accountId })} onPick={setSelected} />
      </>}
    {creating && <Modal title={t('frec.new')} className="formDialog fpmDialog" onClose={() => setCreating(null)}>
      <NewSessionForm accountId={creating.accountId} onDone={(made) => {
        setCreating(null);
        if (made) { setNotice(t('frec.created', { account: made.account_name })); setSelected(made.id); refresh(); }
      }} />
    </Modal>}
  </div>;
}

const QUEUE_COLUMN: Record<rc.QueueSort, MessageKey> = { severity: 'frec.queue.col.severity', account: 'frec.queue.col.account', period: 'frec.queue.col.period', kind: 'frec.queue.col.kind', posted: 'frec.queue.col.posted', amount: 'frec.queue.col.amount' };

/** Every open exception across the owner's accounts (GET /reconciliations/exceptions): sorted on the server, paged, exported as CSV, each with its next step. */
function QueueCard({ onPick, onOpen }: { onPick: (id: string) => void; onOpen: (tab: FinanceTab) => void }) {
  const [state, setState] = useState<Load<FinanceReconQueuePage>>({ kind: 'loading' });
  const [order, setOrder] = useState<rc.QueueOrder>(rc.DEFAULT_QUEUE_ORDER);
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false), [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    rapi.reconciliationExceptions(rc.QUEUE_PAGE, offset, order).then((v) => setState({ kind: 'ready', value: v })).catch((e) => setState(failed(e)));
  }, [offset, order]);
  useEffect(() => { load(); }, [load]);
  const exportCsv = async () => {
    setExporting(true); setNotice(null);
    try { setNotice({ text: t('frec.queue.exported', { name: await rapi.exportExceptions(order) }) }); } catch (e) { setNotice({ text: describeError(e), error: true }); } finally { setExporting(false); }
  };
  const sortBy = (sort: rc.QueueSort) => { setOrder((current) => rc.nextQueueOrder(current, sort)); setOffset(0); };
  const head = (sort: rc.QueueSort, className?: string) => <th scope="col" className={className} aria-sort={order.sort === sort ? (order.desc ? 'descending' : 'ascending') : 'none'}>
    <button type="button" className="finSortButton" onClick={() => sortBy(sort)} aria-label={t('frec.queue.sortBy', { column: t(QUEUE_COLUMN[sort]) })}>{t(QUEUE_COLUMN[sort])}<ArrowDownUp aria-hidden="true" /></button></th>;
  const page = state.kind === 'ready' ? state.value : null;
  return <FinCard title={t('frec.queue.title')} icon={<AlertTriangle />} subtitle={t('frec.queue.sub')}
    actions={page && page.total > 0 ? <button type="button" className="secondary" disabled={exporting} title={t('frec.queue.exportHelp')} onClick={() => void exportCsv()}>
      <Download aria-hidden="true" />{exporting ? t('frec.queue.exporting') : t('frec.queue.export')}</button> : undefined}>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('frec.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('frec.error', { message: state.message })} retry={load} />}
    {page && page.total === 0 && <Empty icon={<BadgeCheck aria-hidden="true" />} title={t('frec.queue.none.title')} text={t('frec.queue.none.text')} />}
    {page && page.total > 0 && <>
      <p className="muted frecQueueCount" aria-live="polite">{t('frec.queue.count', { count: page.total })}. {t('frec.queue.sortedBy', { column: t(QUEUE_COLUMN[order.sort]), direction: t(order.desc ? 'frec.queue.descending' : 'frec.queue.ascending') })}</p>
      <div className="dataTableWrap"><table className="dataTable frecQueue">
        <caption className="visuallyHidden">{t('frec.queue.title')}</caption>
        <thead><tr>{head('account')}{head('period', 'frecHideSm')}{head('severity')}{head('kind')}{head('posted', 'frecHideSm')}{head('amount', 'num')}<th scope="col">{t('frec.queue.col.action')}</th></tr></thead>
        <tbody>{page.items.map((item) => <QueueRow key={`${item.reconciliation_id ?? item.account_id}:${item.exception.id}`} item={item} onPick={onPick} onOpen={onOpen} />)}</tbody>
      </table></div>
      {page.total > rc.QUEUE_PAGE && <div className="actions fpmPager">
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - rc.QUEUE_PAGE))}>{t('fpm.previous')}</button>
        <span>{t('fpm.page', { page: Math.floor(offset / rc.QUEUE_PAGE) + 1, pages: Math.ceil(page.total / rc.QUEUE_PAGE) })}</span>
        <button type="button" className="secondary" disabled={offset + rc.QUEUE_PAGE >= page.total} onClick={() => setOffset(offset + rc.QUEUE_PAGE)}>{t('fpm.next')}</button>
      </div>}
    </>}
  </FinCard>;
}

function QueueRow({ item, onPick, onOpen }: { item: FinanceReconQueueItem; onPick: (id: string) => void; onOpen: (tab: FinanceTab) => void }) {
  const e = item.exception;
  const action = rc.queueAction(item);
  const period = item.period_start && item.period_end ? t('frec.period', { start: day(item.period_start), end: day(item.period_end) }) : t('frec.queue.noPeriod');
  return <tr className={`frecQueueRow ${e.severity}`}>
    <th scope="row" dir="auto">{item.account_name || item.account_id}</th>
    <td className="frecHideSm">{period}</td>
    <td><StatusChip tone={SEVERITY_TONE[e.severity]}>{t(`frec.severity.${e.severity}` as MessageKey)}</StatusChip></td>
    <td><strong>{t(`frec.kind.${e.kind}` as MessageKey)}</strong>{e.description && <><br /><span className="muted" dir="auto">{e.description}</span></>}{e.month && <><br /><span className="muted">{monthLabel(e.month)}</span></>}</td>
    <td className="frecHideSm">{e.posted_on ? day(e.posted_on) : '-'}</td>
    <td className="num">{e.amount === null ? '-' : <span className="frecAmount"><Money value={e.amount} currency={e.details.currency ? String(e.details.currency) : ''} signed /></span>}</td>
    <td><div className="fpmRowActions">
      {action === 'session' && <button type="button" className="secondary" onClick={() => onPick(item.reconciliation_id as string)}>{t('frec.queue.openSession')}</button>}
      {action === 'import' && <button type="button" className="secondary" onClick={() => onOpen('import')}><FileUp aria-hidden="true" />{t('frec.action.import')}</button>}
      {action === 'transactions' && <button type="button" className="secondary" onClick={() => onOpen('transactions')}>{t('frec.action.open')}</button>}
    </div></td>
  </tr>;
}

function SessionsCard({ state, retry, offset, onOffset, onPick, onNew }: {
  state: Load<FinanceReconPage>; retry: () => void; offset: number; onOffset: (next: number) => void; onPick: (id: string) => void; onNew: () => void;
}) {
  if (state.kind === 'loading') return <Notice busy>{t('frec.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('frec.error', { message: state.message })} retry={retry} />;
  const page = state.value;
  return <FinCard title={t('frec.sessions.title')} icon={<Scale />} subtitle={t('frec.sessions.sub')}>
    {page.total === 0
      ? <Empty icon={<Scale aria-hidden="true" />} title={t('frec.empty.title')} text={t('frec.empty.text')}
        action={<button type="button" onClick={onNew}><Plus aria-hidden="true" />{t('frec.new')}</button>} />
      : <ul className="frecSessions">{page.items.map((s) => <SessionRow key={s.id} s={s} onPick={onPick} />)}</ul>}
    {page.total > SESSIONS_PAGE && <div className="actions fpmPager">
      <button type="button" className="secondary" disabled={offset === 0} onClick={() => onOffset(Math.max(0, offset - SESSIONS_PAGE))}>{t('fpm.previous')}</button>
      <span>{t('fpm.page', { page: Math.floor(offset / SESSIONS_PAGE) + 1, pages: Math.ceil(page.total / SESSIONS_PAGE) })}</span>
      <button type="button" className="secondary" disabled={offset + SESSIONS_PAGE >= page.total} onClick={() => onOffset(offset + SESSIONS_PAGE)}>{t('fpm.next')}</button>
    </div>}
  </FinCard>;
}

function SessionRow({ s, onPick }: { s: FinanceReconSummary; onPick: (id: string) => void }) {
  const status = rc.sessionStatus(s);
  return <li className="frecSession">
    <div className="frecSessionMain">
      <strong dir="auto">{s.account_name}</strong>
      <span className="muted">{t('frec.period', { start: day(s.period_start), end: day(s.period_end) })}</span>
    </div>
    <div className="frecSessionFacts">
      <StatusChip tone={rc.sessionTone(s)}>{t(`frec.status.${status}` as MessageKey)}</StatusChip>
      {!rc.isZero(s.unexplained) && <span>{t('frec.unexplainedShort', { amount: amount(s.unexplained, s.currency) })}</span>}
      {s.exceptions > 0 && <span className="muted">{t('frec.exceptionsCount', { count: s.exceptions })}</span>}
    </div>
    <button type="button" className="secondary" onClick={() => onPick(s.id)} aria-label={t('frec.openFor', { account: s.account_name, end: day(s.period_end) })}>{t('frec.open')}</button>
  </li>;
}

function HealthCard({ state, retry, onImport, onReconcile, onPick }: {
  state: Load<FinanceReconHealthPage>; retry: () => void; onImport: () => void; onReconcile: (accountId: string) => void; onPick: (id: string) => void;
}) {
  if (state.kind === 'loading') return <Notice busy>{t('frec.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('frec.error', { message: state.message })} retry={retry} />;
  const accounts = state.value.accounts.filter((a) => a.kind === 'bank' || a.kind === 'card');
  return <FinCard title={t('frec.health.title')} icon={<Link2 />} subtitle={t('frec.health.sub')}>
    {accounts.length === 0
      ? <Empty icon={<Link2 aria-hidden="true" />} title={t('frec.health.empty.title')} text={t('frec.health.empty.text')}
        action={<button type="button" onClick={onImport}><FileUp aria-hidden="true" />{t('frec.import')}</button>} />
      : <ul className="frecHealth">{accounts.map((a) => <HealthRow key={a.account_id} h={a} onImport={onImport} onReconcile={onReconcile} onPick={onPick} />)}</ul>}
  </FinCard>;
}

function HealthRow({ h, onImport, onReconcile, onPick }: { h: FinanceReconHealth; onImport: () => void; onReconcile: (accountId: string) => void; onPick: (id: string) => void }) {
  const step = rc.nextStep(h);
  const label = t(step.key as MessageKey, { month: h.next_month ? monthLabel(h.next_month) : '' });
  return <li className="frecHealthRow">
    <div className="frecHealthHead"><strong dir="auto">{h.account_name}</strong><StatusChip tone="muted">{t('frec.health.noFeed')}</StatusChip></div>
    <dl className="frecFacts">
      <div><dt>{t('frec.health.statement')}</dt><dd>{day(h.last_statement_end)}</dd></div>
      <div><dt>{t('frec.health.import')}</dt><dd>{h.last_import_at ? formatDateTime(h.last_import_at, { day: 'numeric', month: 'short', year: 'numeric' }) : '-'}</dd></div>
      <div><dt>{t('frec.health.sms')}</dt><dd>{day(h.last_sms_on)}</dd></div>
      <div><dt>{t('frec.health.applePay')}</dt><dd>{day(h.last_apple_pay_on)}</dd></div>
      <div><dt>{t('frec.health.through')}</dt><dd>{day(h.reconciled_through)}</dd></div>
    </dl>
    {h.missing_months.length > 0 && <p className="frecFlag" role="note"><AlertTriangle aria-hidden="true" />
      {t('frec.health.missing', { count: h.missing_months.length, months: h.missing_months.slice(0, 4).map((m) => monthLabel(m)).join(', ') })}</p>}
    <div className="frecNext"><span>{label}</span>
      {step.action === 'import' && <button type="button" className="secondary" onClick={onImport}><FileUp aria-hidden="true" />{t('frec.import')}</button>}
      {step.action === 'reconcile' && <button type="button" className="secondary" onClick={() => onReconcile(h.account_id)}><Scale aria-hidden="true" />{t('frec.reconcile')}</button>}
      {step.action === 'open' && step.reconciliationId && <button type="button" className="secondary" onClick={() => onPick(step.reconciliationId as string)}>{t('frec.open')}</button>}
    </div>
  </li>;
}

function SessionDetail({ id, onBack, onDeleted, onChanged, onOpen }: { id: string; onBack: () => void; onDeleted: () => void; onChanged: () => void; onOpen: (tab: FinanceTab) => void }) {
  const [state, setState] = useState<Load<FinanceReconDetail>>({ kind: 'loading' });
  const [severity, setSeverity] = useState<rc.SeverityFilter>('all');
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const [matching, setMatching] = useState<FinanceReconException | null>(null);
  const [correcting, setCorrecting] = useState<FinanceReconCorrectionPreview | null>(null);
  const [reopening, setReopening] = useState(false), [editing, setEditing] = useState(false), [deleting, setDeleting] = useState(false);
  const load = useCallback(() => {
    setState((c) => (c.kind === 'ready' ? c : { kind: 'loading' }));
    rapi.getReconciliation(id, { severity, limit: rc.EXCEPTIONS_PAGE, offset }).then((v) => setState({ kind: 'ready', value: v })).catch((e) => setState(failed(e)));
  }, [id, severity, offset]);
  useEffect(() => { load(); }, [load]);
  const run = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true); setMessage(null);
    try { await work(); setMessage({ text: done }); load(); onChanged(); } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  if (state.kind === 'loading') return <Notice busy>{t('frec.loading')}</Notice>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <LoadError message={t('frec.error', { message: state.message })} retry={load} />;
  const d = state.value;
  const f = d.figures;
  const c = d.currency;
  const status = rc.sessionStatus(d);
  const act = (e: FinanceReconException, action: string) => {
    if (action === 'attach' || action === 'pending') void run(() => rapi.resolve(d.id, { kind: action, transaction_id: e.transaction_id }), t(`frec.done.${action}` as MessageKey));
    else if (action === 'accept_reversal') void run(() => rapi.resolve(d.id, { kind: 'reversal', transaction_id: e.transaction_id, other_transaction_id: e.other_transaction_id }), t('frec.done.reversal'));
    else if (action === 'match') setMatching(e);
    else if (action === 'correct' && e.statement_ref) {
      setBusy(true); setMessage(null);
      rapi.previewCorrection(d.id, e.statement_ref).then(setCorrecting).catch((err) => setMessage({ text: describeError(err), error: true })).finally(() => setBusy(false));
    } else if (action === 'open') onOpen('transactions');
    else if (action === 'import') onOpen('import');
  };
  return <div className="frecDetail">
    <div className="frecDetailHead">
      <button type="button" className="secondary" onClick={onBack}>{t('frec.back')}</button>
      <h2 dir="auto">{d.account_name} <span className="muted">{t('frec.period', { start: day(d.period_start), end: day(d.period_end) })}</span></h2>
      <StatusChip tone={rc.sessionTone(d)}>{t(`frec.status.${status}` as MessageKey)}</StatusChip>
      {d.status === 'open' && <div className="fpmRowActions frecDetailActions">
        <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('frec.edit')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setDeleting(true)}><Trash2 aria-hidden="true" />{t('frec.delete')}</button>
      </div>}
    </div>
    {d.status === 'completed' && <p className="muted frecKept">{t('frec.completedKept')}</p>}
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {d.changed && <FinBanner tone="warn" icon={<History />} title={t('frec.changed.title')}
      action={<button type="button" onClick={() => setReopening(true)}><RotateCcw aria-hidden="true" />{t('frec.reopen')}</button>}>{t('frec.changed.text')}</FinBanner>}
    <div className="frecGrid">
      <FinCard title={t('frec.equation.title')} icon={<Scale />} subtitle={d.source === 'import' ? t('frec.source.import') : t('frec.source.typed')}>
        <dl className="frecEquation">
          <div><dt>{t('frec.eq.bookOpening')}</dt><dd><Money value={f.book_opening} currency={c} /></dd></div>
          <div><dt>{t('frec.eq.deposits')}</dt><dd><Money value={f.deposits} currency={c} signed /></dd></div>
          <div><dt>{t('frec.eq.payments')}</dt><dd><Money value={f.payments} currency={c} signed /></dd></div>
          <div className="frecTotal"><dt>{t('frec.eq.bookClosing')}</dt><dd><Money value={f.book_closing} currency={c} /></dd></div>
          {!rc.isZero(f.pending_total) && <div><dt>{t('frec.eq.pending')}</dt><dd><Money value={f.pending_total} currency={c} signed /></dd></div>}
          {!rc.isZero(f.pending_total) && <div><dt>{t('frec.eq.adjusted')}</dt><dd><Money value={f.adjusted_book_closing} currency={c} /></dd></div>}
          <div><dt>{t('frec.eq.statementOpening')}</dt><dd>{f.statement_opening === null ? '-' : <Money value={f.statement_opening} currency={c} />}</dd></div>
          <div><dt>{t('frec.eq.statementClosing')}</dt><dd><Money value={f.statement_closing} currency={c} /></dd></div>
          <div className={`frecTotal ${rc.isZero(f.unexplained) ? 'ok' : 'bad'}`}><dt>{t('frec.eq.unexplained')}</dt><dd><Money value={f.unexplained} currency={c} signed /></dd></div>
        </dl>
        {f.opening_difference !== null && !rc.isZero(f.opening_difference) && <p className="muted">{t('frec.eq.openingDiffers', { amount: amount(f.opening_difference, c) })}</p>}
        {d.card && <dl className="frecFacts frecCard" aria-label={t('frec.card.title')}>
          <div><dt>{t('frec.card.due')}</dt><dd>{amount(d.card.amount_due, c)}</dd></div>
          <div><dt>{t('frec.card.owed')}</dt><dd>{amount(d.card.owed_in_ledger, c)}</dd></div>
          <div><dt>{t('frec.card.difference')}</dt><dd>{amount(d.card.due_difference, c)}</dd></div>
          <div><dt>{t('frec.card.minimum')}</dt><dd>{amount(d.card.minimum_due, c)}</dd></div>
          <div><dt>{t('frec.card.paid')}</dt><dd>{amount(d.card.paid_since, c)}</dd></div>
          <div><dt>{t('frec.card.dueOn')}</dt><dd>{day(d.card.due_on)}</dd></div>
        </dl>}
        {d.status === 'open' && <div className="frecComplete">
          {d.blockers.length > 0 && <p className="frecFlag" role="note"><AlertTriangle aria-hidden="true" />{d.blockers.map((b) => t(`frec.blocker.${b}` as MessageKey, { amount: amount(f.unexplained, c) })).join(' ')}</p>}
          <button type="button" disabled={busy || !d.can_complete} aria-describedby={d.can_complete ? undefined : 'frec-why'}
            onClick={() => void run(() => rapi.completeReconciliation(d.id, d.version), t('frec.done.complete'))}><BadgeCheck aria-hidden="true" />{t('frec.complete')}</button>
          {!d.can_complete && <span id="frec-why" className="muted">{t('frec.completeWhy')}</span>}
        </div>}
        {d.status === 'completed' && !d.changed && <div className="frecComplete">
          <p>{t('frec.completedBy', { when: d.completed_at ? formatDateTime(d.completed_at, { day: 'numeric', month: 'short', year: 'numeric' }) : '-' })}</p>
          <button type="button" className="secondary" onClick={() => setReopening(true)}><RotateCcw aria-hidden="true" />{t('frec.reopen')}</button>
        </div>}
      </FinCard>
      <FinCard title={t('frec.history.title')} icon={<History />}>
        {d.history.length === 0 ? <p className="muted">{t('frec.history.none')}</p>
          : <ul className="finMoveList">{d.history.map((h, i) => <li key={`${h.at}-${i}`}><div>
            <span>{t(`frec.history.${h.action}` as MessageKey)}</span><small>{formatDateTime(h.at, { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' })}</small>
            {h.reason && <small dir="auto">{h.reason}</small>}</div></li>)}</ul>}
        {d.resolutions.length > 0 && <>
          <h3 className="frecSubhead">{t('frec.resolutions.title')}</h3>
          <ul className="finMoveList">{d.resolutions.map((r) => <li key={r.id}><div><span>{t(`frec.resolution.${r.kind}` as MessageKey)}</span>
            <small>{formatDateTime(r.created_at, { day: 'numeric', month: 'short' })}</small></div>
            {d.status === 'open' && r.kind !== 'correction' && <button type="button" className="secondary" disabled={busy}
              onClick={() => void run(() => rapi.undoResolution(d.id, r.id), t('frec.done.undo'))}><Undo2 aria-hidden="true" />{t('frec.undo')}</button>}</li>)}</ul>
        </>}
      </FinCard>
    </div>
    <FinCard title={t('frec.exceptions.title')} icon={<AlertTriangle />} subtitle={t('frec.exceptions.sub')}>
      <FinanceTabs idPrefix="frec" label={t('frec.exceptions.filter')} value={severity} onChange={(next) => { setSeverity(next as rc.SeverityFilter); setOffset(0); }}
        items={SEVERITIES.map((s) => ({ id: s, label: t(`frec.severity.${s}` as MessageKey), badge: s === 'all' ? undefined : d.counts[s] ?? 0 }))} />
      <div {...tabPanel('frec', severity)}>
        {d.total === 0 ? <p className="muted frecNone">{t(severity === 'all' ? 'frec.exceptions.none' : 'frec.exceptions.noneHere')}</p>
          : <ul className="frecExceptions">{d.items.map((e) => <ExceptionRow key={e.id} e={e} currency={c} open={d.status === 'open'} busy={busy} onAct={act} />)}</ul>}
        {d.total > rc.EXCEPTIONS_PAGE && <div className="actions fpmPager">
          <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - rc.EXCEPTIONS_PAGE))}>{t('fpm.previous')}</button>
          <span>{t('fpm.page', { page: Math.floor(offset / rc.EXCEPTIONS_PAGE) + 1, pages: Math.ceil(d.total / rc.EXCEPTIONS_PAGE) })}</span>
          <button type="button" className="secondary" disabled={offset + rc.EXCEPTIONS_PAGE >= d.total} onClick={() => setOffset(offset + rc.EXCEPTIONS_PAGE)}>{t('fpm.next')}</button>
        </div>}
      </div>
    </FinCard>
    {matching && <Modal title={t('frec.match.title')} className="formDialog fpmDialog" onClose={() => setMatching(null)}>
      <MatchForm rec={d} row={matching} onDone={(text) => { setMatching(null); if (text) { setMessage({ text }); load(); onChanged(); } }} />
    </Modal>}
    {correcting && <ConfirmDialog title={t('frec.correct.title')} confirmLabel={t('frec.correct.post')} busy={busy}
      onCancel={() => setCorrecting(null)} onConfirm={() => { const shown = correcting; setCorrecting(null); void run(() => rapi.postCorrection(d.id, shown), t('frec.done.correct')); }}>
      <p>{t('frec.correct.text')}</p>
      <p><span dir="auto">{correcting.description}</span> <span className="muted">{day(correcting.posted_on)}</span></p>
      <table className="frecLegs"><caption className="visuallyHidden">{t('frec.correct.legs')}</caption>
        <thead><tr><th scope="col">{t('frec.correct.account')}</th><th scope="col">{t('frec.correct.amount')}</th></tr></thead>
        <tbody>{correcting.legs.map((leg) => <tr key={leg.ledger_account}><td dir="ltr">{leg.ledger_account}</td><td><Money value={leg.amount} currency={leg.currency} signed /></td></tr>)}</tbody>
      </table>
    </ConfirmDialog>}
    {editing && <Modal title={t('frec.edit')} className="formDialog fpmDialog" onClose={() => setEditing(false)}>
      <EditSessionForm rec={d} onCancel={() => setEditing(false)} onDone={(text) => { setEditing(false); setMessage({ text }); load(); onChanged(); }} />
    </Modal>}
    {deleting && <ConfirmDialog title={t('frec.delete.title')} confirmLabel={t('frec.delete')} danger busy={busy} onCancel={() => setDeleting(false)}
      onConfirm={() => {
        setBusy(true); setMessage(null);
        rapi.deleteReconciliation(d.id).then(() => { setDeleting(false); onDeleted(); })
          .catch((e) => { setDeleting(false); setMessage({ text: describeError(e), error: true }); })
          .finally(() => setBusy(false));
      }}>
      <p>{t('frec.delete.text', { account: d.account_name, start: day(d.period_start), end: day(d.period_end) })}</p>
    </ConfirmDialog>}
    {reopening && <Modal title={t('frec.reopen')} className="formDialog" onClose={() => setReopening(false)}>
      <ReopenForm onCancel={() => setReopening(false)} onSubmit={(reason) => { setReopening(false); void run(() => rapi.reopenReconciliation(d.id, d.version, reason), t('frec.done.reopen')); }} />
    </Modal>}
  </div>;
}

function ExceptionRow({ e, currency, open, busy, onAct }: { e: FinanceReconException; currency: string; open: boolean; busy: boolean; onAct: (e: FinanceReconException, action: string) => void }) {
  const actions = open ? e.actions : e.actions.filter((a) => a === 'open' || a === 'import');
  return <li className={`frecException ${e.severity}`}>
    <div className="frecExceptionMain">
      <div className="frecExceptionHead"><StatusChip tone={SEVERITY_TONE[e.severity]}>{t(`frec.severity.${e.severity}` as MessageKey)}</StatusChip>
        <strong>{t(`frec.kind.${e.kind}` as MessageKey)}</strong></div>
      <p className="frecExceptionText">
        {e.month ? monthLabel(e.month) : <>{e.posted_on && <span className="muted">{day(e.posted_on)} </span>}{e.description && <span dir="auto">{e.description}</span>}</>}
        {e.reason && rc.REASONS.has(e.reason) && <span className="muted"> {t(`frec.reason.${e.reason}` as MessageKey, { days: e.age_days ?? 0, count: e.age_days ?? 0 })}</span>}
        {e.reason && !rc.REASONS.has(e.reason) && <span className="muted" dir="ltr"> {e.reason}</span>}
      </p>
    </div>
    {e.amount !== null && <span className="frecAmount"><Money value={e.amount} currency={currency} signed /></span>}
    {actions.length > 0 && <div className="fpmRowActions">{actions.map((a) => <button key={a} type="button" className="secondary" disabled={busy} onClick={() => onAct(e, a)}>
      {t(`frec.action.${a}` as MessageKey)}</button>)}</div>}
  </li>;
}

function MatchForm({ rec, row, onDone }: { rec: FinanceReconDetail; row: FinanceReconException; onDone: (text: string | null) => void }) {
  const [options, setOptions] = useState<Load<FinanceReconException[]>>({ kind: 'loading' });
  const [chosen, setChosen] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const fromStatement = Boolean(row.statement_ref);
  useEffect(() => {
    rapi.getReconciliation(rec.id, { limit: 200, offset: 0 }).then((all) => setOptions({ kind: 'ready', value: fromStatement
      ? rc.matchCandidates(row, all.items)
      : all.items.filter((e) => e.kind === 'not_in_ledger' && e.statement_ref && e.amount !== null && Number(e.amount) === Number(row.amount)) }))
      .catch((e) => setOptions(failed(e)));
  }, [rec.id, row, fromStatement]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!chosen) { setError(t('frec.match.pick')); return; }
    const other = options.kind === 'ready' ? options.value.find((o) => o.id === chosen) : undefined;
    setBusy(true); setError(null);
    try {
      await rapi.resolve(rec.id, { kind: 'match', statement_ref: fromStatement ? row.statement_ref : other?.statement_ref, transaction_id: fromStatement ? other?.transaction_id : row.transaction_id });
      onDone(t('frec.done.match'));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)}>
    <p>{t(fromStatement ? 'frec.match.textStatement' : 'frec.match.textLedger', { amount: amount(row.amount, rec.currency) })}</p>
    {options.kind === 'loading' && <Notice busy>{t('frec.loading')}</Notice>}
    {options.kind === 'error' && <p className="formError" role="alert">{options.message}</p>}
    {options.kind === 'ready' && (options.value.length === 0 ? <p className="muted">{t('frec.match.none')}</p>
      : <fieldset className="frecChoices"><legend>{t('frec.match.legend')}</legend>{options.value.map((o) => <label key={o.id}>
        <input type="radio" name="frec-match" value={o.id} checked={chosen === o.id} onChange={() => setChosen(o.id)} />
        <span>{day(o.posted_on)} <span dir="auto">{o.description}</span></span></label>)}</fieldset>)}
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy || options.kind !== 'ready' || options.value.length === 0}>{t('frec.match.confirm')}</button>
      <button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button></div>
  </form>;
}

/** The statement's figures as printed, changed while the session is open (PATCH, compare and set on the version). */
function EditSessionForm({ rec, onCancel, onDone }: { rec: FinanceReconDetail; onCancel: () => void; onDone: (text: string) => void }) {
  const [draft, setDraft] = useState<rc.EditDraft>(() => rc.editDraftOf(rec));
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const owes = rec.account_kind === 'card' || rec.account_kind === 'loan', card = rec.account_kind === 'card';
  const set = (key: keyof rc.EditDraft) => (event: { target: { value: string } }) => setDraft((current) => ({ ...current, [key]: event.target.value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const body = rc.editBody(draft, rec);
    if (!body.ok) { setError(body.error === 'closing' ? t('frec.edit.closingNeeded') : body.error); return; }
    setBusy(true); setError(null);
    try { await rapi.updateReconciliation(rec.id, body.value); onDone(t('frec.done.edit')); } catch (e) { setError(describeError(e)); setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-label={t('frec.edit')}>
    <p className="muted finHelp">{t('frec.edit.help')}</p>
    <div className="cmFormGrid">
      <label className="field"><span>{t(owes ? 'frec.form.openingOwed' : 'frec.form.opening', { currency: rec.currency })}</span>
        <input value={draft.opening} onChange={set('opening')} inputMode="decimal" dir="ltr" data-autofocus /></label>
      <label className="field"><span>{t(owes ? 'frec.form.closingOwed' : 'frec.form.closing', { currency: rec.currency })}</span>
        <input value={draft.closing} onChange={set('closing')} inputMode="decimal" dir="ltr" required /></label>
      {card && <>
        <label className="field"><span>{t('frec.form.due', { currency: rec.currency })}</span><input value={draft.due} onChange={set('due')} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('frec.form.minimum', { currency: rec.currency })}</span><input value={draft.minimum} onChange={set('minimum')} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('frec.form.dueOn')}</span><input type="date" value={draft.dueOn} onChange={set('dueOn')} /></label>
      </>}
    </div>
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('dialog.working') : t('frec.edit.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('fpm.cancel')}</button></div>
  </form>;
}

function ReopenForm({ onCancel, onSubmit }: { onCancel: () => void; onSubmit: (reason: string) => void }) {
  const [reason, setReason] = useState(''), [error, setError] = useState<string | null>(null);
  return <form className="finForm" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length < 3) { setError(t('frec.reopen.reasonNeeded')); return; } onSubmit(reason.trim()); }}>
    <p>{t('frec.reopen.text')}</p>
    <label className="field"><span>{t('frec.reopen.reason')}</span><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} required dir="auto" /></label>
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit">{t('frec.reopen')}</button><button type="button" className="secondary" onClick={onCancel}>{t('fpm.cancel')}</button></div>
  </form>;
}

function NewSessionForm({ accountId, onDone }: { accountId?: string; onDone: (made: FinanceReconDetail | null) => void }) {
  const [accounts, setAccounts] = useState<Load<FinanceAccount[]>>({ kind: 'loading' });
  const [imports, setImports] = useState<FinanceImport[]>([]);
  const period = rc.lastMonth(new Date().toISOString().slice(0, 10));
  const [account, setAccount] = useState(accountId ?? ''), [importId, setImportId] = useState('');
  const [start, setStart] = useState(period.start), [end, setEnd] = useState(period.end);
  const [opening, setOpening] = useState(''), [closing, setClosing] = useState('');
  const [due, setDue] = useState(''), [minimum, setMinimum] = useState(''), [dueOn, setDueOn] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.listAccounts().then((v) => {
      const usable = v.filter((a) => !a.archived && a.kind !== 'investment');
      setAccounts({ kind: 'ready', value: usable });
      setAccount((current) => current || usable[0]?.id || '');
    }).catch((e) => setAccounts(failed(e)));
    api.listImports(100, 0).then((p) => setImports(p.items)).catch(() => setImports([]));
  }, []);
  if (accounts.kind === 'loading') return <Notice busy>{t('frec.loading')}</Notice>;
  if (accounts.kind === 'denied') return <Denied />;
  if (accounts.kind === 'error') return <p className="formError" role="alert">{accounts.message}</p>;
  if (accounts.value.length === 0) return <Empty icon={<Scale aria-hidden="true" />} title={t('frec.noAccounts.title')} text={t('frec.noAccounts.text')} />;
  const chosen = accounts.value.find((a) => a.id === account) ?? accounts.value[0];
  const card = chosen.kind === 'card';
  const owes = chosen.kind === 'card' || chosen.kind === 'loan';
  const mine = imports.filter((i) => i.account_id === chosen.id);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const checks = [closing && parseAmount(rc.ledgerClosing(chosen.kind, closing), chosen.currency), opening && parseAmount(rc.ledgerClosing(chosen.kind, opening), chosen.currency)];
    const bad = checks.find((ch) => ch && !ch.ok);
    if (bad && !bad.ok) { setError(bad.error); return; }
    if (!importId && !closing.trim()) { setError(t('frec.form.closingNeeded')); return; }
    setBusy(true);
    try {
      onDone(await rapi.createReconciliation({
        account_id: chosen.id, import_id: importId || null, period_start: importId ? null : start, period_end: importId ? null : end,
        statement_opening: opening.trim() ? rc.ledgerClosing(chosen.kind, opening) : null, statement_closing: closing.trim() ? rc.ledgerClosing(chosen.kind, closing) : null,
        ...(card ? { amount_due: due.trim() || null, minimum_due: minimum.trim() || null, due_on: dueOn || null } : {}),
      }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <form className="finForm" onSubmit={(e) => void submit(e)}>
    <p className="muted finHelp">{t('frec.form.help')}</p>
    <div className="cmFormGrid">
      <label className="field"><span>{t('frec.form.account')}</span><select value={chosen.id} onChange={(e) => { setAccount(e.target.value); setImportId(''); }}>
        {accounts.value.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
      <label className="field"><span>{t('frec.form.import')}</span><select value={importId} onChange={(e) => setImportId(e.target.value)}>
        <option value="">{t('frec.form.typed')}</option>
        {mine.map((i) => <option key={i.id} value={i.id}>{i.filename} ({formatDateTime(i.created_at, { day: 'numeric', month: 'short' })})</option>)}</select></label>
      {!importId && <>
        <label className="field"><span>{t('frec.form.start')}</span><input type="date" value={start} onChange={(e) => setStart(e.target.value)} required /></label>
        <label className="field"><span>{t('frec.form.end')}</span><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required /></label>
      </>}
      <label className="field"><span>{t(owes ? 'frec.form.openingOwed' : 'frec.form.opening', { currency: chosen.currency })}</span>
        <input value={opening} onChange={(e) => setOpening(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t(owes ? 'frec.form.closingOwed' : 'frec.form.closing', { currency: chosen.currency })}</span>
        <input value={closing} onChange={(e) => setClosing(e.target.value)} inputMode="decimal" dir="ltr" required={!importId} /></label>
      {card && <>
        <label className="field"><span>{t('frec.form.due', { currency: chosen.currency })}</span><input value={due} onChange={(e) => setDue(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('frec.form.minimum', { currency: chosen.currency })}</span><input value={minimum} onChange={(e) => setMinimum(e.target.value)} inputMode="decimal" dir="ltr" /></label>
        <label className="field"><span>{t('frec.form.dueOn')}</span><input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} /></label>
      </>}
    </div>
    {importId && <p className="muted finHelp">{t('frec.form.fromImport')}</p>}
    {error && <p className="formError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{t('frec.form.start.button')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('fpm.cancel')}</button></div>
  </form>;
}
