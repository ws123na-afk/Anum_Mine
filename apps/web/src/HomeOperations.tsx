// The Operations overview, the Overview tab of Home (docs/home.md#operations-overview): the business month (revenue,
// expenses, net profit against the month before), the fleet and its kilometres, everything waiting for someone, income
// against expenses over six months, spend by category, the Attention Center, fleet status, recent activity, the person's
// attendance this week, the next document to expire and purchases on their way. Each part reads its own module's API
// and has its own loading, empty, error and permission state, so one module that is off or refused never blanks the page.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  AlertTriangle, CalendarCheck, CarFront, ChevronRight, ClipboardList, Clock, Coins, FileText, Lock, Package, RefreshCw, Route, ShieldCheck,
  ShoppingCart, TrendingUp, Truck, Wallet,
} from 'lucide-react';
import type { Approval, AttendancePeriod, AttendanceToday, BooksReport, PurchaseOverview, Vehicle, VehicleOverview, VaultDocumentRow, VaultMeta } from '@anum/contracts';
import * as books from './lib/businessBooksApi';
import * as vehiclesApi from './lib/vehiclesApi';
import * as vaultApi from './lib/vaultApi';
import * as purchasesApi from './lib/purchasesApi';
import * as attendanceApi from './lib/attendanceApi';
import { businessTotals } from './lib/financeStatements';
import { delta, monthRange, monthsEnding, type Delta } from './lib/financeNav';
import { formatAmount, monthLabel, shiftMonth } from './lib/finance';
import {
  attentionTotal, daysChip, previousMonthName, trendFor, daysTone, expenseBreakdown, kmIn, kmPerLitre, nextDue, rankAttention, vehicleStatus, weekAttendance, type AttentionItem,
} from './lib/ops';
import { dueLabel, dueText, km, vehicleName } from './lib/vehicles';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';
import { Ring, ShareBars, TrendLines } from './OpsParts';

type Part<T> = { kind: 'loading' } | { kind: 'ready'; value: T } | { kind: 'denied'; message: string } | { kind: 'error'; message: string };
/**
 * Loads one part. With `run` (a generation counter), only the newest request may set the state: a slow reply for a month
 * the person has already left is dropped, so it never replaces the month on screen.
 */
