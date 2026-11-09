// Finance, Money advisor (docs/finance.md#money-advisor): this week's money note from GET /finance/advisor
// (where the money went, how long it would last if income stopped, coming payments, card advice and the
// ranked next steps), past weeks from GET /advisor/notes, "Generate now" (POST /advisor/generate) and the
// weekly switches (PUT /advisor/settings). Figures are the API's; ANUM only advises: it never moves money,
// pays a bill or touches a bank or card, and the page says so on every view.
import { lazy, Suspense, useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from 'react';
import {
  CalendarClock, CreditCard, Gauge, History, Lightbulb, ListChecks, PieChart, RefreshCw, Settings2, ShieldCheck, TriangleAlert, Wallet,
} from 'lucide-react';
import type {
  FinanceAdvisorCard, FinanceAdvisorCurrency, FinanceAdvisorNotePage, FinanceAdvisorNoteView, FinanceAdvisorOverview, FinanceAdvisorScenario,
} from '@anum/contracts';
import * as api from './lib/financeApi';
import { categoryLabel, formatAmount, isolate, minorDigits, type FinanceSection, type FinanceTab } from './lib/finance';
import {
  NOTES_PAGE, WEEKDAYS, actionTab, actionText, cardsDue, headlineScenario, monthsList, pageCount, pickCurrency, runwayTone, severityTone,
  sortActions, sumMoney, weekdayName,
} from './lib/financeAdvisor';
import { delta } from './lib/financeNav';
import { describeError } from './lib/errors';
import { formatDateTime, getLocale, intlLocale, t, type MessageKey } from './lib/i18n';
import { Denied, Empty, LoadError, Money, Notice, failed, type Load } from './FinanceParts';
import { DeltaChip, FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import './financePages.css';
import './financeAdvisor.css';

const AdvisorPlan = lazy(() => import('./FinanceAdvisorPlan').then((m) => ({ default: m.AdvisorPlan })));
// Salary and payslips sit here because the advisor is where income already shows: the salary it found, the one income
// figure and the "No salary found" step (docs/finance.md#salary).
const SalarySection = lazy(() => import('./FinanceSalary').then((m) => ({ default: m.SalarySection })));
type AdvisorView = 'note' | 'plan' | 'salary';

const day = (value: string) => formatDateTime(`${value}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

export function FinanceAdvisorPage({ onOpen, base = 'AED', initialView = 'note' }: {
  onOpen: (tab: FinanceTab, section?: FinanceSection) => void; base?: string; initialView?: AdvisorView;
}) {
  const [view, setView] = useState<AdvisorView>(initialView);
  const [overview, setOverview] = useState<Load<FinanceAdvisorOverview>>({ kind: 'loading' });
  const [shown, setShown] = useState<FinanceAdvisorNoteView | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);
  const [historyKey, setHistoryKey] = useState(0);

  const load = useCallback(() => {
    setOverview((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    api.getAdvisor().then((value) => { setOverview({ kind: 'ready', value }); setShown(value.latest); }).catch((e) => setOverview(failed(e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  const generate = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const made = await api.generateAdvisorNote();
      setShown(made);
      setOverview((current) => (current.kind === 'ready' ? { kind: 'ready', value: { ...current.value, latest: made } } : current));
      setHistoryKey((k) => k + 1);
      setNotice({ text: t('fadv.generated') });
    } catch (e) {
      setNotice({ text: t('fadv.generateFailed', { message: describeError(e) }), error: true });
    } finally {
      setBusy(false);
    }
  };

  const data = overview.kind === 'ready' ? overview.value : null;
  const note = shown?.note ?? null;
  const block = note ? pickCurrency(note, currency) : null;
  const latestId = data?.latest?.id ?? null;
  const viewingPast = Boolean(shown && latestId && shown.id !== latestId);

  const tabs = <FinanceTabs label={t('fplanx.views')} idPrefix="fadvv" value={view} onChange={setView}
    items={[{ id: 'note', label: t('fplanx.view.note') }, { id: 'plan', label: t('fplanx.view.plan') }, { id: 'salary', label: t('fplanx.view.salary') }]} />;
  if (view === 'salary') return <div className="finPage finAdvisorPage">
    <FinancePageHead title={t('finance.tab.advisor')} subtitle={t('fsal.profile.title')} tabs={tabs} />
    <div {...tabPanel('fadvv', 'salary')}><Suspense fallback={<Notice busy>{t('fsal.loading')}</Notice>}><SalarySection base={base} /></Suspense></div>
  </div>;
  if (view === 'plan') return <div className="finPage finAdvisorPage">
    <FinancePageHead title={t('finance.tab.advisor')} subtitle={t('fplanx.subtitle')} tabs={tabs} />
    <FinBanner tone="info" icon={<ShieldCheck />} title={t('fadv.onlyAdvises.title')}>{t('fplanx.onlyAdvises')} {t('fplanx.banner')}</FinBanner>
    <div {...tabPanel('fadvv', 'plan')}><Suspense fallback={<Notice busy>{t('fpm.loading')}</Notice>}><AdvisorPlan base={base} onOpen={onOpen} /></Suspense></div>
  </div>;

  const head = <FinancePageHead title={t('finance.tab.advisor')} subtitle={t('fadv.subtitle')} tabs={tabs}
    actions={overview.kind === 'ready' ? <button type="button" onClick={() => void generate()} disabled={busy} aria-busy={busy || undefined}>
      <RefreshCw aria-hidden="true" />{busy ? t('fadv.generating') : t('fadv.generate')}</button> : undefined}
    controls={shown ? <>
      <div className="finControl">
        <strong className="finStaticPeriod">{t('fadv.weekOf', { date: isolate(day(shown.week_start)) })}</strong>
        <small className="muted">{t('fadv.generatedAt', { time: formatDateTime(shown.generated_at) })}</small></div>
      {note && note.currencies.length > 1 && <label className="finControl"><span className="finControlLabel">{t('fadv.currency')}</span>
        <select value={block?.currency ?? ''} onChange={(e) => setCurrency(e.target.value)}>{note.currencies.map((c) => <option key={c.currency} value={c.currency}>{c.currency}</option>)}</select></label>}
    </> : undefined} />;

  let body: ReactNode;
  if (overview.kind === 'loading') body = <Notice busy>{t('fadv.loading')}</Notice>;
  else if (overview.kind === 'denied') body = <Denied />;
  else if (overview.kind === 'error') body = <LoadError message={t('fadv.error', { message: overview.message })} retry={load} />;
  else if (!note) body = <FinCard title={t('fadv.empty.title')} icon={<Lightbulb />}>
    <Empty icon={<Lightbulb aria-hidden="true" />} title={t('fadv.empty.title')} text={t('fadv.empty.text')}
      action={<div className="finCardButtons"><button type="button" onClick={() => void generate()} disabled={busy}>{t('fadv.generate')}</button>
        <button type="button" className="secondary" onClick={() => onOpen('import')}>{t('fadv.import')}</button></div>} />
  </FinCard>;
  else if (!note.has_accounts || !block) body = <FinCard title={t('fadv.noAccounts.title')} icon={<Wallet />}>
    <Empty icon={<Wallet aria-hidden="true" />} title={t('fadv.noAccounts.title')} text={t('fadv.noAccounts.text')}
      action={<div className="finCardButtons"><button type="button" onClick={() => onOpen('accounts')}>{t('fadv.addAccount')}</button>
        <button type="button" className="secondary" onClick={() => onOpen('import')}>{t('fadv.import')}</button></div>} />
  </FinCard>;
  // A step that points at the advisor itself (the salary check) opens its Salary view.
  else body = <NoteBody view={shown!} block={block} onOpen={(tab) => (tab === 'advisor' ? setView('salary') : onOpen(tab))} />;

  return <div className="finPage finAdvisorPage">
    {head}
    <FinBanner tone="info" icon={<ShieldCheck />} title={t('fadv.onlyAdvises.title')}>{t('fadv.onlyAdvises.text')}</FinBanner>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {viewingPast && shown && <FinBanner tone="info" icon={<History />} title={t('fadv.viewingPast', { date: isolate(day(shown.week_start)) })}
      action={<button type="button" className="secondary" onClick={() => setShown(data?.latest ?? null)}>{t('fadv.backToLatest')}</button>} />}
    <div {...tabPanel('fadvv', 'note')}>{body}</div>
    {data && <div className="finReportGrid finAdvisorGridBottom">
      <HistoryCard key={historyKey} shownId={shown?.id ?? null} onShow={setShown} />
      <SettingsCard overview={data} onSaved={load} />
    </div>}
  </div>;
}

function days(count: number | null): string {
  return count === null ? '-' : t('fadv.days', { count });
}

function NoteBody({ view, block, onOpen }: { view: FinanceAdvisorNoteView; block: FinanceAdvisorCurrency; onOpen: (tab: FinanceTab) => void }) {
  const note = view.note;
  const c = block.currency;
  const headline = headlineScenario(block.runway);
  const enough = block.runway.status === 'ok';
  const due = cardsDue(note.cards, c);
  const dueOwed = sumMoney(due.map((card) => card.owed), minorDigits(c));
  const actions = sortActions(note.actions.filter((a) => a.currency === null || a.currency === c));
  return <>
    <KpiRow label={t('fadv.kpis')}>
      <KpiCard icon={<Gauge />} label={t('fadv.kpi.survival')} value={days(headline?.days ?? null)} tone={runwayTone(headline?.days)}
        hint={headline?.days === null ? t('fadv.kpi.survivalNone') : enough ? t('fadv.kpi.survivalHint') : t('fadv.kpi.survivalBare')} />
      <KpiCard icon={<Wallet />} label={t('fadv.kpi.flow')} value={<Money value={block.income_month} currency={c} />}
        hint={t('fadv.kpi.flowHint', { amount: isolate(formatAmount(block.spend_month, c)) })} />
      <KpiCard icon={<CalendarClock />} label={t('fadv.kpi.coming')} value={<Money value={block.coming.total} currency={c} />}
        hint={t('fadv.kpi.comingHint', { count: block.coming.items.length })} tone={Number(block.coming.within_7_days) > 0 ? 'warn' : ''} />
      <KpiCard icon={<CreditCard />} label={t('fadv.kpi.cards')} value={String(due.length)} tone={due.some((card) => (card.days_until ?? 99) <= 7) ? 'warn' : ''}
        hint={due.length ? t('fadv.kpi.cardsHint', { amount: isolate(formatAmount(dueOwed, c)) }) : t('fadv.kpi.cardsNone')} />
    </KpiRow>
    <div className="finReportGrid finAdvisorGridTop">
      <FinCard title={t('fadv.actions.title')} subtitle={t('fadv.actions.subtitle')} icon={<ListChecks />} className="finAdvisorActions">
        {actions.length === 0 ? <p className="muted">{t('fadv.actions.empty')}</p> : <ol className="finAdvisorSteps">
          {actions.map((action, index) => {
            const tab = actionTab(action);
            return <li key={`${action.code}-${index}`}>
              <StatusChip tone={severityTone(action.severity)}>{t(`fadv.severity.${action.severity}` as MessageKey)}</StatusChip>
              <p dir="auto">{actionText(action, getLocale())}</p>
              {tab && <button type="button" className="secondary" onClick={() => onOpen(tab)}>{t('fadv.actions.open', { page: t(`finance.tab.${tab}` as MessageKey) })}</button>}
            </li>;
          })}
        </ol>}
      </FinCard>
      <RunwayCard block={block} cards={note.cards} />
    </div>
    <SpendingCard block={block} />
    <div className="finReportGrid finAdvisorGridMiddle">
      <ComingCard block={block} />
      <CardsCard cards={note.cards.filter((card) => card.currency === c)} />
    </div>
  </>;
}

function ScenarioRow({ scenario, currency }: { scenario: FinanceAdvisorScenario; currency: string }) {
  return <li className={`finScenario ${runwayTone(scenario.days)}`}>
    <div><strong>{t(`fadv.scenario.${scenario.kind}` as MessageKey)}</strong><small>{t(`fadv.scenario.${scenario.kind}Text` as MessageKey)}</small></div>
    <div className="finScenarioFigure">
      <strong>{days(scenario.days)}</strong>
      <small>{scenario.runs_out_on ? t('fadv.scenario.until', { date: isolate(day(scenario.runs_out_on)) }) : scenario.days === null ? t('fadv.scenario.never') : t('fadv.scenario.beyond')}</small>
      <small>{t('fadv.scenario.perMonth', { amount: isolate(formatAmount(scenario.monthly_outflow, currency)) })}</small>
    </div>
  </li>;
}

function RunwayCard({ block, cards }: { block: FinanceAdvisorCurrency; cards: FinanceAdvisorCard[] }) {
  const r = block.runway, c = block.currency;
  const limits = cards.filter((card) => card.currency === c && card.available !== null);
  return <FinCard title={t('fadv.runway.title')} subtitle={t('fadv.runway.subtitle')} icon={<Gauge />} className="finAdvisorRunway">
    <p className="finAdvisorLiquid">{t('fadv.runway.liquid', { amount: isolate(formatAmount(r.liquid, c)) })}</p>
    {r.status === 'not_enough_history' && <FinBanner tone="warn" icon={<TriangleAlert />} title={t('fadv.runway.history')}>{t('fadv.runway.historyText', { count: r.history_days })}</FinBanner>}
    {r.status === 'no_records' && <p className="muted">{t('fadv.runway.noRecords')}</p>}
    <ul className="finScenarios">{r.scenarios.map((s) => <ScenarioRow key={s.kind} scenario={s} currency={c} />)}</ul>
    <div className="finAssumptions"><strong>{t('fadv.assume.title')}</strong><ul>
      <li>{t('fadv.assume.liquid', { count: r.accounts_counted })}</li>
      <li>{block.card_available !== null && limits.length ? t('fadv.assume.cards', { amount: isolate(formatAmount(block.card_available, c)) }) : t('fadv.assume.noCards')}</li>
      {r.basis === 'months' && <li>{t('fadv.assume.months', { months: monthsList(r.months, intlLocale(getLocale())) })}</li>}
      {r.basis === 'last_30_days' && <li>{t('fadv.assume.last30')}</li>}
      <li>{t('fadv.assume.bills', { amount: isolate(formatAmount(r.bills_monthly, c)) })}</li>
      <li>{t('fadv.assume.minimums', { amount: isolate(formatAmount(r.minimums_monthly, c)) })}</li>
      {r.essential_spending_monthly !== null && <li>{t('fadv.assume.essential', { amount: isolate(formatAmount(r.essential_spending_monthly, c)) })}</li>}
      <li>{t('fadv.assume.days')}</li>
    </ul></div>
  </FinCard>;
}

function SpendingCard({ block }: { block: FinanceAdvisorCurrency }) {
  const s = block.spending, c = block.currency;
  const period = (label: string, p: typeof s.week) => {
    const previous = delta(p.amount, p.previous, false), average = p.average !== null ? delta(p.amount, p.average, false) : null;
    return <div className="finAdvisorPeriod">
      <span className="finControlLabel">{label}</span>
      <strong><Money value={p.amount} currency={c} /></strong>
      <span className="finAdvisorDeltas">
        {previous && <DeltaChip delta={previous} text={`${previous.percent === null ? t(`fshell.delta.${previous.direction}` as MessageKey) : t(`fshell.delta.pct.${previous.direction}` as MessageKey, { percent: Math.abs(previous.percent) })} ${t('fadv.spent.vsPrevious')}`} />}
        {average && <DeltaChip delta={average} text={`${average.percent === null ? t(`fshell.delta.${average.direction}` as MessageKey) : t(`fshell.delta.pct.${average.direction}` as MessageKey, { percent: Math.abs(average.percent) })} ${t('fadv.spent.vsAverage')}`} />}
      </span>
      {p.average !== null && <small className="muted">{t('fadv.spent.average', { amount: isolate(formatAmount(p.average, c)) })}</small>}
    </div>;
  };
  const payees = (title: string, rows: typeof s.payees_week) => <div><h3 className="finAdvisorSub">{title}</h3>
    {rows.length === 0 ? <p className="muted">{t('fadv.spent.none')}</p> : <ul className="finMoveList">{rows.map((p) => <li key={p.name}>
      <div><span dir="auto">{p.name}</span></div><Money value={p.amount} currency={c} /></li>)}</ul>}
  </div>;
  return <FinCard title={t('fadv.spent.title')} icon={<PieChart />} className="finAdvisorSpending">
    <div className="finAdvisorPeriods">{period(t('fadv.spent.week'), s.week)}{period(t('fadv.spent.month'), s.month)}</div>
    {s.spikes.length > 0 && <div className="finAdvisorSpikes"><h3 className="finAdvisorSub">{t('fadv.spent.spikes')}</h3><ul className="finRuleLines">
      {s.spikes.map((spike) => <li key={`${spike.category}-${spike.period}`} className="near"><TriangleAlert aria-hidden="true" /><span>{spike.period === 'new'
        ? t('fadv.spike.new', { category: categoryLabel(spike.category), amount: isolate(formatAmount(spike.amount, c)) })
        : t('fadv.spike.month', { category: categoryLabel(spike.category), amount: isolate(formatAmount(spike.amount, c)), percent: spike.percent ?? 0, expected: isolate(formatAmount(spike.expected, c)) })}</span></li>)}
    </ul></div>}
    <div className="finAdvisorSplit">
      <div><h3 className="finAdvisorSub">{t('fadv.spent.categories')}</h3>
        {s.categories.length === 0 ? <p className="muted">{t('fadv.spent.none')}</p> : <div className="finTableWrap" tabIndex={0}><table className="finGrid">
          <thead><tr><th scope="col">{t('fadv.spent.category')}</th><th scope="col" className="num">{t('fadv.spent.colWeek')}</th>
            <th scope="col" className="num">{t('fadv.spent.colMonth')}</th><th scope="col" className="num">{t('fadv.spent.colAverage')}</th></tr></thead>
          <tbody>{s.categories.map((row) => <tr key={row.category}><th scope="row">{categoryLabel(row.category)}</th>
            <td className="num"><Money value={row.week} currency={c} /></td><td className="num"><Money value={row.month} currency={c} /></td>
            <td className="num">{row.average_month === null ? '-' : <Money value={row.average_month} currency={c} />}</td></tr>)}</tbody>
        </table></div>}
      </div>
      <div className="finAdvisorPayees">{payees(t('fadv.spent.payees'), s.payees_week)}{payees(t('fadv.spent.payeesMonth'), s.payees_month)}</div>
    </div>
  </FinCard>;
}

function ComingCard({ block }: { block: FinanceAdvisorCurrency }) {
  const coming = block.coming, c = block.currency;
  return <FinCard title={t('fadv.coming.title')} subtitle={t('fadv.coming.subtitle')} icon={<CalendarClock />}>
    {coming.items.length === 0 ? <p className="muted">{t('fadv.coming.empty')}</p> : <>
      <ul className="finMoveList finAdvisorComing">{coming.items.map((item, index) => <li key={`${item.kind}-${item.ref_id ?? item.name}-${item.due_on ?? 'none'}-${index}`}>
        <div><span dir="auto">{item.name}</span>
          <small>{item.due_on === null ? t('fadv.coming.noDate') : item.days_until === 0 ? t('fadv.coming.today') : `${day(item.due_on)} · ${t('fadv.coming.inDays', { count: item.days_until ?? 0 })}`}</small></div>
        <StatusChip tone={item.detected ? 'muted' : item.days_until !== null && item.days_until <= 7 ? 'warn' : 'info'}>{t(`fadv.kind.${item.kind}` as MessageKey)}</StatusChip>
        <Money value={item.amount} currency={c} />
      </li>)}</ul>
      <p className="muted finFoot">{t('fadv.coming.saved', { amount: isolate(formatAmount(coming.saved_total, c)) })}</p>
      {Number(coming.detected_total) > 0 && <p className="muted finFoot">{t('fadv.coming.detected', { amount: isolate(formatAmount(coming.detected_total, c)) })}</p>}
    </>}
  </FinCard>;
}

function CardsCard({ cards }: { cards: FinanceAdvisorCard[] }) {
  return <FinCard title={t('fadv.cards.title')} subtitle={t('fadv.cards.subtitle')} icon={<CreditCard />}>
    {cards.length === 0 ? <p className="muted">{t('fadv.cards.empty')}</p> : <ul className="finAdvisorCards">{cards.map((card) => {
      const c = card.currency;
      return <li key={card.account_id ?? card.debt_id ?? card.name}>
        <strong dir="auto">{card.name}{card.last4 ? ` (${card.last4})` : ''}</strong>
        <dl className="finStatement">
          <div><dt>{card.owed_source === 'debt' ? t('fadv.cards.owedDebt') : t('fadv.cards.owed')}</dt><dd><Money value={card.owed} currency={c} /></dd></div>
          <div><dt>{t('fadv.cards.minimum')}</dt><dd>{card.minimum_payment === null ? '-' : <Money value={card.minimum_payment} currency={c} />}</dd></div>
          <div><dt>{t('fadv.cards.due')}</dt><dd>{card.due_on ? day(card.due_on) : '-'}</dd></div>
          <div><dt>{t('fadv.cards.rate')}</dt><dd>{card.apr_percent === null ? '-' : t('fadv.cards.rateValue', { rate: isolate(card.apr_percent) })}</dd></div>
          <div><dt>{t('fadv.cards.limit')}</dt><dd>{card.credit_limit === null ? '-' : <Money value={card.credit_limit} currency={c} />}</dd></div>
        </dl>
        {card.available !== null && <p className="finFoot">{t('fadv.cards.available', { amount: isolate(formatAmount(card.available, c)) })}{card.utilization_percent !== null ? ` · ${t('fadv.cards.used', { percent: card.utilization_percent })}` : ''}</p>}
        {card.observed_balance !== null && card.observed_at && <p className="muted finFoot">{t('fadv.cards.observed', { amount: isolate(formatAmount(card.observed_balance, c)), date: formatDateTime(card.observed_at) })}</p>}
        {card.interest_if_minimum !== null && Number(card.interest_if_minimum) > 0 && <p className="finFoot">{t('fadv.cards.interest', { amount: isolate(formatAmount(card.interest_if_minimum, c)) })}</p>}
        {card.never_clears_at_minimum && <p className="finFoot finAdvisorBad">{t('fadv.cards.never')}</p>}
        {card.months_to_clear_at_minimum !== null && card.total_interest_at_minimum !== null && <p className="muted finFoot">{t('fadv.cards.clears', { count: card.months_to_clear_at_minimum, amount: isolate(formatAmount(card.total_interest_at_minimum, c)) })}</p>}
        {card.missing.length > 0 && <p className="muted finFoot">{t('fadv.cards.missing', { list: card.missing.map((m) => t(`fadv.missing.${m}` as MessageKey)).join(', ') })}</p>}
      </li>;
    })}</ul>}
  </FinCard>;
}

function HistoryCard({ shownId, onShow }: { shownId: string | null; onShow: (view: FinanceAdvisorNoteView) => void }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<FinanceAdvisorNotePage>>({ kind: 'loading' });
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setPage((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    api.listAdvisorNotes(NOTES_PAGE, offset).then((value) => setPage({ kind: 'ready', value })).catch((e) => setPage(failed(e)));
  }, [offset]);
  useEffect(() => { load(); }, [load]);
  const open = async (id: string) => {
    setOpening(id);
    setError(null);
    try { onShow(await api.getAdvisorNote(id)); window.scrollTo({ top: 0 }); } catch (e) { setError(t('fadv.history.error', { message: describeError(e) })); } finally { setOpening(null); }
  };
  let body: ReactNode;
  if (page.kind === 'loading') body = <Notice busy>{t('fadv.loading')}</Notice>;
  else if (page.kind !== 'ready') body = <LoadError message={t('fadv.history.error', { message: page.message })} retry={load} />;
  else if (page.value.total === 0) body = <p className="muted">{t('fadv.history.empty')}</p>;
  else {
    const pages = pageCount(page.value.total), current = Math.floor(offset / NOTES_PAGE) + 1;
    body = <>
      <div className="finTableWrap" tabIndex={0}><table className="finGrid">
        <thead><tr><th scope="col">{t('fadv.history.week')}</th><th scope="col">{t('fadv.history.survival')}</th><th scope="col" className="num">{t('fadv.history.steps')}</th>
          <th scope="col">{t('fadv.history.source')}</th><th scope="col"><span className="visuallyHidden">{t('fadv.history.open')}</span></th></tr></thead>
        <tbody>{page.value.items.map((item) => <tr key={item.id}>
          <th scope="row">{day(item.week_start)}</th>
          <td>{item.survival.map((s) => `${s.currency}: ${days(s.days)}`).join(' · ') || '-'}</td>
          <td className="num">{item.actions}</td>
          <td>{t(item.trigger === 'schedule' ? 'fadv.history.schedule' : 'fadv.history.manual')}</td>
          <td>{item.id === shownId ? <StatusChip tone="ok">{t('fadv.history.opened')}</StatusChip>
            : <button type="button" className="secondary" disabled={opening === item.id} onClick={() => void open(item.id)}
              aria-label={`${t('fadv.history.open')}: ${t('fadv.weekOf', { date: day(item.week_start) })}`}>{t('fadv.history.open')}</button>}</td>
        </tr>)}</tbody>
      </table></div>
      {pages > 1 && <div className="actions finAdvisorPager">
        <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - NOTES_PAGE))}>{t('fadv.history.previous')}</button>
        <span>{t('fadv.history.page', { page: current, pages })}</span>
        <button type="button" className="secondary" disabled={current >= pages} onClick={() => setOffset(offset + NOTES_PAGE)}>{t('fadv.history.next')}</button>
      </div>}
    </>;
  }
  return <FinCard title={t('fadv.history.title')} icon={<History />}>{error && <Notice error>{error}</Notice>}{body}</FinCard>;
}

function SettingsCard({ overview, onSaved }: { overview: FinanceAdvisorOverview; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(overview.settings.enabled);
  const [weekday, setWeekday] = useState(overview.settings.weekday);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const selectId = useId();
  const locale = intlLocale(getLocale());
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try { await api.saveAdvisorSettings({ enabled, weekday }); setMessage({ text: t('fadv.settings.saved') }); onSaved(); }
    catch (e) { setMessage({ text: t('fadv.settings.failed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); }
  };
  return <FinCard title={t('fadv.settings.title')} icon={<Settings2 />}>
    <form className="finAdvisorSettings" onSubmit={(e) => void save(e)}>
      <label className="finAdvisorCheck"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{t('fadv.settings.enabled')}</label>
      <label htmlFor={selectId}>{t('fadv.settings.weekday')}</label>
      <select id={selectId} value={weekday} onChange={(e) => setWeekday(Number(e.target.value))} disabled={!enabled}>
        {WEEKDAYS.map((d) => <option key={d} value={d}>{weekdayName(d, locale)}</option>)}
      </select>
      <div className="actions"><button type="submit" disabled={busy}>{t('fadv.settings.save')}</button></div>
    </form>
    {message && <Notice error={message.error}>{message.text}</Notice>}
    <p className="muted finFoot">{overview.next_note_on ? t('fadv.settings.next', { date: isolate(day(overview.next_note_on)), zone: isolate(overview.timezone) }) : t('fadv.settings.off')}</p>
  </FinCard>;
}
