/** The guided visit of the headquarters (docs/ANUM_MINE_EXECUTIVE_PA.md#guided-tour).
 *
 * Every stop is a room from `GET /api/v1/executive-pa/tour`: only the buildings, floors and rooms the
 * person can access, in the office registry. Nothing is invented: no staff, no activity, no example roles.
 * Words and narration follow the page language (Arabic or English), and the panel mirrors in RTL. */
import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import { ArrowRight, ClipboardList, MapPin, Pause, Play, RotateCcw, Volume2, VolumeX, X } from 'lucide-react';
import { apiRequest } from './lib/api';
import { describeError } from './lib/errors';
import { createPaClient, type Tour } from './lib/executivePa';
import {
  changeGuidedTour, createGuidedTour, GuidedTourError, stopsFromRegistry, tourProgress,
  type GuidedTour, type TourAction, type TourEvent, type TourStop,
} from './lib/guidedExecutiveTour';
import { formatDateTime, formatNumber, t, type MessageKey } from './lib/i18n';
import { useLocale } from './useLocale';
import './guidedExecutiveTour.css';

const KINDS = new Set(['office', 'meeting', 'reception', 'workshop', 'warehouse', 'lounge']);
const kindLabel = (kind: string) => (KINDS.has(kind) ? t(`hqSc.kind.${kind}` as MessageKey) : kind);

/** What the PA says and shows for one stop: the registry's own facts, nothing else. */
export function describeStop(stop: TourStop): string {
  return t('gt.describe', { room: stop.name, floor: stop.floorName, building: stop.buildingName, kind: kindLabel(stop.kind), desks: formatNumber(stop.desks), capacity: formatNumber(stop.capacity) });
}

function eventText(event: TourEvent, stops: readonly TourStop[]): string {
  const room = stops.find((s) => s.id === event.roomId)?.name ?? '';
  return t(`gt.event.${event.kind}` as MessageKey, { room });
}

type Load = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; tour: Tour };

interface Props {
  onClose(): void;
  /** Moves the view to the stop. Resolves with a message when the move did not happen. */
  onArrive(stop: TourStop): Promise<string | null> | string | null | void;
  /** Test seam: reads the stops; defaults to the executive PA tour endpoint. */
  load?: () => Promise<Tour>;
}

export function GuidedExecutiveTour({ onClose, onArrive, load }: Props) {
  const locale = useLocale();
  const client = useMemo(() => createPaClient(apiRequest), []);
  const [loaded, setLoaded] = useState<Load>({ state: 'loading' });
  const reload = useCallback(() => {
    setLoaded({ state: 'loading' });
    (load ? load() : client.tour()).then(
      (tour) => setLoaded({ state: 'ready', tour }),
      (error) => setLoaded({ state: 'error', message: describeError(error) }),
    );
  }, [client, load]);
  useEffect(() => { reload(); }, [reload]);

  return <section className="executiveTour" role="dialog" aria-modal="false" aria-labelledby="gt-title" lang={locale}>
    <header className="executiveTourHeader">
      <div><small>{t('gt.kicker')}</small><h2 id="gt-title">{t('gt.title')}</h2><p>{t('gt.sub')}</p></div>
      <button type="button" aria-label={t('gt.close')} onClick={onClose}><X size={18} aria-hidden="true" /></button>
    </header>
    {loaded.state === 'loading' && <p className="executiveTourState" role="status">{t('gt.loading')}</p>}
    {loaded.state === 'error' && <p className="executiveTourState" role="alert">{t('gt.loadFailed', { message: loaded.message })} <button type="button" onClick={reload}>{t('common.retry')}</button></p>}
    {loaded.state === 'ready' && loaded.tour.status === 'not_connected' && <p className="executiveTourState" role="status">{t('gt.notConnected')}</p>}
    {loaded.state === 'ready' && loaded.tour.status !== 'not_connected' && loaded.tour.stops.length === 0 && <p className="executiveTourState" role="status">{t('gt.empty')}</p>}
    {loaded.state === 'ready' && loaded.tour.stops.length > 0 && <TourBody stops={stopsFromRegistry(loaded.tour.stops)} onArrive={onArrive} />}
    <p className="executiveTourDisclaimer">{t('gt.note')}</p>
  </section>;
}

