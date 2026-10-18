// Calls with teammates (docs/in-app-calls.md#history): the caller's own calls from GET /calls,
// newest first with Load older, a Missed filter, and Call back. Only the two participants can
// see a call (the API's row policies), so this lists the signed-in person's calls only.
import { useCallback, useEffect, useState } from 'react';
import { PhoneCall, PhoneIncoming, PhoneMissed, PhoneOutgoing, RefreshCw } from 'lucide-react';
import type { AppCall } from '@anum/contracts';
import { listCalls } from './lib/appCallsApi';
import { formatDuration, isMissed, outcomeWord } from './lib/appCalls';
import { describeError } from './lib/errors';
import { formatDateTime, t, type MessageKey } from './lib/i18n';
import { CallButtons, useAppCalls } from './AppCalls';

type State = { state: 'loading' } | { state: 'ready'; items: AppCall[]; cursor: string | null } | { state: 'error'; message: string };

export function CallHistory() {
  const calls = useAppCalls();
  const [missed, setMissed] = useState(false);
  const [state, setState] = useState<State>({ state: 'loading' });
  const revision = calls?.revision ?? 0;
  const load = useCallback(async (cursor: string | null, append: boolean) => {
    if (!append) setState({ state: 'loading' });
    try {
      const page = await listCalls({ cursor, missed });
      setState((old) => ({
        state: 'ready',
        items: append && old.state === 'ready' ? [...old.items, ...page.items] : page.items,
        cursor: page.next_cursor,
      }));
    } catch (error) {
      setState({ state: 'error', message: describeError(error) });
    }
  }, [missed]);
  useEffect(() => { void load(null, false); }, [load, revision]);

  const filter = (value: boolean) => { if (value !== missed) setMissed(value); };
  return <section className="surface teamPanel" aria-labelledby="calls-title">
    <h2 id="calls-title">{t('calls.history.title')}</h2>
    <p className="muted">{t('calls.history.help')}</p>
    {calls?.unavailable && <p className="notice callsNote" role="status">{calls.unavailable}</p>}
    {calls?.config.state === 'ready' && calls.config.config.mode === 'stun_only' && <p className="notice callsNote" role="status">{t('calls.stunOnly')}</p>}
    <div className="callFilters" role="group" aria-label={t('calls.history.filter')}>
      <button type="button" className="secondary" aria-pressed={!missed} onClick={() => filter(false)}>{t('calls.history.all')}</button>
      <button type="button" className="secondary" aria-pressed={missed} onClick={() => filter(true)}>{t('calls.history.missed')}</button>
    </div>
    {state.state === 'loading' && <p className="muted" role="status">{t('shell.loadingView')}</p>}
    {state.state === 'error' && <div className="notice errorNotice" role="alert">
      <span>{t('calls.history.failed', { message: state.message })}</span>
      <button type="button" className="secondary" onClick={() => void load(null, false)}><RefreshCw aria-hidden="true" />{t('calls.history.retry')}</button>
    </div>}
    {state.state === 'ready' && !state.items.length && <div className="emptyState">
      <PhoneCall aria-hidden="true" />
      <h3>{missed ? t('calls.history.emptyMissed') : t('calls.history.empty')}</h3>
      <p>{missed ? t('calls.history.emptyMissedText') : t('calls.history.emptyText')}</p>
    </div>}
    {state.state === 'ready' && state.items.length > 0 && <ul className="callHistory" aria-label={t('calls.history.title')}>
      {state.items.map((call) => <CallRow key={call.id} call={call} />)}
    </ul>}
    {state.state === 'ready' && state.cursor && <div className="actions">
      <button type="button" className="secondary" onClick={() => void load(state.cursor, true)}>{t('calls.history.loadOlder')}</button>
    </div>}
  </section>;
}

function CallRow({ call }: { call: AppCall }) {
  const word = outcomeWord(call);
  const missed = isMissed(call);
  const Icon = missed ? PhoneMissed : call.direction === 'incoming' ? PhoneIncoming : PhoneOutgoing;
  const direction = t(call.direction === 'incoming' ? 'calls.history.incoming' : 'calls.history.outgoing');
  const media = t(call.media === 'video' ? 'calls.videoCall' : 'calls.voiceCall');
  const when = formatDateTime(call.created_at, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  const detail = [
    direction, media, when,
    call.duration_seconds !== null && call.answered_at ? t('calls.history.lasted', { duration: formatDuration(call.duration_seconds) }) : null,
  ].filter(Boolean).join(' · ');
  return <li className={`callRow ${missed ? 'missed' : ''}`}>
    <Icon className="direction" aria-hidden="true" />
    <div>
      <strong><bdi>{call.peer_name}</bdi></strong>
      <small><span className="callWord">{word ? t(`calls.word.${word}` as MessageKey) : t('calls.phase.ringing')}</span> · {detail}</small>
    </div>
    <CallButtons person={{ user_id: call.peer_user_id, display_name: call.peer_name }} compact />
  </li>;
}
