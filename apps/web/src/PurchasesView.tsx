// Purchases and Returns (docs/purchases.md#web), on the shared shell (FinanceShell.tsx): the page head with Import order,
// Export and Track purchase; tabs Overview (KPIs, the filterable tracker, the delivery and refund task queue), All purchases,
// Deliveries, Returns and refunds, Warranty and Settings; a row opens the order drawer and the full record. Payments and
// refunds are links to the person's Finance transactions, never new money, and a refund is never income.
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, CircleAlert, Coins, Download, Lock, PackageOpen, Plus, ShoppingCart, Smartphone, Truck, Undo2, Upload } from 'lucide-react';
import type { PurchaseOverview, PurchasePage, PurchasePreferences, PurchaseReturn } from '@anum/contracts';
import * as api from './lib/purchasesApi';
import {
  FILTERS, NO_FILTERS, PAGE_SIZE, PURCHASE_TABS, chipTone, hashForTab, moneyList, openFromHash, reasonLabel, refundMatch, returnStatusLabel, statusLabel, statusTone, tabFromHash,
  type PurchaseTab, type TrackerFilters,
} from './lib/purchases';
import { formatAmount, monthLabel } from './lib/finance';
import { describeError } from './lib/errors';
import { formatNumber, t, type MessageKey } from './lib/i18n';
import { viewLabel } from './lib/shell';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip as ShellChip, tabPanel } from './FinanceShell';
import { type Crumb } from './PageParts';
import { PurchaseDetailView, PurchaseForm } from './PurchaseParts';
import { ImportOrderDialog, PurchaseDrawer, RefundNote, TaskQueue, TrackerCard, TrackerFilterBar, shortDay } from './PurchaseTracker';
import './commerce.css';
import './purchases.css';

function usePurchaseTab(): [PurchaseTab, (next: PurchaseTab) => void] {
  const [tab, setTab] = useState<PurchaseTab>(() => tabFromHash(typeof location === 'undefined' ? '' : location.hash));
  useEffect(() => {
    const sync = () => { if (location.hash.startsWith('#purchases')) setTab(tabFromHash(location.hash)); };
    window.addEventListener('popstate', sync);
    window.addEventListener('hashchange', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('hashchange', sync); };
  }, []);
  const pick = (next: PurchaseTab) => {
    setTab(next);
    if (location.hash !== hashForTab(next)) history.pushState(null, '', hashForTab(next));
  };
  return [tab, pick];
}

export function PurchasesDenied() {
  return <section className="surface cmPanel cmDenied" aria-labelledby="pur-denied"><Lock aria-hidden="true" /><div><h2 id="pur-denied">{t('pur.denied.title')}</h2><p className="muted">{t('pur.denied.text')}</p></div></section>;
}

export function StatusChip({ status, kind = 'purchase' }: { status: string; kind?: 'purchase' | 'return' }) {
  return <span className={`purChip ${statusTone(status as never)}`}>{kind === 'return' ? returnStatusLabel(status) : statusLabel(status)}</span>;
}

