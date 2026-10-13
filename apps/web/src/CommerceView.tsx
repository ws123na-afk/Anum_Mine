// Commerce (docs/commerce.md): sales across Amazon, noon and the owner's own store. Overview
// (sales by channel and day, top products, pending orders, low stock, returns, price alerts),
// orders, the catalogue with tyre attributes and Arabic titles, alerts, and the channel
// connections. Price and stock changes are proposed here and wait for an approval with the
// payload hash; the server checks every permission and the view says who can do what.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  AlertTriangle, BellRing, Boxes, CheckCircle2, ChevronLeft, ChevronRight, CircleDot, Link2, Lock, PackageSearch, Pencil, Plug, Plus, RefreshCw,
  Search, ShieldCheck, ShoppingCart, Store, Trash2, TrendingUp, Truck, Undo2, X,
} from 'lucide-react';
import type {
  Approval, CommerceAlert, CommerceChannel, CommerceConnector, CommerceDashboard, CommerceListing, CommerceOrder, CommerceOrderDetail,
  CommercePage, CommerceProduct, CommerceReturn, CommerceSettings, CommerceSetup, OwnStoreMode, TyreSpec,
} from '@anum/contracts';
import * as api from './lib/commerceApi';
import {
  AMAZON_MARKETPLACES, CHANNEL_COLORS, CHANNELS, NOON_MARKETPLACES, OWN_STORE_MODES, RANGE_PRESETS, channelLabel, connectorBody, displayTitle,
  emptyConnectorForm, formatCount, formatMoney, healthLabel, healthTone, niceMax, orderStatusLabel, orderTone, pageLabel, parsePrice,
  parseQuantity, presetRange, salesChart, severityTone, type ConnectorForm, type RangePreset,
} from './lib/commerce';
import { shortHash } from './lib/approvals';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatDateTime, getLocale, t, type MessageKey } from './lib/i18n';
import { stockAndPrices, summaryFigures, type SummaryNumbers } from './lib/commerce';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { PageHeader, PageSummary } from './PageParts';
import './commerce.css';

type Tab = 'overview' | 'orders' | 'returns' | 'catalog' | 'alerts' | 'connections';
const TABS: Tab[] = ['overview', 'orders', 'returns', 'catalog', 'alerts', 'connections'];
const PAGE = 20;
const when = (value: string | null | undefined) => (value ? formatDateTime(value) : '-');

type Load<T> = { kind: 'loading' } | { kind: 'ready'; value: T } | { kind: 'denied' } | { kind: 'error'; message: string };
const failed = (e: unknown): Load<never> => (isPermissionDenied(e) ? { kind: 'denied' } : { kind: 'error', message: describeError(e) });

function Notice({ children, error, busy }: { children: ReactNode; error?: boolean; busy?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'} aria-live={error ? undefined : 'polite'} aria-busy={busy || undefined}><CircleDot aria-hidden="true" />{children}</p>;
}
function Badge({ tone, children }: { tone: string; children: ReactNode }) { return <span className={`statusBadge ${tone}`}>{children}</span>; }
function ChannelDot({ channel }: { channel: CommerceChannel }) { return <span className="cmDot" style={{ background: CHANNEL_COLORS[channel] }} aria-hidden="true" />; }
function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return <div className="notice errorNotice cmLoadError" role="alert"><CircleDot aria-hidden="true" /><span>{message}</span><button type="button" className="secondary" onClick={retry}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>;
}
function Denied({ text }: { text: string }) {
  return <section className="surface cmPanel cmDenied"><Lock aria-hidden="true" /><div><h2>{t('commerce.denied.title')}</h2><p className="muted">{text}</p></div></section>;
}

