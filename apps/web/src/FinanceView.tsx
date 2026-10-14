// Finance (docs/finance.md): the signed-in person's own ledger and Financial Twin, read only. The
// summary band, the month's spend by category, accounts, transactions with search and paging, CSV
// statement import with a mapping preview, bills, debts, goals and manual holdings. Nothing here
// moves money: there is no pay, transfer or trade button anywhere, by design (Stage 2).
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowDownLeft, ArrowUpRight, CalendarClock, ChevronLeft, ChevronRight, CircleDot, Download, FileUp, Flag, Landmark, Lock, Pencil, PiggyBank,
  Plus, RefreshCw, Search, Settings2, Trash2, Undo2, Wallet,
} from 'lucide-react';
import type {
  FinanceAccount, FinanceAccountKind, FinanceBill, FinanceBillSuggestion, FinanceCadence, FinanceCategory, FinanceDebt, FinanceDebtKind, FinanceFormats,
  FinanceGoal, FinanceHolding, FinanceImport, FinanceImportPreview, FinancePage, FinanceSettings, FinanceSummary, FinanceTransaction, FinanceUpcomingBill,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import {
  FINANCE_TABS, amountTone, isolate, categoryLabel, checkFile, dueLabel, formatAmount, formatAmounts, hashForTab, isLast4, monthLabel, parseAmount, pickAmount,
  rangeLabel, shiftMonth, spendBars, spendCurrencies, tabFromHash, type FinanceTab,
} from './lib/finance';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { ConnectionsPanel, GuardianPanel, SafeToSpendCard, SafeToSpendPanel, reasonText } from './FinancePlan';
import { ExecutionPanel, TradingPanel } from './FinanceTrading';
import { PageHeader, PageSummary, useMediaQuery, type Crumb } from './PageParts';
import './commerce.css';
import './finance.css';

const PAGE = 25;
const KINDS: FinanceAccountKind[] = ['bank', 'card', 'cash', 'loan', 'investment'];
const CADENCES: FinanceCadence[] = ['monthly', 'weekly', 'quarterly', 'yearly', 'once'];
const DEBT_KINDS: FinanceDebtKind[] = ['loan', 'card', 'mortgage', 'other'];
const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'cash', 'transfer', 'income', 'other',
];

const today = () => new Date().toISOString().slice(0, 10);

/** The finance tabs, deep-linkable (`#finance-transactions`) and in the browser history. */
function useFinanceTab(): [FinanceTab, (next: FinanceTab) => void] {
  const [tab, setTab] = useState<FinanceTab>(() => tabFromHash(typeof location === 'undefined' ? '' : location.hash));
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#finance')) setTab(tabFromHash(location.hash)); };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);
  const pick = (next: FinanceTab) => {
    setTab(next);
    if (location.hash !== hashForTab(next)) history.pushState(null, '', hashForTab(next));
  };
  return [tab, pick];
}

export function FinanceView() {
  const [tab, pick] = useFinanceTab();
  const [month, setMonth] = useState<string | null>(null);
  const [summary, setSummary] = useState<Load<FinanceSummary>>({ kind: 'loading' });
  const [accounts, setAccounts] = useState<Load<FinanceAccount[]>>({ kind: 'loading' });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const loadSummary = useCallback(async () => {
    setSummary((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setSummary({ kind: 'ready', value: await api.getSummary(month ?? undefined) }); } catch (e) { setSummary(failed(e)); }
  }, [month]);
  const loadAccounts = useCallback(async () => {
    try { setAccounts({ kind: 'ready', value: await api.listAccounts() }); } catch (e) { setAccounts(failed(e)); }
  }, []);
  useEffect(() => { void loadSummary(); }, [loadSummary]);
  useEffect(() => { void loadAccounts(); }, [loadAccounts]);
  const refresh = () => { void loadSummary(); void loadAccounts(); };

  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('finance'), href: '#finance', onClick: () => pick('overview') }];
  if (tab !== 'overview') crumbs.push({ label: t(`finance.tab.${tab}` as MessageKey) });
  const header = <PageHeader title={viewLabel('finance')} subtitle={t('finance.subtitle')} crumbs={tab === 'overview' ? undefined : crumbs} actions={summary.kind === 'ready' ? <>
    <button type="button" className="secondary" onClick={() => setSettingsOpen(true)}><Settings2 aria-hidden="true" />{t('finance.settings.open')}</button>
    <button type="button" onClick={() => pick('import')}><FileUp aria-hidden="true" />{t('finance.import.open')}</button>
  </> : undefined} />;

  if (summary.kind === 'loading' && accounts.kind !== 'denied') return <div className="cmLayout">{header}<Notice busy>{t('finance.loading')}</Notice></div>;
  if (summary.kind === 'denied' || accounts.kind === 'denied') return <div className="cmLayout">{header}<Denied /></div>;
  if (summary.kind === 'error') return <div className="cmLayout">{header}<LoadError message={t('finance.error.load', { message: summary.message })} retry={refresh} /></div>;
  if (summary.kind !== 'ready') return <div className="cmLayout">{header}</div>;
  const s = summary.value;
  const accountList = accounts.kind === 'ready' ? accounts.value : [];
  const noAccounts = accounts.kind === 'ready' && accountList.length === 0;
  const base = s.base_currency;
  const figures: Figure[] = [
    figure('balance', t('finance.figure.balance'), { state: 'ready' }, () => ({ value: formatAmount(pickAmount(s.balances, base).amount, pickAmount(s.balances, base).currency), hint: s.balances.length > 1 ? formatAmounts(s.balances.filter((b) => b.currency !== pickAmount(s.balances, base).currency)) : t('finance.figure.balanceHint', { count: s.accounts.filter((a) => !a.archived).length }) })),
    figure('spent', t('finance.figure.spent'), { state: 'ready' }, () => ({ value: formatAmount(pickAmount(s.spend, base).amount, pickAmount(s.spend, base).currency), hint: monthLabel(s.month) })),
    figure('income', t('finance.figure.income'), { state: 'ready' }, () => ({ value: formatAmount(pickAmount(s.income, base).amount, pickAmount(s.income, base).currency), hint: t('finance.figure.net', { amount: formatAmounts(s.net) }) })),
    figure('bills', t('finance.figure.bills'), { state: 'ready' }, () => ({ value: String(s.upcoming_bills.length), hint: s.bills_due.length ? formatAmounts(s.bills_due) : t('finance.figure.billsNone'), tone: s.upcoming_bills.some((b) => b.days_until <= 3) ? 'warn' : '' })),
    figure('debts', t('finance.figure.debts'), { state: 'ready' }, () => ({ value: s.debt_count ? formatAmounts(s.debts) : '-', hint: s.debt_count ? t('finance.figure.minimum', { amount: formatAmounts(s.minimum_payments) }) : t('finance.figure.debtsNone') })),
  ];
  const onChanged = refresh;
  return <div className="cmLayout finLayout">
    {header}
    <PageSummary label={t('finance.summary.label')} figures={figures} footer={<p className="muted finReadOnly">{t('finance.readOnly')}</p>} />
    <nav className="settingsTabs cmTabs" aria-label={t('finance.tabs')}>
      {FINANCE_TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`finance.tab.${id}` as MessageKey)}</button>)}
    </nav>
    {noAccounts && tab !== 'accounts' && <section className="surface cmPanel cmWelcome" aria-labelledby="fin-welcome">
      <Wallet aria-hidden="true" />
      <div><h2 id="fin-welcome">{t('finance.welcome.title')}</h2><p className="muted">{t('finance.welcome.text')}</p>
        <div className="actions"><button type="button" onClick={() => pick('accounts')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button></div></div>
    </section>}
    {accounts.kind === 'error' && <LoadError message={t('finance.error.accounts', { message: accounts.message })} retry={() => void loadAccounts()} />}
    {tab === 'overview' && <Overview summary={s} month={month ?? s.month} onMonth={setMonth} onOpen={pick} />}
    {tab === 'safe' && <SafeToSpendPanel onOpen={pick} onChanged={onChanged} />}
    {tab === 'guardian' && <GuardianPanel base={base} />}
    {tab === 'connections' && <ConnectionsPanel />}
    {tab === 'trading' && <TradingPanel base={base} onOpen={pick} />}
    {tab === 'execution' && <ExecutionPanel />}
    {tab === 'transactions' && <Transactions accounts={accountList} onChanged={onChanged} onImport={() => pick('import')} />}
    {tab === 'accounts' && <Accounts accounts={accounts} base={base} onChanged={onChanged} />}
    {tab === 'import' && <Import accounts={accountList} onChanged={onChanged} onAddAccount={() => pick('accounts')} onDone={() => pick('transactions')} />}
    {tab === 'bills' && <Bills accounts={accountList} base={base} onChanged={onChanged} />}
    {tab === 'debts' && <Debts base={base} onChanged={onChanged} />}
    {tab === 'goals' && <Goals base={base} onChanged={onChanged} />}
    {tab === 'holdings' && <Holdings accounts={accountList} onChanged={onChanged} onAddAccount={() => pick('accounts')} />}
    {settingsOpen && <SettingsDialog onClose={(saved) => { setSettingsOpen(false); if (saved) refresh(); }} />}
  </div>;
}

