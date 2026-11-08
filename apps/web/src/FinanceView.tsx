// Finance (docs/finance.md): the signed-in person's own ledger and Financial Twin, read only. The
// summary band, the month's spend by category, accounts, transactions with search and paging, CSV
// statement import with a mapping preview, bills, debts, goals and manual holdings. Nothing here
// moves money: there is no pay, transfer or trade button anywhere, by design (Stage 2).
import { Suspense, lazy, useCallback, useEffect, useId, useState, type FormEvent } from 'react';
import {
  CircleDot, Download, FileUp, Landmark, Lock, Plus, RefreshCw, Search, Settings2, Trash2, Undo2, Wallet,
} from 'lucide-react';
import type {
  FinanceAccount, FinanceAccountKind, FinanceCategory, FinanceFormats,
  FinanceHolding, FinanceImport, FinanceImportPreview, FinancePage, FinanceSettings, FinanceSummary, FinanceTransaction, } from '@anum/contracts';
import * as api from './lib/financeApi';
import {
  PDF_STATEMENT_MAX_BYTES, amountTone, categoryLabel, checkFile, formatAmount, formatAmounts, hashForTab, isLast4, monthLabel, parseAmount, pickAmount,
  rangeLabel, tabFromHash, type FinanceTab,
} from './lib/finance';
import { describeError, isPermissionDenied } from './lib/errors';
import { isPdf } from './lib/financePdf';
import type { PdfImported } from './FinancePdfImport';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { reasonText } from './FinancePlan';
import { ExecutionPanel } from './FinanceTrading';
import { IntelPanel } from './FinanceIntel';
import { PageHeader, PageSummary, useMediaQuery, type Crumb } from './PageParts';
import { FinanceSectionNav } from './FinanceShell';
import { FinanceOverviewPage } from './FinanceOverviewPage';
import { OWN_HEAD_TABS } from './lib/financeNav';
import './commerce.css';
import './finance.css';