function Pager({ page, onPage, label }: { page: CommercePage<unknown>; onPage: (offset: number) => void; label: string }) {
  if (page.total <= page.limit && page.offset === 0) return <p className="muted cmPagerText">{pageLabel(page.offset, page.items.length, page.total)}</p>;
  return <nav className="cmPager" aria-label={label}>
    <button type="button" className="secondary" disabled={page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - page.limit))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('commerce.page.previous')}</button>
    <span className="muted" aria-live="polite">{pageLabel(page.offset, page.items.length, page.total)}</span>
    <button type="button" className="secondary" disabled={page.offset + page.items.length >= page.total} onClick={() => onPage(page.offset + page.limit)}>{t('commerce.page.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
  </nav>;
}

export function CommerceView({ onOpenApprovals }: { onOpenApprovals?: () => void } = {}) {
  const [setup, setSetup] = useState<Load<CommerceSetup>>({ kind: 'loading' });
  const [tab, setTab] = useState<Tab>(() => {
    try { const saved = sessionStorage.getItem('anum.commerce.tab'); return TABS.includes(saved as Tab) ? saved as Tab : 'overview'; } catch { return 'overview'; }
  });
  const load = useCallback(async () => {
    setSetup({ kind: 'loading' });
    try { setSetup({ kind: 'ready', value: await api.getSetup() }); } catch (e) { setSetup(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const pick = (next: Tab) => { setTab(next); try { sessionStorage.setItem('anum.commerce.tab', next); } catch { /* per page only */ } };

  const header = <PageHeader title={viewLabel('commerce')} subtitle={t('pages2.commerce.subtitle')} />;
  if (setup.kind === 'loading') return <>{header}<Notice busy>{t('commerce.loading')}</Notice></>;
  if (setup.kind === 'denied') return <>{header}<Denied text={t('commerce.denied.read')} /></>;
  if (setup.kind === 'error') return <>{header}<LoadError message={t('commerce.error.load', { message: setup.message })} retry={() => void load()} /></>;
  const { permissions } = setup.value;
  const noChannels = setup.value.connectors === 0;
  const active: Tab = noChannels && tab !== 'catalog' ? 'connections' : tab;
  return <div className="cmLayout">
    {header}
    {!noChannels && <TodayBand />}
    <nav className="settingsTabs cmTabs" aria-label={t('commerce.tabs')}>
      {TABS.map((id) => <button key={id} type="button" aria-current={active === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`commerce.tab.${id}` as MessageKey)}</button>)}
    </nav>
    {noChannels && active === 'connections' && <section className="surface cmPanel cmWelcome" aria-labelledby="cm-welcome">
      <Store aria-hidden="true" />
      <div><h2 id="cm-welcome">{t('commerce.welcome.title')}</h2><p className="muted">{permissions.manage ? t('commerce.welcome.owner') : t('commerce.welcome.member')}</p></div>
    </section>}
    {active === 'overview' && <Overview />}
    {active === 'orders' && <Orders canSeeBuyers={permissions.pii} />}
    {active === 'returns' && <Returns />}
    {active === 'catalog' && <Catalog permissions={permissions} onOpenApprovals={onOpenApprovals} />}
    {active === 'alerts' && <Alerts canAct={permissions.operate} />}
    {active === 'connections' && <Connections permissions={permissions} onChanged={() => void api.getSetup().then((value) => setSetup({ kind: 'ready', value })).catch(() => undefined)} />}
  </div>;
}

// ---- Overview ------------------------------------------------------------------------------

function Overview() {
  const [preset, setPreset] = useState<RangePreset>('30d');
  const [currency, setCurrency] = useState<string | null>(null);
  const [channel, setChannel] = useState<CommerceChannel | ''>('');
  const [state, setState] = useState<Load<CommerceDashboard>>({ kind: 'loading' });
  const range = useMemo(() => presetRange(preset), [preset]);
  const load = useCallback(async () => {
    setState((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.getDashboard(range.from, range.to, currency, channel || null) }); } catch (e) { setState(failed(e)); }
  }, [range, currency, channel]);
  useEffect(() => { void load(); }, [load]);

  const filters = <div className="cmFilters" role="group" aria-label={t('commerce.overview.filters')}>
    <div className="cmSegment" role="radiogroup" aria-label={t('commerce.overview.range')}>
      {RANGE_PRESETS.map((id) => <button key={id} type="button" role="radio" aria-checked={preset === id} onClick={() => setPreset(id)}>{t(`commerce.range.${id}` as MessageKey)}</button>)}
    </div>
    <label className="cmInline"><span>{t('commerce.overview.channel')}</span><select value={channel} onChange={(e) => setChannel(e.target.value as CommerceChannel | '')}><option value="">{t('commerce.channel.all')}</option>{CHANNELS.map((id) => <option key={id} value={id}>{channelLabel(id)}</option>)}</select></label>
    {state.kind === 'ready' && state.value.currencies.length > 1 && <label className="cmInline"><span>{t('commerce.overview.currency')}</span><select value={state.value.currency ?? ''} onChange={(e) => setCurrency(e.target.value)}>{state.value.currencies.map((c) => <option key={c.currency} value={c.currency}>{c.currency}</option>)}</select></label>}
  </div>;
  if (state.kind === 'loading') return <>{filters}<Notice busy>{t('commerce.overview.loading')}</Notice></>;
  if (state.kind === 'denied') return <Denied text={t('commerce.denied.read')} />;
  if (state.kind === 'error') return <>{filters}<LoadError message={t('commerce.error.dashboard', { message: state.message })} retry={() => void load()} /></>;
  const d = state.value;
  const main = d.currencies.find((c) => c.currency === d.currency);
  const returnRate = `${Math.round(d.returns.rate * 1000) / 10}%`;
  return <>
    {filters}
    {d.truncated && <Notice>{t('commerce.overview.truncated')}</Notice>}
    <section className="metrics cmMetrics" aria-label={t('commerce.overview.totals')}>
      <Metric icon={<TrendingUp />} label={t('commerce.metric.revenue')} value={main ? formatMoney(main.revenue, main.currency) : formatMoney(0, null)} detail={d.currencies.length > 1 ? d.currencies.filter((c) => c.currency !== d.currency).map((c) => formatMoney(c.revenue, c.currency)).join(' · ') : t('commerce.metric.revenueDetail', { from: d.start, to: d.end })} />
      <Metric icon={<ShoppingCart />} label={t('commerce.metric.orders')} value={formatCount(d.currencies.reduce((s, c) => s + c.orders, 0))} detail={t('commerce.metric.units', { count: d.currencies.reduce((s, c) => s + c.units, 0) })} />
      <Metric icon={<Truck />} label={t('commerce.metric.pending')} value={formatCount(d.pending_total)} detail={d.late_total ? t('commerce.metric.late', { count: d.late_total }) : t('commerce.metric.onTime')} tone={d.late_total ? 'danger' : undefined} />
      <Metric icon={<Undo2 />} label={t('commerce.metric.returns')} value={formatCount(d.returns.count)} detail={t('commerce.metric.returnRate', { rate: returnRate })} />
    </section>
    {d.connectors === 0 ? <div className="emptyState"><Plug aria-hidden="true" /><h3>{t('commerce.overview.noChannels')}</h3><p>{t('commerce.overview.noChannelsText')}</p></div> : <>
      <SalesChart dashboard={d} />
      <div className="cmGrid cmOverviewGrid">
        <section className="surface cmPanel cmWide" aria-labelledby="cm-by-channel">
          <h2 id="cm-by-channel">{t('commerce.overview.byChannel')}</h2>
          {d.by_channel.length === 0 ? <p className="muted cmEmpty">{t('commerce.overview.noSales')}</p> : <div className="cmTableWrap"><table className="cmTable">
            <thead><tr><th scope="col">{t('commerce.col.marketplace')}</th><th scope="col" className="num">{t('commerce.col.orders')}</th><th scope="col" className="num">{t('commerce.col.units')}</th><th scope="col" className="num">{t('commerce.col.revenue')}</th></tr></thead>
            <tbody>{d.by_channel.map((row) => <tr key={`${row.marketplace_id}-${row.currency}`}><td><ChannelDot channel={row.channel} />{row.marketplace}</td><td className="num">{formatCount(row.orders)}</td><td className="num">{formatCount(row.units)}</td><td className="num">{formatMoney(row.revenue, row.currency)}</td></tr>)}</tbody>
          </table></div>}
          <dl className="cmFacts cmMoney" aria-label={t('commerce.overview.money')}>
            <div><dt>{t('commerce.money.fees')}</dt><dd>{Object.keys(d.fees).length ? Object.entries(d.fees).map(([code, amount]) => formatMoney(amount, code)).join(' · ') : '-'}</dd></div>
            <div><dt>{t('commerce.money.refunds')}</dt><dd>{Object.keys(d.returns.amount).length ? Object.entries(d.returns.amount).map(([code, amount]) => formatMoney(amount, code)).join(' · ') : '-'}</dd></div>
            <div><dt>{t('commerce.money.alerts')}</dt><dd>{formatCount(d.open_alerts)}</dd></div>
          </dl>
        </section>
        <section className="surface cmPanel" aria-labelledby="cm-top">
          <h2 id="cm-top">{t('commerce.overview.top')}</h2>
          {d.top_products.length === 0 ? <p className="muted cmEmpty">{t('commerce.overview.noSales')}</p> : <ol className="cmRank">{d.top_products.map((p) => <li key={p.sku}>
            <div><strong>{displayTitle(p) || p.sku}</strong><small>{p.sku}{p.tyre_size ? ` · ${p.tyre_size}` : ''}</small></div>
            <span className="num">{t('commerce.overview.unitsSold', { count: p.units })}<small>{formatMoney(p.revenue, p.currency)}</small></span>
          </li>)}</ol>}
        </section>
        <section className="surface cmPanel" aria-labelledby="cm-pending">
          <h2 id="cm-pending">{t('commerce.overview.pending')}</h2>
          {d.pending_orders.length === 0 ? <p className="muted cmEmpty"><CheckCircle2 aria-hidden="true" />{t('commerce.overview.noPending')}</p> : <ul className="cmList">{d.pending_orders.map((o) => <li key={o.id}>
            <div><strong><ChannelDot channel={o.channel} />{o.external_id}</strong><small>{o.marketplace} · {o.ship_by ? t('commerce.order.shipBy', { when: when(o.ship_by) }) : when(o.purchased_at)}</small></div>
            <Badge tone={orderTone(o.status, o.late)}>{o.late ? t('commerce.order.late') : orderStatusLabel(o.status)}</Badge>
          </li>)}</ul>}
        </section>
        <StockAndPrices dashboard={d} />
      </div>
    </>}
  </>;
}

/** Low stock and buy-box or price alerts in one compact table: one row per listing that needs a look. */
function StockAndPrices({ dashboard: d }: { dashboard: CommerceDashboard }) {
  const rows = stockAndPrices(d.low_stock, d.price_alerts);
  return <section className="surface cmPanel cmWide" aria-labelledby="cm-stock-prices">
    <h2 id="cm-stock-prices">{t('pages2.commerce.stockPrices')}</h2>
    {rows.length === 0 ? <p className="muted cmEmpty"><CheckCircle2 aria-hidden="true" />{t('pages2.commerce.stockPricesNone')}</p> : <div className="cmTableWrap" tabIndex={0} role="group" aria-labelledby="cm-stock-prices"><table className="cmTable">
      <thead><tr><th scope="col">{t('pages2.commerce.col.sku')}</th><th scope="col">{t('commerce.col.marketplace')}</th><th scope="col">{t('pages2.commerce.col.stock')}</th><th scope="col">{t('pages2.commerce.col.price')}</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.listingId}>
        <td><strong>{row.sku}</strong></td>
        <td><ChannelDot channel={row.channel} />{row.marketplace}</td>
        <td>{row.quantity === null ? <span className="muted">-</span> : <Badge tone={row.quantity <= 0 ? 'dangerTone' : 'warning'}>{row.quantity <= 0 ? t('commerce.stock.out') : t('commerce.stock.left', { count: row.quantity })}</Badge>}</td>
        <td>{row.price ? <span className={row.price.buyBoxLost ? 'cmPriceNote' : undefined}>{row.price.buyBoxLost ? t('commerce.price.buyBoxLost', { price: formatMoney(row.price.buyBoxPrice, row.price.currency) }) : t('commerce.price.lowest', { price: formatMoney(row.price.lowest, row.price.currency) })}<small>{t('commerce.price.yours', { price: formatMoney(row.price.yours, row.price.currency) })}</small></span> : <span className="muted">-</span>}</td>
      </tr>)}</tbody>
    </table></div>}
    {rows.length >= 12 && <p className="muted">{t('pages2.commerce.stockPricesMore')}</p>}
  </section>;
}