// ---- Overview --------------------------------------------------------------------------------

function Overview({ summary: s, month, onMonth, onOpen }: { summary: FinanceSummary; month: string; onMonth: (month: string) => void; onOpen: (tab: FinanceTab) => void }) {
  const currencies = spendCurrencies(s, s.base_currency);
  const [currency, setCurrency] = useState<string | null>(null);
  const shown = currency && currencies.includes(currency) ? currency : currencies[0] ?? s.base_currency;
  return <>
    <div className="finMonth" role="group" aria-label={t('finance.month.label')}>
      <button type="button" className="secondary" onClick={() => onMonth(shiftMonth(month, -1))} aria-label={t('finance.month.previous')}><ChevronLeft aria-hidden="true" className="cmFlip" /></button>
      <strong aria-live="polite">{monthLabel(month)}</strong>
      <button type="button" className="secondary" disabled={month >= s.today.slice(0, 7)} onClick={() => onMonth(shiftMonth(month, 1))} aria-label={t('finance.month.next')}><ChevronRight aria-hidden="true" className="cmFlip" /></button>
      {currencies.length > 1 && <label className="cmInline"><span>{t('finance.overview.currency')}</span><select value={shown} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    </div>
    <div className="finOverview">
      <div className="finColumn">
        <SpendChart summary={s} currency={shown} onOpen={() => onOpen('transactions')} />
        <section className="surface cmPanel cmWide" aria-labelledby="fin-upcoming">
          <div className="sectionHeader"><h2 id="fin-upcoming">{t('finance.overview.upcoming')}</h2><button type="button" className="cmLinkButton" onClick={() => onOpen('bills')}>{t('finance.overview.allBills')}</button></div>
          {s.upcoming_bills.length === 0 ? <p className="muted">{t('finance.bills.noneDue')}</p> : <UpcomingList rows={s.upcoming_bills.slice(0, 6)} />}
        </section>
        {s.goals.length > 0 && <section className="surface cmPanel cmWide" aria-labelledby="fin-goals-overview">
          <div className="sectionHeader"><h2 id="fin-goals-overview">{t('finance.tab.goals')}</h2><button type="button" className="cmLinkButton" onClick={() => onOpen('goals')}>{t('finance.overview.manage')}</button></div>
          <ul className="cmList">{s.goals.map((g) => <li key={g.id}><div><strong dir="auto">{g.name}</strong><small>{t('finance.goals.progress', { saved: isolate(formatAmount(g.saved_amount, g.currency)), target: isolate(formatAmount(g.target_amount, g.currency)) })}</small></div><Progress percent={g.percent} label={g.name} /></li>)}</ul>
        </section>}
      </div>
      <div className="finColumn">
        <SafeToSpendCard base={s.base_currency} onOpen={onOpen} />
        <section className="surface cmPanel" aria-labelledby="fin-flow">
          <h2 id="fin-flow">{t('finance.overview.flow')}</h2>
          {s.income.length === 0 && s.spend.length === 0 ? <p className="muted">{t('finance.overview.noFlow')}</p> : <dl className="finFlow">
            {[...new Set([...s.income, ...s.spend].map((row) => row.currency))].map((cur) => {
              const income = s.income.find((r) => r.currency === cur)?.amount ?? '0';
              const spend = s.spend.find((r) => r.currency === cur)?.amount ?? '0';
              const net = s.net.find((r) => r.currency === cur)?.amount ?? '0';
              return <div key={cur} className="finFlowRow">
                <dt>{cur}</dt>
                <dd><span><ArrowDownLeft aria-hidden="true" className="finIn" />{t('finance.overview.in')} <Money value={income} currency={cur} /></span>
                  <span><ArrowUpRight aria-hidden="true" className="finOut" />{t('finance.overview.out')} <Money value={spend} currency={cur} /></span>
                  <span>{t('finance.overview.net')} <Money value={net} currency={cur} signed /></span></dd>
              </div>;
            })}
          </dl>}
        </section>
        <section className="surface cmPanel" aria-labelledby="fin-balances">
          <div className="sectionHeader"><h2 id="fin-balances">{t('finance.overview.accounts')}</h2><button type="button" className="cmLinkButton" onClick={() => onOpen('accounts')}>{t('finance.overview.manage')}</button></div>
          {s.accounts.length === 0 ? <p className="muted">{t('finance.accounts.none')}</p> : <ul className="cmList">{s.accounts.filter((a) => !a.archived).map((a) => <li key={a.id}>
            <div><strong dir="auto">{a.name}</strong><small>{t(`finance.kind.${a.kind}` as MessageKey)}{a.last4 ? ` · ${t('finance.accounts.endingIn', { last4: a.last4 })}` : ''}</small></div>
            <Money value={a.balance} currency={a.currency} signed />
          </li>)}</ul>}
        </section>
      </div>
    </div>
  </>;
}

function Progress({ percent, label }: { percent: number; label: string }) {
  return <span className="finProgress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={t('finance.goals.progressLabel', { name: label, percent })}><span className="finTrack"><span className="finFill" style={{ width: `${percent}%` }} /></span><small aria-hidden="true">{percent}%</small></span>;
}

function UpcomingList({ rows }: { rows: FinanceUpcomingBill[] }) {
  return <ul className="cmList">{rows.map((row) => <li key={`${row.id}-${row.due_on}`}>
    <div><strong dir="auto">{row.name}</strong><small><bdi dir="ltr">{row.due_on}</bdi> · {dueLabel(row.days_until)} · {categoryLabel(row.category)}</small></div>
    <Money value={row.amount} currency={row.currency} />
  </li>)}</ul>;
}

/** The width of an element in CSS pixels, kept current as the window changes. */
function useWidth(): [(node: HTMLDivElement | null) => void, number] {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return undefined;
    const update = () => setWidth(node.getBoundingClientRect().width);
    update();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null;
    observer?.observe(node);
    return () => observer?.disconnect();
  }, [node]);
  return [setNode, width];
}

/**
 * The month's spend by category as horizontal bars (the SalesChart pattern: SVG with a table
 * fallback). The SVG is drawn at the panel's real width, so labels stay 12px on a phone, and it is
 * mirrored in Arabic: labels on the right, bars growing to the left.
 */
