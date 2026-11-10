// Finance, Transactions and ledger (docs/finance.md#register-and-account-overview): every transaction with
// where it came from (an SMS, Apple Pay, a CSV or PDF statement, or typed by hand), its receipt, a possible
// duplicate and the category, filtered and paged on the server. The details drawer shows the masked SMS only
// while the retention setting keeps it, attaches a receipt from the document vault and saves the category.
// Nothing here moves money; ANUM has no live bank connection, so no row ever claims one.
import { Suspense, lazy, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  AlertTriangle, CheckCircle2, Copy, Download, FileText, Landmark, MessageSquare, MoreHorizontal, Paperclip, PenLine, Plus, ReceiptText,
  Scale, Search, Smartphone, Tags, Upload, X,
} from 'lucide-react';
import type {
  FinanceAccount, FinanceCategory, FinanceRegisterDetail, FinanceRegisterPage, FinanceRegisterRow, FinanceSmsSettings, FinanceSubcategory, FinanceTransactionType,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import { getRegister, getRegisterDetail } from './lib/financeRegisterApi';
import {
  NO_REGISTER_FILTERS, ORIGINS, ROW_STATUSES, TRANSACTION_TYPES, hashWithMonth, latestMonthHint, monthBounds, monthFromHash, monthIn, narrowed, percentOf, statusTone, type RegisterFilters,
} from './lib/financeRegister';
import { categoryLabel, formatAmount, formatAmounts, isolate, monthLabel, rangeLabel, shiftMonth } from './lib/finance';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { smsSettings } from './lib/financeSmsApi';
import { aiSuggestion, listSubcategories } from './lib/financeRulesApi';
import { kindLabel } from './lib/financeSms';
import { upload as uploadToVault, addLink as linkDocument } from './lib/vaultApi';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, ScopeSwitch, StatusChip, tabPanel } from './FinanceShell';
import { FxDetails } from './FinanceFxDetails';
import { Import, TransactionForm } from './FinanceView';
import { useMediaQuery } from './PageParts';
import './financeRegister.css';

const SmsReview = lazy(() => import('./FinanceSms').then((m) => ({ default: m.Review })));
const LedgerPanel = lazy(() => import('./FinanceSms').then((m) => ({ default: m.LedgerPanel })));
// Apply to similar (docs/finance.md#rules-and-learning): its own chunk, loaded only when opened.
const SimilarDialog = lazy(() => import('./FinanceApplySimilar').then((m) => ({ default: m.SimilarDialog })));

const PAGE = 25;
export type TxTab = 'transactions' | 'ledger' | 'sms' | 'imports';
const CATEGORIES: FinanceCategory[] = [
  'groceries', 'dining', 'food', 'room_rent', 'transport', 'fuel', 'utilities', 'telecom', 'housing', 'shopping', 'health', 'education', 'entertainment',
  'subscriptions', 'travel', 'insurance', 'fees', 'interest', 'cash', 'transfer', 'income', 'other',
];

/** The months the period picker offers: this one and the 23 before it, plus the chosen one when it is older. */
function recentMonths(today: string, chosen: string): string[] {
  const out = [today];
  for (let i = 1; i < 24; i += 1) out.push(shiftMonth(today, -i));
  if (chosen && !out.includes(chosen)) out.push(chosen);
  return out;
}

const urlMonth = () => (typeof location === 'undefined' ? null : monthFromHash(location.hash));

const SOURCE_ICON = { sms: MessageSquare, apple_pay: Smartphone, statement: FileText, manual: PenLine } as const;

export function SourceLabel({ row }: { row: Pick<FinanceRegisterRow, 'origin' | 'statement_format'> }) {
  const Icon = SOURCE_ICON[row.origin];
  const label = row.origin === 'statement' && row.statement_format
    ? t(`freg.source.statement.${row.statement_format}` as MessageKey) : t(`freg.source.${row.origin}` as MessageKey);
  return <span className="fregSource"><Icon aria-hidden="true" />{label}</span>;
}

export function StatusLabel({ row }: { row: Pick<FinanceRegisterRow, 'status'> }) {
  return <StatusChip tone={statusTone(row.status)}>{t(`freg.status.${row.status}` as MessageKey)}</StatusChip>;
}

export function CategoryChip({ category }: { category: FinanceCategory }) {
  return <span className={`fregCat cat-${category}`}>{categoryLabel(category)}</span>;
}

/** The transaction type (docs/finance.md#transaction-types) in the person's language. */
export function typeLabel(type: FinanceTransactionType): string {
  return t(`freg.type.${type}` as MessageKey);
}

function Evidence({ row }: { row: FinanceRegisterRow }) {
  if (row.evidence === 'linked') return <span className="fregEvidence ok"><Paperclip aria-hidden="true" />{t('freg.evidence.linked')}</span>;
  if (row.evidence === 'missing') return <span className="fregEvidence warn"><AlertTriangle aria-hidden="true" />{t('freg.evidence.missing')}</span>;
  return <span className="muted" aria-label={t('freg.evidence.notNeeded')}>-</span>;
}