/** The page's band: today's and the last seven days' sales from `GET /commerce/summary`, with its sentence. */
function TodayBand() {
  const [today, setToday] = useState<Load<SummaryNumbers & { text: string }>>({ kind: 'loading' });
  const [week, setWeek] = useState<Load<SummaryNumbers>>({ kind: 'loading' });
  const load = useCallback(() => {
    const lang = getLocale() === 'ar' ? 'ar' : 'en';
    setToday({ kind: 'loading' }); setWeek({ kind: 'loading' });
    api.getSummary('today', lang).then((value) => setToday({ kind: 'ready', value: { ...summaryFigures(value.data), text: value.text } }), (e: unknown) => setToday(failed(e)));
    api.getSummary('last_7_days', lang).then((value) => setWeek({ kind: 'ready', value: summaryFigures(value.data) }), (e: unknown) => setWeek(failed(e)));
  }, []);
  useEffect(load, [load]);
  const state = (l: Load<unknown>) => ({ state: l.kind === 'ready' ? 'ready' as const : l.kind === 'loading' ? 'loading' as const : 'error' as const });
  const t1 = today.kind === 'ready' ? today.value : null, w = week.kind === 'ready' ? week.value : null;
  const figures: Figure[] = [
    figure('orders', t('pages2.commerce.fig.ordersToday'), state(today), () => ({ value: formatCount(t1!.orders), hint: t('commerce.metric.units', { count: t1!.units }), tone: t1!.orders ? 'accent' : '' })),
    figure('revenue', t('pages2.commerce.fig.revenueToday'), state(today), () => ({ value: t1!.revenue.length ? formatMoney(t1!.revenue[0].amount, t1!.revenue[0].currency) : '-', hint: t1!.revenue.length > 1 ? t1!.revenue.slice(1).map((r) => formatMoney(r.amount, r.currency)).join(' · ') : t('pages2.commerce.fig.revenueHint') })),
    figure('week', t('pages2.commerce.fig.ordersWeek'), state(week), () => ({ value: formatCount(w!.orders), hint: w!.revenue.length ? w!.revenue.map((r) => formatMoney(r.amount, r.currency)).join(' · ') : t('pages2.commerce.fig.noSales') })),
    figure('top', t('pages2.commerce.fig.topWeek'), state(week), () => ({ value: w!.topSku ?? '-', hint: w!.topSku ? t('commerce.overview.unitsSold', { count: w!.topUnits }) : t('pages2.commerce.fig.noSales') })),
  ];
  return <PageSummary label={t('pages2.commerce.summary')} figures={figures} onRetry={load} footer={t1 ? <span dir="auto">{t1.text}</span> : undefined} />;
}

function Metric({ icon, label, value, detail, tone }: { icon: ReactNode; label: string; value: string; detail: string; tone?: 'danger' }) {
  return <article className={`metric ${tone === 'danger' ? 'cmMetricDanger' : ''}`}><div className="metricTop"><span>{label}</span><div className="metricIcon" aria-hidden="true">{icon}</div></div><strong>{value}</strong><small>{detail}</small></article>;
}

function SalesChart({ dashboard }: { dashboard: CommerceDashboard }) {
  const chart = useMemo(() => salesChart(dashboard), [dashboard]);
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();
  const max = niceMax(chart.max);
  const width = 720, height = 220, pad = { top: 12, bottom: 24, left: 8, right: 8 };
  const inner = width - pad.left - pad.right, plot = height - pad.top - pad.bottom;
  const band = inner / Math.max(1, chart.days.length);
  const bar = Math.max(2, Math.min(28, band - 2));
  const shown = hover === null ? null : chart.days[hover];
  const labelEvery = Math.ceil(chart.days.length / 7);
  return <section className="surface cmPanel cmChartPanel" aria-labelledby={titleId}>
    <div className="sectionHeader"><h2 id={titleId}>{t('commerce.chart.title', { currency: dashboard.currency ?? '-' })}</h2>
      {chart.channels.length > 0 && <ul className="cmLegend" aria-label={t('commerce.chart.legend')}>{chart.channels.map((c) => <li key={c}><ChannelDot channel={c} />{channelLabel(c)}</li>)}</ul>}
    </div>
    {chart.max === 0 ? <p className="muted cmEmpty">{t('commerce.overview.noSales')}</p> : <>
      <div className="cmChart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t('commerce.chart.aria', { days: chart.days.length, max: formatMoney(chart.max, dashboard.currency) })} preserveAspectRatio="none" onMouseLeave={() => setHover(null)}>
          {[0.5, 1].map((f) => <line key={f} className="cmGridLine" x1={pad.left} x2={width - pad.right} y1={pad.top + plot * (1 - f)} y2={pad.top + plot * (1 - f)} />)}
          <line className="cmAxis" x1={pad.left} x2={width - pad.right} y1={pad.top + plot} y2={pad.top + plot} />
          {chart.days.map((day, index) => {
            let y = pad.top + plot;
            const x = pad.left + index * band + (band - bar) / 2;
            return <g key={day.date} onMouseEnter={() => setHover(index)}>
              <rect className="cmHit" x={pad.left + index * band} y={pad.top} width={band} height={plot} />
              {day.parts.filter((p) => p.revenue > 0).map((part, i, list) => {
                const h = Math.max(1, (part.revenue / max) * plot - (i < list.length - 1 ? 2 : 0));
                y -= (part.revenue / max) * plot;
                return <rect key={part.channel} x={x} y={y} width={bar} height={h} rx={i === list.length - 1 ? Math.min(4, bar / 2) : 0} fill={CHANNEL_COLORS[part.channel]} opacity={hover === null || hover === index ? 1 : 0.55} />;
              })}
              {index % labelEvery === 0 && <text className="cmTick" x={x + bar / 2} y={height - 6} textAnchor="middle">{day.date.slice(5)}</text>}
            </g>;
          })}
        </svg>
        {shown && <div className="cmTooltip" role="status"><strong>{shown.date}</strong>{shown.parts.map((p) => <span key={p.channel}><ChannelDot channel={p.channel} />{channelLabel(p.channel)}: {formatMoney(p.revenue, dashboard.currency)}</span>)}<span>{t('commerce.chart.total', { amount: formatMoney(shown.total, dashboard.currency) })}</span></div>}
      </div>
      <details className="cmChartTable"><summary>{t('commerce.chart.table')}</summary><div className="cmTableWrap"><table className="cmTable">
        <thead><tr><th scope="col">{t('commerce.col.date')}</th>{chart.channels.map((c) => <th key={c} scope="col" className="num">{channelLabel(c)}</th>)}<th scope="col" className="num">{t('commerce.col.total')}</th></tr></thead>
        <tbody>{chart.days.filter((d) => d.total > 0).map((d) => <tr key={d.date}><td>{d.date}</td>{d.parts.map((p) => <td key={p.channel} className="num">{formatMoney(p.revenue, dashboard.currency)}</td>)}<td className="num">{formatMoney(d.total, dashboard.currency)}</td></tr>)}</tbody>
      </table></div></details>
    </>}
  </section>;
}

// ---- Orders ------------------------------------------------------------------------------------

function Orders({ canSeeBuyers }: { canSeeBuyers: boolean }) {
  const [status, setStatus] = useState('open'), [channel, setChannel] = useState(''), [draft, setDraft] = useState(''), [q, setQ] = useState(''), [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<CommercePage<CommerceOrder>>>({ kind: 'loading' });
  const [open, setOpen] = useState<Load<CommerceOrderDetail> | null>(null);
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.listOrders({ status: status || undefined, channel: channel || undefined, q, limit: PAGE, offset }) }); } catch (e) { setState(failed(e)); }
  }, [status, channel, q, offset]);
  useEffect(() => { void load(); }, [load]);
  async function show(order: CommerceOrder) {
    setOpen({ kind: 'loading' });
    try { setOpen({ kind: 'ready', value: await api.getOrder(order.id) }); } catch (e) { setOpen(failed(e)); }
  }
  return <section className="surface cmPanel" aria-labelledby="cm-orders">
    <div className="sectionHeader"><h2 id="cm-orders">{t('commerce.orders.title')}</h2></div>
    <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setQ(draft); }}>
      <label className="cmInline"><span>{t('commerce.orders.status')}</span><select value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value); }}>
        <option value="">{t('commerce.orderStatus.all')}</option>
        {(['open', 'pending', 'unshipped', 'shipped', 'delivered', 'cancelled', 'returned'] as const).map((s) => <option key={s} value={s}>{orderStatusLabel(s)}</option>)}
      </select></label>
      <label className="cmInline"><span>{t('commerce.overview.channel')}</span><select value={channel} onChange={(e) => { setOffset(0); setChannel(e.target.value); }}><option value="">{t('commerce.channel.all')}</option>{CHANNELS.map((id) => <option key={id} value={id}>{channelLabel(id)}</option>)}</select></label>
      <label className="cmSearch"><span className="visuallyHidden">{t('commerce.orders.search')}</span><input type="search" value={draft} placeholder={t('commerce.orders.search')} onChange={(e) => setDraft(e.target.value)} /></label>
      <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
    </form>
    {state.kind === 'loading' && <Notice busy>{t('commerce.orders.loading')}</Notice>}
    {state.kind === 'denied' && <Notice error>{t('commerce.denied.read')}</Notice>}
    {state.kind === 'error' && <LoadError message={t('commerce.error.orders', { message: state.message })} retry={() => void load()} />}
    {state.kind === 'ready' && (state.value.items.length === 0
      ? <div className="emptyState"><PackageSearch aria-hidden="true" /><h3>{t('commerce.orders.empty')}</h3><p>{q || status || channel ? t('commerce.orders.emptyFiltered') : t('commerce.orders.emptyText')}</p></div>
      : <>
        <div className="cmTableWrap"><table className="cmTable cmOrders">
          <thead><tr><th scope="col">{t('commerce.col.order')}</th><th scope="col">{t('commerce.col.marketplace')}</th><th scope="col">{t('commerce.col.placed')}</th><th scope="col">{t('commerce.col.status')}</th><th scope="col" className="num">{t('commerce.col.items')}</th><th scope="col" className="num">{t('commerce.col.total')}</th></tr></thead>
          <tbody>{state.value.items.map((o) => <tr key={o.id}>
            <td><button type="button" className="cmLinkButton" onClick={() => void show(o)} aria-label={t('commerce.orders.open', { order: o.external_id })}>{o.external_id}</button></td>
            <td><ChannelDot channel={o.channel} />{o.marketplace}</td>
            <td>{when(o.purchased_at)}</td>
            <td><Badge tone={orderTone(o.status, o.late)}>{o.late ? t('commerce.order.late') : orderStatusLabel(o.status)}</Badge></td>
            <td className="num">{formatCount(o.item_count)}</td>
            <td className="num">{formatMoney(o.total_amount, o.currency)}</td>
          </tr>)}</tbody>
        </table></div>
        <Pager page={state.value} onPage={setOffset} label={t('commerce.orders.pages')} />
      </>)}
    {open && <OrderDetail state={open} canSeeBuyers={canSeeBuyers} onClose={() => setOpen(null)} />}
  </section>;
}

