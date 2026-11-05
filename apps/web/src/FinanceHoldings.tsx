// Finance, Holdings (docs/finance.md#holdings): the person's own positions, valued with the prices they recorded.
// Summary (figures, value over time, allocation, top positions, recent activity, data quality), Positions (search,
// sort, paging, edit, delete, record a price), Performance and Valuation settings. ANUM fetches no market prices
// and places no orders from here: a position without a recorded price has no value, and nothing is converted
// between currencies. Lazy chunk.
import { useCallback, useEffect, useId, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArrowDownUp, BadgeCheck, ChevronLeft, ChevronRight, CircleAlert, Info, Landmark, Pencil, PieChart, Plus, Search, Tag, Trash2, TrendingUp, Wallet,
} from 'lucide-react';
import type { FinanceAccount, FinanceHolding, FinancePrice } from '@anum/contracts';
import * as api from './lib/financeApi';
import { formatAmount, isolate, parseAmount, rangeLabel, type FinanceTab } from './lib/finance';
import {
  DEFAULT_PERIOD, PERIODS, POSITIONS_PAGE, STALE_DAYS, allocation, filterPositions, foldSlices, holdingCurrencies, percent, priceHistory, recentActivity,
  totals, valueHoldings, valueSeries, type Period, type SortKey, type ValuedHolding,
} from './lib/financeHoldings';
import { checkPrice, formatDecimal } from './lib/financeTrading';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { ConfirmDialog, Modal } from './Dialog';
import { Denied, Empty, FormActions, FormError, LoadError, Notice, failed, type Load } from './FinanceParts';
import { Donut, LineChart, Sparkline } from './FinanceCharts';
import { FinBanner, FinCard, FinanceTabs, FinancePageHead, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { delta } from './lib/financeNav';
import { viewLabel } from './lib/shell';
import './financeLab.css';

type HoldingsTab = 'summary' | 'positions' | 'performance' | 'valuation';
const TABS: readonly HoldingsTab[] = ['summary', 'positions', 'performance', 'valuation'];
const today = () => new Date().toISOString().slice(0, 10);
const shortDate = (day: string) => formatDateTime(`${day}T12:00:00Z`, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

interface Data { accounts: FinanceAccount[]; holdings: FinanceHolding[]; prices: FinancePrice[] }

async function loadAll(): Promise<Data> {
  const [accounts, holdings] = await Promise.all([api.listAccounts(), api.listHoldings()]);
  const symbols = [...new Set(holdings.map((h) => h.symbol.toUpperCase()))];
  const lists = await Promise.all(symbols.map((s) => api.listPrices(s, 100)));
  return { accounts, holdings, prices: lists.flat() };
}

export function HoldingsPage({ onOpen }: { onOpen: (tab: FinanceTab) => void }) {
  const [state, setState] = useState<Load<Data>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setState((current) => (current.kind === 'ready' ? current : { kind: 'loading' }));
    try { setState({ kind: 'ready', value: await loadAll() }); } catch (e) { setState(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const [tab, setTab] = useState<HoldingsTab>('summary');
  const [accountId, setAccountId] = useState(''), [currency, setCurrency] = useState(''), [period, setPeriod] = useState<Period>(DEFAULT_PERIOD);
  const [editing, setEditing] = useState<FinanceHolding | 'new' | null>(null), [removing, setRemoving] = useState<FinanceHolding | null>(null);
  const [pricing, setPricing] = useState<{ symbol: string; currency: string } | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null), [busy, setBusy] = useState(false);
  const ids = { account: useId(), currency: useId(), period: useId() };

  const data = state.kind === 'ready' ? state.value : null;
  const brokers = useMemo(() => (data ? data.accounts.filter((a) => a.kind === 'investment' && !a.archived) : []), [data]);
  const day = today();
  const history = useMemo(() => priceHistory(data?.prices ?? []), [data]);
  const shown = useMemo(() => (data ? data.holdings.filter((h) => !accountId || h.account_id === accountId) : []), [data, accountId]);
  const valued = useMemo(() => valueHoldings(shown, data?.accounts ?? [], history, day), [shown, data, history, day]);
  const currencies = holdingCurrencies(shown);
  const cur = currencies.includes(currency) ? currency : currencies[0] ?? brokers[0]?.currency ?? '';

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try { await api.deleteHolding(removing.id); setNotice({ text: t('finance.item.deleted', { name: removing.name || removing.symbol }) }); setRemoving(null); await load(); }
    catch (e) { setNotice({ text: describeError(e), error: true }); setRemoving(null); } finally { setBusy(false); }
  }

  const crumbs = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('finance'), href: '#finance', onClick: () => onOpen('overview') }, { label: t('finance.tab.holdings') }];
  const controls = data && brokers.length > 0 ? <>
    <label className="finControl" htmlFor={ids.account}><span className="finControlLabel">{t('fholdings.portfolio')}</span>
      <select id={ids.account} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
        <option value="">{t('fholdings.allAccounts')}</option>{brokers.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
    {currencies.length > 1 && <label className="finControl" htmlFor={ids.currency}><span className="finControlLabel">{t('finance.form.currency')}</span>
      <select id={ids.currency} value={cur} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
    <label className="finControl" htmlFor={ids.period}><span className="finControlLabel">{t('fholdings.period')}</span>
      <select id={ids.period} value={period} onChange={(e) => setPeriod(Number(e.target.value) as Period)}>
        {PERIODS.map((p) => <option key={p} value={p}>{p ? t('fholdings.months', { count: p }) : t('fholdings.allTime')}</option>)}</select></label>
  </> : undefined;
  const showTabs = Boolean(data && brokers.length > 0 && data.holdings.length > 0);
  const head = <FinancePageHead title={t('finance.tab.holdings')} subtitle={t('fholdings.subtitle')} crumbs={crumbs} controls={controls}
    actions={controls ? <button type="button" className="cmPrimary" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.holdings.add')}</button> : undefined}
    tabs={showTabs ? <FinanceTabs label={t('fholdings.tabs')} idPrefix="fholdings" value={tab} onChange={setTab} items={TABS.map((id) => ({ id, label: t(`fholdings.tab.${id}` as MessageKey) }))} /> : undefined} />;

  if (state.kind === 'loading') return <section className="finPage" aria-labelledby="view-title">{head}<Notice busy>{t('fholdings.loading')}</Notice></section>;
  if (state.kind === 'denied') return <Denied />;
  if (state.kind === 'error') return <section className="finPage" aria-labelledby="view-title">{head}<LoadError message={t('fholdings.error', { message: state.message })} retry={() => void load()} /></section>;
  const d = state.value;
  const changed = (text: string) => { setNotice({ text }); void load(); };
  const dialogs = <>
    {editing && <Modal title={editing === 'new' ? t('finance.holdings.add') : t('finance.item.editTitle', { name: editing.name || editing.symbol })} className="formDialog" onClose={() => setEditing(null)}>
      <HoldingForm holding={editing === 'new' ? null : editing} accounts={brokers} defaultAccount={accountId} onDone={(text) => { setEditing(null); if (text) changed(text); }} />
    </Modal>}
    {pricing && <PriceDialog symbol={pricing.symbol} currency={pricing.currency} onClose={(text) => { setPricing(null); if (text) changed(text); }} />}
    {removing && <ConfirmDialog title={t('finance.item.deleteTitle', { name: removing.name || removing.symbol })} confirmLabel={t('finance.delete')} danger busy={busy} onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('finance.item.deleteText')}</p></ConfirmDialog>}
  </>;

  if (brokers.length === 0) return <section className="finPage" aria-labelledby="view-title">{head}
    <div className="surface cmPanel"><Empty icon={<Landmark aria-hidden="true" />} title={t('finance.holdings.needAccount')} text={t('finance.holdings.needAccountText')}
      action={<button type="button" onClick={() => onOpen('accounts')}><Plus aria-hidden="true" />{t('finance.accounts.add')}</button>} /></div></section>;

  const sum = totals(valued, cur, d.accounts, accountId || null);
  const fmt = (value: string | null) => (value === null ? '-' : formatAmount(value, cur));
  const tone = (value: string | null): 'up' | 'down' | '' => (value === null ? '' : Number(value) > 0 ? 'up' : Number(value) < 0 ? 'down' : '');
  const signed = (value: string | null) => (value === null ? '-' : `${Number(value) > 0 ? '+' : ''}${formatAmount(value, cur)}`);
  const rows = valued.filter((v) => v.holding.currency === cur);
  const actions = { onEdit: setEditing, onRemove: setRemoving, onPrice: (h: FinanceHolding) => setPricing({ symbol: h.symbol.toUpperCase(), currency: h.currency }) };
  const series = valueSeries(valued, cur, history, day, period);
  const chartPoints = series.map((p) => ({ label: shortDate(p.day), short: formatDateTime(`${p.day}T12:00:00Z`, { day: 'numeric', month: 'short', timeZone: 'UTC' }), value: p.value }));
  const axis = (v: number) => new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(v);

  const pricedCost = sum.value !== null && sum.gain !== null ? Number(sum.value) - Number(sum.gain) : null;
  const gainDelta = sum.value === null ? null : delta(sum.value, pricedCost);
  const changeDelta = sum.change === null || sum.value === null ? null : delta(sum.value, Number(sum.value) - Number(sum.change));
  const kpis = <KpiRow label={t('fholdings.figures')}>
    <KpiCard icon={<Wallet />} label={t('fholdings.kpi.value')} value={<span dir="ltr">{fmt(sum.value)}</span>}
      hint={sum.value === null ? t('fholdings.kpi.valueNone') : sum.unpriced ? t('fholdings.kpi.valueSome', { count: sum.unpriced }) : t('fholdings.kpi.valueAsOf', { date: shortDate(sum.oldestPrice ?? day) })} />
    <KpiCard icon={<TrendingUp />} label={t('fholdings.kpi.change')} value={<span dir="ltr" className={tone(sum.change)}>{signed(sum.change)}</span>} delta={changeDelta}
      hint={sum.change === null ? t('fholdings.kpi.changeNone') : t('fholdings.kpi.changeHint')} />
    <KpiCard icon={<BadgeCheck />} label={t('fholdings.kpi.gain')} value={<span dir="ltr" className={tone(sum.gain)}>{signed(sum.gain)}</span>} delta={gainDelta}
      hint={sum.gain === null ? t('fholdings.kpi.gainNone') : t('fholdings.kpi.gainHint', { cost: isolate(fmt(sum.cost)) })} />
    <KpiCard icon={<Landmark />} label={t('fholdings.kpi.cash')} value={<span dir="ltr">{fmt(sum.cash)}</span>} hint={sum.cash === null ? t('fholdings.kpi.cashNone', { currency: cur }) : t('fholdings.kpi.cashHint')} />
  </KpiRow>;

  const chart = (big: boolean) => <FinCard title={t('fholdings.chart.title')} icon={<TrendingUp />} subtitle={period ? t('fholdings.months', { count: period }) : t('fholdings.allTime')}>
    <p className="muted finCardNote">{t('fholdings.chart.note')}</p>
    {chartPoints.filter((p) => p.value !== null).length < 2
      ? <Empty icon={<TrendingUp aria-hidden="true" />} title={t('fholdings.chart.empty')} text={t('fholdings.chart.emptyText')}
        action={rows[0] ? <button type="button" className="secondary" onClick={() => actions.onPrice(rows[0].holding)}><Tag aria-hidden="true" />{t('fholdings.recordPrice')}</button> : undefined} />
      : <LineChart points={chartPoints} format={(v) => formatAmount(String(v), cur)} axis={axis} label={t('fholdings.chart.label', { currency: cur })} height={big ? 300 : 220}
        tableCaption={t('fholdings.chart.caption', { currency: cur })} testId="holdings-value-chart" />}
  </FinCard>;

  const slices = foldSlices(allocation(valued, cur, sum.cash, t('fholdings.kpi.cash')), 5, t('fholdings.allocation.other'));
  const allocationCard = <FinCard title={t('fholdings.allocation.title')} icon={<PieChart />}>
    {slices.length === 0 ? <Empty icon={<PieChart aria-hidden="true" />} title={t('fholdings.allocation.empty')} text={t('fholdings.allocation.emptyText')} />
      : <Donut slices={slices} total={formatAmount(String(slices.reduce((a, s) => a + s.value, 0)), cur)} totalLabel={t('fholdings.allocation.total')}
        format={(s) => formatAmount(String(s.value), cur)} label={t('fholdings.allocation.label', { currency: cur })} />}
  </FinCard>;

  const quality = <DataQuality sum={sum} rows={rows} onReview={() => setTab('valuation')} onPrice={() => rows[0] && actions.onPrice((rows.find((r) => r.stale || !r.price) ?? rows[0]).holding)} />;

  return <section className="finPage" aria-labelledby="view-title">
    {head}
    {notice && <Notice error={notice.error}>{notice.text}</Notice>}
    {d.holdings.length === 0 ? <div className="surface cmPanel"><Empty icon={<Wallet aria-hidden="true" />} title={t('finance.holdings.empty')} text={t('finance.holdings.emptyText')}
      action={<button type="button" onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('finance.holdings.add')}</button>} /></div> : <div {...tabPanel('fholdings', tab)}>
      {tab === 'summary' && <>{kpis}<div className="finCols">
        <div className="finMain">
          <FinBanner tone="info" icon={<Info />}>{t('fholdings.estimates')}</FinBanner>
          {allocationCard}
          <Positions rows={rows} cur={cur} actions={actions} compact onAll={() => setTab('positions')} />
          <ActivityCard holdings={shown} prices={d.prices} />
        </div>
        <aside className="finSide" aria-label={t('fholdings.side')}>
          {chart(false)}
          {quality}
          <FinBanner tone="info" icon={<Info />}>{t('fholdings.notAdvice')}</FinBanner>
        </aside>
      </div></>}
      {tab === 'positions' && <Positions rows={valued} cur={null} actions={actions} />}
      {tab === 'performance' && <div className="finStack">{kpis}{chart(true)}<Performance rows={rows} cur={cur} /></div>}
      {tab === 'valuation' && <Valuation rows={valued} prices={d.prices} onPrice={actions.onPrice} onOpen={onOpen} />}
    </div>}
    {dialogs}
  </section>;
}

// ---- Positions -------------------------------------------------------------------------------------------

interface RowActions { onEdit: (h: FinanceHolding) => void; onRemove: (h: FinanceHolding) => void; onPrice: (h: FinanceHolding) => void }

function Positions({ rows, cur, actions, compact, onAll }: { rows: ValuedHolding[]; cur: string | null; actions: RowActions; compact?: boolean; onAll?: () => void }) {
  const [search, setSearch] = useState(''), [sort, setSort] = useState<SortKey>('value'), [descending, setDescending] = useState(true), [offset, setOffset] = useState(0);
  const headingId = useId(), searchId = useId();
  const all = filterPositions(rows, search, sort, descending);
  const page = compact ? all.slice(0, 5) : all.slice(offset, offset + POSITIONS_PAGE);
  const by = (key: SortKey) => { if (sort === key) setDescending((v) => !v); else { setSort(key); setDescending(key !== 'asset'); } setOffset(0); };
  const sortHead = (key: SortKey, label: string, num = false) => <th scope="col" className={num ? 'num' : undefined} aria-sort={sort === key ? (descending ? 'descending' : 'ascending') : 'none'}>
    {compact ? label : <button type="button" className="finSortButton" onClick={() => by(key)}>{label}<ArrowDownUp aria-hidden="true" /></button>}</th>;
  return <FinCard title={t('fholdings.positions.title')} id={headingId}
    actions={compact && rows.length > 5 && onAll ? <button type="button" className="cmLinkButton" onClick={onAll}>{t('fholdings.positions.all', { count: rows.length })}</button> : undefined}>
    {!compact && <div className="cmFilters finSearch"><label className="cmInline" htmlFor={searchId}><Search aria-hidden="true" /><span className="visuallyHidden">{t('fholdings.positions.search')}</span>
      <input id={searchId} type="search" value={search} placeholder={t('fholdings.positions.search')} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} dir="auto" /></label></div>}
    {page.length === 0 ? <p className="muted">{search ? t('fholdings.positions.noMatch') : t('fholdings.positions.none')}</p>
      : <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finGrid finPositions">
        <caption className="visuallyHidden">{t('fholdings.positions.caption')}</caption>
        <thead><tr>{sortHead('asset', t('fholdings.col.asset'))}{!compact && <th scope="col">{t('finance.col.account')}</th>}{sortHead('quantity', t('finance.form.quantity'), true)}
          {!compact && <th scope="col" className="num">{t('fholdings.col.average')}</th>}<th scope="col" className="num">{t('fholdings.col.price')}</th>{sortHead('value', t('fholdings.col.value'), true)}
          {sortHead('gain', t('fholdings.col.gain'), true)}<th scope="col"><span className="visuallyHidden">{t('fholdings.col.trend')}</span></th><th scope="col"><span className="visuallyHidden">{t('fholdings.col.actions')}</span></th></tr></thead>
        <tbody>{page.map((v) => <PositionRow key={v.holding.id} v={v} actions={actions} showCurrency={cur === null} compact={compact} />)}</tbody>
      </table></div>}
    {!compact && all.length > POSITIONS_PAGE && <nav className="cmPager" aria-label={t('fholdings.positions.pages')}>
      <button type="button" className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - POSITIONS_PAGE))}><ChevronLeft aria-hidden="true" className="cmFlip" />{t('finance.page.previous')}</button>
      <span className="muted" aria-live="polite">{rangeLabel(offset, page.length, all.length)}</span>
      <button type="button" className="secondary" disabled={offset + page.length >= all.length} onClick={() => setOffset(offset + POSITIONS_PAGE)}>{t('finance.page.next')}<ChevronRight aria-hidden="true" className="cmFlip" /></button>
    </nav>}
  </FinCard>;
}

