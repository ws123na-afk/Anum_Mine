// Business books (docs/business-books.md#web): the Books tab of Business. Overview (the P&L on an accrual or cash basis,
// tax kept apart, cash flow with what members paid from their own money, receivables and payables with their age,
// customers, projects, cost centres, budget against actual, recurring costs, the 12-month trend and every exchange rate
// used), Bills (payables: record, pay by hand or with an allocation, void), Vendors, Allocations (the rows members
// allocated from their own Finance ledger and how the books count them), Budgets, Journal (adjustments the invoices
// and allocations cannot express), Intercompany and Settings. Nothing here posts money: the page says so where money is shown.
import { useCallback, useEffect, useId, useState } from 'react';
import { Download, Plus, Search } from 'lucide-react';
import type {
  BooksAdjustment, BooksAdjustmentKind, BooksAllocation, BooksAllocationFilter, BooksBasis, BooksBill, BooksBillFilter, BooksBudget, BooksCostCentre,
  BooksIntercompany, BooksProjectPage, BooksReport, BooksSetup, BooksVendor,
} from '@anum/contracts';
import { BOOKS_ADJUSTMENT_KINDS, BOOKS_BILL_FILTERS } from '@anum/contracts';
import * as api from './lib/businessBooksApi';
import {
  BOOKS_PAGE, BOOKS_TABS, OWNER_TABS, PERIOD_PRESETS, adjustmentLabel, basisLabel, billFilterLabel, booksTabFromId, booksTabLabel, isNegative, lineLabel, money,
  moneyList, paymentStateLabel, paymentStateTone, pct, presetPeriod, reportIsEmpty, sectionLabel, statementRows, type BooksTab, type PeriodPreset,
} from './lib/businessBooks';
import { describeError, isPermissionDenied } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import type { Crumb } from './PageParts';
import { FinancePageHead } from './FinanceShell';
import { BooksDashboard, singleMonth } from './BusinessBooksDashboard';
import { ConfirmDialog } from './Dialog';
import { Badge, Denied, Empty, Failed, Note, Pager, ReasonDialog, day, failure } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';
import {
  AdjustmentDialog, BillDialog, BillDrawer, BudgetDialog, IntercompanyDialog, SettingsSection, TagDialog, VendorDialog,
} from './BusinessBooksForms';
import { GroupSection, RateFeedCard, Recurring, Upcoming } from './BusinessBooksMore';
import { AccountantsCard, AccountsSection, CloseSection, LedgerSection, StatementsSection } from './BusinessBooksLedger';
import { TaxSection } from './BusinessBooksTax';
import './businessBooks.css';

type SetupLoad = { kind: 'loading' } | { kind: 'ready'; setup: BooksSetup } | { kind: 'denied'; text: string } | { kind: 'error'; text: string };
const label = (key: string) => t(key as MessageKey);
const dayText = (value: string | null | undefined) => (value ? day(value) : '-');