function OrderDetail({ state, canSeeBuyers, onClose }: { state: Load<CommerceOrderDetail>; canSeeBuyers: boolean; onClose: () => void }) {
  const headingId = useId();
  if (state.kind === 'loading') return <Notice busy>{t('commerce.order.loading')}</Notice>;
  if (state.kind !== 'ready') return <Notice error>{state.kind === 'error' ? t('commerce.error.order', { message: state.message }) : t('commerce.denied.read')}</Notice>;
  const o = state.value;
  return <section className="cmDetail" aria-labelledby={headingId}>
    <div className="sectionHeader"><h3 id={headingId}>{t('commerce.order.title', { order: o.external_id })}</h3><button type="button" className="iconButton" onClick={onClose} aria-label={t('commerce.order.close')}><X aria-hidden="true" /></button></div>
    <dl className="cmFacts">
      <div><dt>{t('commerce.col.marketplace')}</dt><dd><ChannelDot channel={o.channel} />{o.marketplace}</dd></div>
      <div><dt>{t('commerce.col.status')}</dt><dd>{orderStatusLabel(o.status)}{o.late ? ` · ${t('commerce.order.late')}` : ''}</dd></div>
      <div><dt>{t('commerce.col.placed')}</dt><dd>{when(o.purchased_at)}</dd></div>
      <div><dt>{t('commerce.order.shipByLabel')}</dt><dd>{when(o.ship_by)}</dd></div>
      <div><dt>{t('commerce.order.destination')}</dt><dd>{[o.ship_city, o.ship_country].filter(Boolean).join(', ') || '-'}</dd></div>
      <div><dt>{t('commerce.col.total')}</dt><dd>{formatMoney(o.total_amount, o.currency)}</dd></div>
    </dl>
    <div className="cmTableWrap"><table className="cmTable"><thead><tr><th scope="col">{t('commerce.col.sku')}</th><th scope="col">{t('commerce.col.title')}</th><th scope="col" className="num">{t('commerce.col.qty')}</th><th scope="col" className="num">{t('commerce.col.total')}</th></tr></thead>
      <tbody>{o.lines.length ? o.lines.map((line, i) => <tr key={`${line.sku}-${i}`}><td>{line.sku}</td><td>{line.title}</td><td className="num">{line.quantity}</td><td className="num">{formatMoney(line.total, o.currency)}</td></tr>) : <tr><td colSpan={4} className="muted">{t('commerce.order.noLines')}</td></tr>}</tbody></table></div>
    <h4>{t('commerce.order.buyer')}</h4>
    {!o.customer ? <p className="muted">{t('commerce.order.noBuyer')}</p> : o.customer.pii_visible ? <dl className="cmFacts">
      <div><dt>{t('commerce.order.name')}</dt><dd>{o.customer.name ?? '-'}</dd></div><div><dt>{t('commerce.order.email')}</dt><dd>{o.customer.email ?? '-'}</dd></div>
      <div><dt>{t('commerce.order.phone')}</dt><dd>{o.customer.phone ?? '-'}</dd></div><div><dt>{t('commerce.order.address')}</dt><dd>{o.customer.address ?? '-'}</dd></div>
    </dl> : <p className="muted"><Lock aria-hidden="true" className="cmInlineIcon" />{canSeeBuyers ? t('commerce.order.buyerHidden') : t('commerce.order.buyerOwnersOnly')}</p>}
    {o.returns.length > 0 && <><h4>{t('commerce.order.returns')}</h4><ul className="cmList">{o.returns.map((r) => <li key={r.id}><div><strong>{r.sku ?? '-'} × {r.quantity}</strong><small>{r.reason ?? r.status} · {when(r.returned_at)}</small></div><span>{formatMoney(r.amount, r.currency)}</span></li>)}</ul></>}
  </section>;
}

// ---- Catalog --------------------------------------------------------------------------------

function tyreText(tyre: TyreSpec): string {
  const parts = [tyre.size, tyre.load_index && tyre.speed_index ? `${tyre.load_index}${tyre.speed_index}` : null, tyre.dot_year ? `DOT ${tyre.dot_week ? String(tyre.dot_week).padStart(2, '0') + '/' : ''}${tyre.dot_year}` : null];
  return parts.filter(Boolean).join(' · ');
}