function SpendChart({ summary, currency, onOpen }: { summary: FinanceSummary; currency: string; onOpen: () => void }) {
  const bars = useMemo(() => spendBars(summary, currency), [summary, currency]);
  const titleId = useId();
  const [measure, measured] = useWidth();
  const rtl = typeof document !== 'undefined' && document.documentElement.dir === 'rtl';
  const width = Math.max(280, Math.round(measured) || 720);
  const row = 32, labelWidth = Math.min(150, Math.round(width * 0.3)), valueWidth = 104;
  const plot = width - labelWidth - valueWidth;
  const height = Math.max(row, bars.length * row);
  const x = (value: number) => (rtl ? width - value : value);
  return <section className="surface cmPanel cmWide cmChartPanel" aria-labelledby={titleId}>
    <div className="sectionHeader"><h2 id={titleId}>{t('finance.chart.title', { month: monthLabel(summary.month), currency })}</h2></div>
    {bars.length === 0 ? <Empty icon={<PiggyBank aria-hidden="true" />} title={t('finance.chart.empty')} text={t('finance.chart.emptyText')} action={<button type="button" className="secondary" onClick={onOpen}>{t('finance.chart.openTransactions')}</button>} /> : <>
      <div className="cmChart finChart" ref={measure}>
        <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} direction="ltr" role="img" aria-label={t('finance.chart.aria', { count: bars.length, top: categoryLabel(bars[0].category), amount: isolate(formatAmount(bars[0].amount, currency)) })}>
          {bars.map((bar, index) => {
            const y = index * row;
            const w = Math.max(2, bar.share * (plot - 8));
            return <g key={bar.category}>
              <text className="finLabel" x={x(labelWidth - 8)} y={y + row / 2 + 4} textAnchor={rtl ? 'start' : 'end'}>{categoryLabel(bar.category)}</text>
              <rect x={rtl ? width - labelWidth - w : labelWidth} y={y + 7} width={w} height={row - 14} rx={4} className={`finBar finBar${index % 6}`} />
              <text className="finValue" x={x(labelWidth + w + 8)} y={y + row / 2 + 4} textAnchor={rtl ? 'end' : 'start'}>{formatAmount(bar.amount, currency)}</text>
            </g>;
          })}
        </svg>
      </div>
      <details className="cmChartTable"><summary>{t('finance.chart.table')}</summary><div className="cmTableWrap"><table className="cmTable">
        <thead><tr><th scope="col">{t('finance.col.category')}</th><th scope="col" className="num">{t('finance.col.count')}</th><th scope="col" className="num">{t('finance.col.amount')}</th></tr></thead>
        <tbody>{bars.map((bar) => <tr key={bar.category}><td>{categoryLabel(bar.category)}</td><td className="num">{bar.count}</td><td className="num"><Money value={bar.amount} currency={currency} /></td></tr>)}</tbody>
      </table></div></details>
    </>}
  </section>;
}

// ---- Transactions ----------------------------------------------------------------------------

function Pager({ page, onPage, label }: { page: FinancePage<unknown>; onPage: (offset: number) => void; label: string }) {
  if (page.total <= page.limit && page.offset === 0) return <p className="muted cmPagerText">{rangeLabel(page.offset, page.items.length, page.total)}</p>;
  return <nav className="cmPager" aria-label={label}>
    <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('finance.page.previous')}</button>
    <span className="muted" aria-live="polite">{rangeLabel(page.offset, page.items.length, page.total)}</span>
    <button type="button" className="secondary" disabled={page.offset + page.items.length >= page.total} onClick={() => onPage(page.offset + page.limit)}>{t('finance.page.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
  </nav>;
}

interface Filters { q: string; account_id: string; category: string; direction: '' | 'in' | 'out'; from: string; to: string }
const NO_FILTERS: Filters = { q: '', account_id: '', category: '', direction: '', from: '', to: '' };

function Transactions({ accounts, onChanged, onImport }: { accounts: FinanceAccount[]; onChanged: () => void; onImport: () => void }) {
  const [draft, setDraft] = useState<Filters>(NO_FILTERS), [filters, setFilters] = useState<Filters>(NO_FILTERS), [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<FinancePage<FinanceTransaction>>>({ kind: 'loading' });
  const [editing, setEditing] = useState<FinanceTransaction | 'new' | null>(null), [removing, setRemoving] = useState<FinanceTransaction | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null), [busy, setBusy] = useState(false);
  const names = useMemo(() => new Map(accounts.map((a) => [a.id, a.name])), [accounts]);
  const load = useCallback(async () => {
    setState((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.listTransactions({ ...filters, limit: PAGE, offset }) }); } catch (e) { setState(failed(e)); }
  }, [filters, offset]);
  useEffect(() => { void load(); }, [load]);
  const filtered = Object.values(filters).some(Boolean);
  const wide = useMediaQuery('(min-width: 768px)');
  const moreFilters = <>
        <label className="cmInline"><span>{t('finance.col.account')}</span><select value={draft.account_id} onChange={(e) => setDraft({ ...draft, account_id: e.target.value })}><option value="">{t('finance.filter.allAccounts')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        <label className="cmInline"><span>{t('finance.col.category')}</span><select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}><option value="">{t('finance.filter.allCategories')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
        <label className="cmInline"><span>{t('finance.filter.direction')}</span><select value={draft.direction} onChange={(e) => setDraft({ ...draft, direction: e.target.value as Filters['direction'] })}><option value="">{t('finance.filter.both')}</option><option value="out">{t('finance.filter.out')}</option><option value="in">{t('finance.filter.in')}</option></select></label>
        <label className="cmInline"><span>{t('finance.filter.from')}</span><input type="date" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} /></label>
        <label className="cmInline"><span>{t('finance.filter.to')}</span><input type="date" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} /></label>
  </>;
  async function exportCsv() {
    try { await api.downloadTransactions(filters); setNotice({ text: t('finance.transactions.exported') }); } catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteTransaction(removing.id); setNotice({ text: t('finance.transactions.deleted') }); setRemoving(null); await load(); onChanged(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); setRemoving(null); } finally { setBusy(false); }
  }
  return <section className="surface cmPanel" aria-labelledby="fin-transactions">
    <div className="sectionHeader"><h2 id="fin-transactions">{t('finance.tab.transactions')}</h2>
      <div className="finHeadActions">
        <button type="button" className="secondary" onClick={() => void exportCsv()} disabled={state.kind !== 'ready' || state.value.total === 0}><Download aria-hidden="true" />{t('finance.transactions.export')}</button>
        <button type="button" className="cmPrimary" disabled={accounts.length === 0} onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.transactions.add')}</button>
      </div>
    </div>
    <form className="cmFilters finFilters" role="search" aria-label={t('finance.transactions.filters')} onSubmit={(e) => { e.preventDefault(); setOffset(0); setFilters(draft); }}>
      <label className="cmSearch"><span className="visuallyHidden">{t('finance.transactions.search')}</span><input type="search" value={draft.q} maxLength={200} placeholder={t('finance.transactions.search')} onChange={(e) => setDraft({ ...draft, q: e.target.value })} /></label>
      {wide ? moreFilters : <details className="finMoreFilters" open={filtered || undefined}><summary>{t('finance.filter.more')}</summary><div className="cmFilters">{moreFilters}</div></details>}
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
      {filtered && <button type="button" className="secondary" onClick={() => { setDraft(NO_FILTERS); setFilters(NO_FILTERS); setOffset(0); }}>{t('finance.filter.clear')}</button>}
    </form>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('finance.transactions.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={t('finance.error.transactions', { message: state.message })} retry={() => void load()} />}
    {state.kind === 'ready' && (state.value.items.length === 0
      ? <Empty icon={<FileUp aria-hidden="true" />} title={filtered ? t('finance.transactions.noMatch') : t('finance.transactions.empty')} text={filtered ? t('finance.transactions.noMatchText') : t('finance.transactions.emptyText')}
        action={filtered ? undefined : <button type="button" onClick={onImport}><FileUp aria-hidden="true" />{t('finance.import.open')}</button>} />
      : <><div className="cmTableWrap"><table className="cmTable finTable finTxTable">
        <thead><tr><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.col.description')}</th><th scope="col">{t('finance.col.category')}</th><th scope="col" className="num">{t('finance.col.amount')}</th><th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th></tr></thead>
        <tbody>{state.value.items.map((row) => <tr key={row.id}>
          <td className="finDate"><bdi dir="ltr">{row.posted_on}</bdi></td>
          <td className="finDescription"><strong dir="auto">{row.description}</strong><small dir="auto">{names.get(row.account_id) ?? ''}{row.notes ? ` · ${row.notes}` : ''}</small></td>
          <td className="finCat">{categoryLabel(row.category)}{row.category_source === 'manual' && <small>{t('finance.transactions.manualCategory')}</small>}</td>
          <td className="num"><Money value={row.amount} currency={row.currency} signed /></td>
          <td className="finRowActions">
            <button type="button" className="iconButton small" onClick={() => setEditing(row)} aria-label={t('finance.transactions.edit', { description: row.description })}><Pencil aria-hidden="true" /></button>
            <button type="button" className="iconButton small" onClick={() => setRemoving(row)} aria-label={t('finance.transactions.delete', { description: row.description })}><Trash2 aria-hidden="true" /></button>
          </td>
        </tr>)}</tbody>
      </table></div><Pager page={state.value} onPage={setOffset} label={t('finance.transactions.pages')} /></>)}
    {editing && <Modal title={editing === 'new' ? t('finance.transactions.add') : t('finance.transactions.editTitle')} className="formDialog" onClose={() => setEditing(null)}>
      <TransactionForm accounts={accounts} transaction={editing === 'new' ? null : editing} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); void load(); onChanged(); } }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('finance.transactions.deleteTitle')} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p dir="auto">{t('finance.transactions.deleteText', { description: removing.description, amount: isolate(formatAmount(removing.amount, removing.currency)) })}</p>
    </ConfirmDialog>}
  </section>;
}

