// Purchases, deliveries, returns and refunds (docs/purchases.md#web). Overview (what is on the way, late, open returns,
// spend and refunds per currency, what is due), Purchases (search, status filter, archived, paging, CSV), Returns (open
// returns across purchases) and Settings (reminders, default currency, the iPhone Shortcut). A purchase opens its own page.
// Payments and refunds are links to the person's Finance transactions, never new money: the page says so wherever money is shown.
import { useCallback, useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Lock, PackageOpen, Plus, Smartphone, Undo2 } from 'lucide-react';
import type { Purchase, PurchaseFilter, PurchaseOverview, PurchasePage, PurchasePreferences, PurchaseReturn } from '@anum/contracts';
import * as api from './lib/purchasesApi';
import {
  FILTERS, PAGE_SIZE, PURCHASE_TABS, dueLabel, dueText, filterLabel, hashForTab, linkText, moneyList, purchaseName, returnStatusLabel, statusLabel,
  statusTone, tabFromHash, type PurchaseTab,
} from './lib/purchases';
import { formatAmount } from './lib/finance';
import { describeError } from './lib/errors';
import { t, type MessageKey } from './lib/i18n';
import { figure, type Figure } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import { Empty, LoadError, Notice, failed, type Load } from './FinanceParts';
import { PageHeader, PageSummary, type Crumb } from './PageParts';
import { PurchaseDetailView, PurchaseForm } from './PurchaseParts';
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
  const [adding, setAdding] = useState(false);
  const [tick, setTick] = useState(0);
  const [denied, setDenied] = useState(false);
  const refresh = () => setTick((n) => n + 1);
  const crumbs: Crumb[] = [{ label: viewLabel('home'), href: '#home' }, { label: viewLabel('purchases'), href: '#purchases', onClick: () => { setOpenId(null); pick('overview'); } }];
  if (!openId && tab !== 'overview') crumbs.push({ label: t(`pur.tab.${tab}` as MessageKey) });
  const header = <PageHeader title={viewLabel('purchases')} subtitle={t('pur.subtitle')} crumbs={openId || tab !== 'overview' ? crumbs : undefined}
    actions={denied ? undefined : <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('pur.add')}</button>} />;
  const added = (purchase: { id: string }) => { setAdding(false); refresh(); setOpenId(purchase.id); };
  const onDenied = useCallback(() => setDenied(true), []);
  if (denied) return <div className="cmLayout purLayout">{header}<PurchasesDenied /></div>;
  return <div className="cmLayout purLayout">
    {header}
    {adding && <PurchaseForm onClose={() => setAdding(false)} onSaved={added} />}
    {openId ? <PurchaseDetailView key={`${openId}:${tick}`} purchaseId={openId} onBack={() => { setOpenId(null); refresh(); }} onDeleted={() => { setOpenId(null); refresh(); }} />
      : <>
        <nav className="settingsTabs cmTabs purTabs" aria-label={t('pur.tabs')}>
          {PURCHASE_TABS.map((id) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined} onClick={() => pick(id)}>{t(`pur.tab.${id}` as MessageKey)}</button>)}
        </nav>
        {tab === 'overview' && <OverviewPanel key={tick} onOpen={setOpenId} onAdd={() => setAdding(true)} onDenied={onDenied} onList={() => pick('list')} />}
        {tab === 'list' && <ListPanel key={tick} onOpen={setOpenId} onAdd={() => setAdding(true)} onDenied={onDenied} />}
        {tab === 'returns' && <ReturnsPanel key={tick} onOpen={setOpenId} onDenied={onDenied} />}
        {tab === 'settings' && <SettingsPanel onDenied={onDenied} />}
      </>}
  </div>;
}

// ---- Overview ------------------------------------------------------------------------------------------