function Catalog({ permissions, onOpenApprovals }: { permissions: CommerceSetup['permissions']; onOpenApprovals?: () => void }) {
  const [draft, setDraft] = useState(''), [q, setQ] = useState(''), [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<CommercePage<CommerceProduct>>>({ kind: 'loading' });
  const [editing, setEditing] = useState<CommerceProduct | 'new' | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [pending, setPending] = useState<{ approval: Approval; listing: CommerceListing; kind: 'price' | 'stock' } | null>(null);
  const load = useCallback(async () => {
    setState((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.listProducts(q, PAGE, offset) }); } catch (e) { setState(failed(e)); }
  }, [q, offset]);
  useEffect(() => { void load(); }, [load]);
  async function remove(product: CommerceProduct) {
    if (!confirm(t('commerce.catalog.confirmDelete', { sku: product.sku }))) return;
    try { await api.deleteProduct(product.id); setNotice({ text: t('commerce.catalog.deleted', { sku: product.sku }) }); await load(); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  return <>
    {pending && <section className="surface cmPanel cmPending" aria-labelledby="cm-pending-approval" aria-live="polite">
      <ShieldCheck aria-hidden="true" />
      <div>
        <h2 id="cm-pending-approval">{t('commerce.change.waiting')}</h2>
        <p>{pending.kind === 'price'
          ? t('commerce.change.waitingPrice', { sku: pending.listing.sku, marketplace: pending.listing.marketplace, price: formatMoney(Number(pending.approval.arguments?.price), pending.listing.currency) })
          : t('commerce.change.waitingStock', { sku: pending.listing.sku, marketplace: pending.listing.marketplace, quantity: String(pending.approval.arguments?.quantity ?? '') })}</p>
        <p className="muted">{t('commerce.change.hash', { hash: shortHash(pending.approval.payloadHash) })}</p>
        <div className="actions">{onOpenApprovals && <button type="button" onClick={onOpenApprovals}><ShieldCheck aria-hidden="true" />{t('commerce.change.openApprovals')}</button>}<button type="button" className="secondary" onClick={() => setPending(null)}>{t('commerce.change.dismiss')}</button></div>
      </div>
    </section>}
    <section className="surface cmPanel" aria-labelledby="cm-catalog">
      <div className="sectionHeader"><h2 id="cm-catalog">{t('commerce.catalog.title')}</h2>
        {permissions.operate ? <button type="button" className="cmPrimary" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('commerce.catalog.add')}</button> : <span className="muted">{t('commerce.catalog.readOnly')}</span>}
      </div>
      <form className="cmFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setQ(draft); }}>
        <label className="cmSearch"><span className="visuallyHidden">{t('commerce.catalog.search')}</span><input type="search" value={draft} placeholder={t('commerce.catalog.search')} onChange={(e) => setDraft(e.target.value)} /></label>
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
      </form>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {editing && <ProductForm product={editing === 'new' ? null : editing} onDone={(text) => { setEditing(null); if (text) { setNotice({ text }); void load(); } }} />}
      {state.kind === 'loading' && <Notice busy>{t('commerce.catalog.loading')}</Notice>}
      {state.kind === 'denied' && <Notice error>{t('commerce.denied.read')}</Notice>}
      {state.kind === 'error' && <LoadError message={t('commerce.error.catalog', { message: state.message })} retry={() => void load()} />}
      {state.kind === 'ready' && (state.value.items.length === 0
        ? <div className="emptyState"><Boxes aria-hidden="true" /><h3>{t('commerce.catalog.empty')}</h3><p>{q ? t('commerce.catalog.emptyFiltered') : t('commerce.catalog.emptyText')}</p></div>
        : <><ul className="cmProducts">{state.value.items.map((p) => <li key={p.id} className="cmProduct">
          <div className="cmProductHead">
            <div><strong lang={p.title_ar && document.documentElement.lang === 'ar' ? 'ar' : undefined}>{displayTitle(p) || p.sku}</strong><small>{p.sku}{p.brand ? ` · ${p.brand}` : ''}{tyreText(p.tyre) ? ` · ${tyreText(p.tyre)}` : ''}</small></div>
            <div className="cmProductActions">
              <span className="muted">{p.total_stock === null ? t('commerce.stock.unknown') : t('commerce.stock.total', { count: p.total_stock })}</span>
              {permissions.operate && <button type="button" className="iconButton" onClick={() => setEditing(p)} aria-label={t('commerce.catalog.edit', { sku: p.sku })}><Pencil aria-hidden="true" /></button>}
              {permissions.operate && p.listings.length === 0 && <button type="button" className="iconButton" onClick={() => void remove(p)} aria-label={t('commerce.catalog.delete', { sku: p.sku })}><Trash2 aria-hidden="true" /></button>}
            </div>
          </div>
          {p.listings.length === 0 ? <p className="muted">{t('commerce.catalog.notListed')}</p> : <ul className="cmListings">{p.listings.map((l) => <ListingRow key={l.id} listing={l} canChange={permissions.operate} onProposed={(approval, kind) => { setPending({ approval, listing: l, kind }); void load(); }} />)}</ul>}
        </li>)}</ul><Pager page={state.value} onPage={setOffset} label={t('commerce.catalog.pages')} /></>)}
    </section>
  </>;
}

function ListingRow({ listing, canChange, onProposed }: { listing: CommerceListing; canChange: boolean; onProposed: (approval: Approval, kind: 'price' | 'stock') => void }) {
  const [mode, setMode] = useState<'price' | 'stock' | null>(null), [value, setValue] = useState(''), [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const inputId = useId(), errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!mode) return;
    const parsed = mode === 'price' ? parsePrice(value) : parseQuantity(value);
    if (!parsed.ok) { setError(parsed.error); return; }
    setBusy(true); setError(null);
    try { const result = await api.proposeChange(listing.id, mode, parsed.value); onProposed(result.approval, mode); setMode(null); setValue(''); }
    catch (e) { setError(isPermissionDenied(e) ? t('commerce.change.denied') : describeError(e)); }
    finally { setBusy(false); }
  }
  return <li className="cmListing">
    <div className="cmListingMain">
      <span className="cmListingWhere"><ChannelDot channel={listing.channel} />{listing.marketplace}{listing.external_id ? <small> · {listing.external_id}</small> : null}</span>
      <span className="num">{formatMoney(listing.price, listing.currency)}</span>
      <span className="num">{listing.quantity === null ? '-' : listing.quantity <= 0 ? <Badge tone="dangerTone">{t('commerce.stock.out')}</Badge> : t('commerce.stock.left', { count: listing.quantity })}</span>
      {listing.buy_box_winner === false && <Badge tone="warning">{t('commerce.price.buyBoxLostShort')}</Badge>}
      {canChange && <span className="cmListingButtons">
        <button type="button" className="secondary" onClick={() => { setMode('price'); setValue(listing.price ? String(listing.price) : ''); setError(null); }}>{t('commerce.change.price')}</button>
        {listing.fulfillment !== 'amazon' && listing.fulfillment !== 'noon' && <button type="button" className="secondary" onClick={() => { setMode('stock'); setValue(listing.quantity === null ? '' : String(listing.quantity)); setError(null); }}>{t('commerce.change.stock')}</button>}
      </span>}
    </div>
    {mode && <form className="cmChangeForm" onSubmit={(e) => void submit(e)}>
      <label htmlFor={inputId}>{mode === 'price' ? t('commerce.change.newPrice', { currency: listing.currency ?? '' }) : t('commerce.change.newStock')}</label>
      <input id={inputId} inputMode={mode === 'price' ? 'decimal' : 'numeric'} value={value} onChange={(e) => setValue(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? errorId : undefined} autoFocus />
      <button type="submit" disabled={busy}>{busy ? t('commerce.change.sending') : t('commerce.change.request')}</button>
      <button type="button" className="secondary" onClick={() => setMode(null)}>{t('common.cancel')}</button>
      <small className="muted">{t('commerce.change.explain')}</small>
      {error && <p id={errorId} className="cmFieldError" role="alert">{error}</p>}
    </form>}
  </li>;
}

function ProductForm({ product, onDone }: { product: CommerceProduct | null; onDone: (message: string | null) => void }) {
  const [sku, setSku] = useState(product?.sku ?? ''), [title, setTitle] = useState(product?.title ?? ''), [titleAr, setTitleAr] = useState(product?.title_ar ?? '');
  const [brand, setBrand] = useState(product?.brand ?? ''), [size, setSize] = useState(product?.tyre.size ?? ''), [load, setLoad] = useState(product?.tyre.load_index ?? '');
  const [speed, setSpeed] = useState(product?.tyre.speed_index ?? ''), [dotWeek, setDotWeek] = useState(product?.tyre.dot_week ? String(product.tyre.dot_week) : ''), [dotYear, setDotYear] = useState(product?.tyre.dot_year ? String(product.tyre.dot_year) : '');
  const [threshold, setThreshold] = useState(product?.low_stock_threshold === null || product?.low_stock_threshold === undefined ? '' : String(product.low_stock_threshold));
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const headingId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!product && !/^[A-Za-z0-9][A-Za-z0-9._/ -]{0,119}$/.test(sku.trim())) { setError(t('commerce.catalog.error.sku')); return; }
    if (threshold && !/^\d{1,6}$/.test(threshold)) { setError(t('commerce.catalog.error.threshold')); return; }
    if ((dotWeek && !/^([1-9]|[1-4]\d|5[0-3])$/.test(dotWeek)) || (dotYear && !/^(19|20)\d{2}$/.test(dotYear))) { setError(t('commerce.catalog.error.dot')); return; }
    if (speed && !/^[LMNPQRSTUHVWYZ]$/i.test(speed)) { setError(t('commerce.catalog.error.speed')); return; }
    const sizeMatch = size.trim().toUpperCase().replace(/\s+/g, '').match(/^(\d{3})\/(\d{2})(Z?R)(\d{2}(?:\.\d)?)$/);
    if (size.trim() && !sizeMatch) { setError(t('commerce.catalog.error.size')); return; }
    const tyre: TyreSpec = sizeMatch ? { size: `${sizeMatch[1]}/${sizeMatch[2]}${sizeMatch[3]}${sizeMatch[4]}`, width_mm: Number(sizeMatch[1]), aspect_ratio: Number(sizeMatch[2]), construction: sizeMatch[3], rim_inches: Number(sizeMatch[4]) } : {};
    if (load.trim()) tyre.load_index = load.trim();
    if (speed.trim()) tyre.speed_index = speed.trim().toUpperCase();
    if (dotWeek) tyre.dot_week = Number(dotWeek);
    if (dotYear) tyre.dot_year = Number(dotYear);
    const body = { title: title.trim(), title_ar: titleAr.trim() || null, brand: brand.trim() || null, tyre, low_stock_threshold: threshold ? Number(threshold) : null };
    setBusy(true); setError(null);
    try {
      if (product) { await api.updateProduct(product.id, body); onDone(t('commerce.catalog.saved', { sku: product.sku })); }
      else { await api.createProduct({ ...body, sku: sku.trim() }); onDone(t('commerce.catalog.created', { sku: sku.trim() })); }
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <form className="cmForm" aria-labelledby={headingId} onSubmit={(e) => void submit(e)}>
    <h3 id={headingId}>{product ? t('commerce.catalog.editTitle', { sku: product.sku }) : t('commerce.catalog.newTitle')}</h3>
    <div className="cmFormGrid">
      {!product && <label className="field"><span>{t('commerce.col.sku')}</span><input value={sku} onChange={(e) => setSku(e.target.value)} required maxLength={120} /></label>}
      <label className="field"><span>{t('commerce.catalog.titleEn')}</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} /></label>
      <label className="field"><span>{t('commerce.catalog.titleAr')}</span><input value={titleAr} onChange={(e) => setTitleAr(e.target.value)} maxLength={500} dir="rtl" lang="ar" /></label>
      <label className="field"><span>{t('commerce.catalog.brand')}</span><input value={brand} onChange={(e) => setBrand(e.target.value)} maxLength={120} /></label>
      <label className="field"><span>{t('commerce.catalog.size')}</span><input value={size} onChange={(e) => setSize(e.target.value)} placeholder="205/55R16" dir="ltr" /></label>
      <label className="field"><span>{t('commerce.catalog.load')}</span><input value={load} onChange={(e) => setLoad(e.target.value)} placeholder="91" maxLength={8} dir="ltr" /></label>
      <label className="field"><span>{t('commerce.catalog.speed')}</span><input value={speed} onChange={(e) => setSpeed(e.target.value)} placeholder="V" maxLength={1} dir="ltr" /></label>
      <label className="field"><span>{t('commerce.catalog.dotWeek')}</span><input value={dotWeek} onChange={(e) => setDotWeek(e.target.value)} inputMode="numeric" placeholder="23" dir="ltr" /></label>
      <label className="field"><span>{t('commerce.catalog.dotYear')}</span><input value={dotYear} onChange={(e) => setDotYear(e.target.value)} inputMode="numeric" placeholder="2025" dir="ltr" /></label>
      <label className="field"><span>{t('commerce.catalog.threshold')}</span><input value={threshold} onChange={(e) => setThreshold(e.target.value)} inputMode="numeric" placeholder={t('commerce.catalog.thresholdDefault')} /></label>
    </div>
    <p className="muted">{t('commerce.catalog.localOnly')}</p>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('commerce.saving') : t('commerce.save')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('common.cancel')}</button></div>
  </form>;
}

