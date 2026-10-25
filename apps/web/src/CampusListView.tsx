/**
 * List view: every building and, for the open workspace, every floor and room, as plain
 * semantic lists. It is also the accessible alternative to the 3D scene and the fallback when
 * WebGL is not available, so everything the scene offers is reachable here.
 */
import { ArrowRight, Search } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { buildingSummary } from './CampusView';
import { filterBuildings, readingOrder, buildingStatus, type Campus } from './lib/campusModel.ts';
import type { SceneBuilding } from './lib/headquartersProjection.ts';
import { t, type MessageKey } from './lib/i18n.ts';

export function CampusListView(props: Readonly<{
  campus: Campus;
  currentWorkspaceId: string;
  /** Floors and rooms of the open workspace. Other workspaces open only after an explicit switch. */
  detail: readonly SceneBuilding[];
  onEnter: (workspaceId: string) => void;
  onOpenRoom: (workspaceId: string, floorId: string, roomId: string) => void;
}>) {
  const [query, setQuery] = useState('');
  const searchId = useId();
  const q = query.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim();
  const buildings = useMemo(() => readingOrder(filterBuildings(props.campus.buildings, '')), [props.campus.buildings]);
  const fold = (s: string) => s.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  const visible = buildings.filter((b) => {
    if (!q) return true;
    if (filterBuildings([b], q).length) return true;
    return b.workspace_id === props.currentWorkspaceId && props.detail.some((sb) => sb.floors.some((f) => f.rooms.some((r) => fold(r.label).includes(q))));
  });
  return (
    <section className="hqList" aria-label={t('hq.list.label')}>
      <div className="hqListBar">
        <label htmlFor={searchId} className="hqSearch">
          <Search aria-hidden="true" />
          <input id={searchId} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('hq.list.search')} />
        </label>
        <p className="hqMuted" role="status">{t('hq.list.count', { count: visible.length })}</p>
      </div>
      {visible.length === 0 ? (
        <div className="hqUnavailable" role="status"><strong>{t('hq.list.none')}</strong><button type="button" className="hqGhost" onClick={() => setQuery('')}>{t('hq.list.clear')}</button></div>
      ) : (
        <ul className="hqListBuildings">
          {visible.map((b) => {
            const current = b.workspace_id === props.currentWorkspaceId;
            const status = buildingStatus(b);
            return (
              <li key={b.workspace_id} className="hqListBuilding" data-status={status}>
                <div className="hqListRow">
                  <span className="hqStatusDot" aria-hidden="true" data-status={status} />
                  <div className="hqListName">
                    <h3>{b.name}</h3>
                    <p className="hqMuted">{t(`hq.kind.${b.kind}` as MessageKey)}{b.floors ? ` · ${t('hq.building.floors', { count: b.floors })}` : ''} · {buildingSummary(b)}</p>
                  </div>
                  {b.accessible
                    ? <button type="button" className="hqGhost" onClick={() => props.onEnter(b.workspace_id)} aria-label={t('hq.building.enter', { name: b.name })}>{t('hq.list.open')}<ArrowRight aria-hidden="true" className="flip" /></button>
                    : <span className="hqMuted">{t('hq.detail.notAccessible')}</span>}
                </div>
                {current && props.detail.map((sb) => (
                  <ul key={sb.id} className="hqListFloors" aria-label={t('hq.list.floorsOf', { name: sb.name })}>
                    {sb.floors.map((f) => {
                      const rooms = f.rooms.filter((r) => !q || fold(r.label).includes(q) || filterBuildings([b], q).length > 0);
                      return (
                        <li key={f.id}>
                          <h4>{f.name}</h4>
                          {rooms.length === 0 ? <p className="hqMuted">{t('hq.list.noRooms')}</p> : (
                            <ul className="hqListRooms">
                              {rooms.map((r) => {
                                const desks = f.desks.filter((d) => d.roomId === r.id);
                                return (
                                  <li key={r.id}>
                                    <button type="button" className="hqListRoom" onClick={() => props.onOpenRoom(b.workspace_id, f.id, r.id)}>
                                      <strong>{r.label}</strong>
                                      <small>{t(`hq.roomKind.${r.kind}` as MessageKey)} · {t('hq.list.desks', { count: desks.length })}</small>
                                    </button>
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                ))}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