function OverviewPanel({ onOpen, onAdd, onDenied, onList }: { onOpen: (id: string) => void; onAdd: () => void; onDenied: () => void; onList: () => void }) {
  const [data, setData] = useState<Load<PurchaseOverview>>({ kind: 'loading' });
  const load = useCallback(async () => {
    setData({ kind: 'loading' });
    try { setData({ kind: 'ready', value: await api.overview() }); } catch (e) { setData(failed(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (data.kind === 'denied') onDenied(); }, [data.kind, onDenied]);
  const state = data.kind === 'ready' ? 'ready' : data.kind === 'loading' ? 'loading' : 'error';
  const value = data.kind === 'ready' ? data.value : null;
  const figures: Figure[] = [
    figure('awaiting', t('pur.fig.awaiting'), { state }, () => ({ value: String(value!.awaiting_delivery), hint: value!.late ? t('pur.fig.late', { count: value!.late }) : t('pur.fig.awaitingHint'), tone: value!.late ? 'warn' : undefined })),
    figure('returns', t('pur.fig.returns'), { state }, () => ({ value: String(value!.open_returns), hint: value!.refunds_owed.length ? t('pur.fig.owed', { amount: moneyList(value!.refunds_owed) }) : t('pur.fig.returnsHint') })),
    figure('spend', t('pur.fig.spend'), { state }, () => ({ value: moneyList(value!.spend), hint: value!.unlinked ? t('pur.fig.unlinked', { count: value!.unlinked }) : t('pur.fig.spendHint') })),
    figure('refunded', t('pur.fig.refunded'), { state }, () => ({ value: moneyList(value!.refunded), hint: t('pur.fig.refundedHint') })),
  ];
  if (data.kind === 'error') return <LoadError message={t('pur.error.load', { message: data.message })} retry={() => void load()} />;
  if (data.kind === 'denied') return null;
  if (value && value.purchases === 0) return <section className="surface cmPanel"><Empty icon={<PackageOpen aria-hidden="true" />} title={t('pur.empty.title')} text={t('pur.empty.text')}
    action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('pur.add')}</button>} /></section>;
  return <>
    <PageSummary label={t('pur.summary.label')} figures={figures} onRetry={() => void load()} footer={<p className="muted purNote">{t('pur.noPosting')}</p>} />
    {data.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {value && value.needs_review > 0 && <Notice>{t('pur.review.count', { count: value.needs_review })}</Notice>}
    {value && <div className="purGrid">
      <section className="surface cmPanel" aria-labelledby="pur-due-title">
        <h2 id="pur-due-title">{t('pur.due.title')}</h2>
        {value.due.length === 0 ? <p className="muted">{t('pur.due.none')}</p>
          : <ul className="cmList purDueList">{value.due.map((item) => <li key={`${item.purchase_id}:${item.kind}:${item.source_id}`}>
            <div><strong dir="auto">{dueLabel(item)} · <bdi>{purchaseName(item)}</bdi></strong><small>{dueText(item)} · {item.due_on}</small></div>
            <button type="button" className="secondary" aria-label={t('pur.openLabel', { name: purchaseName(item) })} onClick={() => onOpen(item.purchase_id)}>{t('pur.open')}</button>
          </li>)}</ul>}
      </section>
      <section className="surface cmPanel" aria-labelledby="pur-month-title">
        <h2 id="pur-month-title">{t('pur.monthly.title')}</h2>
        {value.monthly.length === 0 ? <p className="muted">{t('pur.monthly.none')}</p>
          : <div className="purTableWrap"><table className="cmTable purTable"><thead><tr><th scope="col">{t('pur.col.month')}</th><th scope="col" className="num">{t('pur.col.paid')}</th></tr></thead>
            <tbody>{value.monthly.map((row) => <tr key={`${row.month}:${row.currency}`}><td>{row.month}</td><td className="num" dir="ltr">{formatAmount(row.amount, row.currency)}</td></tr>)}</tbody></table></div>}
        <dl className="purFacts">
          <dt>{t('pur.fig.net')}</dt><dd dir="ltr">{moneyList(value.net)}</dd>
          <dt>{t('pur.fig.owedTitle')}</dt><dd dir="ltr">{moneyList(value.refunds_owed)}</dd>
        </dl>
        <button type="button" className="secondary" onClick={onList}>{t('pur.allPurchases')}</button>
      </section>
    </div>}
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

function ListPanel({ onOpen, onAdd, onDenied }: { onOpen: (id: string) => void; onAdd: () => void; onDenied: () => void }) {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<PurchaseFilter | ''>('');
  const [archived, setArchived] = useState(false);
  const [offset, setOffset] = useState(0);
  const [run, setRun] = useState(0);
  const [page, setPage] = useState<Load<PurchasePage<Purchase>>>({ kind: 'loading' });
  const [message, setMessage] = useState<{ text: string; error?: boolean } | null>(null);
  const load = useCallback(async () => {
    setPage({ kind: 'loading' });
    try { setPage({ kind: 'ready', value: await api.listPurchases(search, status, archived, offset, PAGE_SIZE) }); } catch (e) { setPage(failed(e)); }
  }, [search, status, archived, offset, run]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (page.kind === 'denied') onDenied(); }, [page.kind, onDenied]);
  const exportCsv = async () => {
    setMessage(null);
    try { await api.exportCsv(archived); setMessage({ text: t('pur.exported') }); } catch (e) { setMessage({ text: describeError(e), error: true }); }
  };
  const filtered = Boolean(search || status || archived);
  return <section className="surface cmPanel" aria-labelledby="pur-list-title">
    <div className="purBar"><h2 id="pur-list-title">{archived ? t('pur.list.archivedTitle') : t('pur.list.title')}</h2>
      <button type="button" className="secondary" onClick={() => void exportCsv()}><Download aria-hidden="true" />{t('pur.export')}</button></div>
    <form className="cmFilters purFilters" role="search" onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(q.trim()); setRun((n) => n + 1); }}>
      <label className="cmInline cmSearch"><span>{t('pur.list.search')}</span><input type="search" dir="auto" value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} /></label>
      <label className="cmInline"><span>{t('pur.list.status')}</span>
        <select value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value as PurchaseFilter | ''); }}>
          {FILTERS.map((f) => <option key={f} value={f}>{filterLabel(f)}</option>)}
        </select></label>
      <button type="submit" className="secondary">{t('pur.list.find')}</button>
      <label className="purCheck"><input type="checkbox" checked={archived} onChange={(e) => { setOffset(0); setArchived(e.target.checked); }} />{t('pur.list.showArchived')}</label>
    </form>
    {message && <Notice error={message.error}>{message.text}</Notice>}
    {page.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('pur.error.load', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && (filtered
      ? <p className="muted">{search ? t('pur.list.noMatch', { q: search }) : t('pur.list.noneHere')}</p>
      : <Empty icon={<PackageOpen aria-hidden="true" />} title={t('pur.empty.title')} text={t('pur.empty.text')} action={<button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('pur.add')}</button>} />)}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <ul className="cmList purList">{page.value.items.map((p) => <li key={p.id}>
        <div><strong dir="auto"><bdi>{p.merchant}</bdi>{p.order_ref ? <> · <bdi>{p.order_ref}</bdi></> : null}</strong>
          <small>{[p.ordered_on, p.total ? formatAmount(p.total, p.currency) : '', t(`pur.own.${p.ownership}` as MessageKey), p.mine ? linkText(p.payment) : '',
            p.due[0] ? `${dueLabel(p.due[0])}: ${dueText(p.due[0])}` : ''].filter(Boolean).join(' · ')}</small>
          <span className="purChips"><StatusChip status={p.status} />{p.needs_review && <span className="purChip warn">{t('pur.review.chip')}</span>}</span></div>
        <button type="button" className="secondary" aria-label={t('pur.openLabel', { name: purchaseName(p) })} onClick={() => onOpen(p.id)}>{t('pur.open')}</button>
      </li>)}</ul>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
  </section>;
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
  return <section className="surface cmPanel" aria-labelledby="pur-ret-title">
    <div className="purBar"><h2 id="pur-ret-title">{t('pur.returns.title')}</h2>
      <label className="purCheck"><input type="checkbox" checked={!open} onChange={(e) => { setOffset(0); setOpen(!e.target.checked); }} />{t('pur.returns.showClosed')}</label></div>
    <p className="muted purNote">{t('pur.returns.rule')}</p>
    {page.kind === 'loading' && <Notice busy>{t('pur.loading')}</Notice>}
    {page.kind === 'error' && <LoadError message={t('pur.error.load', { message: page.message })} retry={() => void load()} />}
    {page.kind === 'ready' && page.value.total === 0 && <Empty icon={<Undo2 aria-hidden="true" />} title={t('pur.returns.empty')} text={t('pur.returns.emptyText')} />}
    {page.kind === 'ready' && page.value.total > 0 && <>
      <ul className="cmList purList">{page.value.items.map((r) => <li key={r.id}>
        <div><strong dir="auto"><bdi>{r.merchant ?? '-'}</bdi>{r.order_ref ? <> · <bdi>{r.order_ref}</bdi></> : null}</strong>
          <small>{[r.requested_on, r.amount ? t('pur.ret.amountLine', { amount: formatAmount(r.amount, r.currency) }) : '',
            Number(r.refunded) > 0 ? t('pur.ret.refundedLine', { amount: formatAmount(r.refunded, r.currency) }) : ''].filter(Boolean).join(' · ')}</small>
          <span className="purChips"><StatusChip status={r.status} kind="return" /></span></div>
        <button type="button" className="secondary" aria-label={t('pur.openLabel', { name: r.merchant ?? r.purchase_id })} onClick={() => onOpen(r.purchase_id)}>{t('pur.open')}</button>
      </li>)}</ul>
      <Pager total={page.value.total} offset={offset} onChange={setOffset} />
    </>}
  </section>;
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