function TransactionForm({ accounts, transaction, onDone }: { accounts: FinanceAccount[]; transaction: FinanceTransaction | null; onDone: (message: string | null) => void }) {
  const [accountId, setAccountId] = useState(transaction?.account_id ?? accounts.find((a) => !a.archived)?.id ?? '');
  const [postedOn, setPostedOn] = useState(transaction?.posted_on ?? today()), [amount, setAmount] = useState(transaction?.amount ?? '');
  const [description, setDescription] = useState(transaction?.description ?? ''), [category, setCategory] = useState<FinanceCategory | ''>(transaction?.category ?? '');
  const [notes, setNotes] = useState(transaction?.notes ?? ''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const currency = transaction?.currency ?? accounts.find((a) => a.id === accountId)?.currency ?? 'AED';
  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = parseAmount(amount, currency);
    if (!checked.ok) { setError(checked.error); return; }
    if (!description.trim()) { setError(t('finance.error.description')); return; }
    setBusy(true); setError(null);
    try {
      const result = transaction
        ? await api.updateTransaction(transaction.id, { posted_on: postedOn, amount: checked.value, description: description.trim(), notes: notes.trim() || null, ...(category ? { category } : {}) })
        : await api.createTransaction({ account_id: accountId, posted_on: postedOn, amount: checked.value, description: description.trim(), notes: notes.trim() || null, category: category || null });
      const done = transaction ? t('finance.transactions.saved') : t('finance.transactions.created');
      // A record breaking one of the person's Guardian rules is saved (the money is gone) and named.
      const broken = result.guardian && result.guardian.outcome !== 'allow' ? result.guardian.reasons.map(reasonText).join(' ') : '';
      onDone(broken ? `${done} ${t('finance.transactions.ruleWarning', { reasons: broken })}` : done);
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      {!transaction && <label className="field"><span>{t('finance.col.account')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>{accounts.filter((a) => !a.archived).map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>}
      <label className="field"><span>{t('finance.col.date')}</span><input type="date" value={postedOn} onChange={(e) => setPostedOn(e.target.value)} required /></label>
      <label className="field"><span>{t('finance.form.amountSigned', { currency })}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="-45.00" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.col.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value as FinanceCategory | '')}><option value="">{t('finance.form.autoCategory')}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
    </div>
    <label className="field"><span>{t('finance.col.description')}</span><input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} required dir="auto" /></label>
    <label className="field"><span>{t('finance.form.notes')}</span><input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} dir="auto" /></label>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

// ---- Accounts --------------------------------------------------------------------------------

function Accounts({ accounts, base, onChanged }: { accounts: Load<FinanceAccount[]>; base: string; onChanged: () => void }) {
  const [editing, setEditing] = useState<FinanceAccount | 'new' | null>(null), [removing, setRemoving] = useState<FinanceAccount | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null), [busy, setBusy] = useState(false);
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteAccount(removing.id); setNotice({ text: t('finance.accounts.deleted', { name: removing.name }) }); setRemoving(null); onChanged(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); setRemoving(null); } finally { setBusy(false); }
  }
  async function toggleArchive(account: FinanceAccount) {
    try { await api.updateAccount(account.id, { archived: !account.archived }); setNotice({ text: account.archived ? t('finance.accounts.restored', { name: account.name }) : t('finance.accounts.archived', { name: account.name }) }); onChanged(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  return <section className="surface cmPanel" aria-labelledby="fin-accounts">
    <div className="sectionHeader"><h2 id="fin-accounts">{t('finance.tab.accounts')}</h2><button type="button" className="cmPrimary" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button></div>
    <p className="muted">{t('finance.accounts.privacy')}</p>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {accounts.kind === 'loading' && <Notice busy>{t('finance.accounts.loading')}</Notice>}
    {accounts.kind === 'error' && <LoadError message={t('finance.error.accounts', { message: accounts.message })} retry={onChanged} />}
    {accounts.kind === 'ready' && (accounts.value.length === 0
      ? <Empty icon={<Landmark aria-hidden="true" />} title={t('finance.accounts.none')} text={t('finance.accounts.noneText')} action={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} />
      : <ul className="cmList finItems">{accounts.value.map((a) => <li key={a.id} className={a.archived ? 'finArchived' : undefined}>
        <div><strong dir="auto">{a.name}{a.archived && <span className="statusBadge neutral">{t('finance.accounts.archivedBadge')}</span>}</strong>
          <small>{t(`finance.kind.${a.kind}` as MessageKey)} · {a.currency}{a.institution ? ` · ${a.institution}` : ''}{a.last4 ? ` · ${t('finance.accounts.endingIn', { last4: a.last4 })}` : ''} · {t('finance.accounts.count', { count: a.transaction_count })}</small></div>
        <div className="finItemEnd"><Money value={a.balance} currency={a.currency} signed />
          <button type="button" className="iconButton small" onClick={() => setEditing(a)} aria-label={t('finance.accounts.edit', { name: a.name })}><Pencil aria-hidden="true" /></button>
          <button type="button" className="iconButton small" onClick={() => void toggleArchive(a)} aria-label={a.archived ? t('finance.accounts.restore', { name: a.name }) : t('finance.accounts.archive', { name: a.name })}><Undo2 aria-hidden="true" /></button>
          <button type="button" className="iconButton small" onClick={() => setRemoving(a)} aria-label={t('finance.accounts.delete', { name: a.name })}><Trash2 aria-hidden="true" /></button></div>
      </li>)}</ul>)}
    {editing && <Modal title={editing === 'new' ? t('finance.accounts.add') : t('finance.accounts.editTitle')} className="formDialog" onClose={() => setEditing(null)}>
      <AccountForm account={editing === 'new' ? null : editing} base={base} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); onChanged(); } }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('finance.accounts.deleteTitle')} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p dir="auto">{t('finance.accounts.deleteText', { name: removing.name, count: removing.transaction_count })}</p>
    </ConfirmDialog>}
  </section>;
}

