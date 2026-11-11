// Favorites (docs/knowledge-base.md#favorites): the caller's starred items with search, sort, type
// filters and paging, their own order (drag, or Move up and Move down, saved on the server), remove
// with undo, the star toggle other views use, and the quick-access strip on the Assistant home.
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, ExternalLink, GripVertical, Search, Star, StarOff } from 'lucide-react';
import type { Favorite, FavoriteType } from '@anum/contracts';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { FAVORITES_PAGE, FAVORITE_SORTS, FAVORITE_TYPES, favoriteTypeLabel, moveAnchor, moveItem, type FavoriteSortChoice } from './lib/library';
import * as lib from './lib/libraryApi';
import { viewLabel } from './lib/shell';
import { Empty, LoadingSkeleton, Notice, PageHeader, Pager, PaneProblem } from './PageParts';
import './library.css';
import './adminPages.css';

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; detail: string };

/** Which items of one type the caller starred, with a toggle that keeps it in step. */
export function useStars(type: FavoriteType) {
  const [ids, setIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    lib.listFavorites(type, 0, 200).then((page) => { if (live) setIds(new Set(page.items.map((f) => f.item_id))); }).catch(() => undefined);
    return () => { live = false; };
  }, [type]);
  const toggle = useCallback(async (id: string) => {
    setError(null);
    const starred = ids.has(id);
    try {
      if (starred) await lib.removeFavorite(type, id); else await lib.addFavorite(type, id);
      setIds((current) => { const next = new Set(current); if (starred) next.delete(id); else next.add(id); return next; });
    } catch (e) {
      setError(t('favorites.toggleFailed', { message: describeError(e) }));
    }
  }, [ids, type]);
  return { ids, toggle, error };
}

export function StarButton({ starred, label, onToggle }: { starred: boolean; label: string; onToggle: () => void }) {
  const text = starred ? t('favorites.unstar', { name: label }) : t('favorites.star', { name: label });
  return <button type="button" className={`iconButton starButton ${starred ? 'starred' : ''}`} aria-pressed={starred} aria-label={text} title={text} onClick={onToggle}>
    <Star aria-hidden="true" />
  </button>;
}

