// Knowledge base (docs/knowledge-base.md): collections of documents and notes, keyword plus
// semantic search, "Ask the knowledge base" with numbered, provenance-labelled citations,
// index state per item and re-index, and per-collection visibility (workspace or private).
// The page leads with the index summary; a collection opens straight onto its items table and
// the create and add forms open in dialogs.
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { BookOpen, CircleDot, FilePlus2, FileText, Library, Link2, Lock, NotebookPen, Pencil, Plus, RefreshCw, Search, Sparkles, Trash2, Upload, Users } from 'lucide-react';
import type { KnowledgeAnswer, KnowledgeCollection, KnowledgeItem, KnowledgeItemKind, KnowledgeItemPage, KnowledgeSearchResult, Visibility } from '@anum/contracts';
import { ConfirmDialog, Drawer, Modal } from './Dialog';
import { StarButton, useStars } from './Favorites';
import { getFiles } from './lib/api';
import { ApiError, describeError, isBudgetExceeded, isPermissionDenied, budgetMessage } from './lib/errors';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { ITEMS_PAGE, citationPlace, fileSize, parseTags } from './lib/library';
import * as lib from './lib/libraryApi';
import { KnowledgeSummary, useRetrievalStatus } from './MemoryView';
import { DialogButton, SPLIT_QUERY, SplitView, useMediaQuery } from './PageParts';
import './library.css';

type Flash = { text: string; error?: boolean } | null;
type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'forbidden'; detail: string } | { kind: 'error'; detail: string };

function Notice({ flash }: { flash: Flash }) {
  if (!flash) return null;
  return <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>;
}

const failure = (e: unknown, denied: MessageKey): string => isPermissionDenied(e) ? t(denied) : isBudgetExceeded(e) ? budgetMessage(e) : describeError(e);

function IndexBadge({ item }: { item: KnowledgeItem }) {
  if (!item.available) return <span className="statusBadge dangerTone">{t('kb.index.gone')}</span>;
  const tone = item.index.state === 'indexed' ? 'success' : item.index.state === 'failed' ? 'dangerTone' : item.index.state === 'skipped' ? 'neutral' : 'warning';
  return <span className={`statusBadge ${tone}`} title={item.index.error ?? undefined}>{t(`kb.index.${item.index.state}` as MessageKey)}</span>;
}