// ---- Alerts ---------------------------------------------------------------------------------

// ---- Returns -------------------------------------------------------------------------------

const RETURN_WINDOWS = [7, 30, 90] as const;
const RETURN_STATUSES = ['requested', 'approved', 'received', 'refunded', 'completed', 'rejected', 'cancelled'];
/** A marketplace's return status in words; a status ANUM does not know shows as the marketplace sent it. */
const returnStatus = (status: string) => (RETURN_STATUSES.includes(status) ? t(`pages2.commerce.returnStatus.${status}` as MessageKey) : status);

function Returns() {
  const [days, setDays] = useState<number>(30), [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<CommercePage<CommerceReturn>>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.listReturns(days, PAGE, offset) }); } catch (e) { setState(failed(e)); }
  }, [days, offset]);
  useEffect(() => { void load(); }, [load]);
  return <section className="surface cmPanel" aria-labelledby="cm-returns">
    <div className="sectionHeader"><h2 id="cm-returns">{t('pages2.commerce.returns.title')}</h2>
      <div className="cmSegment" role="radiogroup" aria-label={t('pages2.commerce.returns.window')}>
        {RETURN_WINDOWS.map((value) => <button key={value} type="button" role="radio" aria-checked={days === value} onClick={() => { setOffset(0); setDays(value); }}>{t('pages2.commerce.returns.days', { count: value })}</button>)}
      </div>
    </div>
    <p className="muted">{t('pages2.commerce.returns.intro')}</p>
    {state.kind === 'loading' && <Notice busy>{t('pages2.commerce.returns.loading')}</Notice>}
    {state.kind === 'denied' && <Notice error>{t('commerce.denied.read')}</Notice>}
    {state.kind === 'error' && <LoadError message={t('pages2.commerce.returns.failed', { message: state.message })} retry={() => void load()} />}
    {state.kind === 'ready' && state.value.items.length === 0 && <div className="emptyState"><Undo2 aria-hidden="true" /><h3>{t('pages2.commerce.returns.empty', { count: days })}</h3><p>{t('pages2.commerce.returns.emptyText')}</p></div>}
    {state.kind === 'ready' && state.value.items.length > 0 && <>
      <div className="cmTableWrap"><table className="cmTable cmReturns">
        <thead><tr><th scope="col">{t('pages2.commerce.returns.col.when')}</th><th scope="col">{t('commerce.col.order')}</th><th scope="col">{t('pages2.commerce.col.sku')}</th><th scope="col" className="num">{t('pages2.commerce.returns.col.qty')}</th><th scope="col">{t('pages2.commerce.returns.col.reason')}</th><th scope="col">{t('commerce.col.status')}</th><th scope="col" className="num">{t('pages2.commerce.returns.col.amount')}</th></tr></thead>
        <tbody>{state.value.items.map((row) => <tr key={row.id}>
          <td>{when(row.returned_at)}</td>
          <td><ChannelDot channel={row.channel} /><span dir="ltr">{row.order_external_id}</span><small>{channelLabel(row.channel)}</small></td>
          <td dir="ltr">{row.sku ?? '-'}</td>
          <td className="num">{formatCount(row.quantity)}</td>
          <td dir="auto">{row.reason ?? <span className="muted">{t('pages2.commerce.returns.noReason')}</span>}</td>
          <td><Badge tone={row.status === 'completed' || row.status === 'refunded' ? 'success' : row.status === 'rejected' ? 'dangerTone' : 'warning'}>{returnStatus(row.status)}</Badge></td>
          <td className="num">{row.amount === null ? '-' : formatMoney(row.amount, row.currency)}</td>
        </tr>)}</tbody>
      </table></div>
      <Pager page={state.value} onPage={setOffset} label={t('pages2.commerce.returns.pages')} />
    </>}
  </section>;
}

function Alerts({ canAct }: { canAct: boolean }) {
  const [status, setStatus] = useState('open'), [offset, setOffset] = useState(0);
  const [state, setState] = useState<Load<CommercePage<CommerceAlert>>>({ kind: 'loading' });
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setState((current) => current.kind === 'ready' ? current : { kind: 'loading' });
    try { setState({ kind: 'ready', value: await api.listAlerts(status, PAGE, offset) }); } catch (e) { setState(failed(e)); }
  }, [status, offset]);
  useEffect(() => { void load(); }, [load]);
  async function acknowledge(alert: CommerceAlert) {
    setError(null);
    try { await api.acknowledgeAlert(alert.id); await load(); } catch (e) { setError(isPermissionDenied(e) ? t('commerce.alerts.denied') : describeError(e)); }
  }
  return <section className="surface cmPanel" aria-labelledby="cm-alerts">
    <div className="sectionHeader"><h2 id="cm-alerts">{t('commerce.alerts.title')}</h2>
      <div className="cmSegment" role="radiogroup" aria-label={t('commerce.alerts.status')}>{(['open', 'acknowledged', 'resolved'] as const).map((s) => <button key={s} type="button" role="radio" aria-checked={status === s} onClick={() => { setOffset(0); setStatus(s); }}>{t(`commerce.alerts.${s}` as MessageKey)}</button>)}</div>
    </div>
    <p className="muted">{t('commerce.alerts.explain')}</p>
    {error && <Notice error>{error}</Notice>}
    {state.kind === 'loading' && <Notice busy>{t('commerce.alerts.loading')}</Notice>}
    {state.kind === 'denied' && <Notice error>{t('commerce.denied.read')}</Notice>}
    {state.kind === 'error' && <LoadError message={t('commerce.error.alerts', { message: state.message })} retry={() => void load()} />}
    {state.kind === 'ready' && (state.value.items.length === 0
      ? <div className="emptyState"><BellRing aria-hidden="true" /><h3>{t('commerce.alerts.empty')}</h3><p>{t('commerce.alerts.emptyText')}</p></div>
      : <><ul className="cmAlerts">{state.value.items.map((a) => <li key={a.id} className={`cmAlert ${a.severity}`}>
        <AlertTriangle aria-hidden="true" />
        <div><strong>{a.title}</strong><small>{a.detail}</small><small>{t(`commerce.alertKind.${a.kind}` as MessageKey)} · {when(a.updated_at)}</small></div>
        <Badge tone={severityTone(a.severity)}>{t(`commerce.severity.${a.severity}` as MessageKey)}</Badge>
        {a.status === 'open' && canAct && <button type="button" className="secondary" onClick={() => void acknowledge(a)}>{t('commerce.alerts.acknowledge')}</button>}
      </li>)}</ul><Pager page={state.value} onPage={setOffset} label={t('commerce.alerts.pages')} /></>)}
    {!canAct && state.kind === 'ready' && state.value.items.length > 0 && <p className="muted">{t('commerce.alerts.viewerNote')}</p>}
  </section>;
}