const settle = <T,>(promise: Promise<T>, set: (value: Part<T>) => void, run?: { current: number }) => {
  const mine = run ? ++run.current : 0;
  const latest = () => !run || run.current === mine;
  set({ kind: 'loading' });
  promise.then((value) => { if (latest()) set({ kind: 'ready', value }); })
    .catch((e: unknown) => { if (latest()) set(isPermissionDenied(e) ? { kind: 'denied', message: describeError(e) } : { kind: 'error', message: describeError(e) }); });
};
const day = (value: string) => formatDateTime(`${value.slice(0, 10)}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

interface Fleet { months: VehicleOverview; month: VehicleOverview; vehicles: Vehicle[]; total: number }
interface Vault { meta: VaultMeta; next: VaultDocumentRow[] }
interface Attendance { week: AttendancePeriod; today: AttendanceToday }

export function HomeOperations({ month, waiting, activity, live }: { month: string; waiting: readonly Approval[]; activity: ReactNode; live: number }) {
  const [report, setReport] = useState<Part<BooksReport>>({ kind: 'loading' });
  const [fleet, setFleet] = useState<Part<Fleet>>({ kind: 'loading' });
  const [vault, setVault] = useState<Part<Vault>>({ kind: 'loading' });
  const [purchases, setPurchases] = useState<Part<PurchaseOverview>>({ kind: 'loading' });
  const [attendance, setAttendance] = useState<Part<Attendance>>({ kind: 'loading' });
  const months = useMemo(() => monthsEnding(month, 6), [month]);
  const booksRun = useRef(0), fleetRun = useRef(0);
  const loadBooks = useCallback(() => settle(books.getReport(monthRange(month)), setReport, booksRun), [month]);
  const loadFleet = useCallback(() => settle((async () => {
    const range = monthRange(month);
    const [six, one, list] = await Promise.all([vehiclesApi.overview(monthRange(months[0]).from, range.to), vehiclesApi.overview(range.from, range.to), vehiclesApi.listVehicles('', false, 0, 5)]);
    return { months: six, month: one, vehicles: list.items, total: list.total };
  })(), setFleet, fleetRun), [month, months]);
  const loadVault = useCallback(() => settle(Promise.all([vaultApi.getMeta(), vaultApi.listDocuments('status=active&sort=expiry&limit=3')]).then(([meta, page]) => ({ meta, next: page.items })), setVault), []);
  const loadPurchases = useCallback(() => settle(purchasesApi.overview(), setPurchases), []);
  const loadAttendance = useCallback(() => settle(Promise.all([attendanceApi.getSummary('week', isoDay(new Date())), attendanceApi.getToday()]).then(([week, today]) => ({ week, today })), setAttendance), []);
  useEffect(loadBooks, [loadBooks]);
  useEffect(loadFleet, [loadFleet]);
  useEffect(() => { loadVault(); loadPurchases(); loadAttendance(); }, [loadVault, loadPurchases, loadAttendance, live]);

  const r = report.kind === 'ready' ? report.value : null;
  const totals = r ? businessTotals(r) : null;
  const before = r ? r.trend.find((row) => row.month.slice(0, 7) === shiftMonth(month, -1)) ?? null : null;
  const vs = t('ops.vsMonth', { month: previousMonthName(month) });
  const words = (d: Delta | null) => (d ? `${d.percent === null ? t(`fshell.delta.${d.direction}` as MessageKey) : t(`fshell.delta.pct.${d.direction}` as MessageKey, { percent: Math.abs(d.percent) })} ${vs}` : undefined);
  const f = fleet.kind === 'ready' ? fleet.value : null;
  const kmNow = f ? kmIn(f.months.usage, month) : null, kmBefore = f ? kmIn(f.months.usage, shiftMonth(month, -1)) : null;

  const attention = useMemo(() => {
    const items: AttentionItem[] = [];
    if (waiting.length) items.push({ key: 'approvals', kind: 'approval', title: t('ops.att.approvals', { count: waiting.length }), detail: t('ops.att.approvalsDetail'), chip: null, tone: 'warn', href: '#approvals', rank: -2, count: waiting.length });
    for (const d of f?.months.due ?? []) {
      const days = d.days_left;
      items.push({ key: `veh:${d.vehicle_id}:${d.kind}:${d.source_id}`, kind: 'vehicle', title: t('ops.att.vehicle', { what: dueLabel(d) }), detail: d.registration,
        chip: days !== null ? daysChip(days) : dueText(d), tone: days !== null ? daysTone(days) : 'warn', href: '#vehicles', rank: days ?? 30, count: 1 });
    }
    if (vault.kind === 'ready') {
      const s = vault.value.meta.summary;
      if (s.expired) items.push({ key: 'docs:expired', kind: 'document', title: t('ops.att.docsExpired', { count: s.expired }), detail: t('ops.att.docsExpiredDetail'), chip: null, tone: 'bad', href: '#vault', rank: -1, count: s.expired });
      if (s.review) items.push({ key: 'docs:review', kind: 'review', title: t('ops.att.docsReview', { count: s.review }), detail: t('ops.att.docsReviewDetail'), chip: null, tone: 'warn', href: '#vault-review', rank: 5, count: s.review });
    }
    if (purchases.kind === 'ready') {
      const p = purchases.value;
      if (p.late) items.push({ key: 'pur:late', kind: 'purchase', title: t('ops.att.purchasesLate', { count: p.late }), detail: t('ops.att.purchasesLateDetail'), chip: null, tone: 'bad', href: '#purchases', rank: 3, count: p.late });
      if (p.open_returns) items.push({ key: 'pur:returns', kind: 'return', title: t('ops.att.returns', { count: p.open_returns }), detail: p.refunds_owed.length ? t('ops.att.refundsOwed', { amount: p.refunds_owed.map((m) => formatAmount(m.amount, m.currency)).join(' · ') }) : t('ops.att.returnsDetail'),
        chip: null, tone: 'warn', href: '#purchases-returns', rank: 6, count: p.open_returns });
      if (p.needs_review) items.push({ key: 'pur:review', kind: 'review', title: t('ops.att.purchasesReview', { count: p.needs_review }), detail: t('ops.att.purchasesReviewDetail'), chip: null, tone: 'warn', href: '#purchases', rank: 7, count: p.needs_review });
    }
    if (r) {
      const overdue = r.balances.unpaid_bills.filter((b) => b.days_overdue > 0);
      if (overdue.length) items.push({ key: 'books:bills', kind: 'bill', title: t('ops.att.billsOverdue', { count: overdue.length }), detail: overdue.slice(0, 2).map((b) => b.vendor_name).join(', '), chip: null, tone: 'bad', href: '#business-books/bills', rank: 2, count: overdue.length });
      if (r.allocations_in_review) items.push({ key: 'books:alloc', kind: 'allocation', title: t('ops.att.allocations', { count: r.allocations_in_review }), detail: t('ops.att.allocationsDetail'), chip: null, tone: 'warn', href: '#business-books/allocations', rank: 4, count: r.allocations_in_review });
    }
    return items;
  }, [waiting, f, vault, purchases, r]);
  const settledAll = [report, fleet, vault, purchases].every((p) => p.kind !== 'loading');
  const total = attentionTotal(attention);
  const shown = rankAttention(attention, 5);
  const ICON: Record<AttentionItem['kind'], ReactNode> = { vehicle: <CarFront />, document: <FileText />, review: <ClipboardList />, return: <RefreshCw />, purchase: <Truck />,
    approval: <ShieldCheck />, allocation: <Wallet />, bill: <Coins /> };
  const booksUnavailable = report.kind === 'denied' ? t('ops.home.booksDenied') : report.kind === 'error' ? t('ops.home.booksError') : undefined;
  const money = (value: string | undefined) => (totals && value !== undefined ? <bdi>{formatAmount(value, totals.currency)}</bdi> : report.kind === 'loading' ? null : '-');
  const kpi = (key: 'income' | 'expenses' | 'net', icon: ReactNode) => {
    const value = totals ? (key === 'income' ? totals.income : key === 'expenses' ? totals.expenses : totals.net) : undefined;
    const prev = before ? (key === 'income' ? before.revenue : key === 'expenses' ? before.costs : before.profit) : null;
    const d = value !== undefined && prev !== null ? delta(value, prev, key !== 'expenses') : null;
    return <KpiCard icon={icon} label={t(`ops.home.kpi.${key}` as MessageKey)} value={money(value) ?? <Pending />} delta={d} deltaText={words(d)} hint={booksUnavailable}
      tone={key === 'net' && value?.startsWith('-') ? 'bad' : ''} />;
  };
  return <div className="opsHome">
    <KpiRow label={t('ops.home.kpis')} className="opsKpis6">
      {kpi('income', <Coins />)}
      {kpi('expenses', <ShoppingCart />)}
      {kpi('net', <TrendingUp />)}
      <KpiCard icon={<CarFront />} label={t('ops.home.kpi.fleet')} value={f ? t('ops.home.vehicles', { count: f.months.vehicles }) : fleet.kind === 'loading' ? <Pending /> : '-'}
        hint={fleet.kind === 'denied' ? t('ops.home.fleetDenied') : fleet.kind === 'error' ? t('ops.home.fleetError') : f ? t('ops.home.fleetHint', { count: f.months.due.length }) : undefined} />
      <KpiCard icon={<Route />} label={t('ops.home.kpi.mileage')} value={kmNow !== null ? km(kmNow) : fleet.kind === 'loading' ? <Pending /> : '-'}
        delta={kmNow !== null ? delta(kmNow, kmBefore) : null} deltaText={kmNow !== null ? words(delta(kmNow, kmBefore)) : undefined} />
      <KpiCard icon={<ClipboardList />} label={t('ops.home.kpi.pending')} value={settledAll ? formatNumber(total) : <Pending />} tone={total ? 'warn' : ''}
        hint={total ? t('ops.home.pendingHint') : t('ops.home.pendingNone')} />
    </KpiRow>

    <div className="opsHomeRow opsHomeTop">
      <FinCard title={t('ops.home.trend')} icon={<TrendingUp />} subtitle={t('ops.home.trendSub', { from: monthLabel(months[0]), to: monthLabel(month) })}>
        <PartState part={report} retry={loadBooks} denied={t('ops.home.booksDenied')} link={{ href: '#business-books', label: t('ops.home.openBooks') }}>
          {r && (trendFor(r, months).every((row) => Number(row.income) === 0 && Number(row.expenses) === 0)
            ? <Nothing text={t('ops.home.trendEmpty')} href="#business-books/bills" action={t('ops.home.addBill')} />
            : <TrendLines rows={trendFor(r, months)} currency={r.base_currency} title={t('ops.home.trend')} incomeLabel={t('ops.home.kpi.income')} expenseLabel={t('ops.home.kpi.expenses')} />)}
        </PartState>
      </FinCard>
      <FinCard title={t('ops.home.spend')} icon={<Wallet />} subtitle={monthLabel(month)}>
        <PartState part={report} retry={loadBooks} denied={t('ops.home.booksDenied')}>
          {r && (expenseBreakdown(r).length === 0 ? <Nothing text={t('ops.home.spendEmpty')} href="#business-books/bills" action={t('ops.home.addBill')} />
            : <ShareBars rows={expenseBreakdown(r)} currency={r.base_currency} label={t('ops.home.spend')} />)}
        </PartState>
      </FinCard>
      <FinCard title={t('ops.home.attention')} icon={<AlertTriangle />} className="opsAttention"
        actions={settledAll ? <StatusChip tone={total ? 'warn' : 'ok'}>{t('ops.home.items', { count: total })}</StatusChip> : undefined}>
        {!settledAll && shown.length === 0 ? <p className="muted" role="status">{t('common.loading')}</p>
          : shown.length === 0 ? <p className="opsAllClear"><ShieldCheck aria-hidden="true" />{t('ops.home.allClear')}</p>
          : <ul className="opsAttentionList">{shown.map((item) => <li key={item.key}><a href={item.href} className="opsAttentionRow">
            <span className={`opsAttentionIcon ${item.tone}`} aria-hidden="true">{ICON[item.kind]}</span>
            <span className="opsAttentionText"><strong>{item.title}</strong><small dir="auto">{item.detail}</small></span>
            {item.chip && <StatusChip tone={item.tone === 'bad' ? 'bad' : item.tone === 'info' ? 'info' : 'warn'}>{item.chip}</StatusChip>}
            <ChevronRight className="cmFlip" aria-hidden="true" /></a></li>)}</ul>}
        {settledAll && attention.length > shown.length && <p className="muted opsMore">{t('ops.home.moreItems', { count: attention.length - shown.length })}</p>}
      </FinCard>
    </div>

    <div className="opsHomeRow opsHomeMid">
      <FinCard title={t('ops.home.fleet')} icon={<CarFront />} subtitle={t('ops.home.fleetSub')} actions={<a className="finLinkBtn secondary" href="#vehicles">{t('ops.home.allVehicles')}</a>}>
        <PartState part={fleet} retry={loadFleet} denied={t('ops.home.fleetDenied')}>
          {f && (f.vehicles.length === 0 ? <Nothing text={t('ops.home.fleetEmpty')} href="#vehicles" action={t('ops.home.addVehicle')} />
            : <FleetStatus fleet={f} />)}
        </PartState>
      </FinCard>
      {activity}
    </div>

    <div className="opsHomeRow opsHomeBottom">
      <FinCard title={t('ops.home.attendance')} icon={<CalendarCheck />} actions={<a className="cmLinkButton" href="#attendance">{t('ops.home.details')}</a>}>
        <PartState part={attendance} retry={loadAttendance} denied={t('ops.home.attendanceDenied')}>
          {attendance.kind === 'ready' && <AttendanceCard data={attendance.value} />}
        </PartState>
      </FinCard>
      <FinCard title={t('ops.home.expiry')} icon={<FileText />} actions={<a className="cmLinkButton" href="#vault">{t('ops.viewAll')}</a>}>
        <PartState part={vault} retry={loadVault} denied={t('ops.home.vaultDenied')}>
          {vault.kind === 'ready' && <ExpiryCard vault={vault.value} />}
        </PartState>
      </FinCard>
      <FinCard title={t('ops.home.purchases')} icon={<Package />} actions={<a className="cmLinkButton" href="#purchases">{t('ops.viewAll')}</a>}>
        <PartState part={purchases} retry={loadPurchases} denied={t('ops.home.purchasesDenied')}>
          {purchases.kind === 'ready' && <PurchasesCard p={purchases.value} />}
        </PartState>
      </FinCard>
    </div>
  </div>;
}

function Pending() {
  return <><span className="skeletonRow tiny" aria-hidden="true" /><span className="visuallyHidden">{t('common.loading')}</span></>;
}

function Nothing({ text, href, action }: { text: string; href: string; action: string }) {
  return <div className="opsNothing"><p className="muted">{text}</p><a className="finLinkBtn secondary" href={href}>{action}</a></div>;
}

/** A part's loading, refused and failed states; its children only once it is ready. */
function PartState<T>({ part, retry, denied, link, children }: { part: Part<T>; retry: () => void; denied: string; link?: { href: string; label: string }; children: ReactNode }) {
  if (part.kind === 'loading') return <p className="muted" role="status" aria-busy="true">{t('common.loading')}</p>;
  if (part.kind === 'denied') return <p className="opsDenied"><Lock aria-hidden="true" /><span>{denied}</span></p>;
  if (part.kind === 'error') return <div className="opsInlineError" role="alert"><span>{t('ops.home.partError', { message: part.message })}</span>
    <button type="button" className="secondary" onClick={retry}>{t('ops.retry')}</button>{link && <a className="cmLinkButton" href={link.href}>{link.label}</a>}</div>;
  return <>{children}</>;
}

function FleetStatus({ fleet }: { fleet: Fleet }) {
  return <>
    <div className="finTableWrap" tabIndex={0}>
      <table className="finGrid opsFleetTable"><caption className="visuallyHidden">{t('ops.home.fleet')}</caption>
        <thead><tr><th scope="col">{t('ops.veh.col.vehicle')}</th><th scope="col">{t('ops.veh.col.registration')}</th><th scope="col" className="num">{t('ops.veh.col.odometer')}</th>
          <th scope="col" className="num">{t('ops.veh.fact.economy')}</th><th scope="col">{t('ops.veh.col.next')}</th><th scope="col">{t('ops.veh.col.status')}</th></tr></thead>
        <tbody>{fleet.vehicles.map((v) => {
          const usage = fleet.month.by_vehicle_usage.find((u) => u.vehicle_id === v.id);
          const economy = kmPerLitre(usage?.average_per_100);
          const next = nextDue(v.due);
          const status = vehicleStatus(v.due);
          return <tr key={v.id}>
            <th scope="row"><span className="opsVehCell"><span className="opsVehIcon" aria-hidden="true"><CarFront /></span><span><strong dir="auto">{vehicleName(v)}</strong>
              <small>{[v.year, t(`veh.own.${v.ownership}` as MessageKey)].filter(Boolean).join(' · ')}</small></span></span></th>
            <td><span className="opsPlate"><bdi>{v.registration}</bdi></span></td>
            <td className="num"><bdi>{km(v.odometer_km)}</bdi></td>
            <td className="num"><bdi>{economy ? t('ops.veh.kmPerL', { value: economy }) : '-'}</bdi></td>
            <td>{next ? <span className="opsNext"><strong>{dueLabel(next)}</strong><small>{next.due_on ? day(next.due_on) : dueText(next)}</small></span> : '-'}</td>
            <td><StatusChip tone={status.tone === 'ok' ? 'ok' : status.tone === 'bad' ? 'bad' : 'warn'}>{status.label}</StatusChip></td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    {fleet.total > fleet.vehicles.length && <p className="muted opsMore">{t('ops.veh.more', { shown: fleet.vehicles.length, total: fleet.total })}</p>}
  </>;
}

function AttendanceCard({ data }: { data: Attendance }) {
  const week = weekAttendance(data.week.days);
  const arrived = data.today.totals?.first_arrival ?? null;
  const zone = data.today.time_zone;
  const range = t('ops.home.weekRange', { from: day(data.week.start), to: day(data.week.end) });
  return <div className="opsAttendance">
    <div className="opsAttendanceWeek">
      {week.scheduled !== null
        ? <Ring value={week.attended} max={week.scheduled} text={`${week.attended}/${week.scheduled}`} label={t('ops.home.weekRing', { attended: week.attended, scheduled: week.scheduled })} />
        : <span className="opsRingAlt"><strong>{formatNumber(week.attended)}</strong></span>}
      <span><strong>{t('ops.home.week')}</strong><small>{week.scheduled !== null ? t('ops.home.weekOf', { attended: week.attended, scheduled: week.scheduled }) : t('ops.home.weekDays', { count: week.attended })}</small>
        <small><bdi>{range}</bdi></small></span>
    </div>
    <div className="opsAttendanceToday">
      <Clock aria-hidden="true" />
      <span><small>{t('ops.home.checkIn')}</small><strong><bdi>{arrived ? formatDateTime(arrived, { timeStyle: 'short', timeZone: zone }) : '-'}</bdi></strong></span>
      <StatusChip tone={data.today.status.at_site || data.today.status.remote ? 'ok' : arrived ? 'info' : 'muted'}>
        {data.today.status.on_break ? t('ops.home.onBreak') : data.today.status.at_site ? t('ops.home.atSite') : data.today.status.remote ? t('ops.home.remote') : arrived ? t('ops.home.left') : t('ops.home.notIn')}</StatusChip>
    </div>
  </div>;
}

function ExpiryCard({ vault }: { vault: Vault }) {
  const next = vault.next[0] ?? null;
  const s = vault.meta.summary;
  if (!next) return <Nothing text={t('ops.home.expiryNone')} href="#vault" action={t('ops.home.openVault')} />;
  return <div className="opsExpiry">
    <div className="opsExpiryNext">
      <FileText aria-hidden="true" />
      <span><small>{t('ops.home.nextExpiry')}</small><strong dir="auto">{next.title}</strong><small dir="auto">{next.issuer ?? next.category}</small></span>
      <span className="opsExpiryWhen">{next.days_left !== null && <StatusChip tone={next.days_left < 0 ? 'bad' : next.days_left <= 30 ? 'warn' : 'info'}>{daysChip(next.days_left)}</StatusChip>}
        {next.expiry_date && <small><bdi>{day(next.expiry_date)}</bdi></small>}</span>
    </div>
    <a className="opsExpiryMore" href="#vault"><span>{t('ops.home.expiryCounts', { soon: s.expiring, expired: s.expired })}</span><ChevronRight className="cmFlip" aria-hidden="true" /></a>
  </div>;
}

function PurchasesCard({ p }: { p: PurchaseOverview }) {
  const steps = [
    { key: 'orders', icon: <ShoppingCart />, count: p.orders_this_month, label: t('ops.home.pur.orders') },
    { key: 'transit', icon: <Truck />, count: p.in_transit, label: t('ops.home.pur.transit') },
    { key: 'awaiting', icon: <Package />, count: p.awaiting_delivery, label: t('ops.home.pur.awaiting') },
    { key: 'returns', icon: <RefreshCw />, count: p.open_returns, label: t('ops.home.pur.returns') },
  ];
  if (p.purchases === 0) return <Nothing text={t('ops.home.purNone')} href="#purchases" action={t('ops.home.openPurchases')} />;
  return <div className="opsPurchases">
    <ul className="opsSteps">{steps.map((s) => <li key={s.key}><span className="opsStepIcon" aria-hidden="true">{s.icon}</span><strong>{formatNumber(s.count)}</strong><small>{s.label}</small></li>)}</ul>
    <p className="muted opsMore">{p.late ? <><AlertTriangle aria-hidden="true" className="opsWarnIcon" />{t('ops.home.pur.late', { count: p.late })}</> : <><CalendarCheck aria-hidden="true" className="opsOkIcon" />{t('ops.home.pur.onTime')}</>}</p>
  </div>;
}