function PositionRow({ v, actions, showCurrency, compact }: { v: ValuedHolding; actions: RowActions; showCurrency: boolean; compact?: boolean }) {
  const h = v.holding, name = h.name || h.symbol, c = h.currency;
  const gainTone = v.gain === null ? '' : Number(v.gain) > 0 ? 'up' : Number(v.gain) < 0 ? 'down' : '';
  const trendTone = v.trend.length < 2 ? 'flat' : v.trend[v.trend.length - 1] > v.trend[0] ? 'up' : v.trend[v.trend.length - 1] < v.trend[0] ? 'down' : 'flat';
  return <tr>
    <th scope="row"><span className="finAsset"><strong dir="auto">{name}</strong><small><bdi dir="ltr">{h.symbol}</bdi>{showCurrency ? ` · ${c}` : ''}{compact && v.accountName ? <> · <bdi>{v.accountName}</bdi></> : null}</small></span></th>
    {!compact && <td data-label={t('finance.col.account')} dir="auto">{v.accountName}</td>}
    <td className="num" data-label={t('finance.form.quantity')} dir="ltr">{formatDecimal(h.quantity, 8)}</td>
    {!compact && <td className="num" data-label={t('fholdings.col.average')} dir="ltr">{v.averageCost === null ? '-' : formatAmount(Number(v.averageCost).toFixed(4), c)}</td>}
    <td className="num" data-label={t('fholdings.col.price')}>{v.price ? <><span dir="ltr">{formatDecimal(v.price.price, 4)}</span>
      <small className={v.stale ? 'finStale' : 'muted'}>{v.stale ? t('fholdings.staleOn', { date: shortDate(v.price.as_of) }) : shortDate(v.price.as_of)}</small></> : <span className="muted">{t('fholdings.noPrice')}</span>}</td>
    <td className="num" data-label={t('fholdings.col.value')} dir="ltr">{v.value === null ? '-' : formatAmount(v.value, c)}</td>
    <td className={`num finGain ${gainTone}`} data-label={t('fholdings.col.gain')}>{v.gain === null ? '-' : <><span dir="ltr">{Number(v.gain) > 0 ? '+' : ''}{formatAmount(v.gain, c)}</span><small dir="ltr">{percent(v.gainPercent)}</small></>}</td>
    <td data-label={t('fholdings.col.trend')}><Sparkline values={v.trend} tone={trendTone} label={t('fholdings.trendLabel', { name, count: v.trend.length })} /></td>
    <td className="finRowActions">
      <button type="button" className="iconButton small" onClick={() => actions.onPrice(h)} aria-label={t('fholdings.recordPriceFor', { name })}><Tag aria-hidden="true" /></button>
      {!compact && <button type="button" className="iconButton small" onClick={() => actions.onEdit(h)} aria-label={t('finance.item.edit', { name })}><Pencil aria-hidden="true" /></button>}
      {!compact && <button type="button" className="iconButton small" onClick={() => actions.onRemove(h)} aria-label={t('finance.item.delete', { name })}><Trash2 aria-hidden="true" /></button>}
    </td>
  </tr>;
}

