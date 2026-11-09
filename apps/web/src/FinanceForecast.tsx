// The cash-flow forecast and the emergency reserve cards (docs/finance.md#forecast, #emergency-reserve), shared by
// Safe to spend, the Finance overview and Finance settings. Every figure comes from GET /finance/forecast and
// GET /finance/reserve; this file only words and draws them. An estimate, never a promise; nothing here moves money.
import { Suspense, lazy, useState } from 'react';
import { CalendarClock, Gauge, LifeBuoy, ShieldAlert, Settings2, TriangleAlert, Wallet } from 'lucide-react';
import type { FinanceCurrencyForecast, FinanceForecast, FinanceForecastAssumption, FinanceForecastEvent, FinanceReserves } from '@anum/contracts';
import { formatAmount, isolate, monthLabel } from './lib/finance';
import { chartPoints, firstWarning, horizonEnd, eventsUntil, progressValue, reserveFor, type ForecastHorizon } from './lib/financeForecast';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { Empty, LoadError, Money, Notice, type Load } from './FinanceParts';
import { FinBanner, FinCard, KpiCard, KpiRow, StatusChip } from './FinanceShell';

const RunwayChart = lazy(() => import('./FinanceCharts').then((m) => ({ default: m.RunwayChart })));

const day = (iso: string) => formatDateTime(`${iso}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' });

/** An assumption or a left-out line in the person's language (the API's English `detail` is for the assistant). */
export function assumptionText(a: FinanceForecastAssumption, currency: string): string {
  const p = a.params;
  const key = `ffc.assume.${a.code}` as MessageKey;
  return t(key, {
    count: Number(p.count ?? 0), month: p.month ? monthLabel(p.month) : '', date: p.date ? isolate(day(p.date)) : '', day: p.day ?? '',
    months: p.months ? p.months.split(', ').map((m) => monthLabel(m)).join(', ') : '', amount: isolate(formatAmount(p.amount ?? '0', currency)),
    daily: isolate(formatAmount(p.daily ?? '0', currency)), low: isolate(formatAmount(p.low ?? '0', currency)), high: isolate(formatAmount(p.high ?? '0', currency)),
    name: p.name ?? '', bill: p.bill ?? '',
  });
}

function eventKind(e: FinanceForecastEvent): string {
  return t(`ffc.event.${e.kind}` as MessageKey);
}

function warningText(kind: string, on: string, threshold: string, currency: string): string {
  return t(`ffc.warn.${kind}` as MessageKey, { date: isolate(day(on)), amount: isolate(formatAmount(threshold, currency)) });
}

/**
 * The forecast for one currency: today's actual balance, the expected month end with its band, the warning, the chart
 * (this month or three months), what was assumed and left out, and every expected movement.
 */
export function ForecastCard({ forecast, currency, onRetry, onOpenSettings, compact = false }: {
  forecast: Load<FinanceForecast>; currency: string; onRetry: () => void; onOpenSettings?: () => void; compact?: boolean;
}) {
  const [horizon, setHorizon] = useState<ForecastHorizon>('month');
  const data = forecast.kind === 'ready' ? forecast.value : null;
  const row: FinanceCurrencyForecast | null = data?.currencies.find((c) => c.currency === currency) ?? null;
  const title = data ? t('ffc.title', { month: monthLabel(data.month_end.slice(0, 7)) }) : t('ffc.titlePlain');
  if (forecast.kind === 'loading') return <FinCard title={title} icon={<Gauge />} className="finRunway"><Notice busy>{t('ffc.loading')}</Notice></FinCard>;
  if (forecast.kind !== 'ready') return <FinCard title={title} icon={<Gauge />} className="finRunway">
    <LoadError message={t('ffc.error', { message: forecast.message })} retry={onRetry} /></FinCard>;
  if (!row || !data) return <FinCard title={title} icon={<Gauge />} className="finRunway">
    <Empty icon={<Gauge aria-hidden="true" />} title={t('ffc.empty.title', { currency })} text={t('ffc.empty.text')} /></FinCard>;
  const until = horizon === 'month' ? data.month_end : data.horizon_end;
  const end = horizonEnd(row, horizon);
  const warning = firstWarning(row, until);
  const points = chartPoints(row, until);
  const events = eventsUntil(row, until);
  return <FinCard title={title} subtitle={t('ffc.subtitle')} icon={<Gauge />} className="finRunway finForecast"
    actions={<div className="finSegmented" role="group" aria-label={t('ffc.horizon')}>
      {(['month', 'three'] as const).map((h) => <button key={h} type="button" aria-pressed={horizon === h} onClick={() => setHorizon(h)}>{t(`ffc.horizon.${h}`)}</button>)}
    </div>}>
    <KpiRow label={t('ffc.kpis')} className="finForecastKpis">
      <KpiCard icon={<Wallet />} label={t('ffc.kpi.actual')} value={<Money value={row.actual} currency={currency} signed />} hint={t('ffc.kpi.actualHint', { count: row.accounts_counted })} />
      <KpiCard icon={<Gauge />} label={t('ffc.kpi.expected', { date: isolate(day(end.on)) })} value={<Money value={end.expected} currency={currency} signed />}
        tone={Number(end.expected) < 0 ? 'bad' : row.reserve_target && Number(end.expected) < Number(row.reserve_target) ? 'warn' : 'good'}
        hint={t('ffc.kpi.range', { low: isolate(formatAmount(end.low, currency)), high: isolate(formatAmount(end.high, currency)) })} />
      {!compact && <KpiCard icon={<CalendarClock />} label={t('ffc.kpi.inOut')} value={<Money value={end.money_in} currency={currency} />}
        hint={t('ffc.kpi.out', { out: isolate(formatAmount(end.money_out, currency)), spend: isolate(formatAmount(end.spending, currency)) })} />}
    </KpiRow>
    {warning ? <FinBanner tone="warn" icon={<ShieldAlert />} title={t(`ffc.warnTitle.${warning.kind}`)}
      action={warning.kind === 'below_reserve' && onOpenSettings ? <button type="button" className="secondary" onClick={onOpenSettings}>{t('ffc.reserve.edit')}</button> : undefined}>
      {warningText(warning.kind, warning.on, warning.threshold, currency)}</FinBanner>
      : <p className="muted finForecastOk">{t('ffc.noWarning')}</p>}
    {points.length < 2 ? <p className="muted">{t('ffc.lastDay')}</p>
      : <Suspense fallback={<Notice busy>{t('ffc.loading')}</Notice>}>
        <RunwayChart points={points} currency={currency} title={t('ffc.chartTitle', { currency })} reserve={row.reserve_target} />
      </Suspense>}
    {!compact && <div className="finForecastDetails">
      <div className="finAssumptions"><strong>{t('ffc.assume.title')}</strong>
        <ul>{row.assumptions.map((a) => <li key={a.code}>{assumptionText(a, currency)}</li>)}</ul>
        {row.left_out.length > 0 && <><strong>{t('ffc.leftOut.title')}</strong>
          <ul>{row.left_out.map((a, i) => <li key={`${a.code}-${i}`}>{assumptionText(a, currency)}</li>)}</ul></>}
      </div>
      <div className="finTableWrap" tabIndex={0}><table className="finGrid"><caption>{t('ffc.events.title')}</caption>
        <thead><tr><th scope="col">{t('fsafe.col.date')}</th><th scope="col">{t('ffc.col.what')}</th><th scope="col" className="num">{t('fsafe.col.amount')}</th></tr></thead>
        <tbody>{events.length === 0 ? <tr><td colSpan={3} className="muted">{t('ffc.events.none')}</td></tr>
          : events.map((e, i) => <tr key={`${e.kind}-${e.ref_id ?? e.label}-${e.on}-${i}`}><td className="finNoWrap"><bdi>{day(e.on)}</bdi></td>
            <th scope="row" dir="auto">{e.label}<small className="muted finSub">{eventKind(e)}{e.status !== 'expected' && <> <StatusChip tone="warn">{t(`ffc.status.${e.status}`)}</StatusChip></>}</small></th>
            <td className="num"><Money value={e.amount} currency={currency} signed /></td></tr>)}</tbody>
      </table></div>
    </div>}
    <p className="muted finFoot">{t('ffc.note')}</p>
  </FinCard>;
}

/** The emergency reserve in one currency: target, saved, progress, months of essentials covered, where it is held. */
export function ReserveCard({ reserves, currency, onRetry, onOpenSettings }: {
  reserves: Load<FinanceReserves>; currency: string; onRetry: () => void; onOpenSettings: () => void;
}) {
  const edit = <button type="button" className="secondary" onClick={onOpenSettings}><Settings2 aria-hidden="true" />{t('ffc.reserve.edit')}</button>;
  if (reserves.kind === 'loading') return <FinCard title={t('ffc.reserve.title')} icon={<LifeBuoy />}><Notice busy>{t('ffc.loading')}</Notice></FinCard>;
  if (reserves.kind !== 'ready') return <FinCard title={t('ffc.reserve.title')} icon={<LifeBuoy />}><LoadError message={t('ffc.reserve.error', { message: reserves.message })} retry={onRetry} /></FinCard>;
  const { reserve, suggestion } = reserveFor(reserves.value, currency);
  if (!reserve) return <FinCard title={t('ffc.reserve.title')} icon={<LifeBuoy />} className="finReserve">
    <Empty icon={<LifeBuoy aria-hidden="true" />} title={t('ffc.reserve.none', { currency })}
      text={suggestion ? t('ffc.reserve.suggest', { months: suggestion.months, amount: isolate(formatAmount(suggestion.target, currency)), essential: isolate(formatAmount(suggestion.essential_monthly, currency)) })
        : t('ffc.reserve.noneText')} action={<button type="button" onClick={onOpenSettings}>{t('ffc.reserve.setUp')}</button>} />
  </FinCard>;
  const value = progressValue(reserve);
  return <FinCard title={t('ffc.reserve.title')} subtitle={reserve.name} icon={<LifeBuoy />} className="finReserve" actions={edit}>
    <p className="finReserveFigure"><Money value={reserve.saved} currency={currency} /> <span className="muted">{t('ffc.reserve.of', { amount: isolate(formatAmount(reserve.target, currency)) })}</span></p>
    <span className={`finUsage ${reserve.status === 'reached' ? 'ok' : value < 50 ? 'warn' : 'ok'}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}
      aria-label={t('ffc.reserve.meter', { percent: formatNumber(value) })}>
      <span className="finTrack"><span className="finFill" style={{ width: `${value}%` }} /></span><small>{formatNumber(reserve.progress_percent ?? 0)}%</small></span>
    <ul className="finRuleLines">
      <li><CalendarClock aria-hidden="true" />{reserve.months_covered !== null
        ? t('ffc.reserve.covers', { months: reserve.months_covered, essential: isolate(formatAmount(reserve.essential_monthly ?? '0', currency)) })
        : t('ffc.reserve.noEssentials')}</li>
      <li><LifeBuoy aria-hidden="true" />{reserve.mode === 'months' ? t('ffc.reserve.modeMonths', { months: reserve.months ?? 0 }) : t('ffc.reserve.modeAmount')}</li>
      <li><Settings2 aria-hidden="true" /><span dir="auto">{reserve.held_in === 'account' ? t('ffc.reserve.heldAccount', { name: reserve.account_name ?? '' }) : t('ffc.reserve.heldGoal')}</span></li>
      {reserve.paused && <li><TriangleAlert aria-hidden="true" />{t('ffc.reserve.paused')}</li>}
    </ul>
  </FinCard>;
}