export function KnowledgeView({ initialCollectionId }: { initialCollectionId?: string | null }) {
  const [collections, setCollections] = useState<KnowledgeCollection[]>([]);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [selected, setSelected] = useState<string | null>(initialCollectionId ?? null);
  const [flash, setFlash] = useState<Flash>(null);
  const stars = useStars('collection');
  const retrieval = useRetrievalStatus();
  const wide = useMediaQuery(SPLIT_QUERY);

  const fetchCollections = useCallback(async () => {
    setLoad({ kind: 'loading' });
    try {
      const rows = await lib.listCollections();
      setCollections(rows);
      setLoad({ kind: 'ready' });
    } catch (e) { setLoad(isPermissionDenied(e) ? { kind: 'forbidden', detail: describeError(e) } : { kind: 'error', detail: describeError(e) }); }
  }, []);
  useEffect(() => { void fetchCollections(); }, [fetchCollections]);
  // A collection opens straight onto its items: the one linked to, else the first on a wide screen.
  useEffect(() => { if (wide && load.kind === 'ready' && !selected && collections[0]) setSelected(collections[0].id); }, [wide, load.kind, selected, collections]);

  const choose = (id: string | null) => { setSelected(id); history.replaceState(null, '', id ? `#knowledge/${encodeURIComponent(id)}` : '#knowledge'); };
  const current = collections.find((c) => c.id === selected) ?? null;
  const missing = load.kind === 'ready' && initialCollectionId && selected === initialCollectionId && !current;
  const created = (c: KnowledgeCollection) => { setCollections((rows) => [...rows, c].sort((a, b) => a.name.localeCompare(b.name))); choose(c.id); setFlash({ text: t('kb.created', { name: c.name }) }); };

  return <>
    <KnowledgeSummary status={retrieval.status} onRetry={retrieval.reload} />
    <Notice flash={flash} />
    {stars.error && <Notice flash={{ text: stars.error, error: true }} />}
    {load.kind === 'loading' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{t('kb.loading')}</p>}
    {load.kind === 'forbidden' && <div className="surface emptyState" role="alert"><Lock aria-hidden="true" /><h3>{t('pages.kb.deniedTitle')}</h3><p>{t('kb.forbidden', { message: load.detail })}</p></div>}
    {load.kind === 'error' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('kb.loadFailed', { message: load.detail })}</span><button type="button" className="secondary" onClick={() => void fetchCollections()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {load.kind === 'ready' && collections.length === 0 && <section className="surface kbStart" aria-labelledby="kb-start">
      <div className="kbStartText"><Library aria-hidden="true" /><h3 id="kb-start">{t('kb.emptyTitle')}</h3><p>{t('kb.emptyText')}</p></div>
      <CreateCollection onCreated={created} />
    </section>}
    {load.kind === 'ready' && collections.length > 0 && <>
      <AskPanel collections={collections} selected={current} action={<DialogButton label={t('kb.newCollection')} icon={<Plus aria-hidden="true" />} className="secondary">{(close) => <CreateCollection onCreated={(c) => { close(); created(c); }} onCancel={close} />}</DialogButton>} />
      <SplitView className="kbSplit" show={current ? 'detail' : 'list'}>
        <aside data-pane="list" className="pane kbCollections" aria-labelledby="kb-collections">
          <div className="paneHead"><h2 id="kb-collections">{t('kb.collections')} <span className="muted">{formatNumber(collections.length)}</span></h2></div>
          <ul className="kbCollectionList">{collections.map((c) => <li key={c.id}>
            <button type="button" className={`kbCollectionButton ${c.id === selected ? 'active' : ''}`} aria-current={c.id === selected ? 'true' : undefined} onClick={() => choose(c.id)}>
              <span dir="auto">{c.name}</span>
              <small>{c.visibility === 'private' ? <Lock aria-label={t('library.private')} /> : <Users aria-label={t('library.workspace')} />}{t('kb.itemCount', { n: c.item_count })}</small>
            </button>
          </li>)}</ul>
        </aside>
        <section data-pane="detail" className="pane kbDetail" aria-label={current ? undefined : t('kb.collections')}>
          {missing && <div className="paneBody"><p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('kb.collectionMissing')}</p></div>}
          {!current && !missing && <div className="paneEmpty"><BookOpen aria-hidden="true" /><h3>{t('pages.kb.pickTitle')}</h3><p>{t('pages.kb.pickText')}</p></div>}
          {current && <CollectionDetail key={current.id} collection={current} starred={stars.ids.has(current.id)} onStar={() => void stars.toggle(current.id)}
            onBack={() => choose(null)}
            onChanged={(c) => setCollections((rows) => rows.map((row) => row.id === c.id ? c : row))}
            onCountChange={(delta) => { setCollections((rows) => rows.map((row) => row.id === current.id ? { ...row, item_count: Math.max(0, row.item_count + delta) } : row)); retrieval.reload(); }}
            onDeleted={() => { setFlash({ text: t('kb.deleted', { name: current.name }) }); setCollections((rows) => rows.filter((row) => row.id !== current.id)); choose(collections.find((c) => c.id !== current.id)?.id ?? null); retrieval.reload(); }} />}
        </section>
      </SplitView>
    </>}
  </>;
}