// ---- Side cards ------------------------------------------------------------------------------------------

function DataQuality({ sum, rows, onReview, onPrice }: { sum: ReturnType<typeof totals>; rows: ValuedHolding[]; onReview: () => void; onPrice: () => void }) {
  const problems = sum.unpriced + sum.stale;
  return <FinCard title={t('fholdings.quality.title')}>
    <p className="finQualityLine">{problems === 0 ? <BadgeCheck aria-hidden="true" className="finOk" /> : <CircleAlert aria-hidden="true" className="finWarnIcon" />}
      <span>{t('fholdings.quality.manual', { count: rows.length })} · {sum.oldestPrice ? t('fholdings.quality.oldest', { date: shortDate(sum.oldestPrice) }) : t('fholdings.quality.noPrices')}</span></p>
    {sum.stale > 0 && <FinBanner tone="warn" icon={<CircleAlert />}>{t('fholdings.quality.stale', { count: sum.stale, days: STALE_DAYS })}</FinBanner>}
    {sum.unpriced > 0 && <FinBanner tone="warn" icon={<CircleAlert />}>{t('fholdings.quality.unpriced', { count: sum.unpriced })}</FinBanner>}
    <p className="muted finCardNote">{t('fholdings.quality.note')}</p>
    <div className="finCardButtons">
      <button type="button" onClick={onReview}>{t('fholdings.quality.review')}</button>
      <button type="button" className="secondary" onClick={onPrice} disabled={rows.length === 0}><Plus aria-hidden="true" />{t('fholdings.recordPrice')}</button>
    </div>
  </FinCard>;
}