export function BooksTab({ section, onSection, crumbs }: { section: string | null; onSection: (tab: BooksTab) => void; crumbs?: Crumb[] }) {
  const tab = booksTabFromId(section);
  const [load, setLoad] = useState<SetupLoad>({ kind: 'loading' });
  const [recording, setRecording] = useState(false);
  const [recorded, setRecorded] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(async () => {
    try { setLoad({ kind: 'ready', setup: await api.getSetup() }); }
    catch (error) { setLoad(isPermissionDenied(error) ? { kind: 'denied', text: describeError(error) } : { kind: 'error', text: describeError(error) }); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  const ready = load.kind === 'ready' && Boolean(load.setup.settings);
  const write = load.kind === 'ready' && load.setup.permissions.write;
  // The page head of Expenses and P&L (docs/business-books.md#overview-dashboard): its own title, the primary action and the sections.
  const head = <FinancePageHead title={t('books.page.title')} subtitle={t('books.page.subtitle')} crumbs={crumbs}
    actions={ready && write ? <button type="button" onClick={() => setRecording(true)}><Plus aria-hidden="true" />{t('books.page.record')}</button> : undefined}
    tabs={load.kind === 'ready' ? <nav className="finTabs bkNav" aria-label={t('books.sections')}>{BOOKS_TABS.filter((item) => load.setup.permissions.manage || !OWNER_TABS.includes(item)).map((item) =>
      <button key={item} type="button" aria-current={tab === item ? 'page' : undefined} onClick={() => onSection(item)}>{booksTabLabel(item)}</button>)}</nav> : undefined} />;
  if (load.kind === 'loading') return <section className="bkPage opsPage">{head}<Note>{t('books.loading')}</Note></section>;
  if (load.kind === 'denied') return <section className="bkPage opsPage">{head}<Denied text={load.text} /></section>;
  if (load.kind === 'error') return <section className="bkPage opsPage">{head}<Failed text={load.text} onRetry={() => { setLoad({ kind: 'loading' }); void reload(); }} /></section>;
  const setup = load.setup;
  return <section className="bzSection bkBooks bkPage opsPage" aria-label={t('books.page.title')}>
    {head}
    <p className="muted bzHint">{t('books.noPosting')}</p>
    {recorded && <Note>{recorded}</Note>}
    {!ready && tab !== 'settings' && <Empty title={t('books.setupTitle')} text={setup.permissions.manage ? t('books.setupText') : t('books.setupMember')}
      action={setup.permissions.manage ? <button type="button" onClick={() => onSection('settings')}>{t('books.setupAction')}</button> : undefined} />}
    {ready && tab === 'overview' && <Overview key={tick} setup={setup} onSection={onSection} />}
    {ready && tab === 'group' && (setup.permissions.manage ? <GroupSection setup={setup} /> : <Denied text={t('books.group.ownersOnly')} />)}
    {ready && tab === 'bills' && <Bills key={tick} setup={setup} />}
    {ready && tab === 'recurring' && <Recurring setup={setup} />}
    {ready && tab === 'vendors' && <Vendors setup={setup} />}
    {ready && tab === 'allocations' && <Allocations setup={setup} />}
    {ready && tab === 'budgets' && <Budgets setup={setup} />}
    {ready && tab === 'journal' && <Journal setup={setup} />}
    {ready && tab === 'ledger' && <LedgerSection setup={setup} />}
    {ready && tab === 'statements' && <StatementsSection setup={setup} onSection={(next) => onSection(booksTabFromId(next))} />}
    {ready && tab === 'accounts' && <AccountsSection setup={setup} />}
    {ready && tab === 'close' && <CloseSection setup={setup} onSection={(next) => onSection(booksTabFromId(next))} />}
    {ready && tab === 'tax' && <TaxSection setup={setup} />}
    {ready && tab === 'intercompany' && <Intercompany setup={setup} />}
    {tab === 'settings' && <SettingsSection setup={setup} onSaved={() => void reload()} extra={ready ? <><RateFeedCard setup={setup} /><AccountantsCard setup={setup} /></> : null} />}
    {recording && <BillDialog setup={setup} onClose={() => setRecording(false)}
      onSaved={(bill) => { setRecording(false); setRecorded(t('books.page.recorded', { vendor: bill.vendor_name })); setTick((n) => n + 1); }} />}
  </section>;
}

// ---- overview: the P&L and the dashboard -------------------------------------------------------------------------

type ReportLoad = { state: 'loading' } | { state: 'ready'; value: BooksReport } | { state: 'error'; message: string };

function Overview({ setup, onSection }: { setup: BooksSetup; onSection: (tab: BooksTab) => void }) {
  const settings = setup.settings!;
  // The mockup opens on the month: this month by default, the rest of the presets and any dates one choice away.
  const [period, setPeriod] = useState(() => presetPeriod('this_month', new Date(), settings.fiscal_year_start_month));
  const [basis, setBasis] = useState<BooksBasis>(settings.basis);
  const [preset, setPreset] = useState<PeriodPreset | ''>('this_month');
  const [report, setReport] = useState<ReportLoad>({ state: 'loading' });
  const [exporting, setExporting] = useState<string | null>(null);
  const ids = { preset: useId(), from: useId(), to: useId(), basis: useId() };
  const load = useCallback(async () => {
    setReport({ state: 'loading' });
    try { setReport({ state: 'ready', value: await api.getReport({ ...period, basis }) }); }
    catch (error) { setReport({ state: 'error', message: isPermissionDenied(error) ? t('biz.denied', { message: describeError(error) }) : describeError(error) }); }
  }, [period.from, period.to, basis]);
  useEffect(() => { void load(); }, [load]);
  const choose = (value: PeriodPreset | '') => { setPreset(value); if (value) setPeriod(presetPeriod(value, new Date(), settings.fiscal_year_start_month)); };
  const value = report.state === 'ready' ? report.value : null;
  async function exportCsv() {
    setExporting(null);
    try { await api.exportReport({ ...period, basis }); setExporting(t('books.exported')); } catch (error) { setExporting(failure(error, t('books.export'))); }
  }
  return <>
    <div className="bzToolbar bkPeriod">
      <label className="bzCompact" htmlFor={ids.preset}><span>{t('books.period')}</span>
        <select id={ids.preset} value={preset} onChange={(event) => choose(event.target.value as PeriodPreset | '')}>
          <option value="">{t('books.preset.custom')}</option>{PERIOD_PRESETS.map((item) => <option key={item} value={item}>{label(`books.preset.${item}`)}</option>)}</select></label>
      <label className="bzCompact" htmlFor={ids.from}><span>{t('books.from')}</span><input id={ids.from} type="date" value={period.from} max={period.to || undefined}
        onChange={(event) => { setPreset(''); setPeriod({ ...period, from: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.to}><span>{t('books.to')}</span><input id={ids.to} type="date" value={period.to} min={period.from || undefined}
        onChange={(event) => { setPreset(''); setPeriod({ ...period, to: event.target.value }); }} /></label>
      <label className="bzCompact" htmlFor={ids.basis}><span>{t('books.basis')}</span>
        <select id={ids.basis} value={basis} onChange={(event) => setBasis(event.target.value as BooksBasis)}>
          {(['accrual', 'cash'] as const).map((item) => <option key={item} value={item}>{basisLabel(item)}</option>)}</select></label>
      <div className="bzToolbarEnd"><button type="button" className="secondary" disabled={!value} onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('books.export')}</button></div>
    </div>
    {exporting && <Note error={exporting !== t('books.exported')}>{exporting}</Note>}
    {report.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {report.state === 'error' && <Failed text={report.message} onRetry={() => void load()} />}
    {value && reportIsEmpty(value) && <Empty title={t('books.emptyTitle')} text={t('books.emptyText')}
      action={<><button type="button" onClick={() => onSection('bills')}>{t('books.goBills')}</button><button type="button" className="secondary" onClick={() => onSection('allocations')}>{t('books.goAllocations')}</button></>} />}
    {value && !reportIsEmpty(value) && <>
      <BooksDashboard report={value} setup={setup} single={singleMonth(period)} onSection={onSection} />
      <h2 className="bkDetailsTitle">{t('books.page.details')}</h2>
      <ReportBody report={value} setup={setup} period={{ ...period, basis }} />
    </>}
  </>;
}

function ReportBody({ report, setup, period }: { report: BooksReport; setup: BooksSetup; period: api.Period }) {
  const base = report.base_currency;
  const rows = statementRows(report);
  const balances = report.balances;
  return <div className="bzOverview bkGrid">
    <section className="surface bzCard bkWide" aria-labelledby="bk-pnl"><h2 id="bk-pnl">{t('books.pnl.title', { basis: basisLabel(report.period.basis) })}</h2>
      <p className="muted bzHint">{t(report.period.basis === 'accrual' ? 'books.pnl.accrualHelp' : 'books.pnl.cashHelp', { from: dayText(report.period.from), to: dayText(report.period.to) })}</p>
      <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkStatement"><caption className="visuallyHidden">{t('books.pnl.caption')}</caption>
        <thead><tr><th scope="col">{t('books.col.line')}</th><th scope="col" className="num">{t('books.col.amount', { currency: base })}</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.key} className={`bk-${row.kind}`}><th scope="row">{row.label}</th>
          <td className={`num ${row.negative ? 'bkNeg' : ''}`}>{money(row.amount, base)}</td></tr>)}</tbody></table></div>
      {report.pnl.not_converted.length > 0 && <div className="bzGaps" role="note"><strong>{t('books.notConverted.title')}</strong>
        <ul>{report.pnl.not_converted.map((item) => <li key={item.currency}>{Object.entries(item.groups).map(([group, amount]) => `${label(`books.group.${group}`)}: ${money(amount, item.currency)}`).join(' · ')}</li>)}</ul>
        <p className="bzHint">{setup.permissions.manage ? t('books.notConverted.owner') : t('books.notConverted.member')}</p></div>}
      {report.allocations_in_review > 0 && <Note>{t('books.inReview', { count: report.allocations_in_review })}</Note>}
      {report.truncated && <Note>{t('books.truncated')}</Note>}
    </section>

    <section className="surface bzCard" aria-labelledby="bk-tax"><h2 id="bk-tax">{report.tax.label ? t('books.tax.titleNamed', { label: report.tax.label }) : t('books.tax.title')}</h2>
      {report.tax.by_currency.length === 0 ? <p className="muted">{t('books.tax.none')}</p> : <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable">
        <thead><tr><th scope="col">{t('books.col.currency')}</th><th scope="col" className="num">{t('books.tax.output')}</th><th scope="col" className="num">{t('books.tax.input')}</th><th scope="col" className="num">{t('books.tax.net')}</th></tr></thead>
        <tbody>{report.tax.by_currency.map((row) => <tr key={row.currency}><th scope="row">{row.currency}</th><td className="num">{money(row.output, row.currency)}</td>
          <td className="num">{money(row.input, row.currency)}</td><td className="num">{money(row.net, row.currency)}</td></tr>)}</tbody></table></div>}
      {report.tax.allocations_without_tax_split > 0 && <p className="muted bzHint">{t('books.tax.untracked', { count: report.tax.allocations_without_tax_split })}</p>}
      <p className="muted bzHint">{t('books.tax.disclaimer')}</p>
    </section>

    <section className="surface bzCard" aria-labelledby="bk-cash"><h2 id="bk-cash">{t('books.cash.title')}</h2>
      <p className="muted bzHint">{t('books.cash.help')}</p>
      {Object.keys(report.cash_flow.lines).length === 0 ? <p className="muted">{t('books.cash.none')}</p> : <dl className="bzFacts">
        {Object.entries(report.cash_flow.lines).map(([key, values]) => <div key={key}><dt>{label(`books.cash.${key}`)}</dt><dd>{moneyList(values)}</dd></div>)}
        <div><dt>{t('books.cash.net')}</dt><dd><strong>{moneyList(report.cash_flow.net)}</strong></dd></div></dl>}
      {Object.keys(report.cash_flow.paid_by_members).length > 0 && <><h3 className="bkSub">{t('books.cash.members')}</h3><dl className="bzFacts">
        {Object.entries(report.cash_flow.paid_by_members).map(([key, values]) => <div key={key}><dt>{label(`books.treatment.${key}`)}</dt><dd>{moneyList(values)}</dd></div>)}</dl></>}
    </section>

    <section className="surface bzCard" aria-labelledby="bk-owed"><h2 id="bk-owed">{t('books.owed.title', { day: dayText(balances.as_of) })}</h2>
      <dl className="bzFacts">
        <div><dt>{t('books.owed.receivables')}</dt><dd>{moneyList(balances.receivables.map((r) => ({ currency: r.currency, amount: r.total })))}</dd></div>
        <div><dt>{t('books.owed.payables')}</dt><dd>{moneyList(balances.payables.map((r) => ({ currency: r.currency, amount: r.total })))}</dd></div>
        <div><dt>{t('books.owed.members')}</dt><dd>{moneyList(balances.owed_to_members)}</dd></div>
        <div><dt>{t('books.owed.debt')}</dt><dd>{moneyList(balances.debt)}</dd></div>
        <div><dt>{t('books.owed.contributions')}</dt><dd>{moneyList(report.equity.contributions)}</dd></div>
        <div><dt>{t('books.owed.drawings')}</dt><dd>{moneyList(report.equity.drawings)}</dd></div>
      </dl>
      <Aging title={t('books.owed.receivableAge')} rows={balances.receivables} />
      <Aging title={t('books.owed.payableAge')} rows={balances.payables} />
      {balances.intercompany.length > 0 && <><h3 className="bkSub">{t('books.owed.intercompany')}</h3><ul className="bkList">{balances.intercompany.map((row) =>
        <li key={`${row.workspace_id}-${row.currency}`}><span dir="auto">{row.name || row.workspace_id}</span><span className={isNegative(row.amount) ? 'bkNeg' : ''}>
          {isNegative(row.amount) ? t('books.ic.weOwe', { amount: money(row.amount.slice(1), row.currency) }) : t('books.ic.theyOwe', { amount: money(row.amount, row.currency) })}</span></li>)}</ul></>}
    </section>

    <section className="surface bzCard" aria-labelledby="bk-invoices"><h2 id="bk-invoices">{t('books.outstanding.title')}</h2>
      {balances.outstanding_invoices.length === 0 ? <p className="muted">{t('books.outstanding.none')}</p> : <ul className="bkList">
        {balances.outstanding_invoices.slice(0, 10).map((row) => <li key={row.invoice_id}><span><a href={`#business-invoices/${encodeURIComponent(row.invoice_id)}`}>{row.number ?? row.invoice_id}</a> <small dir="auto">{row.account_name}</small></span>
          <span>{money(row.balance, row.currency)}{row.days_overdue > 0 && <> <Badge tone="dangerTone">{t('books.daysLate', { days: row.days_overdue })}</Badge></>}</span></li>)}</ul>}
      {balances.outstanding_invoices_count > 10 && <p className="muted bzHint">{t('books.outstanding.more', { count: balances.outstanding_invoices_count - 10 })}</p>}
    </section>

    <section className="surface bzCard bkWide" aria-labelledby="bk-trend"><h2 id="bk-trend">{t('books.trend.title', { currency: base })}</h2>
      <Trend report={report} /></section>

    <Breakdown id="bk-customers" title={t('books.customers.title')} empty={t('books.customers.none')} rows={report.customers} base={base} />
    <ProjectsBlock period={period} base={base} />
    <Breakdown id="bk-centres" title={t('books.centres.title')} empty={t('books.centres.none')} rows={report.cost_centres} base={base} />

    <section className="surface bzCard" aria-labelledby="bk-budgets"><h2 id="bk-budgets">{t('books.budgets.title')}</h2>
      {report.budgets.length === 0 ? <p className="muted">{t('books.budgets.none')}</p> : <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable">
        <thead><tr><th scope="col">{t('books.col.budget')}</th><th scope="col" className="num">{t('books.col.planned')}</th><th scope="col" className="num">{t('books.col.actual')}</th><th scope="col" className="num">{t('books.col.used')}</th></tr></thead>
        <tbody>{report.budgets.map((row) => <tr key={row.id}><th scope="row"><span dir="auto">{row.name}</span><small>{t('books.budgets.period', { from: dayText(row.period_start), to: dayText(row.period_end) })}</small></th>
          <td className="num">{money(row.budget, row.currency)}</td><td className="num">{row.comparable ? money(row.actual, row.currency) : t('books.budgets.otherCurrency')}</td>
          <td className={`num ${row.used_pct && Number(row.used_pct) > 100 ? 'bkNeg' : ''}`}>{pct(row.used_pct)}</td></tr>)}</tbody></table></div>}
    </section>

    <section className="surface bzCard" aria-labelledby="bk-recurring"><h2 id="bk-recurring">{t('books.recurring.title')}</h2>
      {report.recurring.items.length === 0 ? <p className="muted">{t('books.recurring.none')}</p> : <>
        <ul className="bkList">{report.recurring.items.slice(0, 12).map((row) => <li key={row.template_id ?? row.bill_id}><span dir="auto">{row.vendor_name} <small>{lineLabel(row.category)}</small>
          {row.status === 'paused' && <small>{t('books.rec.status.paused')}</small>}</span>
          <span>{t('books.recurring.perMonth', { amount: money(row.per_month, row.currency) })}{row.next_date && <small>{t('books.rec.nextOn', { day: dayText(row.next_date) })}</small>}</span></li>)}</ul>
        <p className="bzHint"><strong>{t('books.recurring.total', { amount: moneyList(report.recurring.per_month) })}</strong></p></>}
      <h3 className="bkSub">{t('books.rec.upcomingTitle')}</h3>
      <Upcoming items={report.recurring.upcoming ?? []} limit={5} />
    </section>

    <section className="surface bzCard bkWide" aria-labelledby="bk-rates"><h2 id="bk-rates">{t('books.rates.used')}</h2>
      {report.conversions.length === 0 ? <p className="muted">{t('books.rates.noneUsed', { currency: base })}</p> : <ul className="bkList">
        {report.conversions.map((row) => <li key={`${row.from_currency}-${row.as_of}-${row.rate}`}><span>{t('books.rates.line', { from: row.from_currency, rate: row.rate, to: row.to_currency })}</span>
          <small dir="auto">{t('books.rates.source', { day: dayText(row.as_of), source: row.source, count: row.figures })}</small></li>)}</ul>}
      {report.notes.length > 0 && <ul className="bkNotes">{report.notes.map((note) => <li key={note}>{label(`books.note.${note}`)}</li>)}</ul>}
    </section>
  </div>;
}

function Aging({ title, rows }: { title: string; rows: BooksReport['balances']['receivables'] }) {
  if (rows.length === 0) return null;
  const buckets = ['current', 'days_1_30', 'days_31_60', 'days_61_90', 'days_over_90'] as const;
  return <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkAging"><caption>{title}</caption>
    <thead><tr><th scope="col">{t('books.col.currency')}</th>{buckets.map((bucket) => <th key={bucket} scope="col" className="num">{label(`books.age.${bucket}`)}</th>)}</tr></thead>
    <tbody>{rows.map((row) => <tr key={row.currency}><th scope="row">{row.currency}</th>{buckets.map((bucket) => <td key={bucket} className="num">{row[bucket] ? money(row[bucket]!, row.currency) : '-'}</td>)}</tr>)}</tbody></table></div>;
}

function Trend({ report }: { report: BooksReport }) {
  const base = report.base_currency;
  const peak = Math.max(1, ...report.trend.map((row) => Math.max(Math.abs(Number(row.revenue)), Math.abs(Number(row.costs)))));
  return <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bkTrend"><caption className="visuallyHidden">{t('books.trend.caption')}</caption>
    <thead><tr><th scope="col">{t('books.col.month')}</th><th scope="col" className="num">{t('books.col.revenue')}</th><th scope="col" className="num">{t('books.col.costs')}</th><th scope="col" className="num">{t('books.col.profit')}</th><th scope="col" className="bzHideSm" aria-hidden="true" /></tr></thead>
    <tbody>{report.trend.map((row) => <tr key={row.month}><th scope="row">{row.month}{row.unconverted && <small>{t('books.trend.partial')}</small>}</th>
      <td className="num">{money(row.revenue, base)}</td><td className="num">{money(row.costs, base)}</td><td className={`num ${isNegative(row.profit) ? 'bkNeg' : ''}`}>{money(row.profit, base)}</td>
      <td className="bzHideSm" aria-hidden="true"><span className="bkBars"><span className="bkBar in" style={{ inlineSize: `${(Math.abs(Number(row.revenue)) / peak) * 100}%` }} />
        <span className="bkBar out" style={{ inlineSize: `${(Math.abs(Number(row.costs)) / peak) * 100}%` }} /></span></td></tr>)}</tbody></table></div>;
}

function Breakdown({ id, title, empty, rows, base }: { id: string; title: string; empty: string; rows: BooksReport['customers']; base: string }) {
  return <section className="surface bzCard" aria-labelledby={id}><h2 id={id}>{title}</h2>
    {rows.length === 0 ? <p className="muted">{empty}</p> : <div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable">
      <thead><tr><th scope="col">{t('books.col.name')}</th><th scope="col" className="num">{t('books.col.revenue')}</th><th scope="col" className="num">{t('books.col.costs')}</th><th scope="col" className="num">{t('books.col.margin')}</th></tr></thead>
      <tbody>{rows.slice(0, 10).map((row) => <tr key={row.id}><th scope="row"><span dir="auto">{row.name || row.id}</span></th><td className="num">{money(row.revenue, base)}</td>
        <td className="num">{money(row.costs, base)}</td><td className={`num ${isNegative(row.margin) ? 'bkNeg' : ''}`}>{money(row.margin, base)} <small>{pct(row.margin_pct)}</small></td></tr>)}</tbody></table></div>}
  </section>;
}

function ProjectsBlock({ period, base }: { period: api.Period; base: string }) {
  const [page, setPage] = useState<{ state: 'loading' } | { state: 'ready'; value: BooksProjectPage } | { state: 'error'; message: string }>({ state: 'loading' });
  const [offset, setOffset] = useState(0);
  const load = useCallback(async () => {
    setPage({ state: 'loading' });
    try { setPage({ state: 'ready', value: await api.listProjects(period, offset, 10) }); } catch (error) { setPage({ state: 'error', message: describeError(error) }); }
  }, [period.from, period.to, offset]);
  useEffect(() => { void load(); }, [load]);
  return <section className="surface bzCard bkWide" aria-labelledby="bk-projects"><h2 id="bk-projects">{t('books.projects.title')}</h2>
    <p className="muted bzHint">{t('books.projects.help')}</p>
    {page.state === 'loading' && <Note>{t('books.reading')}</Note>}
    {page.state === 'error' && <Note error>{page.message} <button type="button" className="secondary" onClick={() => void load()}>{t('common.tryAgain')}</button></Note>}
    {page.state === 'ready' && page.value.items.length === 0 && <p className="muted">{t('books.projects.none')}</p>}
    {page.state === 'ready' && page.value.items.length > 0 && <><div className="dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable">
      <thead><tr><th scope="col">{t('books.col.project')}</th><th scope="col" className="num">{t('books.projects.books', { currency: base })}</th><th scope="col" className="num bzHideSm">{t('books.projects.contract')}</th></tr></thead>
      <tbody>{page.value.items.map((row) => <tr key={row.id}><th scope="row"><a href={`#business-projects/${encodeURIComponent(row.id)}`} dir="auto">{row.name}</a></th>
        <td className="num">{t('books.projects.booksValue', { revenue: money(row.books.revenue, base), margin: money(row.books.margin, base) })}</td>
        <td className="num bzHideSm">{row.contract ? t('books.projects.contractValue', { margin: row.contract.gross_margin === null ? '-' : money(String(row.contract.gross_margin), row.contract.currency),
          collected: row.contract.cash_collected === null ? '-' : money(String(row.contract.cash_collected), row.contract.currency) }) : '-'}</td></tr>)}</tbody></table></div>
      {page.value.total > 10 && <div className="bzInline"><button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 10))}>{t('books.prev')}</button>
        <span className="muted">{t('books.pageOf', { from: offset + 1, to: Math.min(offset + 10, page.value.total), total: page.value.total })}</span>
        <button type="button" className="secondary" disabled={offset + 10 >= page.value.total} onClick={() => setOffset(offset + 10)}>{t('books.next')}</button></div>}</>}
  </section>;
}