const PAGE = 25;
// IBKR trading is its own lazy chunk (docs/finance-live-trading.md): it loads only when its tab opens.
const IbkrPanel = lazy(() => import('./FinanceIbkr').then((m) => ({ default: m.IbkrPanel })));
// Fund research (docs/finance-research.md) is its own lazy chunk too.
const ResearchPanel = lazy(() => import('./FinanceResearch').then((m) => ({ default: m.ResearchPanel })));
// iPhone SMS banking and the double-entry ledger (docs/finance-sms.md): one lazy chunk for both tabs.
const LedgerPanel = lazy(() => import('./FinanceSms').then((m) => ({ default: m.LedgerPanel })));
const PdfStatementImport = lazy(() => import('./FinancePdfImport').then((m) => ({ default: m.PdfStatementImport })));
const PdfTemplates = lazy(() => import('./FinancePdfImport').then((m) => ({ default: m.PdfTemplates })));
// Pages with their own head (lib/financeNav.ts OWN_HEAD_TABS, docs/finance.md#finance-shell), each its own lazy chunk.
const ReportsPage = lazy(() => import('./FinanceReportsPage').then((m) => ({ default: m.FinanceReportsPage })));
const SafePage = lazy(() => import('./FinanceSafePage').then((m) => ({ default: m.FinanceSafePage })));
const AdvisorPage = lazy(() => import('./FinanceAdvisorPage').then((m) => ({ default: m.FinanceAdvisorPage })));
// Bills and budgets, debts and goals (docs/finance.md#bills-and-budgets): two pages in their own lazy chunk.
const BillsBudgetsPage = lazy(() => import('./FinanceBillsBudgets').then((m) => ({ default: m.BillsBudgetsPage })));
const DebtsGoalsPage = lazy(() => import('./FinanceDebtsGoals').then((m) => ({ default: m.DebtsGoalsPage })));
const PLANNING_TABS: ReadonlySet<FinanceTab> = new Set<FinanceTab>(['bills', 'budgets', 'debts', 'goals']);
// Accounts and Transactions and ledger draw their own page head (docs/finance.md#register-and-account-overview).
const AccountsPage = lazy(() => import('./FinanceAccountsPage').then((m) => ({ default: m.AccountsPage })));
const TransactionsPage = lazy(() => import('./FinanceTransactionsPage').then((m) => ({ default: m.TransactionsPage })));
// Holdings (docs/finance.md#holdings) and the paper trading lab (docs/finance.md#paper-trading-lab) draw their own head too.
const HoldingsPage = lazy(() => import('./FinanceHoldings').then((m) => ({ default: m.HoldingsPage })));
const LabPage = lazy(() => import('./FinanceLab').then((m) => ({ default: m.LabPage })));
const GuardianPage = lazy(() => import('./FinanceGuardian').then((m) => ({ default: m.GuardianPage })));
// Connections and data capture (docs/finance.md#connections-and-data-capture): accounts, SMS capture,
// iPhone Shortcuts, import history and permissions; `#finance-sms` opens its SMS capture tab.
const ConnectionsPage = lazy(() => import('./FinanceConnections').then((m) => ({ default: m.ConnectionsPage })));
const KINDS: FinanceAccountKind[] = ['bank', 'card', 'cash', 'loan', 'investment'];
const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'cash', 'transfer', 'income', 'cashback', 'refunds', 'other',
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
  // Accounts hands an account (and optionally the Imports tab) to Transactions and ledger.
  const [txOpen, setTxOpen] = useState<{ accountId?: string; tab?: 'imports'; key: number }>({ key: 0 });
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

  // Bills, budgets, debts and goals need the summary and accounts first: they render below with their own head.
  if (OWN_HEAD_TABS.has(tab) && !PLANNING_TABS.has(tab)) {
    return <div className="cmLayout finLayout finOwnHead">
      <FinanceSectionNav tab={tab} onPick={pick} />
      <Suspense fallback={<Notice busy>{t('finance.loading')}</Notice>}>
        {tab === 'reports' && <ReportsPage onOpen={pick} />}
        {tab === 'safe' && <SafePage onOpen={pick} onChanged={refresh} />}
        {tab === 'advisor' && <AdvisorPage onOpen={pick} />}
        {tab === 'accounts' && <AccountsPage accounts={accounts} base={summary.kind === 'ready' ? summary.value.base_currency : 'AED'} onChanged={refresh}
          onOpenTransactions={(accountId) => { setTxOpen((o) => ({ accountId, key: o.key + 1 })); pick('transactions'); }}
          onImport={() => { setTxOpen((o) => ({ tab: 'imports', key: o.key + 1 })); pick('transactions'); }} />}
        {tab === 'transactions' && (accounts.kind === 'loading' ? <Notice busy>{t('finance.loading')}</Notice>
          : <TransactionsPage key={txOpen.key} accounts={accounts.kind === 'ready' ? accounts.value : []} initialAccountId={txOpen.accountId} initialTab={txOpen.tab}
            onChanged={refresh} onAddAccount={() => pick('accounts')} />)}
        {tab === 'holdings' && <HoldingsPage onOpen={pick} />}
        {tab === 'trading' && <LabPage base={summary.kind === 'ready' ? summary.value.base_currency : 'AED'} onOpen={pick} />}
        {/* Until the summary arrives, the settings' default base currency (AED) prefills Test and new limits. */}
        {tab === 'guardian' && <GuardianPage base={summary.kind === 'ready' ? summary.value.base_currency : 'AED'} onOpen={pick} />}
        {(tab === 'connections' || tab === 'sms') && <ConnectionsPage from={tab} onOpen={pick} />}
      </Suspense>
    </div>;
  }

  // The overview draws its own head, scope and period (docs/finance.md#finance-overview), in every load state.
  if (tab === 'overview') return <>
    <FinanceOverviewPage summary={summary} accounts={accounts} month={month} onMonth={setMonth} onOpen={pick} onRetry={refresh}
      onSettings={() => setSettingsOpen(true)} onImport={() => pick('import')} onAddAccount={() => pick('accounts')}
      accountsError={accounts.kind === 'error' ? <LoadError message={t('finance.error.accounts', { message: accounts.message })} retry={() => void loadAccounts()} /> : null} />
    {settingsOpen && <SettingsDialog onClose={(saved) => { setSettingsOpen(false); if (saved) refresh(); }} />}
  </>;

  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('finance'), href: '#finance', onClick: () => pick('overview') }];
  crumbs.push({ label: t(`finance.tab.${tab}` as MessageKey) });
  const header = <PageHeader title={viewLabel('finance')} subtitle={t('finance.subtitle')} crumbs={crumbs} actions={summary.kind === 'ready' ? <>
    <button type="button" className="secondary" onClick={() => setSettingsOpen(true)}><Settings2 aria-hidden="true" />{t('finance.settings.open')}</button>
    {tab !== 'import' && <button type="button" onClick={() => pick('import')}><FileUp aria-hidden="true" />{t('finance.import.open')}</button>}
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
  // A section with its own page head (lib/financeNav.ts OWN_HEAD_TABS) draws its title, controls and KPIs itself.
  // Only the pages already built on the shell are switched over here; the others keep the generic head.
  const ownHead = OWN_HEAD_TABS.has(tab) && PLANNING_TABS.has(tab);
  const planning = <Notice busy>{t('fplan.loading')}</Notice>;
  return <div className={`cmLayout finLayout ${ownHead ? 'finOwnHead' : ''}`}>
    {!ownHead && header}
    {!ownHead && <PageSummary label={t('finance.summary.label')} figures={figures} footer={<p className="muted finReadOnly">{t('finance.readOnly')}</p>} />}
    <FinanceSectionNav tab={tab} onPick={pick} />
    {noAccounts && <section className="surface cmPanel cmWelcome" aria-labelledby="fin-welcome">
      <Wallet aria-hidden="true" />
      <div><h2 id="fin-welcome">{t('finance.welcome.title')}</h2><p className="muted">{t('finance.welcome.text')}</p>
        <div className="actions"><button type="button" onClick={() => pick('accounts')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button></div></div>
    </section>}
    {accounts.kind === 'error' && <LoadError message={t('finance.error.accounts', { message: accounts.message })} retry={() => void loadAccounts()} />}
    {tab === 'intel' && <IntelPanel onOpen={pick} />}
    {tab === 'execution' && <ExecutionPanel />}
    {tab === 'ibkr' && <Suspense fallback={<section className="surface cmPanel"><Notice busy>{t('ibkr.loading')}</Notice></section>}><IbkrPanel /></Suspense>}
    {tab === 'research' && <Suspense fallback={<section className="surface cmPanel"><Notice busy>{t('fresearch.loading')}</Notice></section>}><ResearchPanel /></Suspense>}
    {tab === 'ledger' && <Suspense fallback={<section className="surface cmPanel"><Notice busy>{t('fsms.loading')}</Notice></section>}><LedgerPanel /></Suspense>}
    {tab === 'import' && <Import accounts={accountList} onChanged={onChanged} onAddAccount={() => pick('accounts')} onDone={() => pick('transactions')} />}
    {(tab === 'bills' || tab === 'budgets') && <Suspense fallback={planning}><BillsBudgetsPage key={tab} initial={tab} accounts={accountList} base={base} today={s.today} onOpen={pick} onChanged={onChanged} /></Suspense>}
    {(tab === 'debts' || tab === 'goals') && <Suspense fallback={planning}><DebtsGoalsPage key={tab} initial={tab} accounts={accountList} base={base} today={s.today} onOpen={pick} onChanged={onChanged} /></Suspense>}
    {settingsOpen && <SettingsDialog onClose={(saved) => { setSettingsOpen(false); if (saved) refresh(); }} />}
  </div>;
}

// ---- Transactions ----------------------------------------------------------------------------


export function TransactionForm({ accounts, transaction, onDone }: { accounts: FinanceAccount[]; transaction: FinanceTransaction | null; onDone: (message: string | null) => void }) {
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

export function AccountForm({ account, base, onDone }: { account: FinanceAccount | null; base: string; onDone: (message: string | null) => void }) {
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

export function Import({ accounts, onChanged, onAddAccount, onDone }: { accounts: FinanceAccount[]; onChanged: () => void; onAddAccount: () => void; onDone: () => void }) {
  const [formats, setFormats] = useState<Load<FinanceFormats>>({ kind: 'loading' });
  const [accountId, setAccountId] = useState(accounts.find((a) => !a.archived && a.kind !== 'investment')?.id ?? accounts[0]?.id ?? '');
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  // A PDF statement is read by the API, not here: the picked file is kept as is until it is reviewed.
  const [pdf, setPdf] = useState<File | null>(null), [pdfOpen, setPdfOpen] = useState(false), [inputKey, setInputKey] = useState(0);
  const [templatesRefresh, setTemplatesRefresh] = useState(0);
  const [preset, setPreset] = useState(''), [dateFormat, setDateFormat] = useState('');
  const [mapping, setMapping] = useState<{ date: string; description: string; amount: string; debit: string; credit: string; currency: string; original_amount: string; original_currency: string; billed_amount: string; billed_currency: string; fx_rate: string; fee: string } | null>(null);
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
  const selected = accounts.find((a) => a.id === accountId) ?? null;
  const body = () => ({
    account_id: accountId, filename: file?.name ?? 'statement.csv', content: file?.content ?? '', preset: preset || null, date_format: dateFormat || null,
    mapping: mapping ? { date: mapping.date, description: mapping.description, amount: mapping.amount || null, debit: mapping.debit || null, credit: mapping.credit || null,
      currency: mapping.currency || null, original_amount: mapping.original_amount || null, original_currency: mapping.original_currency || null,
      billed_amount: mapping.billed_amount || null, billed_currency: mapping.billed_currency || null, fx_rate: mapping.fx_rate || null, fee: mapping.fee || null } : null,
  });
  async function pickFile(list: FileList | null) {
    setPreview(null); setNotice(null); setError(null); setMapping(null); setPdf(null); setPdfOpen(false);
    const picked = list?.[0];
    if (!picked) { setFile(null); return; }
    const checked = checkFile(picked, maxBytes);
    if (!checked.ok) { setFile(null); setError(checked.error); return; }
    if (isPdf(picked)) { setFile(null); setPdf(picked); return; }
    setFile({ name: picked.name, content: await picked.text() });
  }
  async function runPreview(event?: FormEvent) {
    event?.preventDefault();
    if (pdf) { setError(null); setNotice(null); setPdfOpen(true); return; }
    if (!file) { setError(t('finance.import.error.noFile')); return; }
    setBusy(true); setError(null); setNotice(null);
    try { const result = await api.previewImport(body()); setPreview(result); setMapping({ date: result.mapping.date, description: result.mapping.description, amount: result.mapping.amount ?? '', debit: result.mapping.debit ?? '', credit: result.mapping.credit ?? '',
        currency: result.mapping.currency ?? '', original_amount: result.mapping.original_amount ?? '', original_currency: result.mapping.original_currency ?? '',
        billed_amount: result.mapping.billed_amount ?? '', billed_currency: result.mapping.billed_currency ?? '', fx_rate: result.mapping.fx_rate ?? '', fee: result.mapping.fee ?? '' }); }
    catch (e) { setPreview(null); setError(describeError(e)); } finally { setBusy(false); }
  }
  async function confirm() {
    setBusy(true); setError(null);
    try {
      const result = await api.confirmImport(body());
      const matched = (result.reconciled ?? 0) || (result.held_for_review ?? 0)
        ? ` ${t('fsms.import.reconciled', { reconciled: result.reconciled ?? 0, review: result.held_for_review ?? 0 })}` : '';
      setNotice(t('finance.import.done', { imported: result.imported_count, duplicates: result.duplicate_count, errors: result.error_count }) + matched);
      setPreview(null); setFile(null); setMapping(null); onChanged(); void loadHistory();
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  function pdfImported({ result, vault }: PdfImported) {
    const matched = (result.reconciled ?? 0) || (result.held_for_review ?? 0)
      ? ` ${t('fsms.import.reconciled', { reconciled: result.reconciled ?? 0, review: result.held_for_review ?? 0 })}` : '';
    setNotice(t('finance.import.done', { imported: result.imported_count, duplicates: result.duplicate_count, errors: result.error_count }) + matched + (vault ? ` ${vault}` : ''));
    setPdf(null); setPdfOpen(false); setInputKey((k) => k + 1); setTemplatesRefresh((n) => n + 1); onChanged(); void loadHistory();
  }
  function pdfCancelled() { setPdf(null); setPdfOpen(false); setInputKey((k) => k + 1); }
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
      <p className="muted">{t('finance.import.intro', { mb: Math.floor(maxBytes / 1_000_000), pdfMb: PDF_STATEMENT_MAX_BYTES / 1_000_000 })}</p>
      <form className="finForm" onSubmit={(e) => void runPreview(e)} aria-describedby={error ? errorId : undefined}>
        <div className="cmFormGrid">
          <label className="field"><span>{t('finance.col.account')}</span><select value={accountId} onChange={(e) => { setAccountId(e.target.value); setPreview(null); }}>{accounts.filter((a) => !a.archived).map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}</select></label>
          <label className="field" htmlFor={fileId}><span>{t('finance.import.file')}</span><input key={inputKey} id={fileId} type="file" accept=".csv,.txt,.pdf,text/csv,application/pdf" onChange={(e) => void pickFile(e.target.files)} /></label>
          {!pdf && <><label className="field"><span>{t('finance.import.layout')}</span><select value={preset} onChange={(e) => { setPreset(e.target.value); setMapping(null); setPreview(null); }}><option value="">{t('finance.import.detect')}</option>{formats.kind === 'ready' && formats.value.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          <label className="field"><span>{t('finance.import.dateFormat')}</span><select value={dateFormat} onChange={(e) => { setDateFormat(e.target.value); setPreview(null); }}><option value="">{t('finance.import.detect')}</option>{formats.kind === 'ready' && formats.value.date_formats.map((f) => <option key={f} value={f}>{f}</option>)}</select></label></>}
        </div>
        <FormError id={errorId} text={error} />
        <div className="actions"><button type="submit" disabled={busy || (!file && !pdf) || (pdf !== null && pdfOpen)}>{busy && !preview ? t('finance.import.reading') : t('finance.import.preview')}</button></div>
      </form>
      {notice && <Notice>{notice}</Notice>}
      {notice && <div className="actions"><button type="button" className="secondary" onClick={onDone}>{t('finance.import.seeTransactions')}</button></div>}
    </section>
    {pdf && pdfOpen && selected && <Suspense fallback={<Notice busy>{t('fpdf.reading')}</Notice>}>
      <PdfStatementImport file={pdf} account={selected} onImported={pdfImported} onCancel={pdfCancelled} />
    </Suspense>}
    {preview && <section className="surface cmPanel" aria-labelledby="fin-preview" aria-live="polite">
      <h2 id="fin-preview">{t('finance.import.previewTitle', { file: preview.filename })}</h2>
      <p>{t('finance.import.previewText', { rows: preview.row_count, valid: preview.valid_count, fresh: preview.new_count, duplicates: preview.duplicate_count, errors: preview.error_count })}</p>
      <p className="muted">For foreign purchases, record the original USD (or other) amount and map the bank's actual {selected?.currency ?? 'account'} charge as the billed amount. ANUM will not invent a conversion or add a fee twice.</p>
      {preview.date_format_ambiguous && <Notice>{t('finance.import.ambiguous', { format: preview.date_format })}</Notice>}
      <fieldset className="finMapping"><legend>{t('finance.import.mapping', { layout: preview.preset })}</legend>
        <div className="cmFilters">
          {columnSelect('date', t('finance.col.date'), false)}
          {columnSelect('description', t('finance.col.description'), false)}
          {columnSelect('amount', t('finance.import.amountColumn'), true)}
          {columnSelect('debit', t('finance.import.debitColumn'), true)}
          {columnSelect('credit', t('finance.import.creditColumn'), true)}
          {columnSelect('currency', 'Transaction currency (optional)', true)}
          {columnSelect('original_amount', 'Original/foreign amount (USD, etc.)', true)}
          {columnSelect('original_currency', 'Original currency', true)}
          {columnSelect('billed_amount', `Amount actually billed in ${selected?.currency ?? 'account currency'}`, true)}
          {columnSelect('billed_currency', 'Billed currency', true)}
          {columnSelect('fx_rate', 'Bank exchange rate', true)}
          {columnSelect('fee', 'Bank FX/transaction fee', true)}
          <button type="button" className="secondary" disabled={busy} onClick={() => void runPreview()}><RefreshCw aria-hidden="true" />{t('finance.import.recheck')}</button>
        </div>
      </fieldset>
      {preview.rows.length > 0 && <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable">
        <caption className="visuallyHidden">{t('finance.import.sample')}</caption>
        <thead><tr><th scope="col">{t('finance.import.line')}</th><th scope="col">{t('finance.col.date')}</th><th scope="col">{t('finance.col.description')}</th><th scope="col">{t('finance.col.category')}</th><th scope="col" className="num">{t('finance.col.amount')}</th></tr></thead>
        <tbody>{preview.rows.map((row) => <tr key={row.line} className={row.duplicate ? 'finDuplicate' : undefined}>
          <td>{row.line}</td><td><bdi dir="ltr">{row.posted_on}</bdi></td><td className="finDescription"><strong dir="auto">{row.description}</strong>{row.notes && <small style={{ display: 'block' }}>{row.notes}</small>}{row.duplicate && <small>{t('finance.import.duplicate')}</small>}</td><td>{categoryLabel(row.category)}</td><td className="num"><Money value={row.amount} currency={row.currency} signed /></td>
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
    <Suspense fallback={null}><PdfTemplates refresh={templatesRefresh} /></Suspense>
    {undoing && <ConfirmDialog title={t('finance.import.undoTitle')} confirmLabel={t('finance.import.undo')} danger busy={busy} onConfirm={() => void undo()} onCancel={() => setUndoing(null)}>
      <p dir="auto">{t('finance.import.undoText', { file: undoing.filename, count: undoing.imported_count })}</p>
    </ConfirmDialog>}
  </>;
}

// ---- Bills, debts, goals, holdings ------------------------------------------------------------

/** Shared list frame for the four record kinds: load, empty with a next action, error, add, edit, delete. */
// ---- Settings --------------------------------------------------------------------------------

export function SettingsDialog({ onClose }: { onClose: (saved: boolean) => void }) {
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
