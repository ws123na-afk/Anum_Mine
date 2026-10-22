import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Hand, LogIn, LogOut, Pause, Play, Square, Users, X } from 'lucide-react';
import * as api from './lib/api';
import { describeError } from './lib/errors';
import { intlLocale, t, type MessageKey } from './lib/i18n';
import {
  TERMINAL, activeSpeakers, applyEvent, budgetUsed, draftFromMessage, initialState, presentActors,
  type RoomEvent, type RoomState,
} from './lib/liveMeeting';
import { followRoom, meetingRoomApi, type MeetingSummary } from './lib/liveMeetingApi';
import { AgendaCard, Composer, EndButton, FilesCard, Meter, OutputsCard, RecordCard, Transcript, endedNote, type Draft } from './MeetingRoomParts';
import { useLocale } from './useLocale';
import './meetingRoom.css';

/**
 * The live meeting room (docs/ANUM_MINE_LIVE_MEETING_ROOM_UI.md). Stable props:
 * `workspaceId` the workspace the room belongs to (checked against the signed-in scope),
 * `meetingId` the live agent meeting to open, `roomId` an office room id (no persisted room to
 * meeting link exists yet, so it opens a picker of this workspace's meetings), `onClose`.
 * Everything shown is a stored row or a stored event; nothing is simulated.
 */
export interface MeetingRoomPanelProps { workspaceId: string; meetingId?: string; roomId?: string; onClose: () => void }

type Failure = 'denied' | 'notFound' | 'wrongWorkspace' | 'error';
type Load = { phase: 'loading' } | { phase: 'ready' } | { phase: 'failed'; kind: Failure; detail: string };

export function MeetingRoomPanel({ workspaceId, meetingId, roomId, onClose }: MeetingRoomPanelProps) {
  useLocale();
  const [chosen, setChosen] = useState<string | null>(meetingId ?? null);
  useEffect(() => setChosen(meetingId ?? null), [meetingId]);
  return <section className="mr" aria-label={t('meetingRoom.label')} data-room-id={roomId}>
    {chosen
      ? <Room key={chosen} workspaceId={workspaceId} meetingId={chosen} onClose={onClose} />
      : <Picker onOpen={setChosen} onClose={onClose} />}
  </section>;
}

function Picker({ onOpen, onClose }: { onOpen: (id: string) => void; onClose: () => void }) {
  const [items, setItems] = useState<MeetingSummary[] | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { let live = true; meetingRoomApi.list().then((r) => live && setItems(r.items), (e) => live && setError(describeError(e))); return () => { live = false; }; }, []);
  return <div className="mrPicker">
    <header className="mrHead"><h2>{t('meetingRoom.picker')}</h2><button type="button" className="secondary" onClick={onClose}>{t('meetingRoom.close')}</button></header>
    {error && <p className="notice errorNotice" role="alert">{error}</p>}
    {!items && !error && <p role="status" className="muted">{t('meetingRoom.loading')}</p>}
    {items && items.length === 0 && <p className="muted">{t('meetingRoom.pickerEmpty')}</p>}
    <ul className="mrPickerList">{(items ?? []).map((m) => <li key={m.id}>
      <span dir="auto"><strong>{m.title}</strong> <span className={`mrChip mr-${m.status}`}>{t(`meetingRoom.status.${m.status}` as MessageKey)}</span></span>
      <button type="button" className="secondary" onClick={() => onOpen(m.id)}>{t('meetingRoom.pickerOpen')}</button>
    </li>)}</ul>
  </div>;
}

function failureOf(error: unknown): { kind: Failure; detail: string } {
  const status = (error as { status?: number }).status;
  return { kind: status === 403 ? 'denied' : status === 404 ? 'notFound' : 'error', detail: describeError(error) };
}