export function TransactionsPage({ accounts, initialAccountId, initialTab, onChanged, onAddAccount }: {
  accounts: FinanceAccount[]; initialAccountId?: string; initialTab?: TxTab; onChanged: () => void; onAddAccount: () => void;
}) {
  // The month is the Finance time zone's (Finance settings), not UTC: a UAE evening on the last day of a month is still that month.
  const [timeZone, setTimeZone] = useState<string | null>(null);
  const thisMonth = useMemo(() => monthIn(timeZone), [timeZone]);
  const [tab, setTab] = useState<TxTab>(initialTab ?? 'transactions');
  // The period lives in the URL (`#finance-transactions?month=2026-10`): a reload or a shared link opens the same month.
  const [period, setPeriodState] = useState<string>(() => urlMonth() ?? monthIn(null));
  const chosen = useRef(urlMonth() !== null);
  const setPeriod = useCallback((next: string) => {
    chosen.current = true;
    setPeriodState(next);
    if (location.hash.startsWith('#finance-transactions')) history.replaceState(null, '', hashWithMonth(location.hash, next));
  }, []);
  const [latest, setLatest] = useState<string | null>(null);
  const [accountId, setAccountId] = useState(initialAccountId ?? '');
  const [draft, setDraft] = useState<RegisterFilters>(NO_REGISTER_FILTERS);
  const [applied, setApplied] = useState<RegisterFilters>(NO_REGISTER_FILTERS);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<FinanceRegisterPage>>({ kind: 'loading' });
  const [selected, setSelected] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [sms, setSms] = useState<FinanceSmsSettings | null>(null);
  const wide = useMediaQuery('(min-width: 1280px)');
  const filters = useMemo<RegisterFilters>(() => ({ ...applied, account_id: accountId, ...(period ? monthBounds(period) : { from: '', to: '' }) }), [applied, accountId, period]);
  const load = useCallback(async () => {
    setPage((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setPage({ kind: 'ready', value: await getRegister(filters, PAGE, offset) }); } catch (e) { setPage(failed(e)); }
  }, [filters, offset]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void smsSettings().then(setSms, () => setSms(null)); }, []);
  useEffect(() => {
    void api.getSettings().then((s) => setTimeZone(s.timezone), () => setTimeZone(null));
  }, []);
  // Until the person picks a month, the page follows the Finance time zone's current month.
  useEffect(() => { if (!chosen.current) setPeriodState(thisMonth); }, [thisMonth]);
  // An empty month: find the newest row (any month) so the page can say where the records are.
  const emptyMonth = page.kind === 'ready' && page.value.total === 0 && !!period && !narrowed(applied);
  useEffect(() => {
    setLatest(null);
    if (!emptyMonth) return;
    let live = true;
    getRegister({ ...NO_REGISTER_FILTERS, account_id: accountId }, 1, 0)
      .then((found) => { if (live) setLatest(found.items[0]?.posted_on ?? null); }, () => { if (live) setLatest(null); });
    return () => { live = false; };
  }, [emptyMonth, accountId, period]);
  const hint = latestMonthHint(period, page.kind === 'ready' ? page.value.total : 1, latest);
  const changed = () => { void load(); onChanged(); };
  const account = accounts.find((a) => a.id === accountId) ?? null;
  const months = recentMonths(thisMonth, period);

  async function download() {
    try { await api.downloadTransactions({ q: applied.q, account_id: accountId, category: applied.category, from: filters.from, to: filters.to }); setNotice({ text: t('finance.transactions.exported') }); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }

  const ready = page.kind === 'ready' ? page.value : null;
  const ledgerKind = ready?.ledger_kind ?? sms?.ledger_kind ?? 'personal';
  const reviewCount = ready?.review_count ?? 0;
  // A disabled button never explains itself: with no account yet, say so and offer the way to add one.
  const needsAccount = Boolean(ready) && accounts.length === 0;
  const head = <FinancePageHead title={t('freg.tx.title')} subtitle={t('freg.tx.subtitle')}
    actions={<>
      <button type="button" className="secondary" onClick={() => setTab('imports')}><Upload aria-hidden="true" />{t('freg.tx.import')}</button>
      <button type="button" className="secondary" disabled={!ready || ready.total === 0} aria-describedby={ready && ready.total === 0 ? 'ftx-nothing-to-download' : undefined} onClick={() => void download()}><Download aria-hidden="true" />{t('freg.tx.download')}</button>
      <button type="button" disabled={accounts.length === 0} aria-describedby={needsAccount ? 'ftx-needs-account' : undefined} onClick={() => setRecording(true)}><Plus aria-hidden="true" />{t('freg.tx.record')}</button>
      {ready && ready.total === 0 && <span id="ftx-nothing-to-download" className="visuallyHidden">{t('freg.tx.downloadNone')}</span>}
    </>}
    controls={<>
      <label className="finControl"><span className="finControlLabel">{t('fshell.period')}</span>
        <select value={period} onChange={(e) => { setPeriod(e.target.value); setOffset(0); }}>
          {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}<option value="">{t('freg.period.all')}</option></select></label>
      <label className="finControl"><span className="finControlLabel">{t('finance.col.account')}</span>
        <select value={accountId} onChange={(e) => { setAccountId(e.target.value); setOffset(0); }}>
          <option value="">{t('finance.filter.allAccounts')}</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <LedgerScope kind={ledgerKind} />
    </>}
    tabs={<FinanceTabs idPrefix="ftx" label={t('freg.tx.tabs')} value={tab} onChange={setTab} items={[
      { id: 'transactions', label: t('freg.tab.transactions') }, { id: 'ledger', label: t('freg.tab.ledger') },
      { id: 'sms', label: t('freg.tab.sms'), badge: reviewCount > 0 ? reviewCount : undefined }, { id: 'imports', label: t('freg.tab.imports') },
    ]} />} />;

  return <div className="cmLayout finLayout fregPage">
    {head}
    <div {...tabPanel('ftx', tab)}>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {needsAccount && tab !== 'imports' && <FinBanner icon={<Landmark />} title={t('freg.tx.needsAccount.title')}
        action={<button type="button" className="secondary" onClick={onAddAccount}><Plus aria-hidden="true" />{t('freg.tx.needsAccount.action')}</button>}>
        <span id="ftx-needs-account">{t('freg.tx.needsAccount.text')}</span></FinBanner>}
      {tab === 'transactions' && <>
        {page.kind === 'denied' && <Denied />}
        {page.kind === 'error' && <LoadError message={t('finance.error.transactions', { message: page.message })} retry={() => void load()} />}
        {page.kind === 'loading' && <Notice busy>{t('finance.transactions.loading')}</Notice>}
        {ready && <>
          {hint && <FinBanner tone="info" title={t('freg.empty.month', { month: monthLabel(period), latest: monthLabel(hint) })}
            action={<button type="button" className="secondary" onClick={() => { setPeriod(hint); setOffset(0); }}>{t('freg.empty.show', { month: monthLabel(hint) })}</button>} />}
          <Reconciliation page={ready} account={account} period={period} />
          <Kpis page={ready} />
          <div className={`fregWork ${selected && wide ? 'withDrawer' : ''}`}>
            <FinCard title={t('freg.tx.listTitle')} className="fregTableCard">
              <form className="fregFilters" role="search" aria-label={t('finance.transactions.filters')} onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied({ ...draft }); }}>
                <label className="cmSearch fregSearch"><span className="visuallyHidden">{t('freg.filter.search')}</span><Search aria-hidden="true" />
                  <input type="search" value={draft.q} maxLength={200} placeholder={t('freg.filter.search')} onChange={(e) => setDraft({ ...draft, q: e.target.value })} /></label>
                <FilterSelect label={t('finance.col.category')} value={draft.category} all={t('finance.filter.allCategories')} onChange={(v) => setDraft({ ...draft, category: v as FinanceCategory | '' })}
                  options={CATEGORIES.map((c) => [c, categoryLabel(c)])} />
                <FilterSelect label={t('freg.filter.evidence')} value={draft.evidence} all={t('freg.filter.anyEvidence')} onChange={(v) => setDraft({ ...draft, evidence: v as RegisterFilters['evidence'] })}
                  options={[['linked', t('freg.evidence.linked')], ['missing', t('freg.evidence.missing')]]} />
                <FilterSelect label={t('freg.filter.source')} value={draft.origin} all={t('freg.filter.anySource')} onChange={(v) => setDraft({ ...draft, origin: v as RegisterFilters['origin'] })}
                  options={ORIGINS.map((o) => [o, t(`freg.source.${o}` as MessageKey)])} />
                <FilterSelect label={t('freg.filter.status')} value={draft.status} all={t('freg.filter.anyStatus')} onChange={(v) => setDraft({ ...draft, status: v as RegisterFilters['status'] })}
                  options={ROW_STATUSES.map((s) => [s, t(`freg.status.${s}` as MessageKey)])} />
                <FilterSelect label={t('freg.filter.type')} value={draft.type} all={t('freg.filter.anyType')} onChange={(v) => setDraft({ ...draft, type: v as RegisterFilters['type'] })}
                  options={TRANSACTION_TYPES.map((k) => [k, typeLabel(k)])} />
                <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
                {narrowed(applied) && <button type="button" className="secondary" onClick={() => { setDraft(NO_REGISTER_FILTERS); setApplied(NO_REGISTER_FILTERS); setOffset(0); }}>{t('finance.filter.clear')}</button>}
              </form>
              {ready.truncated && <FinBanner tone="warn" icon={<AlertTriangle />}>{t('freg.tx.truncated')}</FinBanner>}
              {(ready.totals.uncategorized ?? 0) > 0 && applied.status !== 'uncategorized' && <FinBanner tone="warn" icon={<AlertTriangle />}
                action={<button type="button" className="cmLinkButton" onClick={() => { const next = { ...applied, status: 'uncategorized' as const }; setDraft(next); setApplied(next); setOffset(0); }}>{t('freg.tx.showUncategorized')}</button>}>
                {t('freg.tx.uncategorized', { count: ready.totals.uncategorized ?? 0 })}</FinBanner>}
              {ready.items.length === 0
                ? <Empty icon={<ReceiptText aria-hidden="true" />}
                  title={narrowed(applied) ? t('finance.transactions.noMatch') : period ? t('freg.tx.emptyMonth', { month: monthLabel(period) }) : t('finance.transactions.empty')}
                  text={narrowed(applied) ? t('finance.transactions.noMatchText') : t('freg.tx.emptyText')}
                  action={narrowed(applied) ? undefined : <div className="actions">
                    {period && <button type="button" className="secondary" onClick={() => setPeriod('')}>{t('freg.period.showAll')}</button>}
                    <button type="button" onClick={() => setTab('imports')}><Upload aria-hidden="true" />{t('freg.tx.import')}</button></div>} />
                : <RegisterTable page={ready} accounts={accounts} selected={selected} onOpen={setSelected} onPage={setOffset} />}
            </FinCard>
            {selected && (wide
              ? <TransactionDrawer key={selected} id={selected} onClose={() => setSelected(null)} onOpen={setSelected} onChanged={changed} />
              : <Modal title={t('freg.drawer.title')} titleHidden className="fregDrawerModal" onClose={() => setSelected(null)}>
                <TransactionDrawer key={selected} id={selected} onClose={() => setSelected(null)} onOpen={setSelected} onChanged={changed} inModal />
              </Modal>)}
          </div>
        </>}
      </>}
      {tab === 'ledger' && <Suspense fallback={<Notice busy>{t('fsms.loading')}</Notice>}><LedgerPanel /></Suspense>}
      {tab === 'sms' && <>
        <FinBanner icon={<MessageSquare />}>{t('freg.sms.intro')} <a href="#finance-sms">{t('freg.sms.settings')}</a></FinBanner>
        <Suspense fallback={<Notice busy>{t('fsms.loading')}</Notice>}><SmsReview accounts={accounts} aiOn={Boolean(sms?.ai.enabled)} onChanged={changed} /></Suspense>
      </>}
      {tab === 'imports' && <Import accounts={accounts} onChanged={changed} onAddAccount={onAddAccount} onDone={() => { setTab('transactions'); void load(); }} />}
    </div>
    {recording && <Modal title={t('finance.transactions.add')} className="formDialog" onClose={() => setRecording(false)}>
      <TransactionForm accounts={accounts} transaction={null} onDone={(text) => { setRecording(false); if (text) { setNotice({ text }); changed(); } }} />
    </Modal>}
  </div>;
}