// ---- Connections -----------------------------------------------------------------------------

function Connections({ permissions, onChanged }: { permissions: CommerceSetup['permissions']; onChanged: () => void }) {
  const [state, setState] = useState<Load<CommerceConnector[]>>({ kind: 'loading' });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<CommerceConnector | null>(null);
  const [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const [secret, setSecret] = useState<{ secret: string; path: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setState({ kind: 'ready', value: await api.listConnectors() }); } catch (e) { setState(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function act(id: string, kind: string, fn: () => Promise<string | void>) {
    setBusy(`${kind}:${id}`); setNotice(null);
    try { const text = await fn(); if (text) setNotice({ text }); await load(); onChanged(); }
    catch (e) { setNotice({ error: true, text: isPermissionDenied(e) ? t('commerce.connections.denied') : describeError(e) }); }
    finally { setBusy(null); }
  }
  const sync = (c: CommerceConnector) => act(c.id, 'sync', async () => { const r = await api.syncConnector(c.id); return t(`commerce.sync.${r.status}` as MessageKey, { name: c.name, message: r.message, count: Object.values(r.counts).reduce((s, n) => s + n, 0) }); });
  const test = (c: CommerceConnector) => act(c.id, 'test', async () => { const r = await api.testConnector(c.id); return r.ok ? t('commerce.connections.testOk', { message: r.message }) : t('commerce.connections.testFailed', { message: r.message }); });
  const toggle = (c: CommerceConnector) => act(c.id, 'toggle', async () => { await api.updateConnector(c.id, { enabled: !c.enabled }); return c.enabled ? t('commerce.connections.turnedOff', { name: c.name }) : t('commerce.connections.turnedOn', { name: c.name }); });
  const remove = (c: CommerceConnector) => act(c.id, 'delete', async () => {
    if (!confirm(t('commerce.connections.confirmDelete', { name: c.name }))) return;
    await api.deleteConnector(c.id); return t('commerce.connections.deleted', { name: c.name });
  });
  const rotate = (c: CommerceConnector) => act(c.id, 'rotate', async () => {
    if (!confirm(t('commerce.connections.confirmRotate'))) return;
    const r = await api.rotateWebhookSecret(c.id); if (r.webhook_secret && r.connector.webhook_path) setSecret({ secret: r.webhook_secret, path: r.connector.webhook_path });
  });
  return <>
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {secret && <section className="surface cmPanel cmSecret" aria-live="polite" aria-labelledby="cm-secret">
      <h2 id="cm-secret">{t('commerce.webhook.title')}</h2><p>{t('commerce.webhook.once')}</p>
      <dl className="cmFacts"><div><dt>{t('commerce.webhook.url')}</dt><dd><code>{api_base()}{secret.path}</code></dd></div><div><dt>{t('commerce.webhook.secret')}</dt><dd><code>{secret.secret}</code></dd></div></dl>
      <div className="actions"><button type="button" className="secondary" onClick={() => setSecret(null)}>{t('commerce.webhook.done')}</button></div>
    </section>}
    <section className="surface cmPanel" aria-labelledby="cm-connections">
      <div className="sectionHeader"><h2 id="cm-connections">{t('commerce.connections.title')}</h2>
        {permissions.manage ? !adding && <button type="button" className="cmPrimary" onClick={() => { setAdding(true); setEditing(null); }}><Plus aria-hidden="true" />{t('commerce.connections.add')}</button> : <span className="muted">{t('commerce.connections.ownersOnly')}</span>}
      </div>
      {adding && <ConnectorEditor onDone={(created) => { setAdding(false); if (created) { if (created.secret && created.connector.webhook_path) setSecret({ secret: created.secret, path: created.connector.webhook_path }); setNotice({ text: t('commerce.connections.created', { name: created.connector.name }) }); void load(); onChanged(); } }} />}
      {state.kind === 'loading' && <Notice busy>{t('commerce.connections.loading')}</Notice>}
      {state.kind === 'denied' && <Notice error>{t('commerce.denied.read')}</Notice>}
      {state.kind === 'error' && <LoadError message={t('commerce.error.connections', { message: state.message })} retry={() => void load()} />}
      {state.kind === 'ready' && state.value.length === 0 && !adding && <div className="emptyState"><Link2 aria-hidden="true" /><h3>{t('commerce.connections.empty')}</h3><p>{permissions.manage ? t('commerce.connections.emptyOwner') : t('commerce.connections.emptyMember')}</p></div>}
      {state.kind === 'ready' && state.value.length > 0 && <ul className="cmConnectors">{state.value.map((c) => <li key={c.id} className="cmConnector">
        <div className="cmConnectorHead">
          <div><strong><ChannelDot channel={c.channel} />{c.name}</strong><small>{channelLabel(c.channel)}{c.marketplace_names.length && c.channel !== 'own_store' ? ` · ${c.marketplace_names.join(', ')}` : ''}{c.channel === 'own_store' ? ` · ${t(`commerce.mode.${String(c.settings.mode ?? 'anum360')}` as MessageKey)}` : ''}</small></div>
          <Badge tone={healthTone(c.health)}>{healthLabel(c.health)}</Badge>
        </div>
        <dl className="cmFacts">
          <div><dt>{t('commerce.connections.lastSync')}</dt><dd>{when(c.last_sync_at)}</dd></div>
          <div><dt>{t('commerce.connections.nextSync')}</dt><dd>{c.enabled ? when(c.next_sync_at) : t('commerce.connections.off')}</dd></div>
          <div><dt>{t('commerce.connections.credentials')}</dt><dd>{c.credentials_configured ? c.credential_hint ?? t('commerce.connections.saved') : t('commerce.connections.missing')}</dd></div>
          <div><dt>{t('commerce.connections.synced')}</dt><dd>{t('commerce.connections.counts', { orders: c.sync_counts.orders ?? 0, listings: c.sync_counts.listings ?? 0 })}</dd></div>
        </dl>
        {c.last_error && <p className="cmProblem" role="note">{c.last_error}</p>}
        {c.webhook_path && <p className="muted">{t('commerce.webhook.urlLabel')} <code>{api_base()}{c.webhook_path}</code></p>}
        <div className="actions cmConnectorActions">
          {permissions.operate && c.enabled && !c.webhook_path && <button type="button" disabled={busy !== null} onClick={() => void sync(c)}><RefreshCw aria-hidden="true" />{busy === `sync:${c.id}` ? t('commerce.connections.syncing') : t('commerce.connections.sync')}</button>}
          {permissions.manage && !c.webhook_path && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void test(c)}>{busy === `test:${c.id}` ? t('commerce.connections.testing') : t('commerce.connections.test')}</button>}
          {permissions.manage && <button type="button" className="secondary" disabled={busy !== null} onClick={() => { setEditing(c); setAdding(false); }}>{t('commerce.connections.edit')}</button>}
          {permissions.manage && c.webhook_path && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void rotate(c)}>{t('commerce.connections.rotate')}</button>}
          {permissions.manage && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void toggle(c)}>{c.enabled ? t('commerce.connections.turnOff') : t('commerce.connections.turnOn')}</button>}
          {permissions.manage && <button type="button" className="danger" disabled={busy !== null} onClick={() => void remove(c)}><Trash2 aria-hidden="true" />{t('commerce.connections.delete')}</button>}
        </div>
        {editing?.id === c.id && <ConnectorEditor connector={c} onDone={(saved) => { setEditing(null); if (saved) { setNotice({ text: t('commerce.connections.saved', { name: saved.connector.name }) }); void load(); } }} />}
      </li>)}</ul>}
    </section>
    <AlertRules canEdit={permissions.manage} />
  </>;
}

const api_base = () => (import.meta.env.VITE_ANUM_API_URL as string | undefined) ?? 'http://localhost:8000';

