// Favorites (docs/knowledge-base.md#favorites): the caller's starred items with type filters and
// paging, the star toggle other views use, and the quick-access strip on the Assistant home.
import { useCallback, useEffect, useState } from 'react';
import { CircleDot, ExternalLink, Star, StarOff } from 'lucide-react';
import type { Favorite, FavoriteType } from '@anum/contracts';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { FAVORITES_PAGE, FAVORITE_TYPES, favoriteTypeLabel } from './lib/library';
import * as lib from './lib/libraryApi';
import { viewLabel } from './lib/shell';
import { PageHeader, PaneProblem } from './PageParts';
import './library.css';
import './adminPages.css';
import { HelpLink } from './HelpLink';

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
    <PageHeader title={viewLabel('favorites')} subtitle={t('favorites.intro')} />
    {flash && <p className={`notice ${flash.error ? 'errorNotice' : ''}`} role={flash.error ? 'alert' : 'status'}><CircleDot aria-hidden="true" />{flash.text}</p>}
    <section className="pane favoritesPane" aria-busy={load.kind === 'loading'} aria-labelledby="favorites-list">
      <div className="paneHead">
        <h2 id="favorites-list">{load.kind === 'ready' ? t('pages2.favorites.count', { count: total }) : t('favorites.title')}</h2>
        <div className="libraryChips" role="group" aria-label={t('favorites.filter')}>
          <button type="button" className={`libChip ${type === null ? 'active' : ''}`} aria-pressed={type === null} onClick={() => setType(null)}>{t('favorites.all')}</button>
          {FAVORITE_TYPES.map((value) => <button type="button" key={value} className={`libChip ${type === value ? 'active' : ''}`} aria-pressed={type === value} onClick={() => setType(value)}>{favoriteTypeLabel(value)}</button>)}
        </div>
      </div>
      {load.kind === 'loading' && <div className="paneBody"><p className="muted" role="status">{t('favorites.loading')}</p></div>}
      {load.kind === 'error' && <div className="paneBody"><PaneProblem text={t('favorites.loadFailed', { message: load.detail })} onRetry={() => void fetchFirst(type)} /></div>}
      {load.kind === 'ready' && items.length === 0 && <div className="paneEmpty">
        <Star aria-hidden="true" /><h3>{type ? t('favorites.emptyType', { type: favoriteTypeLabel(type) }) : t('favorites.empty')}</h3>
        <p>{t('favorites.emptyText')}</p><HelpLink />
        {type && <div className="actions"><button type="button" className="secondary" onClick={() => setType(null)}>{t('pages2.favorites.showAll')}</button></div>}
      </div>}
      {load.kind === 'ready' && items.length > 0 && <div className="dataTableWrap"><table className="dataTable favoritesTable">
        <thead><tr><th scope="col">{t('pages2.favorites.col.name')}</th><th scope="col">{t('pages2.favorites.col.type')}</th><th scope="col" className="optional">{t('pages2.favorites.col.starred')}</th><th scope="col"><span className="visuallyHidden">{t('pages2.assistants.col.actions')}</span></th></tr></thead>
        <tbody>{items.map((favorite) => <tr key={favorite.id}>
          <th scope="row"><button type="button" className="cellButton" dir="auto" onClick={() => onOpen(favorite)}><strong>{favorite.label}<ExternalLink aria-hidden="true" className="inlineIcon" /></strong></button></th>
          <td><span className="statusBadge neutral">{favoriteTypeLabel(favorite.item_type)}</span></td>
          <td className="optional nowrap">{formatDateTime(favorite.created_at, { dateStyle: 'medium' })}</td>
          <td><div className="cellActions"><button type="button" className="iconButton" onClick={() => void unstar(favorite)} aria-label={t('favorites.unstar', { name: favorite.label })} title={t('favorites.remove')}><StarOff aria-hidden="true" /></button></div></td>
        </tr>)}</tbody>
      </table></div>}
      {load.kind === 'ready' && items.length < total && <div className="actions libraryMore paneBody">
        <button type="button" className="secondary" disabled={more} onClick={() => void loadMore()}>{more ? t('common.loading') : t('favorites.more', { shown: items.length, total })}</button>
      </div>}
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