function Room({ workspaceId, meetingId, onClose }: { workspaceId: string; meetingId: string; onClose: () => void }) {
  const locale = useLocale();
  const [load, setLoad] = useState<Load>({ phase: 'loading' });
  const [state, setState] = useState<RoomState | null>(null);
  const [online, setOnline] = useState(true);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const [attempt, setAttempt] = useState(0);
  const [replay, setReplay] = useState<{ state: RoomState; step: number; total: number } | null>(null);
  const replayTimer = useRef<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  // First load: the stored room and transcript, then the live feed from the stored cursor.
  useEffect(() => {
    let live = true;
    setLoad({ phase: 'loading' });
    if (api.currentScope().workspaceId !== workspaceId) { setLoad({ phase: 'failed', kind: 'wrongWorkspace', detail: '' }); return; }
    (async () => {
      try {
        const bundle = await meetingRoomApi.room(meetingId);
        const messages = bundle.transcript_deleted ? [] : await meetingRoomApi.turns(meetingId);
        if (!live) return;
        setState(initialState(bundle, messages));
        setLoad({ phase: 'ready' });
      } catch (e) { if (live) setLoad({ phase: 'failed', ...failureOf(e) }); }
    })();
    return () => { live = false; };
  }, [workspaceId, meetingId, attempt]);

  const status = state?.meeting.status;
  const terminal = status ? TERMINAL.includes(status) : false;
  const startCursor = useRef(0);
  useEffect(() => { if (load.phase === 'ready' && state) startCursor.current = state.lastSeq; }, [load.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  // Live feed: only while the meeting can still change. It resumes by event number after any break.
  useEffect(() => {
    if (load.phase !== 'ready' || terminal) return;
    const controller = new AbortController();
    followRoom(meetingId, startCursor.current, (event: RoomEvent) => { startCursor.current = event.seq; setState((s) => s && applyEvent(s, event)); }, setOnline, controller.signal)
      .catch((e) => { if (!controller.signal.aborted) setLoad({ phase: 'failed', ...failureOf(e) }); });
    return () => controller.abort();
  }, [load.phase, terminal, meetingId]);

  // The speaking dot expires by itself if a worker died, so the clock ticks only while someone is shown speaking.
  const speakingNow = state ? activeSpeakers(state, now) : [];
  const anySpeaking = state ? Object.keys(state.speaking).length > 0 : false;
  useEffect(() => { if (!anySpeaking) return; const id = window.setInterval(() => setNow(Date.now()), 5000); return () => window.clearInterval(id); }, [anySpeaking]);

  // Agent names come from the agent profiles; an id is shown if a profile cannot be read.
  const agentIds = useMemo(() => (state?.participants ?? []).filter((p) => p.kind === 'agent').map((p) => p.actor_id), [state?.participants]);
  useEffect(() => {
    let live = true;
    agentIds.filter((id) => !names[id]).forEach((id) => meetingRoomApi.agentName(id).then((n) => { if (live && n) setNames((old) => ({ ...old, [id]: n })); }));
    return () => { live = false; };
  }, [agentIds]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (replayTimer.current) window.clearTimeout(replayTimer.current); }, []);

  const shown = replay?.state ?? state;
  const mine = state?.viewer.user_id ?? '';
  const isOwner = state?.viewer.is_owner ?? false;
  const who = useCallback((id: string) => id === mine ? t('meetingRoom.you') : names[id] ?? id, [mine, names]);

  // Reload the stored room after a change, so the page is right even while the feed is reconnecting.
  const reload = useCallback(async () => {
    const bundle = await meetingRoomApi.room(meetingId);
    setState((s) => s ? { ...s, meeting: bundle.meeting, participants: bundle.participants, attendance: bundle.attendance, agenda: bundle.agenda, outputs: bundle.outputs, files: bundle.files, transcriptDeleted: bundle.transcript_deleted } : s);
  }, [meetingId]);
  const act = useCallback(async (work: () => Promise<unknown>, done?: string) => {
    setBusy(true); setError(''); setInfo('');
    try { await work(); await reload(); if (done) setInfo(done); } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }, [reload]);

  if (load.phase === 'loading') return <p role="status" className="muted mrPad">{t('meetingRoom.loading')}</p>;
  if (load.phase === 'failed') return <div className="mrPad">
    <p className="notice errorNotice" role="alert">{load.kind === 'error' ? t('meetingRoom.error', { detail: load.detail }) : t(`meetingRoom.${load.kind}` as MessageKey)}</p>
    <div className="mrRow">{load.kind === 'error' && <button type="button" onClick={() => setAttempt((n) => n + 1)}>{t('meetingRoom.retry')}</button>}<button type="button" className="secondary" onClick={onClose}>{t('meetingRoom.close')}</button></div>
  </div>;
  if (!state || !shown) return null;

  const m = shown.meeting;
  const live = m.status === 'active' || m.status === 'paused';
  const speaking = replay ? [] : speakingNow;
  const present = presentActors(state);
  const youPresent = present.includes(mine);
  const used = budgetUsed(state.meeting, state.messages.length);
  const time = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString(intlLocale(locale), { hour: '2-digit', minute: '2-digit' }); };

  const playReplay = async () => {
    setBusy(true); setError('');
    try {
      const all: RoomEvent[] = []; let after = 0;
      for (let page = 0; page < 10; page++) { const r = await meetingRoomApi.events(meetingId, after); all.push(...r.items); if (!r.next_after) break; after = r.next_after; }
      let cursor = initialState({ ...(await meetingRoomApi.room(meetingId)), last_event_seq: 0, speaking: [] }, []);
      cursor = { ...cursor, messages: [], outputs: [], agenda: [], files: [], attendance: [] };
      const messages = all.filter((e) => e.kind === 'message');
      const step = (i: number, current: RoomState) => {
        if (i >= messages.length) { setReplay(null); return; }
        const upTo = messages[i].seq;
        const next = all.filter((e) => e.seq <= upTo && e.seq > current.lastSeq).reduce(applyEvent, current);
        setReplay({ state: next, step: i + 1, total: messages.length });
        replayTimer.current = window.setTimeout(() => step(i + 1, next), 900);
      };
      step(0, cursor);
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  };
  const stopReplay = () => { if (replayTimer.current) window.clearTimeout(replayTimer.current); setReplay(null); };

  return <div className="mrRoom">
    <header className="mrHead">
      <div className="mrTitle">
        <h2 dir="auto">{m.title}</h2>
        <span className={`mrChip mr-${m.status}`} role="status">{t(`meetingRoom.status.${m.status}` as MessageKey)}</span>
      </div>
      <div className="mrRow">
        {!terminal && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => meetingRoomApi.presence(meetingId, youPresent ? 'leave' : 'join'))}>
          {youPresent ? <LogOut aria-hidden="true" /> : <LogIn aria-hidden="true" />}{youPresent ? t('meetingRoom.leave') : t('meetingRoom.join')}</button>}
        {isOwner && m.status === 'scheduled' && <button type="button" disabled={busy || agentIds.length === 0} onClick={() => void act(() => meetingRoomApi.setState(meetingId, 'active'))}><Play aria-hidden="true" />{t('meetingRoom.start')}</button>}
        {isOwner && m.status === 'active' && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => meetingRoomApi.setState(meetingId, 'paused'))}><Pause aria-hidden="true" />{t('meetingRoom.pause')}</button>}
        {isOwner && m.status === 'paused' && <button type="button" disabled={busy} onClick={() => void act(() => meetingRoomApi.setState(meetingId, 'active'))}><Play aria-hidden="true" />{t('meetingRoom.resume')}</button>}
        {isOwner && live && <EndButton disabled={busy} onEnd={() => void act(() => meetingRoomApi.setState(meetingId, 'finished'))} />}
        <button type="button" className="secondary" onClick={onClose}><X aria-hidden="true" />{t('meetingRoom.close')}</button>
      </div>
    </header>

    {!terminal && !online && <p className="notice" role="status">{t('meetingRoom.offline')}</p>}
    {terminal && <p className="notice" role="note">{t('meetingRoom.replay')}{endedNote(m.ended_reason) ? ' ' + endedNote(m.ended_reason) : ''}</p>}
    {error && <p className="notice errorNotice" role="alert">{error}</p>}
    {info && <p className="notice" role="status">{info}</p>}
    {m.hand_raised_by && !terminal && <p className="notice mrHand" role="status"><Hand aria-hidden="true" />{t('meetingRoom.handRaised', { name: who(m.hand_raised_by) })}
      {isOwner && <button type="button" className="secondary" disabled={busy} onClick={() => void act(() => meetingRoomApi.hand(meetingId, 'lower'))}>{t('meetingRoom.lowerHand')}</button>}</p>}

    <div className="mrGrid">
      <div className="mrMain">
        <ul className="mrSpeakers" aria-label={t('meetingRoom.agents')}>
          {agentIds.length === 0 && <li className="muted">{t('meetingRoom.noAgents')}</li>}
          {agentIds.map((id) => <li key={id} className={speaking.includes(id) ? 'mrSpeaker mrIsSpeaking' : 'mrSpeaker'}>
            <span className="mrAvatar" aria-hidden="true">{(who(id)[0] ?? '?').toUpperCase()}</span>
            <span dir="auto">{who(id)}</span>
            {speaking.includes(id) && <span className="mrSpeakTag" role="status">{t('meetingRoom.speaking', { name: who(id) })}</span>}
          </li>)}
        </ul>

        <section aria-labelledby="mr-transcript">
          <div className="mrSectionHead"><h3 id="mr-transcript">{t('meetingRoom.transcript')}</h3>
            {terminal && !state.transcriptDeleted && state.messages.length > 0 && (replay
              ? <button type="button" className="secondary" onClick={stopReplay}><Square aria-hidden="true" />{`${replay.step}/${replay.total}`}</button>
              : <button type="button" className="secondary" disabled={busy} onClick={() => void playReplay()}><Play aria-hidden="true" />{t('meetingRoom.playback')}</button>)}
          </div>
          <Transcript state={shown} who={who} time={time} deleted={state.transcriptDeleted} isOwner={isOwner} live={live}
            speakingNames={speaking.map(who)} notices={state.notices.map((n) => n.reason)} canDraft={isOwner && !state.transcriptDeleted}
            onDraft={(kind, seq, body) => setDraft({ kind, body: draftFromMessage(body), seq })} />
        </section>

        {isOwner && live && <Composer disabled={busy} onSend={(text) => act(() => meetingRoomApi.say(meetingId, text))} />}
        {isOwner && m.status === 'active' && <div className="mrRow">
          <button type="button" disabled={busy || speaking.length > 0 || agentIds.length === 0 || !!m.hand_raised_by} onClick={() => void act(() => meetingRoomApi.runTurn(meetingId))} title={t('meetingRoom.runTurnHint')}>
            <Play aria-hidden="true" />{busy ? t('meetingRoom.busy') : t('meetingRoom.runTurn')}</button>
          <span className="muted mrHint">{t('meetingRoom.runTurnHint')}</span></div>}
        {!isOwner && live && <div className="mrRow">
          <button type="button" className="secondary" disabled={busy || !youPresent || m.hand_raised_by === mine} onClick={() => void act(() => meetingRoomApi.hand(meetingId, 'raise'))}><Hand aria-hidden="true" />{t('meetingRoom.raiseHand')}</button>
          <span className="muted mrHint">{youPresent ? t('meetingRoom.ownerOnly') : t('meetingRoom.joinFirst')}</span></div>}
      </div>

      <aside className="mrSide">
        <section aria-labelledby="mr-budget" className="mrCard"><h3 id="mr-budget">{t('meetingRoom.budget')}</h3>
          <Meter label={t('meetingRoom.budgetTurns', { used: used.turns.used, max: used.turns.max })} value={used.turns.used} max={used.turns.max} />
          <Meter label={t('meetingRoom.budgetTokens', { used: used.tokens.used, max: used.tokens.max })} value={used.tokens.used} max={used.tokens.max} />
          <Meter label={t('meetingRoom.budgetCost', { used: used.cost.used.toFixed(4), max: used.cost.max.toFixed(2) })} value={used.cost.used} max={used.cost.max} />
        </section>

        <section aria-labelledby="mr-attendance" className="mrCard"><h3 id="mr-attendance"><Users aria-hidden="true" />{t('meetingRoom.attendance')}</h3>
          {state.attendance.length === 0 ? <p className="muted">{t('meetingRoom.noAttendance')}</p> :
            <ul className="mrPlain">{state.attendance.map((a) => <li key={a.actor_id}><span dir="auto">{who(a.actor_id)}</span> <span className="muted">{a.left_at ? t('meetingRoom.left') : t('meetingRoom.present')}</span></li>)}</ul>}
        </section>

        <AgendaCard state={state} owner={isOwner} busy={busy} locked={terminal}
          onAdd={(title) => act(() => meetingRoomApi.addAgenda(meetingId, title))}
          onState={(id, s) => act(() => meetingRoomApi.patchAgenda(meetingId, id, { state: s }))}
          onRemove={(id) => act(() => meetingRoomApi.deleteAgenda(meetingId, id))} />

        <OutputsCard state={state} owner={isOwner} busy={busy} draft={draft} setDraft={setDraft}
          onSave={(d) => act(async () => { await meetingRoomApi.draft(meetingId, { kind: d.kind, body: d.body, owner_label: d.label || null, source_sequences: d.seq ? [d.seq] : [] }); setDraft(null); })}
          onDecide={(o, s) => act(() => meetingRoomApi.decide(meetingId, o.id, { state: s }))}
          onTask={(o) => act(() => meetingRoomApi.createTask(meetingId, o.id), t('meetingRoom.taskCreated'))} />

        <FilesCard state={state} owner={isOwner} busy={busy}          onLink={(id) => act(() => meetingRoomApi.linkFile(meetingId, id))} onUnlink={(id) => act(() => meetingRoomApi.unlinkFile(meetingId, id))} />

        {isOwner && <RecordCard terminal={terminal} deleted={state.transcriptDeleted} busy={busy}
          onExport={() => act(async () => {
            const text = await meetingRoomApi.exportMarkdown(meetingId);
            const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
            const link = document.createElement('a'); link.href = url; link.download = `meeting-${meetingId}.md`; link.click(); URL.revokeObjectURL(url);
          }, t('meetingRoom.exported'))}
          onDelete={() => act(async () => { await meetingRoomApi.deleteTranscript(meetingId); setState((s) => s ? { ...s, messages: [] } : s); }, t('meetingRoom.deleted'))} />}
      </aside>
    </div>
  </div>;
}
