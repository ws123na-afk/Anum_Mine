// The Document vault (docs/documents.md#the-web-page): identity papers, contracts, insurance and
// receipts on top of Files storage. Tabs: Overview, Documents (search, filters, list, detail with
// preview, versions, sharing, links, scanning and activity), To review (scan results waiting for a
// person), Expiring, Trash and Settings (reminders, scanning switches, categories, iPhone Shortcut).
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArchiveRestore, CalendarClock, Check, ChevronLeft, ChevronRight, CircleAlert, CircleDot, Copy, Download, Eye, FileText, History, Layers, Link2, Lock, Pencil, Plus,
  RefreshCw, RotateCcw, ScanLine, ScanText, Search, Share2, ShieldCheck, Smartphone, Tags, Trash2, Upload, Users, X,
} from 'lucide-react';
import { ConfirmDialog, Modal } from './Dialog';
import { FinBanner, FinCard, FinancePageHead, FinanceTabs, KpiCard, KpiRow, StatusChip, tabPanel } from './FinanceShell';
import { LoadingSkeleton, PaneProblem } from './PageParts';
import { copyText } from './lib/clipboard';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatBytes } from './lib/files';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import {
  CURRENCIES, DATE_FIELDS, PAGE_SIZE, RECEIPT_FIELDS, SORTS, VAULT_TABS, activityLabel, categoryLabel, confidenceText, documentFromHash, editDraft, editPatch, expiriesByDay,
  expiryText, expiryTone, filterQuery, hashOf, isIdentity, linkLabel, linkedPercent, mergeDocuments, monthGrid, reviewDraft, reviewProblems, rowStatus, rowStatusTone,
  suggestionFields, tabFromHash, uploadProblem, warningTexts,
  type DocumentExtraction, type EditDraft, type VaultDevice, type VaultDeviceCreated, type VaultDocument, type VaultDocumentDetail, type VaultDocumentRow, type VaultMeta,
  type VaultReviewItem, type VaultSort, type VaultTab, type VaultVersionPage,
} from './lib/vault';
import * as api from './lib/vaultApi';
import './admin.css';
import './vault.css';

const PdfPreview = lazy(() => import('./PdfPreview'));

type Flash = { text: string; error?: boolean } | null;
const day = (value: string) => formatDateTime(`${value}T00:00:00Z`, { dateStyle: 'medium', timeZone: 'UTC' });
const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

function Notice({ children, error }: { children: ReactNode; error?: boolean }) {
  return <p className={`notice ${error ? 'errorNotice' : ''}`} role={error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{children}</p>;
}

function Badge({ tone = '', children }: { tone?: string; children: ReactNode }) {
  return <span className={`vaultBadge ${tone}`}>{children}</span>;
}

function Empty({ icon, title, text, action }: { icon: ReactNode; title: string; text: string; action?: ReactNode }) {
  return <div className="emptyState vaultEmpty">{icon}<h3>{title}</h3><p>{text}</p>{action}</div>;
}

export function VaultView() {
  const [tab, setTab] = useState<VaultTab>(() => tabFromHash(location.hash));
  const [meta, setMeta] = useState<Load<VaultMeta>>({ state: 'loading' });
  const [focus, setFocus] = useState<string | null>(() => documentFromHash(location.hash));
  const [adding, setAdding] = useState<'upload' | 'scan' | null>(null);
  const [flash, setFlash] = useState<Flash>(null);
  const [tick, setTick] = useState(0);

  const loadMeta = useCallback(() => api.getMeta().then((value) => setMeta({ state: 'ready', value }), (error) => setMeta(
    isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) })), []);
  useEffect(() => { void loadMeta(); }, [loadMeta, tick]);
  useEffect(() => {
    const onHash = () => {
      if (!location.hash.startsWith('#vault')) return;
      setTab(tabFromHash(location.hash));
      const doc = documentFromHash(location.hash);
      if (doc) setFocus(doc);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (next: VaultTab) => { setTab(next); setFocus(null); history.replaceState(null, '', hashOf(next)); };
  const openDoc = (id: string) => { setTab('documents'); setFocus(id); history.replaceState(null, '', hashOf('documents')); };
  const changed = (text?: string) => { if (text) setFlash({ text }); setTick((n) => n + 1); };
  const value = meta.state === 'ready' ? meta.value : null;
  const counts = value?.summary;

  if (meta.state === 'denied') {
    return <div className="finPage vaultPage">
      <FinancePageHead title={viewLabel('vault')} subtitle={t('vault.intro')} />
      <section className="surface adminForbidden" aria-label={t('vault.denied.title')}>
        <span className="adminIcon"><Lock aria-hidden="true" /></span>
        <div><h2>{t('vault.denied.title')}</h2><p>{t('vault.denied.text')}</p><p className="muted">{t('vault.apiSaid', { message: meta.message })}</p></div>
      </section>
    </div>;
  }

  const badge = (key: VaultTab) => {
    const n = key === 'review' ? counts?.review : key === 'expiring' ? (counts ? counts.expiring + counts.expired : undefined) : key === 'trash' ? counts?.trash : undefined;
    return n ? formatNumber(n) : undefined;
  };
  const tabs = VAULT_TABS.map((key) => ({ id: key, label: t(`vault.tab.${key}` as MessageKey), badge: badge(key) }));
  const actions = value?.can_write ? <>
    <button type="button" className="secondary" onClick={() => setAdding('scan')}><ScanLine aria-hidden="true" />{t('vault.scan')}</button>
    <button type="button" onClick={() => setAdding('upload')}><Upload aria-hidden="true" />{t('vault.upload')}</button>
  </> : undefined;

  return <div className="finPage vaultPage">
    <FinancePageHead title={viewLabel('vault')} subtitle={t('vault.intro')} actions={actions}
      tabs={<FinanceTabs label={t('vault.tabs')} items={tabs} value={tab} idPrefix="vault" onChange={go} />} />
    {meta.state === 'error' && <PaneProblem text={t('vault.metaFailed', { message: meta.message })} onRetry={() => { setMeta({ state: 'loading' }); void loadMeta(); }} />}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <div {...tabPanel('vault', tab)}>
      {tab === 'overview' && <Overview meta={value} tick={tick} onOpen={openDoc} onGo={go} onAdd={() => setAdding('upload')} onChanged={changed} />}
      {tab === 'documents' && <DocumentsTab meta={value} tick={tick} focus={focus} onFocus={setFocus} onChanged={changed} onAdd={() => setAdding('upload')} status="active" />}
      {tab === 'expiring' && <DocumentsTab meta={value} tick={tick} focus={focus} onFocus={setFocus} onChanged={changed} onAdd={() => setAdding('upload')} status="active" expiringOnly />}
      {tab === 'trash' && <TrashTab meta={value} tick={tick} onChanged={changed} />}
      {tab === 'review' && <ReviewTab meta={value} tick={tick} onChanged={changed} onOpen={openDoc} />}
      {tab === 'categories' && <CategoriesTab meta={value} tick={tick} onChanged={changed} />}
      {tab === 'access' && <AccessTab meta={value} />}
      {tab === 'settings' && <SettingsTab meta={value} onChanged={changed} />}
    </div>
    {adding && value && <AddDialog meta={value} scan={adding === 'scan'} onClose={() => setAdding(null)} onAdded={(doc, text) => {
      setAdding(null); changed(text); openDoc(doc.id);
    }} onOpenExisting={(id) => { setAdding(null); openDoc(id); }} />}
  </div>;
}

// --------------------------------------------------------------------------- overview

function Overview({ meta, tick, onOpen, onGo, onAdd, onChanged }: { meta: VaultMeta | null; tick: number; onOpen(id: string): void; onGo(tab: VaultTab): void; onAdd(): void; onChanged(text?: string): void }) {
  if (meta && meta.summary.active === 0 && meta.summary.trash === 0) {
    return <FinCard title={t('vault.docs.title')}><Empty icon={<ShieldCheck aria-hidden="true" />} title={t('vault.empty.title')} text={t('vault.empty.text')}
      action={meta.can_write ? <div className="actions"><button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('vault.add')}</button>
        <button type="button" className="secondary" onClick={() => onGo('access')}><Smartphone aria-hidden="true" />{t('vault.empty.phone')}</button></div> : undefined} /></FinCard>;
  }
  const counts = meta?.summary;
  const pct = counts ? linkedPercent(counts) : null;
  const loading = <span className="muted">{t('vault.loading')}</span>;
  return <>
    <KpiRow label={t('vault.summary')}>
      <KpiCard icon={<FileText />} label={t('vault.fig.documents')} value={counts ? formatNumber(counts.active) : loading} hint={counts ? t('vault.fig.privateHint', { count: counts.private }) : undefined} />
      <KpiCard icon={<CalendarClock />} label={meta ? t('vault.kpi.expiring', { days: meta.reminder_window_days }) : t('vault.fig.expiring')} value={counts ? formatNumber(counts.expiring) : loading}
        hint={counts ? (counts.expired ? t('vault.kpi.expired', { count: counts.expired }) : t('vault.kpi.noneExpired')) : undefined} tone={counts?.expired ? 'bad' : counts?.expiring ? 'warn' : ''} />
      <KpiCard icon={<ScanText />} label={t('vault.kpi.review')} value={counts ? formatNumber(counts.review) : loading} hint={meta ? (meta.ocr_connected ? t('vault.fig.ocrOn') : t('vault.fig.ocrOff')) : undefined} tone={counts?.review ? 'warn' : ''} />
      <KpiCard icon={<Link2 />} label={t('vault.kpi.linked')} value={counts ? (pct === null ? '-' : t('vault.kpi.pct', { pct })) : loading}
        hint={counts ? t('vault.kpi.linkedHint', { linked: counts.linked, total: counts.active }) : undefined} />
    </KpiRow>
    <div className="vaultBoard">
      <div className="vaultBoardMain">
        <DocumentsTab meta={meta} tick={tick} focus={null} onFocus={(id) => { if (id) onOpen(id); }} onChanged={onChanged} onAdd={onAdd} status="active" embedded />
      </div>
      <div className="vaultBoardSide">
        <NextExpiries tick={tick} onOpen={onOpen} onAll={() => onGo('expiring')} />
        <ReviewPeek meta={meta} tick={tick} onGo={onGo} onOpen={onOpen} onChanged={onChanged} />
        <ScanFromPhone meta={meta} onGo={onGo} />
      </div>
    </div>
    <SecurityStrip meta={meta} />
  </>;
}

const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6];