function AccountForm({ account, base, onDone }: { account: FinanceAccount | null; base: string; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(account?.name ?? ''), [kind, setKind] = useState<FinanceAccountKind>(account?.kind ?? 'bank');
  const [currency, setCurrency] = useState(account?.currency ?? base), [institution, setInstitution] = useState(account?.institution ?? '');
  const [last4, setLast4] = useState(account?.last4 ?? ''), [opening, setOpening] = useState(account?.opening_balance ?? '0');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId(), last4Help = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    if (!/^[A-Za-z]{3}$/.test(currency.trim())) { setError(t('finance.error.currency')); return; }
    if (!isLast4(last4.trim())) { setError(t('finance.error.last4')); return; }
    const amount = parseAmount(opening || '0', currency.trim().toUpperCase());
    if (!amount.ok) { setError(amount.error); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), kind, institution: institution.trim() || null, last4: last4.trim() || null, opening_balance: amount.value };
    try {
      if (account) await api.updateAccount(account.id, body);
      else await api.createAccount({ ...body, currency: currency.trim().toUpperCase() });
      onDone(account ? t('finance.accounts.saved', { name: body.name }) : t('finance.accounts.created', { name: body.name }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.kind')}</span><select value={kind} onChange={(e) => setKind(e.target.value as FinanceAccountKind)}>{KINDS.map((k) => <option key={k} value={k}>{t(`finance.kind.${k}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} disabled={!!account} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.institution')}</span><input value={institution} onChange={(e) => setInstitution(e.target.value)} maxLength={120} dir="auto" /></label>
      <label className="field"><span>{t('finance.form.last4')}</span><input value={last4} onChange={(e) => setLast4(e.target.value.replace(/\D/g, '').slice(0, 4))} inputMode="numeric" maxLength={4} dir="ltr" aria-describedby={last4Help} /><small id={last4Help} className="muted">{t('finance.form.last4Help')}</small></label>
      <label className="field"><span>{t('finance.form.opening', { currency })}</span><input value={opening} onChange={(e) => setOpening(e.target.value)} inputMode="decimal" dir="ltr" /></label>
    </div>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

// ---- Import ----------------------------------------------------------------------------------

function Import({ accounts, onChanged, onAddAccount, onDone }: { accounts: FinanceAccount[]; onChanged: () => void; onAddAccount: () => void; onDone: () => void }) {
  const [formats, setFormats] = useState<Load<FinanceFormats>>({ kind: 'loading' });
  const [accountId, setAccountId] = useState(accounts.find((a) => !a.archived && a.kind !== 'investment')?.id ?? accounts[0]?.id ?? '');
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  const [preset, setPreset] = useState(''), [dateFormat, setDateFormat] = useState('');
  const [mapping, setMapping] = useState<{ date: string; description: string; amount: string; debit: string; credit: string } | null>(null);
  const [preview, setPreview] = useState<FinanceImportPreview | null>(null);
  const [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<Load<FinancePage<FinanceImport>>>({ kind: 'loading' });
  const [undoing, setUndoing] = useState<FinanceImport | null>(null);
  const fileId = useId(), errorId = useId();
  const loadHistory = useCallback(async () => { try { setHistory({ kind: 'ready', value: await api.listImports(20, 0) }); } catch (e) { setHistory(failed(e)); } }, []);
  useEffect(() => { void api.getFormats().then((value) => setFormats({ kind: 'ready', value })).catch((e) => setFormats(failed(e))); void loadHistory(); }, [loadHistory]);
  useEffect(() => { if (!accountId && accounts[0]) setAccountId(accounts[0].id); }, [accounts, accountId]);
  if (accounts.length === 0) return <section className="surface cmPanel" aria-labelledby="fin-import"><h2 id="fin-import">{t('finance.tab.import')}</h2>
    <Empty icon={<Landmark aria-hidden="true" />} title={t('finance.import.needAccount')} text={t('finance.import.needAccountText')} action={<button type="button" onClick={onAddAccount}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} /></section>;
  const maxBytes = formats.kind === 'ready' ? formats.value.limits.max_bytes : 2_000_000;
  const body = () => ({
    account_id: accountId, filename: file?.name ?? 'statement.csv', content: file?.content ?? '', preset: preset || null, date_format: dateFormat || null,
    mapping: mapping ? { date: mapping.date, description: mapping.description, amount: mapping.amount || null, debit: mapping.debit || null, credit: mapping.credit || null } : null,
  });
  async function pickFile(list: FileList | null) {
    setPreview(null); setNotice(null); setError(null); setMapping(null);
    const picked = list?.[0];
    if (!picked) { setFile(null); return; }
    const checked = checkFile(picked, maxBytes);
    if (!checked.ok) { setFile(null); setError(checked.error); return; }
    setFile({ name: picked.name, content: await picked.text() });
  }
  async function runPreview(event?: FormEvent) {
    event?.preventDefault();
    if (!file) { setError(t('finance.import.error.noFile')); return; }
    setBusy(true); setError(null); setNotice(null);
    try { const result = await api.previewImport(body()); setPreview(result); setMapping({ date: result.mapping.date, description: result.mapping.description, amount: result.mapping.amount ?? '', debit: result.mapping.debit ?? '', credit: result.mapping.credit ?? '' }); }
    catch (e) { setPreview(null); setError(describeError(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    setBusy(true); setError(null);
    try {
      const result = await api.confirmImport(body());
      setNotice(t('finance.import.done', { imported: result.imported_count, duplicates: result.duplicate_count, errors: result.error_count }));
      setPreview(null); setFile(null); setMapping(null); onChanged(); void loadHistory();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function undo() {
    if (!undoing) return;
    setBusy(true);
    try { await api.deleteImport(undoing.id); setNotice(t('finance.import.undone', { file: undoing.filename })); setUndoing(null); onChanged(); void loadHistory(); }
    catch (e) { setError(describeError(e)); setUndoing(null); } finally { setBusy(false); }
  }
  const headers = preview?.headers ?? [];
  const columnSelect = (key: keyof NonNullable<typeof mapping>, label: string, optional: boolean) => <label className="cmInline"><span>{label}</span>
    <select value={mapping?.[key] ?? ''} onChange={(e) => mapping && setMapping({ ...mapping, [key]: e.target.value })}>{optional && <option value="">{t('finance.import.noColumn')}</option>}{headers.filter(Boolean).map((h) => <option key={h} value={h}>{h}</option>)}</select></label>;
  return <>
    <section className="surface cmPanel" aria-labelledby="fin-import">
      <h2 id="fin-import">{t('finance.import.title')}</h2>
      <p className="muted">{t('finance.import.intro', { mb: Math.floor(maxBytes / 1_000_000) })}</p>
      <form className="finForm" onSubmit={(e) => void runPreview(e)} aria-describedby={error ? errorId : undefined}>
        <div className="cmFormGrid">
          <label className="field"><span>{t('finance.col.account')}</span><select value={accountId} onChange={(e) => { setAccountId(e.target.value); setPreview(null); }}>{accounts.filter((a) => !a.archived).map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
          <label className="field" htmlFor={fileId}><span>{t('finance.import.file')}</span><input id={fileId} type="file" accept=".csv,.txt,text/csv" onChange={(e) => void pickFile(e.target.files)} /></label>
          <label className="field"><span>{t('finance.import.layout')}</span><select value={preset} onChange={(e) => { setPreset(e.target.value); setMapping(null); setPreview(null); }}><option value="">{t('finance.import.detect')}</option>{formats.kind === 'ready' && formats.value.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          <label className="field"><span>{t('finance.import.dateFormat')}</span><select value={dateFormat} onChange={(e) => { setDateFormat(e.target.value); setPreview(null); }}><option value="">{t('finance.import.detect')}</option>{formats.kind === 'ready' && formats.value.date_formats.map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
        </div>
        <FormError id={errorId} text={error} />
        <div className="actions"><button type="submit" disabled={busy || !file}>{busy && !preview ? t('finance.import.reading') : t('finance.import.preview')}</button></div>
      </form>
      {notice && <Notice>{notice}</Notice>}
      {notice && <div className="actions"><button type="button" className="secondary" onClick={onDone}>{t('finance.import.seeTransactions')}</button></div>}
    </section>
    {preview && <section className="surface cmPanel" aria-labelledby="fin-preview" aria-live="polite">
      <h2 id="fin-preview">{t('finance.import.previewTitle', { file: preview.filename })}</h2>
      <p>{t('finance.import.previewText', { rows: preview.row_count, valid: preview.valid_count, fresh: preview.new_count, duplicates: preview.duplicate_count, errors: preview.error_count })}</p>
      {preview.date_format_ambiguous && <Notice>{t('finance.import.ambiguous', { format: preview.date_format })}</Notice>}
      <fieldset className="finMapping"><legend>{t('finance.import.mapping', { layout: preview.preset })}</legend>
        <div className="cmFilters">
          {columnSelect('date', t('finance.col.date'), false)}
          {columnSelect('description', t('finance.col.description'), false)}
          {columnSelect('amount', t('finance.import.amountColumn'), true)}
          {columnSelect('debit', t('finance.import.debitColumn'), true)}
          {columnSelect('credit', t('finance.import.creditColumn'), true)}
          <button type="button" className="secondary" disabled={busy} onClick={() => void runPreview()}><RefreshCw aria-hidden="true" />{t('finance.import.recheck')}</button>
        </div>
      </fieldset>
      {preview.rows.length > 0 && <div className="cmTableWrap"><table className="cmTable finTable">
        <caption className="visuallyHidden">{t('finance.import.sample')}</caption>
        <thead><tr><th scope="col">{t('finance.import.line')}</th><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.col.description')}</th><th scope="col">{t('finance.col.category')}</th><th scope="col" className="num">{t('finance.col.amount')}</th></tr></thead>
        <tbody>{preview.rows.map((row) => <tr key={row.line} className={row.duplicate ? 'finDuplicate' : undefined}>
          <td>{row.line}</td><td><bdi dir="ltr">{row.posted_on}</bdi></td><td className="finDescription"><strong dir="auto">{row.description}</strong>{row.duplicate && <small>{t('finance.import.duplicate')}</small>}</td><td>{categoryLabel(row.category)}</td><td className="num"><Money value={row.amount} currency={row.currency} signed /></td>
        </tr>)}</tbody>
      </table></div>}
      {preview.errors.length > 0 && <details className="finErrors"><summary>{t('finance.import.errors', { count: preview.error_count })}</summary><ul>{preview.errors.map((e) => <li key={e.line}>{t('finance.import.errorLine', { line: e.line, message: e.message })}</li>)}</ul></details>}
      <div className="actions">
        <button type="button" disabled={busy || preview.new_count === 0} onClick={() => void confirm()}>{busy ? t('finance.import.importing') : t('finance.import.confirm', { count: preview.new_count })}</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => setPreview(null)}>{t('common.cancel')}</button>
      </div>
      {preview.new_count === 0 && <p className="muted">{t('finance.import.nothingNew')}</p>}
    </section>}
    <section className="surface cmPanel" aria-labelledby="fin-import-history">
      <h2 id="fin-import-history">{t('finance.import.history')}</h2>
      {history.kind === 'loading' && <Notice busy>{t('finance.import.historyLoading')}</Notice>}
      {history.kind === 'error' && <LoadError message={history.message} retry={() => void loadHistory()} />}
      {history.kind === 'ready' && (history.value.items.length === 0 ? <p className="muted">{t('finance.import.historyEmpty')}</p> : <ul className="cmList">{history.value.items.map((item) => <li key={item.id}>
        <div><strong dir="auto">{item.filename}</strong><small>{formatDateTime(item.created_at)} · {accounts.find((a) => a.id === item.account_id)?.name ?? ''} · {t('finance.import.historyCounts', { imported: item.imported_count, duplicates: item.duplicate_count, errors: item.error_count })}</small></div>
        <button type="button" className="secondary" onClick={() => setUndoing(item)}><Undo2 aria-hidden="true" />{t('finance.import.undo')}</button>
      </li>)}</ul>)}
    </section>
    {undoing && <ConfirmDialog title={t('finance.import.undoTitle')} confirmLabel={t('finance.import.undo')} danger busy={busy} onConfirm={() => void undo()} onCancel={() => setUndoing(null)}>
      <p dir="auto">{t('finance.import.undoText', { file: undoing.filename, count: undoing.imported_count })}</p>
    </ConfirmDialog>}
  </>;
}

// ---- Bills, debts, goals, holdings ------------------------------------------------------------

/** Shared list frame for the four record kinds: load, empty with a next action, error, add, edit, delete. */
function useItems<T>(list: () => Promise<T[]>) {
  const [state, setState] = useState<Load<T[]>>({ kind: 'loading' });
  const load = useCallback(async () => { try { setState({ kind: 'ready', value: await list() }); } catch (e) { setState(failed(e)); } }, [list]);
  useEffect(() => { void load(); }, [load]);
  return [state, load] as const;
}

function ItemsPanel<T extends { id: string; name?: string | null; symbol?: string }>({ id, title, addLabel, items, load, emptyIcon, emptyTitle, emptyText, intro, render, form, remove, onChanged, extra }: {
  id: string; title: string; addLabel: string; items: Load<T[]>; load: () => Promise<void>; emptyIcon: ReactNode; emptyTitle: string; emptyText: string; intro?: string;
  render: (item: T) => ReactNode; form: (item: T | null, done: (message: string | null) => void) => ReactNode; remove: (item: T) => Promise<void>; onChanged: () => void; extra?: ReactNode;
}) {
  const [editing, setEditing] = useState<T | 'new' | null>(null), [removing, setRemoving] = useState<T | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null), [busy, setBusy] = useState(false);
  const nameOf = (item: T) => item.name || item.symbol || '';
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await remove(removing); setNotice({ text: t('finance.item.deleted', { name: nameOf(removing) }) }); setRemoving(null); await load(); onChanged(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); setRemoving(null); } finally { setBusy(false); }
  }
  return <section className="surface cmPanel" aria-labelledby={id}>
    <div className="sectionHeader"><h2 id={id}>{title}</h2><button type="button" className="cmPrimary" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{addLabel}</button></div>
    {intro && <p className="muted">{intro}</p>}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {extra}
    {items.kind === 'loading' && <Notice busy>{t('finance.item.loading')}</Notice>}
    {items.kind === 'denied' && <Denied />}
    {items.kind === 'error' && <LoadError message={t('finance.error.items', { message: items.message })} retry={() => void load()} />}
    {items.kind === 'ready' && (items.value.length === 0
      ? <Empty icon={emptyIcon} title={emptyTitle} text={emptyText} action={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{addLabel}</button>} />
      : <ul className="cmList finItems">{items.value.map((item) => <li key={item.id}>{render(item)}
        <div className="finItemActions">
          <button type="button" className="iconButton small" onClick={() => setEditing(item)} aria-label={t('finance.item.edit', { name: nameOf(item) })}><Pencil aria-hidden="true" /></button>
          <button type="button" className="iconButton small" onClick={() => setRemoving(item)} aria-label={t('finance.item.delete', { name: nameOf(item) })}><Trash2 aria-hidden="true" /></button>
        </div>
      </li>)}</ul>)}
    {editing && <Modal title={editing === 'new' ? addLabel : t('finance.item.editTitle', { name: nameOf(editing) })} className="formDialog" onClose={() => setEditing(null)}>
      {form(editing === 'new' ? null : editing, (text) => { setEditing(null); if (text) { setNotice({ text }); void load(); onChanged(); } })}
    </Modal>}
    {removing && <ConfirmDialog title={t('finance.item.deleteTitle', { name: nameOf(removing) })} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p>{t('finance.item.deleteText')}</p>
    </ConfirmDialog>}
  </section>;
}

function Bills({ accounts, base, onChanged }: { accounts: FinanceAccount[]; base: string; onChanged: () => void }) {
  const [items, load] = useItems<FinanceBill>(api.listBills);
  const [upcoming, setUpcoming] = useState<Load<FinanceUpcomingBill[]>>({ kind: 'loading' });
  const [suggestions, setSuggestions] = useState<FinanceBillSuggestion[]>([]);
  const [saving, setSaving] = useState<string | null>(null), [message, setMessage] = useState<string | null>(null);
  const loadUpcoming = useCallback(async () => {
    try { setUpcoming({ kind: 'ready', value: await api.upcomingBills(30) }); } catch (e) { setUpcoming(failed(e)); }
    try { setSuggestions(await api.billSuggestions()); } catch { setSuggestions([]); }
  }, []);
  useEffect(() => { void loadUpcoming(); }, [loadUpcoming, items]);
  async function keep(s: FinanceBillSuggestion) {
    setSaving(s.name);
    try { await api.createBill({ name: s.name, amount: s.amount, currency: s.currency, cadence: s.cadence, next_due_on: s.next_due_on, category: s.category, account_id: s.account_id, source: 'detected' }); setMessage(t('finance.bills.kept', { name: s.name })); await load(); onChanged(); }
    catch (e) { setMessage(describeError(e)); } finally { setSaving(null); }
  }
  return <>
    <section className="surface cmPanel" aria-labelledby="fin-due">
      <h2 id="fin-due"><CalendarClock aria-hidden="true" className="cmInlineIcon" />{t('finance.bills.due30')}</h2>
      {upcoming.kind === 'loading' && <Notice busy>{t('finance.item.loading')}</Notice>}
      {upcoming.kind === 'error' && <LoadError message={upcoming.message} retry={() => void loadUpcoming()} />}
      {upcoming.kind === 'ready' && (upcoming.value.length === 0 ? <p className="muted">{t('finance.bills.noneDue')}</p> : <UpcomingList rows={upcoming.value} />)}
    </section>
    {suggestions.length > 0 && <section className="surface cmPanel" aria-labelledby="fin-suggest">
      <h2 id="fin-suggest">{t('finance.bills.suggestions')}</h2>
      <p className="muted">{t('finance.bills.suggestionsText')}</p>
      {message && <Notice>{message}</Notice>}
      <ul className="cmList">{suggestions.map((s) => <li key={`${s.account_id}-${s.name}`}>
        <div><strong dir="auto">{s.name}</strong><small>{t(`finance.cadence.${s.cadence}` as MessageKey)} · {t('finance.bills.seen', { count: s.occurrences })} · {t('finance.bills.next', { date: isolate(s.next_due_on) })}</small></div>
        <div className="finItemEnd"><Money value={s.amount} currency={s.currency} /><button type="button" className="secondary" disabled={saving === s.name} onClick={() => void keep(s)}><Plus aria-hidden="true" />{t('finance.bills.keep')}</button></div>
      </li>)}</ul>
    </section>}
    <ItemsPanel<FinanceBill> id="fin-bills" title={t('finance.tab.bills')} addLabel={t('finance.bills.add')} items={items} load={load} onChanged={onChanged}
      emptyIcon={<CalendarClock aria-hidden="true" />} emptyTitle={t('finance.bills.empty')} emptyText={t('finance.bills.emptyText')}
      render={(b) => <div><strong dir="auto">{b.name}{!b.active && <span className="statusBadge neutral">{t('finance.bills.paused')}</span>}{b.source === 'detected' && <span className="statusBadge neutral">{t('finance.bills.detected')}</span>}</strong>
        <small>{t(`finance.cadence.${b.cadence}` as MessageKey)} · {t('finance.bills.next', { date: isolate(b.next_due_on) })} · {categoryLabel(b.category)} · <Money value={b.amount} currency={b.currency} /></small></div>}
      form={(b, done) => <BillForm bill={b} accounts={accounts} base={base} onDone={done} />}
      remove={(b) => api.deleteBill(b.id)} />
  </>;
}

function BillForm({ bill, accounts, base, onDone }: { bill: FinanceBill | null; accounts: FinanceAccount[]; base: string; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(bill?.name ?? ''), [amount, setAmount] = useState(bill?.amount ?? ''), [currency, setCurrency] = useState(bill?.currency ?? base);
  const [cadence, setCadence] = useState<FinanceCadence>(bill?.cadence ?? 'monthly'), [due, setDue] = useState(bill?.next_due_on ?? today());
  const [category, setCategory] = useState<FinanceCategory>(bill?.category ?? 'utilities'), [accountId, setAccountId] = useState(bill?.account_id ?? '');
  const [active, setActive] = useState(bill?.active ?? true), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    const checked = parseAmount(amount, currency, { signed: false, allowZero: false });
    if (!checked.ok) { setError(checked.error); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), amount: checked.value, currency, cadence, next_due_on: due, category, account_id: accountId || null, active, source: bill?.source ?? 'manual' as const };
    try { if (bill) await api.updateBill(bill.id, body); else await api.createBill(body); onDone(t('finance.item.saved', { name: body.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.amount', { currency })}</span><input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.cadence')}</span><select value={cadence} onChange={(e) => setCadence(e.target.value as FinanceCadence)}>{CADENCES.map((c) => <option key={c} value={c}>{t(`finance.cadence.${c}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.nextDue')}</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} required /></label>
      <label className="field"><span>{t('finance.col.category')}</span><select value={category} onChange={(e) => setCategory(e.target.value as FinanceCategory)}>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.paidFrom')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)}><option value="">{t('finance.form.noAccount')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="field finCheck"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} /><span>{t('finance.form.active')}</span></label>
    </div>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

function Debts({ base, onChanged }: { base: string; onChanged: () => void }) {
  const [items, load] = useItems<FinanceDebt>(api.listDebts);
  return <ItemsPanel<FinanceDebt> id="fin-debts" title={t('finance.tab.debts')} addLabel={t('finance.debts.add')} items={items} load={load} onChanged={onChanged}
    emptyIcon={<Landmark aria-hidden="true" />} emptyTitle={t('finance.debts.empty')} emptyText={t('finance.debts.emptyText')}
    render={(d) => <div><strong dir="auto">{d.name}</strong><small>{t(`finance.debtKind.${d.kind}` as MessageKey)} · <Money value={d.balance} currency={d.currency} /> · {t('finance.debts.apr', { apr: Number(d.apr_percent).toString() })} · {t('finance.debts.minimum', { amount: isolate(formatAmount(d.minimum_payment, d.currency)) })}{d.due_day ? ` · ${t('finance.debts.dueDay', { day: d.due_day })}` : ''}</small></div>}
    form={(d, done) => <DebtForm debt={d} base={base} onDone={done} />}
    remove={(d) => api.deleteDebt(d.id)} />;
}

function DebtForm({ debt, base, onDone }: { debt: FinanceDebt | null; base: string; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(debt?.name ?? ''), [kind, setKind] = useState<FinanceDebtKind>(debt?.kind ?? 'loan'), [currency, setCurrency] = useState(debt?.currency ?? base);
  const [balance, setBalance] = useState(debt?.balance ?? ''), [apr, setApr] = useState(debt ? String(Number(debt.apr_percent)) : '0'), [minimum, setMinimum] = useState(debt?.minimum_payment ?? '0');
  const [dueDay, setDueDay] = useState(debt?.due_day ? String(debt.due_day) : ''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    const owed = parseAmount(balance, currency, { signed: false }), min = parseAmount(minimum || '0', currency, { signed: false });
    if (!owed.ok) { setError(owed.error); return; }
    if (!min.ok) { setError(min.error); return; }
    if (!/^\d{1,3}(\.\d{1,4})?$/.test(apr.trim()) || Number(apr) > 100) { setError(t('finance.error.apr')); return; }
    if (dueDay && !/^([1-9]|[12]\d|3[01])$/.test(dueDay)) { setError(t('finance.error.dueDay')); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), kind, balance: owed.value, currency, apr_percent: apr.trim(), minimum_payment: min.value, due_day: dueDay ? Number(dueDay) : null };
    try { if (debt) await api.updateDebt(debt.id, body); else await api.createDebt(body); onDone(t('finance.item.saved', { name: body.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.kind')}</span><select value={kind} onChange={(e) => setKind(e.target.value as FinanceDebtKind)}>{DEBT_KINDS.map((k) => <option key={k} value={k}>{t(`finance.debtKind.${k}` as MessageKey)}</option>)}</select></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.owed', { currency })}</span><input value={balance} onChange={(e) => setBalance(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.apr')}</span><input value={apr} onChange={(e) => setApr(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('finance.form.minimum', { currency })}</span><input value={minimum} onChange={(e) => setMinimum(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('finance.form.dueDay')}</span><input value={dueDay} onChange={(e) => setDueDay(e.target.value.replace(/\D/g, '').slice(0, 2))} inputMode="numeric" dir="ltr" /></label>
    </div>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

function Goals({ base, onChanged }: { base: string; onChanged: () => void }) {
  const [items, load] = useItems<FinanceGoal>(api.listGoals);
  return <ItemsPanel<FinanceGoal> id="fin-goals" title={t('finance.tab.goals')} addLabel={t('finance.goals.add')} items={items} load={load} onChanged={onChanged}
    emptyIcon={<Flag aria-hidden="true" />} emptyTitle={t('finance.goals.empty')} emptyText={t('finance.goals.emptyText')}
    render={(g) => {
      const percent = Number(g.target_amount) > 0 ? Math.max(0, Math.min(100, Math.floor((Number(g.saved_amount) * 100) / Number(g.target_amount)))) : 0;
      return <div className="finGoal"><strong dir="auto">{g.name}</strong><small>{t('finance.goals.progress', { saved: isolate(formatAmount(g.saved_amount, g.currency)), target: isolate(formatAmount(g.target_amount, g.currency)) })}{g.target_on ? ` · ${t('finance.goals.by', { date: isolate(g.target_on) })}` : ''}{g.monthly_contribution ? ` · ${t('finance.goals.monthly', { amount: isolate(formatAmount(g.monthly_contribution, g.currency)) })}` : ''}</small><Progress percent={percent} label={g.name} /></div>;
    }}
    form={(g, done) => <GoalForm goal={g} base={base} onDone={done} />}
    remove={(g) => api.deleteGoal(g.id)} />;
}

function GoalForm({ goal, base, onDone }: { goal: FinanceGoal | null; base: string; onDone: (message: string | null) => void }) {
  const [name, setName] = useState(goal?.name ?? ''), [currency, setCurrency] = useState(goal?.currency ?? base), [target, setTarget] = useState(goal?.target_amount ?? '');
  const [saved, setSaved] = useState(goal?.saved_amount ?? '0'), [by, setBy] = useState(goal?.target_on ?? ''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [monthly, setMonthly] = useState(goal?.monthly_contribution ?? '');
  const errorId = useId(), monthlyHelp = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setError(t('finance.error.name')); return; }
    const goalAmount = parseAmount(target, currency, { signed: false, allowZero: false }), savedAmount = parseAmount(saved || '0', currency, { signed: false });
    if (!goalAmount.ok) { setError(goalAmount.error); return; }
    if (!savedAmount.ok) { setError(savedAmount.error); return; }
    const monthlyAmount = monthly.trim() ? parseAmount(monthly, currency, { signed: false }) : null;
    if (monthlyAmount && !monthlyAmount.ok) { setError(monthlyAmount.error); return; }
    setBusy(true); setError(null);
    const body = { name: name.trim(), currency, target_amount: goalAmount.value, saved_amount: savedAmount.value, target_on: by || null, monthly_contribution: monthlyAmount?.ok ? monthlyAmount.value : null };
    try { if (goal) await api.updateGoal(goal.id, body); else await api.createGoal(body); onDone(t('finance.item.saved', { name: body.name })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.form.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
      <label className="field"><span>{t('finance.form.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.target', { currency })}</span><input value={target} onChange={(e) => setTarget(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.saved', { currency })}</span><input value={saved} onChange={(e) => setSaved(e.target.value)} inputMode="decimal" dir="ltr" /></label>
      <label className="field"><span>{t('finance.form.by')}</span><input type="date" value={by} onChange={(e) => setBy(e.target.value)} /></label>
      <label className="field"><span>{t('finance.form.monthly', { currency })}</span><input value={monthly} onChange={(e) => setMonthly(e.target.value)} inputMode="decimal" dir="ltr" aria-describedby={monthlyHelp} /></label>
    </div>
    <p id={monthlyHelp} className="muted finHelp">{t('finance.form.monthlyHelp')}</p>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

function Holdings({ accounts, onChanged, onAddAccount }: { accounts: FinanceAccount[]; onChanged: () => void; onAddAccount: () => void }) {
  const [items, load] = useItems<FinanceHolding>(api.listHoldings);
  const brokers = accounts.filter((a) => a.kind === 'investment');
  if (brokers.length === 0) return <section className="surface cmPanel" aria-labelledby="fin-holdings"><h2 id="fin-holdings">{t('finance.tab.holdings')}</h2>
    <Empty icon={<Wallet aria-hidden="true" />} title={t('finance.holdings.needAccount')} text={t('finance.holdings.needAccountText')} action={<button type="button" onClick={onAddAccount}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} /></section>;
  const names = new Map(brokers.map((a) => [a.id, a.name]));
  return <ItemsPanel<FinanceHolding> id="fin-holdings" title={t('finance.tab.holdings')} addLabel={t('finance.holdings.add')} items={items} load={load} onChanged={onChanged}
    intro={t('finance.holdings.intro')}
    emptyIcon={<Wallet aria-hidden="true" />} emptyTitle={t('finance.holdings.empty')} emptyText={t('finance.holdings.emptyText')}
    render={(h) => <div><strong dir="ltr">{h.symbol}{h.name ? <span dir="auto"> · {h.name}</span> : null}</strong><small>{names.get(h.account_id) ?? ''} · {t('finance.holdings.quantity', { quantity: Number(h.quantity).toString() })} · {t('finance.holdings.cost', { amount: isolate(formatAmount(h.cost_basis, h.currency)) })}</small></div>}
    form={(h, done) => <HoldingForm holding={h} accounts={brokers} onDone={done} />}
    remove={(h) => api.deleteHolding(h.id)} />;
}

function HoldingForm({ holding, accounts, onDone }: { holding: FinanceHolding | null; accounts: FinanceAccount[]; onDone: (message: string | null) => void }) {
  const [accountId, setAccountId] = useState(holding?.account_id ?? accounts[0]?.id ?? ''), [symbol, setSymbol] = useState(holding?.symbol ?? ''), [name, setName] = useState(holding?.name ?? '');
  const [quantity, setQuantity] = useState(holding ? String(Number(holding.quantity)) : ''), [cost, setCost] = useState(holding?.cost_basis ?? '');
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  const currency = accounts.find((a) => a.id === accountId)?.currency ?? 'AED';
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!symbol.trim()) { setError(t('finance.error.symbol')); return; }
    if (!/^\d{1,12}(\.\d{1,8})?$/.test(quantity.trim())) { setError(t('finance.error.quantity')); return; }
    const paid = parseAmount(cost, currency, { signed: false });
    if (!paid.ok) { setError(paid.error); return; }
    setBusy(true); setError(null);
    const body = { account_id: accountId, symbol: symbol.trim(), name: name.trim() || null, quantity: quantity.trim(), cost_basis: paid.value };
    try { if (holding) await api.updateHolding(holding.id, body); else await api.createHolding(body); onDone(t('finance.item.saved', { name: body.symbol.toUpperCase() })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
    <div className="cmFormGrid">
      <label className="field"><span>{t('finance.col.account')}</span><select value={accountId} onChange={(e) => setAccountId(e.target.value)}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
      <label className="field"><span>{t('finance.form.symbol')}</span><input value={symbol} onChange={(e) => setSymbol(e.target.value)} maxLength={40} dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.holdingName')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} dir="auto" /></label>
      <label className="field"><span>{t('finance.form.quantity')}</span><input value={quantity} onChange={(e) => setQuantity(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
      <label className="field"><span>{t('finance.form.cost', { currency })}</span><input value={cost} onChange={(e) => setCost(e.target.value)} inputMode="decimal" dir="ltr" required /></label>
    </div>
    <FormError id={errorId} text={error} />
    <FormActions busy={busy} onCancel={() => onDone(null)} />
  </form>;
}

// ---- Settings --------------------------------------------------------------------------------

function SettingsDialog({ onClose }: { onClose: (saved: boolean) => void }) {
  const [state, setState] = useState<Load<FinanceSettings>>({ kind: 'loading' });
  const [currency, setCurrency] = useState('AED'), [zone, setZone] = useState('Asia/Dubai'), [rules, setRules] = useState<{ match: string; category: FinanceCategory }[]>([]);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  useEffect(() => { void api.getSettings().then((value) => { setState({ kind: 'ready', value }); setCurrency(value.base_currency); setZone(value.timezone); setRules(value.category_rules); }).catch((e) => setState(failed(e))); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!/^[A-Za-z]{3}$/.test(currency)) { setError(t('finance.error.currency')); return; }
    const kept = rules.filter((r) => r.match.trim());
    if (kept.some((r) => r.match.trim().length < 2)) { setError(t('finance.error.rule')); return; }
    setBusy(true); setError(null);
    try { await api.saveSettings({ base_currency: currency.toUpperCase(), timezone: zone.trim(), category_rules: kept.map((r) => ({ match: r.match.trim(), category: r.category })) }); onClose(true); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('finance.settings.title')} className="formDialog" onClose={() => onClose(false)}>
    {state.kind === 'loading' && <Notice busy>{t('finance.loading')}</Notice>}
    {(state.kind === 'error' || state.kind === 'denied') && <Notice error>{state.message}</Notice>}
    {state.kind === 'ready' && <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <div className="cmFormGrid">
        <label className="field"><span>{t('finance.settings.currency')}</span><input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase().slice(0, 3))} maxLength={3} dir="ltr" required /></label>
        <label className="field"><span>{t('finance.settings.timezone')}</span><input value={zone} onChange={(e) => setZone(e.target.value)} maxLength={64} dir="ltr" required /></label>
      </div>
      <fieldset className="finMapping"><legend>{t('finance.settings.rules')}</legend>
        <p className="muted">{t('finance.settings.rulesText')}</p>
        {rules.map((rule, index) => <div key={index} className="cmFilters">
          <label className="cmInline cmSearch"><span>{t('finance.settings.match')}</span><input value={rule.match} onChange={(e) => setRules(rules.map((r, i) => i === index ? { ...r, match: e.target.value } : r))} maxLength={80} dir="auto" /></label>
          <label className="cmInline"><span>{t('finance.col.category')}</span><select value={rule.category} onChange={(e) => setRules(rules.map((r, i) => i === index ? { ...r, category: e.target.value as FinanceCategory } : r))}>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></label>
          <button type="button" className="iconButton" onClick={() => setRules(rules.filter((_, i) => i !== index))} aria-label={t('finance.settings.removeRule', { match: rule.match || String(index + 1) })}><Trash2 aria-hidden="true" /></button>
        </div>)}
        <button type="button" className="secondary" disabled={rules.length >= 100} onClick={() => setRules([...rules, { match: '', category: 'other' }])}><Plus aria-hidden="true" />{t('finance.settings.addRule')}</button>
      </fieldset>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(false)} />
    </form>}
  </Modal>;
}