function TourBody({ stops, onArrive }: { stops: readonly TourStop[]; onArrive: Props['onArrive'] }) {
  const locale = useLocale();
  const [state, dispatch] = useReducer(changeGuidedTour, stops, (s) => createGuidedTour(s, new Date().toISOString()));
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window;
  const [voice, setVoice] = useState(false);
  const [error, setError] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const current = stops.find((s) => s.id === state.currentRoomId);
  useEffect(() => () => { if (canSpeak) window.speechSynthesis.cancel(); }, [canSpeak]);

  const announce = (message: string) => {
    if (!voice || !canSpeak) return;
    window.speechSynthesis.cancel();
    // The bidi isolates that keep names in place on screen are not spoken.
    const utterance = new SpeechSynthesisUtterance(message.replace(/[\u2068\u2069]/g, ''));
    utterance.lang = locale === 'ar' ? 'ar-SA' : 'en-US';
    utterance.rate = 0.92;
    window.speechSynthesis.speak(utterance);
  };

  const perform = (action: TourAction) => {
    try {
      const next = changeGuidedTour(state, action);
      dispatch(action); setError('');
      const arrived = next.currentRoomId && next.currentRoomId !== state.currentRoomId ? stops.find((s) => s.id === next.currentRoomId) : undefined;
      if (arrived) {
        announce(describeStop(arrived));
        void Promise.resolve(onArrive(arrived)).then((problem) => { if (problem) setError(t('gt.arriveFailed', { message: problem })); });
      } else if (next.phase === 'finished' && state.phase !== 'finished') announce(t('gt.done'));
    } catch (e) {
      setError(e instanceof GuidedTourError ? t(`gt.err.${e.code}` as MessageKey) : describeError(e));
    }
  };
  const at = () => new Date().toISOString();
  const last = state.index === stops.length - 1;
  const progress = tourProgress(state);

  return <>
    <div className="executiveTourStatus">
      <div><span id="gt-progress">{t('gt.progress')}</span><strong>{formatNumber(progress)}%</strong></div>
      <progress max={100} value={progress} aria-labelledby="gt-progress" />
      <span className="executiveTourPhase">{t(`gt.phase.${state.phase}` as MessageKey)}</span>
    </div>
    <div className="executiveTourCompanion">
      <div className="executiveTourAvatar" aria-hidden="true">✦</div>
      <div><strong>{t('gt.companion')}</strong>
        <p aria-live="polite">{state.phase === 'finished' ? t('gt.done') : current ? describeStop(current) : t('gt.welcome')}</p>
      </div>
    </div>
    <div className="executiveTourActions">
      {state.phase === 'ready' && <button type="button" className="executiveTourPrimary" onClick={() => perform({ type: 'begin', at: at() })}><Play size={15} aria-hidden="true" /> {t('gt.begin')}</button>}
      {state.phase === 'touring' && <>
        <button type="button" className="executiveTourPrimary" onClick={() => perform({ type: 'next', at: at() })}><ArrowRight size={15} aria-hidden="true" /> {last ? t('gt.finish') : t('gt.next')}</button>
        <button type="button" onClick={() => perform({ type: 'pause', at: at() })}><Pause size={15} aria-hidden="true" /> {t('gt.pause')}</button></>}
      {state.phase === 'paused' && <button type="button" className="executiveTourPrimary" onClick={() => perform({ type: 'resume', at: at() })}><Play size={15} aria-hidden="true" /> {t('gt.resume')}</button>}
      {state.phase === 'finished' && <button type="button" onClick={() => perform({ type: 'reset', stops, at: at() })}><RotateCcw size={15} aria-hidden="true" /> {t('gt.restart')}</button>}
      <button type="button" aria-pressed={voice} disabled={!canSpeak} title={canSpeak ? undefined : t('gt.narrationUnavailable')}
        onClick={() => { setVoice((v) => !v); if (voice && canSpeak) window.speechSynthesis.cancel(); }}>
        {voice ? <Volume2 size={15} aria-hidden="true" /> : <VolumeX size={15} aria-hidden="true" />} {voice ? t('gt.narrationOn') : t('gt.narrationOff')}
      </button>
    </div>
    <h3>{t('gt.stops')}</h3>
    <ol className="executiveTourStops">
      {stops.map((s, i) => <li key={s.id} className={current?.id === s.id ? 'active' : ''}>
        <button type="button" onClick={() => perform({ type: 'redirect', roomId: s.id, at: at() })} disabled={state.phase === 'finished'} aria-current={current?.id === s.id ? 'step' : undefined}>
          <span className="executiveTourStopNumber">{state.visited.includes(s.id) ? '✓' : formatNumber(i + 1)}</span>
          <span><strong><bdi>{s.name}</bdi></strong><small><bdi>{t('gt.stopMeta', { floor: s.floorName, building: s.buildingName })}</bdi></small></span>
          <MapPin size={15} aria-hidden="true" />
        </button>
      </li>)}
    </ol>
    <div className="executiveTourBottom">
      <button type="button" onClick={() => setShowHistory((v) => !v)} aria-expanded={showHistory}><ClipboardList size={15} aria-hidden="true" /> {t(showHistory ? 'gt.historyHide' : 'gt.historyShow', { count: formatNumber(state.events.length) })}</button>
      {showHistory && <ol className="executiveTourHistory">{state.events.map((e, i) => <li key={i}><time dateTime={e.at}>{formatDateTime(e.at, { timeStyle: 'medium' })}</time> <bdi>{eventText(e, stops)}</bdi></li>)}</ol>}
    </div>
    {error && <p className="executiveTourError" role="alert">{error}</p>}
  </>;
}

export type { GuidedTour };