/** The workspace's ledger kind. Business and all-finance views of one ledger do not exist, so those options say where to go instead. */
export function LedgerScope({ kind }: { kind: 'personal' | 'business' }) {
  const other = kind === 'personal' ? 'business' : 'personal';
  return <ScopeSwitch value={kind} onChange={() => undefined} unavailable={{ [other]: t(`freg.scope.not.${other}` as MessageKey), all: t('freg.scope.notAll') }} />;
}

function FilterSelect({ label, value, all, options, onChange }: { label: string; value: string; all: string; options: [string, string][]; onChange: (value: string) => void }) {
  return <label className="fregFilter"><span className="visuallyHidden">{label}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}><option value="">{all}</option>{options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>;
}

function Reconciliation({ page, account, period }: { page: FinanceRegisterPage; account: FinanceAccount | null; period: string }) {
  const where = account ? account.name : t('finance.filter.allAccounts');
  const when = period ? monthLabel(period) : t('freg.period.all');
  return <section className="fregRecon" aria-labelledby="freg-recon">
    <div className="fregReconHead"><Landmark aria-hidden="true" />
      <div><h2 id="freg-recon">{t('freg.recon.title', { where: isolate(where), when })}</h2><p className="muted">{t('freg.recon.subtitle')}</p></div></div>
    <dl className="fregReconFigures">
      <div><dt>{t('freg.recon.beginning')}</dt><dd>{page.balances.length ? page.balances.map((b) => <Money key={b.currency} value={b.beginning} currency={b.currency} />) : '-'}</dd></div>
      <div><dt>{t('freg.recon.matched')}</dt><dd className="ok">{page.totals.matched}</dd></div>
      <div><dt>{t('freg.recon.unmatched')}</dt><dd className={page.review_count ? 'warn' : ''}>{page.review_count}</dd></div>
      <div><dt>{t('freg.recon.ending')}</dt><dd>{page.balances.length ? page.balances.map((b) => <Money key={b.currency} value={b.ending} currency={b.currency} />) : '-'}</dd></div>
    </dl>
    <p className="fregReconNote"><Scale aria-hidden="true" /><span>{t('freg.recon.explainer')}</span></p>
  </section>;
}

