import { ChevronLeft, ChevronRight, SkipForward } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { OfficeHistoryEvidence } from '@anum/contracts';
import { useHqHistory } from './useHqHistory';
import { EMPTY_FILTERS, type HistoryFilters } from './lib/hqHistory';
import { actionLabel, fieldLabel, valueLabel, whenLabel, whoLabel, type NameMap } from './lib/hqHistoryText';
import { ROOM_FIELDS, buildRoomTimeline, desksAt, stateAt, type RoomTimeline, type Snap, type Step, type StateAtResult } from './lib/hqRoomHistory';
import { t } from './lib/i18n';
import type { PickOption } from './HqHistoryParts';

const OFFICE_ONLY: HistoryFilters = { ...EMPTY_FILTERS, category: 'office' };

function stepLabel(step: Step): string {
  if (step.kind === 'baseline') return t('hqHist.slider.baseline');
  if (step.kind === 'gap') return t('hqHist.slider.cannot');
  return `${actionLabel(step.change.record.action)}, ${whenLabel(step.change.record.timestamp)}`;
}

function StateList({ state, names }: { state: Snap; names: NameMap }) {
  const rows = ROOM_FIELDS.filter((f) => state[f] !== null && state[f] !== undefined && state[f] !== '' && !(Array.isArray(state[f]) && (state[f] as unknown[]).length === 0));
  return <dl className="hqHistFacts">{rows.map((f) => <div key={f}><dt>{fieldLabel(f)}</dt><dd>{valueLabel(f, state[f], names)}</dd></div>)}</dl>;
}

function DeskList({ timeline, index, names }: { timeline: RoomTimeline; index: number; names: NameMap }) {
  const desks = desksAt(timeline, index);
  return (
    <section aria-labelledby="hqHistDesks">
      <h4 id="hqHistDesks">{t('hqHist.slider.desks')}</h4>
      {desks.length === 0 ? <p className="hqHistHint">{t('hqHist.slider.deskNone')}</p> : (
        <ul className="hqHistDeskList">{desks.map((d) => (
          <li key={d.deskId}>{t('hqHist.desk.label', { label: String(d.state.label ?? d.deskId) })}: {d.state.agent_id ? t('hqHist.desk.agent', { agent: valueLabel('agent_id', d.state.agent_id, names) }) : t('hqHist.desk.vacant')}</li>
        ))}</ul>
      )}
      <p className="hqHistHint">{t('hqHist.slider.desksNote')}</p>
    </section>
  );
}

function StepBody({ timeline, step, names, onOpenRecord }: { timeline: RoomTimeline; step: Step; names: NameMap; onOpenRecord: (r: OfficeHistoryEvidence) => void }) {
  if (step.kind === 'gap') {
    return (
      <div className="hqHistCannot" data-testid="cannot">
        <h4>{t('hqHist.slider.gap')}</h4>
        <p><strong>{t('hqHist.slider.cannot')}</strong></p>
        <p>{t('hqHist.slider.gapBody', { fields: step.fields.map(fieldLabel).join(', ') })}</p>
        <p className="hqHistHint">{whenLabel(new Date(step.from).toISOString())} · {whenLabel(new Date(step.to).toISOString())}</p>
      </div>
    );
  }
  if (step.kind === 'baseline') {
    return (
      <div>
        <h4>{t('hqHist.slider.baseline')}</h4>
        {step.created ? <p>{t('hqHist.slider.baselineCreated')}</p>
          : step.state ? <><p>{t('hqHist.slider.baselineState')}</p><StateList state={step.state} names={names} /></>
            : <p className="hqHistHint">{t('hqHist.slider.baselineNone')}</p>}
        {step.olderUnloaded && <p className="hqHistHint">{t('hqHist.slider.unloaded')}</p>}
        <DeskList timeline={timeline} index={step.index} names={names} />
      </div>
    );
  }
  const { change } = step;
  return (
    <div>
      <h4>{actionLabel(change.record.action)}</h4>
      <p className="hqHistHint">{whenLabel(change.record.timestamp)} · {t('hqHist.by', { actor: whoLabel(change.record.actor, names) })}</p>
      {step.deleted && <p><strong>{t('hqHist.slider.deleted')}</strong></p>}
      {change.kind === 'copy_source' && <p>{t('hqHist.slider.copy')}</p>}
      {(change.fields.length > 0 || change.desks.some((d) => d.fields.length > 0)) && (
        <section aria-labelledby="hqHistStepChange">
          <h5 id="hqHistStepChange">{t('hqHist.slider.change')}</h5>
          <table className="hqHistDiff">
            <thead><tr><th scope="col">{t('hqHist.slider.change')}</th><th scope="col">{t('hqHist.detail.before')}</th><th scope="col">{t('hqHist.detail.after')}</th></tr></thead>
            <tbody>
              {change.fields.map((f) => <tr key={f.field}><th scope="row">{fieldLabel(f.field)}</th><td>{valueLabel(f.field, f.before, names)}</td><td>{valueLabel(f.field, f.after, names)}</td></tr>)}
              {change.desks.flatMap((d) => d.fields.map((f) => (
                <tr key={`${d.deskId}:${f.field}`}><th scope="row">{t('hqHist.desk.label', { label: String(d.after?.label ?? d.before?.label ?? d.deskId) })}: {fieldLabel(f.field)}</th><td>{valueLabel(f.field, f.before, names)}</td><td>{valueLabel(f.field, f.after, names)}</td></tr>
              )))}
            </tbody>
          </table>
        </section>
      )}
      <section aria-labelledby="hqHistStepState">
        <h5 id="hqHistStepState">{t('hqHist.slider.state')}</h5>
        {step.state ? <StateList state={step.state} names={names} /> : <p className="hqHistHint">{t('hqHist.slider.stateNone')}</p>}
      </section>
      <DeskList timeline={timeline} index={step.index} names={names} />
      <button type="button" className="hqGhost" onClick={() => onOpenRecord(change.record)}>{t('hqHist.slider.open')}</button>
    </div>
  );
}