function ConnectorEditor({ connector, onDone }: { connector?: CommerceConnector; onDone: (result: { connector: CommerceConnector; secret?: string | null } | null) => void }) {
  const [form, setForm] = useState<ConnectorForm>(() => {
    if (!connector) return emptyConnectorForm('amazon');
    const base = emptyConnectorForm(connector.channel);
    const s = connector.settings;
    return { ...base, name: connector.name, marketplaces: connector.marketplaces, sellerId: String(s.seller_id ?? ''), fetchBuyerDetails: Boolean(s.fetch_buyer_details), partnerId: String(s.partner_id ?? ''), mode: (s.mode as OwnStoreMode) ?? 'anum360', baseUrl: String(s.base_url ?? ''), currency: String(s.currency ?? 'AED') };
  });
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const headingId = useId();
  const set = (patch: Partial<ConnectorForm>) => setForm((current) => ({ ...current, ...patch }));
  const markets = form.channel === 'amazon' ? AMAZON_MARKETPLACES : form.channel === 'noon' ? NOON_MARKETPLACES : [];
  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = connectorBody(form, Boolean(connector));
    if (!body.ok) { setError(body.error); return; }
    setBusy(true); setError(null);
    try {
      if (connector) onDone({ connector: await api.updateConnector(connector.id, body.value) });
      else { const created = await api.createConnector(body.value); onDone({ connector: created.connector, secret: created.webhook_secret }); }
    } catch (e) { setError(isPermissionDenied(e) ? t('commerce.connections.denied') : describeError(e)); } finally { setBusy(false); }
  }
  const secretField = (label: MessageKey, key: keyof ConnectorForm, type = 'password') => <label className="field"><span>{t(label)}</span><input type={type} autoComplete="off" value={String(form[key])} onChange={(e) => set({ [key]: e.target.value } as Partial<ConnectorForm>)} placeholder={connector ? t('commerce.form.keepSaved') : undefined} dir="ltr" /></label>;
  return <form className="cmForm" aria-labelledby={headingId} onSubmit={(e) => void submit(e)}>
    <h3 id={headingId}>{connector ? t('commerce.form.editTitle', { name: connector.name }) : t('commerce.form.addTitle')}</h3>
    {!connector && <fieldset className="cmChoice"><legend>{t('commerce.form.channel')}</legend>{CHANNELS.map((c) => <label key={c}><input type="radio" name="cm-channel" checked={form.channel === c} onChange={() => setForm({ ...emptyConnectorForm(c), name: form.name })} />{channelLabel(c)}</label>)}</fieldset>}
    <p className="muted cmHelp">{t(`commerce.form.help.${form.channel === 'own_store' ? `own_store.${form.mode}` : form.channel}` as MessageKey)}</p>
    <div className="cmFormGrid">
      <label className="field"><span>{t('commerce.form.name')}</span><input value={form.name} onChange={(e) => set({ name: e.target.value })} maxLength={120} placeholder={t('commerce.form.namePlaceholder')} /></label>
      {markets.length > 0 && <fieldset className="cmChoice"><legend>{t('commerce.form.marketplaces')}</legend>{markets.map((m) => <label key={m.id}><input type="checkbox" checked={form.marketplaces.includes(m.id)} onChange={(e) => set({ marketplaces: e.target.checked ? [...form.marketplaces, m.id] : form.marketplaces.filter((x) => x !== m.id) })} />{m.name}</label>)}</fieldset>}
      {form.channel === 'amazon' && <>
        <label className="field"><span>{t('commerce.form.sellerId')}</span><input value={form.sellerId} onChange={(e) => set({ sellerId: e.target.value })} dir="ltr" autoComplete="off" /></label>
        {secretField('commerce.form.lwaClientId', 'lwaClientId', 'text')}
        {secretField('commerce.form.lwaClientSecret', 'lwaClientSecret')}
        {secretField('commerce.form.refreshToken', 'refreshToken')}
        <label className="settingToggle cmToggle"><div><strong>{t('commerce.form.buyerDetails')}</strong><small className="muted">{t('commerce.form.buyerDetailsHelp')}</small></div><input type="checkbox" checked={form.fetchBuyerDetails} onChange={(e) => set({ fetchBuyerDetails: e.target.checked })} /></label>
      </>}
      {form.channel === 'noon' && <>
        {secretField('commerce.form.apiKey', 'apiKey')}
        <label className="field"><span>{t('commerce.form.partnerId')}</span><input value={form.partnerId} onChange={(e) => set({ partnerId: e.target.value })} dir="ltr" /></label>
      </>}
      {form.channel === 'own_store' && <>
        {!connector && <label className="field"><span>{t('commerce.form.mode')}</span><select value={form.mode} onChange={(e) => set({ mode: e.target.value as OwnStoreMode, currency: e.target.value === 'anum360' ? 'AED' : form.currency })}>{OWN_STORE_MODES.map((m) => <option key={m} value={m}>{t(`commerce.mode.${m}` as MessageKey)}</option>)}</select></label>}
        {form.mode !== 'webhook' && <label className="field"><span>{t('commerce.form.baseUrl')}</span><input type="url" value={form.baseUrl} onChange={(e) => set({ baseUrl: e.target.value })} placeholder="https://" dir="ltr" /></label>}
        <label className="field"><span>{t('commerce.form.currency')}</span><input value={form.currency} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} maxLength={3} dir="ltr" /></label>
        {form.mode === 'anum360' && <>{secretField('commerce.form.staffEmail', 'email', 'email')}{secretField('commerce.form.staffPassword', 'password')}</>}
        {(form.mode === 'rest' || form.mode === 'supabase') && secretField(form.mode === 'supabase' ? 'commerce.form.supabaseKey' : 'commerce.form.apiKey', 'apiKey')}
      </>}
    </div>
    <p className="muted">{t('commerce.form.encrypted')}</p>
    {error && <p className="cmFieldError" role="alert">{error}</p>}
    <div className="actions"><button type="submit" disabled={busy}>{busy ? t('commerce.saving') : connector ? t('commerce.save') : t('commerce.form.connect')}</button><button type="button" className="secondary" onClick={() => onDone(null)}>{t('common.cancel')}</button></div>
  </form>;
}

function AlertRules({ canEdit }: { canEdit: boolean }) {
  const [state, setState] = useState<Load<CommerceSettings>>({ kind: 'loading' });
  const [draft, setDraft] = useState<CommerceSettings | null>(null), [notice, setNotice] = useState<{ error?: boolean; text: string } | null>(null);
  const load = useCallback(async () => { try { const value = await api.getSettings(); setState({ kind: 'ready', value }); setDraft(value); } catch (e) { setState(failed(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  const fields: { key: keyof CommerceSettings; label: MessageKey; step?: string }[] = [
    { key: 'low_stock_threshold', label: 'commerce.rules.lowStock' }, { key: 'handling_hours', label: 'commerce.rules.handling' },
    { key: 'return_spike_count', label: 'commerce.rules.spikeCount' }, { key: 'return_spike_ratio', label: 'commerce.rules.spikeRatio', step: '0.01' },
    { key: 'price_alert_percent', label: 'commerce.rules.pricePercent', step: '0.1' }, { key: 'sync_interval_minutes', label: 'commerce.rules.interval' },
  ];
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    try { const saved = await api.saveSettings(draft); setDraft(saved); setNotice({ text: t('commerce.rules.saved') }); }
    catch (e) { setNotice({ error: true, text: describeError(e) }); }
  }
  return <section className="surface cmPanel" aria-labelledby="cm-rules">
    <h2 id="cm-rules">{t('commerce.rules.title')}</h2>
    <p className="muted">{canEdit ? t('commerce.rules.explain') : t('commerce.rules.ownersOnly')}</p>
    {state.kind === 'loading' && <Notice busy>{t('commerce.rules.loading')}</Notice>}
    {state.kind === 'error' && <LoadError message={t('commerce.error.rules', { message: state.message })} retry={() => void load()} />}
    {draft && <form onSubmit={(e) => void save(e)}>
      <div className="cmFormGrid">{fields.map((f) => <label key={f.key} className="field"><span>{t(f.label)}</span><input type="number" min={0} step={f.step ?? '1'} value={String(draft[f.key] ?? '')} disabled={!canEdit} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value === '' ? 0 : Number(e.target.value) })} /></label>)}</div>
      {notice && <Notice error={notice.error}>{notice.text}</Notice>}
      {canEdit && <div className="actions"><button type="submit">{t('commerce.rules.save')}</button></div>}
    </form>}
  </section>;
}
