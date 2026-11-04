// The Document vault (docs/documents.md#the-web-page): identity papers, contracts, insurance and
// receipts on top of Files storage. Tabs: Overview, Documents (search, filters, list, detail with
// preview, versions, sharing, links, scanning and activity), To review (scan results waiting for a
// person), Expiring, Trash and Settings (reminders, scanning switches, categories, iPhone Shortcut).
import { lazy, Suspense, useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  ArchiveRestore, CalendarClock, CircleAlert, CircleDot, Copy, Download, Eye, FileText, Link2, Lock, Plus, RefreshCw, RotateCcw, ScanText,
  Search, Share2, ShieldCheck, Smartphone, Trash2, Upload, Users, X,
} from 'lucide-react';
import { ConfirmDialog, Modal } from './Dialog';
import { LoadingSkeleton, PageHeader, PageSummary, PaneProblem } from './PageParts';
import { copyText } from './lib/clipboard';
import { describeError, isPermissionDenied } from './lib/errors';
import { formatBytes } from './lib/files';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { figure, type Load } from './lib/pageSummary';
import { viewLabel } from './lib/shell';
import {
  CURRENCIES, DATE_FIELDS, RECEIPT_FIELDS, VAULT_TABS, activityLabel, categoryLabel, confidenceText, editDraft, editPatch, expiryText, expiryTone,
  filterQuery, hashOf, mergeDocuments, reviewDraft, reviewProblems, tabFromHash, uploadProblem, warningTexts,
  type DocumentExtraction, type EditDraft, type VaultDevice, type VaultDeviceCreated, type VaultDocument, type VaultDocumentDetail, type VaultMeta,
  type VaultReviewItem, type VaultTab, type VaultVersionPage,
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
  const [focus, setFocus] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [tick, setTick] = useState(0);

  const loadMeta = useCallback(() => api.getMeta().then((value) => setMeta({ state: 'ready', value }), (error) => setMeta(
    isPermissionDenied(error) ? { state: 'denied', message: describeError(error) } : { state: 'error', message: describeError(error) })), []);
  useEffect(() => { void loadMeta(); }, [loadMeta, tick]);
  useEffect(() => {
    const onHash = () => { if (location.hash.startsWith('#vault')) setTab(tabFromHash(location.hash)); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const go = (next: VaultTab) => { setTab(next); setFocus(null); history.replaceState(null, '', hashOf(next)); };
  const changed = (text?: string) => { if (text) setFlash({ text }); setTick((n) => n + 1); };
  const value = meta.state === 'ready' ? meta.value : null;
  const counts = value?.summary;

  if (meta.state === 'denied') {
    return <>
      <PageHeader title={viewLabel('vault')} subtitle={t('vault.intro')} />
      <section className="surface adminForbidden" aria-label={t('vault.denied.title')}>
        <span className="adminIcon"><Lock aria-hidden="true" /></span>
        <div><h2>{t('vault.denied.title')}</h2><p>{t('vault.denied.text')}</p><p className="muted">{t('vault.apiSaid', { message: meta.message })}</p></div>
      </section>
    </>;
  }

  const figures = [
    figure('active', t('vault.fig.documents'), meta, () => ({ value: formatNumber(counts!.active), hint: t('vault.fig.privateHint', { count: counts!.private }) })),
    figure('expiring', t('vault.fig.expiring'), meta, () => ({ value: formatNumber(counts!.expiring), hint: t('vault.fig.expiringHint', { days: value!.reminder_window_days }), tone: counts!.expiring ? 'warn' : '' })),
    figure('expired', t('vault.fig.expired'), meta, () => ({ value: formatNumber(counts!.expired), hint: t('vault.fig.expiredHint'), tone: counts!.expired ? 'danger' : '' })),
    figure('review', t('vault.fig.review'), meta, () => ({ value: formatNumber(counts!.review), hint: value!.ocr_connected ? t('vault.fig.ocrOn') : t('vault.fig.ocrOff'), tone: counts!.review ? 'accent' : '' })),
  ];
  const count = (key: VaultTab) => (key === 'review' ? counts?.review : key === 'expiring' ? (counts ? counts.expiring + counts.expired : undefined) : key === 'trash' ? counts?.trash : undefined);

  return <>
    <PageHeader title={viewLabel('vault')} subtitle={t('vault.intro')} actions={value?.can_write
      ? <button type="button" className="uploadButton" onClick={() => setAdding(true)}><Upload aria-hidden="true" />{t('vault.add')}</button> : undefined} />
    {meta.state === 'error' && <PaneProblem text={t('vault.metaFailed', { message: meta.message })} onRetry={() => { setMeta({ state: 'loading' }); void loadMeta(); }} />}
    {flash && <Notice error={flash.error}>{flash.text}</Notice>}
    <PageSummary label={t('vault.summary')} figures={figures} onRetry={() => { setMeta({ state: 'loading' }); void loadMeta(); }} />
    <div className="segmented vaultTabs" role="tablist" aria-label={t('vault.tabs')}>
      {VAULT_TABS.map((key) => <button key={key} type="button" role="tab" id={`vault-tab-${key}`} aria-selected={tab === key} aria-controls="vault-panel"
        onClick={() => go(key)}>{t(`vault.tab.${key}` as MessageKey)}{count(key) ? <span className="vaultCount">{formatNumber(count(key)!)}</span> : null}</button>)}
    </div>
    <div id="vault-panel" role="tabpanel" aria-labelledby={`vault-tab-${tab}`} className="vaultPanel">
      {tab === 'overview' && <Overview meta={value} tick={tick} onOpen={(id) => { setTab('documents'); setFocus(id); history.replaceState(null, '', hashOf('documents')); }} onGo={go} onAdd={() => setAdding(true)} />}
      {tab === 'documents' && <DocumentsTab meta={value} tick={tick} focus={focus} onFocus={setFocus} onChanged={changed} onAdd={() => setAdding(true)} status="active" />}
      {tab === 'expiring' && <DocumentsTab meta={value} tick={tick} focus={focus} onFocus={setFocus} onChanged={changed} onAdd={() => setAdding(true)} status="active" expiringOnly />}
      {tab === 'trash' && <TrashTab meta={value} tick={tick} onChanged={changed} />}
      {tab === 'review' && <ReviewTab meta={value} tick={tick} onChanged={changed} onOpen={(id) => { setTab('documents'); setFocus(id); history.replaceState(null, '', hashOf('documents')); }} />}
      {tab === 'settings' && <SettingsTab meta={value} onChanged={changed} />}
    </div>
    {adding && value && <AddDialog meta={value} onClose={() => setAdding(false)} onAdded={(doc, text) => {
      setAdding(false); changed(text); setTab('documents'); setFocus(doc.id); history.replaceState(null, '', hashOf('documents'));
    }} onOpenExisting={(id) => { setAdding(false); setTab('documents'); setFocus(id); history.replaceState(null, '', hashOf('documents')); }} />}
  </>;
}

// --------------------------------------------------------------------------- overview

function Overview({ meta, tick, onOpen, onGo, onAdd }: { meta: VaultMeta | null; tick: number; onOpen(id: string): void; onGo(tab: VaultTab): void; onAdd(): void }) {
  const [soon, setSoon] = useState<Load<VaultDocument[]>>({ state: 'loading' });
  const [review, setReview] = useState<Load<VaultReviewItem[]>>({ state: 'loading' });
  const load = useCallback(() => {
    api.listDocuments(filterQuery({ q: '', category: '', access: '', expiry: 'soon' })).then((page) => setSoon({ state: 'ready', value: page.items.slice(0, 5) }),
      (error) => setSoon({ state: 'error', message: describeError(error) }));
    api.getReview().then((items) => setReview({ state: 'ready', value: items.slice(0, 5) }), (error) => setReview({ state: 'error', message: describeError(error) }));
  }, []);
  useEffect(() => { load(); }, [load, tick]);
  if (meta && meta.summary.active === 0 && meta.summary.trash === 0) {
    return <Empty icon={<ShieldCheck aria-hidden="true" />} title={t('vault.empty.title')} text={t('vault.empty.text')}
      action={meta.can_write ? <div className="actions"><button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('vault.add')}</button>
        <button type="button" className="secondary" onClick={() => onGo('settings')}><Smartphone aria-hidden="true" />{t('vault.empty.phone')}</button></div> : undefined} />;
  }
  return <div className="vaultOverview">
    <section className="surface vaultCard" aria-labelledby="vault-soon-title">
      <h2 id="vault-soon-title"><CalendarClock aria-hidden="true" />{t('vault.overview.soon')}</h2>
      {soon.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={2} />}
      {soon.state === 'error' && <PaneProblem text={t('vault.listFailed', { message: soon.message })} onRetry={load} />}
      {soon.state === 'ready' && (soon.value.length ? <ul className="vaultMiniList">{soon.value.map((doc) => <li key={doc.id}>
        <button type="button" className="linkButton" onClick={() => onOpen(doc.id)} dir="auto">{doc.title}</button>
        <Badge tone={expiryTone(doc.expiry_state)}>{expiryText(doc)}</Badge></li>)}</ul> : <p className="muted">{t('vault.overview.noneSoon')}</p>)}
      <button type="button" className="secondary" onClick={() => onGo('expiring')}>{t('vault.overview.allExpiring')}</button>
    </section>
    <section className="surface vaultCard" aria-labelledby="vault-review-title">
      <h2 id="vault-review-title"><ScanText aria-hidden="true" />{t('vault.overview.review')}</h2>
      {review.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={2} />}
      {review.state === 'error' && <PaneProblem text={t('vault.listFailed', { message: review.message })} onRetry={load} />}
      {review.state === 'ready' && (review.value.length ? <ul className="vaultMiniList">{review.value.map((item) => <li key={item.extraction.id}>
        <button type="button" className="linkButton" onClick={() => onGo('review')} dir="auto">{item.document.title}</button>
        <Badge>{t(`vault.kind.${item.extraction.kind}` as MessageKey)}</Badge></li>)}</ul> : <p className="muted">{t('vault.overview.noneReview')}</p>)}
      {meta && !meta.ocr_connected && <p className="muted">{t('vault.ocr.notConnected')}</p>}
    </section>
  </div>;
}

// --------------------------------------------------------------------------- documents list and detail

function DocumentsTab({ meta, tick, focus, onFocus, onChanged, onAdd, status, expiringOnly }: {
  meta: VaultMeta | null; tick: number; focus: string | null; onFocus(id: string | null): void; onChanged(text?: string): void; onAdd(): void;
  status: 'active'; expiringOnly?: boolean;
}) {
  const [filters, setFilters] = useState({ q: '', category: '', access: '', expiry: expiringOnly ? 'soon' : '' });
  const [draft, setDraft] = useState(filters);
  const [items, setItems] = useState<VaultDocument[]>([]);
  const [load, setLoad] = useState<Load<null>>({ state: 'loading' });
  const [cursor, setCursor] = useState<string | null>(null);
  const [more, setMore] = useState<'idle' | 'loading' | { error: string }>('idle');
  const request = useRef(0);
  const ids = { q: useId(), category: useId(), access: useId(), expiry: useId() };

  const fetchFirst = useCallback(async (next: typeof filters) => {
    const token = ++request.current;
    setLoad((current) => (current.state === 'ready' ? current : { state: 'loading' }));
    try {
      const page = await api.listDocuments(filterQuery(next, status));
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
  }, [status, expiringOnly]);
  useEffect(() => { void fetchFirst(filters); }, [fetchFirst, filters, tick]);

  const loadMore = async () => {
    setMore('loading');
    try {
      const page = await api.listDocuments(filterQuery(filters, status, cursor));
      setItems((current) => mergeDocuments(current, page.items)); setCursor(page.next_cursor); setMore('idle');
    } catch (error) { setMore({ error: describeError(error) }); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); setFilters(draft); };
  const clear = () => { const empty = { q: '', category: '', access: '', expiry: expiringOnly ? 'soon' : '' }; setDraft(empty); setFilters(empty); };
  const filtered = Boolean(filters.q || filters.category || filters.access || (!expiringOnly && filters.expiry));
  const categories = meta?.categories ?? [];

  return <div className={`vaultSplit ${focus ? 'hasDetail' : ''}`}>
    <section className="surface vaultList" aria-label={t(expiringOnly ? 'vault.list.expiringLabel' : 'vault.list.label')}>
      <form className="vaultFilters" role="search" onSubmit={submit}>
        <label className="field vaultSearch" htmlFor={ids.q}><span>{t('vault.filter.search')}</span>
          <input id={ids.q} type="search" dir="auto" value={draft.q} maxLength={120} placeholder={t('vault.filter.searchHint')} onChange={(e) => setDraft({ ...draft, q: e.target.value })} /></label>
        <label className="field" htmlFor={ids.category}><span>{t('vault.field.category')}</span>
          <select id={ids.category} value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
            <option value="">{t('vault.filter.anyCategory')}</option>
            {categories.map((c) => <option key={c.id} value={c.id}>{categoryLabel(c.id, categories)}</option>)}
          </select></label>
        <label className="field" htmlFor={ids.access}><span>{t('vault.field.access')}</span>
          <select id={ids.access} value={draft.access} onChange={(e) => setDraft({ ...draft, access: e.target.value })}>
            <option value="">{t('vault.filter.anyAccess')}</option>
            <option value="private">{t('vault.access.private')}</option><option value="workspace">{t('vault.access.workspace')}</option>
          </select></label>
        {!expiringOnly && <label className="field" htmlFor={ids.expiry}><span>{t('vault.field.expiry')}</span>
          <select id={ids.expiry} value={draft.expiry} onChange={(e) => setDraft({ ...draft, expiry: e.target.value })}>
            <option value="">{t('vault.filter.anyExpiry')}</option><option value="soon">{t('vault.filter.soon')}</option><option value="expired">{t('vault.filter.expired')}</option>
          </select></label>}
        <div className="actions">
          <button type="submit" className="secondary"><Search aria-hidden="true" />{t('vault.filter.find')}</button>
          {filtered && <button type="button" className="secondary" onClick={clear}><RotateCcw aria-hidden="true" />{t('vault.filter.clear')}</button>}
        </div>
      </form>
      {load.state === 'loading' && <LoadingSkeleton label={t('vault.loading')} rows={4} />}
      {load.state === 'error' && <PaneProblem text={t('vault.listFailed', { message: load.message })} onRetry={() => void fetchFirst(filters)} />}
      {load.state === 'ready' && !items.length && (filtered
        ? <Empty icon={<Search aria-hidden="true" />} title={t('vault.list.noMatch')} text={t('vault.list.noMatchText')} action={<button type="button" className="secondary" onClick={clear}>{t('vault.filter.clear')}</button>} />
        : expiringOnly
          ? <Empty icon={<CalendarClock aria-hidden="true" />} title={t('vault.expiring.none')} text={t('vault.expiring.noneText')} />
          : <Empty icon={<FileText aria-hidden="true" />} title={t('vault.list.empty')} text={t('vault.list.emptyText')} action={meta?.can_write ? <button type="button" onClick={onAdd}><Plus aria-hidden="true" />{t('vault.add')}</button> : undefined} />)}
      {load.state === 'ready' && items.length > 0 && <ul className="vaultRows">
        {items.map((doc) => <li key={doc.id}><button type="button" className={`vaultRow ${focus === doc.id ? 'selected' : ''}`} aria-current={focus === doc.id || undefined} onClick={() => onFocus(doc.id)}>
          <span className="vaultRowMain"><strong dir="auto">{doc.title}</strong>
            <small>{categoryLabel(doc.category, categories)}{doc.number_masked ? <> · <bdi dir="ltr">{doc.number_masked}</bdi></> : null}</small></span>
          <span className="vaultRowTags">
            {doc.access_class === 'private' ? <Badge><Lock aria-hidden="true" />{t('vault.access.private')}</Badge> : <Badge><Users aria-hidden="true" />{t('vault.access.workspace')}</Badge>}
            {doc.expiry_state !== 'none' && <Badge tone={expiryTone(doc.expiry_state)}>{expiryText(doc)}</Badge>}
          </span>
        </button></li>)}
      </ul>}
      {cursor && load.state === 'ready' && <div className="actions vaultMore"><button type="button" className="secondary" disabled={more === 'loading'} onClick={() => void loadMore()}>{more === 'loading' ? t('vault.loading') : t('vault.list.more')}</button></div>}
      {typeof more === 'object' && <Notice error>{more.error}</Notice>}
    </section>
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

function AddDialog({ meta, onClose, onAdded, onOpenExisting }: { meta: VaultMeta; onClose(): void; onAdded(doc: VaultDocument, text: string): void; onOpenExisting(id: string): void }) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('other');
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
      onAdded(doc, reads && ((reads === 'receipt' && meta.settings.ocr_receipts) || (reads === 'dates' && meta.settings.ocr_identity_dates)) ? t('vault.add.doneScan', { title: doc.title }) : t('vault.add.done', { title: doc.title }));
    } catch (error) {
      setProgress(null);
      const found = api.duplicateOf(error);
      if (found) { setDuplicate(found); setProblem(describeError(error)); return; }
      setProblem(describeError(error));
    }
  };
  return <Modal title={t('vault.add.title')} className="formDialog vaultAddDialog" onClose={onClose}>
    <form noValidate onSubmit={(e) => void submit(e)} aria-label={t('vault.add.title')}>
      <label className="field" htmlFor={ids.file}><span>{t('vault.add.file')}</span>
        <input id={ids.file} type="file" accept={meta.accepted_extensions.join(',')} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setDuplicate(null); setProblem(null); }} />
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
        </> : <button type="submit" disabled={progress !== null}><Upload aria-hidden="true" />{progress !== null ? t('vault.add.sending') : t('vault.add.submit')}</button>}
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
  const custom = meta.categories.filter((c) => !c.built_in);
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
    <section className="surface vaultCard" aria-labelledby="vault-cats"><h2 id="vault-cats">{t('vault.cats.title')}</h2>
      <p className="muted">{t('vault.cats.text')}</p>
      {custom.length ? <ul className="vaultPeople">{custom.map((c) => <li key={c.id}><bdi dir="auto">{c.label}</bdi>
        {manage && <button type="button" className="secondary" aria-label={t('vault.cats.removeLabel', { name: c.label ?? '' })} onClick={() => void api.deleteCategory(c.id).then(() => onChanged(t('vault.cats.removed')), (e) => setProblem(describeError(e)))}><X aria-hidden="true" />{t('vault.share.remove')}</button>}</li>)}</ul>
        : <p className="muted">{t('vault.cats.none')}</p>}
      {manage && <form className="vaultInline" onSubmit={(e) => { e.preventDefault(); if (label.trim()) void api.addCategory(label.trim()).then(() => { setLabel(''); onChanged(t('vault.cats.added')); }, (err) => setProblem(describeError(err))); }}>
        <label className="field" htmlFor={ids.label}><span>{t('vault.cats.name')}</span><input id={ids.label} dir="auto" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <button type="submit" className="secondary" disabled={!label.trim()}><Plus aria-hidden="true" />{t('vault.cats.add')}</button>
      </form>}
    </section>
    {meta.can_write && <Devices />}
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