function ActivityCard({ holdings, prices }: { holdings: FinanceHolding[]; prices: FinancePrice[] }) {
  const items = recentActivity(holdings, prices);
  return <FinCard title={t('fholdings.activity.title')}>
    {items.length === 0 ? <p className="muted">{t('fholdings.activity.none')}</p> : <div className="cmTableWrap" tabIndex={0}><table className="cmTable finTable finGrid finActivity">
      <caption className="visuallyHidden">{t('fholdings.activity.caption')}</caption>
      <thead><tr><th scope="col">{t('fholdings.chart.date')}</th><th scope="col">{t('fholdings.activity.what')}</th><th scope="col">{t('fholdings.col.asset')}</th>
        <th scope="col" className="num">{t('fholdings.col.price')}</th><th scope="col">{t('fholdings.activity.source')}</th></tr></thead>
      <tbody>{items.map((a) => <tr key={a.id}><td>{formatDateTime(a.at, { dateStyle: 'medium' })}</td><td>{t(`fholdings.activity.${a.kind}` as MessageKey)}</td>
        <th scope="row" dir="auto">{a.label}</th><td className="num" dir="ltr">{a.detail ? formatAmount(Number(a.detail).toFixed(4), a.currency) : '-'}</td>
        <td><StatusChip tone="muted">{t('fholdings.activity.manual')}</StatusChip></td></tr>)}</tbody>
    </table></div>}
  </FinCard>;
}

