/**
 * The glass chrome over the building scene: the Back to campus card, the floor selector and the Live operations and
 * Current flow strips (docs/ANUM_MINE_HQ_LABELS.md). A separate chunk, loaded by the building view, so the headquarters chunk keeps its budget.
 * Every number is a stored count; a source that did not answer says "Not connected", never zero.
 */
import type { OfficeFlowSnapshot, OfficeFlowStage } from '@anum/contracts';
import { ArrowLeft, Building2, Layers } from 'lucide-react';
import type { CampusTotals, StatusKey } from './lib/campusModel.ts';
import { floorNameParts } from './lib/hqLabelModel.ts';
import { t, type MessageKey } from './lib/i18n.ts';

export type BackTone = 'ok' | 'stop' | 'faint';

/** The building's dot: faint when presence is not reporting, red when an agent is blocked, green otherwise. */
export function backTone(totals: CampusTotals | null): BackTone {
  if (!totals) return 'faint';
  return totals.blocked > 0 ? 'stop' : 'ok';
}

export function SceneBackCard(props: {
  name: string; totals: CampusTotals | null; onBack: () => void;
}) {
  const tone = backTone(props.totals);
  const toneText = tone === 'ok' ? t('hqLabels.back.status.live') : tone === 'stop' ? t('hqLabels.back.status.blocked') : t('hqLabels.back.status.none');
  return (
    <div className="hqBackCard">
      <nav className="hqCrumbs" aria-label={t('hq.crumb.label')}>
        <ol>
          <li><button type="button" className="hqBack" onClick={props.onBack}><ArrowLeft aria-hidden="true" className="flip" />{t('hq.back')}</button></li>
          <li className="hqBuildingTitle" aria-current="page">
            <Building2 aria-hidden="true" />
            <div>
              <h2><bdi>{props.name}</bdi></h2>
              <span className="hqBackDot" data-tone={tone} role="img" aria-label={toneText} title={toneText} />
            </div>
          </li>
        </ol>
      </nav>
    </div>
  );
}


/**
 * The building's facts (type, people, floors, rooms, live meetings, orders today), docked under the scene with the other building
 * panels: the stage carries only the Back card and the camera controls. People reads "-" without presence; nothing is invented.
 */
export function BuildingFacts(props: { kindLabel: string; totals: CampusTotals | null; floors: number; rooms: number; meetings: number | null; orders: number | null }) {
  return (
    <ul className="hqCounters hqFacts" aria-label={t('hq.counters.label')}>
      <li className="hqFactKind"><span>{props.kindLabel}</span></li>
      <li><strong>{props.totals ? props.totals.agents : '-'}</strong><span>{t('hq.counters.people')}</span></li>
      <li><strong>{props.floors}</strong><span>{t('hq.counters.floors')}</span></li>
      <li><strong>{props.rooms}</strong><span>{t('hq.counters.rooms')}</span></li>
      {props.meetings !== null && <li><strong>{props.meetings}</strong><span>{t('hq.counters.meetings')}</span></li>}
      {props.orders !== null && <li><strong>{props.orders.toLocaleString()}</strong><span>{t('hqLabels.facts.orders')}</span></li>}
    </ul>
  );
}


export function FloorSelector(props: { floors: ReadonlyArray<{ id: string; name: string }>; activeId: string; stacked: boolean; onPick: (id: string) => void; onStack: (on: boolean) => void }) {
  return (
    <div className="hqFloorSelector" role="group" aria-label={t('hqLabels.selector.label')}>
      <div className="hqFloorButtons">
        {props.floors.map((f) => (
          <button key={f.id} type="button" data-active={f.id === props.activeId && !props.stacked} aria-pressed={f.id === props.activeId} aria-label={t('hqLabels.selector.aria', { name: f.name })} title={f.name} onClick={() => props.onPick(f.id)}>
            {floorNameParts(f.name).short}
          </button>
        ))}
      </div>
      <button type="button" className="hqFloorStack" aria-pressed={props.stacked} aria-label={props.stacked ? t('hqLabels.selector.unstack') : t('hqLabels.selector.stack')} title={props.stacked ? t('hqLabels.selector.unstack') : t('hqLabels.selector.stack')} onClick={() => props.onStack(!props.stacked)}>
        <Layers aria-hidden="true" />
      </button>
    </div>
  );
}

/** The tone of each status shown here (the same names as `STATUS_TONE` in campusModel). */
const OPS_TONE: Readonly<Partial<Record<StatusKey, string>>> = { working: 'ok', meeting: 'meeting', idle: 'info' };
const OPS: ReadonlyArray<{ key: StatusKey; label: MessageKey }> = [
  { key: 'working', label: 'hqLabels.ops.working' }, { key: 'meeting', label: 'hqLabels.ops.meeting' }, { key: 'idle', label: 'hqLabels.ops.idle' },
];

export function LiveOpsStrip(props: { totals: CampusTotals | null; unreported: readonly StatusKey[]; snapshot: OfficeFlowSnapshot | null | undefined }) {
  const un = new Set(props.unreported);
  const flowOk = props.snapshot ? Object.values(props.snapshot.sources).some((s) => s === 'connected') : false;
  return (
    <section className="hqOpsStrip" aria-label={t('hqLabels.ops.aria')}>
      <h3>{t('hqLabels.ops.title')}</h3>
      {props.totals ? (
        <ul>
          <li><strong>{props.totals.agents}</strong><span>{t('hqLabels.ops.people')}</span></li>
          {OPS.map((o) => <li key={o.key} className={`tone-${OPS_TONE[o.key]}`}><strong>{un.has(o.key) ? '-' : props.totals![o.key]}</strong><span>{t(o.label)}</span></li>)}
          {(props.totals.off_shift ?? 0) > 0 && <li className="tone-faint" data-testid="ops-off-shift"><strong>{props.totals.off_shift}</strong><span>{t('hq.status.off_shift')}</span></li>}
          <li className="tone-stop"><strong>{flowOk && props.snapshot ? props.snapshot.totals.stuck : '-'}</strong><span>{t('hqLabels.ops.stuck')}</span></li>
        </ul>
      ) : <p className="hqMuted" role="status"><strong>{t('hqLabels.ops.notConnected')}</strong> {t('hqLabels.ops.noPresence')}</p>}
    </section>
  );
}

const STAGES: readonly OfficeFlowStage[] = ['arrived', 'in_office', 'waiting_human', 'blocked', 'done'];

export function FlowStrip(props: { snapshot: OfficeFlowSnapshot | null | undefined }) {
  const s = props.snapshot;
  const connected = s ? Object.values(s.sources).some((v) => v === 'connected') : false;
  return (
    <section className="hqFlowStrip" aria-label={t('hqLabels.flow.aria')}>
      <h3>{t('hqLabels.flow.title')}</h3>
      {s && connected ? (
        s.totals.items === 0 ? <p className="hqMuted" role="status">{t('hqLabels.flow.none')}</p> : (
          <ol>
            {STAGES.map((stage, i) => {
              const step = t(`hqFlow.stage.${stage}` as MessageKey);
              const count = s.totals.by_stage[stage] ?? 0;
              return <li key={stage} data-stage={stage} aria-label={t('hqLabels.flow.stepAria', { step, count })}><b aria-hidden="true">{i + 1}</b><strong aria-hidden="true">{count}</strong><span aria-hidden="true">{step}</span></li>;
            })}
          </ol>
        )
      ) : <p className="hqMuted" role="status"><strong>{t('hqLabels.ops.notConnected')}</strong> {t('hqLabels.flow.notConnected')}</p>}
    </section>
  );
}
