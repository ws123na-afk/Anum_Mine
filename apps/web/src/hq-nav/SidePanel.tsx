/** Right side of the scene: quick moves, the floor plan with its 2D/3D switch, and the people at their posts. */
import { useState, type ReactNode } from 'react';
import { STATUS_TONE } from '../lib/campusModel.ts';
import type { HqView } from '../HqChrome';
import { statusLabel } from '../HqChrome';
import { hqCommands } from '../lib/hqExtensions.ts';
import type { SceneFloor } from '../lib/headquartersProjection.ts';
import { t, type MessageKey } from '../lib/i18n.ts';
import { initialsOf, planModel, type PersonRow } from './navModel.ts';

export type Move = Readonly<{ id: string; label: string; icon: ReactNode; run: () => void }>;

export function QuickMoves(props: { moves: readonly Move[]; suggestions: ReadonlyArray<Readonly<{ id: string; label: string; target: Parameters<typeof hqCommands.navigateTo>[0] }>> }) {
  const [error, setError] = useState<string | null>(null);
  const go = async (target: Parameters<typeof hqCommands.navigateTo>[0]) => {
    setError(null);
    const result = await hqCommands.navigateTo(target);
    if (!result.ok && result.code !== 'not-mounted') setError(t('hqNav.moves.failed', { reason: result.message }));
  };
  return (
    <section className="hqMoves" aria-label={t('hqNav.moves.title')}>
      <h3>{t('hqNav.moves.title')}</h3>
      <ul className="hqMoveList">
        {props.moves.map((m) => <li key={m.id}><button type="button" className="hqMove" onClick={m.run}>{m.icon}<span>{m.label}</span></button></li>)}
      </ul>
      {props.suggestions.length > 0 ? (
        <>
          <h4>{t('hqNav.moves.suggested')}</h4>
          <ul className="hqPhrases">
            {props.suggestions.map((s) => <li key={s.id}><button type="button" onClick={() => void go(s.target)}>{s.label}</button></li>)}
          </ul>
        </>
      ) : props.moves.length === 0 && <p className="hqMuted">{t('hqNav.moves.none')}</p>}
      {error && <p className="hqNote" role="alert" data-error="true">{error}</p>}
    </section>
  );
}

export function FloorPlan(props: { floor: SceneFloor | null; selectedRoomId: string | null; view: HqView; canThreeD: boolean; onView: (v: HqView) => void; onRoom: (roomId: string) => void }) {
  const { floor } = props;
  const plan = planModel(floor, 100, 4);
  const selected = floor?.rooms.find((r) => r.id === props.selectedRoomId) ?? null;
  const heading = floor ? (selected ? t('hqNav.plan.headingRoom', { floor: floor.name, room: selected.label }) : t('hqNav.plan.heading', { floor: floor.name })) : '';
  return (
    <section className="hqPlan" aria-label={floor ? t('hqNav.plan.label', { floor: floor.name }) : t('hqNav.tab.plan')}>
      <div className="hqPlanHead">
        <h3>{heading}</h3>
        <div className="hqSegment" role="group" aria-label={t('hqNav.plan.mode')}>
          <button type="button" aria-pressed={props.view === 'top'} onClick={() => props.onView('top')}>{t('hqNav.plan.2d')}</button>
          <button type="button" aria-pressed={props.view === '3d'} disabled={!props.canThreeD} onClick={() => props.onView('3d')}>{t('hqNav.plan.3d')}</button>
        </div>
      </div>
      {plan.rooms.length === 0 ? <p className="hqMuted" role="status">{t('hqNav.plan.empty')}</p> : (
        <svg viewBox={`0 0 ${plan.width} ${plan.height}`} className="hqPlanSvg" role="group" aria-label={t('hqNav.plan.label', { floor: floor?.name ?? '' })}>
          {plan.rooms.map((r) => (
            <g key={r.id} className={`hqPlanRoom kind-${r.kind}`} data-selected={r.id === props.selectedRoomId} role="button" tabIndex={0}
              aria-label={t('hqNav.plan.room', { name: r.label, kind: t(`hq.roomKind.${r.kind}` as MessageKey) })} aria-pressed={r.id === props.selectedRoomId}
              onClick={() => props.onRoom(r.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); props.onRoom(r.id); } }}>
              <rect x={r.x} y={r.y} width={r.w} height={r.h} rx="1.2" />
            </g>
          ))}
          {plan.desks.map((d) => <circle key={d.id} cx={d.x} cy={d.y} r="1.1" className={d.assigned ? 'hqPlanDesk assigned' : 'hqPlanDesk'} aria-hidden="true" />)}
          {plan.rooms.filter((r) => r.w > 14 && r.h > 7).map((r) => {
            const fit = Math.max(3, Math.floor(r.w / 2.3));
            return <text key={r.id} className="hqPlanLabel" x={r.x + r.w / 2} y={r.y + 3.2} textAnchor="middle" aria-hidden="true">{r.label.length > fit ? r.label.slice(0, fit - 1) + '\u2026' : r.label}</text>;
          })}
        </svg>
      )}
    </section>
  );
}

const PAGE = 6;

export function PeopleList(props: { rows: readonly PersonRow[]; presenceKnown: boolean; onOpen: (row: PersonRow) => void }) {
  const [all, setAll] = useState(false);
  const rows = all ? props.rows : props.rows.slice(0, PAGE);
  return (
    <section className="hqPeople" aria-label={t('hqNav.people.title')}>
      <h3>{t('hqNav.people.title')}</h3>
      {props.rows.length === 0 ? <p className="hqMuted" role="status">{t('hqNav.people.none')}</p> : (
        <ul>
          {rows.map((r) => {
            const name = r.state === 'private' ? t('hq.agent.private') : r.name ?? t('hq.agent.assigned');
            const chip = r.state === 'not_connected' ? (props.presenceKnown ? t('hqNav.people.notConnected') : t('hqNav.people.unavailable')) : r.status ? statusLabel(r.status) : t('hq.status.unknown');
            const tone = r.state === 'known' && r.status ? STATUS_TONE[r.status] : 'faint';
            return (
              <li key={r.key}>
                <button type="button" className="hqPerson" disabled={!r.agentId} aria-label={`${t('hqNav.people.open', { name })}. ${chip}.`} onClick={() => r.agentId && props.onOpen(r)}>
                  <span className="hqAvatar" aria-hidden="true">{r.name && r.state !== 'private' ? initialsOf(r.name) : '?'}</span>
                  <span className="hqPersonText">
                    <strong>{name}</strong>
                    <small>{[r.role, r.roomLabel ? t('hqNav.people.in', { room: r.roomLabel }) : null].filter(Boolean).join(', ')}</small>
                  </span>
                  <span className={`hqPill tone-${tone}`} data-stale={r.stale}>{chip}{r.stale ? ` (${t('hq.agent.stale')})` : ''}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {props.rows.length > PAGE && <button type="button" className="hqGhost" onClick={() => setAll((v) => !v)}>{all ? t('hqNav.people.less') : t('hqNav.people.more', { count: props.rows.length - PAGE })}</button>}
    </section>
  );
}