const REASON: Readonly<Record<string, string>> = {
  older: 'hqHist.slider.cannotOlder', future: 'hqHist.slider.cannotFuture', invalid: 'hqHist.slider.cannotInvalid', empty: 'hqHist.slider.cannotEmpty', gap: 'hqHist.slider.gapBody',
};

/**
 * The room history slider: a scrubber over the stored audit records that changed one room. A step is a record.
 * Where the records cannot say what the room looked like, the panel says so and shows nothing in its place.
 */
export function RoomSlider(props: {
  rooms: readonly (PickOption & { deskIds: readonly string[] })[]; roomId: string; onPickRoom: (id: string) => void;
  names: NameMap; onOpenRecord: (r: OfficeHistoryEvidence) => void;
}) {
  const room = props.rooms.find((r) => r.id === props.roomId) ?? null;
  const scope = useMemo(() => (room ? { kind: 'room' as const, roomId: room.id, label: room.label, deskIds: room.deskIds } : { kind: 'office' as const }), [room]);
  const history = useHqHistory(OFFICE_ONLY, scope, room !== null);
  const timeline = useMemo(() => buildRoomTimeline(history.state.items, props.roomId, history.state.cursor !== null), [history.state, props.roomId]);
  const [index, setIndex] = useState(0);
  const [jump, setJump] = useState('');
  const [verdict, setVerdict] = useState<StateAtResult | null>(null);
  const last = timeline.steps.length - 1;
  // Start on the latest step; keep the person's step when more records load (older steps are added at the start, so follow the same step).
  const [followLatest, setFollowLatest] = useState(true);
  useEffect(() => { setFollowLatest(true); setVerdict(null); }, [props.roomId]);
  useEffect(() => { if (followLatest && last >= 0) setIndex(last); }, [followLatest, last]);
  const step = timeline.steps[Math.min(index, Math.max(last, 0))];
  const go = (next: number) => { setFollowLatest(next >= last); setIndex(Math.max(0, Math.min(last, next))); setVerdict(null); };
  const submitJump = () => {
    if (!jump) return;
    const result = stateAt(timeline, jump);
    setVerdict(result);
    if (result.stepIndex !== null) { setFollowLatest(false); setIndex(result.stepIndex); }
  };

  return (
    <div className="hqHistSlider">
      <label className="hqHistPick">{t('hqHist.slider.pick')}
        <select value={props.roomId} onChange={(e) => props.onPickRoom(e.target.value)}>
          <option value="">{t('hqHist.slider.pick')}</option>
          {props.rooms.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
      </label>
      {!room && <p className="hqHistHint">{t('hqHist.slider.noRoom')}</p>}
      {room && (
        <>
          <p className="hqHistHint">{t('hqHist.slider.intro')}</p>
          {history.status === 'loading' && <p role="status" aria-busy="true" className="hqHistHint">{t('hqHist.loading')}</p>}
          {history.status === 'denied' && <p role="alert" className="hqHistError">{t('hqHist.denied')}</p>}
          {(history.status === 'error' || history.status === 'invalid') && (
            <p role="alert" className="hqHistError">{t('hqHist.error', { detail: history.error })} <button type="button" className="hqLinkButton" onClick={history.reload}>{t('hqHist.retry')}</button></p>
          )}
          {history.status === 'ready' && history.error && <p role="alert" className="hqHistError">{history.error}</p>}
          {history.status === 'ready' && timeline.steps.length === 0 && (
            <div className="hqHistEmpty">
              <p>{history.state.cursor ? t('hqHist.slider.empty') : t('hqHist.slider.emptyDone')}</p>
              {history.state.cursor && <button type="button" className="hqGhost" onClick={history.loadOlder} disabled={history.busy}>{history.busy ? t('hqHist.loadingMore') : t('hqHist.slider.load')}</button>}
            </div>
          )}
          {history.status === 'ready' && timeline.steps.length > 0 && step && (
            <>
              <div className="hqHistScrub">
                <button type="button" className="hqIconButton" onClick={() => go(index - 1)} disabled={index <= 0} aria-label={t('hqHist.slider.prev')}><ChevronLeft aria-hidden="true" className="flip" /></button>
                <input type="range" min={0} max={last} step={1} value={Math.min(index, last)} onChange={(e) => go(Number(e.target.value))}
                  aria-label={t('hqHist.slider.label', { room: room.label })}
                  aria-valuetext={t('hqHist.slider.value', { step: Math.min(index, last) + 1, total: timeline.steps.length, label: stepLabel(step) })} />
                <button type="button" className="hqIconButton" onClick={() => go(index + 1)} disabled={index >= last} aria-label={t('hqHist.slider.next')}><ChevronRight aria-hidden="true" className="flip" /></button>
                <button type="button" className="hqIconButton" onClick={() => go(last)} disabled={index >= last} aria-label={t('hqHist.slider.latest')} title={t('hqHist.slider.latest')}><SkipForward aria-hidden="true" className="flip" /></button>
              </div>
              <ol className="hqHistTicks" aria-hidden="true">{timeline.steps.map((s) => <li key={s.index} data-kind={s.kind} data-active={s.index === Math.min(index, last)} />)}</ol>
              <p className="hqHistHint" aria-live="polite">{t('hqHist.slider.stepOf', { step: Math.min(index, last) + 1, total: timeline.steps.length })}</p>
              {history.state.cursor && (
                <p className="hqHistHint">{t('hqHist.slider.unloaded')} <button type="button" className="hqLinkButton" onClick={history.loadOlder} disabled={history.busy}>{history.busy ? t('hqHist.loadingMore') : t('hqHist.slider.load')}</button></p>
              )}
              <form className="hqHistJump" onSubmit={(e) => { e.preventDefault(); submitJump(); }}>
                <label>{t('hqHist.slider.jump')}<input type="datetime-local" value={jump} onChange={(e) => setJump(e.target.value)} /></label>
                <button type="submit" className="hqGhost" disabled={!jump}>{t('hqHist.slider.jumpGo')}</button>
              </form>
              {verdict && verdict.status !== 'known' && (
                <div className="hqHistCannot" role="status" data-testid="jump-verdict">
                  <p><strong>{verdict.status === 'not_created' ? t('hqHist.slider.notCreated') : t('hqHist.slider.cannot')}</strong></p>
                  {verdict.status === 'unknown' && verdict.reason !== 'gap' && <p>{t(REASON[verdict.reason] as 'hqHist.slider.cannotOlder')}</p>}
                </div>
              )}
              <div className="hqHistStep" aria-live="polite"><StepBody timeline={timeline} step={step} names={props.names} onOpenRecord={props.onOpenRecord} /></div>
              <p className="hqHistHint">{t('hqHist.slider.noPresence')}</p>
            </>
          )}
        </>
      )}
    </div>
  );
}
