// Favorites (docs/knowledge-base.md#favorites): the caller's starred items with type filters and
// paging, the star toggle other views use, and the quick-access strip on the Assistant home.
import { useCallback, useEffect, useState } from 'react';
import { CircleDot, ExternalLink, RefreshCw, Star, StarOff } from 'lucide-react';
import type { Favorite, FavoriteType } from '@anum/contracts';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { FAVORITES_PAGE, FAVORITE_TYPES, favoriteTypeLabel } from './lib/library';
import * as lib from './lib/libraryApi';
import './library.css';

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
  const [items, setItems] = useState<Favorite[]>([]);
  const [total, setTotal] = useState(0);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [more, setMore] = useState(false);
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null);

  const fetchFirst = useCallback(async (next: FavoriteType | null) => {
    setLoad({ kind: 'loading' });
    try {
      const page = await lib.listFavorites(next, 0, FAVORITES_PAGE);
      setItems(page.items); setTotal(page.total); setLoad({ kind: 'ready' });
    } catch (e) { setLoad({ kind: 'error', detail: describeError(e) }); }
  }, []);
  useEffect(() => { void fetchFirst(type); }, [fetchFirst, type]);

  async function loadMore() {
    setMore(true);
    try {
      const page = await lib.listFavorites(type, items.length, FAVORITES_PAGE);
      setItems((current) => [...current, ...page.items]); setTotal(page.total);
    } catch (e) { setFlash({ text: describeError(e), error: true }); }
    finally { setMore(false); }
  }

  async function unstar(favorite: Favorite) {
    try {
      await lib.removeFavorite(favorite.item_type, favorite.item_id);
      setItems((current) => current.filter((f) => f.id !== favorite.id));
      setTotal((n) => Math.max(0, n - 1));
      setFlash({ text: t('favorites.removed', { name: favorite.label }) });
    } catch (e) { setFlash({ text: describeError(e), error: true }); }
  }

  return <>
    <section className="moduleIntro">
      <div><p className="eyebrow">{t('favorites.eyebrow')}</p><h2>{t('favorites.title')}</h2><p>{t('favorites.intro')}</p></div>
    </section>
    <div className="libraryChips" role="group" aria-label={t('favorites.filter')}>
      <button type="button" className={`libChip ${type === null ? 'active' : ''}`} aria-pressed={type === null} onClick={() => setType(null)}>{t('favorites.all')}</button>
      {FAVORITE_TYPES.map((value) => <button type="button" key={value} className={`libChip ${type === value ? 'active' : ''}`} aria-pressed={type === value} onClick={() => setType(value)}>{favoriteTypeLabel(value)}</button>)}
    </div>
    {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
    <section className="surface libraryPanel" aria-busy={load.kind === 'loading'} aria-labelledby="favorites-list">
      <h3 id="favorites-list" className="libSrOnly">{t('favorites.title')}</h3>
      {load.kind === 'loading' && <p className="muted" role="status">{t('favorites.loading')}</p>}
      {load.kind === 'error' && <div role="alert" className="libraryState"><p>{t('favorites.loadFailed', { message: load.detail })}</p><button type="button" className="secondary" onClick={() => void fetchFirst(type)}><RefreshCw aria-hidden="true" />{t('common.tryAgain')}</button></div>}
      {load.kind === 'ready' && items.length === 0 && <div className="emptyState">
        <Star aria-hidden="true" /><h3>{type ? t('favorites.emptyType', { type: favoriteTypeLabel(type) }) : t('favorites.empty')}</h3>
        <p>{t('favorites.emptyText')}</p>
      </div>}
      {load.kind === 'ready' && items.length > 0 && <ul className="libraryList">
        {items.map((favorite) => <li key={favorite.id} className="libraryRow">
          <span className="libraryMain">
            <button type="button" className="linkButton libraryTitle" dir="auto" onClick={() => onOpen(favorite)}>{favorite.label}<ExternalLink aria-hidden="true" /></button>
            <small><span className="statusBadge">{favoriteTypeLabel(favorite.item_type)}</span> {t('favorites.starredOn', { date: formatDateTime(favorite.created_at, { dateStyle: 'medium' }) })}</small>
          </span>
          <button type="button" className="secondary" onClick={() => void unstar(favorite)} aria-label={t('favorites.unstar', { name: favorite.label })}><StarOff aria-hidden="true" />{t('favorites.remove')}</button>
        </li>)}
      </ul>}
      {load.kind === 'ready' && items.length < total && <div className="actions libraryMore">
        <button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('favorites.more', { shown: items.length, total })}</button>
      </div>}
    </section>
  </>;
}

/** The first starred items, on the Assistant home. Hidden while there are none or on error. */
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