// ---- bills ------------------------------------------------------------------------------------------------------------

/** A search box; Find is off while the text is the search already shown (`applied`), so it never does nothing. */
function SearchForm({ label: name, value, applied, onChange, onSubmit }: { label: string; value: string; applied: string; onChange: (value: string) => void; onSubmit: () => void }) {
  const id = useId();
  return <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
    <label className="visuallyHidden" htmlFor={id}>{name}</label>
    <input id={id} type="search" value={value} placeholder={name} onChange={(event) => onChange(event.target.value)} />
    <button type="submit" className="secondary" disabled={value.trim() === applied}><Search aria-hidden="true" />{t('biz.find')}</button>
  </form>;
}

function Bills({ setup }: { setup: BooksSetup }) {
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [status, setStatus] = useState<BooksBillFilter | ''>('');
  const [tick, setTick] = useState(0), [creating, setCreating] = useState(false), [openId, setOpenId] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ open: { currency: string; amount: string }[]; overdue: { currency: string; amount: string }[] } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const list = usePaged(async (offset) => { const page = await api.listBills(search, status, offset, BOOKS_PAGE); setSummary(page.summary); return page; }, [search, status, tick]);
  const statusId = useId();
  const filtered = Boolean(search || status);
  const write = setup.permissions.write;
  const exportCsv = async () => { setMessage(null); try { await api.exportBills(); setMessage(t('books.exported')); } catch (error) { setMessage(failure(error, t('books.export'))); } };
  return <>
    <div className="bzToolbar">
      <SearchForm label={t('books.bills.search')} value={q} applied={search} onChange={(value) => { setQ(value); if (!value) setSearch(''); }} onSubmit={() => setSearch(q.trim())} />
      <label className="bzCompact" htmlFor={statusId}><span>{t('books.bills.filter')}</span>
        <select id={statusId} value={status} onChange={(event) => setStatus(event.target.value as BooksBillFilter | '')}>
          <option value="">{t('books.bills.all')}</option>{BOOKS_BILL_FILTERS.map((item) => <option key={item} value={item}>{billFilterLabel(item)}</option>)}</select></label>
      <div className="bzToolbarEnd"><button type="button" className="secondary" onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('books.export')}</button>
        {write && <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('books.bills.new')}</button>}</div>
    </div>
    {message && <Note error={message !== t('books.exported')}>{message}</Note>}
    {summary && (summary.open.length > 0 || summary.overdue.length > 0) && <p className="bzHint">{t('books.bills.summary', { open: moneyList(summary.open), overdue: moneyList(summary.overdue) })}</p>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); setStatus(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('books.bills.emptyTitle')} text={write ? t('books.bills.emptyText') : t('books.readOnly')}
      action={write ? <button type="button" onClick={() => setCreating(true)}><Plus aria-hidden="true" />{t('books.bills.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <p className="bzCount" aria-live="polite">{t('books.bills.count', { count: list.total })}</p>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.bills')}</caption>
        <thead><tr><th scope="col">{t('books.col.bill')}</th><th scope="col">{t('books.col.state')}</th><th scope="col" className="num">{t('books.col.total')}</th><th scope="col" className="num">{t('books.col.balance')}</th><th scope="col" className="bzHideMd">{t('books.col.due')}</th></tr></thead>
        <tbody>{list.items.map((bill: BooksBill) => <tr key={bill.id} className={bill.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => setOpenId(bill.id)}><span dir="auto">{bill.vendor_name}</span></button>
            <small dir="auto">{bill.reference ? `${bill.reference}, ` : ''}{bill.description}</small></th>
          <td><Badge tone={paymentStateTone(bill.payment_state, bill.overdue)}>{paymentStateLabel(bill.payment_state)}</Badge>{bill.overdue && <> <Badge tone="dangerTone">{t('books.daysLate', { days: bill.days_overdue })}</Badge></>}
            {bill.template_id && <> <Badge tone="neutral">{t('books.rec.badge')}</Badge></>}</td>
          <td className="num">{money(bill.total, bill.currency)}</td><td className="num">{bill.status === 'void' ? '-' : money(bill.balance, bill.currency)}</td>
          <td className="bzHideMd nowrap">{dayText(bill.due_date)}</td></tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {creating && <BillDialog setup={setup} onClose={() => setCreating(false)} onSaved={(bill) => { setCreating(false); setTick((n) => n + 1); setOpenId(bill.id); }} />}
    {openId && <BillDrawer setup={setup} billId={openId} onClose={() => setOpenId(null)} onChanged={() => setTick((n) => n + 1)} />}
  </>;
}

// ---- vendors ----------------------------------------------------------------------------------------------------------

function Vendors({ setup }: { setup: BooksSetup }) {
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [archived, setArchived] = useState(false), [tick, setTick] = useState(0);
  const [editing, setEditing] = useState<BooksVendor | 'new' | null>(null);
  const list = usePaged((offset) => api.listVendors(search, archived, offset, BOOKS_PAGE), [search, archived, tick]);
  const write = setup.permissions.write;
  const filtered = Boolean(search || archived);
  return <>
    <div className="bzToolbar">
      <SearchForm label={t('books.vendors.search')} value={q} applied={search} onChange={(value) => { setQ(value); if (!value) setSearch(''); }} onSubmit={() => setSearch(q.trim())} />
      <label className="bzCheck"><input type="checkbox" checked={archived} onChange={(event) => setArchived(event.target.checked)} />{t('books.showArchived')}</label>
      <div className="bzToolbarEnd">{write && <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('books.vendors.new')}</button>}</div>
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); setArchived(false); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('books.vendors.emptyTitle')} text={write ? t('books.vendors.emptyText') : t('books.readOnly')}
      action={write ? <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('books.vendors.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.vendors')}</caption>
        <thead><tr><th scope="col">{t('books.col.name')}</th><th scope="col" className="bzHideSm">{t('books.col.taxNumber')}</th><th scope="col">{t('books.col.category')}</th><th scope="col" className="bzHideMd">{t('books.col.country')}</th></tr></thead>
        <tbody>{list.items.map((vendor: BooksVendor) => <tr key={vendor.id}><th scope="row">{write ? <button type="button" className="linkButton" onClick={() => setEditing(vendor)}><span dir="auto">{vendor.name}</span></button> : <span dir="auto">{vendor.name}</span>}
          {vendor.archived && <small>{t('books.archived')}</small>}</th>
          <td className="bzHideSm" dir="ltr">{vendor.tax_number || '-'}</td><td>{vendor.default_category ? lineLabel(vendor.default_category) : '-'}{vendor.default_section && <small>{sectionLabel(vendor.default_section)}</small>}</td>
          <td className="bzHideMd">{vendor.country ?? '-'}</td></tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {editing && <VendorDialog vendor={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setTick((n) => n + 1); }} />}
  </>;
}