export function FavoritesView({ onOpen }: { onOpen: (favorite: Favorite) => void }) {
  const [type, setType] = useState<FavoriteType | null>(null);
  const [draft, setDraft] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<FavoriteSortChoice>('custom');
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<Favorite[]>([]);
  const [total, setTotal] = useState(0);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean; undo?: Favorite } | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  // The row being dragged, read by dragover before React has rendered the drag state.
  const dragFrom = useRef<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [moved, setMoved] = useState('');

  // The search box waits for a pause in typing before it asks the API.
  useEffect(() => { const timer = window.setTimeout(() => setQ(draft.trim()), 250); return () => window.clearTimeout(timer); }, [draft]);
  useEffect(() => setOffset(0), [q]);
  const fetchPage = useCallback(async (quiet = false) => {
    if (!quiet) setLoad({ kind: 'loading' });
    try {
      const page = await lib.listFavorites(type, offset, FAVORITES_PAGE, { q, sort });
      setItems(page.items); setTotal(page.total); setLoad({ kind: 'ready' });
      // A removal that emptied a later page steps back one page.
      if (page.items.length === 0 && offset > 0) setOffset(Math.max(0, offset - FAVORITES_PAGE));
    } catch (e) { setLoad({ kind: 'error', detail: describeError(e) }); }
  }, [type, offset, q, sort]);
  useEffect(() => { void fetchPage(); }, [fetchPage]);

  const canArrange = sort === 'custom';
  const filtered = type !== null || q !== '';

  async function unstar(favorite: Favorite) {
    setBusy(true);
    try {
      await lib.removeFavoriteById(favorite.id);
      setFlash({ text: t('favorites.removed', { name: favorite.label }), undo: favorite });
      await fetchPage(true);
    } catch (e) { setFlash({ text: t('fav.failed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); }
  }
  async function undo(favorite: Favorite) {
    setBusy(true);
    try { await lib.addFavorite(favorite.item_type, favorite.item_id); setFlash({ text: t('fav.restored', { name: favorite.label }) }); await fetchPage(true); }
    catch (e) { setFlash({ text: t('fav.failed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); }
  }
  /** The favorite next to row `index` in direction `step`, read from the API when it is on another page. */
  async function neighbour(index: number, step: -1 | 1): Promise<Favorite | null> {
    const inPage = items[index + step];
    if (inPage) return inPage;
    const at = offset + index + step;
    if (at < 0 || at >= total) return null;
    return (await lib.listFavorites(type, at, 1, { q, sort })).items[0] ?? null;
  }
  async function move(index: number, target: number | null, step: -1 | 1) {
    const favorite = items[index];
    if (!favorite || !canArrange) return;
    setBusy(true); setFlash(null);
    const before = items;
    try {
      const anchor = target !== null ? items[target] : await neighbour(index, step);
      if (!anchor) return;
      if (target !== null) setItems(moveItem(items, index, target));
      await lib.moveFavorite(favorite.id, moveAnchor(step > 0, anchor.id));
      setMoved(t('fav.movedTo', { name: favorite.label, other: anchor.label, where: step > 0 ? t('fav.after') : t('fav.before') }));
      await fetchPage(true);
    } catch (e) { setItems(before); setFlash({ text: t('fav.moveFailed', { message: describeError(e) }), error: true }); }
    finally { setBusy(false); setDragging(null); setOver(null); }
  }

  const sortLabel = (value: FavoriteSortChoice) => t(`fav.sort.${value}` as MessageKey);
  return <>
    <PageHeader title={viewLabel('favorites')} subtitle={t('favorites.intro')} />
    <div aria-live="polite">{flash && !flash.error && <Notice>{flash.text}{flash.undo && <button type="button" className="linkButton favUndo" disabled={busy} onClick={() => { const item = flash.undo; if (item) void undo(item); }}>{t('fav.undo')}</button>}</Notice>}</div>
    {flash?.error && <Notice error>{flash.text}</Notice>}
    <p className="visuallyHidden" aria-live="polite">{moved}</p>
    <section className="pane favoritesPane" aria-busy={load.kind === 'loading'} aria-labelledby="favorites-list">
      <div className="paneHead">
        <h2 id="favorites-list">{load.kind === 'ready' ? t('pages2.favorites.count', { count: total }) : t('favorites.title')}</h2>
        <div className="favToolbar" role="search" aria-label={t('fav.searchLabel')}>
          <label className="field favSearch"><span>{t('fav.search')}</span>
            <span className="favSearchBox"><Search aria-hidden="true" /><input type="search" value={draft} placeholder={t('fav.searchPlaceholder')} onChange={(e) => setDraft(e.target.value)} /></span></label>
          <label className="field favSort"><span>{t('fav.sort')}</span>
            <select value={sort} onChange={(e) => { setSort(e.target.value as FavoriteSortChoice); setOffset(0); }}>{FAVORITE_SORTS.map((value) => <option key={value} value={value}>{sortLabel(value)}</option>)}</select></label>
        </div>
        <div className="libraryChips" role="group" aria-label={t('favorites.filter')}>
          <button type="button" className={`libChip ${type === null ? 'active' : ''}`} aria-pressed={type === null} onClick={() => { setType(null); setOffset(0); }}>{t('favorites.all')}</button>
          {FAVORITE_TYPES.map((value) => <button type="button" key={value} className={`libChip ${type === value ? 'active' : ''}`} aria-pressed={type === value} onClick={() => { setType(value); setOffset(0); }}>{favoriteTypeLabel(value)}</button>)}
        </div>
      </div>
      {canArrange && load.kind === 'ready' && items.length > 1 && <p className="muted favHint paneBody" id="fav-arrange-hint">{t('fav.arrangeHint')}</p>}
      {!canArrange && load.kind === 'ready' && items.length > 1 && <p className="muted favHint paneBody">{t('fav.arrangeOff', { sort: sortLabel('custom') })}</p>}
      {load.kind === 'loading' && <div className="paneBody"><LoadingSkeleton label={t('favorites.loading')} rows={4} /></div>}
      {load.kind === 'error' && <div className="paneBody"><PaneProblem text={t('favorites.loadFailed', { message: load.detail })} onRetry={() => void fetchPage()} /></div>}
      {load.kind === 'ready' && items.length === 0 && (q
        ? <Empty icon={<Search aria-hidden="true" />} title={t('fav.noMatch', { query: q })} text={t('fav.noMatchText')}
          action={<button type="button" className="secondary" onClick={() => { setDraft(''); setQ(''); }}>{t('fav.clearSearch')}</button>} />
        : <Empty icon={<Star aria-hidden="true" />} title={type ? t('favorites.emptyType', { type: favoriteTypeLabel(type) }) : t('favorites.empty')} text={t('favorites.emptyText')}
          action={type ? <button type="button" className="secondary" onClick={() => setType(null)}>{t('pages2.favorites.showAll')}</button>
            : <button type="button" className="secondary" onClick={() => { location.hash = '#reports'; }}>{t('fav.openReports')}</button>} />)}
      {load.kind === 'ready' && items.length > 0 && <ol className="libraryList favList" aria-label={t('fav.listLabel')} aria-describedby={canArrange ? 'fav-arrange-hint' : undefined}>
        {items.map((favorite, index) => {
          const first = offset + index === 0, last = offset + index === total - 1;
          return <li key={favorite.id} className={`libraryRow favRow ${dragging === index ? 'dragging' : ''} ${over === index && dragging !== index ? 'dropTarget' : ''}`}
            draggable={canArrange && !busy} onDragStart={(e) => { dragFrom.current = index; setDragging(index); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', favorite.id); }}
            onDragOver={(e) => { if (dragFrom.current !== null) { e.preventDefault(); setOver(index); } }} onDragLeave={() => setOver((current) => (current === index ? null : current))}
            onDrop={(e) => { e.preventDefault(); const from = dragFrom.current; dragFrom.current = null; if (from !== null && from !== index) void move(from, index, index > from ? 1 : -1); else { setDragging(null); setOver(null); } }}
            onDragEnd={() => { dragFrom.current = null; setDragging(null); setOver(null); }}>
            {canArrange && <span className="favGrip" aria-hidden="true" title={t('fav.drag')}><GripVertical /></span>}
            <div className="libraryMain">
              <button type="button" className="libraryTitle cellButton" dir="auto" onClick={() => onOpen(favorite)}>{favorite.label}<ExternalLink aria-hidden="true" /></button>
              <small><span className="statusBadge neutral">{favoriteTypeLabel(favorite.item_type)}</span><span>{t('favorites.starredOn', { date: formatDateTime(favorite.created_at, { dateStyle: 'medium' }) })}</span></small>
            </div>
            <div className="cellActions">
              {canArrange && <>
                <button type="button" className="iconButton" disabled={busy || first} aria-label={t('fav.moveUp', { name: favorite.label })} title={t('fav.moveUpShort')} onClick={() => void move(index, index > 0 ? index - 1 : null, -1)}><ArrowUp aria-hidden="true" /></button>
                <button type="button" className="iconButton" disabled={busy || last} aria-label={t('fav.moveDown', { name: favorite.label })} title={t('fav.moveDownShort')} onClick={() => void move(index, index < items.length - 1 ? index + 1 : null, 1)}><ArrowDown aria-hidden="true" /></button>
              </>}
              <button type="button" className="iconButton" disabled={busy} onClick={() => void unstar(favorite)} aria-label={t('favorites.unstar', { name: favorite.label })} title={t('favorites.remove')}><StarOff aria-hidden="true" /></button>
            </div>
          </li>;
        })}
      </ol>}
      {load.kind === 'ready' && <Pager label={t('fav.pages')} from={offset + 1} to={offset + items.length} total={total} hasPrev={offset > 0} hasNext={offset + items.length < total} busy={busy}
        onPrev={() => setOffset(Math.max(0, offset - FAVORITES_PAGE))} onNext={() => setOffset(offset + FAVORITES_PAGE)} />}
    </section>
  </>;
}

export function FavoritesStrip({ onOpen, onOpenAll }: { onOpen: (favorite: Favorite) => void; onOpenAll: () => void }) {
  const [items, setItems] = useState<Favorite[] | null>(null);
  useEffect(() => {
    let live = true;
    lib.listFavorites(null, 0, 6).then((page) => { if (live) setItems(page.items); }).catch(() => { if (live) setItems([]); });
    return () => { live = false; };
  }, []);
  if (!items || items.length === 0) return null;
  return <nav className="favoritesStrip" aria-label={t('favorites.quick')}>
    <span className="favoritesStripLabel"><Star aria-hidden="true" />{t('favorites.quick')}</span>
    {items.map((favorite) => <button type="button" key={favorite.id} className="libChip" dir="auto" onClick={() => onOpen(favorite)} title={favoriteTypeLabel(favorite.item_type)}>{favorite.label}</button>)}
    <button type="button" className="linkButton" onClick={onOpenAll}>{t('favorites.seeAll')}</button>
  </nav>;
}
