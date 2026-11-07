// Finance, Bills and budgets (docs/finance.md#bills-and-budgets), from the owner's mockup of 5 Nov 2026.
// Bills are forecasts until a recorded payment is matched to a due date: the status of each due date
// (Planned, Due soon, Overdue, Review, Paid) comes from the API, which marks a date paid only when a
// matched payment exists. Budgets are the person's Guardian category caps: a new or changed cap is
// confirmed with its code like every rule; the alert threshold per budget is saved with the plan.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, CalendarDays, Check, ChevronLeft, ChevronRight, Gauge, Layers, Link2, Plus, Repeat, Search, Wallet } from 'lucide-react';
import type {
  FinanceAccount, FinanceBill, FinanceBillOccurrence, FinanceBillSchedule, FinanceBillSuggestion, FinanceBudgetAlert, FinanceBudgetLine, FinanceBudgetReport,
  FinanceCategory, FinanceGuardian,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import * as plan from './lib/financePlanningApi';
import { categoryLabel, formatAmount, isolate, minorDigits, monthLabel, type FinanceTab } from './lib/finance';
import { monthRange } from './lib/financeNav';
import { budgetAtRisk, budgetKpis, budgetTone, billKpis, calendarWeeks, dayStrip, pickTotal, statusTone, type CurrencyTotal } from './lib/financeBills';
import { toMinor } from './lib/financeDebtPlan';
import { describeError } from './lib/errors';
import { formatDateTime, getLocale, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, PeriodPicker, StatusChip, tabPanel } from './FinanceShell';
import { BillForm } from './FinancePlanningForms';
import { BudgetSpendChart } from './FinancePlanningCharts';
import { BillsCsvImport } from './FinanceBillsCsvImport';
import { FinanceInstallmentStatement } from './FinanceInstallmentStatement';
import { CapDialog, MatchDialog, PersonalScope, Progress, RowMenu } from './FinancePlanningParts';
import './financePlanning.css';

type Section = 'bills' | 'budgets' | 'recurring' | 'calendar';
const SECTIONS: readonly Section[] = ['bills', 'budgets', 'recurring', 'calendar'];
const THRESHOLDS = [50, 60, 70, 75, 80, 85, 90, 95, 99];

const longDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const weekday = (day: string) => formatDateTime(`${day}T12:00:00Z`, { weekday: 'short', timeZone: 'UTC' });
const totalText = (row: CurrencyTotal | null, fallbackCurrency: string) => formatAmount(row?.amount ?? '0', row?.currency ?? fallbackCurrency);

export function BillsBudgetsPage({ initial, accounts, base, today, onOpen, onChanged }: {
  initial: 'bills' | 'budgets'; accounts: FinanceAccount[]; base: string; today: string; onOpen: (tab: FinanceTab) => void; onChanged: () => void;
}) {
  const [section, setSection] = useState<Section>(initial);
  const [month, setMonth] = useState(today.slice(0, 7));
  const [schedule, setSchedule] = useState<Load<FinanceBillSchedule>>({ kind: 'loading' });
  const [report, setReport] = useState<Load<FinanceBudgetReport>>({ kind: 'loading' });
  const [bills, setBills] = useState<Load<FinanceBill[]>>({ kind: 'loading' });
  const [guardian, setGuardian] = useState<FinanceGuardian | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [editing, setEditing] = useState<FinanceBill | 'new' | null>(null), [removing, setRemoving] = useState<FinanceBill | null>(null), [busy, setBusy] = useState(false);
  const [matching, setMatching] = useState<FinanceBillOccurrence | null>(null);
  const [importing, setImporting] = useState(false);
  const [statementReload, setStatementReload] = useState(0);
  const [cap, setCap] = useState<{ category: FinanceCategory; currency: string; amount: string } | 'new' | null>(null);

  const loadSchedule = useCallback(async () => {
    const { from, to } = monthRange(month);
    try { setSchedule({ kind: 'ready', value: await plan.billSchedule(from, to) }); } catch (e) { setSchedule(failed(e)); }
  }, [month]);
  const loadReport = useCallback(async () => { try { setReport({ kind: 'ready', value: await plan.budgets(month) }); } catch (e) { setReport(failed(e)); } }, [month]);
  const loadBills = useCallback(async () => { try { setBills({ kind: 'ready', value: await api.listBills() }); } catch (e) { setBills(failed(e)); } }, []);
  const loadGuardian = useCallback(async () => { try { setGuardian(await api.getGuardian()); } catch { setGuardian(null); } }, []);
  useEffect(() => { void loadSchedule(); }, [loadSchedule]);
  useEffect(() => { void loadReport(); }, [loadReport]);
  useEffect(() => { void loadBills(); void loadGuardian(); }, [loadBills, loadGuardian]);
  const reloadBills = () => { void loadSchedule(); void loadBills(); onChanged(); };

  async function saveAlerts(next: FinanceBudgetAlert[]) {
    try { await plan.saveBudgetAlerts(next); setNotice({ text: t('fplan.rules.saved') }); await loadReport(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  async function unmatch(row: FinanceBillOccurrence) {
    try {
      for (const link of row.link_ids) await plan.unmatchBill(row.bill_id, link);
      setNotice({ text: t('fplan.bill.unmatched', { name: row.name }) }); await loadSchedule();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  async function confirmRemove() {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteBill(removing.id); setNotice({ text: t('finance.item.deleted', { name: removing.name }) }); reloadBills(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); } finally { setBusy(false); setRemoving(null); }
  }
  async function toggleBill(bill: FinanceBill) {
    try {
      await api.updateBill(bill.id, { name: bill.name, amount: bill.amount, currency: bill.currency, cadence: bill.cadence, next_due_on: bill.next_due_on, category: bill.category,
        account_id: bill.account_id, source: bill.source, active: !bill.active, notes: bill.notes });
      setNotice({ text: bill.active ? t('fplan.bill.pausedNow', { name: bill.name }) : t('fplan.bill.resumed', { name: bill.name }) }); reloadBills();
    } catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  const openBill = (billId: string) => {
    const found = bills.kind === 'ready' ? bills.value.find((b) => b.id === billId) : undefined;
    if (found) setEditing(found);
  };

  const crumbs = [{ label: t('finance.tab.overview'), href: '#finance', onClick: () => onOpen('overview') }, { label: t('fplan.bills.title') }];
  const head = <FinancePageHead title={t('fplan.bills.title')} subtitle={t('fplan.bills.subtitle')} crumbs={crumbs}
    actions={<><button type="button" onClick={() => setImporting(true)}>Import CSV</button><button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.bills.add')}</button></>}
    controls={<><PersonalScope /><PeriodPicker month={month} onChange={setMonth} /></>}
    tabs={<FinanceTabs label={t('fplan.bills.tabs')} idPrefix="fbb" value={section} onChange={setSection}
      items={SECTIONS.map((id) => ({ id, label: t(`fplan.bills.tab.${id}` as MessageKey) }))} />} />;

  if (schedule.kind === 'denied') return <div className="fplPage">{head}<Denied /></div>;
  if (schedule.kind === 'error') return <div className="fplPage">{head}<LoadError message={t('fplan.error.bills', { message: schedule.message })} retry={() => void loadSchedule()} /></div>;
  if (schedule.kind === 'loading') return <div className="fplPage">{head}<Notice busy>{t('fplan.loading')}</Notice></div>;
  const rows = schedule.value.items;
  const k = billKpis(rows);
  const budgetReady = report.kind === 'ready' ? report.value : null;
  const bk = budgetReady ? budgetKpis(budgetReady) : null;
  const risk = budgetReady ? budgetAtRisk(budgetReady) : null;
  const current = month === today.slice(0, 7);

  const upcoming = <UpcomingCard rows={rows} month={month} today={schedule.value.today} accounts={accounts} onAdd={() => setEditing('new')} onMatch={setMatching}
    onUnmatch={(row) => void unmatch(row)} onEdit={openBill} onRemove={(billId) => { const b = bills.kind === 'ready' ? bills.value.find((x) => x.id === billId) : undefined; if (b) setRemoving(b); }}
    onCalendar={() => setSection('calendar')} />;
  const health = <HealthCard report={report} base={base} retry={() => void loadReport()} onAdd={() => setCap('new')} />;
  const riskBanner = risk && <FinBanner tone="warn" icon={<AlertTriangle />}
    action={<button type="button" onClick={() => onOpen('transactions')}>{t('fplan.risk.review', { category: categoryLabel(risk.category) })}</button>}>
    {risk.state === 'over' ? t('fplan.risk.over', { category: categoryLabel(risk.category), amount: isolate(formatAmount(risk.over, risk.currency)) })
      : t('fplan.risk.pace', { category: categoryLabel(risk.category), amount: isolate(formatAmount(risk.projected_over ?? '0', risk.currency)) })}
  </FinBanner>;
  const rules = <RulesCard report={report} onAdd={() => setCap('new')} onEdit={(line) => setCap({ category: line.category, currency: line.currency, amount: line.cap })} onSave={saveAlerts}
    onRemove={async (line) => {
      if (!guardian) return;
      try {
        const change = await api.proposeRules({ ...guardian.rules, category_caps: guardian.rules.category_caps.filter((c) => !(c.category === line.category && c.currency === line.currency)) }, t('fplan.cap.reason'));
        setNotice({ text: t('fplan.cap.removeWaiting', { code: change.payload_hash.slice(0, 8) }) }); onOpen('guardian');
      } catch (e) { setNotice({ error: true, text: describeError(e) }); }
    }} />;

  return <div className="fplPage">
    {head}
    <KpiRow label={t('fplan.bills.kpis')}>
      <KpiCard icon={<CalendarDays />} label={t('fplan.kpi.upcoming')} value={totalText(pickTotal(k.month, base), base)}
        hint={rows.length ? t('fplan.kpi.billCount', { count: rows.length, month: monthLabel(month) }) : t('fplan.kpi.noBills')} />
      <KpiCard icon={<AlertTriangle />} label={current ? t('fplan.kpi.week') : t('fplan.kpi.overdue')} tone={(current ? k.week : k.overdue).length ? 'warn' : ''}
        value={totalText(pickTotal(current ? k.week : k.overdue, base), base)} hint={k.overdue.length && current ? t('fplan.kpi.overdueHint', { amount: k.overdue.map((r) => formatAmount(r.amount, r.currency)).join(' · ') }) : undefined} />
      <KpiCard icon={<Layers />} label={t('fplan.kpi.activeBudgets')} value={bk ? String(bk.active) : '-'} hint={bk && bk.active === 0 ? t('fplan.kpi.noBudgets') : undefined} />
      <KpiCard icon={<Gauge />} label={t('fplan.kpi.remaining')} value={bk && bk.remaining.length ? totalText(pickTotal(bk.remaining, base), base) : '-'}
        hint={bk && bk.remaining.length > 1 ? bk.remaining.filter((r) => r !== pickTotal(bk.remaining, base)).map((r) => formatAmount(r.amount, r.currency)).join(' · ') : undefined} />
    </KpiRow>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    <div {...tabPanel('fbb', section)}>
      {section === 'bills' && <>
        {bills.kind === 'ready' && <FinanceInstallmentStatement bills={bills.value} today={today} refreshKey={statementReload} onRefresh={() => void loadBills()} />}
        <div className="fplColumns wideStart"><div className="fplColumn">{upcoming}{rules}</div><div className="fplColumn">{health}{riskBanner}<LinkPayCard /></div></div>
      </>}
      {section === 'budgets' && <>{riskBanner}<div className="fplColumns"><div className="fplColumn">{health}</div><div className="fplColumn"><LinkPayCard /></div></div>{rules}</>}
      {section === 'recurring' && <RecurringCard bills={bills} accounts={accounts} retry={() => void loadBills()} onAdd={() => setEditing('new')} onEdit={setEditing}
        onToggle={(b) => void toggleBill(b)} onRemove={setRemoving} onKept={(name) => { setNotice({ text: t('finance.bills.kept', { name }) }); reloadBills(); }} />}
      {section === 'calendar' && <CalendarCard rows={rows} month={month} today={schedule.value.today} onPick={(row) => (row.status === 'paid' ? openBill(row.bill_id) : setMatching(row))} />}
    </div>
    {importing && <Modal title="Import installment bills from CSV" className="formDialog" onClose={() => setImporting(false)}>
      <BillsCsvImport existing={bills.kind === 'ready' ? bills.value : []} onClose={() => setImporting(false)}
        onDone={() => { reloadBills(); setStatementReload(n => n + 1); }} />
    </Modal>}
    {editing && <Modal title={editing === 'new' ? t('finance.bills.add') : t('finance.item.editTitle', { name: editing.name })} className="formDialog" onClose={() => setEditing(null)}>
      <BillForm bill={editing === 'new' ? null : editing} accounts={accounts} base={base} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); reloadBills(); } }} />
    </Modal>}
    {removing && <ConfirmDialog title={t('finance.item.deleteTitle', { name: removing.name })} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void confirmRemove()} onCancel={() => setRemoving(null)}>
      <p>{t('fplan.bill.deleteText')}</p>
    </ConfirmDialog>}
    {matching && <MatchDialog title={t('fplan.match.billTitle', { name: matching.name, date: longDate(matching.due_on) })} kind="bill" around={matching.due_on} amount={matching.expected}
      currency={matching.currency} accounts={accounts} onOpenTransactions={() => onOpen('transactions')}
      onMatch={async (candidate, key) => { await plan.matchBill(matching.bill_id, { canonical_id: candidate.canonical_id, due_on: matching.due_on }, key); }}
      onClose={(matched) => { setMatching(null); if (matched) { setNotice({ text: t('fplan.match.billDone', { name: matching.name }) }); void loadSchedule(); } }} />}
    {cap && guardian && <CapDialog rules={guardian.rules} existing={cap === 'new' ? null : cap} base={base}
      onClose={(changed) => { setCap(null); if (changed) { setNotice({ text: t('fplan.cap.saved') }); void loadGuardian(); void loadReport(); } }} />}
    {cap && !guardian && <Modal title={t('fplan.cap.add')} onClose={() => setCap(null)}><Notice error>{t('fplan.cap.noGuardian')}</Notice></Modal>}
  </div>;
}

// ---- Upcoming bills ---------------------------------------------------------------------------------

function UpcomingCard({ rows, month, today, accounts, onAdd, onMatch, onUnmatch, onEdit, onRemove, onCalendar }: {
  rows: FinanceBillOccurrence[]; month: string; today: string; accounts: FinanceAccount[]; onAdd: () => void; onMatch: (r: FinanceBillOccurrence) => void;
  onUnmatch: (r: FinanceBillOccurrence) => void; onEdit: (billId: string) => void; onRemove: (billId: string) => void; onCalendar: () => void;
}) {
  const [view, setView] = useState<'month' | 'list'>('month');
  const [day, setDay] = useState<string | null>(null);
  const [start, setStart] = useState(0);
  const names = new Map(accounts.map((a) => [a.id, a]));
  const { from, to } = monthRange(month);
  const strip = useMemo(() => dayStrip(from, to, rows, today), [from, to, rows, today]);
  useEffect(() => { setDay(null); const i = strip.findIndex((d) => d.today); setStart(i > 3 ? Math.min(i - 3, Math.max(0, strip.length - 14)) : 0); }, [month]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = view === 'month' && day ? rows.filter((r) => r.due_on === day) : rows;
  const k = billKpis(rows);
  const visible = strip.slice(start, start + 14);
  return <FinCard title={t('fplan.upcoming.title')} subtitle={rows.length ? t('fplan.upcoming.sub', { month: monthLabel(month), count: rows.length, total: k.month.map((r) => formatAmount(r.amount, r.currency)).join(' · ') }) : undefined}
    actions={<>
      <div className="finSegmented" role="radiogroup" aria-label={t('fplan.upcoming.view')}>
        {(['month', 'list'] as const).map((v) => <button key={v} type="button" role="radio" aria-checked={view === v} onClick={() => setView(v)}>{t(`fplan.upcoming.${v}` as MessageKey)}</button>)}
      </div>
      <button type="button" className="iconButton" onClick={onCalendar} aria-label={t('fplan.upcoming.openCalendar')}><CalendarDays aria-hidden="true" /></button>
    </>}>
    {view === 'month' && rows.length > 0 && <div className="fplStrip">
      <button type="button" className="iconButton small" disabled={start === 0} onClick={() => setStart(Math.max(0, start - 7))} aria-label={t('fplan.strip.earlier')}><ChevronLeft aria-hidden="true" className="cmFlip" /></button>
      <ol>{visible.map((d) => <li key={d.day}><button type="button" aria-pressed={day === d.day} className={`${d.today ? 'today' : ''} ${d.dueCount ? 'due' : ''}`}
        onClick={() => setDay(day === d.day ? null : d.day)}>
        {/* The visible weekday and date start the accessible name; the full date and the count follow, hidden. */}
        <small>{weekday(d.day)}</small><strong>{d.date}</strong><span className="visuallyHidden">{t('fplan.strip.day', { date: longDate(d.day), count: d.dueCount })}</span>
        {d.dueCount > 0 && <span className="fplDot" aria-hidden="true" />}</button></li>)}</ol>
      <button type="button" className="iconButton small" disabled={start + 14 >= strip.length} onClick={() => setStart(Math.min(strip.length - 14, start + 7))} aria-label={t('fplan.strip.later')}><ChevronRight aria-hidden="true" className="cmFlip" /></button>
    </div>}
    {rows.length === 0 ? <Empty icon={<CalendarDays aria-hidden="true" />} title={t('fplan.upcoming.empty')} text={t('fplan.upcoming.emptyText')}
      action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.bills.add')}</button>} />
      : shown.length === 0 ? <p className="muted">{t('fplan.upcoming.noneOnDay')}</p>
      : <div className="finTableWrap" tabIndex={0}><table className="finGrid fplBills">
        <thead><tr><th scope="col">{t('fplan.col.bill')}</th><th scope="col">{t('finance.col.category')}</th><th scope="col">{t('fplan.col.due')}</th><th scope="col" className="num">{t('fplan.col.expected')}</th>
          <th scope="col">{t('finance.col.account')}</th><th scope="col">{t('fplan.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th></tr></thead>
        <tbody>{shown.map((r) => {
          const account = r.account_id ? names.get(r.account_id) : undefined;
          return <tr key={`${r.bill_id}-${r.due_on}`}>
            <td><strong dir="auto">{r.name}</strong><small className="fplSub"><Repeat aria-hidden="true" />{t(`finance.cadence.${r.cadence}` as MessageKey)}</small></td>
            <td><span className="fplTag">{categoryLabel(r.category)}</span></td>
            <td><bdi>{longDate(r.due_on)}</bdi></td>
            <td className="num"><Money value={r.expected} currency={r.currency} /></td>
            <td dir="auto">{account ? `${account.name}${account.last4 ? ` (${account.last4})` : ''}` : <span className="muted">{t('fplan.bill.noAccount')}</span>}</td>
            <td><StatusChip tone={statusTone(r.status)}>{t(`fplan.status.${r.status}` as MessageKey)}</StatusChip>
              {r.status === 'paid' && r.paid_amount !== r.expected && <small className="fplPaid" dir="ltr">{formatAmount(r.paid_amount, r.currency)}</small>}</td>
            <td className="fplActions">
              {r.status === 'paid' || r.status === 'review'
                ? <button type="button" className="iconButton small" onClick={() => onUnmatch(r)} aria-label={t('fplan.bill.unmatch', { name: r.name })}><Link2 aria-hidden="true" /></button>
                : <button type="button" className="iconButton small" onClick={() => onMatch(r)} aria-label={t('fplan.bill.match', { name: r.name })}><Link2 aria-hidden="true" /></button>}
              <RowMenu label={t('fplan.menu', { name: r.name })} items={[
                ...(r.status === 'paid' || r.status === 'review' ? [{ label: t('fplan.bill.unmatchShort'), onSelect: () => onUnmatch(r) }] : [{ label: t('fplan.bill.matchShort'), onSelect: () => onMatch(r) }]),
                { label: t('finance.item.edit', { name: r.name }), onSelect: () => onEdit(r.bill_id) },
                { label: t('finance.item.delete', { name: r.name }), onSelect: () => onRemove(r.bill_id), danger: true },
              ]} />
            </td>
          </tr>;
        })}</tbody>
      </table></div>}
    <p className="muted fplNote"><Search aria-hidden="true" />{t('fplan.upcoming.forecast')}</p>
  </FinCard>;
}

// ---- Budget health ----------------------------------------------------------------------------------

function HealthCard({ report, base, retry, onAdd }: { report: Load<FinanceBudgetReport>; base: string; retry: () => void; onAdd: () => void }) {
  const value = report.kind === 'ready' ? report.value : null;
  const series = value ? value.series.find((s) => s.currency === base) ?? value.series[0] : undefined;
  return <FinCard title={t('fplan.health.title')} subtitle={value ? t('fplan.health.sub', { month: monthLabel(value.month) }) : undefined}>
    {report.kind === 'loading' && <Notice busy>{t('fplan.loading')}</Notice>}
    {report.kind === 'denied' && <Denied />}
    {report.kind === 'error' && <LoadError message={report.message} retry={retry} />}
    {value && (value.budgets.length === 0 ? <Empty icon={<Wallet aria-hidden="true" />} title={t('fplan.health.empty')} text={t('fplan.health.emptyText')}
      action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('fplan.cap.add')}</button>} /> : <>
      <ul className="fplHealth">{value.budgets.slice(0, 6).map((b) => <li key={`${b.category}-${b.currency}`}>
        <div className="fplHealthTop"><strong>{categoryLabel(b.category)}</strong><span dir="ltr">{formatAmount(b.spent, b.currency)} / {formatAmount(b.cap, b.currency)}</span>
          <StatusChip tone={budgetTone(b) === 'ok' ? 'ok' : budgetTone(b)}>{b.percent}%</StatusChip></div>
        <Progress percent={b.percent} tone={budgetTone(b)} label={t('fplan.health.aria', { category: categoryLabel(b.category), percent: b.percent })} />
        {b.state !== 'ok' && <small className={`fplHealthState ${b.state}`}>{t(`fplan.health.${b.state}` as MessageKey)}</small>}
      </li>)}</ul>
      {series && <div className="fplChartBlock">
        <div className="fplChartHead"><h3>{t('fplan.budgetChart.title')}</h3><span>{t('fplan.kpi.remaining')}: <strong dir="ltr">{formatAmount(String(Math.max(0, Number(series.budget) - Number(series.spent)).toFixed(minorDigits(series.currency))), series.currency)}</strong></span></div>
        <BudgetSpendChart points={series.points.map((p) => ({ day: p.day, spent: toMinor(p.spent, series.currency) }))} budget={toMinor(series.budget, series.currency)} currency={series.currency} digits={minorDigits(series.currency)} />
      </div>}
    </>)}
  </FinCard>;
}

// ---- Budget rules -------------------------------------------------------------------------------------

function RulesCard({ report, onAdd, onEdit, onSave, onRemove }: {
  report: Load<FinanceBudgetReport>; onAdd: () => void; onEdit: (line: FinanceBudgetLine) => void; onSave: (alerts: FinanceBudgetAlert[]) => Promise<void>; onRemove: (line: FinanceBudgetLine) => Promise<void>;
}) {
  const value = report.kind === 'ready' ? report.value : null;
  const alerts = (lines: FinanceBudgetLine[]): FinanceBudgetAlert[] => lines.map((l) => ({ category: l.category, currency: l.currency, alert_percent: l.alert_percent, alert_near: l.alert_near, alert_over: l.alert_over }));
  const change = (line: FinanceBudgetLine, patch: Partial<FinanceBudgetAlert>) => {
    if (!value) return;
    void onSave(alerts(value.budgets).map((a) => (a.category === line.category && a.currency === line.currency ? { ...a, ...patch } : a)));
  };
  return <FinCard title={t('fplan.rules.title')} subtitle={t('fplan.rules.sub')} actions={<button type="button" className="secondary" onClick={onAdd}><Plus aria-hidden="true" />{t('fplan.cap.add')}</button>}>
    {report.kind === 'loading' && <Notice busy>{t('fplan.loading')}</Notice>}
    {report.kind === 'error' && <p className="muted">{t('fplan.rules.error')}</p>}
    {value && (value.budgets.length === 0 ? <p className="muted">{t('fplan.rules.empty')}</p>
      : <div className="finTableWrap" tabIndex={0}><table className="finGrid fplRules">
        <thead><tr><th scope="col">{t('finance.col.category')}</th><th scope="col" className="num">{t('fplan.col.cap')}</th><th scope="col" className="num">{t('fplan.col.spent')}</th><th scope="col" className="num">{t('fplan.col.remaining')}</th>
          <th scope="col">{t('fplan.col.threshold')}</th><th scope="col">{t('fplan.col.alerts')}</th><th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th></tr></thead>
        <tbody>{value.budgets.map((b) => {
          const name = categoryLabel(b.category);
          return <tr key={`${b.category}-${b.currency}`}>
            <th scope="row">{name}{b.currency !== value.budgets[0].currency && <small className="muted"> {b.currency}</small>}</th>
            <td className="num"><Money value={b.cap} currency={b.currency} /></td>
            <td className="num"><Money value={b.spent} currency={b.currency} /></td>
            <td className="num"><Money value={b.remaining} currency={b.currency} /></td>
            <td><select value={b.alert_percent} aria-label={t('fplan.rules.thresholdAria', { category: name })} onChange={(e) => change(b, { alert_percent: Number(e.target.value) })}>
              {[...new Set([...THRESHOLDS, b.alert_percent])].sort((x, y) => x - y).map((p) => <option key={p} value={p}>{p}%</option>)}</select></td>
            <td className="fplChecks">
              <label><input type="checkbox" checked={b.alert_near} onChange={(e) => change(b, { alert_near: e.target.checked })} />{t('fplan.rules.alertAt', { percent: b.alert_percent })}</label>
              <label><input type="checkbox" checked={b.alert_over} onChange={(e) => change(b, { alert_over: e.target.checked })} />{t('fplan.rules.alertAt', { percent: 100 })}</label>
            </td>
            <td><RowMenu label={t('fplan.menu', { name })} items={[
              { label: t('fplan.cap.edit'), onSelect: () => onEdit(b) },
              { label: t('fplan.cap.remove'), onSelect: () => void onRemove(b), danger: true },
            ]} /></td>
          </tr>;
        })}</tbody>
      </table></div>)}
    <p className="muted finHelp">{t('fplan.rules.note')}</p>
  </FinCard>;
}

// ---- Link and pay -------------------------------------------------------------------------------------

function LinkPayCard() {
  const steps = [{ icon: Link2, key: 'attach' }, { icon: Search, key: 'match' }, { icon: Check, key: 'paid' }] as const;
  return <FinCard title={t('fplan.link.title')} icon={<Link2 />} subtitle={t('fplan.link.sub')}>
    <ol className="fplLinkSteps">{steps.map((s, i) => {
      const Icon = s.icon;
      return <li key={s.key}><span className="fplStepIcon" aria-hidden="true"><Icon /></span><div><strong>{i + 1}. {t(`fplan.link.${s.key}` as MessageKey)}</strong><small>{t(`fplan.link.${s.key}Text` as MessageKey)}</small></div>
        {i < steps.length - 1 && <ArrowRight aria-hidden="true" className="fplStepArrow cmFlip" />}</li>;
    })}</ol>
  </FinCard>;
}

// ---- Recurring ----------------------------------------------------------------------------------------

function RecurringCard({ bills, accounts, retry, onAdd, onEdit, onToggle, onRemove, onKept }: {
  bills: Load<FinanceBill[]>; accounts: FinanceAccount[]; retry: () => void; onAdd: () => void; onEdit: (b: FinanceBill) => void; onToggle: (b: FinanceBill) => void;
  onRemove: (b: FinanceBill) => void; onKept: (name: string) => void;
}) {
  const [suggestions, setSuggestions] = useState<FinanceBillSuggestion[]>([]);
  const [saving, setSaving] = useState<string | null>(null), [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState('');
  useEffect(() => { void api.billSuggestions().then(setSuggestions).catch(() => setSuggestions([])); }, [bills]);
  const names = new Map(accounts.map((a) => [a.id, a.name]));
  async function keep(s: FinanceBillSuggestion) {
    setSaving(s.name); setError(null);
    try { await api.createBill({ name: s.name, amount: s.amount, currency: s.currency, cadence: s.cadence, next_due_on: s.next_due_on, category: s.category, account_id: s.account_id, source: 'detected' }); onKept(s.name); }
    catch (e) { setError(describeError(e)); } finally { setSaving(null); }
  }
  const list = bills.kind === 'ready' ? bills.value.filter((b) => !q.trim() || b.name.toLowerCase().includes(q.trim().toLowerCase())) : [];
  return <>
    {suggestions.length > 0 && <FinCard title={t('finance.bills.suggestions')} subtitle={t('finance.bills.suggestionsText')}>
      {error && <Notice error>{error}</Notice>}
      <ul className="cmList">{suggestions.map((s) => <li key={`${s.account_id}-${s.name}`}>
        <div><strong dir="auto">{s.name}</strong><small>{t(`finance.cadence.${s.cadence}` as MessageKey)} · {t('finance.bills.seen', { count: s.occurrences })} · {t('finance.bills.next', { date: isolate(s.next_due_on) })}</small></div>
        <div className="finItemEnd"><Money value={s.amount} currency={s.currency} /><button type="button" className="secondary" disabled={saving === s.name} onClick={() => void keep(s)}><Plus aria-hidden="true" />{t('finance.bills.keep')}</button></div>
      </li>)}</ul>
    </FinCard>}
    <FinCard title={t('fplan.recurring.title')} actions={<button type="button" className="secondary" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.bills.add')}</button>}>
      {bills.kind === 'loading' && <Notice busy>{t('fplan.loading')}</Notice>}
      {bills.kind === 'denied' && <Denied />}
      {bills.kind === 'error' && <LoadError message={bills.message} retry={retry} />}
      {bills.kind === 'ready' && (bills.value.length === 0 ? <Empty icon={<Repeat aria-hidden="true" />} title={t('finance.bills.empty')} text={t('finance.bills.emptyText')}
        action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('finance.bills.add')}</button>} /> : <>
        {bills.value.length > 8 && <label className="cmSearch"><span className="visuallyHidden">{t('fplan.recurring.search')}</span><input type="search" value={q} placeholder={t('fplan.recurring.search')} onChange={(e) => setQ(e.target.value)} /></label>}
        <div className="finTableWrap" tabIndex={0}><table className="finGrid">
          <thead><tr><th scope="col">{t('fplan.col.bill')}</th><th scope="col">{t('finance.col.category')}</th><th scope="col">{t('fplan.col.recurring')}</th><th scope="col">{t('fplan.col.next')}</th>
            <th scope="col" className="num">{t('fplan.col.expected')}</th><th scope="col">{t('finance.col.account')}</th><th scope="col">{t('fplan.col.status')}</th><th scope="col"><span className="visuallyHidden">{t('finance.col.actions')}</span></th></tr></thead>
          <tbody>{list.map((b) => <tr key={b.id}>
            <td><strong dir="auto">{b.name}</strong>{b.source === 'detected' && <small className="muted"> · {t('finance.bills.detected')}</small>}</td>
            <td>{categoryLabel(b.category)}</td><td>{t(`finance.cadence.${b.cadence}` as MessageKey)}</td><td><bdi>{longDate(b.next_due_on)}</bdi></td>
            <td className="num"><Money value={b.amount} currency={b.currency} /></td><td dir="auto">{b.account_id ? names.get(b.account_id) ?? '-' : '-'}</td>
            <td><StatusChip tone={b.active ? 'ok' : 'muted'}>{b.active ? t('fplan.recurring.active') : t('finance.bills.paused')}</StatusChip></td>
            <td><RowMenu label={t('fplan.menu', { name: b.name })} items={[
              { label: t('finance.item.edit', { name: b.name }), onSelect: () => onEdit(b) },
              { label: b.active ? t('fplan.bill.pause') : t('fplan.bill.resume'), onSelect: () => onToggle(b) },
              { label: t('finance.item.delete', { name: b.name }), onSelect: () => onRemove(b), danger: true },
            ]} /></td>
          </tr>)}</tbody>
        </table></div>
      </>)}
    </FinCard>
  </>;
}

// ---- Calendar -----------------------------------------------------------------------------------------

function CalendarCard({ rows, month, today, onPick }: { rows: FinanceBillOccurrence[]; month: string; today: string; onPick: (row: FinanceBillOccurrence) => void }) {
  const weekStart = getLocale() === 'ar' ? 6 : 0;
  const weeks = calendarWeeks(month, weekStart);
  const byDay = new Map<string, FinanceBillOccurrence[]>();
  for (const r of rows) byDay.set(r.due_on, [...(byDay.get(r.due_on) ?? []), r]);
  const heads = weeks[0].map((_, i) => formatDateTime(Date.UTC(2026, 10, 1 + ((weekStart + i) % 7), 12), { weekday: 'short', timeZone: 'UTC' }));
  return <FinCard title={t('fplan.calendar.title', { month: monthLabel(month) })}>
    {rows.length === 0 && <p className="muted">{t('fplan.upcoming.emptyText')}</p>}
    <div className="finTableWrap" tabIndex={0}><table className="fplCalendar">
      <thead><tr>{heads.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
      <tbody>{weeks.map((week, w) => <tr key={w}>{week.map((day, i) => <td key={day ?? `pad-${w}-${i}`} className={day === today ? 'today' : undefined}>
        {day && <><span className="fplCalDate">{Number(day.slice(8))}</span>
          <ul>{(byDay.get(day) ?? []).map((r) => <li key={r.bill_id}><button type="button" className={`fplCalBill ${statusTone(r.status)}`} onClick={() => onPick(r)}
            aria-label={t('fplan.calendar.bill', { name: r.name, amount: formatAmount(r.expected, r.currency), status: t(`fplan.status.${r.status}` as MessageKey) })}>
            <span dir="auto">{r.name}</span><small dir="ltr">{formatAmount(r.expected, r.currency)}</small></button></li>)}</ul></>}
      </td>)}</tr>)}</tbody>
    </table></div>
    <p className="muted fplNote">{t('fplan.upcoming.forecast')}</p>
  </FinCard>;
}