function CreateCollection({ onCreated, onCancel }: { onCreated: (c: KnowledgeCollection) => void; onCancel?: () => void }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<Visibility>('workspace');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) { setProblem(t('kb.nameRequired')); return; }
    setBusy(true); setProblem(null);
    try { onCreated(await lib.createCollection({ name: name.trim(), description: description.trim(), visibility })); setName(''); setDescription(''); }
    catch (e) { setProblem(failure(e, 'kb.createDenied')); }
    finally { setBusy(false); }
  }
  return <form className="kbCreate" onSubmit={submit} aria-label={t('kb.newCollection')}>
    <label className="field"><span>{t('kb.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} dir="auto" data-autofocus /></label>
    <label className="field"><span>{t('kb.description')}</span><input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} dir="auto" /></label>
    <fieldset className="kbVisibility"><legend>{t('kb.visibility')}</legend>
      <label><input type="radio" name="kb-visibility" checked={visibility === 'workspace'} onChange={() => setVisibility('workspace')} />{t('library.workspace')}</label>
      <label><input type="radio" name="kb-visibility" checked={visibility === 'private'} onChange={() => setVisibility('private')} />{t('library.privateToMe')}</label>
    </fieldset>
    {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    <div className="actions"><button type="submit" disabled={busy}><FilePlus2 aria-hidden="true" />{busy ? t('kb.creating') : t('kb.create')}</button>{onCancel && <button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button>}</div>
  </form>;
}

function AskPanel({ collections, selected, action }: { collections: KnowledgeCollection[]; selected: KnowledgeCollection | null; action?: ReactNode }) {
  const [text, setText] = useState('');
  const [scope, setScope] = useState<'all' | 'current'>('all');
  const [busy, setBusy] = useState<'ask' | 'search' | null>(null);
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [results, setResults] = useState<KnowledgeSearchResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const collectionId = scope === 'current' && selected ? selected.id : null;
  const names = new Map(collections.map((c) => [c.id, c.name]));

  async function run(kind: 'ask' | 'search') {
    const value = text.trim();
    if (!value) { setProblem(t('kb.askEmpty')); return; }
    setBusy(kind); setProblem(null);
    try {
      if (kind === 'ask') { setResults(null); setAnswer(await lib.askKnowledge(value, collectionId)); }
      else { setAnswer(null); setResults(await lib.searchKnowledge(value, collectionId)); }
    } catch (e) { setProblem(failure(e, kind === 'ask' ? 'kb.askDenied' : 'kb.forbiddenShort')); }
    finally { setBusy(null); }
  }

  return <section className="kbAsk" aria-labelledby="kb-ask-title">
    <h2 id="kb-ask-title" className="visuallyHidden">{t('kb.askTitle')}</h2>
    <form className="toolbar kbAskForm" onSubmit={(e) => { e.preventDefault(); void run('ask'); }}>
      <label className="visuallyHidden" htmlFor="kb-ask-input">{t('kb.askLabel')}</label>
      <input id="kb-ask-input" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('kb.askPlaceholder')} maxLength={2000} dir="auto" />
      {selected && <><label className="visuallyHidden" htmlFor="kb-ask-scope">{t('kb.scope')}</label>
        <select id="kb-ask-scope" value={scope} onChange={(e) => setScope(e.target.value as 'all' | 'current')}><option value="all">{t('kb.scopeAll')}</option><option value="current">{t('kb.scopeCurrent', { name: selected.name })}</option></select></>}
      <button type="submit" disabled={busy !== null}><Sparkles aria-hidden="true" />{busy === 'ask' ? t('kb.asking') : t('kb.ask')}</button>
      <button type="button" className="secondary" disabled={busy !== null} onClick={() => void run('search')}><Search aria-hidden="true" />{busy === 'search' ? t('kb.searching') : t('common.search')}</button>
      {action}
    </form>
    {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    {(answer || results) && <div className="surface kbAskResult">
      {answer && <div className="kbAnswer" aria-live="polite">
        {answer.status !== 'answered' && <p className="notice" role="status"><CircleDot aria-hidden="true" />{answer.answer}</p>}
        {answer.status === 'answered' && <p className="kbAnswerText" dir="auto">{answer.answer}</p>}
        {answer.citations.length > 0 && <><h3>{t('kb.sources')}</h3><ol className="kbCitations">{answer.citations.map((c) => <li key={c.n} value={c.n}>
          <strong dir="auto">{c.title}</strong> <span className="statusBadge neutral">{c.provenance === 'file' ? t('kb.provenance.file') : t('kb.provenance.memory')}</span>
          <small>{names.get(c.collection_id) ?? ''}{citationPlace(c) ? <> · <bdi>{citationPlace(c)}</bdi></> : null} · {t('kb.match', { score: Math.round(c.score * 100) })}</small>
          <q dir="auto">{c.snippet}</q>
        </li>)}</ol><p className="muted kbUntrusted">{t('kb.untrusted')}</p></>}
      </div>}
      {results && <div className="kbResults" aria-live="polite">
        {results.semantic === 'no_index' && <p className="muted">{t('kb.semanticNoIndex')}</p>}
        {results.semantic === 'unavailable' && <p className="muted">{t('kb.semanticUnavailable')}</p>}
        {results.hits.length === 0 ? <p className="muted">{t('kb.noHits', { query: results.query })}</p>
          : <ul className="libraryList">{results.hits.map((hit) => <li key={hit.item.id} className="libraryRow">
            <span className="libraryMain">
              <strong dir="auto">{hit.item.title}</strong>
              <small>{hit.collection_name} · {hit.matched_by.map((m) => t(`kb.matched.${m}` as MessageKey)).join(' + ')}</small>
              {hit.snippet && <q dir="auto">{hit.snippet}</q>}
            </span>
          </li>)}</ul>}
      </div>}
      <div className="actions"><button type="button" className="secondary" onClick={() => { setAnswer(null); setResults(null); }}>{t('pages.kb.closeAnswer')}</button></div>
    </div>}
  </section>;
}

function CollectionDetail({ collection, starred, onStar, onBack, onChanged, onCountChange, onDeleted }: {
  collection: KnowledgeCollection; starred: boolean; onStar: () => void; onBack: () => void; onChanged: (c: KnowledgeCollection) => void; onCountChange: (delta: number) => void; onDeleted: () => void;
}) {
  const [filters, setFilters] = useState<{ q?: string; tag?: string; kind?: KnowledgeItemKind | '' }>({});
  const [draft, setDraft] = useState('');
  const [page, setPage] = useState<KnowledgeItemPage | null>(null);
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | { error: string }>('loading');
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState(false);
  const [openItem, setOpenItem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const request = useRef(0);

  const fetchItems = useCallback(async (next: typeof filters) => {
    const id = ++request.current;
    setState('loading');
    try {
      const result = await lib.listItems(collection.id, next, 0, ITEMS_PAGE);
      if (id !== request.current) return;
      setPage(result); setItems(result.items); setState('ready');
    } catch (e) { if (id === request.current) setState({ error: describeError(e) }); }
  }, [collection.id]);
  useEffect(() => { void fetchItems(filters); }, [fetchItems, filters]);

  async function more() {
    try { const result = await lib.listItems(collection.id, filters, items.length, ITEMS_PAGE); setPage(result); setItems((rows) => [...rows, ...result.items]); }
    catch (e) { setFlash({ text: describeError(e), error: true }); }
  }
  async function act(fn: () => Promise<void>, denied: MessageKey) {
    setBusy(true); setFlash(null);
    try { await fn(); } catch (e) { setFlash({ text: failure(e, denied), error: true }); } finally { setBusy(false); }
  }
  const added = (item: KnowledgeItem, key: MessageKey) => { setAdding(false); setFlash({ text: t(key, { title: item.title }) }); onCountChange(1); void fetchItems(filters); };
  const reindex = () => void act(async () => {
    const result = await lib.reindexCollection(collection.id);
    setItems((rows) => rows.map((row) => result.items.find((i) => i.id === row.id) ?? row));
    setFlash({ text: t('kb.reindexed', { indexed: result.indexed, reused: result.reused, failed: result.failed + result.missing }) });
  }, 'kb.reindexDenied');
  const filtered = Boolean(filters.q || filters.tag || filters.kind);

  return <div className="kbCollection">
    <div className="paneHead kbCollectionTop">
      <div className="kbCollectionHead">
        <button type="button" className="secondary paneBack" onClick={onBack}>{t('page.back')}</button>
        <h2 dir="auto">{collection.name}</h2>
        <p className="muted">{collection.visibility === 'private' ? <><Lock aria-hidden="true" />{t('kb.privateNote')}</> : <><Users aria-hidden="true" />{t('kb.workspaceNote')}</>}{collection.description && <> · <span dir="auto">{collection.description}</span></>}</p>
      </div>
      <div className="kbCollectionActions">
        <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('pages.kb.addItem')}</button>
        <button type="button" className="secondary" disabled={busy} onClick={reindex}><RefreshCw aria-hidden="true" />{t('kb.reindex')}</button>
        {collection.can_manage && <button type="button" className="secondary" disabled={busy} onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('kb.edit')}</button>}
        {collection.can_manage && <button type="button" className="secondary dangerText" disabled={busy} onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />{t('kb.deleteCollection')}</button>}
        <StarButton starred={starred} label={collection.name} onToggle={onStar} />
      </div>
    </div>
    <div className="paneBody">
      <Notice flash={flash} />
      <form className="toolbar kbItemFilters" role="search" onSubmit={(e) => { e.preventDefault(); setFilters({ ...filters, q: draft.trim() || undefined }); }}>
        <label className="visuallyHidden" htmlFor="kb-filter-q">{t('kb.filterItems')}</label>
        <input id="kb-filter-q" type="search" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t('kb.filterItems')} maxLength={200} dir="auto" />
        <label className="visuallyHidden" htmlFor="kb-filter-kind">{t('kb.kind')}</label>
        <select id="kb-filter-kind" value={filters.kind ?? ''} onChange={(e) => setFilters({ ...filters, kind: e.target.value as KnowledgeItemKind | '' })}><option value="">{t('kb.anyKind')}</option><option value="file">{t('kb.kind.file')}</option><option value="note">{t('kb.kind.note')}</option></select>
        <label className="visuallyHidden" htmlFor="kb-filter-tag">{t('kb.tag')}</label>
        <select id="kb-filter-tag" value={filters.tag ?? ''} onChange={(e) => setFilters({ ...filters, tag: e.target.value || undefined })}><option value="">{t('kb.anyTag')}</option>{(page?.tags ?? []).map((tag) => <option key={tag} value={tag}>{tag}</option>)}</select>
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('common.search')}</button>
        {filtered && <button type="button" className="secondary" onClick={() => { setDraft(''); setFilters({}); }}>{t('common.clear')}</button>}
      </form>
      <section aria-labelledby="kb-items" aria-busy={state === 'loading'} className="kbItems">
        <h3 id="kb-items" className="kbItemsHead">{t('kb.items')}{page ? <span className="muted"> · {t('kb.itemCount', { n: page.total })}</span> : null}</h3>
        {state === 'loading' && <p className="muted" role="status">{t('kb.loadingItems')}</p>}
        {typeof state === 'object' && <div className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" /><span>{t('kb.itemsFailed', { message: state.error })}</span><button type="button" className="secondary" onClick={() => void fetchItems(filters)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
        {state === 'ready' && items.length === 0 && <div className="paneEmpty"><BookOpen aria-hidden="true" /><h3>{filtered ? t('kb.noItemsMatch') : t('kb.noItems')}</h3><p>{t('kb.noItemsText')}</p><div className="actions">{filtered ? <button type="button" className="secondary" onClick={() => { setDraft(''); setFilters({}); }}>{t('common.clear')}</button> : <button type="button" onClick={() => setAdding(true)}><Plus aria-hidden="true" />{t('pages.kb.addItem')}</button>}</div></div>}
        {state === 'ready' && items.length > 0 && <div className="dataTableWrap"><table className="dataTable kbItemsTable">
          <caption className="visuallyHidden">{t('kb.items')}</caption>
          <thead><tr><th scope="col">{t('pages.kb.col.title')}</th><th scope="col" className="optional">{t('pages.kb.col.tags')}</th><th scope="col">{t('pages.kb.col.index')}</th><th scope="col" className="optional num">{t('pages.files.col.size')}</th><th scope="col" className="optional">{t('pages.kb.col.added')}</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.id} className="libraryRow">
            <th scope="row"><button type="button" className="cellButton" dir="auto" onClick={() => setOpenItem(item.id)}>
              <strong>{item.kind === 'file' ? <FileText aria-hidden="true" className="cellIcon" /> : <NotebookPen aria-hidden="true" className="cellIcon" />}{item.title}</strong>
              <small>{item.kind === 'file' ? t('kb.kind.file') : t('kb.kind.note')}</small>
            </button></th>
            <td className="optional">{item.tags.length > 0 ? <span className="kbTags">{item.tags.map((tag) => <button type="button" key={tag} className="libChip" onClick={() => setFilters({ ...filters, tag })} aria-label={t('kb.filterByTag', { tag })}>{tag}</button>)}</span> : <span className="muted">-</span>}</td>
            <td><IndexBadge item={item} /></td>
            <td className="optional num muted">{item.size_bytes !== null ? <bdi>{fileSize(item.size_bytes)}</bdi> : '-'}</td>
            <td className="optional nowrap muted">{formatDateTime(item.created_at, { dateStyle: 'medium' })}</td>
          </tr>)}</tbody>
        </table></div>}
        {state === 'ready' && page && items.length < page.total && <div className="actions libraryMore"><button type="button" className="secondary" onClick={() => void more()}>{t('kb.more', { shown: items.length, total: page.total })}</button></div>}
      </section>
    </div>
    {adding && <Modal title={t('kb.add')} className="formDialog" onClose={() => setAdding(false)}><AddItems collection={collection} onAdded={added} onCancel={() => setAdding(false)} /></Modal>}
    {editing && <Modal title={t('pages.kb.editTitle')} className="formDialog" onClose={() => setEditing(false)}><EditCollection collection={collection} onCancel={() => setEditing(false)} onSaved={(c) => { onChanged(c); setEditing(false); setFlash({ text: t('kb.saved') }); }} /></Modal>}
    {openItem && <ItemDrawer id={openItem} onClose={() => setOpenItem(null)} onChanged={(item) => setItems((rows) => rows.map((row) => row.id === item.id ? item : row))} onRemoved={(title) => { setOpenItem(null); setFlash({ text: t('kb.removed', { title }) }); onCountChange(-1); void fetchItems(filters); }} />}
    {confirming && <ConfirmDialog title={t('kb.deleteTitle')} confirmLabel={t('kb.deleteCollection')} danger busy={busy} onCancel={() => setConfirming(false)}
      onConfirm={() => void act(async () => { await lib.deleteCollection(collection.id); setConfirming(false); onDeleted(); }, 'kb.manageDenied')}>
      <p>{t('kb.deleteText', { name: collection.name })}</p>
    </ConfirmDialog>}
  </div>;
}