// ---- allocations ------------------------------------------------------------------------------------------------------

const ALLOCATION_FILTERS: BooksAllocationFilter[] = ['active', 'needs_review', 'unpaid_bill', 'untagged'];

function Allocations({ setup }: { setup: BooksSetup }) {
  const [q, setQ] = useState(''), [search, setSearch] = useState(''), [status, setStatus] = useState<BooksAllocationFilter | ''>(''), [tick, setTick] = useState(0);
  const [tagging, setTagging] = useState<BooksAllocation | null>(null);
  const list = usePaged((offset) => api.listAllocations(status, search, offset, BOOKS_PAGE), [search, status, tick]);
  const statusId = useId();
  const filtered = Boolean(search || status);
  const centres = new Map(setup.cost_centres.map((item: BooksCostCentre) => [item.id, item]));
  return <>
    <p className="muted bzHint">{t('books.alloc.help')}</p>
    <div className="bzToolbar">
      <SearchForm label={t('books.alloc.search')} value={q} applied={search} onChange={(value) => { setQ(value); if (!value) setSearch(''); }} onSubmit={() => setSearch(q.trim())} />
      <label className="bzCompact" htmlFor={statusId}><span>{t('books.alloc.filter')}</span>
        <select id={statusId} value={status} onChange={(event) => setStatus(event.target.value as BooksAllocationFilter | '')}>
          <option value="">{t('books.alloc.all')}</option>{ALLOCATION_FILTERS.map((item) => <option key={item} value={item}>{label(`books.alloc.filter.${item}`)}</option>)}</select></label>
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setQ(''); setSearch(''); setStatus(''); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('books.alloc.emptyTitle')} text={t('books.alloc.emptyText')}
      action={<a className="button secondary" href="#finance">{t('books.alloc.goFinance')}</a>} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.allocations')}</caption>
        <thead><tr><th scope="col">{t('books.col.allocation')}</th><th scope="col" className="num">{t('books.col.amount', { currency: '' }).trim()}</th><th scope="col">{t('books.col.countsAs')}</th><th scope="col" className="bzHideMd">{t('books.col.date')}</th></tr></thead>
        <tbody>{list.items.map((row: BooksAllocation) => <tr key={row.id}><th scope="row">
          {setup.permissions.write ? <button type="button" className="linkButton" onClick={() => setTagging(row)}>{lineLabel(row.category)}</button> : lineLabel(row.category)}
          <small dir="auto">#{row.source_ref}{row.memo ? `, ${row.memo}` : ''}</small></th>
          <td className="num">{money(row.amount, row.currency)}</td>
          <td><div className="invBadges">{row.status === 'needs_review' ? <Badge tone="warning">{t('books.alloc.review')}</Badge> : <Badge tone={row.section === 'excluded' ? 'neutral' : 'success'}>{sectionLabel(row.section)}</Badge>}
            {row.paying_bill_id && <Badge tone="neutral">{t('books.alloc.paysBill')}</Badge>}
            <Badge tone="neutral">{label(`books.treatment.${row.treatment}`)}</Badge>
            {row.tag?.cost_centre_id && centres.get(row.tag.cost_centre_id) && <Badge tone="neutral">{centres.get(row.tag.cost_centre_id)!.code}</Badge>}</div></td>
          <td className="bzHideMd nowrap">{dayText(row.posted_on)}</td></tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {tagging && <TagDialog setup={setup} allocation={tagging} onClose={() => setTagging(null)} onSaved={() => { setTagging(null); setTick((n) => n + 1); }} />}
  </>;
}

// ---- budgets ----------------------------------------------------------------------------------------------------------

function Budgets({ setup }: { setup: BooksSetup }) {
  const [tick, setTick] = useState(0), [editing, setEditing] = useState<BooksBudget | 'new' | null>(null), [removing, setRemoving] = useState<BooksBudget | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState<string | null>(null);
  const list = usePaged((offset) => api.listBudgets(offset, BOOKS_PAGE), [tick]);
  const write = setup.permissions.write;
  async function remove() {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteBudget(removing.id); setRemoving(null); setTick((n) => n + 1); setMessage(t('books.budget.removed')); }
    catch (error) { setMessage(failure(error, t('books.budget.remove'))); } finally { setBusy(false); }
  }
  return <>
    <div className="bzToolbar"><p className="muted bzHint">{t('books.budget.help', { currency: setup.settings!.base_currency })}</p>
      <div className="bzToolbarEnd">{write && <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('books.budget.new')}</button>}</div></div>
    {message && <Note error={message !== t('books.budget.removed')}>{message}</Note>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={false} onRetry={() => void list.first()} onClear={() => undefined} />
    {list.load.kind === 'ready' && list.items.length === 0 && <Empty title={t('books.budget.emptyTitle')} text={write ? t('books.budget.emptyText') : t('books.readOnly')}
      action={write ? <button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('books.budget.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.budgets')}</caption>
        <thead><tr><th scope="col">{t('books.col.budget')}</th><th scope="col">{t('books.col.covers')}</th><th scope="col" className="num">{t('books.col.planned')}</th>{write && <th scope="col"><span className="visuallyHidden">{t('books.col.actions')}</span></th>}</tr></thead>
        <tbody>{list.items.map((row: BooksBudget) => <tr key={row.id}><th scope="row">{write ? <button type="button" className="linkButton" onClick={() => setEditing(row)}><span dir="auto">{row.name}</span></button> : <span dir="auto">{row.name}</span>}
          <small>{t('books.budget.months', { start: dayText(row.period_start), months: row.months })}</small></th>
          <td>{[row.section ? sectionLabel(row.section) : null, row.category ? lineLabel(row.category) : null, row.cost_centre_id ? setup.cost_centres.find((c) => c.id === row.cost_centre_id)?.code : null,
            row.project_id ? t('books.budget.oneProject') : null].filter(Boolean).join(' · ') || t('books.budget.allCosts')}</td>
          <td className="num">{money(row.amount, row.currency)}</td>
          {write && <td><button type="button" className="secondary" onClick={() => setRemoving(row)} aria-label={t('books.budget.removeLabel', { name: row.name })}>{t('books.budget.remove')}</button></td>}</tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {editing && <BudgetDialog setup={setup} budget={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setTick((n) => n + 1); }} />}
    {removing && <ConfirmDialog title={t('books.budget.removeTitle')} confirmLabel={t('books.budget.remove')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('books.budget.removeText', { name: removing.name })}</p></ConfirmDialog>}
  </>;
}

// ---- journal adjustments ----------------------------------------------------------------------------------------------

function Journal({ setup }: { setup: BooksSetup }) {
  const [kind, setKind] = useState<BooksAdjustmentKind | ''>(''), [includeVoid, setIncludeVoid] = useState(false), [tick, setTick] = useState(0);
  const [adding, setAdding] = useState(false), [voiding, setVoiding] = useState<BooksAdjustment | null>(null);
  const list = usePaged((offset) => api.listAdjustments(kind, includeVoid, offset, BOOKS_PAGE), [kind, includeVoid, tick]);
  const kindId = useId();
  const manage = setup.permissions.account ?? setup.permissions.manage;  // owners and accountants
  const filtered = Boolean(kind || includeVoid);
  return <>
    <p className="muted bzHint">{t('books.journal.help')}</p>
    <div className="bzToolbar">
      <label className="bzCompact" htmlFor={kindId}><span>{t('books.journal.kind')}</span>
        <select id={kindId} value={kind} onChange={(event) => setKind(event.target.value as BooksAdjustmentKind | '')}>
          <option value="">{t('books.journal.allKinds')}</option>{BOOKS_ADJUSTMENT_KINDS.map((item) => <option key={item} value={item}>{adjustmentLabel(item)}</option>)}</select></label>
      <label className="bzCheck"><input type="checkbox" checked={includeVoid} onChange={(event) => setIncludeVoid(event.target.checked)} />{t('books.showVoid')}</label>
      <div className="bzToolbarEnd">{manage && <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('books.journal.new')}</button>}</div>
    </div>
    {!manage && <Note>{t('ledger.accountantsOnly')}</Note>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setKind(''); setIncludeVoid(false); }} />
    {list.load.kind === 'ready' && list.items.length === 0 && !filtered && <Empty title={t('books.journal.emptyTitle')} text={t('books.journal.emptyText')}
      action={manage ? <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('books.journal.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.journal')}</caption>
        <thead><tr><th scope="col">{t('books.col.adjustment')}</th><th scope="col" className="num">{t('books.col.total')}</th><th scope="col" className="bzHideMd">{t('books.col.date')}</th>{manage && <th scope="col"><span className="visuallyHidden">{t('books.col.actions')}</span></th>}</tr></thead>
        <tbody>{list.items.map((row: BooksAdjustment) => <tr key={row.id}><th scope="row">{adjustmentLabel(row.kind)}{row.status === 'void' && <> <Badge tone="neutral">{t('books.void')}</Badge></>}
          <small dir="auto">{row.description}{row.months ? `, ${t('books.journal.spread', { months: row.months, start: dayText(row.start_month) })}` : ''}{row.reverse_on ? `, ${t('books.journal.reverses', { day: dayText(row.reverse_on) })}` : ''}</small>
          {row.status === 'void' && <small dir="auto">{t('books.voidReason', { reason: row.void_reason })}</small>}</th>
          <td className="num">{money(row.amount, row.currency)}</td><td className="bzHideMd nowrap">{dayText(row.entry_date)}</td>
          {manage && <td>{row.status === 'active' && <button type="button" className="secondary" onClick={() => setVoiding(row)} aria-label={t('books.voidLabel', { what: adjustmentLabel(row.kind) })}>{t('books.voidAction')}</button>}</td>}</tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {adding && <AdjustmentDialog setup={setup} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); setTick((n) => n + 1); }} />}
    {voiding && <ReasonDialog title={t('books.journal.voidTitle')} label={t('books.reason')} confirm={t('books.voidAction')}
      onConfirm={async (reason) => { await api.voidAdjustment(voiding.id, reason); setVoiding(null); setTick((n) => n + 1); }} onCancel={() => setVoiding(null)} />}
  </>;
}