export function PurchasesView() {
  const [tab, pick] = usePurchaseTab();
  const [openId, setOpenId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(() => openFromHash(typeof location === 'undefined' ? '' : location.hash));
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [tick, setTick] = useState(0);
  const [denied, setDenied] = useState(false);
  const [filters, setFilters] = useState<TrackerFilters>(NO_FILTERS);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  // The All purchases list's "Show archived": Export follows what that list shows.
  const [archived, setArchived] = useState(false);
  const refresh = () => setTick((n) => n + 1);
  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('purchases'), href: '#purchases', onClick: () => { setOpenId(null); pick('overview'); } }];
  const added = (purchase: { id: string }) => { setAdding(false); refresh(); setSelected(purchase.id); };
  const onDenied = useCallback(() => setDenied(true), []);
  const exportCsv = async () => {
    setMessage(null);
    try { await api.exportCsv(tab === 'list' && archived); setMessage({ text: t('pur.exported') }); } catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  const openFull = (id: string) => { setSelected(null); setOpenId(id); };
  const tabs = PURCHASE_TABS.map((id) => ({ id, label: t(`pur.tab.${id}` as MessageKey) }));
  const actions = denied || openId ? undefined : <>
    <button type="button" className="secondary" onClick={() => setImporting(true)}><Upload aria-hidden="true" />{t('pur.import.button')}</button>
    <button type="button" className="secondary" onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('pur.export')}</button>
    <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('pur.track')}</button>
  </>;
  const head = <FinancePageHead title={t('pur.title')} subtitle={t('pur.subtitle')} crumbs={openId ? crumbs : undefined} actions={actions}
    tabs={denied || openId ? undefined : <FinanceTabs label={t('pur.tabs')} items={tabs} value={tab} idPrefix="pur" onChange={(next) => { setSelected(null); pick(next); }} />} />;
  if (denied) return <div className="finPage purLayout">{head}<PurchasesDenied /></div>;
  const drawer = selected && <PurchaseDrawer key={selected} purchaseId={selected} onClose={() => setSelected(null)} onChanged={refresh} onOpenFull={openFull} />;
  const bar = <TrackerFilterBar value={filters} onChange={setFilters} label={t('pur.filter.label')}
    statusChoices={tab === 'deliveries' ? ['', 'ordered', 'confirmed', 'shipped', 'out_for_delivery', 'partially_delivered'] : FILTERS} />;
  const card = (props: Partial<Parameters<typeof TrackerCard>[0]> & { title: string }) => <TrackerCard filters={filters} selected={selected} onSelect={setSelected}
    onOpenFull={openFull} onAdd={() => setAdding(true)} tick={tick} onDenied={onDenied} {...props} />;
  return <div className="finPage purLayout">
    {head}
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {adding && <PurchaseForm onClose={() => setAdding(false)} onSaved={added} />}
    {importing && <ImportOrderDialog onClose={() => setImporting(false)} onImported={(id, text) => { setImporting(false); setMessage({ text }); refresh(); if (id) setSelected(id); }} />}
    {openId ? <PurchaseDetailView key={`${openId}:${tick}`} purchaseId={openId} onBack={() => { setOpenId(null); refresh(); }} onDeleted={() => { setOpenId(null); refresh(); }} />
      : <div {...tabPanel('pur', tab)}>
        {tab === 'overview' && <OverviewPanel key={tick} onAdd={() => setAdding(true)} onImport={() => setImporting(true)} onDenied={onDenied} onSelect={setSelected}>
          {bar}
          <div className={`purWorkspace ${selected ? 'withDrawer' : ''}`}>
            <div className="purMain">{card({ title: t('pur.tracker.title'), limit: 5, actions: <button type="button" className="linkButton" onClick={() => pick('list')}>{t('pur.allPurchases')}</button> })}</div>
            {drawer}
          </div>
        </OverviewPanel>}
        {tab === 'list' && <>{bar}<ListWorkspace drawer={drawer} selected={Boolean(selected)} card={card} archived={archived} onArchived={setArchived} /></>}
        {tab === 'deliveries' && <>{bar}<div className={`purWorkspace ${selected ? 'withDrawer' : ''}`}><div className="purMain">
          {card({ title: t('pur.deliveries.title'), columns: 'deliveries', extra: { status: filters.status || 'awaiting' }, emptyTitle: t('pur.deliveries.empty'), emptyText: t('pur.deliveries.emptyText') })}
          <FinBanner tone="info" icon={<Truck />}>{t('pur.deliveries.rule')}</FinBanner></div>{drawer}</div></>}
        {tab === 'returns' && <div className={`purWorkspace ${selected ? 'withDrawer' : ''}`}><div className="purMain"><ReturnsPanel key={tick} onOpen={setSelected} onDenied={onDenied} /><RefundNote /></div>{drawer}</div>}
        {tab === 'warranty' && <>{bar}<div className={`purWorkspace ${selected ? 'withDrawer' : ''}`}><div className="purMain">
          {card({ title: t('pur.warranty.title'), columns: 'warranty', extra: { warranty: true }, emptyTitle: t('pur.warranty.empty'), emptyText: t('pur.warranty.emptyText') })}</div>{drawer}</div></>}
        {tab === 'settings' && <SettingsPanel onDenied={onDenied} />}
      </div>}
  </div>;
}

function ListWorkspace({ drawer, selected, card, archived, onArchived: setArchived }: {
  drawer: ReactNode; selected: boolean; card: (props: Partial<Parameters<typeof TrackerCard>[0]> & { title: string }) => ReactNode; archived: boolean; onArchived: (value: boolean) => void;
}) {
  return <div className={`purWorkspace ${selected ? 'withDrawer' : ''}`}><div className="purMain">
    {card({ title: archived ? t('pur.list.archivedTitle') : t('pur.list.title'), extra: { archived },
      actions: <label className="purCheck"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />{t('pur.list.showArchived')}</label> })}
  </div>{drawer}</div>;
}

// ---- Overview ------------------------------------------------------------------------------------------