function Kpis({ page }: { page: FinanceRegisterPage }) {
  const totals = page.totals;
  const share = percentOf(totals.categorized, totals.count);
  return <KpiRow label={t('freg.kpi.label')}>
    <KpiCard icon={<ReceiptText />} label={t('freg.kpi.transactions')} value={totals.count} />
    <KpiCard icon={<Download className="fregDown" />} label={t('freg.kpi.outflow')} value={totals.outflow.length ? formatAmounts(totals.outflow) : '-'} tone={totals.outflow.length ? 'bad' : ''} />
    <KpiCard icon={<CheckCircle2 />} label={share === null ? t('freg.kpi.categorizedNone') : t('freg.kpi.categorized', { count: totals.categorized, total: totals.count })} value={share === null ? '-' : `${share}%`} tone={share !== null && share >= 90 ? 'good' : ''} />
    <KpiCard icon={<Paperclip />} label={t('freg.kpi.receiptsMissing')} value={totals.receipts_missing} tone={totals.receipts_missing ? 'warn' : ''} />
  </KpiRow>;
}

function RegisterTable({ page, accounts, selected, onOpen, onPage }: { page: FinanceRegisterPage; accounts: FinanceAccount[]; selected: string | null; onOpen: (id: string) => void; onPage: (offset: number) => void }) {
  const names = useMemo(() => new Map(accounts.map((a) => [a.id, a.name])), [accounts]);
  return <>
    <div className="finTableWrap" tabIndex={0} role="region" aria-label={t('freg.tx.listTitle')}>
      <table className="finGrid fregTable">
        <thead><tr>
          <th scope="col" className="fregHidePhone">{t('finance.col.date')}</th><th scope="col">{t('freg.col.merchant')}</th><th scope="col" className="fregHideXs">{t('finance.col.category')}</th>
          <th scope="col" className="fregHideMd">{t('finance.col.account')}</th><th scope="col" className="fregHideMd">{t('freg.col.receipt')}</th>
          <th scope="col" className="fregHideSm">{t('freg.col.source')}</th><th scope="col" className="num">{t('finance.col.amount')}</th>
          <th scope="col" className="fregHideXs">{t('freg.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th>
        </tr></thead>
        <tbody>{page.items.map((row) => <tr key={row.id} className={selected === row.id ? 'selected' : undefined} onClick={() => onOpen(row.id)}>
          <td className="fregDate fregHidePhone"><bdi dir="ltr">{row.posted_on}</bdi></td>
          <td className="fregDesc"><strong dir="auto">{row.description}</strong>{row.notes && <small dir="auto">{row.notes}</small>}
            {/* Narrow windows: the Category and Status columns (and the Date column on phones) give way so the amount stays on screen; they show here instead (financeRegister.css). */}
            <span className="fregRowMeta"><bdi className="fregRowDate" dir="ltr">{row.posted_on}</bdi><CategoryChip category={row.category} />{row.transaction_type && <small className="fregType">{typeLabel(row.transaction_type)}</small>}<StatusLabel row={row} /></span></td>
          <td className="fregCatCell fregHideXs"><CategoryChip category={row.category} />{row.transaction_type && <small className="fregType">{typeLabel(row.transaction_type)}</small>}</td>
          <td className="fregHideMd" dir="auto">{names.get(row.account_id) ?? '-'}</td>
          <td className="fregHideMd"><Evidence row={row} /></td>
          <td className="fregHideSm"><SourceLabel row={row} /></td>
          <td className="num"><Money value={row.amount} currency={row.currency} signed /></td>
          <td className="fregHideXs"><StatusLabel row={row} /></td>
          <td><button type="button" className="iconButton small" aria-label={t('freg.row.open', { description: row.description })} onClick={(e) => { e.stopPropagation(); onOpen(row.id); }}><MoreHorizontal aria-hidden="true" /></button></td>
        </tr>)}</tbody>
      </table>
    </div>
    <Pager page={page} onPage={onPage} />
  </>;
}

function Pager({ page, onPage }: { page: FinanceRegisterPage; onPage: (offset: number) => void }) {
  if (page.total <= page.limit && page.offset === 0) return <p className="muted cmPagerText">{rangeLabel(page.offset, page.items.length, page.total)}</p>;
  return <nav className="cmPager" aria-label={t('finance.transactions.pages')}>
    <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}>{t('finance.page.previous')}</button>
    <span className="muted" aria-live="polite">{rangeLabel(page.offset, page.items.length, page.total)}</span>
    <button type="button" className="secondary" disabled={page.offset + page.items.length >= page.total} onClick={() => onPage(page.offset + page.limit)}>{t('finance.page.next')}</button>
  </nav>;
}

// ---- Details drawer ----------------------------------------------------------------------------

function TransactionDrawer({ id, onClose, onOpen, onChanged, inModal }: {
  id: string; onClose: () => void; onOpen: (id: string) => void; onChanged: () => void; inModal?: boolean;
}) {
  const [state, setState] = useState<Load<FinanceRegisterDetail>>({ kind: 'loading' });
  const [category, setCategory] = useState<FinanceCategory | ''>('');
  const [kind, setKind] = useState<FinanceTransactionType | ''>('');
  const [categoryTouched, setCategoryTouched] = useState(false);
  const [learn, setLearn] = useState(false);
  const [learnPhrase, setLearnPhrase] = useState('');
  const [reversePending, setReversePending] = useState(false);
  const [busy, setBusy] = useState<'' | 'category' | 'receipt' | 'delete' | 'direction'>('');
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [removing, setRemoving] = useState(false);
  const [similar, setSimilar] = useState(false);
  const [fromAi, setFromAi] = useState<FinanceCategory | null>(null);
  const [undoBatch, setUndoBatch] = useState<string | null>(null);
  const [subs, setSubs] = useState<FinanceSubcategory[]>([]);
  const [sub, setSub] = useState('');
  useEffect(() => { listSubcategories().then(setSubs).catch(() => setSubs([])); }, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const headingId = useId(), categoryId = useId(), typeId = useId(), subId = useId();
  const load = useCallback(async () => {
    try { const value = await getRegisterDetail(id); setState({ kind: 'ready', value }); setCategory(value.row.category_source === 'none' ? value.suggested_category : value.row.category);
      setKind(value.row.transaction_type ?? ''); setCategoryTouched(false); setSub(value.row.subcategory_id ?? '');
      setLearn(false);
      setLearnPhrase((value.merchant && value.row.description.toLowerCase().includes(value.merchant.toLowerCase()) ? value.merchant : value.row.description).slice(0, 80)); }
    catch (e) { setState(failed(e)); }
  }, [id]);
  useEffect(() => { void load(); }, [load]);

  async function saveCategory(row: FinanceRegisterRow) {
    if (!category) return;
    const typeChanged = Boolean(kind) && kind !== row.transaction_type;
    // A type picked on its own files the row under that type's category on the server; the suggested category is sent only when chosen.
    const subChanged = sub !== (row.subcategory_id ?? '');
    const categoryChanged = categoryTouched || learn || (!typeChanged && !subChanged);
    setBusy('category'); setNotice(null);
    try {
      await api.updateTransaction(row.id, {
        ...(categoryChanged ? { category } : {}), ...(typeChanged && kind ? { transaction_type: kind } : {}),
        ...(learn ? { remember_category_match: learnPhrase.trim() } : {}),
        ...(fromAi && categoryChanged && fromAi === category ? { from_ai_suggestion: true } : {}),
        ...(subChanged || (categoryChanged && sub) ? { subcategory_id: sub || null } : {}),
      });
      setFromAi(null); setUndoBatch(null);
      setNotice({ text: typeChanged && kind && !categoryChanged ? t('freg.drawer.typeSaved', { type: typeLabel(kind) }) : t('freg.drawer.categorySaved', { category: categoryLabel(category) }) });
      await load(); onChanged();
    }
    catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(''); }
  }
  async function askAi(row: FinanceRegisterRow) {
    setBusy('category'); setNotice(null);
    try {
      const found = await aiSuggestion(row.id);
      if (found.category) { setCategory(found.category); setCategoryTouched(true); setFromAi(found.category); setNotice({ text: t('fsug.ai.result', { category: categoryLabel(found.category) }) }); }
      else setNotice({ text: t('fsug.ai.none') });
    } catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(''); }
  }
  async function undoSimilar(batchId: string) {
    setBusy('category');
    try { const done = await (await import('./lib/financeRulesApi')).undoBatch(batchId); setUndoBatch(null); setNotice({ text: t('fsug.undone', { count: done.restored }) }); await load(); onChanged(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(''); }
  }
  async function correctDirection(row: FinanceRegisterRow) {
    if (Number(row.amount) <= 0 || !['manual', 'statement'].includes(row.origin)) return;
    if (!category || ['income', 'cashback', 'refunds', 'transfer'].includes(category)) {
      setNotice({ error: true, text: 'Choose an appropriate expense category before changing an incoming transaction to money out.' });
      setReversePending(false);
      return;
    }
    setBusy('direction'); setNotice(null);
    try {
      const updated = await api.updateTransaction(row.id, {
        amount: '-' + row.amount.replace(/^\\+/, ''),
        category,
      });
      setReversePending(false);
      setNotice({ text: `Corrected to money out: ${updated.amount} ${updated.currency}. Balance and journal updated.` });
      await load(); onChanged();
    } catch (e) {
      setNotice({ error: true, text: describeError(e) });
      setReversePending(false);
    } finally { setBusy(''); }
  }
  async function attach(row: FinanceRegisterRow, file: File | undefined) {
    if (!file || !row.canonical_id) return;
    setBusy('receipt'); setNotice(null);
    try {
      const doc = await uploadToVault(file, { title: file.name.replace(/\.[^.]+$/, '') || row.description, category: 'receipt', access_class: 'private' });
      await linkDocument(doc.id, { target_type: 'finance_transaction', target_id: row.canonical_id, label: row.description.slice(0, 200) });
      setNotice({ text: t('freg.drawer.receiptAttached') }); await load(); onChanged();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(''); if (fileRef.current) fileRef.current.value = ''; }
  }
  async function remove(row: FinanceRegisterRow) {
    setBusy('delete');
    try { await api.deleteTransaction(row.id); setRemoving(false); onChanged(); onClose(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); setRemoving(false); } finally { setBusy(''); }
  }

  // A plain function, not a component: a component made here would remount the drawer on every render.
  const frame = (children: ReactNode) => inModal
    ? <div className="fregDrawer inModal">{children}</div>
    : <aside className="fregDrawer" aria-labelledby={headingId}>{children}</aside>;
  const close = <button type="button" className="iconButton" onClick={onClose} aria-label={t('freg.drawer.close')}><X aria-hidden="true" /></button>;
  if (state.kind !== 'ready') return frame(<><div className="fregDrawerHead"><h2 id={headingId}>{t('freg.drawer.title')}</h2>{close}</div>
    {state.kind === 'loading' && <Notice busy>{t('freg.drawer.loading')}</Notice>}
    {state.kind === 'denied' && <Denied />}
    {state.kind === 'error' && <LoadError message={state.message} retry={() => void load()} />}</>);
  const d = state.value, row = d.row;
  const sms = d.sources.find((s) => s.kind === 'sms');
  const smsGone = d.sources.some((s) => s.kind === 'sms' && !s.text);
  const counterparty = d.merchant || row.description;
  return frame(<>
    <div className="fregDrawerHead"><h2 id={headingId}>{t(`freg.drawer.titleFor.${row.origin}` as MessageKey)}</h2>{close}</div>
    <div className="fregDrawerAmount">
      <span className="fregDrawerIcon" aria-hidden="true">{(() => { const Icon = SOURCE_ICON[row.origin]; return <Icon />; })()}</span>
      <div><strong><Money value={row.amount} currency={row.currency} signed /></strong> <StatusLabel row={row} />
        <p dir="auto">{counterparty}</p><small className="muted">{t(Number(row.amount) >= 0 ? 'freg.drawer.in' : 'freg.drawer.out')}{d.kind ? ` · ${kindLabel(d.kind)}` : ''}</small></div>
    </div>
    <dl className="fregFacts">
      <div><dt>{t('finance.col.date')}</dt><dd><bdi dir="ltr">{row.posted_on}</bdi></dd></div>
      <div><dt>{t('freg.drawer.account')}</dt><dd dir="auto">{d.account_name || '-'}</dd></div>
      <div><dt>{t('freg.col.source')}</dt><dd><SourceLabel row={row} /></dd></div>
      {d.reference && <div><dt>{t('freg.drawer.reference')}</dt><dd dir="ltr">{d.reference}</dd></div>}
      <div><dt>{t('freg.drawer.suggested')}</dt><dd><CategoryChip category={d.suggested_category} /> <small className="muted">{t(`freg.suggestedBy.${d.suggested_by}` as MessageKey)}</small></dd></div>
      {row.transaction_type && <div><dt>{t('freg.drawer.type')}</dt><dd>{typeLabel(row.transaction_type)}{row.type_source && <> <small className="muted">{t(`freg.typeBy.${row.type_source}` as MessageKey)}</small></>}</dd></div>}
      <div><dt>{t('freg.drawer.statement')}</dt><dd>{row.reconciled ? <StatusChip tone="ok">{t('freg.drawer.reconciled')}</StatusChip> : t('freg.drawer.notReconciled')}</dd></div>
    </dl>
    {sms?.text && <section className="fregSms" aria-label={t('freg.drawer.sms')}>
      <h3><MessageSquare aria-hidden="true" />{t('freg.drawer.sms')}</h3>
      <p dir="auto">{sms.text}</p>
      <small className="muted">{t('freg.drawer.smsKept', { date: sms.kept_until ? formatDateTime(sms.kept_until) : '-' })}</small>
    </section>}
    {smsGone && <p className="muted fregSmsGone">{t('freg.drawer.smsGone')}</p>}
    {d.duplicate && <FinBanner tone="warn" icon={<Copy />} title={t('freg.drawer.duplicate')}
      action={<button type="button" className="cmLinkButton" onClick={() => onOpen(d.duplicate!.id)}>{t('freg.drawer.viewMatch')}</button>}>
      {t('freg.drawer.duplicateText', { amount: isolate(formatAmount(d.duplicate.amount, d.duplicate.currency)), date: d.duplicate.posted_on, description: d.duplicate.description })}
    </FinBanner>}
    {Number(row.amount) > 0 && (/room\\s*rent|rent\\s*room|bed\\s*space|accommodation\\s*rent|إيجار\\s*غرفة|ايجار\\s*غرفة/i.test(row.description) || category === 'room_rent') &&
      <FinBanner tone="warn" icon={<AlertTriangle />} title="Check payment direction">
        This room-rent entry is incoming. If you paid it, verify the statement debit and correct it to money out. Nothing is changed automatically.
      </FinBanner>}
    {row.transaction_type === 'uncategorized' && <FinBanner tone="warn" icon={<AlertTriangle />} title={t('freg.drawer.uncategorizedTitle')}>{t('freg.drawer.uncategorizedText')}</FinBanner>}
    {row.type_reason === 'refund_suggested' && <FinBanner icon={<ReceiptText />}>{t('freg.drawer.refundSuggested')}</FinBanner>}
    {row.links_need_review > 0 && <FinBanner tone="warn" icon={<AlertTriangle />}>{t('freg.drawer.linksReview', { count: row.links_need_review })}</FinBanner>}
    <div className="fregAttach">
      <div><strong><Paperclip aria-hidden="true" />{t('freg.drawer.receipt')}</strong>
        <small className="muted">{row.receipts ? t('freg.drawer.receipts', { count: row.receipts }) : row.canonical_id ? t('freg.drawer.receiptHint') : t('freg.drawer.receiptNeedsSave')}</small></div>
      {row.canonical_id && <label className={`secondary fregFileButton ${busy ? 'disabled' : ''}`}>{busy === 'receipt' ? t('freg.drawer.attaching') : t('freg.drawer.attach')}
        <input ref={fileRef} type="file" className="visuallyHidden" accept="application/pdf,image/jpeg,image/png" disabled={Boolean(busy)} onChange={(e) => void attach(row, e.target.files?.[0])} /></label>}
    </div>
    {row.receipts > 0 && <p className="fregReceiptsLink"><a href="#vault">{t('freg.drawer.openVault')}</a></p>}
    <FxDetails transactionId={row.id} onChanged={() => { void load(); onChanged(); }} />
    <div className="fregCategory">
      <label htmlFor={categoryId}>{t('freg.drawer.chooseCategory')}</label>
      <select id={categoryId} value={category} onChange={(e) => { setCategory(e.target.value as FinanceCategory); setCategoryTouched(true); setSub(''); }}>
        {CATEGORIES.map((c) => <option key={c} value={c}>{c === d.suggested_category ? t('freg.drawer.suggestedOption', { category: categoryLabel(c) }) : categoryLabel(c)}</option>)}
      </select>
      {subs.some((s) => s.parent === category && !s.archived) && <>
        <label htmlFor={subId}>{t('fsug.editor.subcategory')}</label>
        <select id={subId} value={sub} onChange={(e) => setSub(e.target.value)}>
          <option value="">{t('fsug.editor.none')}</option>
          {subs.filter((s) => s.parent === category && (!s.archived || s.id === sub)).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select></>}
      <label htmlFor={typeId}>{t('freg.drawer.chooseType')}</label>
      <select id={typeId} value={kind} onChange={(e) => setKind(e.target.value as FinanceTransactionType)}>
        {!kind && <option value="">-</option>}
        {TRANSACTION_TYPES.map((k) => <option key={k} value={k}>{typeLabel(k)}</option>)}
      </select>
    </div>
    {["sms", "apple_pay"].includes(row.origin) && <p className="muted">This transaction is linked to an SMS or Apple Pay journal entry. Make its category correction under Finance → iPhone SMS → Review to preserve the audit trail.</p>}
    {["manual", "statement"].includes(row.origin) && <label className="field"><input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} />
      Remember this category for future matching descriptions
    </label>}
    {learn && <label className="field"><span>Merchant/description to remember (specific phrase)</span>
      <input value={learnPhrase} maxLength={80} onChange={(e) => setLearnPhrase(e.target.value)} />
      <small>Applies to future imports only. Review the phrase so unrelated payments are not affected.</small>
    </label>}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {undoBatch && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void undoSimilar(undoBatch)}>{t('fsug.undo')}</button>}
    <div className="fregDrawerActions">
      <button type="button" disabled={Boolean(busy) || !category || !['manual', 'statement'].includes(row.origin)
        || (category === row.category && row.category_source === 'manual' && !learn && (!kind || kind === row.transaction_type) && sub === (row.subcategory_id ?? ''))} onClick={() => void saveCategory(row)}>
        <Tags aria-hidden="true" />{busy === 'category' ? t('finance.saving') : kind && kind !== row.transaction_type ? t('freg.drawer.save') : t('freg.drawer.saveCategory')}</button>
      {Number(row.amount) > 0 && ['statement', 'manual'].includes(row.origin) &&
        <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => setReversePending(true)}>
          <AlertTriangle aria-hidden="true" />Correct to money out
        </button>}
      {['manual', 'statement'].includes(row.origin) && row.category_source === 'manual' &&
        <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => setSimilar(true)}>{t('fsug.similar.button')}</button>}
      {row.transaction_type === 'uncategorized' && ['manual', 'statement'].includes(row.origin) &&
        <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void askAi(row)}>{t('fsug.ai.button')}</button>}
      {d.duplicate && <button type="button" className="secondary danger" disabled={Boolean(busy)} onClick={() => setRemoving(true)}>{t('freg.drawer.deleteCopy')}</button>}
      <button type="button" className="secondary" onClick={onClose}>{t('freg.drawer.done')}</button>
    </div>
    {reversePending && <ConfirmDialog title="Confirm outgoing payment" confirmLabel="Correct to money out" danger busy={busy === 'direction'} onConfirm={() => void correctDirection(row)} onCancel={() => setReversePending(false)}>
      <p>Only confirm after checking the bank statement and selecting the correct expense category. This changes {row.currency} {row.amount} from incoming to outgoing, updates the ledger and may change your balance. The original record remains auditable.</p>
    </ConfirmDialog>}
    {similar && <Suspense fallback={null}><SimilarDialog transactionId={row.id} onClose={() => setSimilar(false)}
      onDone={(text, batchId) => { setSimilar(false); setNotice({ text }); setUndoBatch(batchId ?? null); onChanged(); }} /></Suspense>}
    {removing && <ConfirmDialog title={t('finance.transactions.deleteTitle')} confirmLabel={t('finance.delete')} danger busy={busy === 'delete'} onConfirm={() => void remove(row)} onCancel={() => setRemoving(false)}>
      <p dir="auto">{t('finance.transactions.deleteText', { description: row.description, amount: isolate(formatAmount(row.amount, row.currency)) })}</p>
    </ConfirmDialog>}
  </>);
}