function Performance({ rows, cur }: { rows: ValuedHolding[]; cur: string }) {
  const id = useId();
  const sorted = [...rows].sort((a, b) => (b.gainPercent ?? -Infinity) - (a.gainPercent ?? -Infinity));
  return <FinCard title={t('fholdings.performance.title')}>
    <p className="muted finCardNote">{t('fholdings.performance.note', { currency: cur })}</p>
    <ul className="finBars">{sorted.map((v) => {
      const pct = v.gainPercent, width = pct === null ? 0 : Math.min(100, Math.abs(pct));
      return <li key={v.holding.id}><span className="finBarName" dir="auto">{v.holding.name || v.holding.symbol}</span>
        <span className="finBarTrack" aria-hidden="true"><span className={`finBarFill ${pct !== null && pct < 0 ? 'down' : 'up'}`} style={{ inlineSize: `${width}%` }} /></span>
        <span className="finBarValue" dir="ltr">{pct === null ? t('fholdings.noPrice') : percent(pct)}</span></li>;
    })}</ul>
  </FinCard>;
}

function Valuation({ rows, prices, onPrice, onOpen }: { rows: ValuedHolding[]; prices: FinancePrice[]; onPrice: (h: FinanceHolding) => void; onOpen: (tab: FinanceTab) => void }) {
  const id = useId();
  const [removing, setRemoving] = useState<FinancePrice | null>(null), [notice, setNotice] = useState<string | null>(null), [gone, setGone] = useState<string[]>([]);
  const keys = new Set(rows.map((r) => `${r.holding.symbol.toUpperCase()}|${r.holding.currency}`));
  const list = prices.filter((p) => keys.has(`${p.symbol}|${p.currency}`) && !gone.includes(p.id)).sort((a, b) => b.as_of.localeCompare(a.as_of) || b.recorded_at.localeCompare(a.recorded_at));
  async function remove() {
    if (!removing) return;
    try { await api.deletePrice(removing.id); setGone((g) => [...g, removing.id]); setNotice(t('finance.prices.deleted')); } catch (e) { setNotice(describeError(e)); } finally { setRemoving(null); }
  }
  return <div className="finStack">
    <FinCard title={t('fholdings.valuation.title')}>
      <ul className="finRules">
        <li>{t('fholdings.valuation.rule1')}</li><li>{t('fholdings.valuation.rule2', { days: STALE_DAYS })}</li><li>{t('fholdings.valuation.rule3')}</li>
      </ul>
      <p className="muted">{t('fholdings.valuation.fx')} <button type="button" className="cmLinkButton" onClick={() => onOpen('connections')}>{t('finance.prices.fxLink')}</button></p>
      <ul className="cmList finItems">{rows.map((r) => <li key={r.holding.id}><div><strong dir="auto">{r.holding.name || r.holding.symbol} <bdi dir="ltr">{r.holding.symbol}</bdi></strong>
        <small>{r.price ? t('fholdings.valuation.latest', { price: isolate(formatAmount(Number(r.price.price).toFixed(4), r.holding.currency)), date: shortDate(r.price.as_of) }) : t('fholdings.noPrice')}
          {r.stale ? ` · ${t('fholdings.stale')}` : ''}</small></div>
        <div className="finItemEnd"><button type="button" className="secondary" onClick={() => onPrice(r.holding)}><Tag aria-hidden="true" />{t('fholdings.recordPrice')}</button></div></li>)}</ul>
    </FinCard>
    <FinCard title={t('fholdings.valuation.history')}>
      {notice && <Notice>{notice}</Notice>}
      {list.length === 0 ? <p className="muted">{t('finance.prices.none')}</p> : <ul className="cmList finItems">{list.slice(0, 50).map((p) => <li key={p.id}><div>
        <strong><bdi dir="ltr">{p.symbol}</bdi> <span dir="ltr">{formatDecimal(p.price)}</span> {p.currency}</strong>
        <small>{t('finance.prices.line', { date: isolate(p.as_of), source: p.source === 'manual' ? t('finance.prices.manual') : p.source })}</small></div>
        <div className="finItemEnd"><button type="button" className="iconButton" onClick={() => setRemoving(p)} aria-label={t('finance.prices.deleteNamed', { symbol: p.symbol, date: p.as_of })}><Trash2 aria-hidden="true" /></button></div></li>)}</ul>}
    </FinCard>
    {removing && <ConfirmDialog title={t('finance.prices.deleteTitle')} confirmLabel={t('finance.delete')} danger onConfirm={() => void remove()} onCancel={() => setRemoving(null)}>
      <p>{t('finance.prices.deleteText', { symbol: removing.symbol, date: removing.as_of })}</p></ConfirmDialog>}
  </div>;
}