function OverviewPanel({ onAdd, onImport, onDenied, onSelect, children }: { onAdd: () => void; onImport: () => void; onDenied: () => void; onSelect: (id: string) => void; children: ReactNode }) {
  const [data, setData] = useState<Load<PurchaseOverview>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setData({ kind: 'loading' });
    try { setData({ kind: 'ready', value: await api.overview() }); } catch (e) { setData(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (data.kind === 'denied') onDenied(); }, [data.kind, onDenied]);
  if (data.kind === 'error') return <LoadError message={t('pur.error.load', { message: data.message })} retry={() => void load()} />;
  if (data.kind === 'denied') return null;
  const value = data.kind === 'ready' ? data.value : null;
  if (value && value.purchases === 0) return <FinCard title={t('pur.tracker.title')}><Empty icon={<PackageOpen aria-hidden="true" />} title={t('pur.empty.title')} text={t('pur.empty.text')}
    action={<div className="actions"><button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('pur.track')}</button>
      <button type="button" className="secondary" onClick={onImport}><Upload aria-hidden="true" />{t('pur.import.button')}</button></div>} /></FinCard>;
  const loading = <span className="muted">{t('pur.loading')}</span>;
  return <>
    <KpiRow label={t('pur.summary.label')}>
      <KpiCard icon={<ShoppingCart />} label={t('pur.kpi.orders')} value={value ? formatNumber(value.orders_this_month) : loading}
        hint={value ? t('pur.kpi.ordersHint', { month: monthLabel(value.month) }) : undefined} />
      <KpiCard icon={<Coins />} label={t('pur.kpi.committed')} value={value ? (value.committed_this_month.length ? moneyList(value.committed_this_month) : '-') : loading}
        hint={value ? (value.untotalled_this_month ? t('pur.kpi.untotalled', { count: value.untotalled_this_month }) : t('pur.kpi.committedHint')) : undefined} />
      <KpiCard icon={<Truck />} label={t('pur.kpi.transit')} value={value ? formatNumber(value.in_transit) : loading}
        hint={value ? (value.late ? t('pur.fig.late', { count: value.late }) : t('pur.kpi.transitHint', { count: value.awaiting_delivery })) : undefined} tone={value?.late ? 'warn' : ''} />
      <KpiCard icon={<Undo2 />} label={t('pur.kpi.returns')} value={value ? formatNumber(value.open_returns) : loading}
        hint={value ? (value.refunds_owed.length ? t('pur.fig.owed', { amount: moneyList(value.refunds_owed) }) : t('pur.fig.returnsHint')) : undefined} tone={value?.open_returns ? 'bad' : ''} />
    </KpiRow>
    {value && value.needs_review > 0 && <FinBanner tone="warn" icon={<CircleAlert />}>{t('pur.review.count', { count: value.needs_review })}</FinBanner>}
    {children}
    {value && <TaskQueue due={value.due} onOpen={onSelect} />}
    <RefundNote />
  </>;
}

// ---- The list ----------------------------------------------------------------------------------------

export function Pager({ total, offset, onChange }: { total: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= PAGE_SIZE) return null;
  return <div className="purPager">
    <span className="muted">{t('pur.page.showing', { from: offset + 1, to: Math.min(offset + PAGE_SIZE, total), total })}</span>
    <button type="button" className="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft aria-hidden="true" className="purFlip" />{t('pur.page.previous')}</button>
    <button type="button" className="secondary" disabled={offset + PAGE_SIZE >= total} onClick={() => onChange(offset + PAGE_SIZE)}>{t('pur.page.next')}<ChevronRight aria-hidden="true" className="purFlip" /></button>
  </div>;
}

// ---- Returns across purchases ------------------------------------------------------------------------

function ReturnsPanel({ onOpen, onDenied }: { onOpen: (id: string) => void; onDenied: () => void }) {
  const [open, setOpen] = useState(true);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Load<PurchasePage<PurchaseReturn>>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setPage({ kind: 'loading' });
    try { setPage({ kind: 'ready', value: await api.listReturns(open, offset, PAGE_SIZE) }); } catch (e) { setPage(failed(e)); }
  }, [open, offset]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (page.kind === 'denied') onDenied(); }, [page.kind, onDenied]);
  return <FinCard title={t('pur.returns.title')} subtitle={page.kind === 'ready' ? t('pur.returns.count', { count: page.value.total }) : undefined}
    actions={<label className="purCheck"><input type="checkbox" checked={!open} onChange={(e) => { setOffset(0); setOpen(!e.target.checked); }} />{t('pur.returns.showClosed')}</label>}>
    <p className="muted purNote">{t('pur.returns.rule')}</p>
    {page.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('pur.error.load', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && <Empty icon={<Undo2 aria-hidden="true" />} title={t('pur.returns.empty')} text={t('pur.returns.emptyText')} />}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <div className="finTableWrap" tabIndex={0}><table className="finGrid purTracker">
        <caption className="visuallyHidden">{t('pur.returns.title')}</caption>
        <thead><tr><th scope="col">{t('pur.col.seller')}</th><th scope="col">{t('pur.col.requested')}</th><th scope="col">{t('pur.col.reason')}</th>
          <th scope="col">{t('pur.col.status')}</th><th scope="col">{t('pur.col.refundMatch')}</th><th scope="col" className="num">{t('pur.col.expectedRefund')}</th>
          <th scope="col" className="num">{t('pur.col.refunded')}</th></tr></thead>
        <tbody>{page.value.items.map((r) => <tr key={r.id}>
          <th scope="row"><button type="button" className="purRowButton" onClick={() => onOpen(r.purchase_id)}>
            <strong dir="auto"><bdi>{r.merchant ?? '-'}</bdi></strong>{r.order_ref ? <small dir="ltr">{r.order_ref}</small> : null}</button></th>
          <td>{shortDay(r.requested_on)}</td>
          <td>{reasonLabel(r.reason)}</td>
          <td><ShellChip tone={chipTone(r.status)}>{returnStatusLabel(r.status)}</ShellChip></td>
          <td><ShellChip tone={refundMatch(r) === 'matched' ? 'ok' : refundMatch(r) === 'review' ? 'warn' : 'muted'}>{t(`pur.match.${refundMatch(r)}` as MessageKey)}</ShellChip></td>
          <td className="num" dir="ltr">{r.amount ? formatAmount(r.amount, r.currency) : '-'}</td>
          <td className="num" dir="ltr">{r.refunded_all.length ? r.refunded_all.map((m) => formatAmount(m.amount, m.currency)).join(', ') : '-'}</td>
        </tr>)}</tbody>
      </table></div>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
  </FinCard>;
}