function NextExpiries({ tick, onOpen, onAll }: { tick: number; onOpen(id: string): void; onAll(): void }) {
  const [load, setLoad] = useState<Load<VaultDocumentRow[]>>({ state: 'loading' });
  const now = new Date();
  const [month, setMonth] = useState({ year: now.getFullYear(), month: now.getMonth() });
  const fetch = useCallback(() => api.listDocuments(filterQuery({ q: '', category: '', access: '', expiry: '', sort: 'expiry' }, 'active', null, 100))
    .then((page) => setLoad({ state: 'ready', value: page.items }), (e) => setLoad({ state: 'error', message: describeError(e) })), []);
  useEffect(() => { void fetch(); }, [fetch, tick]);
  const shift = (by: number) => setMonth(({ year, month: m }) => { const d = new Date(year, m + by, 1); return { year: d.getFullYear(), month: d.getMonth() }; });
  const rows = load.state === 'ready' ? load.value : [];
  const marks = expiriesByDay(rows, month.year, month.month);
  const todayIso = new Date().toISOString().slice(0, 10);
  const monthName = formatDateTime(new Date(Date.UTC(month.year, month.month, 1)), { month: 'long', year: 'numeric', timeZone: 'UTC' });
  const weekday = (d: number) => formatDateTime(new Date(Date.UTC(2026, 10, 1 + d)), { weekday: 'narrow', timeZone: 'UTC' });
  return <FinCard title={t('vault.next.title')} icon={<CalendarClock />} actions={<button type="button" className="linkButton" onClick={onAll}>{t('vault.next.all')}</button>} className="vaultNext">
    {load.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={3} />}
    {load.state === 'error' && <PaneProblem text={t('vault.listFailed', { message: load.message })} onRetry={() => void fetch()} />}
    {load.state === 'ready' && <div className="vaultNextBody">
      <div className="vaultCal">
        <div className="vaultCalHead">
          <button type="button" className="vaultIconButton" aria-label={t('vault.next.prev')} onClick={() => shift(-1)}><ChevronLeft aria-hidden="true" className="vaultFlip" /></button>
          <strong aria-live="polite">{monthName}</strong>
          <button type="button" className="vaultIconButton" aria-label={t('vault.next.nextMonth')} onClick={() => shift(1)}><ChevronRight aria-hidden="true" className="vaultFlip" /></button>
        </div>
        <table className="vaultCalGrid">
          <caption className="visuallyHidden">{t('vault.next.calendar', { month: monthName })}</caption>
          <thead><tr>{WEEKDAYS.map((d) => <th key={d} scope="col" abbr={formatDateTime(new Date(Date.UTC(2026, 10, 1 + d)), { weekday: 'long', timeZone: 'UTC' })}>{weekday(d)}</th>)}</tr></thead>
          <tbody>{monthGrid(month.year, month.month).map((week, i) => <tr key={i}>{week.map((d, j) => {
            if (!d) return <td key={j} />;
            const iso = `${month.year}-${String(month.month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
            const titles = marks[d];
            return <td key={j} className={`${titles ? 'has' : ''} ${iso === todayIso ? 'today' : ''}`}>
              {titles ? <span title={titles.join(', ')}>{d}<span className="visuallyHidden">: {t('vault.next.expires', { titles: titles.join(', ') })}</span></span> : d}
            </td>;
          })}</tr>)}</tbody>
        </table>
      </div>
      {rows.length === 0 ? <p className="muted">{t('vault.next.none')}</p>
        : <ul className="vaultNextList">{rows.slice(0, 4).map((doc) => <li key={doc.id}>
          <button type="button" className="vaultNextItem" onClick={() => onOpen(doc.id)}>
            <span><strong dir="auto">{doc.title}</strong><small>{doc.expiry_date ? day(doc.expiry_date) : ''}</small></span>
            <StatusChip tone={doc.expiry_state === 'soon' ? 'warn' : 'ok'}>{expiryText(doc)}</StatusChip>
          </button>
        </li>)}</ul>}
    </div>}
  </FinCard>;
}

function ReviewPeek({ meta, tick, onGo, onOpen, onChanged }: { meta: VaultMeta | null; tick: number; onGo(tab: VaultTab): void; onOpen(id: string): void; onChanged(text?: string): void }) {
  const [load, setLoad] = useState<Load<VaultReviewItem[]>>({ state: 'loading' });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const fetch = useCallback(() => api.getReview().then((value) => setLoad({ state: 'ready', value }), (e) => setLoad({ state: 'error', message: describeError(e) })), []);
  useEffect(() => { void fetch(); }, [fetch, tick]);
  const first = load.state === 'ready' ? load.value[0] : null;
  const confirm = async () => {
    if (!first) return;
    const draft = reviewDraft(first.extraction);
    const problems = reviewProblems(first.extraction.kind, draft);
    if (Object.keys(problems).length) { onGo('review'); return; }
    setBusy(true); setProblem(null);
    try {
      await api.confirmExtraction(first.extraction.id, draft, first.extraction.items);
      onChanged(t('vault.review.confirmed', { title: first.document.title }));
      onOpen(first.document.id);
    } catch (e) { setProblem(describeError(e)); } finally { setBusy(false); }
  };
  const count = load.state === 'ready' ? load.value.length : 0;
  return <FinCard title={t('vault.peek.title', { count })} icon={<ScanText />} className="vaultPeek"
    actions={count > 0 ? <button type="button" className="linkButton" onClick={() => onGo('review')}>{t('vault.peek.all')}</button> : undefined}>
    {load.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={2} />}
    {load.state === 'error' && <PaneProblem text={t('vault.listFailed', { message: load.message })} onRetry={() => void fetch()} />}
    {load.state === 'ready' && !first && <p className="muted">{meta && !meta.ocr_connected ? t('vault.ocr.notConnected') : t('vault.overview.noneReview')}</p>}
    {first && <div className="vaultPeekBody">
      <div><strong dir="auto">{first.document.title}</strong><small className="muted">{t(`vault.kind.${first.extraction.kind}` as MessageKey)} · {when(first.extraction.created_at)}</small></div>
      <dl className="vaultSuggest" aria-label={t('vault.peek.suggestions')}>
        {suggestionFields(first.extraction).map((f) => <div key={f.key}><dt>{t(`vault.rf.${f.key}` as MessageKey)}</dt><dd dir="auto">{f.value}</dd></div>)}
      </dl>
      {suggestionFields(first.extraction).length === 0 && <p className="muted">{t('vault.review.notRead')}</p>}
      <p className="vaultPeekNote"><CircleAlert aria-hidden="true" />{t('vault.peek.note')}</p>
      {problem && <Notice error>{problem}</Notice>}
      {first.document.can_edit ? <div className="actions">
        <button type="button" disabled={busy} onClick={() => void confirm()}><Check aria-hidden="true" />{t('vault.peek.confirm')}</button>
        <button type="button" className="secondary" onClick={() => onGo('review')}><Pencil aria-hidden="true" />{t('vault.peek.edit')}</button>
      </div> : <p className="muted">{t('vault.review.notYours')}</p>}
    </div>}
  </FinCard>;
}

function ScanFromPhone({ meta, onGo }: { meta: VaultMeta | null; onGo(tab: VaultTab): void }) {
  const [devices, setDevices] = useState<Load<VaultDevice[]>>({ state: 'loading' });
  useEffect(() => { if (meta?.can_write) api.getDevices().then((value) => setDevices({ state: 'ready', value }), (e) => setDevices({ state: 'error', message: describeError(e) })); }, [meta?.can_write]);
  if (!meta?.can_write) return null;
  const active = devices.state === 'ready' ? devices.value.filter((d) => d.status === 'active') : [];
  return <FinCard title={t('vault.phone.title')} icon={<Smartphone />} className="vaultPhone">
    <p className="muted">{t('vault.phone.text')}</p>
    {devices.state === 'ready' && <p>{active.length ? t('vault.phone.connected', { count: active.length }) : t('vault.phone.none')}</p>}
    {devices.state === 'error' && <p className="muted">{devices.message}</p>}
    <div className="actions"><button type="button" className="secondary" onClick={() => onGo('access')}><Smartphone aria-hidden="true" />{active.length ? t('vault.phone.manage') : t('vault.phone.setup')}</button></div>
  </FinCard>;
}

function SecurityStrip({ meta }: { meta: VaultMeta | null }) {
  if (!meta) return null;
  const items: { icon: ReactNode; title: string; text: string }[] = [
    { icon: <History />, title: t('vault.sec.log'), text: t('vault.sec.logText') },
    { icon: <Lock />, title: t('vault.sec.private'), text: t('vault.sec.privateText', { count: meta.summary.private }) },
    { icon: <Users />, title: t('vault.sec.access'), text: t('vault.sec.accessText') },
    { icon: <Layers />, title: t('vault.sec.versions'), text: t('vault.sec.versionsText') },
    { icon: <Trash2 />, title: t('vault.sec.retention'), text: t('vault.sec.retentionText', { days: meta.settings.trash_days }) },
  ];
  return <FinCard title={t('vault.sec.title')} icon={<ShieldCheck />} subtitle={t('vault.sec.subtitle')} className="vaultSecurity">
    <ul className="vaultSecList">{items.map((item) => <li key={item.title}><span className="vaultSecIcon" aria-hidden="true">{item.icon}</span><span><strong>{item.title}</strong><small>{item.text}</small></span></li>)}</ul>
    <FinBanner tone="info" icon={<Lock />}>{t('vault.sec.twoStep')}</FinBanner>
  </FinCard>;
}

// --------------------------------------------------------------------------- documents list and detail

function DocumentsTab({ meta, tick, focus, onFocus, onChanged, onAdd, status, expiringOnly, embedded }: {
  meta: VaultMeta | null; tick: number; focus: string | null; onFocus(id: string | null): void; onChanged(text?: string): void; onAdd(): void;
  status: 'active'; expiringOnly?: boolean; embedded?: boolean;
}) {
  const blank = { q: '', category: '', access: '', expiry: expiringOnly ? 'soon' : '', sort: (embedded ? 'updated' : 'created') as VaultSort };
  const [filters, setFilters] = useState(blank);
  const [draft, setDraft] = useState(filters);
  const [items, setItems] = useState<VaultDocumentRow[]>([]);
  const [load, setLoad] = useState<Load<null>>({ state: 'loading' });
  const [cursor, setCursor] = useState<string | null>(null);
  const [more, setMore] = useState<'idle' | 'loading' | { error: string }>('idle');
  const [problem, setProblem] = useState<string | null>(null);
  const request = useRef(0);
  const ids = { q: useId(), category: useId(), access: useId(), expiry: useId(), sort: useId() };
  const limit = embedded ? 6 : PAGE_SIZE;

  const fetchFirst = useCallback(async (next: typeof filters) => {
    const token = ++request.current;
    setLoad((current) => (current.state === 'ready' ? current : { state: 'loading' }));
    try {
      const page = await api.listDocuments(filterQuery(next, status, null, limit));
      let rows = page.items;
      if (expiringOnly && next.expiry === 'soon') {
        const expired = await api.listDocuments(filterQuery({ ...next, expiry: 'expired' }, status));
        rows = [...expired.items, ...rows];
      }
      if (token !== request.current) return;
      setItems(rows); setCursor(page.next_cursor); setLoad({ state: 'ready', value: null });
    } catch (error) {
      if (token !== request.current) return;
      setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) });
    }
  }, [status, expiringOnly, limit]);
  useEffect(() => { void fetchFirst(filters); }, [fetchFirst, filters, tick]);

  const loadMore = async () => {
    setMore('loading');
    try {
      const page = await api.listDocuments(filterQuery(filters, status, cursor));
      setItems((current) => mergeDocuments(current, page.items) as VaultDocumentRow[]); setCursor(page.next_cursor); setMore('idle');
    } catch (error) { setMore({ error: describeError(error) }); }
  };
  const apply = (next: typeof filters) => { setDraft(next); setFilters(next); };
  const submit = (event: FormEvent) => { event.preventDefault(); setFilters(draft); };
  const clear = () => apply(blank);
  const filtered = Boolean(filters.q || filters.category || filters.access || (!expiringOnly && filters.expiry));
  const categories = meta?.categories ?? [];
  const download = async (doc: VaultDocumentRow) => {
    setProblem(null);
    try { await api.download(doc.id, doc.file?.name ?? doc.title); } catch (e) { setProblem(describeError(e)); }
  };
  const title = expiringOnly ? t('vault.tab.expiring') : t('vault.docs.title');
  const count = load.state === 'ready' ? (cursor ? t('vault.docs.countMore', { count: items.length }) : t('vault.docs.count', { count: items.length })) : undefined;

  return <div className={`vaultSplit ${focus ? 'hasDetail' : ''}`}>
    <FinCard title={title} subtitle={count} className="vaultList">
      <form className="vaultFilters" role="search" aria-label={t(expiringOnly ? 'vault.list.expiringLabel' : 'vault.list.label')} onSubmit={submit}>
        <label className="vaultSearchBox" htmlFor={ids.q}><Search aria-hidden="true" /><span className="visuallyHidden">{t('vault.filter.search')}</span>
          <input id={ids.q} type="search" dir="auto" value={draft.q} maxLength={120} placeholder={t('vault.filter.searchHint')} onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            onBlur={() => { if (draft.q !== filters.q) setFilters({ ...filters, q: draft.q }); }} /></label>
        <label className="vaultSelect" htmlFor={ids.category}><span>{t('vault.field.category')}</span>
          <select id={ids.category} value={draft.category} onChange={(e) => apply({ ...draft, category: e.target.value })}>
            <option value="">{t('vault.filter.all')}</option>
            {categories.map((c) => <option key={c.id} value={c.id}>{categoryLabel(c.id, categories)}</option>)}
          </select></label>
        {!expiringOnly && <label className="vaultSelect" htmlFor={ids.expiry}><span>{t('vault.filter.expiryState')}</span>
          <select id={ids.expiry} value={draft.expiry} onChange={(e) => apply({ ...draft, expiry: e.target.value })}>
            <option value="">{t('vault.filter.all')}</option><option value="soon">{t('vault.filter.soon')}</option><option value="expired">{t('vault.filter.expired')}</option>
          </select></label>}
        <label className="vaultSelect" htmlFor={ids.access}><span>{t('vault.filter.workspace')}</span>
          <select id={ids.access} value={draft.access} onChange={(e) => apply({ ...draft, access: e.target.value })}>
            <option value="">{t('vault.filter.all')}</option>
            <option value="private">{t('vault.access.private')}</option><option value="workspace">{t('vault.access.workspace')}</option>
          </select></label>
        {!expiringOnly && <label className="vaultSelect" htmlFor={ids.sort}><span>{t('vault.filter.sort')}</span>
          <select id={ids.sort} value={draft.sort} onChange={(e) => apply({ ...draft, sort: e.target.value as VaultSort })}>
            {SORTS.map((key) => <option key={key} value={key}>{t(`vault.sort.${key}` as MessageKey)}</option>)}
          </select></label>}
        {filtered && <button type="button" className="secondary vaultClear" onClick={clear}><RotateCcw aria-hidden="true" />{t('vault.filter.clear')}</button>}
      </form>
      {problem && <Notice error>{problem}</Notice>}
      {load.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={4} />}
      {load.state === 'error' && <PaneProblem text={t('vault.listFailed', { message: load.message })} onRetry={() => void fetchFirst(filters)} />}
      {load.state === 'ready' && !items.length && (filtered
        ? <Empty icon={<Search aria-hidden="true" />} title={t('vault.list.noMatch')} text={t('vault.list.noMatchText')} action={<button type="button" className="secondary" onClick={clear}>{t('vault.filter.clear')}</button>} />
        : expiringOnly
          ? <Empty icon={<CalendarClock aria-hidden="true" />} title={t('vault.expiring.none')} text={t('vault.expiring.noneText')} />
          : filters.sort === 'expiry'
            ? <Empty icon={<CalendarClock aria-hidden="true" />} title={t('vault.sort.expiryNone')} text={t('vault.sort.expiryNoneText')} />
            : <Empty icon={<FileText aria-hidden="true" />} title={t('vault.list.empty')} text={t('vault.list.emptyText')} action={meta?.can_write ? <button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('vault.add')}</button> : undefined} />)}
      {load.state === 'ready' && items.length > 0 && <div className="finTableWrap"><table className="finGrid vaultTable">
        <caption className="visuallyHidden">{title}</caption>
        <thead><tr>
          <th scope="col">{t('vault.col.title')}</th><th scope="col" className="vaultOpt">{t('vault.col.linked')}</th><th scope="col">{t('vault.field.category')}</th>
          <th scope="col" className="vaultOpt">{t('vault.col.updated')}</th><th scope="col">{t('vault.col.expiry')}</th><th scope="col">{t('vault.col.status')}</th>
          <th scope="col"><span className="visuallyHidden">{t('vault.col.actions')}</span></th>
        </tr></thead>
        <tbody>{items.map((doc) => {
          const state = rowStatus(doc, categories);
          return <tr key={doc.id} className={focus === doc.id ? 'selected' : undefined}>
            <th scope="row"><button type="button" className="vaultRowButton" aria-current={focus === doc.id || undefined} onClick={() => onFocus(doc.id)}>
              <span className={`vaultFileIcon ${doc.file?.kind ?? ''}`} aria-hidden="true">{(doc.file?.name.split('.').pop() ?? 'file').slice(0, 4).toUpperCase()}</span>
              <span className="vaultRowText"><strong dir="auto">{doc.title}</strong>
                {doc.file && <small><bdi dir="ltr">{doc.file.name} ({formatBytes(doc.file.size_bytes)})</bdi></small>}
                {doc.number_masked && <small><bdi dir="ltr">{doc.number_masked}</bdi></small>}
              </span>
            </button>{isIdentity(doc.category, categories) && <StatusChip tone="muted"><Lock aria-hidden="true" />{t('vault.row.identity')}</StatusChip>}</th>
            <td className="vaultOpt">{doc.first_link ? <span className="vaultLinkCell"><strong dir="auto">{linkLabel(doc.first_link)}</strong>
              <small>{t(`vault.link.${doc.first_link.target_type}` as MessageKey)}{doc.links_count > 1 ? ` · ${t('vault.row.moreLinks', { count: doc.links_count - 1 })}` : ''}</small></span>
              : <span className="muted">{t('vault.row.noLink')}</span>}</td>
            <td><StatusChip tone="muted">{categoryLabel(doc.category, categories)}</StatusChip></td>
            <td className="vaultOpt">{formatDateTime(doc.updated_at, { dateStyle: 'medium' })}</td>
            <td>{doc.expiry_date ? day(doc.expiry_date) : <span className="muted">{t('vault.row.noExpiry')}</span>}</td>
            <td><StatusChip tone={rowStatusTone(state)}>{state === 'restricted' ? <Lock aria-hidden="true" /> : state === 'valid' ? <Check aria-hidden="true" /> : <CircleAlert aria-hidden="true" />}{t(`vault.status.${state}` as MessageKey)}</StatusChip></td>
            <td className="vaultActions">
              {doc.file && doc.file.scan_status !== 'infected' && <button type="button" className="vaultIconButton" aria-label={t('vault.row.download', { title: doc.title })} onClick={() => void download(doc)}><Download aria-hidden="true" /></button>}
              <button type="button" className="vaultIconButton" aria-label={t('vault.row.versions', { title: doc.title, version: doc.file?.version ?? 1 })} onClick={() => onFocus(doc.id)}><History aria-hidden="true" /></button>
            </td>
          </tr>;
        })}</tbody>
      </table></div>}
      {embedded && load.state === 'ready' && items.length > 0 && cursor && <div className="actions vaultMore"><a className="linkButton" href={hashOf('documents')}>{t('vault.docs.all')}</a></div>}
      {!embedded && cursor && load.state === 'ready' && <div className="actions vaultMore"><button type="button" className="secondary" disabled={more === 'loading'} onClick={() => void loadMore()}>{more === 'loading' ? t('vault.loading') : t('vault.list.more')}</button></div>}
      {typeof more === 'object' && <Notice error>{more.error}</Notice>}
    </FinCard>
    {focus && <DocumentDetail key={focus} id={focus} meta={meta} onClose={() => onFocus(null)} onChanged={onChanged} />}
  </div>;
}

function Preview({ doc }: { doc: VaultDocumentDetail }) {
  const [state, setState] = useState<{ kind: 'idle' | 'loading' } | { kind: 'image'; url: string } | { kind: 'pdf'; data: ArrayBuffer } | { kind: 'error'; message: string }>({ kind: 'idle' });
  const file = doc.file;
  useEffect(() => () => { if (state.kind === 'image') URL.revokeObjectURL(state.url); }, [state]);
  if (!file) return null;
  if (file.scan_status === 'infected') return <Notice error>{t('vault.preview.quarantined')}</Notice>;
  if (!file.previewable || (file.kind !== 'image' && file.kind !== 'pdf')) return <p className="muted">{t('vault.preview.none')}</p>;
  const open = async () => {
    setState({ kind: 'loading' });
    try {
      const blob = await api.previewBytes(doc.id);
      setState(file.kind === 'pdf' ? { kind: 'pdf', data: await blob.arrayBuffer() } : { kind: 'image', url: URL.createObjectURL(blob) });
    } catch (error) { setState({ kind: 'error', message: describeError(error) }); }
  };
  return <div className="vaultPreview">
    {state.kind === 'idle' && <button type="button" className="secondary" onClick={() => void open()}><Eye aria-hidden="true" />{t('vault.preview.show')}</button>}
    {state.kind === 'loading' && <LoadingSkeleton label={t('vault.preview.loading')} rows={3} />}
    {state.kind === 'image' && <img src={state.url} alt={t('vault.preview.alt', { title: doc.title })} />}
    {state.kind === 'pdf' && <Suspense fallback={<LoadingSkeleton label={t('vault.preview.loading')} />}><PdfPreview data={state.data} name={file.name} /></Suspense>}
    {state.kind === 'error' && <PaneProblem text={t('vault.preview.failed', { message: state.message })} onRetry={() => void open()} />}
  </div>;
}

function DocumentDetail({ id, meta, onClose, onChanged }: { id: string; meta: VaultMeta | null; onClose(): void; onChanged(text?: string): void }) {
  const [load, setLoad] = useState<Load<VaultDocumentDetail>>({ state: 'loading' });
  const [editing, setEditing] = useState<EditDraft | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmTrash, setConfirmTrash] = useState(false);
  const [number, setNumber] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const categories = meta?.categories ?? [];
  const fetch = useCallback(() => api.getDocument(id).then((value) => setLoad({ state: 'ready', value }),
    (error) => setLoad(isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) })), [id]);
  useEffect(() => { void fetch(); }, [fetch]);
  useEffect(() => { if (load.state === 'ready') heading.current?.focus(); }, [load.state]);

  const run = async (key: string, work: () => Promise<unknown>, text?: string) => {
    setBusy(key); setProblem(null);
    try { await work(); await fetch(); onChanged(text); } catch (error) { setProblem(describeError(error)); } finally { setBusy(null); }
  };

  if (load.state === 'loading') return <section className="surface vaultDetail" aria-label={t('vault.detail.label')}><LoadingSkeleton label={t('vault.loading')} rows={6} /></section>;
  if (load.state !== 'ready') return <section className="surface vaultDetail" aria-label={t('vault.detail.label')}>
    <PaneProblem text={t('vault.detail.failed', { message: load.message })} onRetry={() => void fetch()} />
    <button type="button" className="secondary" onClick={onClose}>{t('vault.detail.close')}</button></section>;
  const doc = load.value;
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    const { patch, problem: why } = editPatch(doc, editing);
    if (!patch) { setProblem(why); return; }
    await run('save', async () => { await api.patchDocument(doc.id, patch); setEditing(null); }, t('vault.detail.saved', { title: patch.title ?? doc.title }));
  };

  return <section className="surface vaultDetail" aria-labelledby="vault-detail-title">
    <header className="vaultDetailHead">
      <div>
        <h2 id="vault-detail-title" tabIndex={-1} ref={heading} dir="auto">{doc.title}</h2>
        <div className="tagRow">
          <Badge>{categoryLabel(doc.category, categories)}</Badge>
          {doc.access_class === 'private' ? <Badge><Lock aria-hidden="true" />{t('vault.access.private')}</Badge> : <Badge><Users aria-hidden="true" />{t('vault.access.workspace')}</Badge>}
          {doc.expiry_state !== 'none' && <Badge tone={expiryTone(doc.expiry_state)}>{expiryText(doc)}</Badge>}
          {doc.assistant_may_read && <Badge tone="accent">{t('vault.assistant.on')}</Badge>}
        </div>
      </div>
      <button type="button" className="iconButton" aria-label={t('vault.detail.close')} onClick={onClose}><X aria-hidden="true" /></button>
    </header>
    {problem && <Notice error>{problem}</Notice>}
    <div className="actions vaultDetailActions">
      <button type="button" className="secondary" disabled={!doc.file || doc.file.scan_status === 'infected'} onClick={() => void api.download(doc.id, doc.file?.name ?? 'document').catch((e) => setProblem(describeError(e)))}><Download aria-hidden="true" />{t('vault.detail.download')}</button>
      {doc.can_edit && !editing && <button type="button" className="secondary" onClick={() => { setEditing(editDraft(doc)); setProblem(null); }}>{t('vault.detail.edit')}</button>}
      {doc.can_extract && <button type="button" className="secondary" disabled={busy !== null} onClick={() => void run('extract', () => api.extract(doc.id), t('vault.scan.started'))}><ScanText aria-hidden="true" />{busy === 'extract' ? t('vault.scan.reading') : t('vault.scan.read')}</button>}
      {doc.can_edit && <button type="button" className="secondary danger" onClick={() => setConfirmTrash(true)}><Trash2 aria-hidden="true" />{t('vault.detail.trash')}</button>}
    </div>
    {!doc.can_edit && <p className="muted vaultReadOnly"><Lock aria-hidden="true" />{t(doc.access_class === 'private' ? 'vault.detail.sharedReadOnly' : 'vault.detail.readOnly')}</p>}
    <Preview doc={doc} />

    {editing ? <EditForm draft={editing} doc={doc} categories={categories} busy={busy === 'save'} onChange={setEditing} onCancel={() => { setEditing(null); setProblem(null); }} onSubmit={save} />
      : <dl className="vaultFacts">
        <div><dt>{t('vault.field.issue')}</dt><dd>{doc.issue_date ? day(doc.issue_date) : '-'}</dd></div>
        <div><dt>{t('vault.field.expiryDate')}</dt><dd>{doc.expiry_date ? day(doc.expiry_date) : '-'}</dd></div>
        <div><dt>{t('vault.field.issuer')}</dt><dd dir="auto">{doc.issuer ?? '-'}</dd></div>
        <div><dt>{t('vault.field.country')}</dt><dd>{doc.country ?? '-'}</dd></div>
        <div><dt>{t('vault.field.number')}</dt><dd>{doc.number_masked ? <><bdi dir="ltr">{number ?? doc.number_masked}</bdi>
          {doc.is_owner && !number && <button type="button" className="linkButton" onClick={() => void api.revealNumber(doc.id).then((r) => setNumber(r.number), (e) => setProblem(describeError(e)))}>{t('vault.number.show')}</button>}</> : '-'}</dd></div>
        <div><dt>{t('vault.field.tags')}</dt><dd dir="auto">{doc.tags.length ? doc.tags.join(', ') : '-'}</dd></div>
        <div><dt>{t('vault.field.reminder')}</dt><dd>{t('vault.reminder.days', { count: doc.reminder_days ?? meta?.settings.reminder_days ?? 30 })}</dd></div>
        <div><dt>{t('vault.field.retention')}</dt><dd>{doc.retention_until ? day(doc.retention_until) : t('vault.retention.none')}</dd></div>
        <div className="wide"><dt>{t('vault.field.description')}</dt><dd dir="auto">{doc.description || '-'}</dd></div>
        <div className="wide"><dt>{t('vault.field.file')}</dt><dd><bdi dir="auto">{doc.file?.name ?? '-'}</bdi>{doc.file ? ` · ${formatBytes(doc.file.size_bytes)} · ${t('vault.version.current', { number: doc.file.version })}` : ''}</dd></div>
      </dl>}

    {doc.receipt && <section className="vaultSub" aria-labelledby="vault-receipt"><h3 id="vault-receipt">{t('vault.receipt.title')}</h3>
      <dl className="vaultFacts">
        {RECEIPT_FIELDS.map((key) => <div key={key}><dt>{t(`vault.rf.${key}` as MessageKey)}</dt><dd dir="auto">{doc.receipt?.[key] ?? '-'}</dd></div>)}
      </dl>
      {doc.receipt.items.length > 0 && <ul className="vaultItems">{doc.receipt.items.map((item, index) => <li key={index}><span dir="auto">{item.description}</span><span>{item.quantity} × <bdi dir="ltr">{item.amount}</bdi></span></li>)}</ul>}
      <p className="muted">{t('vault.receipt.noPosting')}</p>
    </section>}

    <Extractions doc={doc} />
    <Versions doc={doc} onChanged={() => { void fetch(); onChanged(); }} />
    {(doc.can_share || doc.shared_with.length > 0) && <Sharing doc={doc} onChanged={() => { void fetch(); onChanged(); }} />}
    <Links doc={doc} meta={meta} onChanged={() => void fetch()} />
    {doc.activity.length > 0 && <section className="vaultSub" aria-labelledby="vault-activity"><h3 id="vault-activity">{t('vault.activity.title')}</h3>
      <ul className="vaultActivity">{doc.activity.slice(0, 20).map((event) => <li key={event.id}><span>{activityLabel(event.action)}</span><small><bdi dir="auto">{event.actor}</bdi> · {when(event.created_at)}</small></li>)}</ul>
    </section>}
    {confirmTrash && <ConfirmDialog title={t('vault.trash.confirmTitle', { title: doc.title })} confirmLabel={t('vault.detail.trash')} danger busy={busy === 'trash'}
      onCancel={() => setConfirmTrash(false)} onConfirm={() => void run('trash', async () => { await api.trashDocument(doc.id); setConfirmTrash(false); onClose(); }, t('vault.trash.done', { title: doc.title }))}>
      <p>{t('vault.trash.confirmText', { count: meta?.settings.trash_days ?? 30 })}</p>
    </ConfirmDialog>}
  </section>;
}

function EditForm({ draft, doc, categories, busy, onChange, onCancel, onSubmit }: {
  draft: EditDraft; doc: VaultDocumentDetail; categories: VaultMeta['categories']; busy: boolean; onChange(d: EditDraft): void; onCancel(): void; onSubmit(e: FormEvent): void;
}) {
  const set = <K extends keyof EditDraft>(key: K, value: EditDraft[K]) => onChange({ ...draft, [key]: value });
  const base = useId();
  const field = (key: keyof EditDraft) => `${base}-${key}`;
  return <form className="vaultEdit" aria-label={t('vault.edit.label')} noValidate onSubmit={onSubmit}>
    <label className="field wide" htmlFor={field('title')}><span>{t('vault.field.title')}</span><input id={field('title')} dir="auto" maxLength={200} value={draft.title} onChange={(e) => set('title', e.target.value)} /></label>
    <label className="field" htmlFor={field('category')}><span>{t('vault.field.category')}</span>
      <select id={field('category')} value={draft.category} onChange={(e) => set('category', e.target.value)}>{categories.map((c) => <option key={c.id} value={c.id}>{categoryLabel(c.id, categories)}</option>)}</select></label>
    {doc.is_owner && <label className="field" htmlFor={field('access_class')}><span>{t('vault.field.access')}</span>
      <select id={field('access_class')} value={draft.access_class} onChange={(e) => set('access_class', e.target.value as EditDraft['access_class'])}>
        <option value="private">{t('vault.access.privateLong')}</option><option value="workspace">{t('vault.access.workspaceLong')}</option></select></label>}
    <label className="field" htmlFor={field('issue_date')}><span>{t('vault.field.issue')}</span><input id={field('issue_date')} type="date" value={draft.issue_date} onChange={(e) => set('issue_date', e.target.value)} /></label>
    <label className="field" htmlFor={field('expiry_date')}><span>{t('vault.field.expiryDate')}</span><input id={field('expiry_date')} type="date" value={draft.expiry_date} onChange={(e) => set('expiry_date', e.target.value)} /></label>
    <label className="field" htmlFor={field('issuer')}><span>{t('vault.field.issuer')}</span><input id={field('issuer')} dir="auto" maxLength={200} value={draft.issuer} onChange={(e) => set('issuer', e.target.value)} /></label>
    <label className="field" htmlFor={field('country')}><span>{t('vault.field.country')}</span><input id={field('country')} dir="ltr" maxLength={2} placeholder="SA" value={draft.country} onChange={(e) => set('country', e.target.value)} /></label>
    {doc.is_owner && <label className="field" htmlFor={field('number')}><span>{t('vault.field.numberNew')}</span>
      <input id={field('number')} dir="ltr" autoComplete="off" maxLength={60} placeholder={doc.number_masked ?? ''} value={draft.number} onChange={(e) => set('number', e.target.value)} />
      <small className="muted">{t('vault.field.numberHint')}</small></label>}
    <label className="field" htmlFor={field('reminder_days')}><span>{t('vault.field.reminder')}</span><input id={field('reminder_days')} inputMode="numeric" maxLength={3} value={draft.reminder_days} placeholder="30" onChange={(e) => set('reminder_days', e.target.value)} /></label>
    <label className="field" htmlFor={field('retention_until')}><span>{t('vault.field.retention')}</span><input id={field('retention_until')} type="date" value={draft.retention_until} onChange={(e) => set('retention_until', e.target.value)} /></label>
    <label className="field wide" htmlFor={field('tags')}><span>{t('vault.field.tags')}</span><input id={field('tags')} dir="auto" value={draft.tags} placeholder={t('vault.field.tagsHint')} onChange={(e) => set('tags', e.target.value)} /></label>
    <label className="field wide" htmlFor={field('description')}><span>{t('vault.field.description')}</span><textarea id={field('description')} dir="auto" rows={3} maxLength={2000} value={draft.description} onChange={(e) => set('description', e.target.value)} /></label>
    <label className="checkField wide"><input type="checkbox" checked={draft.assistant_may_read} disabled={draft.access_class === 'private'} onChange={(e) => set('assistant_may_read', e.target.checked)} />
      <span>{t('vault.field.assistant')}<small className="muted">{t(draft.access_class === 'private' ? 'vault.field.assistantPrivate' : 'vault.field.assistantHint')}</small></span></label>
    <div className="actions wide"><button type="submit" disabled={busy}>{busy ? t('vault.saving') : t('vault.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('vault.cancel')}</button></div>
  </form>;
}

function Extractions({ doc }: { doc: VaultDocumentDetail }) {
  if (!doc.extractions.length) return null;
  const latest = doc.extractions[0];
  return <section className="vaultSub" aria-labelledby="vault-scan"><h3 id="vault-scan">{t('vault.scan.title')}</h3>
    <p><Badge tone={latest.status === 'failed' ? 'danger' : latest.status === 'pending' ? 'accent' : latest.status === 'confirmed' ? 'ok' : ''}>{t(`vault.status.${latest.status}` as MessageKey)}</Badge>
      {' '}<small className="muted">{when(latest.created_at)}</small></p>
    {latest.error && <p className="muted" dir="auto">{latest.error}</p>}
    {latest.status === 'pending' && <p className="muted">{t('vault.scan.goReview')}</p>}
  </section>;
}

function Versions({ doc, onChanged }: { doc: VaultDocumentDetail; onChanged(): void }) {
  const [page, setPage] = useState<Load<VaultVersionPage>>({ state: 'loading' });
  const [problem, setProblem] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const load = useCallback(() => api.getVersions(doc.id).then((value) => setPage({ state: 'ready', value }), (e) => setPage({ state: 'error', message: describeError(e) })), [doc.id]);
  useEffect(() => { void load(); }, [load, doc.row_version]);
  const act = async (work: () => Promise<unknown>) => { setProblem(null); try { await work(); onChanged(); } catch (e) { setProblem(describeError(e)); } };
  return <section className="vaultSub" aria-labelledby="vault-versions"><h3 id="vault-versions">{t('vault.version.title')}</h3>
    {doc.can_edit && <><button type="button" className="secondary" onClick={() => picker.current?.click()}><Upload aria-hidden="true" />{t('vault.version.add')}</button>
      <input ref={picker} type="file" hidden aria-label={t('vault.version.add')} onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void act(() => api.uploadVersion(doc.id, file)); }} /></>}
    {problem && <Notice error>{problem}</Notice>}
    {page.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={1} />}
    {page.state === 'error' && <PaneProblem text={page.message} onRetry={() => void load()} />}
    {page.state === 'ready' && (page.value.items.length ? <ul className="vaultVersions">{page.value.items.map((v) => <li key={v.id}>
      <span>{t('vault.version.number', { number: v.version })} · {formatBytes(v.size_bytes)} · {when(v.created_at)}</span>
      <span className="actions">
        <button type="button" className="secondary" disabled={!v.downloadable} onClick={() => void act(() => api.download(doc.id, `${doc.title} v${v.version}`, v.id))}><Download aria-hidden="true" />{t('vault.detail.download')}</button>
        {doc.can_edit && <button type="button" className="secondary" disabled={!v.downloadable} onClick={() => void act(() => api.restoreVersion(doc.id, v.id))}><RotateCcw aria-hidden="true" />{t('vault.version.restore')}</button>}
      </span></li>)}</ul> : <p className="muted">{t('vault.version.none')}</p>)}
  </section>;
}

function Sharing({ doc, onChanged }: { doc: VaultDocumentDetail; onChanged(): void }) {
  const [people, setPeople] = useState<string[] | null>(null);
  const [choice, setChoice] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const selectId = useId();
  useEffect(() => { if (doc.can_share) api.getPeople().then((rows) => setPeople(rows.map((r) => r.user_id)), (e) => setProblem(describeError(e))); }, [doc.can_share]);
  const shared = new Set(doc.shared_with.map((g) => g.user_id));
  const options = (people ?? []).filter((p) => !shared.has(p));
  const act = async (work: () => Promise<unknown>) => { setProblem(null); try { await work(); setChoice(''); onChanged(); } catch (e) { setProblem(describeError(e)); } };
  return <section className="vaultSub" aria-labelledby="vault-sharing"><h3 id="vault-sharing"><Share2 aria-hidden="true" />{t('vault.share.title')}</h3>
    <p className="muted">{t('vault.share.text')}</p>
    {doc.shared_with.length ? <ul className="vaultPeople">{doc.shared_with.map((g) => <li key={g.user_id}><bdi dir="auto">{g.user_id}</bdi>
      {doc.can_share && <button type="button" className="secondary" aria-label={t('vault.share.removeLabel', { user: g.user_id })} onClick={() => void act(() => api.unshare(doc.id, g.user_id))}><X aria-hidden="true" />{t('vault.share.remove')}</button>}</li>)}</ul>
      : <p className="muted">{t('vault.share.none')}</p>}
    {doc.can_share && <form className="vaultInline" onSubmit={(e) => { e.preventDefault(); if (choice) void act(() => api.share(doc.id, choice)); }}>
      <label className="field" htmlFor={selectId}><span>{t('vault.share.person')}</span>
        <select id={selectId} value={choice} onChange={(e) => setChoice(e.target.value)} disabled={!options.length}>
          <option value="">{options.length ? t('vault.share.pick') : t('vault.share.nobody')}</option>
          {options.map((p) => <option key={p} value={p}>{p}</option>)}</select></label>
      <button type="submit" className="secondary" disabled={!choice}>{t('vault.share.add')}</button>
    </form>}
    {problem && <Notice error>{problem}</Notice>}
  </section>;
}

function Links({ doc, meta, onChanged }: { doc: VaultDocumentDetail; meta: VaultMeta | null; onChanged(): void }) {
  const [draft, setDraft] = useState({ target_type: 'vehicle', target_id: '', label: '' });
  const [problem, setProblem] = useState<string | null>(null);
  const ids = { type: useId(), target: useId(), label: useId() };
  const types = meta?.link_types ?? ['vehicle', 'person', 'business', 'purchase'];
  const act = async (work: () => Promise<unknown>) => { setProblem(null); try { await work(); setDraft({ ...draft, target_id: '', label: '' }); onChanged(); } catch (e) { setProblem(describeError(e)); } };
  if (!doc.can_edit && !doc.links.length) return null;
  return <section className="vaultSub" aria-labelledby="vault-links"><h3 id="vault-links"><Link2 aria-hidden="true" />{t('vault.links.title')}</h3>
    {doc.links.length ? <ul className="vaultPeople">{doc.links.map((link) => <li key={link.id}><span>{t(`vault.link.${link.target_type}` as MessageKey)}: <bdi dir="auto">{link.label || link.target_id}</bdi></span>
      {doc.can_edit && <button type="button" className="secondary" aria-label={t('vault.links.removeLabel', { name: link.label || link.target_id })} onClick={() => void act(() => api.removeLink(doc.id, link.id))}><X aria-hidden="true" />{t('vault.share.remove')}</button>}</li>)}</ul>
      : <p className="muted">{t('vault.links.none')}</p>}
    {doc.can_edit && <form className="vaultInline" onSubmit={(e) => { e.preventDefault(); if (draft.target_id.trim()) void act(() => api.addLink(doc.id, draft)); else setProblem(t('vault.links.need')); }}>
      <label className="field" htmlFor={ids.type}><span>{t('vault.links.type')}</span>
        <select id={ids.type} value={draft.target_type} onChange={(e) => setDraft({ ...draft, target_type: e.target.value })}>{types.map((type) => <option key={type} value={type}>{t(`vault.link.${type}` as MessageKey)}</option>)}</select></label>
      <label className="field" htmlFor={ids.target}><span>{t('vault.links.ref')}</span><input id={ids.target} dir="auto" maxLength={160} value={draft.target_id} placeholder={t('vault.links.refHint')} onChange={(e) => setDraft({ ...draft, target_id: e.target.value })} /></label>
      <label className="field" htmlFor={ids.label}><span>{t('vault.links.label')}</span><input id={ids.label} dir="auto" maxLength={200} value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></label>
      <button type="submit" className="secondary"><Plus aria-hidden="true" />{t('vault.links.add')}</button>
    </form>}
    <p className="muted">{t('vault.links.finance')}</p>
    {problem && <Notice error>{problem}</Notice>}
  </section>;
}

// --------------------------------------------------------------------------- adding

function AddDialog({ meta, scan, onClose, onAdded, onOpenExisting }: { meta: VaultMeta; scan?: boolean; onClose(): void; onAdded(doc: VaultDocument, text: string): void; onOpenExisting(id: string): void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState(scan ? 'receipt' : 'other');
  const [access, setAccess] = useState<'private' | 'workspace'>('private');
  const [problem, setProblem] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<{ id: string; inTrash: boolean } | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const ids = { file: useId(), title: useId(), category: useId(), access: useId() };
  const submit = async (event: FormEvent, allowDuplicate = false) => {
    event.preventDefault();
    if (!file) { setProblem(t('vault.add.pick')); return; }
    const refusal = uploadProblem(file, meta);
    if (refusal) { setProblem(refusal); return; }
    setProblem(null); setProgress(0);
    try {
      const doc = await api.upload(file, { title: title.trim() || file.name.replace(/\.[^.]+$/, ''), category, access_class: access, allowDuplicate }, setProgress);
      const reads = meta.categories.find((c) => c.id === category)?.reads;
      const automatic = Boolean(reads && ((reads === 'receipt' && meta.settings.ocr_receipts) || (reads === 'dates' && meta.settings.ocr_identity_dates)));
      if (scan && reads && !automatic) {
        // Scan document reads it now, whatever the automatic switch says; the result still waits for review.
        try { await api.extract(doc.id); onAdded(doc, t('vault.add.doneScan', { title: doc.title })); }
        catch (error) { onAdded(doc, t('vault.add.scanFailed', { title: doc.title, message: describeError(error) })); }
        return;
      }
      onAdded(doc, automatic ? t('vault.add.doneScan', { title: doc.title }) : t('vault.add.done', { title: doc.title }));
    } catch (error) {
      setProgress(null);
      const found = api.duplicateOf(error);
      if (found) { setDuplicate(found); setProblem(describeError(error)); return; }
      setProblem(describeError(error));
    }
  };
  const heading = scan ? t('vault.scanDialog.title') : t('vault.add.title');
  return <Modal title={heading} className="formDialog vaultAddDialog" onClose={onClose}>
    <form noValidate onSubmit={(e) => void submit(e)} aria-label={heading}>
      {scan && <p className="muted">{meta.ocr_connected ? t('vault.scan.text') : t('vault.scan.noOcr')}</p>}
      <label className="field" htmlFor={ids.file}><span>{scan ? t('vault.scan.file') : t('vault.add.file')}</span>
        <input id={ids.file} type="file" accept={scan ? 'image/*,.pdf' : meta.accepted_extensions.join(',')} capture={scan ? 'environment' : undefined} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setDuplicate(null); setProblem(null); }} />
        <small className="muted">{t('vault.add.limits', { size: formatBytes(meta.max_upload_bytes) })}</small></label>
      <label className="field" htmlFor={ids.title}><span>{t('vault.field.title')}</span><input id={ids.title} dir="auto" maxLength={200} value={title} placeholder={file?.name ?? ''} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="field" htmlFor={ids.category}><span>{t('vault.field.category')}</span>
        <select id={ids.category} value={category} onChange={(e) => setCategory(e.target.value)}>{meta.categories.map((c) => <option key={c.id} value={c.id}>{categoryLabel(c.id, meta.categories)}</option>)}</select></label>
      <label className="field" htmlFor={ids.access}><span>{t('vault.field.access')}</span>
        <select id={ids.access} value={access} onChange={(e) => setAccess(e.target.value as 'private' | 'workspace')}>
          <option value="private">{t('vault.access.privateLong')}</option><option value="workspace">{t('vault.access.workspaceLong')}</option></select></label>
      {progress !== null && file && <div className="meterTrack" role="progressbar" aria-label={t('vault.add.progress')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((progress / file.size) * 100)}><span style={{ width: `${Math.min(100, (progress / file.size) * 100)}%` }} /></div>}
      {problem && <Notice error>{problem}</Notice>}
      <div className="actions">
        {duplicate ? <>
          <button type="button" onClick={() => onOpenExisting(duplicate.id)} disabled={duplicate.inTrash}>{t('vault.add.openExisting')}</button>
          <button type="button" className="secondary" onClick={(e) => void submit(e as unknown as FormEvent, true)}>{t('vault.add.anyway')}</button>
        </> : <button type="submit" disabled={progress !== null}><Upload aria-hidden="true" />{progress !== null ? t('vault.add.sending') : scan ? t('vault.scan.submit') : t('vault.add.submit')}</button>}
        <button type="button" className="secondary" onClick={onClose}>{t('vault.cancel')}</button>
      </div>
    </form>
  </Modal>;
}

// --------------------------------------------------------------------------- review

function ReviewTab({ meta, tick, onChanged, onOpen }: { meta: VaultMeta | null; tick: number; onChanged(text?: string): void; onOpen(id: string): void }) {
  const [load, setLoad] = useState<Load<VaultReviewItem[]>>({ state: 'loading' });
  const fetch = useCallback(() => api.getReview().then((value) => setLoad({ state: 'ready', value }), (e) => setLoad({ state: 'error', message: describeError(e) })), []);
  useEffect(() => { void fetch(); }, [fetch, tick]);
  if (load.state === 'loading') return <LoadingSkeleton label={t('vault.loading')} rows={4} />;
  if (load.state !== 'ready') return <PaneProblem text={t('vault.listFailed', { message: load.message })} onRetry={() => void fetch()} />;
  if (!load.value.length) return <Empty icon={<ScanText aria-hidden="true" />} title={t('vault.review.none')} text={meta?.ocr_connected ? t('vault.review.noneText') : t('vault.ocr.notConnected')} />;
  return <div className="vaultReviewList">
    <p className="muted">{t('vault.review.lead')}</p>
    {load.value.map((item) => <ReviewCard key={item.extraction.id} item={item} categories={meta?.categories ?? []} onOpen={onOpen} onDone={(text) => { void fetch(); onChanged(text); }} />)}
  </div>;
}

function ReviewCard({ item, categories, onOpen, onDone }: { item: VaultReviewItem; categories: VaultMeta['categories']; onOpen(id: string): void; onDone(text: string): void }) {
  const { extraction, document } = item;
  const [draft, setDraft] = useState<Record<string, string>>(() => reviewDraft(extraction));
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const base = useId();
  const fields = extraction.kind === 'receipt' ? RECEIPT_FIELDS : DATE_FIELDS;
  const warnings = warningTexts(extraction);
  const decide = async (accept: boolean) => {
    if (accept) {
      const found = reviewProblems(extraction.kind, draft);
      setProblems(found);
      if (Object.keys(found).length) return;
    }
    setBusy(true); setError(null);
    try {
      if (accept) await api.confirmExtraction(extraction.id, draft, extraction.items);
      else await api.rejectExtraction(extraction.id);
      onDone(t(accept ? 'vault.review.confirmed' : 'vault.review.rejected', { title: document.title }));
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  return <article className="surface vaultReviewCard" aria-labelledby={`${base}-t`}>
    <header><h3 id={`${base}-t`} dir="auto">{document.title}</h3><span className="tagRow"><Badge>{categoryLabel(document.category, categories)}</Badge><Badge tone="accent">{t(`vault.kind.${extraction.kind}` as MessageKey)}</Badge></span></header>
    {warnings.map((text) => <p key={text} className="notice warnNotice" role="note"><CircleAlert aria-hidden="true" />{text}</p>)}
    <p className="muted">{t('vault.review.untrusted')}</p>
    <form className="vaultReviewForm" noValidate onSubmit={(e) => { e.preventDefault(); if (document.can_edit) void decide(true); }}>
      {fields.map((key) => {
        const proposed = extraction.fields[key];
        const fid = `${base}-${key}`;
        const hint = proposed ? `${confidenceText(proposed.confidence)}${proposed.source ? ` · ${t('vault.review.from', { line: proposed.source })}` : ''}` : t('vault.review.notRead');
        return <div key={key} className="field"><label htmlFor={fid}>{t(`vault.rf.${key}` as MessageKey)}</label>
          {key === 'currency'
            ? <select id={fid} value={draft[key] ?? ''} disabled={!document.can_edit} aria-describedby={`${fid}-hint`} aria-invalid={Boolean(problems[key]) || undefined} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}>
              <option value="">-</option>{CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}</select>
            : <input id={fid} type={key.endsWith('date') ? 'date' : 'text'} dir={key === 'merchant' ? 'auto' : 'ltr'} inputMode={key === 'total' || key === 'tax' ? 'decimal' : undefined}
              aria-describedby={`${fid}-hint`} value={draft[key] ?? ''} disabled={!document.can_edit} aria-invalid={Boolean(problems[key]) || undefined} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />}
          <small id={`${fid}-hint`} className={problems[key] ? 'fieldError' : 'muted'} dir="auto">{problems[key] ?? hint}</small></div>;
      })}
      {extraction.items.length > 0 && <div className="wide"><h4>{t('vault.review.items')}</h4><ul className="vaultItems">{extraction.items.map((line, index) => <li key={index}><span dir="auto">{line.description}</span><span>{line.quantity} × <bdi dir="ltr">{line.amount}</bdi></span></li>)}</ul></div>}
      {error && <Notice error>{error}</Notice>}
      <div className="actions wide">
        {document.can_edit ? <>
          <button type="submit" disabled={busy}>{t('vault.review.confirm')}</button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void decide(false)}>{t('vault.review.reject')}</button>
        </> : <p className="muted">{t('vault.review.notYours')}</p>}
        <button type="button" className="secondary" onClick={() => onOpen(document.id)}>{t('vault.review.open')}</button>
      </div>
    </form>
  </article>;
}

// --------------------------------------------------------------------------- trash

function TrashTab({ meta, tick, onChanged }: { meta: VaultMeta | null; tick: number; onChanged(text?: string): void }) {
  const [load, setLoad] = useState<Load<VaultDocument[]>>({ state: 'loading' });
  const [confirm, setConfirm] = useState<VaultDocument | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const fetch = useCallback(() => api.listDocuments(filterQuery({ q: '', category: '', access: '', expiry: '' }, 'deleted')).then((page) => setLoad({ state: 'ready', value: page.items }),
    (e) => setLoad({ state: 'error', message: describeError(e) })), []);
  useEffect(() => { void fetch(); }, [fetch, tick]);
  const act = async (work: () => Promise<unknown>, text: string) => { setProblem(null); try { await work(); setConfirm(null); onChanged(text); } catch (e) { setProblem(describeError(e)); } };
  if (load.state === 'loading') return <LoadingSkeleton label={t('vault.loading')} rows={3} />;
  if (load.state !== 'ready') return <PaneProblem text={t('vault.listFailed', { message: load.message })} onRetry={() => void fetch()} />;
  return <section className="surface vaultList" aria-label={t('vault.tab.trash')}>
    <p className="muted">{t('vault.trash.lead', { count: meta?.settings.trash_days ?? 30 })}</p>
    {problem && <Notice error>{problem}</Notice>}
    {!load.value.length ? <Empty icon={<Trash2 aria-hidden="true" />} title={t('vault.trash.empty')} text={t('vault.trash.emptyText')} />
      : <ul className="vaultRows">{load.value.map((doc) => <li key={doc.id} className="vaultTrashRow">
        <span className="vaultRowMain"><strong dir="auto">{doc.title}</strong><small>{doc.purge_after ? t('vault.trash.until', { when: when(doc.purge_after) }) : ''}</small></span>
        {doc.can_edit && <span className="actions">
          <button type="button" className="secondary" aria-label={t('vault.trash.restoreLabel', { title: doc.title })} onClick={() => void act(() => api.restoreDocument(doc.id), t('vault.trash.restored', { title: doc.title }))}><ArchiveRestore aria-hidden="true" />{t('vault.trash.restore')}</button>
          <button type="button" className="secondary danger" aria-label={t('vault.trash.foreverLabel', { title: doc.title })} onClick={() => setConfirm(doc)}><Trash2 aria-hidden="true" />{t('vault.trash.forever')}</button>
        </span>}
      </li>)}</ul>}
    {confirm && <ConfirmDialog title={t('vault.trash.foreverTitle', { title: confirm.title })} confirmLabel={t('vault.trash.forever')} danger
      onCancel={() => setConfirm(null)} onConfirm={() => void act(() => api.deleteForever(confirm.id), t('vault.trash.gone', { title: confirm.title }))}>
      <p>{t('vault.trash.foreverText')}</p></ConfirmDialog>}
  </section>;
}

// --------------------------------------------------------------------------- settings

function SettingsTab({ meta, onChanged }: { meta: VaultMeta | null; onChanged(text?: string): void }) {
  const [form, setForm] = useState(() => meta ? { reminder_days: String(meta.settings.reminder_days), trash_days: String(meta.settings.trash_days), ocr_receipts: meta.settings.ocr_receipts, ocr_identity_dates: meta.settings.ocr_identity_dates } : null);
  const [problem, setProblem] = useState<string | null>(null);
  const [label, setLabel] = useState('');
  const ids = { reminder: useId(), trash: useId(), label: useId() };
  useEffect(() => { if (meta && !form) setForm({ reminder_days: String(meta.settings.reminder_days), trash_days: String(meta.settings.trash_days), ocr_receipts: meta.settings.ocr_receipts, ocr_identity_dates: meta.settings.ocr_identity_dates }); }, [meta, form]);
  if (!meta || !form) return <LoadingSkeleton label={t('vault.loading')} rows={4} />;
  const manage = meta.can_manage;
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const reminder = Number(form.reminder_days), trash = Number(form.trash_days);
    if (!Number.isInteger(reminder) || reminder < 1 || reminder > 365 || !Number.isInteger(trash) || trash < 1 || trash > 365) { setProblem(t('vault.check.days')); return; }
    setProblem(null);
    try { await api.saveSettings({ reminder_days: reminder, trash_days: trash, ocr_receipts: form.ocr_receipts, ocr_identity_dates: form.ocr_identity_dates }); onChanged(t('vault.settings.saved')); }
    catch (e) { setProblem(describeError(e)); }
  };
  return <div className="vaultSettings">
    <form className="surface vaultCard" aria-label={t('vault.settings.title')} noValidate onSubmit={(e) => void save(e)}>
      <h2>{t('vault.settings.title')}</h2>
      {!manage && <p className="notice accessNotice" role="note"><Lock aria-hidden="true" />{t('vault.settings.ownerOnly')}</p>}
      <label className="field" htmlFor={ids.reminder}><span>{t('vault.settings.reminder')}</span><input id={ids.reminder} inputMode="numeric" disabled={!manage} value={form.reminder_days} onChange={(e) => setForm({ ...form, reminder_days: e.target.value })} />
        <small className="muted">{t('vault.settings.reminderHint')}</small></label>
      <label className="field" htmlFor={ids.trash}><span>{t('vault.settings.trash')}</span><input id={ids.trash} inputMode="numeric" disabled={!manage} value={form.trash_days} onChange={(e) => setForm({ ...form, trash_days: e.target.value })} /></label>
      <label className="checkField"><input type="checkbox" disabled={!manage} checked={form.ocr_receipts} onChange={(e) => setForm({ ...form, ocr_receipts: e.target.checked })} /><span>{t('vault.settings.ocrReceipts')}<small className="muted">{t('vault.settings.ocrReceiptsHint')}</small></span></label>
      <label className="checkField"><input type="checkbox" disabled={!manage} checked={form.ocr_identity_dates} onChange={(e) => setForm({ ...form, ocr_identity_dates: e.target.checked })} /><span>{t('vault.settings.ocrDates')}<small className="muted">{t('vault.settings.ocrDatesHint')}</small></span></label>
      <p className={meta.ocr_connected ? 'muted' : 'notice'}>{meta.ocr_connected ? t('vault.ocr.connected') : t('vault.ocr.notConnected')}</p>
      {problem && <Notice error>{problem}</Notice>}
      {manage && <div className="actions"><button type="submit">{t('vault.save')}</button></div>}
    </form>
  </div>;
}

function CategoriesTab({ meta, tick, onChanged }: { meta: VaultMeta | null; tick: number; onChanged(text?: string): void }) {
  const [label, setLabel] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const labelId = useId();
  useEffect(() => {
    // Counts per category from the documents themselves (the first 100, newest first): honest, never estimated beyond that.
    api.listDocuments(filterQuery({ q: '', category: '', access: '', expiry: '' }, 'active', null, 100)).then((page) => {
      const out: Record<string, number> = {};
      for (const doc of page.items) out[doc.category] = (out[doc.category] ?? 0) + 1;
      setCounts(page.next_cursor ? null : out);
    }, () => setCounts(null));
  }, [tick]);
  if (!meta) return <LoadingSkeleton label={t('vault.loading')} rows={4} />;
  const manage = meta.can_manage;
  const custom = meta.categories.filter((c) => !c.built_in);
  const builtIn = meta.categories.filter((c) => c.built_in);
  return <div className="vaultTwo">
    <FinCard title={t('vault.cats.builtIn')} icon={<Tags />}>
      <ul className="vaultCatList">{builtIn.map((c) => <li key={c.id}>
        <span><strong>{categoryLabel(c.id, meta.categories)}</strong>
          <small className="muted">{c.identity ? t('vault.cats.identity') : c.reads === 'receipt' ? t('vault.cats.readsReceipt') : c.reads === 'dates' ? t('vault.cats.readsDates') : t('vault.cats.plain')}</small></span>
        {counts && <StatusChip tone="muted">{t('vault.cats.count', { count: counts[c.id] ?? 0 })}</StatusChip>}
      </li>)}</ul>
    </FinCard>
    <FinCard title={t('vault.cats.title')} icon={<Tags />}>
      <p className="muted">{t('vault.cats.text')}</p>
      {!manage && <p className="notice accessNotice" role="note"><Lock aria-hidden="true" />{t('vault.settings.ownerOnly')}</p>}
      {custom.length ? <ul className="vaultPeople">{custom.map((c) => <li key={c.id}><bdi dir="auto">{c.label}</bdi>
        {counts && <StatusChip tone="muted">{t('vault.cats.count', { count: counts[c.id] ?? 0 })}</StatusChip>}
        {manage && <button type="button" className="secondary" aria-label={t('vault.cats.removeLabel', { name: c.label ?? '' })} onClick={() => void api.deleteCategory(c.id).then(() => onChanged(t('vault.cats.removed')), (e) => setProblem(describeError(e)))}><X aria-hidden="true" />{t('vault.share.remove')}</button>}</li>)}</ul>
        : <p className="muted">{t('vault.cats.none')}</p>}
      {manage && <form className="vaultInline" onSubmit={(e) => { e.preventDefault(); if (label.trim()) void api.addCategory(label.trim()).then(() => { setLabel(''); onChanged(t('vault.cats.added')); }, (err) => setProblem(describeError(err))); }}>
        <label className="field" htmlFor={labelId}><span>{t('vault.cats.name')}</span><input id={labelId} dir="auto" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <button type="submit" className="secondary" disabled={!label.trim()}><Plus aria-hidden="true" />{t('vault.cats.add')}</button>
      </form>}
      {problem && <Notice error>{problem}</Notice>}
    </FinCard>
  </div>;
}

function AccessTab({ meta }: { meta: VaultMeta | null }) {
  if (!meta) return <LoadingSkeleton label={t('vault.loading')} rows={4} />;
  const rows: [MessageKey, MessageKey, MessageKey, MessageKey][] = [
    ['vault.who.seeWorkspace', 'vault.who.no', 'vault.who.yes', 'vault.who.yes'],
    ['vault.who.seePrivate', 'vault.who.sharedOnly', 'vault.who.sharedOnly', 'vault.who.sharedOnly'],
    ['vault.who.add', 'vault.who.no', 'vault.who.yes', 'vault.who.yes'],
    ['vault.who.change', 'vault.who.no', 'vault.who.own', 'vault.who.ownAndWorkspace'],
    ['vault.who.share', 'vault.who.no', 'vault.who.adder', 'vault.who.adder'],
    ['vault.who.settings', 'vault.who.no', 'vault.who.no', 'vault.who.yes'],
  ];
  return <div className="vaultTwo">
    <div className="vaultStack">
      <FinCard title={t('vault.who.title')} icon={<Users />} subtitle={meta.can_manage ? t('vault.who.youOwner') : meta.can_write ? t('vault.who.youMember') : t('vault.who.youViewer')}>
        <div className="finTableWrap"><table className="finGrid">
          <caption className="visuallyHidden">{t('vault.who.title')}</caption>
          <thead><tr><th scope="col">{t('vault.who.action')}</th><th scope="col">{t('vault.who.viewer')}</th><th scope="col">{t('vault.who.member')}</th><th scope="col">{t('vault.who.owner')}</th></tr></thead>
          <tbody>{rows.map(([label, ...cells]) => <tr key={label}><th scope="row">{t(label)}</th>{cells.map((c, i) => <td key={i}>{t(c)}</td>)}</tr>)}</tbody>
        </table></div>
      </FinCard>
      <SecurityStrip meta={meta} />
    </div>
    {meta.can_write ? <Devices /> : <FinCard title={t('vault.devices.title')} icon={<Smartphone />}><p className="muted">{t('vault.devices.viewer')}</p></FinCard>}
  </div>;
}

function Devices() {
  const [load, setLoad] = useState<Load<VaultDevice[]>>({ state: 'loading' });
  const [created, setCreated] = useState<VaultDeviceCreated | null>(null);
  const [name, setName] = useState('iPhone');
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const nameId = useId();
  const fetch = useCallback(() => api.getDevices().then((value) => setLoad({ state: 'ready', value }), (e) => setLoad({ state: 'error', message: describeError(e) })), []);
  useEffect(() => { void fetch(); }, [fetch]);
  const create = async (event: FormEvent) => {
    event.preventDefault(); setProblem(null); setCopied(false);
    try { setCreated(await api.createDevice(name.trim() || 'iPhone')); await fetch(); } catch (e) { setProblem(describeError(e)); }
  };
  return <section className="surface vaultCard vaultDevices" aria-labelledby="vault-devices"><h2 id="vault-devices"><Smartphone aria-hidden="true" />{t('vault.devices.title')}</h2>
    <p className="muted">{t('vault.devices.text')}</p>
    {created && <div className="notice vaultToken" role="status">
      <p>{t('vault.devices.once')}</p>
      <code dir="ltr">{created.token}</code>
      <div className="actions"><button type="button" className="secondary" onClick={() => void copyText(created.token).then(() => setCopied(true))}><Copy aria-hidden="true" />{copied ? t('vault.devices.copied') : t('vault.devices.copy')}</button>
        <button type="button" className="secondary" onClick={() => setCreated(null)}>{t('vault.devices.hide')}</button></div>
      <ol className="vaultSteps"><li>{t('vault.devices.step1')}</li><li>{t('vault.devices.step2', { url: created.upload_url })}</li><li>{t('vault.devices.step3')}</li><li>{t('vault.devices.step4')}</li></ol>
    </div>}
    <form className="vaultInline" onSubmit={(e) => void create(e)}>
      <label className="field" htmlFor={nameId}><span>{t('vault.devices.name')}</span><input id={nameId} dir="auto" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></label>
      <button type="submit" className="secondary"><Plus aria-hidden="true" />{t('vault.devices.create')}</button>
    </form>
    {problem && <Notice error>{problem}</Notice>}
    {load.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={1} />}
    {load.state === 'error' && <PaneProblem text={load.message} onRetry={() => void fetch()} />}
    {load.state === 'ready' && (load.value.length ? <ul className="vaultPeople">{load.value.map((d) => <li key={d.id}>
      <span><bdi dir="auto">{d.name}</bdi> <small className="muted">· {t(d.status === 'active' ? 'vault.devices.active' : 'vault.devices.revoked')} · {d.last_seen_at ? t('vault.devices.seen', { when: when(d.last_seen_at) }) : t('vault.devices.never')} · {t('vault.devices.count', { count: d.upload_count })}{d.last_error ? ` · ${t('vault.devices.error', { code: d.last_error })}` : ''}</small></span>
      {d.status === 'active' && <button type="button" className="secondary" aria-label={t('vault.devices.revokeLabel', { name: d.name })} onClick={() => void api.revokeDevice(d.id).then(fetch, (e) => setProblem(describeError(e)))}><X aria-hidden="true" />{t('vault.devices.revoke')}</button>}
    </li>)}</ul> : <p className="muted">{t('vault.devices.none')}</p>)}
    <p className="muted"><RefreshCw aria-hidden="true" className="inlineIcon" />{t('vault.devices.interim')}</p>
  </section>;
}
