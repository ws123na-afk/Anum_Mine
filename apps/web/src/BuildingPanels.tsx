/** The module panels of a building (commerce, tyre): a lazy chunk, fetched only when a building shows one. */
import type { CommerceLive, ModuleState, TyreLive } from './lib/hqModules.ts';
import { commerceFlow } from './lib/hqModules.ts';
import { t, type MessageKey } from './lib/i18n.ts';
import { roomName, sceneName } from './lib/registryNames.ts';
import type { RoomGeometry, SceneFloor } from './lib/headquartersProjection.ts';
import { Building2, DoorOpen, HardDrive, Users } from 'lucide-react';

const ROOM_ICON = { meeting: Users, reception: DoorOpen, warehouse: HardDrive } as const;


function NotConnected(props: { title: string; state: ModuleState<unknown>; setupHash: string }) {
  const s = props.state;
  return (
    <section className="hqPanel hqModule" aria-label={props.title}>
      <h2>{props.title}</h2>
      {s.state === 'loading' && <p className="hqMuted" role="status">{t('hq.module.loading')}</p>}
      {s.state === 'not_connected' && <div className="hqUnavailable"><strong>{t('hq.module.notConnected')}</strong><a href={props.setupHash}>{t('hq.module.setup')}</a></div>}
      {s.state === 'forbidden' && <div className="hqUnavailable"><strong>{t('hq.module.forbidden')}</strong><span>{t('hq.module.forbiddenHint')}</span></div>}
      {s.state === 'error' && <div className="hqUnavailable" role="alert"><strong>{t('hq.module.error')}</strong><span>{s.message}</span></div>}
    </section>
  );
}

export function CommercePanels({ state }: { state: ModuleState<CommerceLive> }) {
  if (state.state !== 'ready') return <NotConnected title={t('hq.commerce.title')} state={state} setupHash="#commerce" />;
  const c = state.data;
  return (
    <>
      <section className="hqPanel hqModule" aria-label={t('hq.commerce.title')}>
        <h2>{t('hq.commerce.title')}</h2>
        <ul className="hqStats">
          <li><strong>{c.orders}</strong><span>{t('hq.commerce.orders')}</span></li>
          <li><strong>{c.waiting}</strong><span>{t('hq.commerce.waiting')}</span></li>
          <li><strong>{c.late}</strong><span>{t('hq.commerce.late')}</span></li>
          {c.lowStock !== null && <li><strong>{c.lowStock}</strong><span>{t('hq.commerce.lowStock')}</span></li>}
          <li><strong>{c.openAlerts}</strong><span>{t('hq.commerce.alerts')}</span></li>
        </ul>
      </section>
      <section className="hqPanel hqModule" aria-label={t('hq.flow.title')}>
        <h2>{t('hq.flow.title')}</h2>
        <ol className="hqFlow">
          {commerceFlow(c).map((step) => (
            <li key={step.key}><strong>{step.count}</strong><span>{t(`hq.flow.${step.key}` as MessageKey)}</span></li>
          ))}
        </ol>
      </section>
    </>
  );
}

export function TyrePanel({ state }: { state: ModuleState<TyreLive> }) {
  if (state.state !== 'ready') return <NotConnected title={t('hq.tyre.title')} state={state} setupHash="#tyre" />;
  const d = state.data;
  return (
    <section className="hqPanel hqModule" aria-label={t('hq.tyre.title')}>
      <h2>{t('hq.tyre.title')}</h2>
      <ul className="hqStats">
        <li><strong>{d.vehicles}</strong><span>{t('hq.tyre.vehicles')}</span></li>
        <li><strong>{d.fitted}</strong><span>{t('hq.tyre.fitted')}</span></li>
        {d.inStock !== null && <li><strong>{d.inStock}</strong><span>{t('hq.tyre.stock')}</span></li>}
        <li><strong>{d.belowMinimum}</strong><span>{t('hq.tyre.below')}</span></li>
        <li><strong>{d.overdueInspections}</strong><span>{t('hq.tyre.overdue')}</span></li>
        <li><strong>{d.dueNext30}</strong><span>{t('hq.tyre.due')}</span></li>
      </ul>
    </section>
  );
}

/** Every room of the active floor as a plain list under the scene: the same facts as the cards, always reachable by keyboard and screen reader. */
export function FloorRooms(props: { floor: SceneFloor; has: (desk: SceneFloor['desks'][number]) => boolean; selectedRoomId: string | null; onPick: (id: string) => void; onLeave: () => void }) {
  const { floor } = props;
  const floorTitle = sceneName(floor);
  const rows = floor.rooms.map((room) => {
    const desks = floor.desks.filter((d) => d.roomId === room.id);
    return { room, desks: desks.length, agents: desks.filter((d) => d.agentId || props.has(d)).length };
  });
  const totals = rows.reduce((n, r) => ({ desks: n.desks + r.desks, agents: n.agents + r.agents }), { desks: 0, agents: 0 });
  return (
    <section className="hqPanel hqFloorRooms" aria-label={t('hqFloor.listTitle', { floor: floorTitle })} data-testid="hq-floor-rooms">
      <header>
        <h2>{t('hqFloor.listTitle', { floor: floorTitle })}</h2>
        <p className="hqMuted">{t('hqFloor.summary', { rooms: rows.length, desks: totals.desks, agents: totals.agents })}</p>
        <button type="button" className="hqGhost" onClick={props.onLeave}>{t('hqFloor.back')}</button>
      </header>
      <ul>
        {rows.map(({ room, desks, agents }) => (
          <li key={room.id}>
            <button type="button" data-selected={room.id === props.selectedRoomId} aria-pressed={room.id === props.selectedRoomId} onClick={() => props.onPick(room.id)}>
              <strong>{roomName(room)}</strong>
              <small>{t(`hq.roomKind.${room.kind}` as MessageKey)}. {t('hqFloor.stats', { desks, agents })}</small>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The room card of the floor overview: the name, the kind, the desks and the people at them; a button that selects the room. */
export function OverviewCard(props: { room: RoomGeometry; desks: number; assigned: number; selected: boolean; onPick: () => void }) {
  const Icon = (ROOM_ICON as Record<string, typeof Users>)[props.room.kind] ?? Building2;
  return (
    <button type="button" className="hqLabelCard hqOverviewCard" data-selected={props.selected} data-kind={props.room.kind} aria-pressed={props.selected} onClick={props.onPick}>
      <span className="hqLabelIcon"><Icon aria-hidden="true" /></span>
      <span className="hqLabelText"><strong>{roomName(props.room)}</strong><small>{t(`hq.roomKind.${props.room.kind}` as MessageKey)}</small><small>{t('hqFloor.stats', { desks: props.desks, agents: props.assigned })}</small></span>
    </button>
  );
}