// ---- Settings ------------------------------------------------------------------------------------------

function SettingsPanel({ onDenied }: { onDenied: () => void }) {
  const [prefs, setPrefs] = useState<Load<PurchasePreferences>>({ kind: 'loading' });
  const [draft, setDraft] = useState<PurchasePreferences | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setPrefs({ kind: 'loading' });
    try { const p = await api.getPreferences(); setPrefs({ kind: 'ready', value: p }); setDraft(p); } catch (e) { setPrefs(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (prefs.kind === 'denied') onDenied(); }, [prefs.kind, onDenied]);
  if (prefs.kind === 'loading') return <Notice busy>{t('pur.loading')}</Notice>;
  if (prefs.kind === 'error') return <LoadError message={t('pur.error.load', { message: prefs.message })} retry={() => void load()} />;
  if (prefs.kind !== 'ready' || !draft) return null;
  const save = async () => {
    if (!/^[A-Za-z]{3}$/.test(draft.default_currency)) { setMessage({ text: t('pur.form.badCurrency'), error: true }); return; }
    setBusy(true); setMessage(null);
    try {
      const saved = await api.savePreferences({ version: draft.version, reminders: draft.reminders, lead_days: draft.lead_days, warranty_days: draft.warranty_days,
        default_currency: draft.default_currency.toUpperCase() });
      setDraft(saved); setMessage({ text: t('pur.settings.saved') });
    } catch (e) { setMessage({ text: describeError(e), error: true }); } finally { setBusy(false); }
  };
  const clamp = (value: string, min: number, max: number, fallback: number) => Math.max(min, Math.min(max, Number(value) || fallback));
  return <div className="purGrid">
    <form className="surface cmPanel purForm" aria-labelledby="pur-set-title" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <h2 id="pur-set-title">{t('pur.settings.title')}</h2>
      <label className="purCheck"><input type="checkbox" checked={draft.reminders} onChange={(e) => setDraft({ ...draft, reminders: e.target.checked })} />{t('pur.settings.reminders')}</label>
      <p className="muted purNote">{t('pur.settings.remindersHint')}</p>
      <div className="purTwo">
        <label className="field"><span>{t('pur.settings.lead')}</span>
          <input type="number" inputMode="numeric" min={1} max={60} value={draft.lead_days} onChange={(e) => setDraft({ ...draft, lead_days: clamp(e.target.value, 1, 60, 3) })} /></label>
        <label className="field"><span>{t('pur.settings.warranty')}</span>
          <input type="number" inputMode="numeric" min={1} max={120} value={draft.warranty_days} onChange={(e) => setDraft({ ...draft, warranty_days: clamp(e.target.value, 1, 120, 30) })} /></label>
        <label className="field"><span>{t('pur.settings.currency')}</span>
          <input dir="ltr" maxLength={3} value={draft.default_currency} onChange={(e) => setDraft({ ...draft, default_currency: e.target.value.toUpperCase() })} /></label>
      </div>
      {message && <Notice error={message.error}>{message.text}</Notice>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('pur.saving') : t('pur.save')}</button></div>
    </form>
    <section className="surface cmPanel" aria-labelledby="pur-iphone-title">
      <h2 id="pur-iphone-title"><Smartphone aria-hidden="true" className="purInlineIcon" />{t('pur.iphone.title')}</h2>
      <p>{t('pur.iphone.text')}</p>
      <p className="muted purNote">{t('pur.iphone.safety')}</p>
      <a className="purLinkButton" href="#iphone">{t('pur.iphone.open')}</a>
    </section>
  </div>;
}