// ---- intercompany -----------------------------------------------------------------------------------------------------

function Intercompany({ setup }: { setup: BooksSetup }) {
  const [includeVoid, setIncludeVoid] = useState(false), [tick, setTick] = useState(0), [adding, setAdding] = useState(false);
  const [voiding, setVoiding] = useState<BooksIntercompany | null>(null);
  const list = usePaged((offset) => api.listIntercompany(includeVoid, offset, BOOKS_PAGE), [includeVoid, tick]);
  const manage = setup.permissions.manage;
  return <>
    <p className="muted bzHint">{t('books.ic.help')}</p>
    <div className="bzToolbar">
      <label className="bzCheck"><input type="checkbox" checked={includeVoid} onChange={(event) => setIncludeVoid(event.target.checked)} />{t('books.showVoid')}</label>
      <div className="bzToolbarEnd">{manage && <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('books.ic.new')}</button>}</div>
    </div>
    {!manage && <Note>{t('books.ownersOnly')}</Note>}
    <ListState load={list.load} empty={list.items.length === 0} filtered={includeVoid} onRetry={() => void list.first()} onClear={() => setIncludeVoid(false)} />
    {list.load.kind === 'ready' && list.items.length === 0 && !includeVoid && <Empty title={t('books.ic.emptyTitle')} text={t('books.ic.emptyText')}
      action={manage ? <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('books.ic.new')}</button> : undefined} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <div className="pane dataTableWrap" tabIndex={0} role="region" aria-label={t('books.table')}><table className="dataTable bzTable"><caption className="visuallyHidden">{t('books.tab.intercompany')}</caption>
        <thead><tr><th scope="col">{t('books.col.entry')}</th><th scope="col" className="num">{t('books.col.total')}</th><th scope="col" className="bzHideMd">{t('books.col.date')}</th>{manage && <th scope="col"><span className="visuallyHidden">{t('books.col.actions')}</span></th>}</tr></thead>
        <tbody>{list.items.map((row: BooksIntercompany) => <tr key={row.id}><th scope="row">{label(`books.ic.kind.${row.kind}`)}: <span dir="auto">{row.counterparty_name || row.counterparty_workspace_id}</span>
          {row.status === 'void' && <> <Badge tone="neutral">{t('books.void')}</Badge></>}<small>{label(`books.ic.dir.${row.kind}.${row.direction}`)}</small><small dir="auto">{row.description}</small></th>
          <td className="num">{money(row.amount, row.currency)}</td><td className="bzHideMd nowrap">{dayText(row.entry_date)}</td>
          {manage && <td>{row.status === 'active' && <button type="button" className="secondary" onClick={() => setVoiding(row)} aria-label={t('books.voidLabel', { what: row.description })}>{t('books.voidAction')}</button>}</td>}</tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {adding && <IntercompanyDialog setup={setup} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); setTick((n) => n + 1); }} />}
    {voiding && <ReasonDialog title={t('books.ic.voidTitle')} label={t('books.reason')} confirm={t('books.voidAction')}
      onConfirm={async (reason) => { await api.voidIntercompany(voiding.id, reason); setVoiding(null); setTick((n) => n + 1); }} onCancel={() => setVoiding(null)} />}
  </>;
}