// ---- Dialogs ---------------------------------------------------------------------------------------------

function PriceDialog({ symbol, currency, onClose }: { symbol: string; currency: string; onClose: (message: string | null) => void }) {
  const [price, setPrice] = useState(''), [asOf, setAsOf] = useState(today);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const errorId = useId();
  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = checkPrice(price);
    if (!value.ok) { setError(value.error); return; }
    if (!asOf || asOf > today()) { setError(t('fholdings.error.date')); return; }
    setBusy(true); setError(null);
    try { await api.addPrice({ symbol, currency, price: value.value, as_of: asOf }); onClose(t('fholdings.priceSaved', { symbol })); }
    catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  return <Modal title={t('fholdings.recordPriceFor', { name: symbol })} className="formDialog" onClose={() => onClose(null)}>
    <form className="finForm" onSubmit={(e) => void submit(e)} aria-describedby={error ? errorId : undefined}>
      <p className="muted">{t('fholdings.priceHelp', { currency })}</p>
      <div className="cmFormGrid">
        <label className="field"><span>{t('fholdings.col.price')} ({currency})</span><input value={price} onChange={(e) => setPrice(e.target.value)} inputMode="decimal" dir="ltr" required data-autofocus /></label>
        <label className="field"><span>{t('finance.prices.date')}</span><input type="date" value={asOf} max={today()} onChange={(e) => setAsOf(e.target.value)} dir="ltr" required /></label>
      </div>
      <FormError id={errorId} text={error} />
      <FormActions busy={busy} onCancel={() => onClose(null)} />
    </form>
  </Modal>;
}

export function HoldingForm({ holding, accounts, defaultAccount, onDone }: { holding: FinanceHolding | null; accounts: FinanceAccount[]; defaultAccount?: string; onDone: (message: string | null) => void }) {
  const [accountId, setAccountId] = useState(holding?.account_id ?? (defaultAccount || accounts[0]?.id) ?? ''), [symbol, setSymbol] = useState(holding?.symbol ?? ''), [name, setName] = useState(holding?.name ?? '');
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