function EditCollection({ collection, onSaved, onCancel }: { collection: KnowledgeCollection; onSaved: (c: KnowledgeCollection) => void; onCancel: () => void }) {
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description);
  const [problem, setProblem] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    try { onSaved(await lib.updateCollection(collection.id, { name: name.trim(), description: description.trim() })); }
    catch (e) { setProblem(failure(e, 'kb.manageDenied')); }
  }
  return <form className="kbEdit" onSubmit={submit}>
    <label className="field"><span>{t('kb.name')}</span><input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required dir="auto" /></label>
    <label className="field"><span>{t('kb.description')}</span><input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} dir="auto" /></label>
    {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
    <div className="actions"><button type="submit" disabled={!name.trim()}>{t('kb.save')}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
  </form>;
}

function AddItems({ collection, onAdded, onCancel }: { collection: KnowledgeCollection; onAdded: (item: KnowledgeItem, key: MessageKey) => void; onCancel: () => void }) {
  const [mode, setMode] = useState<'upload' | 'note' | 'link'>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [tags, setTags] = useState('');
  const [fileId, setFileId] = useState('');
  const [files, setFiles] = useState<{ id: string; name: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (mode === 'link' && files === null) getFiles().then((rows) => setFiles(rows.map((f) => ({ id: f.id, name: f.name })))).catch(() => setFiles([])); }, [mode, files]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    const tagList = parseTags(tags);
    if (mode === 'upload' && !file) { setProblem(t('kb.chooseFile')); return; }
    if (mode === 'note' && (!title.trim() || !content.trim())) { setProblem(t('kb.noteRequired')); return; }
    if (mode === 'link' && !fileId) { setProblem(t('kb.chooseExisting')); return; }
    setBusy(true);
    try {
      if (mode === 'upload' && file) { onAdded(await lib.uploadDocument(collection.id, file, tagList), 'kb.uploaded'); setFile(null); if (input.current) input.current.value = ''; }
      if (mode === 'note') { onAdded(await lib.createNote(collection.id, { title: title.trim(), content: content.trim(), tags: tagList }), 'kb.noteAdded'); setTitle(''); setContent(''); }
      if (mode === 'link') { onAdded(await lib.linkFile(collection.id, fileId, tagList), 'kb.linked'); setFileId(''); }
      setTags('');
    } catch (e) { setProblem(failure(e, 'kb.addDenied')); }
    finally { setBusy(false); }
  }

  return <div className="kbAddItems">
    <div className="libraryChips" role="tablist" aria-label={t('kb.add')}>
      {(['upload', 'note', 'link'] as const).map((value) => <button type="button" role="tab" key={value} aria-selected={mode === value} className={`libChip ${mode === value ? 'active' : ''}`} onClick={() => { setMode(value); setProblem(null); }}>
        {value === 'upload' ? <Upload aria-hidden="true" /> : value === 'note' ? <NotebookPen aria-hidden="true" /> : <Link2 aria-hidden="true" />}{t(`kb.mode.${value}` as MessageKey)}
      </button>)}
    </div>
    <form className="kbAddForm" onSubmit={submit}>
      {mode === 'upload' && <>
        <label className="field"><span>{t('kb.document')}</span><input ref={input} type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <p className="muted">{collection.visibility === 'private' ? t('kb.uploadPrivateWarning') : t('kb.uploadNote')}</p>
      </>}
      {mode === 'note' && <>
        <label className="field"><span>{t('kb.noteTitle')}</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} dir="auto" /></label>
        <label className="field"><span>{t('kb.noteText')}</span><textarea value={content} onChange={(e) => setContent(e.target.value)} maxLength={16000} dir="auto" /></label>
      </>}
      {mode === 'link' && <label className="field"><span>{t('kb.existingFile')}</span>
        <select value={fileId} onChange={(e) => setFileId(e.target.value)}>
          <option value="">{files === null ? t('common.loading') : files.length ? t('kb.pickFile') : t('kb.noFiles')}</option>
          {(files ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
        </select>
      </label>}
      <label className="field"><span>{t('kb.tags')}</span><input value={tags} onChange={(e) => setTags(e.target.value)} placeholder={t('kb.tagsPlaceholder')} maxLength={600} dir="auto" /></label>
      {problem && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{problem}</p>}
      <div className="actions"><button type="submit" disabled={busy}>{busy ? t('kb.adding') : t(`kb.submit.${mode}` as MessageKey)}</button><button type="button" className="secondary" onClick={onCancel}>{t('common.cancel')}</button></div>
    </form>
  </div>;
}

function ItemDrawer({ id, onClose, onChanged, onRemoved }: { id: string; onClose: () => void; onChanged: (item: KnowledgeItem) => void; onRemoved: (title: string) => void }) {
  const [item, setItem] = useState<KnowledgeItem | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'missing' | { error: string }>('loading');
  const [title, setTitle] = useState('');
  const [tags, setTags] = useState('');
  const [content, setContent] = useState('');
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const fetchItem = useCallback(async () => {
    setState('loading');
    try { const value = await lib.getItem(id); setItem(value); setTitle(value.title); setTags(value.tags.join(', ')); setContent(value.content ?? ''); setState('ready'); }
    catch (e) { setState(e instanceof ApiError && e.status === 404 ? 'missing' : { error: describeError(e) }); }
  }, [id]);
  useEffect(() => { void fetchItem(); }, [fetchItem]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!item) return;
    setBusy(true); setFlash(null);
    try {
      const updated = await lib.updateItem(item.id, { title: title.trim(), tags: parseTags(tags), ...(item.kind === 'note' ? { content: content.trim() } : {}) });
      setItem(updated); onChanged(updated); setFlash({ text: t('kb.saved') });
    } catch (e) { setFlash({ text: failure(e, 'kb.itemDenied'), error: true }); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!item) return;
    setBusy(true);
    try { await lib.deleteItem(item.id); setConfirming(false); onRemoved(item.title); }
    catch (e) { setConfirming(false); setFlash({ text: failure(e, 'kb.itemDenied'), error: true }); }
    finally { setBusy(false); }
  }

  return <Drawer title={item?.title ?? t('kb.item')} onClose={onClose}>
    {state === 'loading' && <p className="muted" role="status">{t('kb.loadingItem')}</p>}
    {state === 'missing' && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('kb.itemMissing')}</p>}
    {typeof state === 'object' && <div className="libraryState" role="alert"><p>{state.error}</p><button type="button" className="secondary" onClick={() => void fetchItem()}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
    {state === 'ready' && item && <>
      <p className="reportMeta"><IndexBadge item={item} /><span>{item.kind === 'file' ? t('kb.kind.file') : t('kb.kind.note')}</span>{item.size_bytes !== null && <span>{fileSize(item.size_bytes)}</span>}</p>
      {item.index.state === 'skipped' && <p className="muted">{t('kb.skippedHint')}</p>}
      {item.index.state === 'failed' && <p className="muted">{t('kb.failedHint')}</p>}
      {!item.available && <p className="notice errorNotice" role="alert"><CircleDot aria-hidden="true" />{t('kb.goneHint')}</p>}
      <form className="kbEdit" onSubmit={save}>
        <label className="field"><span>{t('kb.itemTitle')}</span><input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required dir="auto" /></label>
        <label className="field"><span>{t('kb.tags')}</span><input value={tags} onChange={(e) => setTags(e.target.value)} maxLength={600} dir="auto" /></label>
        {item.kind === 'note' && <label className="field"><span>{t('kb.noteText')}</span><textarea value={content} onChange={(e) => setContent(e.target.value)} maxLength={16000} dir="auto" /></label>}
        <Notice flash={flash} />
        <div className="actions">
          <button type="submit" disabled={busy || !title.trim()}>{t('kb.save')}</button>
          <button type="button" className="danger" disabled={busy} onClick={() => setConfirming(true)}><Trash2 aria-hidden="true" />{t('kb.removeItem')}</button>
        </div>
      </form>
    </>}
    {confirming && item && <ConfirmDialog title={t('kb.removeTitle')} confirmLabel={t('kb.removeItem')} danger busy={busy} onCancel={() => setConfirming(false)} onConfirm={() => void remove()}>
      <p>{item.kind === 'note' ? t('kb.removeNoteText', { title: item.title }) : t('kb.removeFileText', { title: item.title })}</p>
    </ConfirmDialog>}
  </Drawer>;
}
